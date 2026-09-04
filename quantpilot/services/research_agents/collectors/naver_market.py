"""Standard-library client for Naver Finance's public market endpoints.

Chosen over pykrx on 2026-09-04: since 2025-12 KRX's own data service needs a
personal login for indices, sectors and investor flows, while the same data is
public on m.stock.naver.com (JSON) and fchart.stock.naver.com (XML) with no
key. Implements the `MarketDataClient` protocol from `collectors.krx`.

Two limits are structural and surface as empty results (which the snapshot
treats as failures where they matter): the industry list carries only the
current day's change, and the index-level investor flow endpoint is a same-day
snapshot rather than a history.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import date as _date, datetime, timedelta, timezone
from typing import Any, Callable

from quantpilot.services.research_agents.collectors.krx import CollectionError

NAVER_MOBILE_API = "https://m.stock.naver.com/api"
NAVER_FCHART = "https://fchart.stock.naver.com/sise.nhn"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuantPilot-research/1.0"
_ITEM_RE = re.compile(r'<item data="([^"]+)"')
_KST = timezone(timedelta(hours=9))


def _iso(value: str) -> str:
    return value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def _number(value: Any) -> float:
    """'6,682.94' / '+2,840' / '-22,947' / '46.71%' → float."""

    text = str(value).replace(",", "").replace("%", "").replace("+", "").strip()
    return float(text) if text else 0.0


def _is_code(value: str) -> bool:
    return value.isdigit() and len(value) == 6


class NaverMarketClient:
    def __init__(
        self,
        pause_s: float = 0.3,
        retries: int = 3,
        timeout_s: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], _date] | None = None,
        fetch: Callable[[str], bytes] | None = None,
    ) -> None:
        self._pause_s = pause_s
        self._retries = retries
        self._timeout_s = timeout_s
        self._sleep = sleep
        self._today = today or (lambda: datetime.now(_KST).date())
        self._fetch = fetch or self._http_get

    def _http_get(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "*/*"})
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310 - fixed https host  # nosemgrep
            return response.read()

    def _get(self, url: str, *, encoding: str = "utf-8") -> str:
        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                body = self._fetch(url)
                self._sleep(self._pause_s)
                return body.decode(encoding, errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise CollectionError(f"naver: 404 for {url}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
            self._sleep(self._pause_s * attempt)
        raise CollectionError(f"naver: request failed after {self._retries} attempts: {type(last_error).__name__}")

    def _json(self, path: str) -> Any:
        return json.loads(self._get(f"{NAVER_MOBILE_API}/{path}"))

    def _fchart(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        """Daily bars between start and end (compact YYYYMMDD), ascending."""

        span = (_date.fromisoformat(_iso(end)) - _date.fromisoformat(_iso(start))).days + 1
        count = max(span, 10)
        xml = self._get(f"{NAVER_FCHART}?symbol={symbol}&timeframe=day&count={count}&requestType=0", encoding="cp949")
        rows: list[dict[str, Any]] = []
        for raw in _ITEM_RE.findall(xml):
            parts = raw.split("|")
            if len(parts) != 6 or not (start <= parts[0] <= end):
                continue
            rows.append(
                {
                    "date": _iso(parts[0]),
                    "open": float(parts[1]),
                    "high": float(parts[2]),
                    "low": float(parts[3]),
                    "close": float(parts[4]),
                    "volume": int(float(parts[5])),
                }
            )
        rows.sort(key=lambda r: r["date"])
        return rows

    # --- MarketDataClient -----------------------------------------------------------

    def index_ohlcv(self, ticker: str, start: str, end: str) -> list[dict[str, Any]]:
        return [{"date": r["date"], "close": r["close"], "volume": r["volume"]} for r in self._fchart(ticker, start, end)]

    def index_changes(self, market: str, start: str, end: str) -> list[dict[str, Any]]:
        """Naver's industry groups carry today's change only; for any other day the list is empty."""

        if _iso(end) != self._today().isoformat():
            return []
        payload = self._json("stocks/industry?pageSize=100&page=1")
        return [{"name": g["name"], "change_pct": _number(g.get("changeRate", 0))} for g in payload.get("groups", [])]

    def trading_value_by_date(self, market: str, start: str, end: str) -> list[dict[str, Any]]:
        """Today's index-level net buying. Naver reports 억원; returned in KRW to honour the protocol."""

        payload = self._json(f"index/{market}/trend")
        bizdate = str(payload.get("bizdate", ""))
        if not bizdate or not (start <= bizdate <= end):
            return []
        return [
            {
                "date": _iso(bizdate),
                "foreign": _number(payload.get("foreignValue", 0)) * 100_000_000,
                "institution": _number(payload.get("institutionalValue", 0)) * 100_000_000,
                "individual": _number(payload.get("personalValue", 0)) * 100_000_000,
            }
        ]

    def stock_ohlcv(self, ticker: str, start: str, end: str) -> list[dict[str, Any]]:
        if not _is_code(ticker):
            raise CollectionError(f"not a KRX code: {ticker!r}")
        out: list[dict[str, Any]] = []
        prev_close: float | None = None
        for bar in self._fchart(ticker, start, end):
            change = round((bar["close"] / prev_close - 1.0) * 100.0, 2) if prev_close else 0.0
            out.append({"date": bar["date"], "close": bar["close"], "change_pct": change, "volume": bar["volume"]})
            prev_close = bar["close"]
        return out

    def ticker_name(self, ticker: str) -> str:
        if not _is_code(ticker):
            raise CollectionError(f"not a KRX code: {ticker!r}")
        payload = self._json(f"stock/{ticker}/basic")
        name = str(payload.get("stockName", "")).strip()
        if not name:
            raise CollectionError(f"naver: no name for {ticker}")
        return name
