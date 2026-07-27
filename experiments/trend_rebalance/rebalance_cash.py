"""EXP-007 · Rebalancing WITH cash and periodic deposits (the actual design).

EXP-006 tested a 100%-invested stock-only portfolio, which forces every
rebalance to be a SALE. That is not the confirmed design. The design is:

    target 80% equities / 20% cash, plus a fixed monthly deposit

With new money arriving every month, weights can be restored by BUYING the
underweight names instead of SELLING the overweight ones. That version never
trims the right tail and never realises a taxable gain -- exactly the two
mechanisms that killed every sell rule so far.

Policies (deposit handling differs; the stock picks are identical)
    drift_even    deposit split evenly across names. never sell
    buy_only      deposit goes to the MOST underweight names. never sell
    buy_capped    buy_only, but stop buying while equity share > 90%
    full_annual   deposit + full restore of all weights once a year (sells)
    cash_band     restore only when equity share leaves the 70-90% band (sells)

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
INITIAL_CAPITAL = 50_000.0
MONTHLY_DEPOSIT = 1_000.0
EQUITY_TARGET = 0.80
EQUITY_BAND = (0.70, 0.90)
WARMUP = 205

COMMISSION_PER_SHARE = 0.005
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01
SLIPPAGE_BPS = 5.0
CGT_RATE = 0.22
ANNUAL_EXEMPTION_USD = 1_800.0

BENCHMARKS = {"SPY", "QQQ"}
POLICIES = ["drift_even", "buy_only", "buy_capped", "full_annual", "cash_band"]


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
    sells: int = 0
    sell_notional: float = 0.0
    end_equity_share: float = 0.0

    @property
    def final(self) -> float:
        return self.final_pretax - self.tax

    @property
    def multiple(self) -> float:
        return self.final / self.deposited if self.deposited else 0.0


def simulate(
    series: dict[str, tuple[list[str], list[float], list[float]]],
    symbols: list[str],
    policy: str,
    calendar: list[str],
) -> Result:
    book = Book()
    cash = INITIAL_CAPITAL
    realised: dict[str, float] = {}
    result = Result(deposited=INITIAL_CAPITAL)
    per_name = EQUITY_TARGET / len(symbols)

    def px(symbol: str, index: int, which: str) -> float:
        _dates, opens, closes = series[symbol]
        index = min(index, len(closes) - 1)
        return opens[index] if which == "open" else closes[index]

    def equity_value(index: int) -> float:
        return sum(book.shares(s) * px(s, index, "close") for s in symbols)

    def nav(index: int) -> float:
        return cash + equity_value(index)

    def do_buy(symbol: str, index: int, amount: float) -> None:
        nonlocal cash
        if amount <= 1.0 or amount > cash:
            amount = min(amount, cash)
        if amount <= 1.0:
            return
        price = px(symbol, index, "open") * (1 + SLIPPAGE_BPS / 10_000)
        fee = commission(amount / price, price)
        book.buy(symbol, (amount - fee) / price, price)
        cash -= amount

    def do_sell(symbol: str, index: int, shares: float, day: str) -> None:
        nonlocal cash
        if shares <= 1e-9:
            return
        price = px(symbol, index, "open") * (1 - SLIPPAGE_BPS / 10_000)
        fee = commission(shares, price)
        year = day[:4]
        realised[year] = realised.get(year, 0.0) + book.sell(symbol, shares, price) - fee
        cash += shares * price - fee
        result.sells += 1
        result.sell_notional += shares * price

    # initial build to target
    for symbol in symbols:
        do_buy(symbol, WARMUP, INITIAL_CAPITAL * per_name)

    peak = INITIAL_CAPITAL
    last_full = WARMUP
    month = calendar[WARMUP][:7]

    for index in range(WARMUP, len(calendar) - 1):
        value = nav(index)
        peak = max(peak, value)
        result.mdd = max(result.mdd, (peak - value) / peak)

        current_month = calendar[index][:7]
        new_month = current_month != month
        if new_month:
            month = current_month
            cash += MONTHLY_DEPOSIT
            result.deposited += MONTHLY_DEPOSIT

            total = nav(index)
            share = equity_value(index) / total if total else 0.0
            gaps = {
                s: per_name * total - book.shares(s) * px(s, index, "close")
                for s in symbols
            }

            if policy == "drift_even":
                for symbol in symbols:
                    do_buy(symbol, index + 1, MONTHLY_DEPOSIT / len(symbols))
            elif policy in ("buy_only", "buy_capped", "cash_band", "full_annual"):
                if policy == "buy_capped" and share >= EQUITY_BAND[1]:
                    pass  # hold the deposit as cash
                else:
                    hungry = sorted((g for g in gaps.items() if g[1] > 0), key=lambda kv: -kv[1])
                    budget = MONTHLY_DEPOSIT
                    if hungry:
                        need = sum(g for _s, g in hungry)
                        for symbol, gap in hungry:
                            do_buy(symbol, index + 1, budget * gap / need)
                    else:
                        for symbol in symbols:
                            do_buy(symbol, index + 1, budget / len(symbols))

        if policy == "full_annual" and index - last_full >= 252:
            total = nav(index)
            for symbol in symbols:
                held = book.shares(symbol)
                want = per_name * total / px(symbol, index + 1, "open")
                if held - want > 1e-9:
                    do_sell(symbol, index + 1, held - want, calendar[index + 1])
            for symbol in symbols:
                want_value = per_name * total
                have = book.shares(symbol) * px(symbol, index + 1, "open")
                if want_value - have > 1.0:
                    do_buy(symbol, index + 1, want_value - have)
            last_full = index

        elif policy == "cash_band":
            total = nav(index)
            share = equity_value(index) / total if total else 0.0
            if share > EQUITY_BAND[1]:
                excess = (share - EQUITY_TARGET) * total
                for symbol in symbols:
                    price = px(symbol, index + 1, "open")
                    quantity = min(book.shares(symbol), excess / len(symbols) / price)
                    do_sell(symbol, index + 1, quantity, calendar[index + 1])
            elif share < EQUITY_BAND[0] and cash > 1.0:
                deficit = (EQUITY_TARGET - share) * total
                for symbol in symbols:
                    do_buy(symbol, index + 1, min(deficit / len(symbols), cash))

    last = len(calendar) - 1
    result.final_pretax = nav(last)
    result.end_equity_share = equity_value(last) / result.final_pretax if result.final_pretax else 0.0
    result.tax = sum(max(0.0, g - ANNUAL_EXEMPTION_USD) * CGT_RATE for g in realised.values())
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
        outcomes = {p: simulate(series, symbols, p, calendar) for p in POLICIES}
        base = outcomes["drift_even"].multiple
        row: dict[str, float | str] = {"portfolio": trial}
        for policy, outcome in outcomes.items():
            row[f"{policy}_mult"] = outcome.multiple
            row[f"{policy}_vs_base"] = outcome.multiple - base
            row[f"{policy}_mdd"] = outcome.mdd
            row[f"{policy}_tax"] = outcome.tax
            row[f"{policy}_sells"] = outcome.sells
            row[f"{policy}_eqshare"] = outcome.end_equity_share
        rows.append(row)

    with (RESULTS_DIR / "rebalance_cash.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    total_in = INITIAL_CAPITAL + MONTHLY_DEPOSIT * (len(calendar) - WARMUP) / 21
    print("=" * 96)
    print(f"EXP-007  rebalancing WITH cash + deposits   ({PORTFOLIOS} random {HOLDINGS}-name portfolios)")
    print(f"initial ${INITIAL_CAPITAL:,.0f} + ${MONTHLY_DEPOSIT:,.0f}/month   total in ~${total_in:,.0f}   target equity {EQUITY_TARGET:.0%}")
    print("=" * 96)
    print(f"{'policy':>13s}{'median x':>11s}{'mean x':>10s}{'vs base':>11s}{'beat base':>12s}{'MDD':>8s}{'tax':>10s}{'sells':>8s}{'end eq%':>9s}")
    print("-" * 96)
    for policy in POLICIES:
        mult = [float(r[f"{policy}_mult"]) for r in rows]
        diff = [float(r[f"{policy}_vs_base"]) for r in rows]
        mdd = [float(r[f"{policy}_mdd"]) for r in rows]
        tax = [float(r[f"{policy}_tax"]) for r in rows]
        sells = [float(r[f"{policy}_sells"]) for r in rows]
        eq = [float(r[f"{policy}_eqshare"]) for r in rows]
        wins = sum(1 for d in diff if d > 1e-9)
        print(
            f"{policy:>13s}{statistics.median(mult):10.2f}x{statistics.fmean(mult):9.2f}x"
            f"{statistics.median(diff):10.3f}x{wins:8d}/{len(rows):<3d}"
            f"{statistics.median(mdd) * 100:7.1f}%{statistics.fmean(tax):9,.0f}${statistics.fmean(sells):7.0f}"
            f"{statistics.median(eq) * 100:8.0f}%"
        )
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
