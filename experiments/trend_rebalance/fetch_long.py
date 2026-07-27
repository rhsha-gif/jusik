"""Full-history US universe (stdlib only) -- adds the eras the 10y sample lacks.

Every conclusion so far rests on 2016-2026, a stretch in which momentum worked
almost without interruption. The single biggest untested risk is that the
ranking edge is an artefact of that regime. This fetches everything Yahoo will
serve, which for most survivors reaches the 1980s, and adds the companies that
died in 2000-2002 and 2008-2009.

On survivorship, stated plainly rather than hidden:
    the pre-2010 slice is badly biased -- only names still quoted today can be
    downloaded, so the losers of those eras are largely missing. Absolute
    returns from that period are therefore inflated and must not be quoted as
    performance.
    What IS defensible is the paired measurement this data is for: the ranking
    arm and the random control draw from the SAME biased pool on the SAME dates,
    so the bias inflates both and largely cancels in the difference.

Contamination guards are inherited from fetch_pit.py: recycled tickers are
truncated at a long blank run, and names with too little usable history drop.

Research only.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fetch_pit import BLOCKLIST, DELISTED, REUSE_GAP_SESSIONS, SURVIVORS

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

DATA_DIR = Path(__file__).resolve().parent / "data_long"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MIN_VALID_BARS = 1_000        # a long-history study needs real depth

# Names that were large before 2010 and are still quoted, giving depth the
# 2016-vintage list lacks.
OLD_GUARD = [
    "GE", "F", "GM", "CAT", "MMM", "BA", "DIS", "KO", "PEP", "PG", "JNJ",
    "MRK", "PFE", "XOM", "CVX", "IBM", "HPQ", "INTC", "MSFT", "AAPL", "ORCL",
    "CSCO", "TXN", "AMD", "MU", "AMAT", "ADI", "KLAC", "LRCX", "NVDA",
    "WMT", "HD", "MCD", "SBUX", "NKE", "TGT", "LOW", "COST", "AXP", "JPM",
    "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "AIG", "MET", "PRU", "ALL",
    "TRV", "T", "VZ", "SO", "DUK", "D", "AEP", "EXC", "ED", "PEG", "PPL",
    "UNP", "CSX", "NSC", "UPS", "FDX", "LUV", "DAL", "HON", "EMR", "ITW",
    "DE", "LMT", "NOC", "GD", "RTX", "ADM", "CAG", "GIS", "K", "SYY", "CL",
    "KMB", "CLX", "MO", "HSY", "MKC", "HRL", "TSN", "STZ", "EL", "COP",
    "SLB", "HAL", "OXY", "BKR", "MRO", "APA", "DVN", "EOG", "PSX", "VLO",
    "NEM", "FCX", "NUE", "DOW", "DD", "PPG", "SHW", "ECL", "APD", "LIN",
    "AMGN", "BIIB", "GILD", "LLY", "BMY", "ABT", "BDX", "BSX", "MDT", "SYK",
    "ZBH", "UNH", "CI", "HUM", "CVS", "MCK", "CAH", "ABC", "DGX", "LH",
    "SPGI", "MCO", "ICE", "CME", "NDAQ", "BLK", "BK", "STT", "SCHW", "COF",
    "DFS", "V", "MA", "ADP", "PAYX", "INTU", "ADSK", "ANSS", "CDNS", "SNPS",
    "AKAM", "JBLU", "ALK", "HAS", "MAT", "WHR", "LEG", "NWL", "SEE", "IP",
    "PKG", "WY", "VMC", "MLM", "ROK", "DOV", "PH", "PNR", "SWK", "TT",
    "JCI", "CMI", "PCAR", "GWW", "FAST", "MSM", "URI", "RSG", "WM",
]

# Companies that were large and then failed or were taken out during the two
# regimes missing from the 10y sample. Most will 404 -- Yahoo drops long-dead
# tickers -- and the run reports exactly which ones came back.
CASUALTIES = [
    # dot-com bust
    "LU", "NT", "GX", "WCOM", "ENE", "JDSU", "CMGI", "PALM", "SUNW", "YHOO",
    "AOL", "EK", "Q", "TYC", "AV", "BRCD", "NOVL", "SGI", "ATHM", "EGGS",
    # global financial crisis
    "LEH", "LEHMQ", "BSC", "MER", "WM", "WAMUQ", "CFC", "ABK", "MBI", "FNM",
    "FRE", "NCC", "WB", "IndyMac", "DSL", "CIT", "GNW",
    # 2010s and 2020s removals
    "MOT", "RIMM", "SPLS", "TWX", "ESRX", "AET", "ANDV", "SCG", "MON", "CA",
    "RHT", "CELG", "RTN", "AGN", "MYL", "CTL", "TIF", "XLNX", "MXIM", "ALXN",
    "ATVI", "VMW", "TWTR", "SIVB", "FRC", "SBNY", "APC", "LB", "ARNC", "LLL",
    "DPS", "WCG", "PBCT", "ETFC", "NBL", "CXO", "CERN", "ABMD", "SGEN",
    "DISCA", "VIAB", "INFO", "GPS", "JWN", "X", "M", "KSS", "SPLS", "TOY",
    "CC", "BBI", "RSH", "SHLD", "JCP", "DDS", "ANF", "AEO",
]


def build_rows(stamps: list[int], quote: dict[str, list[float | None]], adj: list[float | None]) -> list[dict[str, object]]:
    """Same guards as fetch_pit, but dates are built by hand.

    datetime.fromtimestamp raises OSError on Windows for pre-1970 stamps, and
    range=max reaches 1962 for the oldest names -- exactly the history this
    fetcher exists to get.
    """
    rows: list[dict[str, object]] = []
    blank_run = 0
    for index, stamp in enumerate(stamps):
        close = quote["close"][index]
        adjusted = adj[index]
        opening = quote["open"][index]
        high = quote["high"][index]
        low = quote["low"][index]
        if close is None or adjusted is None or opening is None or high is None or low is None or close == 0:
            blank_run += 1
            continue
        # A long blank run alone is NOT proof of ticker reuse -- Yahoo's pre-2000
        # history is full of holes. Reuse shows up as a gap plus a price level that
        # has nothing to do with where the old company left off.
        if blank_run >= REUSE_GAP_SESSIONS and rows:
            previous = float(rows[-1]["close"])  # type: ignore[arg-type]
            if previous > 0 and not (1 / 3 <= adjusted / previous <= 3):
                break
        blank_run = 0
        ratio = adjusted / close
        rows.append({
            "date": (EPOCH + timedelta(seconds=int(stamp))).date().isoformat(),
            "open": round(opening * ratio, 6),
            "high": round(high * ratio, 6),
            "low": round(low * ratio, 6),
            "close": round(adjusted, 6),
            "volume": int(quote["volume"][index] or 0),
        })
    return rows


def fetch_raw(symbol: str) -> tuple[list[int], dict[str, list[float | None]], list[float | None]]:
    # range=max is silently downgraded to quarterly bars (dataGranularity 3mo).
    # Explicit epoch bounds are the only way to get daily data for the full history.
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1=0&period2={int(time.time())}&interval=1d&events=div%2Csplit"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=40) as response:
        payload = json.loads(response.read().decode())
    result = payload["chart"]["result"][0]
    return result["timestamp"], result["indicators"]["quote"][0], result["indicators"]["adjclose"][0]["adjclose"]


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    groups: list[tuple[str, list[str]]] = [
        ("survivor", SURVIVORS),
        ("old_guard", OLD_GUARD),
        ("delisted_candidate", DELISTED),
        ("casualty", CASUALTIES),
    ]

    manifest: list[dict[str, object]] = []
    dropped: list[str] = []
    recovered: list[str] = []

    for group, symbols in groups:
        for symbol in symbols:
            if symbol in seen or symbol in BLOCKLIST:
                continue
            seen.add(symbol)
            try:
                stamps, quote, adj = fetch_raw(symbol)
                rows = build_rows(stamps, quote, adj)
            except (urllib.error.URLError, KeyError, IndexError, TypeError) as exc:
                dropped.append(f"{symbol}({type(exc).__name__})")
                continue
            if len(rows) < MIN_VALID_BARS:
                dropped.append(f"{symbol}({len(rows)}bars)")
                continue

            with (DATA_DIR / f"{symbol}.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "volume"])
                writer.writeheader()
                writer.writerows(rows)

            last = date.fromisoformat(str(rows[-1]["date"]))
            first = date.fromisoformat(str(rows[0]["date"]))
            dead = last < date(2026, 6, 1)
            if group == "casualty" and dead:
                recovered.append(symbol)
            manifest.append({
                "symbol": symbol,
                "group": group,
                "bars": len(rows),
                "first": rows[0]["date"],
                "last": rows[-1]["date"],
                "delisted": "yes" if dead else "no",
                "start_year": first.year,
            })
            if len(manifest) % 40 == 0:
                print(f"  {len(manifest)} fetched ...")
            time.sleep(0.2)

    manifest.sort(key=lambda r: str(r["symbol"]))
    with (DATA_DIR / "_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "group", "bars", "first", "last", "delisted", "start_year"])
        writer.writeheader()
        writer.writerows(manifest)

    dead_count = sum(1 for r in manifest if r["delisted"] == "yes")
    by_decade: dict[str, int] = {}
    for row in manifest:
        decade = f"{int(str(row['start_year'])) // 10 * 10}s"
        by_decade[decade] = by_decade.get(decade, 0) + 1

    print()
    print(f"symbols written {len(manifest)}   delisted {dead_count}   dropped {len(dropped)}")
    print("history starts:", "  ".join(f"{k} {v}" for k, v in sorted(by_decade.items())))
    print(f"pre-2000 history available for {sum(1 for r in manifest if int(str(r['start_year'])) < 2000)} names")
    if recovered:
        print(f"dead tickers Yahoo still serves ({len(recovered)}): {', '.join(sorted(recovered))}")
    print(f"dropped: {', '.join(dropped[:30])}{' ...' if len(dropped) > 30 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
