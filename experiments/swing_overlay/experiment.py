"""Does a swing overlay add value on top of a core holding?

Pre-registered comparison (parameters fixed before the first run):

    A  core 100%                      -- buy and hold, the baseline
    B  core 70% + swing 30%           -- grid-style swing, no regime filter
    C  core 70% + swing 30%           -- same, but swing SELLS are suppressed
                                         while the 200d SMA slopes up

The number that decides everything is the *swing net contribution*:

    B.cagr - A.cagr   and   C.cagr - A.cagr

Acceptance threshold agreed in advance: **+2.0%p per year**. Below that the
overlay does not pay for its complexity, cost, tax drag and operational load.

Each symbol is simulated independently with the same starting capital, so the
30 symbols form a sample of 30 paired observations rather than one blended
portfolio. That answers "does the swing rule add value on an arbitrary stock"
without letting stock-selection skill contaminate the result.

No look-ahead: a signal computed from session *t* is filled at the open of
session *t+1*.

Research only: never touches a broker.
"""

from __future__ import annotations

import csv
import statistics
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# ---------------------------------------------------------------- parameters
START_CAPITAL = 10_000.0
CORE_FRACTION = 0.70           # B/C put 70% into the untouchable core
SWING_SLOTS = 3                # swing cash is deployed in three equal tranches
SMA_WINDOW = 20                # swing reference price
ATR_WINDOW = 14
ATR_MULTIPLE = 1.5             # band = SMA +/- 1.5 * ATR
TREND_WINDOW = 200             # regime filter reference
TREND_SLOPE_LOOKBACK = 20      # SMA200 rising = SMA200[t] > SMA200[t-20]
WARMUP_BARS = TREND_WINDOW + TREND_SLOPE_LOOKBACK

# ------------------------------------------------------------------ costs/tax
COMMISSION_PER_SHARE = 0.005   # IBKR fixed tier
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01     # capped at 1% of trade value
SLIPPAGE_BPS = 5.0
CAPITAL_GAINS_RATE = 0.22      # KR tax on foreign equity gains
ANNUAL_EXEMPTION_USD = 1_800.0 # ~KRW 2.5m basic deduction

TRADING_DAYS = 252.0


