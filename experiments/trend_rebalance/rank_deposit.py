"""EXP-013 · Does protocol v0.1's alpha survive with the selling removed?

The audit showed v0.1 beats equal-weight hold by +7.91%p after tax on the
point-in-time universe -- but it pays for that with 128x turnover and $135,772
of capital gains tax, and it loses in the pre-2022 third.

The audit also showed WHY it worked when seven other sell rules failed: it never
asks "should I exit". It always holds a full book and only ever asks "which
names". The sell is a by-product of a buy. That is an allocation question, the
same shape as the two rules that passed (EXP-003 E1, EXP-007 buy_only).

So the ranking can be moved off the sell trigger and onto the deposit, keeping
the allocation logic and dropping the turnover:

    even        deposits split evenly across the ten held names
    buy_only    deposits go to the most underweight names (EXP-007 winner)
    rank_held   deposits go to the top-ranked names AMONG THE TEN HELD
    rank_open   deposits go to the top-ranked names in the whole universe,
                so new names enter over time and the book grows

No arm sells anything. Every arm therefore pays zero capital gains tax, which is
the point: this measures whether the ranking carries information, stripped of
the tax and turnover that made v0.1 expensive.

Ranking is v0.1's own: risk-adjusted 6m and 12m skip-1-month momentum, z-scored
and averaged, gated on price > 10-month SMA. Scores are computed once for the
whole universe per month and cached -- they do not depend on what is held.

Research only.
"""

from __future__ import annotations

import csv
import math
import random
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

PORTFOLIOS = 200
HOLDINGS = 10
SEED = 20260727
INITIAL_CAPITAL = 50_000.0
MONTHLY_DEPOSIT = 1_000.0
WARMUP_MONTHS = 14
SMA_MONTHS = 10
VOL_YEARS = 3
RISK_FREE_ANNUAL = 0.02
DEPOSIT_TARGETS = 3          # how many top-ranked names a deposit is spread over
MAX_NAMES_OPEN = 20          # rank_open cannot grow without bound

COMMISSION_PER_SHARE = 0.005
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01
SLIPPAGE_BPS = 5.0
BENCHMARKS = {"SPY", "QQQ"}
# random_open is the control that matters: it grows to the same name count on the
# same schedule from the same eligible pool, choosing at random instead of by rank.
#   breadth effect = random_open - even
#   ranking effect = rank_open - random_open
ARMS = ["even", "buy_only", "rank_held", "rank_open", "random_open"]


@dataclass
class Series:
    symbol: str
    closes: list[float]
    opens: list[float]
    month_end: dict[str, int] = field(default_factory=dict)
    month_first: dict[str, int] = field(default_factory=dict)


def load(symbol: str) -> Series:
    closes: list[float] = []
    opens: list[float] = []
    dates: list[date] = []
    with (DATA_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dates.append(date.fromisoformat(row["date"]))
            opens.append(float(row["open"]))
            closes.append(float(row["close"]))
    series = Series(symbol=symbol, closes=closes, opens=opens)
    for index, day in enumerate(dates):
        key = f"{day.year:04d}-{day.month:02d}"
        series.month_end[key] = index
        series.month_first.setdefault(key, index)
    return series


def weekly_returns(series: Series, upto: int, years: int) -> list[float]:
    start = max(1, upto - years * 252)
    closes = series.closes[start : upto + 1]
    return [closes[i] / closes[i - 5] - 1.0 for i in range(5, len(closes), 5) if closes[i - 5] > 0]


def annualised_vol(returns: list[float]) -> float | None:
    return statistics.pstdev(returns) * math.sqrt(52) if len(returns) >= 30 else None


def zscore(values: dict[str, float]) -> dict[str, float] | None:
    if len(values) < 5:
        return None
    data = list(values.values())
    sd = statistics.pstdev(data)
    if sd <= 0:
        return None
    mean = statistics.fmean(data)
    return {k: (v - mean) / sd for k, v in values.items()}


def build_rankings(series_map: dict[str, Series], keys: list[str]) -> dict[str, list[str]]:
    """month key -> universe symbols ordered best-first, gate failures dropped."""
    rankings: dict[str, list[str]] = {}
    rf6 = (1 + RISK_FREE_ANNUAL) ** 0.5 - 1

    for t in range(WARMUP_MONTHS, len(keys)):
        key = keys[t]
        needed = [keys[t - offset] for offset in range(0, 14)]
        er6: dict[str, float] = {}
        er12: dict[str, float] = {}
        sigma: dict[str, float] = {}
        gate: list[str] = []

        for symbol, series in series_map.items():
            if any(k not in series.month_end for k in needed):
                continue
            closes = [series.closes[series.month_end[k]] for k in needed]
            if closes[7] <= 0 or closes[13] <= 0:
                continue
            vol = annualised_vol(weekly_returns(series, series.month_end[keys[t - 1]], VOL_YEARS))
            if vol is None or vol <= 0:
                continue
            sigma[symbol] = vol
            er6[symbol] = (closes[1] / closes[7] - 1.0) - rf6
            er12[symbol] = (closes[1] / closes[13] - 1.0) - RISK_FREE_ANNUAL
            if closes[0] > statistics.fmean(closes[0:SMA_MONTHS]):
                gate.append(symbol)

        if len(sigma) < 5:
            rankings[key] = []
            continue
        z6 = zscore({s: er6[s] / sigma[s] for s in sigma})
        z12 = zscore({s: er12[s] / sigma[s] for s in sigma})
        if z6 is None or z12 is None:
            rankings[key] = []
            continue
        final = zscore({s: 0.5 * z6[s] + 0.5 * z12[s] for s in sigma})
        if final is None:
            rankings[key] = []
            continue
        score = final
        rankings[key] = sorted(gate, key=lambda s: (-score[s], s))
    return rankings


def commission(shares: float, price: float) -> float:
    return min(max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares), shares * price * COMMISSION_CAP_RATE)


