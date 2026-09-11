from datetime import datetime
import json
import pytest

from quantpilot.paper.calendar import KST
from quantpilot.paper.intraday.stream import PAPER_WS, PaperStream, parse_frame

NOW = datetime(2026, 9, 11, 10, 0, 1, tzinfo=KST)


def tick_frame():
    fields = ["0"] * 46
    for i, v in {
        0: "005930",
        1: "100000",
        2: "70000",
        12: "100",
        13: "10000",
        33: "20260911",
    }.items():
        fields[i] = v
    return "0|H0STCNT0|001|" + "^".join(fields)


def test_only_public_market_frames_with_both_times():
    event = parse_frame(tick_frame(), NOW, ["005930"])[0]
    assert event["price"] == 70000 and event["received_at"] != event["event_at"]
    with pytest.raises(ValueError, match="non_market"):
        parse_frame("1|H0STCNI9|001|private", NOW, ["005930"])
    with pytest.raises(ValueError, match="unsubscribed"):
        parse_frame(tick_frame(), NOW, ["000660"])


def test_stream_is_paper_only_and_never_subscribes_account_channels():
    class Socket:
        messages = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send(self, value):
            self.messages.append(json.loads(value))

    socket = Socket()

    def connect(url, **kwargs):
        assert url == PAPER_WS and kwargs["proxy"] is None
        return socket

    stream = PaperStream("fixture-key", ["005930"], connect=connect)
    stream.consume(
        lambda e: pytest.fail("no events expected"), lambda: NOW, lambda: True
    )
    assert {r["body"]["input"]["tr_id"] for r in socket.messages} == {
        "H0STCNT0",
        "H0STASP0",
    }


def test_disabled_collection_cannot_construct_client(tmp_path, monkeypatch):
    from quantpilot.paper.intraday.collection import collect_paper
    from quantpilot.packages.core.kis_paper import KisPaperConfigurationError

    monkeypatch.setattr(
        "quantpilot.packages.core.kis_paper.KisPaperClient",
        lambda *a, **k: pytest.fail("constructed before explicit diagnostic gate"),
    )
    with pytest.raises(KisPaperConfigurationError):
        collect_paper(tmp_path / "market.sqlite3", env={})


def test_approval_passes_real_transport_validation_with_fake_http_response():
    from io import BytesIO
    from quantpilot.paper.intraday.stream import (
        PaperApprovalTransport,
        request_approval,
    )
    from quantpilot.packages.core.kis_paper import (
        KIS_PAPER_BASE_URL,
        KIS_WS_APPROVAL_ENDPOINT,
        KisPaperConfig,
    )

    class Response(BytesIO):
        headers = {}

        def getcode(self):
            return 200

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == KIS_PAPER_BASE_URL + KIS_WS_APPROVAL_ENDPOINT
            assert request.method == "POST" and timeout == 10
            assert set(json.loads(request.data)) == {
                "grant_type",
                "appkey",
                "secretkey",
            }
            return Response(b'{"approval_key":"fixture-stream-approval"}')

    transport = PaperApprovalTransport()
    transport._opener = Opener()
    config = KisPaperConfig(
        app_key="fixture", app_secret="fixture", account_number="12345678"
    )
    assert request_approval(config, transport) == "fixture-stream-approval"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/oauth2/Approval"),
        ("POST", "/uapi/domestic-stock/v1/trading/order-cash"),
        ("POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl"),
        ("POST", "/oauth2/Approval?redirect=1"),
        ("POST", "/oauth2/Approval/"),
    ],
)
def test_approval_transport_rejects_other_operations_before_network(method, path):
    from quantpilot.paper.intraday.stream import PaperApprovalTransport
    from quantpilot.packages.core.kis_paper import (
        KIS_PAPER_BASE_URL,
        KisPaperConfigurationError,
    )

    class NoNetwork:
        def open(self, *args, **kwargs):
            pytest.fail("rejected operation reached network")

    transport = PaperApprovalTransport()
    transport._opener = NoNetwork()
    with pytest.raises(KisPaperConfigurationError, match="endpoint blocked"):
        transport.request_json(
            method,
            KIS_PAPER_BASE_URL + path,
            headers={},
            params=None,
            body={},
            timeout_seconds=10,
        )
    with pytest.raises(KisPaperConfigurationError, match="endpoint blocked"):
        transport.request_json(
            "POST",
            "https://openapi.koreainvestment.com:9443/oauth2/Approval",
            headers={},
            params=None,
            body={},
            timeout_seconds=10,
        )
