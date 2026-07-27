"""EXP-012 · Once the decision to sell is made, does slicing the exit help?

Every rejected sell rule asked "sell or not" -- a prediction. Both surviving
rules asked "how do I split something already decided" -- an allocation. This is
the first allocation question on the sell side, and it is the mirror image of
the buy-side split that passed as EXP-003 E1.

The exit trigger is a human judgement (thesis broken / thesis realised), so it
cannot be backtested. It is proxied two ways:

    random    an exit date drawn uniformly -- the unconditional case. Chosen
              deliberately: any price-based trigger would smuggle the prediction
              problem back in, which is exactly what EXP-011 just killed.
    drawdown  the first day the name sits 20% below its trailing 250d high --
              a bad-news proxy, since a broken thesis rarely arrives on a high.

Three effects must be kept apart or the result is meaningless:

    drift     mean proceeds rise simply because equities drift up and slicing
              delays the sale. This is NOT a benefit of slicing; it is the
              benefit of having sold later, and it comes with the risk of
              having sold later.
    spread    the dispersion of proceeds. This is what slicing actually buys:
              the same average outcome with fewer extreme ones.
    tax       Korean CGT allows a fresh annual deduction each calendar year, so
              an exit straddling New Year is taxed twice-deducted. Arithmetic,
              not prediction -- the one certain gain available.

Tax is reported under two assumptions because a single position cannot know
whether the year's deduction is still unused:
    exemption  this sale is the only realisation that year (best case)
    marginal   the deduction is already consumed elsewhere (worst case)

Research only.
"""

from __future__ import annotations

import csv
import random
import statistics
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

POSITION_USD = 10_000.0
HOLD_SESSIONS = 750
EXITS_PER_SYMBOL = 40
SEED = 20260727
WARMUP = 205
MAX_HORIZON = 260

DRAWDOWN_LOOKBACK = 250
DRAWDOWN_TRIGGER = 0.20

COMMISSION_PER_SHARE = 0.005
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01
SLIPPAGE_BPS = 5.0
CGT_RATE = 0.22
ANNUAL_EXEMPTION_USD = 1_800.0
BENCHMARKS = {"SPY", "QQQ"}

# (label, tranche count, sessions between tranches)
SCHEMES: list[tuple[str, int, int]] = [
    ("immediate", 1, 0),
    ("2 x weekly", 2, 5),
    ("4 x weekly", 4, 5),
    ("8 x weekly", 8, 5),
    ("2 x monthly", 2, 21),
    ("4 x monthly", 4, 21),
    ("8 x monthly", 8, 21),
    ("4 x quarterly", 4, 63),
    ("2 x half-year", 2, 126),
]


@dataclass
class Series:
    dates: list[str]
    opens: list[float]
    closes: list[float]


def load(symbol: str) -> Series:
    dates: list[str] = []
    opens: list[float] = []
    closes: list[float] = []
    with (DATA_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dates.append(row["date"])
            opens.append(float(row["open"]))
            closes.append(float(row["close"]))
    return Series(dates, opens, closes)


def commission(shares: float, price: float) -> float:
    return min(max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares), shares * price * COMMISSION_CAP_RATE)


@dataclass
class Outcome:
    proceeds: float
    tax_exempt: float
    tax_marginal: float
    years_touched: int

    def net(self, mode: str) -> float:
        return self.proceeds - (self.tax_exempt if mode == "exemption" else self.tax_marginal)


def run_scheme(series: Series, exit_index: int, cost_price: float, shares: float, tranches: int, gap: int) -> Outcome | None:
    per_tranche = shares / tranches
    proceeds = 0.0
    by_year: dict[str, float] = {}
    for step in range(tranches):
        index = exit_index + step * gap
        if index >= len(series.opens):
            return None
        price = series.opens[index] * (1 - SLIPPAGE_BPS / 10_000)
        fee = commission(per_tranche, price)
        gross = per_tranche * price - fee
        proceeds += gross
        year = series.dates[index][:4]
        by_year[year] = by_year.get(year, 0.0) + per_tranche * (price - cost_price) - fee
    tax_exempt = sum(max(0.0, g - ANNUAL_EXEMPTION_USD) * CGT_RATE for g in by_year.values())
    tax_marginal = sum(max(0.0, g) * CGT_RATE for g in by_year.values())
    return Outcome(proceeds, tax_exempt, tax_marginal, len(by_year))


def drawdown_triggers(series: Series, limit: int) -> list[int]:
    triggers: list[int] = []
    armed = True
    for index in range(max(WARMUP, DRAWDOWN_LOOKBACK), limit):
        peak = max(series.closes[index - DRAWDOWN_LOOKBACK : index + 1])
        below = peak > 0 and series.closes[index] / peak - 1 <= -DRAWDOWN_TRIGGER
        if below and armed:
            triggers.append(index)
            armed = False
        elif not below:
            armed = True
    return triggers


