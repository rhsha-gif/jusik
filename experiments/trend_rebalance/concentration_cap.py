"""EXP-010 · What does a pure concentration cap cost?

Seven sell rules have been rejected, all of them attempts to earn MORE by
selling. This tests the other kind: selling to keep one name from becoming the
whole portfolio -- ruin prevention, not alpha.

It is the only survivor of the three conditions the failures implied:
    low frequency   a 40% cap fires almost never
    not price-based it reads a WEIGHT, and only an extreme one
    no re-entry     the trim is permanent; nothing is bought back

The question is therefore not "does it earn more" (it cannot) but
"how much does the insurance cost, and does it buy anything".

Setup matches the confirmed design: monthly deposits, deposit goes to the most
underweight names, never sell except the cap. Costs and Korean CGT included.

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
INITIAL_CAPITAL = 50_000.0
MONTHLY_DEPOSIT = 1_000.0
WARMUP = 205
CAPS: list[float | None] = [None, 0.50, 0.40, 0.33, 0.25]

COMMISSION_PER_SHARE = 0.005
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01
SLIPPAGE_BPS = 5.0
CGT_RATE = 0.22
ANNUAL_EXEMPTION_USD = 1_800.0
BENCHMARKS = {"SPY", "QQQ"}


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
    deposited: float = 0.0
    mdd: float = 0.0
    trims: int = 0
    max_weight: float = 0.0
    worst_1y: float = 0.0

    @property
    def final(self) -> float:
        return self.final_pretax - self.tax

    @property
    def multiple(self) -> float:
        return self.final / self.deposited if self.deposited else 0.0


def simulate(
    series: dict[str, tuple[list[str], list[float], list[float]]],
    symbols: list[str],
    cap: float | None,
    calendar: list[str],
) -> Result:
    book = Book()
    cash = INITIAL_CAPITAL
    realised: dict[str, float] = {}
    result = Result(deposited=INITIAL_CAPITAL)
    per_name = 1.0 / len(symbols)

    def px(symbol: str, index: int, which: str) -> float:
        _dates, opens, closes = series[symbol]
        index = min(index, len(closes) - 1)
        return opens[index] if which == "open" else closes[index]

    def equity(index: int) -> float:
        return sum(book.shares(s) * px(s, index, "close") for s in symbols)

    def do_buy(symbol: str, index: int, amount: float) -> None:
        nonlocal cash
        amount = min(amount, cash)
        if amount <= 1.0:
            return
        price = px(symbol, index, "open") * (1 + SLIPPAGE_BPS / 10_000)
        fee = commission(amount / price, price)
        book.buy(symbol, (amount - fee) / price, price)
        cash -= amount

    for symbol in symbols:
        do_buy(symbol, WARMUP, INITIAL_CAPITAL * per_name)

    peak = INITIAL_CAPITAL
    month = calendar[WARMUP][:7]
    equity_curve: list[float] = []

    for index in range(WARMUP, len(calendar) - 1):
        nav = cash + equity(index)
        equity_curve.append(nav)
        peak = max(peak, nav)
        result.mdd = max(result.mdd, (peak - nav) / peak)

        if calendar[index][:7] != month:
            month = calendar[index][:7]
            cash += MONTHLY_DEPOSIT
            result.deposited += MONTHLY_DEPOSIT
            total = cash + equity(index)
            gaps = {s: per_name * total - book.shares(s) * px(s, index, "close") for s in symbols}
            hungry = sorted((g for g in gaps.items() if g[1] > 0), key=lambda kv: -kv[1])
            if hungry:
                need = sum(g for _s, g in hungry)
                for symbol, gap in hungry:
                    do_buy(symbol, index + 1, MONTHLY_DEPOSIT * gap / need)
            else:
                for symbol in symbols:
                    do_buy(symbol, index + 1, MONTHLY_DEPOSIT / len(symbols))

        total = cash + equity(index)
        if total > 0:
            for symbol in symbols:
                weight = book.shares(symbol) * px(symbol, index, "close") / total
                result.max_weight = max(result.max_weight, weight)
                if cap is not None and weight > cap:
                    sell_price = px(symbol, index + 1, "open") * (1 - SLIPPAGE_BPS / 10_000)
                    quantity = (weight - cap) * total / sell_price
                    quantity = min(quantity, book.shares(symbol))
                    fee = commission(quantity, sell_price)
                    year = calendar[index + 1][:4]
                    realised[year] = realised.get(year, 0.0) + book.sell(symbol, quantity, sell_price) - fee
                    cash += quantity * sell_price - fee
                    result.trims += 1

    last = len(calendar) - 1
    result.final_pretax = cash + equity(last)
    result.tax = sum(max(0.0, g - ANNUAL_EXEMPTION_USD) * CGT_RATE for g in realised.values())
    if len(equity_curve) > 252:
        result.worst_1y = min(
            equity_curve[i + 252] / equity_curve[i] - 1
            for i in range(0, len(equity_curve) - 252, 21)
        )
    return result


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    pool = [r["symbol"] for r in manifest if r["symbol"] not in BENCHMARKS and int(r["bars"]) >= 2400]
    series = {symbol: load(symbol) for symbol in pool}
    calendar = series[pool[0]][0]

    rng = random.Random(SEED)
    rows: list[dict[str, float | str]] = []
    for trial in range(PORTFOLIOS):
        symbols = rng.sample(pool, HOLDINGS)
        outcomes = {cap: simulate(series, symbols, cap, calendar) for cap in CAPS}
        base = outcomes[None].multiple
        row: dict[str, float | str] = {"portfolio": trial}
        for cap, outcome in outcomes.items():
            key = "none" if cap is None else f"{int(cap * 100)}"
            row[f"cap{key}_mult"] = outcome.multiple
            row[f"cap{key}_vs_none"] = outcome.multiple - base
            row[f"cap{key}_mdd"] = outcome.mdd
            row[f"cap{key}_tax"] = outcome.tax
            row[f"cap{key}_trims"] = outcome.trims
            row[f"cap{key}_maxw"] = outcome.max_weight
            row[f"cap{key}_worst1y"] = outcome.worst_1y
        rows.append(row)

    with (RESULTS_DIR / "concentration_cap.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print("=" * 100)
    print(f"EXP-010  concentration cap = ruin insurance   ({PORTFOLIOS} random {HOLDINGS}-name portfolios, deposits on)")
    print("=" * 100)
    print(f"{'cap':>7s}{'median x':>11s}{'mean x':>10s}{'vs none':>11s}{'fired in':>11s}{'MDD':>9s}{'worst 1y':>11s}{'tax':>10s}{'max wt':>9s}")
    print("-" * 100)
    for cap in CAPS:
        key = "none" if cap is None else f"{int(cap * 100)}"
        mult = [float(r[f"cap{key}_mult"]) for r in rows]
        diff = [float(r[f"cap{key}_vs_none"]) for r in rows]
        mdd = [float(r[f"cap{key}_mdd"]) for r in rows]
        worst = [float(r[f"cap{key}_worst1y"]) for r in rows]
        tax = [float(r[f"cap{key}_tax"]) for r in rows]
        maxw = [float(r[f"cap{key}_maxw"]) for r in rows]
        fired = sum(1 for r in rows if float(r[f"cap{key}_trims"]) > 0)
        label = "none" if cap is None else f"{cap:.0%}"
        print(
            f"{label:>7s}{statistics.median(mult):10.2f}x{statistics.fmean(mult):9.2f}x"
            f"{statistics.median(diff):10.3f}x{fired:8d}/{len(rows):<3d}"
            f"{statistics.median(mdd) * 100:8.1f}%{statistics.median(worst) * 100:10.1f}%"
            f"{statistics.fmean(tax):9,.0f}${statistics.median(maxw) * 100:8.0f}%"
        )
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
