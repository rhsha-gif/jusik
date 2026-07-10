from __future__ import annotations

import hashlib
import http.client
import re
import ssl
import urllib.request
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import pytest

from quantpilot.packages.core.kis_paper import (
    KIS_BALANCE_ENDPOINT,
    KIS_BALANCE_TR_ID,
    KIS_BUYING_POWER_ENDPOINT,
    KIS_BUYING_POWER_TR_ID,
    KIS_CASH_BUY_TR_ID,
    KIS_CASH_ORDER_ENDPOINT,
    KIS_CASH_SELL_TR_ID,
    KIS_CURRENT_PRICE_ENDPOINT,
    KIS_CURRENT_PRICE_TR_ID,
    KIS_DAILY_ORDERS_ENDPOINT,
    KIS_DAILY_ORDERS_TR_ID,
    KIS_L2_ENDPOINT,
    KIS_L2_TR_ID,
    KIS_PAPER_BASE_URL,
    KIS_TOKEN_ENDPOINT,
    KisHttpResponse,
    KisJsonTransport,
    KisPaperBusinessError,
    KisPaperClient,
    KisPaperConfig,
    KisPaperConfigurationError,
    KisPaperOrderOutcomeUnknown,
    KisPaperProtocolError,
    StrictUrllibKisPaperTransport,
    kis_recent_three_month_start,
)


