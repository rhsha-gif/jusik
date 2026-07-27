"""EXP-006 · Does periodic rebalancing beat letting weights drift?

Rebalancing is the one sell rule that makes NO price prediction. It sells
because a weight grew, not because a price is expected to fall. That puts it
outside the four rules EXP-001/002/004/005 rejected, so it deserves its own test.

Three claimed benefits, only one of which is prediction-free:
    (1) concentration control          -- prediction-free, legitimate
    (2) rebalancing premium ~ w*sigma^2/2 -- only holds with ZERO drift
    (3) mean-reversion bet             -- a prediction; EXP-001 rejected this family

Design: draw many random equal-weight portfolios from the same universe, then
run each under several rebalancing policies. Stock selection is identical
across policies, so survivorship bias and selection skill cancel in the
difference -- the same property that made EXP-003 trustworthy.

Policies
    drift      buy once, never touch                      (baseline)
    annual     restore equal weight every 12 months
    quarterly  every 3 months
    band20     restore only when a name is >20% relative off its target
    cap25      trim only what exceeds 25% of the portfolio (concentration control only)

Costs: IBKR commission + 5bp slippage + Korean CGT 22% (FIFO, USD 1,800/yr free).

Research only.
"""

from __future__ import annotations

import csv
import random
import statistics
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

PORTFOLIOS = 200
HOLDINGS = 10
SEED = 20260726
START_CAPITAL = 100_000.0
WARMUP = 205

COMMISSION_PER_SHARE = 0.005
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01
SLIPPAGE_BPS = 5.0
CGT_RATE = 0.22
ANNUAL_EXEMPTION_USD = 1_800.0

BENCHMARKS = {"SPY", "QQQ"}
POLICIES = ["drift", "annual", "quarterly", "band20", "cap25"]


@dataclass
class Lot:
    shares: float
    cost: float


@dataclass
class Book:
    lots: dict[str, list[Lot]] = field(default_factory=dict)

    def shares(self, symbol: str) -> float:
        return sum(lot.shares for lot in self.lots.get(symbol, []))

    def buy(self, symbol: str, shares: float, price: float) -> None:
        self.lots.setdefault(symbol, []).append(Lot(shares, price))

    def sell(self, symbol: str, shares: float, price: float) -> float:
        remaining, realised = shares, 0.0
        queue = self.lots.get(symbol, [])
        while remaining > 1e-12 and queue:
            lot = queue[0]
            take = min(lot.shares, remaining)
            realised += take * (price - lot.cost)
            lot.shares -= take
            remaining -= take
            if lot.shares <= 1e-12:
                queue.pop(0)
        return realised


def load(symbol: str) -> tuple[list[str], list[float], list[float]]:
    dates: list[str] = []
    opens: list[float] = []
    closes: list[float] = []
    with (DATA_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dates.append(row["date"])
            opens.append(float(row["open"]))
            closes.append(float(row["close"]))
    return dates, opens, closes


def commission(shares: float, price: float) -> float:
    return min(max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares), shares * price * COMMISSION_CAP_RATE)


@dataclass
class Result:
    final_pretax: float = 0.0
    tax: float = 0.0
    mdd: float = 0.0
    turnover: float = 0.0
    trades: int = 0

    @property
    def final(self) -> float:
        return self.final_pretax - self.tax


