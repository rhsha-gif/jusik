"""Self-audit · placebo test on return-shuffled data.

Shuffling each name's daily returns in time destroys momentum, mean reversion
and every other serial structure, while leaving the volatility and the total
return of each name untouched. Running the same experiment on that data gives
two falsifiable predictions:

    buy_only   the edge SHOULD survive. Feeding whichever holding has fallen
               behind is buying low mechanically; it needs no forecast, so
               scrambling the order should not remove it. If it survives, the
               honest name for that edge is the rebalancing premium, not alpha.

    rank       the edge SHOULD vanish. Trend rank is a forecast and there is
               nothing left to forecast. If it does NOT vanish, the measured
               edge was never momentum -- it would mean a look-ahead or a
               control that is unfair in some way, and every ranking result in
               this project would have to be thrown out.

Either prediction failing invalidates the corresponding conclusion. That is the
point of running it.

Research only.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
from datetime import date
from pathlib import Path

from era_test import DATA_DIR, load, simulate
from rank_deposit import Series, build_rankings

RESULTS_DIR = Path(__file__).resolve().parent / "results"

TRIALS = 100
SEED = 20260728
SEED_NAMES = 5
BOOK = 10
POOL = 50
ERAS = [
    ("1993-2002 dot-com", "1993-01", "2002-12"),
    ("2003-2012 GFC", "2003-01", "2012-12"),
    ("2016-2026 original", "2016-01", "2026-07"),
]


def shuffled(series: Series, rng: random.Random) -> Series:
    """Same daily returns, random order. Volatility and total return preserved."""
    closes = series.closes
    if len(closes) < 3:
        return series
    returns = [closes[i] / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1] > 0]
    rng.shuffle(returns)
    rebuilt = [closes[0]]
    for step in returns:
        rebuilt.append(rebuilt[-1] * step)
    # open of a session is the previous close; both arms see the same convention
    opens = [rebuilt[0]] + rebuilt[:-1]
    out = Series(symbol=series.symbol, closes=rebuilt, opens=opens)
    out.month_end = dict(series.month_end)
    out.month_first = dict(series.month_first)
    limit = len(rebuilt) - 1
    out.month_end = {k: min(v, limit) for k, v in out.month_end.items()}
    out.month_first = {k: min(v, limit) for k, v in out.month_first.items()}
    return out


def measure(
    series_map: dict[str, Series],
    rankings: dict[str, list[str]],
    window: list[str],
    available: list[str],
    arm: str,
    control: str,
    use_pool: bool,
) -> tuple[float, float, float]:
    rng_master = random.Random(SEED)
    gaps: list[float] = []
    for trial in range(TRIALS):
        if use_pool:
            pool = set(rng_master.sample(available, POOL))
            start = rng_master.sample(sorted(pool), SEED_NAMES)
        else:
            start = rng_master.sample(available, BOOK)
            pool = set(start)
        a = simulate(series_map, window, rankings, pool, start, arm, random.Random(SEED + trial))
        b = simulate(series_map, window, rankings, pool, start, control, random.Random(SEED + trial))
        gaps.append(a - b)
    median = statistics.median(gaps)
    win = sum(1 for g in gaps if g > 0) / len(gaps) * 100
    return statistics.fmean(gaps), median, win


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest]
    real = {s: load(s) for s in symbols}
    all_keys = sorted({k for s in real.values() for k in s.month_end})

    rng = random.Random(SEED)
    fake = {s: shuffled(v, rng) for s, v in real.items()}

    print("building rankings on real data ...")
    real_rank = build_rankings(real, all_keys)
    print("building rankings on shuffled data ...")
    fake_rank = build_rankings(fake, all_keys)
    print()

    rows: list[list[object]] = []
    print("=" * 104)
    print(f"PLACEBO  same experiment on return-shuffled prices   {TRIALS} trials per cell")
    print("momentum is destroyed; volatility and each name's total return are preserved")
    print("=" * 104)
    print(f"{'era':>22s}{'test':>12s}{'real median':>14s}{'real win':>10s}"
          f"{'fake median':>14s}{'fake win':>10s}{'prediction':>20s}")
    print("-" * 104)

    for label, start_key, end_key in ERAS:
        window = [k for k in all_keys if start_key <= k <= end_key]
        available = sorted(
            s for s in symbols
            if sum(1 for k in window if k in real[s].month_end) >= len(window) * 0.95
        )
        if len(available) < POOL + 5:
            continue

        for test, arm, control, use_pool, expectation in (
            ("buy_only", "underweight", "even", False, "should SURVIVE"),
            ("rank", "rank", "random", True, "should VANISH"),
        ):
            _rm, r_med, r_win = measure(real, real_rank, window, available, arm, control, use_pool)
            _fm, f_med, f_win = measure(fake, fake_rank, window, available, arm, control, use_pool)
            print(
                f"{label:>22s}{test:>12s}{r_med:+13.4f}x{r_win:9.1f}%"
                f"{f_med:+13.4f}x{f_win:9.1f}%{expectation:>20s}"
            )
            rows.append([label, test, r_med, r_win, f_med, f_win])

    print("=" * 104)
    print("reading it: buy_only surviving on fake data means the edge is mechanical -- the")
    print("rebalancing premium, which is real but is not a forecast and should be named as such.")
    print("rank surviving on fake data would mean a bug or a look-ahead, not an edge.")

    with (RESULTS_DIR / "placebo.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["era", "test", "real_median_edge", "real_win", "fake_median_edge", "fake_win"])
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
