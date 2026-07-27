"""EXP-011 · Is alpha-selling possible inside locally sideways regimes?

The seven rejected sell rules all lost because trends cut the right tail. The
hypothesis under test: inside a genuinely range-bound stretch there is no tail
to cut, so selling rips and buying dips should pay.

Two gates, in order. Gate 1 closing makes Gate 2 pointless.

    Gate 1  PERSISTENCE  -- can "currently sideways" be identified ex-ante?
            Kaufman Efficiency Ratio over the trailing 60 sessions:
                ER = |close[t] - close[t-n]| / sum(|daily changes|)
            ER near 0 = chop, near 1 = clean trend. Split into terciles on
            TRAILING data only, then look at the FORWARD 60 sessions.
            If low trailing ER does not predict low forward ER, the regime is
            unknowable in advance and the idea dies here.

    Gate 2  MEAN REVERSION -- inside low-ER stretches, do returns actually
            reverse? Measured as the autocorrelation of consecutive 5-day
            returns within each ER bucket. Negative = reversal = selling rips
            pays. Zero or positive = nothing to harvest.

Both gates are pure measurement: no parameters are chosen from results.

Research only.
"""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

ER_WINDOW = 60
FORWARD_WINDOW = 60
RETURN_STEP = 5
WARMUP = 205
BENCHMARKS = {"SPY", "QQQ"}


