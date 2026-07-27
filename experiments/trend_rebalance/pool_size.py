"""EXP-014 · How wide must the approved pool be before ranking earns its keep?

The confirmed purpose puts the judgement with the person and everything else with
the system. EXP-013 found the ranking is worth a lot when it may choose from all
157 names and worth nothing when confined to ten already-held names, which is a
direct conflict with that purpose -- the wide-pool version takes the selection
away from the person.

The compromise being tested: the person approves a POOL (theme, thesis, whatever
they want in the account) and the system ranks inside it. That keeps Level 4.
The open question is purely quantitative -- how big must the pool be.

    pool P    candidate names the person has approved
    book H    how many the account actually holds

For every (P, H) the ranking arm is compared against a control that is identical
in every way except that it picks at random from the same pool on the same
schedule. Starting holdings are the same names in both arms, so the measured gap
is the deposit allocation alone.

The pool is drawn at random, not chosen with skill. This is deliberate and
conservative: it measures what the ranking contributes on a NEUTRAL pool. If the
person's thesis-driven picks beat random, the real result should be better than
this, not worse.

No arm sells anything. Capital gains tax is zero throughout.

Research only.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
from pathlib import Path

from rank_deposit import (
    BENCHMARKS,
    COMMISSION_CAP_RATE,
    COMMISSION_MINIMUM,
    COMMISSION_PER_SHARE,
    DEPOSIT_TARGETS,
    INITIAL_CAPITAL,
    MONTHLY_DEPOSIT,
    SLIPPAGE_BPS,
    WARMUP_MONTHS,
    Series,
    build_rankings,
    load,
)

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

TRIALS = 100
SEED = 20260727

# The first run exposed the real constraint. With no selling, a book that starts
# full can never take a new name -- room = book - held is zero forever -- so the
# pool is unreachable and its size is irrelevant. What actually matters is how
# many slots the person leaves open for the system to fill.
#   seed  names the person picks up front (their theses)
#   book  total names the account may end up holding
#   pool  approved candidates the system may draw the remaining slots from
SEEDS = [3, 5, 10]
BOOK_SIZES = [10, 20]
POOL_SIZES = [20, 50, 148]


def commission(shares: float, price: float) -> float:
    return min(max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares), shares * price * COMMISSION_CAP_RATE)


def simulate(
    series_map: dict[str, Series],
    keys: list[str],
    rankings: dict[str, list[str]],
    pool: set[str],
    start: list[str],
    book_size: int,
    ranked_mode: bool,
    rng: random.Random,
) -> float:
    """Returns the final value divided by everything deposited."""
    shares: dict[str, float] = {}
    cash = INITIAL_CAPITAL
    deposited = INITIAL_CAPITAL

    def close_price(symbol: str, key: str) -> float | None:
        index = series_map[symbol].month_end.get(key)
        return series_map[symbol].closes[index] if index is not None else None

    def equity(key: str) -> float:
        return sum(h * (close_price(s, key) or 0.0) for s, h in shares.items())

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
        buy(symbol, keys[WARMUP_MONTHS], INITIAL_CAPITAL / len(start))

    for t in range(WARMUP_MONTHS, len(keys) - 1):
        key, key_next = keys[t], keys[t + 1]
        cash += MONTHLY_DEPOSIT
        deposited += MONTHLY_DEPOSIT

        eligible = [s for s in rankings.get(key, []) if s in pool]
        if not ranked_mode:
            eligible = list(eligible)
            rng.shuffle(eligible)
        room = book_size - len(shares)
        targets = [s for s in eligible if s in shares or room > 0][:DEPOSIT_TARGETS]
        if not targets:
            targets = list(shares)
        for symbol in targets:
            buy(symbol, key_next, MONTHLY_DEPOSIT / len(targets))

    return (cash + equity(keys[-1])) / deposited


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest if r["symbol"] not in BENCHMARKS]
    series_map = {s: load(s) for s in symbols}
    keys = sorted(load("SPY").month_end)

    print("building monthly universe rankings ...")
    rankings = build_rankings(series_map, keys)
    universe = [s for s in symbols if len(series_map[s].month_end) >= 100]
    print(f"  {len(universe)} names available for pools\n")

    rows: list[list[object]] = []
    print("=" * 108)
    print(f"EXP-014  how many slots must the person leave open?   ({TRIALS} trials per cell, nothing is ever sold)")
    print("seed = names the person picks;  book = total names allowed;  pool = approved candidates for the rest")
    print("control = identical pool and seed, slots filled at random instead of by rank")
    print("=" * 108)
    print(f"{'seed':>6s}{'book':>6s}{'pool':>6s}{'open':>6s}{'ranked':>11s}{'random':>10s}"
          f"{'edge':>12s}{'SE':>9s}{'t':>8s}{'win':>8s}{'verdict':>14s}")

    for seed_names in SEEDS:
        print("-" * 108)
        for book in BOOK_SIZES:
            if seed_names > book:
                continue
            for pool_size in POOL_SIZES:
                if pool_size < book or pool_size > len(universe):
                    continue
                rng_master = random.Random(SEED)
                gaps: list[float] = []
                ranked_out: list[float] = []
                random_out: list[float] = []
                for trial in range(TRIALS):
                    pool = set(rng_master.sample(universe, pool_size))
                    start = rng_master.sample(sorted(pool), seed_names)
                    a = simulate(series_map, keys, rankings, pool, start, book, True, random.Random(SEED + trial))
                    b = simulate(series_map, keys, rankings, pool, start, book, False, random.Random(SEED + trial))
                    ranked_out.append(a)
                    random_out.append(b)
                    gaps.append(a - b)
                mean = statistics.fmean(gaps)
                se = statistics.pstdev(gaps) / math.sqrt(len(gaps))
                t = mean / se if se > 0 else 0.0
                win = sum(1 for g in gaps if g > 0) / len(gaps) * 100
                verdict = "pays" if t > 2.34 else ("marginal" if t > 1.0 else "no value")
                print(
                    f"{seed_names:>6d}{book:>6d}{pool_size:>6d}{book - seed_names:>6d}"
                    f"{statistics.fmean(ranked_out):10.3f}x{statistics.fmean(random_out):9.3f}x"
                    f"{mean:+11.4f}x{se:9.4f}{t:+8.2f}{win:7.1f}%{verdict:>14s}"
                )
                rows.append([seed_names, book, pool_size, book - seed_names,
                             statistics.fmean(ranked_out), statistics.fmean(random_out), mean, se, t, win])
    print("=" * 108)

    with (RESULTS_DIR / "pool_size.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["seed", "book", "pool", "open_slots", "ranked_mult", "random_mult", "edge", "se", "t", "win_pct"])
        writer.writerows(rows)

    # The grid says pool width dominates. This traces the trade-off finely at the
    # one configuration that keeps the person in the judgement seat: five theses
    # picked by hand, ten names allowed, the rest filled from what they approved.
    fine_seed, fine_book, fine_trials = 5, 10, 200
    print()
    print("=" * 108)
    print(f"TRADE-OFF CURVE   seed={fine_seed} hand-picked, book={fine_book}, {fine_trials} trials")
    print("how much the person must approve, against what the ranking returns for it")
    print("-" * 108)
    print(f"{'pool':>6s}{'ranked':>11s}{'random':>10s}{'edge':>12s}{'t':>8s}{'win':>8s}{'median edge':>14s}{'MDD':>9s}")
    fine_rows: list[list[object]] = []
    for pool_size in (20, 30, 40, 50, 65, 80, 100, 148):
        rng_master = random.Random(SEED)
        gaps: list[float] = []
        ranked_out: list[float] = []
        random_out: list[float] = []
        for trial in range(fine_trials):
            pool = set(rng_master.sample(universe, pool_size))
            start = rng_master.sample(sorted(pool), fine_seed)
            a = simulate(series_map, keys, rankings, pool, start, fine_book, True, random.Random(SEED + trial))
            b = simulate(series_map, keys, rankings, pool, start, fine_book, False, random.Random(SEED + trial))
            ranked_out.append(a)
            random_out.append(b)
            gaps.append(a - b)
        mean = statistics.fmean(gaps)
        se = statistics.pstdev(gaps) / math.sqrt(len(gaps))
        t = mean / se if se > 0 else 0.0
        win = sum(1 for g in gaps if g > 0) / len(gaps) * 100
        print(
            f"{pool_size:>6d}{statistics.fmean(ranked_out):10.3f}x{statistics.fmean(random_out):9.3f}x"
            f"{mean:+11.4f}x{t:+8.2f}{win:7.1f}%{statistics.median(gaps):+13.4f}x{'-':>9s}"
        )
        fine_rows.append([pool_size, statistics.fmean(ranked_out), statistics.fmean(random_out),
                          mean, statistics.median(gaps), t, win])
    print("=" * 108)
    print("note: a positive mean with a win rate under 50% means the average is carried by a few")
    print("      outliers and the typical account sees nothing. Read the median column, not the mean.")

    with (RESULTS_DIR / "pool_size_curve.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pool", "ranked_mult", "random_mult", "mean_edge", "median_edge", "t", "win_pct"])
        writer.writerows(fine_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
