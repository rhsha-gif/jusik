from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from quantpilot.jobs.check_paper_readiness import ReadinessTransport, probe, readiness_market
from quantpilot.packages.core.kis_paper import KIS_PAPER_BASE_URL, KisPaperConfigurationError
from quantpilot.paper.strategy import Bar


@pytest.mark.parametrize("method,url", [
    ("POST", KIS_PAPER_BASE_URL + "/uapi/domestic-stock/v1/trading/order-cash"),
    ("POST", KIS_PAPER_BASE_URL + "/uapi/domestic-stock/v1/trading/order-rvsecncl"),
    ("GET", "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price"),
    ("GET", KIS_PAPER_BASE_URL + "/unknown"),
])
def test_probe_transport_rejects_order_cancel_live_and_unknown(method, url):
    with pytest.raises(KisPaperConfigurationError):
        ReadinessTransport().request_json(method, url)


def setup(opened=True, lag=0):
    now = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)
    client = SimpleNamespace(get_current_price=lambda _: None, get_balance=lambda: None)
    bar = Bar("005930", now - timedelta(minutes=1 + lag), 100, 101, 99, 100, 1)
    quote = SimpleNamespace(last=100, bid=99, ask=101, as_of=now)
    market = SimpleNamespace(
        candidates=lambda *_: (["005930"], "kis_paper_ranking"),
        minutes=lambda *_: [bar], quotes=lambda *_: {"005930": quote},
    )
    calendar = SimpleNamespace(session=lambda _: SimpleNamespace(trading=lambda _: opened))
    return client, market, calendar, lambda: now


def test_good_read_probes_do_not_grant_order_authority():
    result = probe(*setup())
    assert result["status"] == "passed"
    assert result["order_authority"] is False


def test_closed_session_defers_quotes_and_minutes():
    args = setup(False)
    def forbidden(*_):
        pytest.fail("closed-session market probe")
    args[1].minutes = args[1].quotes = forbidden
    result = probe(*args)
    assert result["status"] == "pending_open_session"


def test_stale_minutes_block_readiness():
    result = probe(*setup(lag=5))
    assert result["status"] == "failed"
    assert result["checks"]["minutes"]["status"] == "failed"


def test_failure_is_redacted_and_other_reads_continue():
    args = setup()
    def broken():
        raise RuntimeError("private account response")
    args[0].get_balance = broken
    result = probe(*args)
    assert "private" not in str(result)
    assert result["status"] == "failed"
    assert result["checks"]["minutes"]["status"] == "passed"


def test_kis_ranking_failure_never_uses_public_network(monkeypatch):
    from quantpilot.paper.data import PaperMarket, DataUnavailable

    def forbidden(*_):
        pytest.fail("public network must never be called")

    monkeypatch.setattr(PaperMarket, "_public_fetch", forbidden)

    def rejected(*args, **kwargs):
        raise RuntimeError("fixture KIS error")

    client, _, calendar, clock = setup()
    client._authenticated_get = rejected
    market = readiness_market(client, calendar, clock)
    with pytest.raises(DataUnavailable, match="paper_ranking_unavailable"):
        market.candidates(clock(), 20)


def test_public_fallback_cannot_pass_kis_readiness():
    args = setup()
    args[1].candidates = lambda *_: (["005930"], "public_marketcap_universe")
    assert probe(*args)["checks"]["candidates"]["status"] == "failed"
