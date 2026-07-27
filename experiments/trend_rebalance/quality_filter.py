"""EXP-018 · Does screening the approved pool on fundamentals beat screening it at random?

The last selection candidate standing. EXP-017 killed insider clusters and the
remaining literature signals need paid data, so this is what is left that is
free, large-cap, long-only and computable.

The claim under test is narrow and matches how the protocol would actually use
it: the person approves a pool, and the question is whether ranking candidates
into that pool by gross profitability beats filling it at random. The deposit
rule is buy_only, the only allocation arm that cleared significance.

Four guards, each answering a way this project has already been fooled once:

    point-in-time    scores key off the FILED date, never the fiscal period end.
                     A FY2023 ratio is invisible until the 10-K lands in 2024.
    paired trials    the quality pool and the random pool share the era, the
                     dates and the deposit schedule; only membership differs.
    era as the unit  significance is computed across eras, not across
                     portfolios. Portfolios inside one era are not independent
                     draws -- that error inflated EXP-013 by an order of
                     magnitude (see CONCLUSIONS 3-3).
    median reported  a positive mean with a sub-50% win rate means a few
                     outliers carried it and the typical account saw nothing.

Honest limit, stated before the result: XBRL begins around 2010, so at most
three quasi-independent periods exist. With that many, this test can REJECT the
signal but cannot confirm it. Read a positive result as "not refuted", never as
"established".

Research only.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

from blend import simulate
from fetch_fundamentals import CONCEPTS
from era_test import DATA_DIR, load
from expected_value import irr_annual
from rank_deposit import INITIAL_CAPITAL, MONTHLY_DEPOSIT, Series, build_rankings

RESULTS_DIR = Path(__file__).resolve().parent / "results"
# Prices come from data_long (needed for pre-2016 eras); the fundamentals file
# is written next to the point-in-time universe, so name it explicitly.
FUND_PATH = Path(__file__).resolve().parent / "data_pit" / "_fundamentals.csv"

TRIALS = 200
SEED = 20260728
SEED_NAMES = 5
BOOK = 10
POOL = 50
REPORT_LAG_DAYS = 0     # `filed` already is the public date; no extra padding needed

ERAS: list[tuple[str, str, str]] = [
    ("2012-2016", "2012-01", "2016-12"),
    ("2017-2021", "2017-01", "2021-12"),
    ("2022-2026", "2022-01", "2026-07"),
    ("2012-2019 (long)", "2012-01", "2019-12"),
    ("2019-2026 (long)", "2019-01", "2026-07"),
]


def load_fundamentals() -> dict[str, list[tuple[str, dict[str, float]]]]:
    """symbol -> [(filed_date, {concept: value})], ascending by filed date.

    Facts are grouped by fiscal period, then stamped with the LATEST filed date
    among the concepts in that period -- a ratio is only computable once every
    input of it is public, and the numerator often lands after the denominator.
    """
    path = FUND_PATH
    if not path.exists():
        return {}
    # Several tags can carry the same concept for one period. Picking whichever
    # was filed first is wrong: a company reporting both CostOfGoodsAndServicesSold
    # and CostOfServices would contribute only the fragment that happened to be
    # filed earlier, inflating gross profit. Resolve by the fallback order the
    # fetcher declares, and only then by filing date.
    order = [t for tags in CONCEPTS.values() for t in tags]
    priority = {tag: rank for rank, tag in enumerate(order)}
    by_period: dict[tuple[str, str], dict[str, tuple[int, str, float]]] = defaultdict(dict)
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                value = float(row["value"])
            except ValueError:
                continue
            key = (row["symbol"], row["fiscal_end"])
            concept = row["concept"]
            rank = priority.get(row["tag"], 99)
            current = by_period[key].get(concept)
            # preferred tag wins; within a tag the FIRST filing wins, because a
            # restatement was not visible at the time
            if current is None or (rank, row["filed"]) < (current[0], current[1]):
                by_period[key][concept] = (rank, row["filed"], value)

    out: dict[str, list[tuple[str, dict[str, float]]]] = defaultdict(list)
    for (symbol, _end), concepts in by_period.items():
        if not concepts:
            continue
        available = max(f for _r, f, _v in concepts.values())
        out[symbol].append((available, {c: v for c, (_r, _f, v) in concepts.items()}))
    for symbol in out:
        out[symbol].sort(key=lambda t: t[0])
    return dict(out)


def score_at(history: list[tuple[str, dict[str, float]]], asof: str) -> float | None:
    """Gross profit over assets, using only filings public on or before `asof`.

    Walks BACKWARDS to the most recent period that carries all three inputs. A
    10-K restates three years of income statement against two of balance sheet,
    so the newest period on file is often a comparative year with revenue but no
    assets. Reading only the last entry discards companies that are perfectly
    well covered a year earlier.
    """
    for available, facts in reversed([h for h in history if h[0] <= asof]):
        assets = facts.get("assets")
        revenue = facts.get("revenue")
        cogs = facts.get("cogs")
        if assets and assets > 0 and revenue is not None and cogs is not None:
            _ = available
            return (revenue - cogs) / assets
    return None


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest if r["symbol"] not in {"SPY", "QQQ"}]
    series_map = {s: load(s) for s in symbols}
    all_keys = sorted({k for s in series_map.values() for k in s.month_end})
    fundamentals = load_fundamentals()
    print(f"price universe {len(symbols)}   fundamentals for {len(fundamentals)} symbols")
    if not fundamentals:
        print("run fetch_fundamentals.py first")
        return 1
    print("building monthly rankings (used only by the deposit rule, not the screen) ...")
    rankings = build_rankings(series_map, all_keys)

    rows: list[list[object]] = []
    era_edges: list[float] = []
    placebo_era_edges: list[float] = []
    print()
    print("=" * 104)
    print(f"EXP-018  gross-profitability screen on the approved pool   pool={POOL} seed={SEED_NAMES} book={BOOK}")
    print("both arms use buy_only deposits, share the era and the dates, and never sell")
    print("=" * 104)
    print(f"{'era':>20s}{'eligible':>10s}{'quality':>11s}{'random':>10s}{'placebo':>10s}"
          f"{'vs random':>13s}{'vs placebo':>14s}{'t(port)':>9s}{'win':>8s}")
    print("-" * 104)

    for label, start_key, end_key in ERAS:
        window = [k for k in all_keys if start_key <= k <= end_key]
        if len(window) < 36:
            continue
        asof = window[0] + "-01"
        eligible = sorted(
            s for s in symbols
            if s in fundamentals
            and score_at(fundamentals[s], asof) is not None
            and sum(1 for k in window if k in series_map[s].month_end) >= len(window) * 0.95
        )
        if len(eligible) < POOL * 2:
            print(f"{label:>20s}{len(eligible):>10d}   -- too few names with point-in-time fundamentals")
            continue

        scored = sorted(eligible, key=lambda s: -(score_at(fundamentals[s], asof) or 0.0))
        top_half = scored[: len(scored) // 2]

        # Placebo: the same top-half mechanics driven by scores shuffled across
        # symbols. Drawing 50 names from a 75-name shortlist is not the same
        # sampling process as drawing 50 from 150 -- pools overlap more, so the
        # trials correlate differently. This arm holds that mechanic fixed and
        # removes only the information, so quality-minus-placebo is the clean
        # contrast and quality-minus-random keeps the mechanic in it.
        # Shuffled ONCE per era was wrong: all 200 trials then inherit a single
        # random draw of 75 names, so the arm has an effective sample size of one
        # and its gap to the random arm measures that draw's luck, not the screen.
        # Reshuffled per trial below.
        placebo_rng = random.Random(SEED + 991)

        rng = random.Random(SEED)
        gaps: list[float] = []
        placebo_gaps: list[float] = []
        q_out: list[float] = []
        r_out: list[float] = []
        p_out: list[float] = []
        months = len(window) - 1
        deposited = INITIAL_CAPITAL + MONTHLY_DEPOSIT * months
        for _ in range(TRIALS):
            q_pool = set(rng.sample(top_half, POOL))
            r_pool = set(rng.sample(eligible, POOL))
            shuffled = list(eligible)
            placebo_rng.shuffle(shuffled)
            p_pool = set(rng.sample(shuffled[: len(shuffled) // 2], POOL))
            q_start = rng.sample(sorted(q_pool), SEED_NAMES)
            r_start = rng.sample(sorted(r_pool), SEED_NAMES)
            p_start = rng.sample(sorted(p_pool), SEED_NAMES)
            q = irr_annual(months, simulate(series_map, window, rankings, q_pool, q_start, 0.0) * deposited)
            r = irr_annual(months, simulate(series_map, window, rankings, r_pool, r_start, 0.0) * deposited)
            p = irr_annual(months, simulate(series_map, window, rankings, p_pool, p_start, 0.0) * deposited)
            q_out.append(q)
            r_out.append(r)
            p_out.append(p)
            gaps.append(q - r)
            placebo_gaps.append(q - p)
        mean = statistics.fmean(gaps)
        se = statistics.pstdev(gaps) / math.sqrt(len(gaps))
        median = statistics.median(gaps)
        win = sum(1 for g in gaps if g > 0) / len(gaps) * 100
        placebo_median = statistics.median(placebo_gaps)
        if not label.endswith("(long)"):
            era_edges.append(median)
            placebo_era_edges.append(placebo_median)
        print(
            f"{label:>20s}{len(eligible):>10d}{statistics.median(q_out) * 100:10.2f}%"
            f"{statistics.median(r_out) * 100:9.2f}%{statistics.median(p_out) * 100:10.2f}%"
            f"{median * 100:+11.2f}%p{placebo_median * 100:+12.2f}%p"
            f"{mean / se if se else 0:+9.2f}{win:7.1f}%"
        )
        rows.append([label, len(eligible), statistics.median(q_out), statistics.median(r_out),
                     statistics.median(p_out), mean, median, placebo_median, win])

    print("-" * 104)
    if len(era_edges) >= 2:
        mean = statistics.fmean(era_edges)
        se = statistics.stdev(era_edges) / math.sqrt(len(era_edges))
        print(f"ERA-LEVEL (the only valid unit)   n={len(era_edges)}")
        for name, values in (("quality vs random ", era_edges), ("quality vs placebo", placebo_era_edges)):
            m = statistics.fmean(values)
            s_e = statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0
            print(f"   {name}  mean {m * 100:+.2f}%p   SE {s_e * 100:.2f}   "
                  f"t = {m / s_e if s_e else 0:+.2f}   positive {sum(1 for v in values if v > 0)}/{len(values)}")
        print("   t(port) above is shown only to demonstrate how far it overstates; ignore it.")
    print("=" * 104)

    with (RESULTS_DIR / "quality_filter.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["era", "eligible", "quality_irr", "random_irr", "placebo_irr",
                         "mean_edge", "median_edge", "placebo_median_edge", "win_pct"])
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
