"""EXP-017 stage 1 · Is insider cluster buying LEADING or LAGGING the price?

Deliberately the same test EXP-009 applied to analyst downgrades, so the two are
directly comparable. That test rejected analysts because the move happened
BEFORE the signal (-3.73% before, -0.23% after, and 120 days later the
downgraded names beat a random control by 1.77%p -- a lagging indicator).

Insider buying is the natural counter-case. An executive buying with their own
money is not reacting to a price move the way a rating change is, and clusters
-- several distinct insiders at one company inside a short window -- are the
version of the signal the literature treats as informative.

Two design points decide whether this test is honest:

    event date = FILING date, not transaction date.
        A Form 4 is due within two business days, so the transaction is private
        until it is filed. Dating the event at the trade would score information
        nobody could act on. This costs signal and is not optional.

    control = random dates drawn from the same symbols.
        Insiders buy in companies of a certain kind, and those companies have
        their own drift. Without the control, that drift reads as signal.

Returns are market-adjusted (stock minus SPY over the identical window).

Research only.
"""

from __future__ import annotations

import csv
import random
import statistics
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

CLUSTER_WINDOW_DAYS = 30      # distinct insiders buying inside this span form a cluster
WINDOWS_PRE = [20, 60]
WINDOWS_POST = [20, 60, 120]
CONTROL_SAMPLES = 20_000
SEED = 20260728
MIN_VALUE = 10_000.0          # ignore token purchases


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
    start, end = (other, index) if offset < 0 else (index, other)
    if series.closes[start] <= 0:
        return None
    return series.closes[end] / series.closes[start] - 1.0


