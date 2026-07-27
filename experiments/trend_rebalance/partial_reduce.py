"""EXP-004 · How does partial reduction on a trend-gate break actually behave?

This is an OBSERVATION, not an optimisation. The question is not "which
reduction size earns most" but "what does a gate break actually look like":

    how often does the gate break?
    how long does it stay broken?
    when it repairs, is the buy-back price higher or lower than the sell price?

That last number is the whole ball game. If price is usually HIGHER when the
gate repairs, every reduction is a round-trip loss and the rule is a whipsaw
machine regardless of size. If price is usually LOWER, the reduction pays.

P&L by reduction size is reported second, deliberately -- it is a consequence
of the mechanics above, not the thing being selected on.

Rules observed (fixed in advance, not searched):
    gate      close vs 200-day SMA   -- same gate the entry rule E1 uses
    break     close crosses below
    repair    close crosses back above
    action    sell X% of held shares on break, buy back with all cash on repair
    fills     next session open, commission + slippage applied

Research only.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

TREND_WINDOW = 200
WARMUP = TREND_WINDOW + 5
REDUCTION_SIZES = [0.0, 0.25, 0.50, 0.75, 1.00]
START_CAPITAL = 10_000.0

COMMISSION_PER_SHARE = 0.005
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01
SLIPPAGE_BPS = 5.0

BENCHMARKS = {"SPY", "QQQ"}


@dataclass
class Event:
    symbol: str
    break_index: int
    repair_index: int | None
    break_price: float
    repair_price: float | None
    trough_price: float

    @property
    def duration(self) -> int | None:
        return None if self.repair_index is None else self.repair_index - self.break_index

    @property
    def roundtrip(self) -> float | None:
        """buy-back price / sell price - 1.  >0 means the reduction cost money."""
        if self.repair_price is None:
            return None
        return self.repair_price / self.break_price - 1.0

    @property
    def avoided(self) -> float:
        """how far price fell below the sell price while out (positive = drop avoided)"""
        return 1.0 - self.trough_price / self.break_price


def load(symbol: str) -> tuple[list[str], list[float], list[float]]:
    dates: list[str] = []
    opens: list[float] = []
    closes: list[float] = []
    with (DATA_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dates.append(row["date"])
            opens.append(float(row["open"]))
            closes.append(float(row["close"]))
    return dates, opens, closes


def sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        if index >= window - 1:
            out[index] = running / window
    return out


def commission(shares: float, price: float) -> float:
    return min(max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares), shares * price * COMMISSION_CAP_RATE)


def find_events(symbol: str, opens: list[float], closes: list[float]) -> list[Event]:
    trend = sma(closes, TREND_WINDOW)
    events: list[Event] = []
    inside = True
    current: Event | None = None
    for index in range(WARMUP, len(closes) - 1):
        line = trend[index]
        if line is None:
            continue
        above = closes[index] > line
        if inside and not above:
            inside = False
            current = Event(
                symbol=symbol,
                break_index=index + 1,
                repair_index=None,
                break_price=opens[index + 1],
                repair_price=None,
                trough_price=opens[index + 1],
            )
        elif not inside:
            if current is not None:
                current.trough_price = min(current.trough_price, closes[index])
            if above and current is not None:
                current.repair_index = index + 1
                current.repair_price = opens[index + 1]
                events.append(current)
                current = None
                inside = True
    if current is not None:
        events.append(current)  # still broken at the end of the data
    return events


def simulate(opens: list[float], closes: list[float], reduction: float) -> float:
    """Buy fully at warmup, then apply partial reduce/rebuy on every gate event."""
    trend = sma(closes, TREND_WINDOW)
    price = opens[WARMUP] * (1 + SLIPPAGE_BPS / 10_000)
    fee = commission(START_CAPITAL / price, price)
    shares = (START_CAPITAL - fee) / price
    cash = 0.0
    inside = True

    for index in range(WARMUP, len(closes) - 1):
        line = trend[index]
        if line is None:
            continue
        above = closes[index] > line
        if inside and not above and reduction > 0 and shares > 0:
            sell_price = opens[index + 1] * (1 - SLIPPAGE_BPS / 10_000)
            quantity = shares * reduction
            sell_fee = commission(quantity, sell_price)
            cash += quantity * sell_price - sell_fee
            shares -= quantity
            inside = False
        elif not inside and above:
            if cash > 1.0:
                buy_price = opens[index + 1] * (1 + SLIPPAGE_BPS / 10_000)
                buy_fee = commission(cash / buy_price, buy_price)
                shares += (cash - buy_fee) / buy_price
                cash = 0.0
            inside = True
        elif not inside and not above:
            continue
        elif above:
            inside = True

    return shares * closes[-1] + cash


@dataclass
class SymbolStat:
    symbol: str
    events: int
    median_duration: float
    whipsaw_rate: float          # share of events where buy-back price > sell price
    median_roundtrip: float
    median_avoided: float
    values: dict[float, float] = field(default_factory=dict)


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [row["symbol"] for row in manifest if row["symbol"] not in BENCHMARKS]

    all_events: list[Event] = []
    stats: list[SymbolStat] = []

    for symbol in symbols:
        _dates, opens, closes = load(symbol)
        if len(closes) < WARMUP + 60:
            continue
        events = find_events(symbol, opens, closes)
        closed = [e for e in events if e.repair_index is not None]
        all_events.extend(closed)
        if not closed:
            continue
        roundtrips = [e.roundtrip for e in closed if e.roundtrip is not None]
        stat = SymbolStat(
            symbol=symbol,
            events=len(closed),
            median_duration=statistics.median([e.duration for e in closed if e.duration is not None]),
            whipsaw_rate=sum(1 for r in roundtrips if r > 0) / len(roundtrips),
            median_roundtrip=statistics.median(roundtrips),
            median_avoided=statistics.median([e.avoided for e in closed]),
        )
        for size in REDUCTION_SIZES:
            stat.values[size] = simulate(opens, closes, size)
        stats.append(stat)

    # ---------------- mechanics first
    durations = [e.duration for e in all_events if e.duration is not None]
    roundtrips = [e.roundtrip for e in all_events if e.roundtrip is not None]
    avoided = [e.avoided for e in all_events]
    years = 6.8

    print("=" * 84)
    print("EXP-004  gate-break MECHANICS   (200d SMA, 157 names incl. delisted)")
    print("=" * 84)
    print(f"  gate-break events (closed)      {len(all_events):>10d}")
    print(f"  per symbol per year             {len(all_events) / len(stats) / years:>10.2f}")
    print(f"  duration median / p25 / p75     "
          f"{statistics.median(durations):>6.0f} / {sorted(durations)[len(durations) // 4]:.0f} / "
          f"{sorted(durations)[3 * len(durations) // 4]:.0f}  sessions")
    print(f"  breaks repaired within 21d      {sum(1 for d in durations if d <= 21) / len(durations) * 100:>9.1f}%")
    print("-" * 84)
    print("  ROUND TRIP  (buy-back price vs sell price;  >0 = the reduction cost money)")
    print(f"    median                        {statistics.median(roundtrips) * 100:>9.2f}%")
    print(f"    mean                          {statistics.fmean(roundtrips) * 100:>9.2f}%")
    print(f"    WHIPSAW RATE (buy back higher){sum(1 for r in roundtrips if r > 0) / len(roundtrips) * 100:>9.1f}%")
    print("-" * 84)
    print("  DROP AVOIDED  (how far it fell below the sell price while out)")
    print(f"    median                        {statistics.median(avoided) * 100:>9.2f}%")
    print(f"    events with >10% drop avoided {sum(1 for a in avoided if a > 0.10) / len(avoided) * 100:>9.1f}%")

    # ---------------- P&L second, on purpose
    print("=" * 84)
    print("consequence: terminal value by reduction size  (base = no reduction)")
    print("-" * 84)
    print(f"{'reduction':>12s}{'median ret':>14s}{'mean ret':>13s}{'median vs 0%':>15s}{'beat 0%':>12s}")
    base = [s.values[0.0] for s in stats]
    for size in REDUCTION_SIZES:
        values = [s.values[size] for s in stats]
        rets = [v / START_CAPITAL - 1 for v in values]
        diffs = [(v - b) / START_CAPITAL for v, b in zip(values, base)]
        wins = sum(1 for d in diffs if d > 1e-9)
        print(
            f"{size * 100:11.0f}%{statistics.median(rets) * 100:13.1f}%{statistics.fmean(rets) * 100:12.1f}%"
            f"{statistics.median(diffs) * 100:14.2f}%{wins:9d}/{len(stats):<4d}"
        )
    print("=" * 84)

    with (RESULTS_DIR / "partial_reduce.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "events", "median_duration", "whipsaw_rate", "median_roundtrip", "median_avoided"]
                        + [f"value_{int(s * 100)}" for s in REDUCTION_SIZES])
        for stat in stats:
            writer.writerow([stat.symbol, stat.events, stat.median_duration, stat.whipsaw_rate,
                             stat.median_roundtrip, stat.median_avoided]
                            + [stat.values[s] for s in REDUCTION_SIZES])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
