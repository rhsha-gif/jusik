"""EXP-003 · Does a pullback entry rule buy at a better price than buying it all at once?

This is the question the user's purpose actually asks:

    "I decide WHAT to buy. I don't want to watch charts every day to decide WHEN.
     If the system does the watching, does the result get worse?"

So every arm buys THE SAME stock, over THE SAME window, with THE SAME money.
Only the entry schedule differs. Nothing is sold except on delisting, so
stock-selection skill and survivorship bias affect all arms identically and
cancel in the difference.

Arms (rule pool assembled from public implementations, not invented here):
    E0  lump      buy 100% at the first eligible session          -- baseline, zero watching
    E1  stonks    200SMA up-trend + price within 3% of 50SMA      -- github.com/chand1012/stonks
    E2  rsi14     200SMA up-trend + RSI(14) < 40                  -- classic pullback
    E3  rsi2      200SMA up-trend + RSI(2) < 10                   -- Connors RSI-2 family
    E4  calendar  4 equal buys 63 sessions apart, no signal       -- control, zero watching

E1-E3 buy in 4 equal tranches, one per signal, at the NEXT session's open.
If tranches remain unfilled at the end of the window they are bought at the
last session (money cannot stay uninvested forever).

Pass criterion agreed in advance: a timing rule is worth having if it is
NOT WORSE than E0. Beating E0 is a bonus; matching it already buys back the
user's time. Losing to E0 means the watching is being paid for in returns.

Research only.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data_pit"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

START_CAPITAL = 10_000.0
TRANCHES = 4
TREND_WINDOW = 200
PULLBACK_MA = 50
PULLBACK_BAND = 0.03          # within 3% of the 50SMA
RSI14_THRESHOLD = 40.0
RSI2_THRESHOLD = 10.0
CALENDAR_GAP = 63             # ~3 months
WARMUP = TREND_WINDOW + 5

COMMISSION_PER_SHARE = 0.005
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01
SLIPPAGE_BPS = 5.0


@dataclass
class Bars:
    symbol: str
    dates: list[str]
    opens: list[float]
    closes: list[float]


def load(symbol: str) -> Bars:
    dates: list[str] = []
    opens: list[float] = []
    closes: list[float] = []
    with (DATA_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dates.append(row["date"])
            opens.append(float(row["open"]))
            closes.append(float(row["close"]))
    return Bars(symbol=symbol, dates=dates, opens=opens, closes=closes)


def sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        if index >= window - 1:
            out[index] = running / window
    return out


def rsi(values: list[float], window: int) -> list[float | None]:
    """Wilder RSI."""
    out: list[float | None] = [None] * len(values)
    gains = 0.0
    losses = 0.0
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if index <= window:
            gains += gain
            losses += loss
            if index == window:
                gains /= window
                losses /= window
                out[index] = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
            continue
        gains = (gains * (window - 1) + gain) / window
        losses = (losses * (window - 1) + loss) / window
        out[index] = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
    return out


def commission(shares: float, price: float) -> float:
    return min(max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares), shares * price * COMMISSION_CAP_RATE)


@dataclass
class ArmResult:
    final_value: float
    avg_cost: float
    sessions_uninvested: int
    fills: int


def run_arm(bars: Bars, signals: list[bool] | None, calendar: bool = False) -> ArmResult:
    """signals[i] True -> buy one tranche at open of i+1. None -> lump at first session."""
    n = len(bars.closes)
    tranche_cash = START_CAPITAL / TRANCHES
    cash = START_CAPITAL
    shares = 0.0
    spent = 0.0
    bought_shares = 0.0
    filled = 0
    uninvested = 0

    def buy(index: int, amount: float) -> None:
        nonlocal cash, shares, spent, bought_shares, filled
        price = bars.opens[index] * (1 + SLIPPAGE_BPS / 10_000)
        fee = commission(amount / price, price)
        qty = (amount - fee) / price
        shares += qty
        bought_shares += qty
        spent += amount
        cash -= amount
        filled += 1

    if calendar:  # E4 fixed schedule
        for tranche in range(TRANCHES):
            index = min(WARMUP + tranche * CALENDAR_GAP, n - 1)
            buy(index, tranche_cash)
    elif signals is None:  # E0 lump
        buy(WARMUP, START_CAPITAL)
    else:
        for index in range(WARMUP, n - 1):
            if filled >= TRANCHES:
                break
            if signals[index]:
                buy(index + 1, tranche_cash)
        while filled < TRANCHES:      # force-fill leftovers at the end
            buy(n - 1, tranche_cash)

    for index in range(WARMUP, n):
        if bars.closes[index] * shares < 1e-9:
            uninvested += 1

    final = shares * bars.closes[-1] + cash
    avg_cost = spent / bought_shares if bought_shares > 0 else 0.0
    return ArmResult(final_value=final, avg_cost=avg_cost, sessions_uninvested=uninvested, fills=filled)


def build_signals(bars: Bars) -> dict[str, list[bool]]:
    closes = bars.closes
    trend = sma(closes, TREND_WINDOW)
    mid = sma(closes, PULLBACK_MA)
    r14 = rsi(closes, 14)
    r2 = rsi(closes, 2)

    stonks: list[bool] = []
    rsi14: list[bool] = []
    rsi2: list[bool] = []
    for index in range(len(closes)):
        long_ma = trend[index]
        short_ma = mid[index]
        value14 = r14[index]
        value2 = r2[index]
        up = long_ma is not None and closes[index] > long_ma
        stonks.append(
            bool(up and short_ma is not None and abs(closes[index] / short_ma - 1) <= PULLBACK_BAND)
        )
        rsi14.append(bool(up and value14 is not None and value14 < RSI14_THRESHOLD))
        rsi2.append(bool(up and value2 is not None and value2 < RSI2_THRESHOLD))
    return {"E1_stonks": stonks, "E2_rsi14": rsi14, "E3_rsi2": rsi2}


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((DATA_DIR / "_manifest.csv").open(encoding="utf-8")))
    rows: list[dict[str, object]] = []

    for entry in manifest:
        symbol = entry["symbol"]
        bars = load(symbol)
        if len(bars.closes) < WARMUP + 30:
            continue
        signals = build_signals(bars)
        arms = {
            "E0_lump": run_arm(bars, None),
            "E4_calendar": run_arm(bars, None, calendar=True),
        }
        for name, series in signals.items():
            arms[name] = run_arm(bars, series)

        base = arms["E0_lump"].final_value
        row: dict[str, object] = {"symbol": symbol, "delisted": entry["delisted"]}
        for name, result in arms.items():
            row[f"{name}_ret"] = result.final_value / START_CAPITAL - 1
            row[f"{name}_vs_E0"] = (result.final_value - base) / START_CAPITAL
            row[f"{name}_idle"] = result.sessions_uninvested
        rows.append(row)

    with (RESULTS_DIR / "entry_rules.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    arms = ["E0_lump", "E4_calendar", "E1_stonks", "E2_rsi14", "E3_rsi2"]
    labels = {
        "E0_lump": "E0 lump (baseline)",
        "E4_calendar": "E4 calendar 4x",
        "E1_stonks": "E1 200SMA+50SMA3%",
        "E2_rsi14": "E2 200SMA+RSI14<40",
        "E3_rsi2": "E3 200SMA+RSI2<10",
    }

    def report(subset: list[dict[str, object]], title: str) -> None:
        print("=" * 88)
        print(f"{title}   (n={len(subset)})")
        print("-" * 88)
        print(f"{'arm':24s}{'median ret':>13s}{'mean ret':>12s}{'med vs E0':>12s}{'beat E0':>10s}{'idle d':>9s}")
        for arm in arms:
            rets = [float(str(r[f"{arm}_ret"])) for r in subset]
            diffs = [float(str(r[f"{arm}_vs_E0"])) for r in subset]
            idle = [float(str(r[f"{arm}_idle"])) for r in subset]
            wins = sum(1 for d in diffs if d > 1e-9)
            print(
                f"{labels[arm]:24s}{statistics.median(rets) * 100:12.1f}%{statistics.fmean(rets) * 100:11.1f}%"
                f"{statistics.median(diffs) * 100:11.2f}%{wins:8d}/{len(subset):<3d}{statistics.fmean(idle):8.0f}"
            )

    report(rows, "EXP-003  entry-rule pool  |  same stock, same money, only WHEN differs")
    dead = [r for r in rows if r["delisted"] == "yes"]
    if dead:
        report(dead, "delisted subset (acquired 2018)")
    print("=" * 88)
    print("pass = NOT WORSE than E0 (matching already buys back the watching time)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