def evaluate(series_map: dict[str, Series], scenario: str, rng: random.Random) -> dict[str, list[float]]:
    """Returns, per scheme label, the list of net-vs-immediate ratios plus raw stats."""
    collected: dict[str, list[Outcome]] = {label: [] for label, _t, _g in SCHEMES}
    baseline: list[Outcome] = []

    for symbol, series in series_map.items():
        limit = len(series.opens) - MAX_HORIZON
        low = max(WARMUP + HOLD_SESSIONS, DRAWDOWN_LOOKBACK)
        if limit <= low:
            continue
        if scenario == "random":
            picks = [rng.randrange(low, limit) for _ in range(EXITS_PER_SYMBOL)]
        else:
            picks = [i for i in drawdown_triggers(series, limit) if i >= low]

        for exit_index in picks:
            entry_index = exit_index - HOLD_SESSIONS
            entry_price = series.opens[entry_index] * (1 + SLIPPAGE_BPS / 10_000)
            if entry_price <= 0:
                continue
            shares = (POSITION_USD - commission(POSITION_USD / entry_price, entry_price)) / entry_price
            results = {}
            for label, tranches, gap in SCHEMES:
                outcome = run_scheme(series, exit_index, entry_price, shares, tranches, gap)
                if outcome is None:
                    break
                results[label] = outcome
            if len(results) != len(SCHEMES):
                continue
            baseline.append(results["immediate"])
            for label, outcome in results.items():
                collected[label].append(outcome)

    return {"__n__": [float(len(baseline))]} | {
        label: [o.net("exemption") for o in outs] for label, outs in collected.items()
    } | {
        f"{label}::marginal": [o.net("marginal") for o in outs] for label, outs in collected.items()
    } | {
        f"{label}::years": [float(o.years_touched) for o in outs] for label, outs in collected.items()
    }


def report(title: str, data: dict[str, list[float]]) -> list[list[object]]:
    n = int(data["__n__"][0])
    base_ex = data["immediate"]
    base_mg = data["immediate::marginal"]
    print()
    print("=" * 104)
    print(f"{title}   exits {n:,}   position ${POSITION_USD:,.0f}   held {HOLD_SESSIONS} sessions before the trigger")
    print("=" * 104)
    print(
        f"{'scheme':>15s}{'net (exempt)':>15s}{'vs immediate':>15s}{'spread':>10s}"
        f"{'worst 5%':>11s}{'net (marginal)':>17s}{'vs imm':>10s}{'yrs':>7s}"
    )
    print("-" * 104)
    rows: list[list[object]] = []
    for label, _tranches, _gap in SCHEMES:
        ex = data[label]
        mg = data[f"{label}::marginal"]
        ratio_ex = [a / b for a, b in zip(ex, base_ex) if b > 0]
        ratio_mg = [a / b for a, b in zip(mg, base_mg) if b > 0]
        ordered = sorted(ratio_ex)
        p5 = ordered[max(0, int(len(ordered) * 0.05))]
        spread = statistics.pstdev(ratio_ex)
        years = statistics.fmean(data[f"{label}::years"])
        print(
            f"{label:>15s}{statistics.fmean(ex):14,.0f}${(statistics.fmean(ratio_ex) - 1) * 100:+14.2f}%"
            f"{spread * 100:9.1f}%{(p5 - 1) * 100:+10.1f}%"
            f"{statistics.fmean(mg):16,.0f}${(statistics.fmean(ratio_mg) - 1) * 100:+9.2f}%{years:7.2f}"
        )
        rows.append([
            title, label, n, statistics.fmean(ex), statistics.fmean(ratio_ex) - 1,
            spread, p5 - 1, statistics.fmean(mg), statistics.fmean(ratio_mg) - 1, years,
        ])
    print("=" * 104)
    return rows


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest if r["symbol"] not in BENCHMARKS and int(r["bars"]) >= 2400]
    series_map = {symbol: load(symbol) for symbol in symbols}

    all_rows: list[list[object]] = []
    all_rows += report(
        "EXP-012 A  random exit date (unconditional)",
        evaluate(series_map, "random", random.Random(SEED)),
    )
    all_rows += report(
        f"EXP-012 B  exit after a {DRAWDOWN_TRIGGER:.0%} drawdown from the {DRAWDOWN_LOOKBACK}d high (bad-news proxy)",
        evaluate(series_map, "drawdown", random.Random(SEED)),
    )

    with (RESULTS_DIR / "exit_slicing.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "scenario", "scheme", "n", "mean_net_exempt", "vs_immediate_exempt",
            "spread", "p5_vs_immediate", "mean_net_marginal", "vs_immediate_marginal", "mean_years_touched",
        ])
        writer.writerows(all_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
