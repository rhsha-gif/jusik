"""What is the person's stock selection actually worth, per year?

Every experiment so far drew the approved pool at RANDOM, because a person's
judgement cannot be simulated. That was the conservative choice and it isolated
what the machinery contributes on a neutral pool: +0.19%p a year, verified.

It also left the largest term in the whole system unmeasured. The confirmed
design puts selection with the person, so if there is alpha anywhere it is
there. This measures its LEVERAGE -- not whether the person has skill, but what
a given amount of it is worth once it runs through the protocol.

Skill is constructed with hindsight on purpose. For each era every name's
realised return is known after the fact; a skill level s means the approved pool
is drawn with fraction s from the names that finished in the top third and
(1 - s) at random. s = 0 is the random pool used everywhere else. s = 1 is
perfect foresight, which nobody has -- it is the ceiling, not a target.

Using hindsight to BUILD the skill is legitimate here because the question is
"what does skill pay", not "do I have skill". Nothing in the result is a claim
about future performance.

The deposit rule is buy_only, the only arm that cleared the significance bar.

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
from expected_value import irr_annual
from rank_deposit import INITIAL_CAPITAL, MONTHLY_DEPOSIT, build_rankings

RESULTS_DIR = Path(__file__).resolve().parent / "results"
TRIALS = 100
SEED = 20260728
SKILLS = [0.0, 0.25, 0.50, 0.75, 1.0]
BOOK = 10


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest]
    series_map = {s: load(s) for s in symbols}
    all_keys = sorted({k for s in series_map.values() for k in s.month_end})
    print("building rankings ...")
    rankings = build_rankings(series_map, all_keys)
    print()

    print("=" * 104)
    print(f"SKILL LEVERAGE  money-weighted annual return by selection skill   deposit rule = buy_only")
    print("skill s = fraction of the approved pool drawn from names that finished in the top third")
    print("s is built with hindsight to price the skill, NOT a claim that anyone has it")
    print("=" * 104)
    header = f"{'era':>24s}" + "".join(f"{f's={int(s * 100)}%':>12s}" for s in SKILLS)
    print(header)
    print("-" * 104)

    per_skill: dict[float, list[float]] = {s: [] for s in SKILLS}
    gains: dict[float, list[float]] = {s: [] for s in SKILLS if s > 0}
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

        # realised return over the era -- hindsight, used only to define "skill"
        realised: dict[str, float] = {}
        for symbol in available:
            series = series_map[symbol]
            first = series.month_end.get(window[0])
            last = series.month_end.get(window[-1])
            if first is None or last is None or series.closes[first] <= 0:
                continue
            realised[symbol] = series.closes[last] / series.closes[first]
        ranked_names = sorted(realised, key=lambda s: -realised[s])
        winners = set(ranked_names[: len(ranked_names) // 3])
        others = [s for s in available if s not in winners]
        winner_list = [s for s in available if s in winners]

        cells: list[float] = []
        baseline: list[float] = []
        for skill in SKILLS:
            rng = random.Random(SEED)
            irrs: list[float] = []
            for _ in range(TRIALS):
                take = min(int(round(POOL * skill)), len(winner_list))
                pool = set(rng.sample(winner_list, take)) | set(rng.sample(others, POOL - take))
                start = rng.sample(sorted(pool), SEED_NAMES)
                multiple = simulate(series_map, window, rankings, pool, start, 0.0)
                irrs.append(irr_annual(months, multiple * deposited))
            median = statistics.median(irrs)
            cells.append(median)
            per_skill[skill].append(median)
            if skill == 0.0:
                baseline = irrs
            else:
                gains[skill].append(median - statistics.median(baseline))
        print(f"{label:>24s}" + "".join(f"{c * 100:11.2f}%" for c in cells))
        rows.append([label] + cells)

    print("-" * 104)
    print(f"{'MEDIAN across eras':>24s}" + "".join(f"{statistics.median(per_skill[s]) * 100:11.2f}%" for s in SKILLS))
    print()
    print("=" * 104)
    print("ANNUAL GAIN FROM SELECTION SKILL, over the same protocol with a random pool")
    print("-" * 104)
    print(f"{'skill':>10s}{'mean gain':>13s}{'SE':>9s}{'t':>8s}{'worst era':>13s}{'best era':>12s}{'eras +':>9s}")
    for skill, values in gains.items():
        mean = statistics.fmean(values)
        se = statistics.stdev(values) / math.sqrt(len(values))
        print(
            f"{skill * 100:9.0f}%{mean * 100:+12.2f}%p{se * 100:8.2f}{mean / se if se else 0:+8.2f}"
            f"{min(values) * 100:+12.2f}%p{max(values) * 100:+11.2f}%p"
            f"{sum(1 for v in values if v > 0):>6d}/{len(values):<2d}"
        )
    print("=" * 104)
    print("for scale: the deposit machinery itself is worth +0.19%p a year (verified),")
    print("and +0.56%p if the unverified rank sleeve is included.")

    with (RESULTS_DIR / "skill_leverage.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["era"] + [f"skill_{int(s * 100)}" for s in SKILLS])
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
