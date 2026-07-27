"""EXP-016 · Splitting the monthly deposit between trend rank and underweight.

EXP-015 measured the two candidate deposit rules across seven decades and found
them complementary rather than competing:

    rank        trend-following. Large gains in 1983-92 and 2016-26, real losses
                in 1973-82 (win 24%) and 2000-09 (win 38%).
    buy_only    contrarian. Small but positive in 5 of 7 eras, best exactly where
                rank was worst (2000-09, win 72%), losing only in 2016-26.

So the question is not which one, but how much of each. This sweeps the split.

Selection criterion follows the stated definition of failure -- "losing money
with no visible way to improve" -- which is a worst-case concern, not an average
one. The reported figure that decides it is therefore the WORST era's median
edge, not the mean across eras. A rule that wins big four decades out of seven
and is unusable in the other three does not fit the purpose.

Research only.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
from pathlib import Path

from era_test import ERAS, load
from rank_deposit import (
    COMMISSION_CAP_RATE,
    COMMISSION_MINIMUM,
    COMMISSION_PER_SHARE,
    DEPOSIT_TARGETS,
    INITIAL_CAPITAL,
    MONTHLY_DEPOSIT,
    SLIPPAGE_BPS,
    Series,
    build_rankings,
)

DATA_DIR = Path(__file__).resolve().parent / "data_long"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

TRIALS = 150
SEED = 20260727
SEED_NAMES = 5
BOOK = 10
POOL = 50
WEIGHTS = [0.0, 0.25, 0.50, 0.75, 1.0]   # share of each deposit driven by rank


def commission(shares: float, price: float) -> float:
    return min(max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares), shares * price * COMMISSION_CAP_RATE)


def simulate(
    series_map: dict[str, Series],
    window: list[str],
    rankings: dict[str, list[str]],
    pool: set[str],
    start: list[str],
    rank_share: float | None,
) -> float:
    """rank_share None = plain even split (the baseline every arm is measured against)."""
    shares: dict[str, float] = {}
    cash = INITIAL_CAPITAL
    deposited = INITIAL_CAPITAL

    def close_price(symbol: str, key: str) -> float | None:
        index = series_map[symbol].month_end.get(key)
        return series_map[symbol].closes[index] if index is not None else None

    def buy(symbol: str, key: str, amount: float) -> None:
        nonlocal cash
        index = series_map[symbol].month_first.get(key)
        if index is None:
            return
        price = series_map[symbol].opens[index]
        amount = min(amount, cash)
        if price <= 0 or amount <= 1.0:
            return
        price *= 1 + SLIPPAGE_BPS / 10_000
        fee = commission(amount / price, price)
        shares[symbol] = shares.get(symbol, 0.0) + (amount - fee) / price
        cash -= amount

    for symbol in start:
        buy(symbol, window[0], INITIAL_CAPITAL / len(start))

    for t in range(len(window) - 1):
        key, key_next = window[t], window[t + 1]
        cash += MONTHLY_DEPOSIT
        deposited += MONTHLY_DEPOSIT

        if rank_share is None:
            for symbol in list(shares):
                buy(symbol, key_next, MONTHLY_DEPOSIT / max(1, len(shares)))
            continue

        rank_money = MONTHLY_DEPOSIT * rank_share
        under_money = MONTHLY_DEPOSIT - rank_money

        if rank_money > 1.0:
            eligible = [s for s in rankings.get(key, []) if s in pool]
            room = BOOK - len(shares)
            targets = [s for s in eligible if s in shares or room > 0][:DEPOSIT_TARGETS]
            if not targets:
                targets = list(shares)
            for symbol in targets:
                buy(symbol, key_next, rank_money / len(targets))

        if under_money > 1.0 and shares:
            held = cash + sum(h * (close_price(s, key) or 0.0) for s, h in shares.items())
            per = held / len(shares)
            gaps = {s: per - shares[s] * (close_price(s, key) or 0.0) for s in shares}
            targets = [s for s, g in sorted(gaps.items(), key=lambda kv: -kv[1]) if g > 0][:DEPOSIT_TARGETS]
            if not targets:
                targets = list(shares)
            for symbol in targets:
                buy(symbol, key_next, under_money / len(targets))

    final = cash + sum(h * (close_price(s, window[-1]) or 0.0) for s, h in shares.items())
    return final / deposited


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest]
    series_map = {s: load(s) for s in symbols}
    all_keys = sorted({k for s in series_map.values() for k in s.month_end})
    print(f"loaded {len(symbols)} names, building rankings ...")
    rankings = build_rankings(series_map, all_keys)
    print()

    per_era: dict[float, list[float]] = {w: [] for w in WEIGHTS}
    rows: list[list[object]] = []

    print("=" * 112)
    print(f"EXP-016  deposit split between trend rank and underweight   seed={SEED_NAMES} book={BOOK} pool={POOL}")
    print("each cell is the MEDIAN edge over an even split, same names and dates in both arms")
    print("=" * 112)
    header = f"{'era':>24s}" + "".join(f"{f'{int(w * 100)}% rank':>14s}" for w in WEIGHTS)
    print(header)
    print("-" * 112)

    for label, start_key, end_key in ERAS:
        window = [k for k in all_keys if start_key <= k <= end_key]
        if len(window) < 60:
            continue
        available = sorted(
            s for s in symbols
            if sum(1 for k in window if k in series_map[s].month_end) >= len(window) * 0.95
        )
        if len(available) < POOL + 5:
            continue

        cells: list[float] = []
        for weight in WEIGHTS:
            rng_master = random.Random(SEED)
            gaps: list[float] = []
            for trial in range(TRIALS):
                pool = set(rng_master.sample(available, POOL))
                start = rng_master.sample(sorted(pool), SEED_NAMES)
                arm = simulate(series_map, window, rankings, pool, start, weight)
                base = simulate(series_map, window, rankings, pool, start, None)
                gaps.append(arm - base)
            median = statistics.median(gaps)
            cells.append(median)
            per_era[weight].append(median)
            rows.append([label, weight, statistics.fmean(gaps), median,
                         sum(1 for g in gaps if g > 0) / len(gaps) * 100])
        print(f"{label:>24s}" + "".join(f"{c:+13.4f}x" for c in cells))

    print("-" * 112)
    print(f"{'WORST era':>24s}" + "".join(f"{min(per_era[w]):+13.4f}x" for w in WEIGHTS))
    print(f"{'median across eras':>24s}" + "".join(f"{statistics.median(per_era[w]):+13.4f}x" for w in WEIGHTS))
    print(f"{'eras positive':>24s}" + "".join(f"{sum(1 for v in per_era[w] if v > 0):>8d}/{len(per_era[w]):<5d}" for w in WEIGHTS))
    print("=" * 112)
    best = max(WEIGHTS, key=lambda w: min(per_era[w]))
    print(f"best worst-case split: {int(best * 100)}% rank / {int((1 - best) * 100)}% underweight")
    print(f"  worst era {min(per_era[best]):+.4f}x   median era {statistics.median(per_era[best]):+.4f}x")

    with (RESULTS_DIR / "blend.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["era", "rank_share", "mean_edge", "median_edge", "win_pct"])
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
