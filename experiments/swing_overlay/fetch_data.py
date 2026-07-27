"""Download 10 years of split/dividend-adjusted US daily bars (stdlib only).

Uses the public Yahoo Finance chart endpoint so the experiment adds no new
dependency. Raw OHLC is scaled by ``adjclose / close`` so that ATR and moving
averages are computed on a continuous, split-adjusted series.

Research only: this script never touches a broker.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
RANGE = "10y"
INTERVAL = "1d"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# 30 symbols, evenly split across three volatility/behaviour buckets.
# All listed well before 2016-07 so the full 10y window is populated.
UNIVERSE: dict[str, list[str]] = {
    "high_vol_growth": [
        "NVDA", "TSLA", "AMD", "NFLX", "AMZN",
        "META", "SHOP", "ENPH", "MU", "CRM",
    ],
    "low_vol_defensive": [
        "JNJ", "PG", "KO", "PEP", "WMT",
        "MCD", "VZ", "XOM", "CVX", "MRK",
    ],
    "range_bound_cyclical": [
        "INTC", "CSCO", "IBM", "F", "GM",
        "PFE", "BAC", "C", "GILD", "T",
    ],
}


def all_symbols() -> list[tuple[str, str]]:
    return [(symbol, bucket) for bucket, names in UNIVERSE.items() for symbol in names]


def fetch_chart(symbol: str) -> list[dict[str, float | str]]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={RANGE}&interval={INTERVAL}&events=div%2Csplit"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())

    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"].get("adjclose", [{}])[0].get("adjclose")
    if adjclose is None:
        raise RuntimeError(f"{symbol}: response is missing adjclose")

    rows: list[dict[str, float | str]] = []
    for index, stamp in enumerate(stamps):
        close = quote["close"][index]
        adjusted = adjclose[index]
        opening = quote["open"][index]
        high = quote["high"][index]
        low = quote["low"][index]
        volume = quote["volume"][index]
        if None in (close, adjusted, opening, high, low) or close == 0:
            continue  # halted/incomplete session
        ratio = adjusted / close
        rows.append(
            {
                "date": datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat(),
                "open": round(opening * ratio, 6),
                "high": round(high * ratio, 6),
                "low": round(low * ratio, 6),
                "close": round(adjusted, 6),
                "volume": int(volume or 0),
            }
        )
    return rows


def write_csv(symbol: str, rows: list[dict[str, float | str]]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{symbol}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> int:
    failures: list[str] = []
    for symbol, bucket in all_symbols():
        try:
            rows = fetch_chart(symbol)
        except (urllib.error.URLError, RuntimeError, KeyError, IndexError) as exc:
            failures.append(f"{symbol}: {type(exc).__name__} {exc}")
            print(f"FAIL {symbol:6s} {type(exc).__name__}")
            continue
        write_csv(symbol, rows)
        print(f"ok   {symbol:6s} {bucket:22s} {len(rows):5d} bars  {rows[0]['date']} -> {rows[-1]['date']}")
        time.sleep(0.4)  # be polite to the public endpoint

    if failures:
        print("\nfailures:")
        for line in failures:
            print(" ", line)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
