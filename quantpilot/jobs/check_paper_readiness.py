"""Manual paper read probes. Never arm, store account data, or submit orders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os

from quantpilot.jobs.check_kis_paper_connection import connection_config
from quantpilot.packages.core.kis_paper import (
    KIS_BALANCE_ENDPOINT,
    KIS_CURRENT_PRICE_ENDPOINT,
    KIS_L2_ENDPOINT,
    KIS_PAPER_BASE_URL,
    KIS_TOKEN_ENDPOINT,
    KisPaperClient,
    KisPaperConfigurationError,
    StrictUrllibKisPaperTransport,
)
from quantpilot.packages.core.marketdata.kis_paper import KisPaperMarketDataProvider
from quantpilot.paper.calendar import Calendar
from quantpilot.paper.data import DataUnavailable, LimitedTransport, PaperMarket
from quantpilot.paper.risk import fresh_quote
from quantpilot.paper.strategy import validate_bars


class ReadinessTransport(StrictUrllibKisPaperTransport):
    def request_json(self, method, url, **kwargs):
        reads = {
            KIS_BALANCE_ENDPOINT,
            KIS_CURRENT_PRICE_ENDPOINT,
            KIS_L2_ENDPOINT,
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            "/uapi/domestic-stock/v1/quotations/volume-rank",
        }
        allowed = {("GET", KIS_PAPER_BASE_URL + path) for path in reads}
        allowed.add(("POST", KIS_PAPER_BASE_URL + KIS_TOKEN_ENDPOINT))
        if (method, url) not in allowed:
            raise KisPaperConfigurationError("readiness_endpoint_blocked")
        return super().request_json(method, url, **kwargs)


def readiness_market(client, calendar, clock):
    def reject_public_fallback(_market):
        raise DataUnavailable("paper_ranking_unavailable")

    quotes = KisPaperMarketDataProvider(
        client, session_authority=calendar, clock=clock
    )
    return PaperMarket(client, quotes, public_fetch=reject_public_fallback)


def probe(client, market, calendar, clock):
    """Expose only aggregate checks, never provider payloads or exception text."""
    checks = {}

    def check(name, operation):
        try:
            detail = operation()
            checks[name] = {"status": "passed", **(detail or {})}
        except Exception as exc:
            checks[name] = {"status": "failed", "error_type": type(exc).__name__}

    def price():
        client.get_current_price("005930")

    def balance():
        client.get_balance()

    def candidates():
        codes, source = market.candidates(clock(), 20)
        if not codes or source != "kis_paper_ranking":
            raise ValueError("empty_candidates")
        return {"count": len(codes), "source": source}

    def minutes():
        now = clock()
        bars = validate_bars(market.minutes("005930", now), now)
        if not bars:
            raise ValueError("missing_minutes")
        age = now - (bars[-1].start + timedelta(minutes=1))
        if not timedelta(0) <= age <= timedelta(minutes=2):
            raise ValueError("missing_or_stale_minutes")
        return {
            "completed_bars": len(bars),
            "latest_start": bars[-1].start.isoformat(),
        }

    def quotes():
        rows = market.quotes(["005930"])
        fresh_quote(rows["005930"], clock(), 15)

    check("price", price)
    check("balance", balance)
    check("candidates", candidates)
    try:
        now = clock()
        session = calendar.session(now)
        opened = session is not None and session.trading(now)
        checks["session"] = {
            "status": "passed" if opened else "pending_open_session"
        }
    except Exception as exc:
        opened = False
        checks["session"] = {"status": "failed", "error_type": type(exc).__name__}
    if opened:
        check("minutes", minutes)
        check("quotes", quotes)
    else:
        for name in ("minutes", "quotes"):
            checks[name] = {"status": "pending_open_session"}
    statuses = {entry["status"] for entry in checks.values()}
    status = (
        "failed"
        if "failed" in statuses
        else "passed" if statuses == {"passed"} else "pending_open_session"
    )
    return {
        "data_mode": "paper_trading",
        "status": status,
        "order_authority": False,
        "checks": checks,
    }


def main():
    try:
        config = connection_config(os.environ)
        transport = LimitedTransport(ReadinessTransport())
        client = KisPaperClient(config, transport=transport)
        if not config.access_token:
            config = config.with_access_token(client.request_access_token().access_token)
            client = KisPaperClient(config, transport=transport)
        calendar = Calendar()
        clock = lambda: datetime.now(timezone.utc)
        result = probe(client, readiness_market(client, calendar, clock), calendar, clock)
    except Exception as exc:
        result = {
            "status": "failed",
            "stage": "setup",
            "order_authority": False,
            "error_type": type(exc).__name__,
        }
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
