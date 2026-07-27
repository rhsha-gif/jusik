"""EXP-005 · How does a max-loss backstop actually behave?

Different animal from the moving-average exit that EXP-004 killed:

    trigger   price vs the position's OWN average cost, not a trend line
    action    liquidate, do NOT buy back  (the judgment is over)
    frequency much rarer by construction

Same discipline as EXP-004: mechanics first, P&L second. The question is not
"which stop earns most" but:

    when a -N% stop fires, what happens next?
      * price keeps falling  -> the stop saved money
      * price recovers       -> the stop crystallised a loss and left the trade

Entry is fixed to the rule already settled in EXP-003:
    200d SMA up-trend + within 3% of 50d SMA, 4 equal tranches, next-open fills.

Research only.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

START_CAPITAL = 10_000.0
TRANCHES = 4
TREND_WINDOW = 200
PULLBACK_MA = 50
PULLBACK_BAND = 0.03
WARMUP = TREND_WINDOW + 5

STOPS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]

COMMISSION_PER_SHARE = 0.005
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01
SLIPPAGE_BPS = 5.0
BENCHMARKS = {"SPY", "QQQ"}


def load(symbol: str) -> tuple[list[float], list[float]]:
    opens: list[float] = []
    closes: list[float] = []
    with (DATA_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            opens.append(float(row["open"]))
            closes.append(float(row["close"]))
    return opens, closes


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


def entry_signals(closes: list[float]) -> list[bool]:
    trend = sma(closes, TREND_WINDOW)
    mid = sma(closes, PULLBACK_MA)
    out: list[bool] = []
    for index in range(len(closes)):
        long_ma = trend[index]
        short_ma = mid[index]
        out.append(
            bool(
                long_ma is not None
                and short_ma is not None
                and closes[index] > long_ma
                and abs(closes[index] / short_ma - 1) <= PULLBACK_BAND
            )
        )
    return out


@dataclass
class Outcome:
    final_value: float
    fired: bool = False
    fire_index: int | None = None
    fire_price: float = 0.0
    avg_cost: float = 0.0
    trough_after: float = 0.0     # lowest close after the stop fired
    final_price: float = 0.0


def run(opens: list[float], closes: list[float], stop: float | None) -> Outcome:
    signals = entry_signals(closes)
    n = len(closes)
    tranche = START_CAPITAL / TRANCHES
    cash = START_CAPITAL
    shares = 0.0
    spent = 0.0
    filled = 0

    def buy(index: int, amount: float) -> None:
        nonlocal cash, shares, spent, filled
        price = opens[index] * (1 + SLIPPAGE_BPS / 10_000)
        fee = commission(amount / price, price)
        shares += (amount - fee) / price
        spent += amount
        cash -= amount
        filled += 1

    result = Outcome(final_value=0.0)
    stopped_at: int | None = None

    for index in range(WARMUP, n - 1):
        if filled < TRANCHES and signals[index]:
            buy(index + 1, tranche)
        if stop is not None and shares > 0 and spent > 0:
            avg_cost = spent / shares
            if closes[index] <= avg_cost * (1 - stop):
                sell_price = opens[index + 1] * (1 - SLIPPAGE_BPS / 10_000)
                fee = commission(shares, sell_price)
                cash += shares * sell_price - fee
                result.fired = True
                result.fire_index = index + 1
                result.fire_price = sell_price
                result.avg_cost = avg_cost
                shares = 0.0
                stopped_at = index + 1
                break

    if stopped_at is None:
        while filled < TRANCHES:      # force-fill leftovers at the end
            buy(n - 1, tranche)

    if stopped_at is not None:
        tail = closes[stopped_at:]
        result.trough_after = min(tail) if tail else result.fire_price
        result.final_price = closes[-1]

    result.final_value = shares * closes[-1] + cash
    return result


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [row["symbol"] for row in manifest if row["symbol"] not in BENCHMARKS]

    base: dict[str, float] = {}
    per_stop: dict[float, list[tuple[str, Outcome]]] = {stop: [] for stop in STOPS}

    for symbol in symbols:
        opens, closes = load(symbol)
        if len(closes) < WARMUP + 60:
            continue
        base[symbol] = run(opens, closes, None).final_value
        for stop in STOPS:
            per_stop[stop].append((symbol, run(opens, closes, stop)))

    print("=" * 92)
    print("EXP-005  max-loss backstop MECHANICS   (entry = 200SMA + 50SMA3%, 4 tranches)")
    print("=" * 92)
    print(f"{'stop':>6s}{'fired':>9s}{'saved?':>9s}{'med drop after':>16s}{'med miss after':>16s}{'med recovery':>15s}")
    print(f"{'':6s}{'':9s}{'(fell more)':>9s}{'(further fall)':>16s}{'(rally missed)':>16s}{'(final/stop)':>15s}")
    print("-" * 92)
    for stop in STOPS:
        fired = [(s, o) for s, o in per_stop[stop] if o.fired]
        if not fired:
            continue
        further = [1 - o.trough_after / o.fire_price for _s, o in fired]
        recovery = [o.final_price / o.fire_price - 1 for _s, o in fired]
        saved = sum(1 for r in recovery if r < 0)
        print(
            f"{stop * 100:5.0f}%{len(fired):>6d}/{len(per_stop[stop]):<3d}"
            f"{saved / len(fired) * 100:8.0f}%{statistics.median(further) * 100:15.1f}%"
            f"{statistics.median([r for r in recovery if r > 0] or [0]) * 100:15.1f}%"
            f"{statistics.median(recovery) * 100:14.1f}%"
        )

    print("=" * 92)
    print("consequence: terminal value by stop level  (base = no stop)")
    print("-" * 92)
    print(f"{'stop':>8s}{'median ret':>14s}{'mean ret':>13s}{'median vs none':>17s}{'beat none':>13s}")
    base_values = [base[s] for s in base]
    print(
        f"{'none':>8s}{statistics.median([v / START_CAPITAL - 1 for v in base_values]) * 100:13.1f}%"
        f"{statistics.fmean([v / START_CAPITAL - 1 for v in base_values]) * 100:12.1f}%"
        f"{0.0:16.2f}%{'-':>13s}"
    )
    for stop in STOPS:
        values = [o.final_value for _s, o in per_stop[stop]]
        diffs = [(o.final_value - base[s]) / START_CAPITAL for s, o in per_stop[stop]]
        rets = [v / START_CAPITAL - 1 for v in values]
        wins = sum(1 for d in diffs if d > 1e-9)
        print(
            f"{stop * 100:7.0f}%{statistics.median(rets) * 100:13.1f}%{statistics.fmean(rets) * 100:12.1f}%"
            f"{statistics.median(diffs) * 100:16.2f}%{wins:10d}/{len(diffs):<3d}"
        )
    print("=" * 92)

    with (RESULTS_DIR / "backstop.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["stop", "symbol", "fired", "avg_cost", "fire_price", "trough_after", "final_price", "value", "base_value"])
        for stop in STOPS:
            for symbol, outcome in per_stop[stop]:
                writer.writerow([stop, symbol, outcome.fired, outcome.avg_cost, outcome.fire_price,
                                 outcome.trough_after, outcome.final_price, outcome.final_value, base[symbol]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
