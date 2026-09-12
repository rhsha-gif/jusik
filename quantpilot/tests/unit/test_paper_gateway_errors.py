"""Gateway refusals are definitive; other non-2xx statuses stay unknown outcomes.

Shapes were observed on the KIS paper server in a read-only probe on 2026-09-12:
`EGW00201` (per-second limit) arrives as HTTP 500 with an `rt_cd`/`msg_cd` body and the
one-per-minute token throttle arrives as HTTP 403 with `error_code`/`error_description`.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from quantpilot.packages.core.kis_paper import (
    KIS_PAPER_BASE_URL,
    KIS_TOKEN_ENDPOINT,
    KisHttpResponse,
    KisPaperBusinessError,
    KisPaperClient,
    KisPaperConfig,
    KisPaperGatewayRejected,
    KisPaperTransportError,
    StrictUrllibKisPaperTransport,
    safe_failure,
)
from quantpilot.paper.auth import RefreshingClient


def _http_error(status, body):
    payload = json.dumps(body).encode() if body is not None else b""
    return urllib.error.HTTPError(
        KIS_PAPER_BASE_URL + "/x", status, "error", {}, io.BytesIO(payload)
    )


class _Opener:
    def __init__(self, error):
        self.error = error

    def open(self, request, timeout):
        raise self.error


def _transport(error):
    transport = StrictUrllibKisPaperTransport()
    transport._opener = _Opener(error)
    return transport


def _call(transport):
    return transport.request_json(
        "GET",
        KIS_PAPER_BASE_URL + "/uapi/domestic-stock/v1/quotations/inquire-price",
        headers={},
        params={"a": "b"},
        body=None,
        timeout_seconds=1.0,
    )


@pytest.mark.parametrize(
    "status, body, code",
    [
        (500, {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}, "EGW00201"),
        (403, {"error_code": "EGW00133", "error_description": "접근토큰 발급 잠시 후 다시 시도"}, "EGW00133"),
        (500, {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}, "EGW00123"),
        (500, {"rt_cd": "1", "msg_cd": "EGW02006", "msg1": "모의투자 미지원 API"}, "EGW02006"),
    ],
)
def test_gateway_refusal_is_a_definitive_business_rejection(status, body, code):
    with pytest.raises(KisPaperGatewayRejected) as info:
        _call(_transport(_http_error(status, body)))
    assert isinstance(info.value, KisPaperBusinessError)
    assert info.value.code == code
    assert safe_failure(info.value, "probe")["broker_code"] == code
    assert body.get("msg1", body.get("error_description", "")) not in str(info.value)


@pytest.mark.parametrize(
    "status, body",
    [
        (500, {"rt_cd": "1", "msg_cd": "OPSQ9999", "msg1": "unknown broker text"}),
        (502, None),
        (500, {"rt_cd": "1", "msg_cd": "../not safe"}),
        (503, {"unexpected": True}),
    ],
)
def test_other_http_failures_stay_transport_errors_and_never_leak_text(status, body):
    with pytest.raises(KisPaperTransportError) as info:
        _call(_transport(_http_error(status, body)))
    assert not isinstance(info.value, KisPaperBusinessError)
    assert f"HTTP status {status}" in str(info.value)
    assert "unknown broker text" not in str(info.value)
    assert "unavailable" not in str(info.value)


def test_oversized_error_body_is_ignored_safely():
    huge = urllib.error.HTTPError(
        KIS_PAPER_BASE_URL + "/x", 500, "error", {}, io.BytesIO(b"x" * 1_000_001)
    )
    with pytest.raises(KisPaperTransportError):
        _call(_transport(huge))


class _TokenTransport:
    def __init__(self, payload):
        self.payload = payload

    def request_json(self, method, url, **kwargs):
        assert (method, url) == ("POST", KIS_PAPER_BASE_URL + KIS_TOKEN_ENDPOINT)
        return KisHttpResponse(200, self.payload)


def _config():
    return KisPaperConfig(app_key="fake", app_secret="fake", account_number="12345678")


def test_token_expiry_is_read_from_the_absolute_kst_field():
    client = KisPaperClient(
        _config(),
        transport=_TokenTransport(
            {
                "access_token": "secret",
                "token_type": "Bearer",
                "expires_in": 86400,
                "access_token_token_expired": "2026-09-13 10:42:18",
            }
        ),
    )
    token = client.request_access_token()
    assert token.expires_at == datetime(2026, 9, 13, 1, 42, 18, tzinfo=timezone.utc)
    assert "secret" not in repr(token)


@pytest.mark.parametrize("value", [None, "", "not a date", 42])
def test_missing_or_malformed_expiry_field_falls_back_to_expires_in(value):
    payload = {"access_token": "secret", "token_type": "Bearer", "expires_in": 3600}
    if value is not None:
        payload["access_token_token_expired"] = value
    token = KisPaperClient(_config(), transport=_TokenTransport(payload)).request_access_token()
    assert token.expires_at is None and token.expires_in_seconds == 3600


NOW = datetime(2026, 9, 12, 1, 42, tzinfo=timezone.utc)


def _refreshing(tokens, *, initial_token=""):
    calls = []

    def request():
        calls.append("auth")
        outcome = tokens.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    config = SimpleNamespace(
        access_token=initial_token,
        with_access_token=lambda value: SimpleNamespace(
            access_token=value, with_access_token=config.with_access_token
        ),
    )
    factory = lambda cfg: SimpleNamespace(
        request_access_token=request, token=cfg.access_token
    )
    now = [NOW]
    client = RefreshingClient(config, factory, lambda: now[0])
    return client, calls, now


def test_renewal_is_anchored_to_the_absolute_expiry_not_expires_in():
    # A re-issued token carries the original expiry: 86400s expires_in but only
    # 30 minutes actually left. Renewal must not wait 0.9 * 86400 seconds.
    token = SimpleNamespace(
        access_token="t1",
        expires_in_seconds=86400,
        expires_at=NOW + timedelta(minutes=30),
    )
    client, calls, now = _refreshing([token, SimpleNamespace(
        access_token="t2", expires_in_seconds=86400, expires_at=None)])
    assert client.current_client().token == "t1"
    now[0] = NOW + timedelta(minutes=24)
    assert client.current_client().token == "t1" and calls == ["auth"]
    now[0] = NOW + timedelta(minutes=26)
    assert client.current_client().token == "t2" and calls == ["auth", "auth"]


def test_throttled_issuance_keeps_the_held_token_and_waits_out_the_backoff():
    throttled = KisPaperGatewayRejected("throttled (code=EGW00133)", code="EGW00133")
    fresh = SimpleNamespace(access_token="t2", expires_in_seconds=86400, expires_at=None)
    client, calls, now = _refreshing([throttled, fresh], initial_token="held")
    assert client.current_client().token == "held" and calls == ["auth"]
    now[0] = NOW + timedelta(seconds=30)
    assert client.current_client().token == "held" and calls == ["auth"]
    now[0] = NOW + timedelta(seconds=61)
    assert client.current_client().token == "t2" and calls == ["auth", "auth"]


def test_throttled_issuance_without_any_token_still_fails_closed():
    throttled = KisPaperGatewayRejected("throttled (code=EGW00133)", code="EGW00133")
    client, calls, now = _refreshing([throttled])
    with pytest.raises(KisPaperGatewayRejected):
        client.current_client()
    now[0] = NOW + timedelta(seconds=30)
    with pytest.raises(ValueError, match="token_refresh_backoff"):
        client.current_client()
    assert calls == ["auth"]


def test_broker_reported_expiry_forces_reissue_after_the_backoff():
    first = SimpleNamespace(access_token="t1", expires_in_seconds=86400, expires_at=None)
    second = SimpleNamespace(access_token="t2", expires_in_seconds=86400, expires_at=None)
    client, calls, now = _refreshing([first, second])
    assert client.current_client().token == "t1"
    client.invalidate()
    # Still inside the one-per-minute issuance backoff: the held token is used.
    now[0] = NOW + timedelta(seconds=30)
    assert client.current_client().token == "t1" and calls == ["auth"]
    now[0] = NOW + timedelta(seconds=61)
    assert client.current_client().token == "t2" and calls == ["auth", "auth"]
