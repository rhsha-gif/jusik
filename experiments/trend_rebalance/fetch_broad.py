"""Download a broad 2016-vintage US large-cap universe (stdlib only).

Selection rule, applied deliberately to suppress hindsight:
  include a name if it was a well-known US large cap in mid-2016,
  REGARDLESS of what happened afterwards.

Laggards are included on purpose (GE, INTC, XOM, T, VZ, WBA, KHC, M, GPS,
X, FCX, DVN, APA, PARA, DISH ...). Winners are not privileged.

Known, stated limitation: names that were fully delisted or acquired before
2026 are still missing, because free endpoints do not serve them. So this is
a *reduced*-bias universe, not a survivorship-free one.

Research only.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_broad"
RANGE = "10y"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MIN_BARS = 2400  # ~9.5y of sessions

UNIVERSE = [
    # information technology / communication
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "INTC", "CSCO", "ORCL", "IBM",
    "QCOM", "TXN", "AVGO", "ADBE", "CRM", "NVDA", "AMD", "MU", "HPQ", "HPE",
    "STX", "WDC", "NTAP", "JNPR", "AKAM", "EBAY", "PYPL", "NFLX", "DIS",
    "CMCSA", "T", "VZ", "TMUS", "DISH", "PARA", "FOX",
    # financials
    "JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "AXP", "BK", "SCHW",
    "BLK", "COF", "TRV", "ALL", "PGR", "MET", "PRU", "AIG",
    # health care
    "JNJ", "PFE", "MRK", "ABBV", "AMGN", "GILD", "BIIB", "LLY", "BMY", "UNH",
    "CVS", "CI", "HUM", "ABT", "MDT", "SYK", "BSX", "BAX", "ZTS",
    # energy
    "XOM", "CVX", "COP", "SLB", "HAL", "OXY", "PSX", "VLO", "MPC", "KMI",
    "WMB", "DVN", "APA",
    # industrials
    "BA", "CAT", "DE", "MMM", "HON", "GE", "UPS", "FDX", "LMT", "RTX", "NOC",
    "GD", "EMR", "ETN", "ITW", "CSX", "UNP", "NSC", "DAL", "UAL", "LUV",
    # consumer
    "PG", "KO", "PEP", "WMT", "COST", "TGT", "HD", "LOW", "MCD", "SBUX",
    "NKE", "KHC", "GIS", "K", "CPB", "CL", "KMB", "MO", "PM", "MDLZ", "CLX",
    "SYY", "YUM", "GPS", "M", "JWN", "KSS", "BBY", "F", "GM",
    # materials / utilities / real estate
    "DD", "NEM", "FCX", "MOS", "NUE", "X", "CLF", "DUK", "SO", "D", "NEE",
    "AEP", "EXC", "XEL", "SPG", "PLD", "AMT", "CCI", "O",
]


def fetch(symbol: str) -> list[dict[str, object]]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={RANGE}&interval=1d&events=div%2Csplit"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"]["adjclose"][0]["adjclose"]

    rows: list[dict[str, object]] = []
    for index, stamp in enumerate(stamps):
        close = quote["close"][index]
        adjusted = adjclose[index]
        opening = quote["open"][index]
        high = quote["high"][index]
        low = quote["low"][index]
        if None in (close, adjusted, opening, high, low) or close == 0:
            continue
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
    kept: list[str] = []
    dropped: list[str] = []
    for symbol in UNIVERSE:
        try:
            rows = fetch(symbol)
        except (urllib.error.URLError, KeyError, IndexError, TypeError) as exc:
            dropped.append(f"{symbol} ({type(exc).__name__})")
            continue
        if len(rows) < MIN_BARS:
            dropped.append(f"{symbol} (only {len(rows)} bars)")
            continue
        path = DATA_DIR / f"{symbol}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            writer.writerows(rows)
        kept.append(symbol)
        time.sleep(0.25)

    print(f"kept {len(kept)} / {len(UNIVERSE)}")
    if dropped:
        print("dropped:", ", ".join(dropped))
    (DATA_DIR / "_universe.txt").write_text("\n".join(kept), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