# ------------------------------------------------------------------- helpers
def load_bars(path: Path) -> list[dict[str, float | str]]:
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        {
            "date": row["date"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for row in rows
    ]


def simple_moving_average(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        if index >= window - 1:
            out[index] = running / window
    return out


def wilder_atr(bars: list[dict[str, float | str]], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        high, low = float(bar["high"]), float(bar["low"])
        if index == 0:
            true_ranges.append(high - low)
            continue
        previous_close = float(bars[index - 1]["close"])
        true_ranges.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
    average: float | None = None
    for index in range(len(bars)):
        if index < window:
            continue
        if average is None:
            average = sum(true_ranges[1 : window + 1]) / window
        else:
            average = (average * (window - 1) + true_ranges[index]) / window
        out[index] = average
    return out


def commission(shares: float, price: float) -> float:
    value = shares * price
    fee = max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares)
    return min(fee, value * COMMISSION_CAP_RATE)


def buy_fill(price: float) -> float:
    return price * (1 + SLIPPAGE_BPS / 10_000.0)


def sell_fill(price: float) -> float:
    return price * (1 - SLIPPAGE_BPS / 10_000.0)


# -------------------------------------------------------------------- result
@dataclass
class RunResult:
    label: str
    symbol: str
    final_pretax: float = 0.0
    final_aftertax: float = 0.0
    max_drawdown: float = 0.0
    swing_buys: int = 0
    swing_sells: int = 0
    total_commission: float = 0.0
    total_tax: float = 0.0
    realised_gain: float = 0.0
    years: float = 0.0
    equity_curve: list[float] = field(default_factory=list)

    @property
    def cagr_pretax(self) -> float:
        if self.years <= 0 or self.final_pretax <= 0:
            return 0.0
        return (self.final_pretax / START_CAPITAL) ** (1 / self.years) - 1

    @property
    def cagr_aftertax(self) -> float:
        if self.years <= 0 or self.final_aftertax <= 0:
            return 0.0
        return (self.final_aftertax / START_CAPITAL) ** (1 / self.years) - 1


# ----------------------------------------------------------------- simulation
def simulate(
    symbol: str,
    bars: list[dict[str, float | str]],
    *,
    label: str,
    swing_enabled: bool,
    regime_filter: bool,
) -> RunResult:
    closes = [float(bar["close"]) for bar in bars]
    sma_short = simple_moving_average(closes, SMA_WINDOW)
    sma_trend = simple_moving_average(closes, TREND_WINDOW)
    atr = wilder_atr(bars, ATR_WINDOW)

    result = RunResult(label=label, symbol=symbol)

    start = WARMUP_BARS
    entry_price = buy_fill(float(bars[start]["open"]))
    core_cash = START_CAPITAL * (CORE_FRACTION if swing_enabled else 1.0)
    core_fee = commission(core_cash / entry_price, entry_price)
    core_shares = (core_cash - core_fee) / entry_price
    result.total_commission += core_fee

    swing_cash = START_CAPITAL - core_cash
    tranche = swing_cash / SWING_SLOTS if swing_enabled else 0.0
    swing_shares = 0.0
    swing_cost_basis = 0.0
    slots_used = 0

    pending: str | None = None
    realised_by_year: dict[str, float] = {}
    peak = START_CAPITAL

    for index in range(start, len(bars)):
        bar = bars[index]
        open_price = float(bar["open"])

        # ---- fill yesterday's signal at today's open (no look-ahead)
        if pending == "buy" and slots_used < SWING_SLOTS and swing_cash >= tranche > 0:
            price = buy_fill(open_price)
            fee = commission(tranche / price, price)
            bought = (tranche - fee) / price
            swing_shares += bought
            swing_cost_basis += tranche
            swing_cash -= tranche
            slots_used += 1
            result.swing_buys += 1
            result.total_commission += fee
        elif pending == "sell" and swing_shares > 0:
            price = sell_fill(open_price)
            gross = swing_shares * price
            fee = commission(swing_shares, price)
            proceeds = gross - fee
            year = str(bar["date"])[:4]
            realised_by_year[year] = realised_by_year.get(year, 0.0) + (proceeds - swing_cost_basis)
            swing_cash += proceeds
            swing_shares = 0.0
            swing_cost_basis = 0.0
            slots_used = 0
            result.swing_sells += 1
            result.total_commission += fee
        pending = None

        # ---- mark to market
        close = closes[index]
        equity = core_shares * close + swing_shares * close + swing_cash
        result.equity_curve.append(equity)
        peak = max(peak, equity)
        result.max_drawdown = max(result.max_drawdown, (peak - equity) / peak)

        # ---- generate tomorrow's signal
        if not swing_enabled or index + 1 >= len(bars):
            continue
        reference, band = sma_short[index], atr[index]
        if reference is None or band is None:
            continue
        lower = reference - ATR_MULTIPLE * band
        upper = reference + ATR_MULTIPLE * band

        if close < lower and slots_used < SWING_SLOTS and swing_cash >= tranche > 0:
            pending = "buy"
        elif close > upper and swing_shares > 0:
            if regime_filter:
                trend_now = sma_trend[index]
                trend_past = sma_trend[index - TREND_SLOPE_LOOKBACK]
                rising = trend_now is not None and trend_past is not None and trend_now > trend_past
                if rising:
                    continue  # trending up: hold, do not let the swing sell into strength
            pending = "sell"

    # ---- settle
    final_close = closes[-1]
    result.final_pretax = core_shares * final_close + swing_shares * final_close + swing_cash
    result.realised_gain = sum(realised_by_year.values())
    result.total_tax = sum(
        max(0.0, gain - ANNUAL_EXEMPTION_USD) * CAPITAL_GAINS_RATE
        for gain in realised_by_year.values()
    )
    result.final_aftertax = result.final_pretax - result.total_tax
    result.years = (len(bars) - start) / TRADING_DAYS
    return result


# --------------------------------------------------------------------- report
BUCKETS = {
    "high_vol_growth": ["NVDA", "TSLA", "AMD", "NFLX", "AMZN", "META", "SHOP", "ENPH", "MU", "CRM"],
    "low_vol_defensive": ["JNJ", "PG", "KO", "PEP", "WMT", "MCD", "VZ", "XOM", "CVX", "MRK"],
    "range_bound_cyclical": ["INTC", "CSCO", "IBM", "F", "GM", "PFE", "BAC", "C", "GILD", "T"],
}
ACCEPTANCE_THRESHOLD = 0.02  # +2.0%p per year, agreed before the run


def percent(value: float) -> str:
    return f"{value * 100:7.2f}%"


@dataclass
class SymbolRow:
    bucket: str
    symbol: str
    a_cagr: float
    b_cagr: float
    c_cagr: float
    b_minus_a: float
    c_minus_a: float
    a_mdd: float
    b_mdd: float
    c_mdd: float
    b_trades: int
    c_trades: int
    b_tax: float
    c_tax: float
    b_fees: float
    c_fees: float


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[SymbolRow] = []

    for bucket, symbols in BUCKETS.items():
        for symbol in symbols:
            path = DATA_DIR / f"{symbol}.csv"
            if not path.exists():
                print(f"missing data: {symbol}")
                continue
            bars = load_bars(path)
            a = simulate(symbol, bars, label="A_core_only", swing_enabled=False, regime_filter=False)
            b = simulate(symbol, bars, label="B_swing_nofilter", swing_enabled=True, regime_filter=False)
            c = simulate(symbol, bars, label="C_swing_filtered", swing_enabled=True, regime_filter=True)
            rows.append(
                SymbolRow(
                    bucket=bucket,
                    symbol=symbol,
                    a_cagr=a.cagr_aftertax,
                    b_cagr=b.cagr_aftertax,
                    c_cagr=c.cagr_aftertax,
                    b_minus_a=b.cagr_aftertax - a.cagr_aftertax,
                    c_minus_a=c.cagr_aftertax - a.cagr_aftertax,
                    a_mdd=a.max_drawdown,
                    b_mdd=b.max_drawdown,
                    c_mdd=c.max_drawdown,
                    b_trades=b.swing_buys + b.swing_sells,
                    c_trades=c.swing_buys + c.swing_sells,
                    b_tax=b.total_tax,
                    c_tax=c.total_tax,
                    b_fees=b.total_commission,
                    c_fees=c.total_commission,
                )
            )

    if not rows:
        print("no results")
        return 1

    out_csv = RESULTS_DIR / "per_symbol.csv"
    fieldnames = [field_.name for field_ in fields(SymbolRow)]
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)

    print("=" * 96)
    print("SWING OVERLAY EXPERIMENT  |  30 symbols x 10y  |  after-tax CAGR")
    print(f"pre-registered acceptance: swing net contribution >= +{ACCEPTANCE_THRESHOLD * 100:.1f}%p / year")
    print("=" * 96)
    header = f"{'symbol':7s}{'bucket':24s}{'A core':>9s}{'B swing':>9s}{'C filt':>9s}{'B-A':>9s}{'C-A':>9s}{'B trd':>7s}{'C trd':>7s}"
    print(header)
    print("-" * 96)
    for row in rows:
        print(
            f"{row.symbol:7s}{row.bucket:24s}"
            f"{percent(row.a_cagr):>9s}{percent(row.b_cagr):>9s}{percent(row.c_cagr):>9s}"
            f"{percent(row.b_minus_a):>9s}{percent(row.c_minus_a):>9s}"
            f"{row.b_trades:>7d}{row.c_trades:>7d}"
        )

    print("-" * 96)
    for bucket in BUCKETS:
        subset = [row for row in rows if row.bucket == bucket]
        b_diff = [row.b_minus_a for row in subset]
        c_diff = [row.c_minus_a for row in subset]
        print(
            f"{bucket:31s} median B-A {percent(statistics.median(b_diff))}"
            f"   median C-A {percent(statistics.median(c_diff))}"
        )

    b_all = [row.b_minus_a for row in rows]
    c_all = [row.c_minus_a for row in rows]
    print("=" * 96)
    print("VERDICT")
    for name, diffs in (("B (no filter)", b_all), ("C (regime filter)", c_all)):
        median = statistics.median(diffs)
        mean = statistics.fmean(diffs)
        wins = sum(1 for value in diffs if value > 0)
        print(
            f"  {name:20s} median {percent(median)}  mean {percent(mean)}"
            f"  beat core in {wins}/{len(diffs)} symbols"
            f"  -> {'ACCEPT' if median >= ACCEPTANCE_THRESHOLD else 'REJECT'}"
        )
    print("=" * 96)
    print(f"per-symbol detail written to {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
