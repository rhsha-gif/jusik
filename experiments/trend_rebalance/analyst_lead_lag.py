"""EXP-009 stage 1 · Is an analyst downgrade LEADING or LAGGING the price?

Every sell rule rejected so far used price alone. An analyst downgrade is the
first non-price signal available, but it is only useful if it carries
information the price has not already expressed.

    lagging   price already fell before the downgrade, and nothing unusual
              happens after  ->  same thing as a price rule, already rejected
    leading   price falls MORE than baseline after the downgrade  ->  new information

Measured against two baselines so the market's own drift cannot be mistaken for
signal:
    * raw return over the window
    * market-adjusted (stock minus SPY over the same window)
    * a random-date control drawn from the same symbols and period

Research only.
"""

from __future__ import annotations

import csv
import random
import statistics
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

WINDOWS_PRE = [20, 60]
WINDOWS_POST = [20, 60, 120]
CONTROL_SAMPLES = 20_000
SEED = 20260726


@dataclass
class Series:
    dates: list[str]
    closes: list[float]


def load(symbol: str) -> Series | None:
    path = DATA_DIR / f"{symbol}.csv"
    if not path.exists():
        return None
    dates: list[str] = []
    closes: list[float] = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dates.append(row["date"])
            closes.append(float(row["close"]))
    return Series(dates, closes)


def window_return(series: Series, index: int, offset: int) -> float | None:
    other = index + offset
    if other < 0 or other >= len(series.closes) or index < 0 or index >= len(series.closes):
        return None
    if offset < 0:
        start, end = other, index
    else:
        start, end = index, other
    if series.closes[start] <= 0:
        return None
    return series.closes[end] / series.closes[start] - 1.0


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    spy = load("SPY")
    if spy is None:
        print("SPY missing")
        return 1
    spy_index = {d: i for i, d in enumerate(spy.dates)}

    events = list(csv.DictReader((DATA_DIR / "_analyst.csv").open(encoding="utf-8")))
    cache: dict[str, Series | None] = {}

    def series_for(symbol: str) -> Series | None:
        if symbol not in cache:
            cache[symbol] = load(symbol)
        return cache[symbol]

    buckets: dict[str, dict[int, list[float]]] = {
        "down": {w: [] for w in [-x for x in WINDOWS_PRE] + WINDOWS_POST},
        "up": {w: [] for w in [-x for x in WINDOWS_PRE] + WINDOWS_POST},
        "control": {w: [] for w in [-x for x in WINDOWS_PRE] + WINDOWS_POST},
    }
    used_symbols: set[str] = set()
    matched = 0

    for event in events:
        action = event["action"]
        if action not in ("down", "up"):
            continue
        series = series_for(event["symbol"])
        if series is None:
            continue
        pos = bisect_left(series.dates, event["date"])
        if pos >= len(series.dates):
            continue
        day = series.dates[pos]
        if day not in spy_index:
            continue
        matched += 1
        used_symbols.add(event["symbol"])
        for offset in [-x for x in WINDOWS_PRE] + WINDOWS_POST:
            stock = window_return(series, pos, offset)
            market = window_return(spy, spy_index[day], offset)
            if stock is None or market is None:
                continue
            buckets[action][offset].append(stock - market)

    rng = random.Random(SEED)
    symbols = sorted(used_symbols)
    for _ in range(CONTROL_SAMPLES):
        symbol = rng.choice(symbols)
        series = series_for(symbol)
        if series is None or len(series.dates) < 400:
            continue
        pos = rng.randrange(150, len(series.dates) - 150)
        day = series.dates[pos]
        if day not in spy_index:
            continue
        for offset in [-x for x in WINDOWS_PRE] + WINDOWS_POST:
            stock = window_return(series, pos, offset)
            market = window_return(spy, spy_index[day], offset)
            if stock is None or market is None:
                continue
            buckets["control"][offset].append(stock - market)

    print("=" * 88)
    print("EXP-009 stage 1  analyst rating changes: lead or lag?   (market-adjusted returns)")
    print(f"matched events {matched:,}   symbols {len(used_symbols)}   control draws {CONTROL_SAMPLES:,}")
    print("=" * 88)
    header = f"{'window':>10s}" + "".join(f"{name:>16s}" for name in ("DOWNGRADE", "UPGRADE", "control"))
    print(header)
    print("-" * 88)
    for offset in [-x for x in WINDOWS_PRE] + WINDOWS_POST:
        label = f"{'before' if offset < 0 else 'after'} {abs(offset)}d"
        cells = ""
        for name in ("down", "up", "control"):
            values = buckets[name][offset]
            cells += f"{statistics.median(values) * 100:15.2f}%" if values else f"{'-':>16s}"
        print(f"{label:>10s}{cells}")
    print("-" * 88)
    print("excess vs control (median):")
    for offset in WINDOWS_POST:
        d = statistics.median(buckets["down"][offset]) - statistics.median(buckets["control"][offset])
        u = statistics.median(buckets["up"][offset]) - statistics.median(buckets["control"][offset])
        print(f"   after {offset:>3d}d     downgrade {d * 100:+6.2f}%p     upgrade {u * 100:+6.2f}%p")
    print("=" * 88)

    with (RESULTS_DIR / "analyst_lead_lag.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "window_days", "n", "median", "mean"])
        for name in ("down", "up", "control"):
            for offset, values in buckets[name].items():
                if values:
                    writer.writerow([name, offset, len(values), statistics.median(values), statistics.fmean(values)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