def load(symbol: str) -> list[float]:
    closes: list[float] = []
    with (DATA_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            closes.append(float(row["close"]))
    return closes


def path_prefix(closes: list[float]) -> list[float]:
    """Cumulative sum of |daily change|, so any window's path length is O(1)."""
    prefix = [0.0]
    for i in range(1, len(closes)):
        prefix.append(prefix[-1] + abs(closes[i] - closes[i - 1]))
    return prefix


def efficiency_ratio(closes: list[float], prefix: list[float], index: int, window: int) -> float | None:
    start = index - window
    if start < 0:
        return None
    path = prefix[index] - prefix[start]
    if path <= 0:
        return None
    return abs(closes[index] - closes[start]) / path


def correlation(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 30:
        return float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else float("nan")


@dataclass
class Sample:
    symbol: str
    index: int
    trailing_er: float
    forward_er: float
    ret_now: float
    ret_next: float
    forward_ret: float


def collect(series: dict[str, list[float]], er_window: int, forward_window: int) -> list[Sample]:
    samples: list[Sample] = []
    for symbol, closes in series.items():
        prefix = path_prefix(closes)
        limit = len(closes) - forward_window - RETURN_STEP - 1
        for index in range(max(WARMUP, er_window + 1), limit, RETURN_STEP):
            trailing = efficiency_ratio(closes, prefix, index, er_window)
            forward = efficiency_ratio(closes, prefix, index + forward_window, forward_window)
            if trailing is None or forward is None:
                continue
            back = closes[index - RETURN_STEP]
            if back <= 0 or closes[index] <= 0:
                continue
            samples.append(
                Sample(
                    symbol=symbol,
                    index=index,
                    trailing_er=trailing,
                    forward_er=forward,
                    ret_now=closes[index] / back - 1,
                    ret_next=closes[index + RETURN_STEP] / closes[index] - 1,
                    forward_ret=closes[index + forward_window] / closes[index] - 1,
                )
            )
    return samples


def split(samples: list[Sample]) -> dict[str, list[Sample]]:
    ordered = sorted(samples, key=lambda s: s.trailing_er)
    third = len(ordered) // 3
    return {
        "low ER (chop)": ordered[:third],
        "mid ER": ordered[third : 2 * third],
        "high ER (trend)": ordered[2 * third :],
    }


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest if r["symbol"] not in BENCHMARKS and int(r["bars"]) >= 2400]
    series = {symbol: load(symbol) for symbol in symbols}

    samples = collect(series, ER_WINDOW, FORWARD_WINDOW)
    buckets = split(samples)

    print("=" * 92)
    print(f"EXP-011  locally sideways regimes   samples {len(samples):,}   symbols {len(symbols)}")
    print(f"detector: Kaufman Efficiency Ratio, trailing {ER_WINDOW}d, forward {FORWARD_WINDOW}d")
    print("=" * 92)
    print("GATE 1  persistence -- does 'currently sideways' predict 'still sideways'?")
    print("-" * 92)
    print(f"{'trailing bucket':>20s}{'trailing ER':>14s}{'forward ER':>13s}{'all-sample fwd':>17s}{'edge':>10s}")
    overall_forward = statistics.median([s.forward_er for s in samples])
    for name, group in buckets.items():
        t = statistics.median([s.trailing_er for s in group])
        f = statistics.median([s.forward_er for s in group])
        print(f"{name:>20s}{t:13.3f}{f:12.3f}{overall_forward:16.3f}{f - overall_forward:+9.3f}")
    corr_er = correlation([s.trailing_er for s in samples], [s.forward_er for s in samples])
    print(f"\n   corr(trailing ER, forward ER) = {corr_er:+.3f}")
    low_stay = sum(1 for s in buckets["low ER (chop)"] if s.forward_er <= overall_forward)
    print(f"   low-ER stretches that stayed below-median = {low_stay / len(buckets['low ER (chop)']) * 100:.1f}%  (coin flip = 50%)")

    print()
    print("=" * 92)
    print(f"GATE 2  mean reversion -- inside each bucket, do consecutive {RETURN_STEP}d returns reverse?")
    print("-" * 92)
    print(f"{'bucket':>20s}{'n':>10s}{'autocorr':>12s}{'meaning':>34s}")
    for name, group in buckets.items():
        rho = correlation([s.ret_now for s in group], [s.ret_next for s in group])
        if rho < -0.02:
            meaning = "reversal -- selling rips may pay"
        elif rho > 0.02:
            meaning = "momentum -- selling rips loses"
        else:
            meaning = "none -- nothing to harvest"
        print(f"{name:>20s}{len(group):>10,d}{rho:+11.3f}{meaning:>34s}")

    print()
    print("-" * 92)
    print(f"cross-check: is a low-ER stretch actually flat over the next {FORWARD_WINDOW}d?")
    print(f"{'bucket':>20s}{'median fwd':>13s}{'mean fwd':>12s}{'|fwd| > 10%':>14s}{'stdev':>10s}")
    for name, group in buckets.items():
        fwd = [s.forward_ret for s in group]
        big = sum(1 for r in fwd if abs(r) > 0.10) / len(fwd)
        print(
            f"{name:>20s}{statistics.median(fwd) * 100:12.2f}%{statistics.fmean(fwd) * 100:11.2f}%"
            f"{big * 100:13.1f}%{statistics.pstdev(fwd) * 100:9.1f}%"
        )
    print("=" * 92)

    print()
    print("=" * 92)
    print("ROBUSTNESS  same two gates across detector window lengths")
    print("-" * 92)
    print(f"{'window':>10s}{'n':>10s}{'corr(ER,fwdER)':>17s}{'stay rate':>12s}{'autocorr chop':>16s}{'autocorr trend':>17s}")
    sweep_rows: list[list[object]] = []
    for window in (20, 40, 60, 120):
        pool = collect(series, window, window)
        groups = split(pool)
        median_fwd = statistics.median([s.forward_er for s in pool])
        stay = sum(1 for s in groups["low ER (chop)"] if s.forward_er <= median_fwd) / len(groups["low ER (chop)"])
        corr = correlation([s.trailing_er for s in pool], [s.forward_er for s in pool])
        chop = correlation([s.ret_now for s in groups["low ER (chop)"]], [s.ret_next for s in groups["low ER (chop)"]])
        trend = correlation([s.ret_now for s in groups["high ER (trend)"]], [s.ret_next for s in groups["high ER (trend)"]])
        print(f"{window:>8d}d{len(pool):>10,d}{corr:+16.3f}{stay * 100:11.1f}%{chop:+15.3f}{trend:+16.3f}")
        sweep_rows.append([window, len(pool), corr, stay, chop, trend])
    print("=" * 92)

    with (RESULTS_DIR / "sideways_alpha_sweep.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["window_days", "n", "corr_trailing_forward_er", "chop_stay_rate", "autocorr_chop", "autocorr_trend"])
        writer.writerows(sweep_rows)

    with (RESULTS_DIR / "sideways_alpha.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "n", "median_trailing_er", "median_forward_er", "autocorr_5d"])
        for name, group in buckets.items():
            writer.writerow([
                name,
                len(group),
                statistics.median([s.trailing_er for s in group]),
                statistics.median([s.forward_er for s in group]),
                correlation([s.ret_now for s in group], [s.ret_next for s in group]),
            ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