@dataclass
class Result:
    final: float = 0.0
    deposited: float = 0.0
    mdd: float = 0.0
    names: int = 0

    @property
    def multiple(self) -> float:
        return self.final / self.deposited if self.deposited else 0.0


def simulate(
    series_map: dict[str, Series],
    keys: list[str],
    rankings: dict[str, list[str]],
    start: list[str],
    arm: str,
    rng: random.Random | None = None,
    start_month: int = WARMUP_MONTHS,
) -> Result:
    shares: dict[str, float] = {}
    cash = INITIAL_CAPITAL
    result = Result(deposited=INITIAL_CAPITAL)

    def close_price(symbol: str, key: str) -> float | None:
        series = series_map[symbol]
        index = series.month_end.get(key)
        return series.closes[index] if index is not None else None

    def open_price(symbol: str, key: str) -> float | None:
        series = series_map[symbol]
        index = series.month_first.get(key)
        return series.opens[index] if index is not None else None

    def equity(key: str) -> float:
        total = 0.0
        for symbol, held in shares.items():
            price = close_price(symbol, key)
            if price is not None:
                total += held * price
        return total

    def buy(symbol: str, key: str, amount: float) -> None:
        nonlocal cash
        amount = min(amount, cash)
        price = open_price(symbol, key)
        if price is None or price <= 0 or amount <= 1.0:
            return
        price *= 1 + SLIPPAGE_BPS / 10_000
        fee = commission(amount / price, price)
        shares[symbol] = shares.get(symbol, 0.0) + (amount - fee) / price
        cash -= amount

    first = start_month
    for symbol in start:
        buy(symbol, keys[first], INITIAL_CAPITAL / len(start))

    peak = INITIAL_CAPITAL
    for t in range(first, len(keys) - 1):
        key, key_next = keys[t], keys[t + 1]
        nav = cash + equity(key)
        peak = max(peak, nav)
        result.mdd = max(result.mdd, (peak - nav) / peak if peak > 0 else 0.0)

        cash += MONTHLY_DEPOSIT
        result.deposited += MONTHLY_DEPOSIT
        ranked = rankings.get(key, [])

        if arm == "even":
            targets = list(shares)
        elif arm == "buy_only":
            total = cash + equity(key)
            per = total / max(1, len(shares))
            gaps = {s: per - shares[s] * (close_price(s, key) or 0.0) for s in shares}
            targets = [s for s, g in sorted(gaps.items(), key=lambda kv: -kv[1]) if g > 0][:DEPOSIT_TARGETS]
        elif arm == "rank_held":
            targets = [s for s in ranked if s in shares][:DEPOSIT_TARGETS]
        elif arm == "rank_open":
            room = MAX_NAMES_OPEN - len(shares)
            targets = [s for s in ranked if s in shares or room > 0][:DEPOSIT_TARGETS]
        else:  # random_open -- identical mechanics, rank replaced by a shuffle
            pool = list(ranked)
            if rng is not None:
                rng.shuffle(pool)
            room = MAX_NAMES_OPEN - len(shares)
            targets = [s for s in pool if s in shares or room > 0][:DEPOSIT_TARGETS]

        if not targets:
            targets = list(shares)
        for symbol in targets:
            buy(symbol, key_next, MONTHLY_DEPOSIT / len(targets))

    result.final = cash + equity(keys[-1])
    result.names = len(shares)
    return result


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    symbols = [r["symbol"] for r in manifest if r["symbol"] not in BENCHMARKS]
    series_map = {s: load(s) for s in symbols}
    keys = sorted(load("SPY").month_end)

    print("building monthly universe rankings (cached once, reused by every portfolio) ...")
    rankings = build_rankings(series_map, keys)
    live = sum(1 for v in rankings.values() if v)
    print(f"  {live}/{len(rankings)} months ranked, median gate-pass names "
          f"{statistics.median([len(v) for v in rankings.values() if v]):.0f}")

    pool = [s for s in symbols if len(series_map[s].month_end) >= 100]
    rng = random.Random(SEED)
    rows: list[dict[str, float]] = []
    for trial in range(PORTFOLIOS):
        start = rng.sample(pool, HOLDINGS)
        row: dict[str, float] = {"portfolio": float(trial)}
        base = None
        for arm in ARMS:
            outcome = simulate(series_map, keys, rankings, start, arm, random.Random(SEED + trial))
            if arm == "even":
                base = outcome.multiple
            row[f"{arm}_mult"] = outcome.multiple
            row[f"{arm}_vs_even"] = outcome.multiple - (base or 0.0)
            row[f"{arm}_mdd"] = outcome.mdd
            row[f"{arm}_names"] = outcome.names
        rows.append(row)

    with (RESULTS_DIR / "rank_deposit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print()
    print("=" * 98)
    print(f"EXP-013  protocol ranking moved from the SELL trigger to the DEPOSIT   "
          f"({PORTFOLIOS} random {HOLDINGS}-name books)")
    print("no arm sells anything -- capital gains tax is $0 in every column")
    print("=" * 98)
    print(f"{'arm':>12s}{'median x':>11s}{'mean x':>10s}{'vs even (med)':>16s}{'win rate':>11s}{'MDD':>9s}{'names':>8s}")
    print("-" * 98)
    for arm in ARMS:
        mult = [float(r[f"{arm}_mult"]) for r in rows]
        diff = [float(r[f"{arm}_vs_even"]) for r in rows]
        mdd = [float(r[f"{arm}_mdd"]) for r in rows]
        names = [float(r[f"{arm}_names"]) for r in rows]
        wins = sum(1 for d in diff if d > 0) / len(diff)
        print(
            f"{arm:>12s}{statistics.median(mult):10.3f}x{statistics.fmean(mult):9.3f}x"
            f"{statistics.median(diff):+15.4f}x{wins * 100:10.1f}%"
            f"{statistics.median(mdd) * 100:8.1f}%{statistics.fmean(names):8.1f}"
        )
    print("-" * 98)
    for arm in ARMS[1:]:
        diff = [float(r[f"{arm}_vs_even"]) for r in rows]
        se = statistics.pstdev(diff) / math.sqrt(len(diff))
        print(f"   {arm:>11s} vs even          mean {statistics.fmean(diff):+.4f}x   SE {se:.4f}x   "
              f"t = {statistics.fmean(diff) / se if se > 0 else 0:+.2f}")

    print("-" * 98)
    print("DECOMPOSITION  the open arms hold 20 names and may reach the whole universe,")
    print("               so their edge over `even` mixes breadth with ranking.")
    isolated = [float(r["rank_open_mult"]) - float(r["random_open_mult"]) for r in rows]
    se = statistics.pstdev(isolated) / math.sqrt(len(isolated))
    breadth = [float(r["random_open_vs_even"]) for r in rows]
    print(f"   breadth  random_open - even     mean {statistics.fmean(breadth):+.4f}x")
    print(f"   RANKING  rank_open - random_open mean {statistics.fmean(isolated):+.4f}x   SE {se:.4f}x   "
          f"t = {statistics.fmean(isolated) / se if se > 0 else 0:+.2f}   "
          f"win {sum(1 for d in isolated if d > 0) / len(isolated) * 100:.1f}%")
    print("=" * 98)

    # The protocol audit already showed momentum losing in the pre-2022 third, so a
    # t of +24 on one start date proves little. Re-run for books opened later.
    print()
    print("=" * 98)
    print("COHORTS  same measurement for books opened at different times")
    print("-" * 98)
    print(f"{'opened':>12s}{'months':>9s}{'rank_open':>12s}{'random_open':>14s}{'RANKING edge':>15s}{'t':>9s}{'win':>9s}")
    cohort_rows: list[list[object]] = []
    for offset in (0, 24, 48, 66):
        month = WARMUP_MONTHS + offset
        if month >= len(keys) - 12:
            continue
        rng_c = random.Random(SEED)
        gaps: list[float] = []
        ranks: list[float] = []
        rands: list[float] = []
        for trial in range(100):
            start = rng_c.sample(pool, HOLDINGS)
            a = simulate(series_map, keys, rankings, start, "rank_open", random.Random(SEED + trial), month)
            b = simulate(series_map, keys, rankings, start, "random_open", random.Random(SEED + trial), month)
            ranks.append(a.multiple)
            rands.append(b.multiple)
            gaps.append(a.multiple - b.multiple)
        se_c = statistics.pstdev(gaps) / math.sqrt(len(gaps))
        t_c = statistics.fmean(gaps) / se_c if se_c > 0 else 0.0
        win = sum(1 for g in gaps if g > 0) / len(gaps) * 100
        print(
            f"{keys[month]:>12s}{len(keys) - month:>9d}{statistics.fmean(ranks):11.3f}x"
            f"{statistics.fmean(rands):13.3f}x{statistics.fmean(gaps):+14.4f}x{t_c:+9.2f}{win:8.1f}%"
        )
        cohort_rows.append([keys[month], len(keys) - month, statistics.fmean(ranks), statistics.fmean(rands), statistics.fmean(gaps), t_c, win])
    print("=" * 98)

    with (RESULTS_DIR / "rank_deposit_cohorts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["opened", "months", "rank_open_mult", "random_open_mult", "ranking_edge", "t", "win_pct"])
        writer.writerows(cohort_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
