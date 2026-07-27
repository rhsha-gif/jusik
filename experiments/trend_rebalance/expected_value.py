"""What does the current protocol actually expect to earn, in annual terms?

Everything so far was reported as a multiple of total contributions, which is not
comparable to anything a person quotes. With an initial sum plus monthly deposits
the money is invested for very different lengths of time, so a multiple cannot be
annualised by taking a root -- it needs an internal rate of return on the actual
cash flows.

This computes, per era and per arm, the money-weighted annual return:

    t=0        -50,000
    monthly    -1,000
    end        +final value

and reports the arms as annual percentages plus the spread between them. The
difference in IRR is the only honest way to state what a deposit rule is worth
per year.

Arms:
    even        deposits split evenly across the book -- the neutral baseline
    buy_only    deposits to whichever holding has fallen furthest behind
    blend25     25% of each deposit by trend rank, 75% to the laggards
    rank100     the whole deposit by trend rank

Research only.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
from pathlib import Path

from blend import POOL, SEED_NAMES, simulate
from era_test import DATA_DIR, ERAS, load
from rank_deposit import INITIAL_CAPITAL, MONTHLY_DEPOSIT, build_rankings

RESULTS_DIR = Path(__file__).resolve().parent / "results"
TRIALS = 100
SEED = 20260728
ARMS: list[tuple[str, float | None]] = [
    ("even", None),
    ("buy_only", 0.0),
    ("blend25", 0.25),
    ("rank100", 1.0),
]


def irr_annual(months: int, final: float) -> float:
    """Money-weighted annual return for -INITIAL at t0, -MONTHLY each month, +final."""
    def npv(rate: float) -> float:
        total = -INITIAL_CAPITAL
        for m in range(months):
            total -= MONTHLY_DEPOSIT / (1 + rate) ** m
        return total + final / (1 + rate) ** months

    low, high = -0.99 / 12, 1.0
    if npv(low) < 0:
        return float("nan")
    for _ in range(200):
        mid = (low + high) / 2
        if npv(mid) > 0:
            low = mid
        else:
            high = mid
    return (1 + (low + high) / 2) ** 12 - 1


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest]
    series_map = {s: load(s) for s in symbols}
    all_keys = sorted({k for s in series_map.values() for k in s.month_end})
    print("building rankings ...")
    rankings = build_rankings(series_map, all_keys)
    print()

    print("=" * 100)
    print(f"EXPECTED VALUE  money-weighted annual return by era   {TRIALS} trials, median account")
    print("pre-2010 levels are survivorship-inflated -- compare the COLUMNS, not the levels")
    print("=" * 100)
    print(f"{'era':>24s}{'yrs':>5s}" + "".join(f"{name:>12s}" for name, _ in ARMS)
          + f"{'buy_only-even':>15s}{'blend25-even':>14s}")
    print("-" * 100)

    collected: dict[str, list[float]] = {name: [] for name, _ in ARMS}
    diffs: dict[str, list[float]] = {"buy_only": [], "blend25": [], "rank100": []}
    rows: list[list[object]] = []

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
        months = len(window) - 1
        deposited = INITIAL_CAPITAL + MONTHLY_DEPOSIT * months

        per_arm: dict[str, float] = {}
        per_trial: dict[str, list[float]] = {name: [] for name, _ in ARMS}
        rng_master = random.Random(SEED)
        pools = []
        for _ in range(TRIALS):
            pool = set(rng_master.sample(available, POOL))
            pools.append((pool, rng_master.sample(sorted(pool), SEED_NAMES)))

        for name, share in ARMS:
            for pool, start in pools:
                multiple = simulate(series_map, window, rankings, pool, start, share)
                per_trial[name].append(irr_annual(months, multiple * deposited))
            per_arm[name] = statistics.median(per_trial[name])
            collected[name].append(per_arm[name])

        # The trials are PAIRED -- every arm sees the same pool, the same starting
        # names and the same dates. The statistic for a paired design is the median
        # of the per-trial differences, not the difference of the two medians; the
        # latter can and does flip sign when the distributions are skewed.
        for name in diffs:
            paired = [a - b for a, b in zip(per_trial[name], per_trial["even"])]
            diffs[name].append(statistics.median(paired))
            per_arm[f"{name}_vs_even"] = statistics.median(paired)

        print(
            f"{label:>24s}{months / 12:5.1f}" + "".join(f"{per_arm[n] * 100:11.2f}%" for n, _ in ARMS)
            + f"{per_arm['buy_only_vs_even'] * 100:+14.2f}%p"
            + f"{per_arm['blend25_vs_even'] * 100:+13.2f}%p"
        )
        rows.append([label, months / 12] + [per_arm[n] for n, _ in ARMS])

    print("-" * 100)
    print(f"{'MEDIAN across eras':>24s}{'':5s}" + "".join(f"{statistics.median(collected[n]) * 100:11.2f}%" for n, _ in ARMS))
    print()
    print("=" * 100)
    print("ANNUALISED EDGE OVER AN EVEN SPLIT   (era is the independent unit; n = 7)")
    print("-" * 100)
    print(f"{'arm':>12s}{'mean':>11s}{'SE':>9s}{'t':>8s}{'worst era':>13s}{'best era':>12s}{'eras +':>9s}")
    for name, values in diffs.items():
        mean = statistics.fmean(values)
        se = statistics.stdev(values) / math.sqrt(len(values))
        print(
            f"{name:>12s}{mean * 100:+10.2f}%p{se * 100:8.2f}{mean / se if se else 0:+8.2f}"
            f"{min(values) * 100:+12.2f}%p{max(values) * 100:+11.2f}%p"
            f"{sum(1 for v in values if v > 0):>6d}/{len(values):<2d}"
        )
    print("=" * 100)

    with (RESULTS_DIR / "expected_value.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["era", "years"] + [n for n, _ in ARMS])
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