class RecordingTransport(KisJsonTransport):
    def __init__(self, *outcomes: KisHttpResponse | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def request_json(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None,
        body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> KisHttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "params": None if params is None else dict(params),
                "body": None if body is None else dict(body),
                "timeout_seconds": timeout_seconds,
            }
        )
        if not self.outcomes:
            raise AssertionError("unexpected transport call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _config(*, access_token: str = "fake-paper-token") -> KisPaperConfig:
    return KisPaperConfig(
        app_key="fake-app-key",
        app_secret="fake-app-secret",
        account_number="00000000",
        product_code="01",
        access_token=access_token,
    )


def _response(
    payload: Mapping[str, Any],
    *,
    tr_cont: str = "",
    status_code: int = 200,
) -> KisHttpResponse:
    return KisHttpResponse(status_code, payload, {"Tr_Cont": tr_cont})


@pytest.mark.parametrize(
    "unsafe_base_url",
    [
        "http://openapivts.koreainvestment.com:29443",
        "https://openapi.koreainvestment.com:9443",
        "https://openapivts.koreainvestment.com",
        "https://openapivts.koreainvestment.com:443",
        "https://user@openapivts.koreainvestment.com:29443",
        "https://openapivts.koreainvestment.com:29443/",
        "https://openapivts.koreainvestment.com:29443/path",
        "https://openapivts.koreainvestment.com:29443?mode=paper",
        "https://openapivts.koreainvestment.com:29443#fragment",
        "https://openapivts.koreainvestment.com.evil.test:29443",
    ],
)
def test_config_rejects_every_non_exact_paper_origin(unsafe_base_url: str) -> None:
    with pytest.raises(KisPaperConfigurationError, match="approved paper origin"):
        KisPaperConfig(
            app_key="fake-app-key",
            app_secret="fake-app-secret",
            account_number="00000000",
            base_url=unsafe_base_url,
        )


def test_config_repr_hides_all_credentials_and_exposes_only_account_fingerprint() -> None:
    config = _config()

    rendered = repr(config)

    assert config.base_url == KIS_PAPER_BASE_URL
    assert "fake-app-key" not in rendered
    assert "fake-app-secret" not in rendered
    assert "fake-paper-token" not in rendered
    assert "00000000" not in rendered
    expected_digest = hashlib.sha256(b"00000000\x1f01").hexdigest()
    expected = f"sha256:{expected_digest}"
    assert config.account_scope_fingerprint == expected
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", config.account_scope_fingerprint)


def test_token_request_uses_only_the_paper_token_endpoint_and_hides_token_repr() -> None:
    transport = RecordingTransport(
        _response(
            {
                "access_token": "returned-secret-token",
                "token_type": "Bearer",
                "expires_in": 86400,
            }
        )
    )
    client = KisPaperClient(_config(access_token=""), transport=transport)

    token = client.request_access_token()

    assert token.access_token == "returned-secret-token"
    assert token.expires_in_seconds == 86400
    assert "returned-secret-token" not in repr(token)
    assert transport.calls == [
        {
            "method": "POST",
            "url": f"{KIS_PAPER_BASE_URL}{KIS_TOKEN_ENDPOINT}",
            "headers": {"content-type": "application/json; charset=utf-8"},
            "params": None,
            "body": {
                "grant_type": "client_credentials",
                "appkey": "fake-app-key",
                "appsecret": "fake-app-secret",
            },
            "timeout_seconds": 10.0,
        }
    ]


def test_current_price_and_l2_use_exact_quote_contracts() -> None:
    l2_output: dict[str, str] = {"aspr_acpt_hour": "101530"}
    for level in range(1, 11):
        l2_output[f"askp{level}"] = str(70_000 + level)
        l2_output[f"bidp{level}"] = str(70_000 - level)
        l2_output[f"askp_rsqn{level}"] = str(level * 10)
        l2_output[f"bidp_rsqn{level}"] = str(level * 20)
    transport = RecordingTransport(
        _response(
            {
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "output": {
                    "stck_prpr": "70000",
                    "stck_oprc": "69000",
                    "stck_hgpr": "71000",
                    "stck_lwpr": "68000",
                    "acml_vol": "123456",
                },
            }
        ),
        _response({"rt_cd": "0", "msg_cd": "MCA00000", "output1": l2_output}),
    )
    client = KisPaperClient(_config(), transport=transport)

    price = client.get_current_price("005930")
    l2 = client.get_l2("005930")

    assert price.last_price == Decimal("70000")
    assert price.accumulated_volume == 123456
    assert l2.accepted_at_hhmmss == "101530"
    assert len(l2.levels) == 10
    assert l2.levels[0].ask_price == Decimal("70001")
    price_call, l2_call = transport.calls
    assert price_call["url"] == f"{KIS_PAPER_BASE_URL}{KIS_CURRENT_PRICE_ENDPOINT}"
    assert price_call["headers"]["tr_id"] == KIS_CURRENT_PRICE_TR_ID
    assert price_call["params"] == {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930",
    }
    assert l2_call["url"] == f"{KIS_PAPER_BASE_URL}{KIS_L2_ENDPOINT}"
    assert l2_call["headers"]["tr_id"] == KIS_L2_TR_ID
    assert l2_call["headers"]["authorization"] == "Bearer fake-paper-token"
    assert l2_call["params"] == price_call["params"]


def _balance_position(symbol: str) -> dict[str, str]:
    return {
        "pdno": symbol,
        "prdt_name": f"FAKE-{symbol}",
        "hldg_qty": "10",
        "ord_psbl_qty": "8",
        "pchs_avg_pric": "60000",
        "prpr": "70000",
        "pchs_amt": "600000",
        "evlu_amt": "700000",
    }


def _balance_summary() -> dict[str, str]:
    return {
        "dnca_tot_amt": "1000000",
        "nxdy_excc_amt": "900000",
        "pchs_amt_smtl_amt": "600000",
        "tot_evlu_amt": "1700000",
        "nass_amt": "1700000",
        "evlu_pfls_smtl_amt": "100000",
    }


def test_balance_pagination_carries_both_cursor_keys_and_tr_cont_header() -> None:
    transport = RecordingTransport(
        _response(
            {
                "rt_cd": "0",
                "output1": [_balance_position("005930")],
                "output2": [_balance_summary()],
                "ctx_area_fk100": "cursor-fk",
                "ctx_area_nk100": "cursor-nk",
            },
            tr_cont="F",
        ),
        _response(
            {
                "rt_cd": "0",
                "output1": [_balance_position("000660")],
                "output2": [_balance_summary()],
            },
            tr_cont="D",
        ),
    )
    client = KisPaperClient(_config(), transport=transport)

    result = client.get_balance()

    assert result.pages_fetched == 2
    assert [position.symbol for position in result.positions] == ["005930", "000660"]
    assert result.summary.net_asset_amount == Decimal("1700000")
    first, second = transport.calls
    assert first["url"] == f"{KIS_PAPER_BASE_URL}{KIS_BALANCE_ENDPOINT}"
    assert first["headers"]["tr_id"] == KIS_BALANCE_TR_ID
    assert "tr_cont" not in first["headers"]
    assert first["params"]["CTX_AREA_FK100"] == ""
    assert second["headers"]["tr_cont"] == "N"
    assert second["params"]["CTX_AREA_FK100"] == "cursor-fk"
    assert second["params"]["CTX_AREA_NK100"] == "cursor-nk"


@pytest.mark.parametrize("failure", ["duplicate_symbol", "summary_changed"])
def test_balance_pagination_rejects_duplicate_or_mixed_snapshot_evidence(
    failure: str,
) -> None:
    second_summary = _balance_summary()
    second_symbol = "000660"
    expected_error = "summary changed"
    if failure == "duplicate_symbol":
        second_symbol = "005930"
        expected_error = "repeated a symbol"
    else:
        second_summary["nass_amt"] = "1600000"

    transport = RecordingTransport(
        _response(
            {
                "rt_cd": "0",
                "output1": [_balance_position("005930")],
                "output2": [_balance_summary()],
                "ctx_area_fk100": "cursor-fk",
                "ctx_area_nk100": "cursor-nk",
            },
            tr_cont="F",
        ),
        _response(
            {
                "rt_cd": "0",
                "output1": [_balance_position(second_symbol)],
                "output2": [second_summary],
            },
            tr_cont="D",
        ),
    )
    client = KisPaperClient(_config(), transport=transport)

    with pytest.raises(KisPaperProtocolError, match=expected_error):
        client.get_balance()


def _daily_row() -> dict[str, str]:
    return {
        "odno": "0000012345",
        "orgn_odno": "",
        "ord_gno_brno": "91234",
        "ord_dt": "20260710",
        "ord_tmd": "101530",
        "pdno": "005930",
        "prdt_name": "FAKE-SAMSUNG",
        "sll_buy_dvsn_cd": "02",
        "ord_qty": "3",
        "ord_unpr": "70000",
        "tot_ccld_qty": "2",
        "avg_prvs": "69900",
        "rmn_qty": "1",
        "rjct_qty": "0",
        "cncl_yn": "N",
        "cnc_cfrm_qty": "0",
        "tot_ccld_amt": "139800",
    }


def test_daily_order_query_uses_exact_paper_tr_and_preserves_fill_evidence() -> None:
    transport = RecordingTransport(
        _response({"rt_cd": "0", "output1": [_daily_row()]}, tr_cont="D")
    )
    client = KisPaperClient(_config(), transport=transport)

    result = client.get_daily_orders_and_fills(
        date(2026, 7, 1),
        date(2026, 7, 10),
        as_of_date=date(2026, 7, 10),
    )

    assert result.pages_fetched == 1
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.order_number == "0000012345"
    assert row.order_branch_number == "91234"
    assert row.side == "buy"
    assert row.total_filled_quantity == 2
    assert row.average_fill_price == Decimal("69900")
    assert row.remaining_quantity == 1
    call = transport.calls[0]
    assert call["url"] == f"{KIS_PAPER_BASE_URL}{KIS_DAILY_ORDERS_ENDPOINT}"
    assert call["headers"]["tr_id"] == KIS_DAILY_ORDERS_TR_ID
    assert call["params"]["INQR_STRT_DT"] == "20260701"
    assert call["params"]["INQR_END_DT"] == "20260710"
    assert call["params"]["CANO"] == "00000000"
    assert call["params"]["EXCG_ID_DVSN_CD"] == "KRX"


def test_daily_order_window_uses_calendar_months_instead_of_fixed_days() -> None:
    assert kis_recent_three_month_start(date(2026, 7, 10)) == date(2026, 4, 10)
    assert kis_recent_three_month_start(date(2026, 5, 31)) == date(2026, 2, 28)
    assert kis_recent_three_month_start(date(2024, 5, 31)) == date(2024, 2, 29)
    assert kis_recent_three_month_start(date(2026, 1, 31)) == date(2025, 10, 31)

    accepted_transport = RecordingTransport(
        _response({"rt_cd": "0", "output1": []}, tr_cont="D")
    )
    accepted = KisPaperClient(_config(), transport=accepted_transport)
    result = accepted.get_daily_orders_and_fills(
        date(2026, 4, 10),
        date(2026, 7, 10),
        as_of_date=date(2026, 7, 10),
    )
    assert result.rows == ()

    rejected = KisPaperClient(_config(), transport=RecordingTransport())
    with pytest.raises(KisPaperConfigurationError, match="recent-three-month"):
        rejected.get_daily_orders_and_fills(
            date(2026, 4, 9),
            date(2026, 7, 10),
            as_of_date=date(2026, 7, 10),
        )


def test_daily_order_window_rejects_datetime_values_instead_of_truncating() -> None:
    client = KisPaperClient(_config(), transport=RecordingTransport())
    timestamp = datetime(2026, 7, 10, 9, 0)

    with pytest.raises(KisPaperConfigurationError, match="date value"):
        kis_recent_three_month_start(timestamp)  # type: ignore[arg-type]
    with pytest.raises(KisPaperConfigurationError, match="date values"):
        client.get_daily_orders_and_fills(  # type: ignore[arg-type]
            timestamp,
            date(2026, 7, 10),
            as_of_date=date(2026, 7, 10),
        )
    with pytest.raises(KisPaperConfigurationError, match="as-of date"):
        client.get_daily_orders_and_fills(
            date(2026, 7, 10),
            date(2026, 7, 10),
            as_of_date=timestamp,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("side", "expected_tr_id"),
    [("buy", KIS_CASH_BUY_TR_ID), ("sell", KIS_CASH_SELL_TR_ID)],
)
def test_limit_cash_order_uses_exact_body_and_side_specific_paper_tr(
    side: Literal["buy", "sell"], expected_tr_id: str
) -> None:
    transport = RecordingTransport(
        _response(
            {
                "rt_cd": "0",
                "msg_cd": "APBK0013",
                "output": {
                    "ODNO": "0000099999",
                    "KRX_FWDG_ORD_ORGNO": "91234",
                    "ORD_TMD": "101530",
                },
            }
        )
    )
    client = KisPaperClient(_config(), transport=transport)

    result = client.place_limit_cash_order(
        symbol="005930", side=side, quantity=3, limit_price=Decimal("70000")
    )

    assert result.order_number == "0000099999"
    assert result.krx_forwarding_order_org_number == "91234"
    assert result.transaction_id == expected_tr_id
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{KIS_PAPER_BASE_URL}{KIS_CASH_ORDER_ENDPOINT}"
    assert call["headers"]["tr_id"] == expected_tr_id
    assert call["headers"]["custtype"] == "P"
    assert call["body"] == {
        "CANO": "00000000",
        "ACNT_PRDT_CD": "01",
        "PDNO": "005930",
        "ORD_DVSN": "00",
        "ORD_QTY": "3",
        "ORD_UNPR": "70000",
        "EXCG_ID_DVSN_CD": "KRX",
        "SLL_TYPE": "01" if side == "sell" else "",
        "CNDT_PRIC": "",
    }
    assert len(transport.calls) == 1


def test_buying_power_uses_no_receivable_paper_inquiry_contract() -> None:
    transport = RecordingTransport(
        _response(
            {
                "rt_cd": "0",
                "output": {
                    "ord_psbl_cash": "500000",
                    "nrcvb_buy_amt": "450000",
                    "nrcvb_buy_qty": "6",
                    "max_buy_amt": "900000",
                    "max_buy_qty": "12",
                    "psbl_qty_calc_unpr": "70000",
                },
            }
        )
    )
    client = KisPaperClient(_config(), transport=transport)

    result = client.get_buying_power("005930", Decimal("70000"))

    assert result.no_receivable_buy_amount == Decimal("450000")
    assert result.no_receivable_buy_quantity == 6
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == f"{KIS_PAPER_BASE_URL}{KIS_BUYING_POWER_ENDPOINT}"
    assert call["headers"]["tr_id"] == KIS_BUYING_POWER_TR_ID
    assert call["params"] == {
        "CANO": "00000000",
        "ACNT_PRDT_CD": "01",
        "PDNO": "005930",
        "ORD_UNPR": "70000",
        "ORD_DVSN": "01",
        "CMA_EVLU_AMT_ICLD_YN": "N",
        "OVRS_ICLD_YN": "N",
    }


def test_buying_power_rejects_negative_or_malformed_capacity() -> None:
    transport = RecordingTransport(
        _response(
            {
                "rt_cd": "0",
                "output": {
                    "ord_psbl_cash": "500000",
                    "nrcvb_buy_amt": "-1",
                    "nrcvb_buy_qty": "6",
                    "max_buy_amt": "900000",
                    "max_buy_qty": "12",
                    "psbl_qty_calc_unpr": "70000",
                },
            }
        )
    )

    with pytest.raises(KisPaperProtocolError, match="negative amount"):
        KisPaperClient(_config(), transport=transport).get_buying_power(
            "005930",
            Decimal("70000"),
        )
    assert len(transport.calls) == 1


def test_http_200_business_failure_is_definitive_and_never_leaks_broker_message() -> None:
    transport = RecordingTransport(
        _response(
            {
                "rt_cd": "1",
                "msg_cd": "APBK1001",
                "msg1": "do not leak fake-paper-token or 00000000",
            }
        )
    )
    client = KisPaperClient(_config(), transport=transport)

    with pytest.raises(KisPaperBusinessError) as captured:
        client.place_limit_cash_order(
            symbol="005930", side="buy", quantity=1, limit_price=Decimal("70000")
        )

    rendered = str(captured.value)
    assert "APBK1001" in rendered
    assert "fake-paper-token" not in rendered
    assert "00000000" not in rendered
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        TimeoutError("fake timeout containing fake-paper-token"),
        ConnectionError("fake reset containing 00000000"),
        http.client.IncompleteRead(b"truncated fake-paper-token"),
        http.client.BadStatusLine("00000000"),
        _response({"rt_cd": "0", "msg_cd": "APBK0013", "output": {}}),
    ],
)
def test_ambiguous_order_outcomes_are_unknown_and_are_never_retried(
    outcome: KisHttpResponse | BaseException,
) -> None:
    transport = RecordingTransport(outcome)
    client = KisPaperClient(_config(), transport=transport)

    with pytest.raises(KisPaperOrderOutcomeUnknown) as captured:
        client.place_limit_cash_order(
            symbol="005930", side="sell", quantity=1, limit_price=Decimal("70000")
        )

    assert "reconcile before any retry" in str(captured.value)
    assert "fake-paper-token" not in str(captured.value)
    assert "00000000" not in str(captured.value)
    assert len(transport.calls) == 1