def simulate(
    series: dict[str, tuple[list[str], list[float], list[float]]],
    symbols: list[str],
    policy: str,
    calendar: list[str],
) -> Result:
    book = Book()
    cash = START_CAPITAL
    realised: dict[str, float] = {}
    result = Result()
    target = 1.0 / len(symbols)

    def price(symbol: str, index: int, field_name: str) -> float | None:
        _dates, opens, closes = series[symbol]
        if index >= len(closes):
            return None
        return opens[index] if field_name == "open" else closes[index]

    def equity(index: int) -> float:
        total = cash
        for symbol in symbols:
            close = price(symbol, index, "close")
            if close is not None:
                total += book.shares(symbol) * close
        return total

    def trade_to(index: int, weights: dict[str, float]) -> None:
        nonlocal cash
        nav = equity(index)
        # sells first
        for symbol in symbols:
            fill = price(symbol, index, "open")
            if fill is None:
                continue
            held = book.shares(symbol)
            want = weights.get(symbol, 0.0) * nav / fill
            if held - want > 1e-9:
                quantity = held - want
                sell_price = fill * (1 - SLIPPAGE_BPS / 10_000)
                fee = commission(quantity, sell_price)
                year = calendar[index][:4]
                realised[year] = realised.get(year, 0.0) + book.sell(symbol, quantity, sell_price) - fee
                cash += quantity * sell_price - fee
                result.turnover += quantity * sell_price
                result.trades += 1
        # buys with actual cash
        for symbol in symbols:
            fill = price(symbol, index, "open")
            if fill is None:
                continue
            buy_price = fill * (1 + SLIPPAGE_BPS / 10_000)
            need = weights.get(symbol, 0.0) * nav - book.shares(symbol) * buy_price
            spend = min(need, cash)
            if spend <= 1.0:
                continue
            fee = commission(spend / buy_price, buy_price)
            book.buy(symbol, (spend - fee) / buy_price, buy_price)
            cash -= spend
            result.turnover += spend
            result.trades += 1

    trade_to(WARMUP, {s: target for s in symbols})

    peak = START_CAPITAL
    last_rebalance = WARMUP
    for index in range(WARMUP, len(calendar) - 1):
        value = equity(index)
        peak = max(peak, value)
        result.mdd = max(result.mdd, (peak - value) / peak)

        if policy == "drift":
            continue
        due = False
        if policy == "annual":
            due = index - last_rebalance >= 252
        elif policy == "quarterly":
            due = index - last_rebalance >= 63
        elif policy in ("band20", "cap25"):
            nav = value
            for symbol in symbols:
                close = price(symbol, index, "close")
                if close is None:
                    continue
                weight = book.shares(symbol) * close / nav
                if policy == "band20" and abs(weight / target - 1) > 0.20:
                    due = True
                if policy == "cap25" and weight > 0.25:
                    due = True
        if not due:
            continue

        if policy == "cap25":  # trim excess only, redistribute to the rest
            nav = equity(index)
            weights: dict[str, float] = {}
            excess = 0.0
            under: list[str] = []
            for symbol in symbols:
                close = price(symbol, index, "close")
                weight = (book.shares(symbol) * close / nav) if close else 0.0
                if weight > 0.25:
                    excess += weight - 0.25
                    weights[symbol] = 0.25
                else:
                    weights[symbol] = weight
                    under.append(symbol)
            if under and excess > 0:
                for symbol in under:
                    weights[symbol] += excess / len(under)
            trade_to(index + 1, weights)
        else:
            trade_to(index + 1, {s: target for s in symbols})
        last_rebalance = index

    result.final_pretax = equity(len(calendar) - 1)
    result.tax = sum(max(0.0, g - ANNUAL_EXEMPTION_USD) * CGT_RATE for g in realised.values())
    return result


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    pool = [
        row["symbol"] for row in manifest
        if row["symbol"] not in BENCHMARKS and int(row["bars"]) >= 2400
    ]
    series = {symbol: load(symbol) for symbol in pool}
    calendar = series[pool[0]][0]
    print(f"pool {len(pool)} names, {len(calendar)} sessions, {PORTFOLIOS} random {HOLDINGS}-name portfolios")

    rng = random.Random(SEED)
    rows: list[dict[str, float | str]] = []
    for trial in range(PORTFOLIOS):
        symbols = rng.sample(pool, HOLDINGS)
        outcomes = {policy: simulate(series, symbols, policy, calendar) for policy in POLICIES}
        base = outcomes["drift"].final
        row: dict[str, float | str] = {"portfolio": trial}
        for policy, outcome in outcomes.items():
            row[f"{policy}_ret"] = outcome.final / START_CAPITAL - 1
            row[f"{policy}_vs_drift"] = (outcome.final - base) / START_CAPITAL
            row[f"{policy}_mdd"] = outcome.mdd
            row[f"{policy}_tax"] = outcome.tax
            row[f"{policy}_turn"] = outcome.turnover / START_CAPITAL
        rows.append(row)

    with (RESULTS_DIR / "rebalance.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print("=" * 92)
    print(f"EXP-006  rebalancing vs drift   ({PORTFOLIOS} random {HOLDINGS}-name equal-weight portfolios)")
    print("=" * 92)
    print(f"{'policy':>12s}{'median ret':>13s}{'mean ret':>12s}{'vs drift':>12s}{'beat drift':>13s}{'MDD':>9s}{'tax':>10s}{'turnover':>10s}")
    print("-" * 92)
    for policy in POLICIES:
        rets = [float(r[f"{policy}_ret"]) for r in rows]
        diffs = [float(r[f"{policy}_vs_drift"]) for r in rows]
        mdds = [float(r[f"{policy}_mdd"]) for r in rows]
        taxes = [float(r[f"{policy}_tax"]) for r in rows]
        turns = [float(r[f"{policy}_turn"]) for r in rows]
        wins = sum(1 for d in diffs if d > 1e-9)
        print(
            f"{policy:>12s}{statistics.median(rets) * 100:12.1f}%{statistics.fmean(rets) * 100:11.1f}%"
            f"{statistics.median(diffs) * 100:11.2f}%{wins:9d}/{len(rows):<3d}"
            f"{statistics.median(mdds) * 100:8.1f}%{statistics.fmean(taxes):9,.0f}${statistics.fmean(turns):9.2f}x"
        )
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
