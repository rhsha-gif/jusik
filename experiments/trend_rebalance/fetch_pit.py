"""Point-in-time-ish US large-cap universe INCLUDING names that died (stdlib only).

The previous fetcher required ~2400 bars, which silently deleted every company
that was acquired, merged or went bankrupt -- i.e. it manufactured the very
survivorship bias it was meant to avoid. This one keeps them.

How Yahoo serves dead tickers: real closes up to the delisting date, then None.
So a short valid span IS the signal that the name disappeared, and the last
valid close is the best available liquidation price.

Two contamination guards:
  * ticker reuse -- a long gap of Nones followed by fresh prices means the
    symbol was recycled by a different company (BBBY -> Beyond Inc.). Truncate
    at the gap.
  * too little history -- fewer than MIN_VALID_BARS usable closes is dropped,
    because the strategy needs a warmup before it could ever have traded it.

Research only.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
RANGE = "10y"
MIN_VALID_BARS = 300          # need some history before the rules can act
REUSE_GAP_SESSIONS = 60       # >3 months of blanks then prices again = recycled ticker

# --- survivors: broad 2016-vintage large caps, laggards deliberately included
SURVIVORS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "INTC", "CSCO", "ORCL", "IBM",
    "QCOM", "TXN", "AVGO", "ADBE", "CRM", "NVDA", "AMD", "MU", "HPQ", "HPE",
    "STX", "WDC", "NTAP", "AKAM", "EBAY", "PYPL", "NFLX", "DIS", "CMCSA",
    "T", "VZ", "TMUS",
    "JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "AXP", "SCHW",
    "BLK", "COF", "TRV", "ALL", "PGR", "MET", "PRU", "AIG",
    "JNJ", "PFE", "MRK", "ABBV", "AMGN", "GILD", "BIIB", "LLY", "BMY", "UNH",
    "CVS", "CI", "HUM", "ABT", "MDT", "SYK", "BSX", "BAX", "ZTS",
    "XOM", "CVX", "COP", "SLB", "HAL", "OXY", "PSX", "VLO", "MPC", "KMI",
    "WMB", "DVN", "APA",
    "BA", "CAT", "DE", "MMM", "HON", "GE", "UPS", "FDX", "LMT", "RTX", "NOC",
    "GD", "EMR", "ETN", "ITW", "CSX", "UNP", "NSC", "DAL", "UAL", "LUV",
    "PG", "KO", "PEP", "WMT", "COST", "TGT", "HD", "LOW", "MCD", "SBUX",
    "NKE", "KHC", "GIS", "CPB", "CL", "KMB", "MO", "PM", "MDLZ", "CLX",
    "SYY", "YUM", "M", "KSS", "BBY", "F", "GM", "COTY",
    "DD", "NEM", "FCX", "MOS", "NUE", "CLF", "DUK", "SO", "D", "NEE",
    "AEP", "EXC", "XEL", "SPG", "PLD", "AMT", "CCI", "O",
]

# --- names that were large in 2016 and later vanished (acquired / merged / failed)
DELISTED = [
    "TWX",    # Time Warner    -> AT&T 2018
    "ESRX",   # Express Scripts-> Cigna 2018
    "AET",    # Aetna          -> CVS 2018
    "ANDV",   # Andeavor       -> Marathon 2018
    "SCG",    # SCANA          -> Dominion 2019
    "JNPR", "DISH", "PARA", "BK", "K", "GPS", "JWN", "X", "FOX",
    "MON", "CA", "RHT", "CELG", "RTN", "ETFC", "PBCT", "WCG", "DPS",
    "AGN", "MYL", "CTL", "DISCA", "VIAB", "TIF", "NBL", "CXO", "XLNX",
    "MXIM", "ALXN", "CERN", "ABMD", "SGEN", "ATVI", "VMW", "TWTR",
    "SIVB", "FRC", "SBNY", "INFO", "APC", "LB", "WYND", "ARNC", "LLL",
]

BLOCKLIST = {"BBBY"}  # ticker recycled by an unrelated company


def fetch_raw(symbol: str) -> tuple[list[int], dict[str, list[float | None]], list[float | None]]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={RANGE}&interval=1d&events=div%2Csplit"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    result = payload["chart"]["result"][0]
    return result["timestamp"], result["indicators"]["quote"][0], result["indicators"]["adjclose"][0]["adjclose"]


def build_rows(stamps: list[int], quote: dict[str, list[float | None]], adj: list[float | None]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    blank_run = 0
    for index, stamp in enumerate(stamps):
        close = quote["close"][index]
        adjusted = adj[index]
        opening = quote["open"][index]
        high = quote["high"][index]
        low = quote["low"][index]
        if (
            close is None
            or adjusted is None
            or opening is None
            or high is None
            or low is None
            or close == 0
        ):
            blank_run += 1
            continue
        if blank_run >= REUSE_GAP_SESSIONS and rows:
            break  # long blank then prices again -> recycled ticker, stop here
        blank_run = 0
        ratio = adjusted / close
        rows.append(
            {
                "date": datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat(),
                "open": round(opening * ratio, 6),
                "high": round(high * ratio, 6),
                "low": round(low * ratio, 6),
                "close": round(adjusted, 6),
                "volume": int(quote["volume"][index] or 0),
            }
        )
    return rows


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    dropped: list[str] = []

    for group, symbols in (("survivor", SURVIVORS), ("delisted_candidate", DELISTED)):
        for symbol in symbols:
            if symbol in BLOCKLIST:
                dropped.append(f"{symbol}(blocklist:ticker_reuse)")
                continue
            try:
                stamps, quote, adj = fetch_raw(symbol)
                rows = build_rows(stamps, quote, adj)
            except (urllib.error.URLError, KeyError, IndexError, TypeError) as exc:
                dropped.append(f"{symbol}({type(exc).__name__})")
                continue
            if len(rows) < MIN_VALID_BARS:
                dropped.append(f"{symbol}({len(rows)}bars)")
                continue

            path = DATA_DIR / f"{symbol}.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "volume"])
                writer.writeheader()
                writer.writerows(rows)

            last = date.fromisoformat(str(rows[-1]["date"]))
            manifest.append(
                {
                    "symbol": symbol,
                    "group": group,
                    "bars": len(rows),
                    "first": rows[0]["date"],
                    "last": rows[-1]["date"],
                    # anything ending well before the data cutoff really did disappear
                    "delisted": "yes" if last < date(2026, 6, 1) else "no",
                }
            )
            time.sleep(0.2)

    with (DATA_DIR / "_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "group", "bars", "first", "last", "delisted"])
        writer.writeheader()
        writer.writerows(manifest)

    alive = [row for row in manifest if row["delisted"] == "no"]
    dead = [row for row in manifest if row["delisted"] == "yes"]
    print(f"kept {len(manifest)}   alive {len(alive)}   DELISTED {len(dead)}")
    print("delisted names actually captured:")
    for row in sorted(dead, key=lambda r: str(r["last"])):
        print(f"   {row['symbol']:7s} {row['first']} -> {row['last']}  ({row['bars']} bars)")
    print(f"dropped {len(dropped)}: {', '.join(dropped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