def test_malformed_business_response_and_repeated_pagination_cursor_fail_closed() -> None:
    malformed = RecordingTransport(_response({"output": {"stck_prpr": "70000"}}))
    with pytest.raises(KisPaperProtocolError, match="business result code"):
        KisPaperClient(_config(), transport=malformed).get_current_price("005930")
    assert len(malformed.calls) == 1

    page = {
        "rt_cd": "0",
        "output1": [],
        "output2": [_balance_summary()],
        "ctx_area_fk100": "same-fk",
        "ctx_area_nk100": "same-nk",
    }
    repeated = RecordingTransport(
        _response(page, tr_cont="F"),
        _response(page, tr_cont="M"),
    )
    with pytest.raises(KisPaperProtocolError, match="repeated"):
        KisPaperClient(_config(), transport=repeated).get_balance()
    assert len(repeated.calls) == 2


def test_strict_urllib_transport_builds_tls12_no_proxy_no_redirect_one_shot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        minimum_version: ssl.TLSVersion | None = None

    class FakeHeaders:
        def get(self, key: str, default: str = "") -> str:
            return default

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return b'{"rt_cd":"0"}'

        def getcode(self) -> int:
            return 200

    class FakeOpener:
        def __init__(self) -> None:
            self.calls = 0

        def open(self, request: urllib.request.Request, *, timeout: float) -> FakeResponse:
            self.calls += 1
            assert request.full_url == (
                f"{KIS_PAPER_BASE_URL}{KIS_CURRENT_PRICE_ENDPOINT}?FID_INPUT_ISCD=005930"
            )
            assert timeout == 3.0
            return FakeResponse()

    context = FakeContext()
    opener = FakeOpener()
    captured_handlers: list[Any] = []
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)

    def fake_build_opener(*handlers: Any) -> FakeOpener:
        captured_handlers.extend(handlers)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    transport = StrictUrllibKisPaperTransport()

    response = transport.request_json(
        "GET",
        f"{KIS_PAPER_BASE_URL}{KIS_CURRENT_PRICE_ENDPOINT}",
        headers={},
        params={"FID_INPUT_ISCD": "005930"},
        body=None,
        timeout_seconds=3.0,
    )

    assert response.payload["rt_cd"] == "0"
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    proxy_handlers = [item for item in captured_handlers if isinstance(item, urllib.request.ProxyHandler)]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}
    redirect_handlers = [
        item for item in captured_handlers if isinstance(item, urllib.request.HTTPRedirectHandler)
    ]
    assert len(redirect_handlers) == 1
    assert redirect_handlers[0].redirect_request(None, None, 302, "", {}, "https://evil.test") is None
    assert opener.calls == 1


def test_strict_transport_rejects_non_paper_request_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NeverOpen:
        def open(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("unsafe URL reached the network opener")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: NeverOpen())
    transport = StrictUrllibKisPaperTransport()

    with pytest.raises(KisPaperConfigurationError, match="approved boundary"):
        transport.request_json(
            "GET",
            "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={},
            params={},
            body=None,
            timeout_seconds=3.0,
        )
