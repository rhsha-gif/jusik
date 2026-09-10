"""Upbit daily-candle adapter.

Uses only the public Quotation API (no key, no account scope), so this module
stays entirely outside QuantPilot's broker/credential paths. Endpoint docs:
https://docs.upbit.com/reference/일day-캔들-1

Session-date convention: ``candle_date_time_utc`` (UTC midnight boundary), so
bars line up with future Binance/US-equity adapters rather than KST.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from marketdata.types import Bar

_API_URL = "https://api.upbit.com/v1/candles/days"
_PAGE_SIZE = 200
_MAX_PAGES = 100
_TIMEOUT_S = 30
_RETRY_WAITS_S = (0.5, 1.0, 2.0)
# Public quotation limit is 10 req/s; 0.15s spacing keeps us well under it.
_REQUEST_INTERVAL_S = 0.15
_USER_AGENT = "quantpilot-marketdata/0.1 (research fetcher)"


class UpbitFetchError(RuntimeError):
    """Raised when a complete candle history could not be downloaded."""


class UpbitDailySource:
    """Fetches the full daily KRW-market history for one Upbit symbol."""

    name = "upbit"

    def _request(self, url: str) -> list[dict]:
        """GET *url* and return the decoded JSON array.

        Retries 429 and 5xx up to three times with 0.5s/1s/2s waits; any other
        4xx is a caller bug (bad symbol, bad param) and fails immediately.
        """
        last_error: Exception | None = None
        for attempt in range(len(_RETRY_WAITS_S) + 1):
            if attempt > 0:
                time.sleep(_RETRY_WAITS_S[attempt - 1])
            request = urllib.request.Request(
                url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code == 429 or error.code >= 500:
                    last_error = error
                    continue
                raise UpbitFetchError(f"Upbit rejected the request ({error.code}): {url}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                continue
            if not isinstance(payload, list):
                raise UpbitFetchError(f"Unexpected non-array response from {url}: {payload!r}")
            return payload
        raise UpbitFetchError(f"Upbit request failed after retries: {url}") from last_error

    def fetch_daily(self, symbol: str) -> list[Bar]:
        """Return the complete daily history for *symbol*, oldest first.

        Pages backwards from the newest candle using the oldest
        ``candle_date_time_utc`` of each page as the next ``to`` cursor, until
        Upbit returns an empty page or the cursor stops moving.
        """
        raw_candles: list[dict] = []
        to_cursor: str | None = None
        for _ in range(_MAX_PAGES):
            params = {"market": symbol, "count": str(_PAGE_SIZE)}
            if to_cursor is not None:
                params["to"] = to_cursor
            url = f"{_API_URL}?{urllib.parse.urlencode(params)}"
            page = self._request(url)
            if not page:
                break
            raw_candles.extend(page)
            oldest = min(candle["candle_date_time_utc"] for candle in page)
            if oldest == to_cursor:
                break
            to_cursor = oldest
            if len(page) < _PAGE_SIZE:
                break
            time.sleep(_REQUEST_INTERVAL_S)
        else:
            raise UpbitFetchError(
                f"Exceeded {_MAX_PAGES} pages fetching {symbol}; refusing to loop forever"
            )

        bars_by_date: dict[str, Bar] = {}
        for candle in raw_candles:
            date = str(candle["candle_date_time_utc"])[:10]
            bars_by_date[date] = Bar(
                symbol=symbol,
                date=date,
                open=float(candle["opening_price"]),
                high=float(candle["high_price"]),
                low=float(candle["low_price"]),
                close=float(candle["trade_price"]),
                volume=float(candle["candle_acc_trade_volume"]),
            )
        if not bars_by_date:
            raise UpbitFetchError(f"Upbit returned no candles for {symbol}")
        return [bars_by_date[date] for date in sorted(bars_by_date)]
