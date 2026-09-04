"""KRX market snapshot through Naver Finance's public JSON/XML endpoints.

Standard library only. pykrx was audited on 2026-09-04 and rejected: since
2025-12 KRX's own data service needs a personal login for indices, sectors
and investor flows, and only stock OHLCV (which pykrx itself fetches from
Naver) works without one. The same data is public on m.stock.naver.com and
fchart.stock.naver.com without a key. `NaverMarketClient` implements the
`MarketDataClient` protocol; the unit tests use a fake.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from quantpilot.services.research_agents.models import (
    InvestorFlows,
    MarketSnapshot,
    SectorMove,
    WatchlistRow,
)

KOSPI_INDEX = "KOSPI"
KOSDAQ_INDEX = "KOSDAQ"
_VOLUME_WINDOW = 20
_HISTORY_CALENDAR_DAYS = 45  # enough calendar days to cover 20 sessions plus holidays
_DEFAULT_WATCHLIST = Path(__file__).resolve().parent.parent / "config" / "watchlist.json"


class CollectionError(RuntimeError):
    """Raised when any part of the snapshot could not be collected (fail-closed)."""


class MarketDataClient(Protocol):
    """Plain-python view of the handful of pykrx calls the snapshot needs.

    Rows are dicts so the snapshot logic stays free of pandas and testable.
    """

    def index_ohlcv(self, ticker: str, start: str, end: str) -> list[dict[str, Any]]:
        """[{"date": "YYYY-MM-DD", "close": float, "volume": int}] ascending by date."""
        ...

    def index_changes(self, market: str, start: str, end: str) -> list[dict[str, Any]]:
        """[{"name": str, "change_pct": float}] for every index of `market` over start..end."""
        ...

    def trading_value_by_date(self, market: str, start: str, end: str) -> list[dict[str, Any]]:
        """[{"date", "foreign", "institution", "individual"}] net buy value in KRW, ascending."""
        ...

    def stock_ohlcv(self, ticker: str, start: str, end: str) -> list[dict[str, Any]]:
        """[{"date", "close", "change_pct", "volume"}] ascending by date."""
        ...

    def ticker_name(self, ticker: str) -> str:
        """Human name for a KRX ticker."""
        ...


@dataclass(frozen=True)
class WatchlistEntry:
    code: str
    name: str
    theme: str = ""


def load_watchlist(path: Path | None = None) -> list[WatchlistEntry]:
    source = path or _DEFAULT_WATCHLIST
    data = json.loads(source.read_text(encoding="utf-8"))
    entries = [WatchlistEntry(**row) for row in data["symbols"]]
    if not entries:
        raise CollectionError(f"watchlist is empty: {source}")
    return entries


def _compact(value: str) -> str:
    return value.replace("-", "")


def _iso(value: str) -> str:
    return value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def _to_krw_100m(value: float) -> float:
    return round(value / 100_000_000, 1)


def _pct_change(rows: list[dict[str, Any]]) -> tuple[float, float]:
    if len(rows) < 2:
        raise CollectionError("index history shorter than two sessions")
    last, prev = rows[-1], rows[-2]
    if not prev["close"]:
        raise CollectionError("previous close is zero")
    return float(last["close"]), round((last["close"] / prev["close"] - 1.0) * 100.0, 2)


def collect_market_snapshot(
    session_date: str,
    client: MarketDataClient,
    watchlist: list[WatchlistEntry] | None = None,
) -> MarketSnapshot:
    """Build the day's snapshot or raise `CollectionError`; never a partial object."""

    entries = watchlist or load_watchlist()
    end = _compact(session_date)
    start = _compact((_date.fromisoformat(_iso(session_date)) - timedelta(days=_HISTORY_CALENDAR_DAYS)).isoformat())
    try:
        kospi_rows = client.index_ohlcv(KOSPI_INDEX, start, end)
        kosdaq_rows = client.index_ohlcv(KOSDAQ_INDEX, start, end)
        if not kospi_rows or _iso(kospi_rows[-1]["date"]) != _iso(session_date):
            raise CollectionError(f"no KOSPI session row for {session_date} (holiday or data not yet published)")
        prev_session = _compact(kospi_rows[-2]["date"])
        kospi_close, kospi_change = _pct_change(kospi_rows)
        kosdaq_close, kosdaq_change = _pct_change(kosdaq_rows)

        sector_changes = [
            SectorMove(name=row["name"], change_pct=round(float(row["change_pct"]), 2))
            for row in client.index_changes("KOSPI", prev_session, end)
            if row["name"] not in ("코스피", "코스피 200", "코스피 100", "코스피 50")
        ]
        sector_sorted = sorted(sector_changes, key=lambda item: item.change_pct, reverse=True)

        flows_rows = client.trading_value_by_date("KOSPI", end, end)
        if not flows_rows:
            raise CollectionError("no investor flow row")
        flow = flows_rows[-1]
        flows = InvestorFlows(
            foreign=_to_krw_100m(float(flow["foreign"])),
            institution=_to_krw_100m(float(flow["institution"])),
            individual=_to_krw_100m(float(flow["individual"])),
        )

        watch_rows: list[WatchlistRow] = []
        for entry in entries:
            rows = client.stock_ohlcv(entry.code, start, end)
            if not rows or _iso(rows[-1]["date"]) != _iso(session_date):
                raise CollectionError(f"no session row for {entry.code} on {session_date}")
            last = rows[-1]
            history = [int(r["volume"]) for r in rows[:-1][-_VOLUME_WINDOW:]]
            ratio = None
            if len(history) >= _VOLUME_WINDOW and sum(history) > 0:
                ratio = round(int(last["volume"]) / (sum(history) / len(history)), 2)
            watch_rows.append(
                WatchlistRow(
                    symbol=entry.code,
                    name=entry.name or client.ticker_name(entry.code),
                    theme=entry.theme,
                    close=float(last["close"]),
                    change_pct=round(float(last["change_pct"]), 2),
                    volume=int(last["volume"]),
                    volume_ratio_20d=ratio,
                )
            )
    except CollectionError:
        raise
    except Exception as exc:  # noqa: BLE001 - any upstream failure is a collection failure
        raise CollectionError(f"snapshot collection failed: {type(exc).__name__}: {exc}") from exc

    return MarketSnapshot(
        date=_iso(session_date),
        kospi_close=kospi_close,
        kospi_change_pct=kospi_change,
        kosdaq_close=kosdaq_close,
        kosdaq_change_pct=kosdaq_change,
        sector_top=sector_sorted[:3],
        sector_bottom=list(reversed(sector_sorted[-3:])) if len(sector_sorted) >= 3 else [],
        investor_flows=flows,
        watchlist_rows=watch_rows,
    )