def build_events(rows: list[dict[str, str]]) -> list[tuple[str, str, int, float]]:
    """(symbol, filing_date, distinct insiders, total value) -- one row per cluster.

    A cluster is grown greedily: purchases at a symbol are walked in transaction
    order and absorbed while they stay inside CLUSTER_WINDOW_DAYS of the first
    one. The event is dated at the LATEST filing in the cluster, which is the
    first moment the whole pattern was visible to an outsider.
    """
    by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        try:
            if float(row["value"]) < MIN_VALUE:
                continue
        except ValueError:
            continue
        if row["trans_date"] and row["filing_date"]:
            by_symbol[row["symbol"]].append(row)

    events: list[tuple[str, str, int, float]] = []
    for symbol, purchases in by_symbol.items():
        purchases.sort(key=lambda r: r["trans_date"])
        i = 0
        while i < len(purchases):
            anchor = date.fromisoformat(purchases[i]["trans_date"])
            group = [purchases[i]]
            j = i + 1
            while j < len(purchases):
                if date.fromisoformat(purchases[j]["trans_date"]) - anchor > timedelta(days=CLUSTER_WINDOW_DAYS):
                    break
                group.append(purchases[j])
                j += 1
            insiders = len({g["owner_cik"] for g in group})
            value = sum(float(g["value"]) for g in group)
            events.append((symbol, max(g["filing_date"] for g in group), insiders, value))
            i = j
    return events


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    spy = load("SPY")
    if spy is None:
        print("SPY missing")
        return 1
    spy_index = {d: i for i, d in enumerate(spy.dates)}

    with (DATA_DIR / "_insider.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    events = build_events(rows)

    offsets = [-x for x in WINDOWS_PRE] + WINDOWS_POST
    buckets: dict[str, dict[int, list[float]]] = {
        name: {o: [] for o in offsets}
        for name in ("1 insider", "2 insiders", "3+ insiders", "control")
    }
    counts: dict[str, int] = defaultdict(int)
    cache: dict[str, Series | None] = {}
    used: set[str] = set()

    def series_for(symbol: str) -> Series | None:
        if symbol not in cache:
            cache[symbol] = load(symbol)
        return cache[symbol]

    for symbol, filing_date, insiders, _value in events:
        series = series_for(symbol)
        if series is None:
            continue
        pos = bisect_left(series.dates, filing_date)
        if pos >= len(series.dates):
            continue
        day = series.dates[pos]
        if day not in spy_index:
            continue
        name = "1 insider" if insiders == 1 else ("2 insiders" if insiders == 2 else "3+ insiders")
        counts[name] += 1
        used.add(symbol)
        for offset in offsets:
            stock = window_return(series, pos, offset)
            market = window_return(spy, spy_index[day], offset)
            if stock is None or market is None:
                continue
            buckets[name][offset].append(stock - market)

    rng = random.Random(SEED)
    symbols = sorted(used)
    for _ in range(CONTROL_SAMPLES):
        symbol = rng.choice(symbols)
        series = series_for(symbol)
        if series is None or len(series.dates) < 400:
            continue
        pos = rng.randrange(150, len(series.dates) - 150)
        day = series.dates[pos]
        if day not in spy_index:
            continue
        for offset in offsets:
            stock = window_return(series, pos, offset)
            market = window_return(spy, spy_index[day], offset)
            if stock is None or market is None:
                continue
            buckets["control"][offset].append(stock - market)

    print("=" * 96)
    print("EXP-017 stage 1  insider cluster buying: lead or lag?   (market-adjusted, event = FILING date)")
    print(f"clusters {sum(counts.values()):,}   symbols {len(used)}   control draws {CONTROL_SAMPLES:,}")
    print(f"  1 insider {counts['1 insider']:,}   2 insiders {counts['2 insiders']:,}   3+ insiders {counts['3+ insiders']:,}")
    print("=" * 96)
    names = ("1 insider", "2 insiders", "3+ insiders", "control")
    print(f"{'window':>10s}" + "".join(f"{n:>16s}" for n in names))
    print("-" * 96)
    for offset in offsets:
        label = f"{'before' if offset < 0 else 'after'} {abs(offset)}d"
        cells = ""
        for name in names:
            values = buckets[name][offset]
            cells += f"{statistics.median(values) * 100:15.2f}%" if values else f"{'-':>16s}"
        print(f"{label:>10s}{cells}")
    print("-" * 96)
    print("excess over control (median):")
    for offset in WINDOWS_POST:
        control = statistics.median(buckets["control"][offset])
        line = f"   after {offset:>3d}d  "
        for name in names[:3]:
            values = buckets[name][offset]
            line += f"  {name} {(statistics.median(values) - control) * 100:+6.2f}%p" if values else ""
        print(line)
    print("=" * 96)
    print("compare EXP-009 (analyst downgrade): before -3.73%, after -0.23%, +1.77%p vs control at 120d")

    # The random-date control is not enough. Insiders buy INTO declines -- the 3+
    # bucket is down 11.86% over the prior 60 days -- and a beaten-down stock
    # rebounds relative to a random moment whether or not anyone bought. So the
    # random control scores mechanical reversal as if it were insider information.
    # This control instead draws dates at the SAME symbol whose trailing 60-day
    # market-adjusted return is within DECLINE_BAND of the event's, isolating what
    # the insider adds on top of the drawdown.
    print()
    print("=" * 96)
    print("DECLINE-MATCHED CONTROL   same symbol, same trailing 60d drawdown, no insider")
    print("-" * 96)
    matched: dict[str, dict[int, list[float]]] = {
        name: {o: [] for o in WINDOWS_POST} for name in ("event", "matched control")
    }
    per_bucket: dict[str, dict[int, list[float]]] = {
        name: {o: [] for o in WINDOWS_POST} for name in ("1 insider", "2 insiders", "3+ insiders")
    }
    rng2 = random.Random(SEED)
    pool_dates: dict[str, list[int]] = {}
    matched_pairs = 0

    for symbol, filing_date, insiders, _v in events:
        series = series_for(symbol)
        if series is None:
            continue
        pos = bisect_left(series.dates, filing_date)
        if pos >= len(series.dates) or series.dates[pos] not in spy_index:
            continue
        stock_pre = window_return(series, pos, -60)
        market_pre = window_return(spy, spy_index[series.dates[pos]], -60)
        if stock_pre is None or market_pre is None:
            continue
        target = stock_pre - market_pre

        if symbol not in pool_dates:
            pool_dates[symbol] = list(range(150, max(151, len(series.dates) - 150)))
        candidates: list[int] = []
        for _ in range(300):
            if not pool_dates[symbol]:
                break
            k = rng2.choice(pool_dates[symbol])
            day = series.dates[k]
            if day not in spy_index or abs(k - pos) < 60:
                continue
            s_pre = window_return(series, k, -60)
            m_pre = window_return(spy, spy_index[day], -60)
            if s_pre is None or m_pre is None:
                continue
            if abs((s_pre - m_pre) - target) <= 0.02:
                candidates.append(k)
                if len(candidates) >= 5:
                    break
        if not candidates:
            continue
        matched_pairs += 1
        name = "1 insider" if insiders == 1 else ("2 insiders" if insiders == 2 else "3+ insiders")
        for offset in WINDOWS_POST:
            stock = window_return(series, pos, offset)
            market = window_return(spy, spy_index[series.dates[pos]], offset)
            if stock is None or market is None:
                continue
            event_excess = stock - market
            control_excess: list[float] = []
            for k in candidates:
                s = window_return(series, k, offset)
                m = window_return(spy, spy_index[series.dates[k]], offset)
                if s is not None and m is not None:
                    control_excess.append(s - m)
            if not control_excess:
                continue
            matched["event"][offset].append(event_excess)
            matched["matched control"][offset].append(statistics.fmean(control_excess))
            per_bucket[name][offset].append(event_excess - statistics.fmean(control_excess))

    print(f"matched events {matched_pairs:,} of {sum(counts.values()):,}   (band +/-2%p on trailing 60d)")
    print(f"{'window':>10s}{'event':>13s}{'matched ctrl':>15s}{'difference':>14s}"
          + "".join(f"{n:>15s}" for n in ("1 insider", "2 insiders", "3+ insiders")))
    for offset in WINDOWS_POST:
        e = matched["event"][offset]
        c = matched["matched control"][offset]
        if not e:
            continue
        row = (f"{'after ' + str(offset) + 'd':>10s}{statistics.median(e) * 100:12.2f}%"
               f"{statistics.median(c) * 100:14.2f}%{(statistics.median(e) - statistics.median(c)) * 100:+13.2f}%p")
        for n in ("1 insider", "2 insiders", "3+ insiders"):
            vals = per_bucket[n][offset]
            row += f"{statistics.median(vals) * 100:+14.2f}%p" if vals else f"{'-':>15s}"
        print(row)
    print("=" * 96)

    with (RESULTS_DIR / "insider_cluster.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "window_days", "n", "median", "mean"])
        for name in names:
            for offset, values in buckets[name].items():
                if values:
                    writer.writerow([name, offset, len(values), statistics.median(values), statistics.fmean(values)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
