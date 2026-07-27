"""EXP-002 · Does the monthly trend-rebalance protocol survive Korean capital gains tax?

Implements protocol v0.1 with N held FIXED (the monthly-discretion N is a known
error in the spec and is out of scope here), then asks the one question that can
kill the design before any paid data is purchased:

    does the protocol still beat equal-weight buy-and-hold AFTER 22% tax?

Tax model (Korean resident holding US equities):
  - FIFO cost basis (per the spec's own finding: sells consume the oldest lots)
  - annual gain/loss netting, KRW 2.5m (~USD 1,800) basic deduction
  - 22% on the excess, no loss carry-forward
  - buy-and-hold pays nothing until it sells (it never does here)

Deliberate simplifications -- all of them make the protocol look BETTER, so a
rejection here is conservative:
  - no survivorship control (all 30 names survived the window)
  - U and N fixed in advance (no selection-bias scenario)
  - risk-free series is a flat 2%/yr constant
  - daily 200/175 band omitted; monthly rebalance only (understates turnover,
    therefore understates tax)
  - no spread/impact beyond a flat slippage

Research only: never touches a broker.
"""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "swing_overlay" / "data"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# ----------------------------------------------------------- protocol v0.1
N_HOLDINGS = 5                 # fixed (spec's monthly-discretion N is out of scope)
SMA_MONTHS = 10                # absolute trend gate
MOM_SHORT, MOM_LONG = 6, 12    # skip-1-month momentum windows
VOL_YEARS = 3                  # weekly-return volatility lookback
VOL_FLOOR = 0.05               # annualised
POSITION_CAP = 0.25
RISK_FREE_ANNUAL = 0.02        # flat proxy for the cash total-return index
WARMUP_MONTHS = 40             # 14 monthly prices + 3y weekly history

# ------------------------------------------------------------ costs / tax
COMMISSION_PER_SHARE = 0.005
COMMISSION_MINIMUM = 1.00
COMMISSION_CAP_RATE = 0.01
SLIPPAGE_BPS = 5.0
CGT_RATE = 0.22
ANNUAL_EXEMPTION_USD = 1_800.0

START_CAPITAL = 100_000.0

SYMBOLS = [
    "NVDA", "TSLA", "AMD", "NFLX", "AMZN", "META", "SHOP", "ENPH", "MU", "CRM",
    "JNJ", "PG", "KO", "PEP", "WMT", "MCD", "VZ", "XOM", "CVX", "MRK",
    "INTC", "CSCO", "IBM", "F", "GM", "PFE", "BAC", "C", "GILD", "T",
]


# ------------------------------------------------------------------- data
@dataclass
class Series:
    symbol: str
    dates: list[date]
    opens: list[float]
    closes: list[float]
    month_end_index: dict[str, int] = field(default_factory=dict)   # "YYYY-MM" -> bar index
    month_first_index: dict[str, int] = field(default_factory=dict)


def load_series(symbol: str) -> Series:
    dates: list[date] = []
    opens: list[float] = []
    closes: list[float] = []
    with (DATA_DIR / f"{symbol}.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            dates.append(date.fromisoformat(row["date"]))
            opens.append(float(row["open"]))
            closes.append(float(row["close"]))
    series = Series(symbol=symbol, dates=dates, opens=opens, closes=closes)
    for index, day in enumerate(dates):
        key = f"{day.year:04d}-{day.month:02d}"
        series.month_end_index[key] = index          # last write wins = month end
        series.month_first_index.setdefault(key, index)
    return series


def month_keys(series: Series) -> list[str]:
    return sorted(series.month_end_index)


def weekly_returns(series: Series, upto_index: int, years: int) -> list[float]:
    """Weekly (5-bar) returns over the trailing `years`, ending at upto_index."""
    span = years * 252
    start = max(1, upto_index - span)
    closes = series.closes[start : upto_index + 1]
    out: list[float] = []
    for i in range(5, len(closes), 5):
        previous = closes[i - 5]
        if previous > 0:
            out.append(closes[i] / previous - 1.0)
    return out


def annualised_vol(returns: list[float]) -> float | None:
    if len(returns) < 30:
        return None
    return statistics.pstdev(returns) * math.sqrt(52)


def zscore(values: dict[str, float]) -> dict[str, float] | None:
    if len(values) < 5:
        return None
    data = list(values.values())
    mean = statistics.fmean(data)
    sd = statistics.pstdev(data)
    if sd <= 0:
        return None
    return {key: (value - mean) / sd for key, value in values.items()}


# ------------------------------------------------------------------ trade
def commission(shares: float, price: float) -> float:
    return min(max(COMMISSION_MINIMUM, COMMISSION_PER_SHARE * shares), shares * price * COMMISSION_CAP_RATE)


@dataclass
class Lot:
    shares: float
    cost_per_share: float


@dataclass
class Book:
    """FIFO lot book -- sells consume the OLDEST lots first (the spec's own point)."""
    lots: dict[str, list[Lot]] = field(default_factory=dict)

    def shares(self, symbol: str) -> float:
        return sum(lot.shares for lot in self.lots.get(symbol, []))

    def buy(self, symbol: str, shares: float, price: float) -> None:
        self.lots.setdefault(symbol, []).append(Lot(shares=shares, cost_per_share=price))

    def sell(self, symbol: str, shares: float, price: float) -> float:
        """Return realised gain, consuming lots FIFO."""
        remaining = shares
        realised = 0.0
        queue = self.lots.get(symbol, [])
        while remaining > 1e-12 and queue:
            lot = queue[0]
            take = min(lot.shares, remaining)
            realised += take * (price - lot.cost_per_share)
            lot.shares -= take
            remaining -= take
            if lot.shares <= 1e-12:
                queue.pop(0)
        return realised


# ------------------------------------------------------------- simulation
@dataclass
class RunStats:
    label: str
    equity: list[tuple[str, float]] = field(default_factory=list)
    realised_by_year: dict[str, float] = field(default_factory=dict)
    commissions: float = 0.0
    turnover_notional: float = 0.0
    rebalances: int = 0
    name_changes: int = 0

    def tax_total(self) -> float:
        return sum(
            max(0.0, gain - ANNUAL_EXEMPTION_USD) * CGT_RATE
            for gain in self.realised_by_year.values()
        )


def build_targets(
    series_map: dict[str, Series],
    keys: list[str],
    t_index: int,
    incumbents: set[str],
) -> dict[str, float] | None:
    """Protocol v0.1 monthly target vector (risky weights; remainder is cash)."""
    key_t = keys[t_index]
    eligible: list[str] = []
    gate_pass: list[str] = []
    er6: dict[str, float] = {}
    er12: dict[str, float] = {}
    sigma_score: dict[str, float] = {}
    sigma_weight: dict[str, float] = {}

    rf6 = (1 + RISK_FREE_ANNUAL) ** 0.5 - 1
    rf12 = RISK_FREE_ANNUAL

    for symbol, series in series_map.items():
        needed = [keys[t_index - offset] for offset in range(0, 14)]
        if any(k not in series.month_end_index for k in needed):
            continue
        closes = [series.closes[series.month_end_index[k]] for k in needed]  # [T, T-1, ..., T-13]
        bar_t = series.month_end_index[key_t]
        bar_t1 = series.month_end_index[keys[t_index - 1]]
        vol_score = annualised_vol(weekly_returns(series, bar_t1, VOL_YEARS))
        vol_weight = annualised_vol(weekly_returns(series, bar_t, VOL_YEARS))
        if vol_score is None or vol_weight is None or vol_score <= 0:
            continue

        eligible.append(symbol)
        sigma_score[symbol] = vol_score
        sigma_weight[symbol] = vol_weight
        er6[symbol] = (closes[1] / closes[7] - 1.0) - rf6
        er12[symbol] = (closes[1] / closes[13] - 1.0) - rf12

        sma10 = statistics.fmean(closes[0:SMA_MONTHS])
        if closes[0] > sma10:
            gate_pass.append(symbol)

    if len(eligible) < 5:
        return None

    ra6 = {s: er6[s] / sigma_score[s] for s in eligible}
    ra12 = {s: er12[s] / sigma_score[s] for s in eligible}
    z6, z12 = zscore(ra6), zscore(ra12)
    if z6 is None or z12 is None:
        return None
    combined = {s: 0.5 * z6[s] + 0.5 * z12[s] for s in eligible}
    final = zscore(combined)
    if final is None:
        return None

    ranked = sorted(
        gate_pass,
        key=lambda s: (-final[s], -ra12[s], sigma_score[s], s),
    )

    # 50% replacement buffer
    buffer_size = math.ceil(N_HOLDINGS / 2)
    priority = max(1, N_HOLDINGS - buffer_size)
    retain_limit = N_HOLDINGS + buffer_size

    selected: list[str] = ranked[:priority]
    for position, symbol in enumerate(ranked[priority:retain_limit], start=priority):
        if len(selected) >= N_HOLDINGS:
            break
        if symbol in incumbents:
            selected.append(symbol)
    for symbol in ranked:
        if len(selected) >= N_HOLDINGS:
            break
        if symbol not in selected:
            selected.append(symbol)

    k = len(selected)
    if k == 0:
        return {}

    risk_budget = k / N_HOLDINGS
    quotient = {s: 1.0 / max(sigma_weight[s], VOL_FLOOR) for s in selected}

    weights: dict[str, float] = {}
    remaining_budget = risk_budget
    open_names = set(selected)
    while open_names and remaining_budget > 1e-9:
        total = sum(quotient[s] for s in open_names)
        capped_now: set[str] = set()
        for symbol in list(open_names):
            share = remaining_budget * quotient[symbol] / total
            proposed = weights.get(symbol, 0.0) + share
            if proposed >= POSITION_CAP - 1e-12:
                weights[symbol] = POSITION_CAP
                capped_now.add(symbol)
            else:
                weights[symbol] = proposed
        if not capped_now:
            break
        remaining_budget = risk_budget - sum(weights.values())
        open_names -= capped_now
    return weights


def simulate(
    series_map: dict[str, Series],
    keys: list[str],
    *,
    protocol: bool,
    window: tuple[str, str] | None = None,
) -> RunStats:
    label = "protocol" if protocol else "equal_weight_hold"
    stats = RunStats(label=label)
    book = Book()
    cash = START_CAPITAL
    incumbents: set[str] = set()

    def price_at(symbol: str, key: str, field_name: str) -> float | None:
        series = series_map[symbol]
        if key not in series.month_end_index:
            return None
        index = series.month_end_index[key] if field_name == "close" else series.month_first_index[key]
        return series.closes[index] if field_name == "close" else series.opens[index]

    def equity_at(key: str) -> float:
        total = cash
        for symbol in list(book.lots):
            price = price_at(symbol, key, "close")
            if price is not None:
                total += book.shares(symbol) * price
        return total

    first = WARMUP_MONTHS
    last = len(keys) - 1
    if window is not None:
        first = max(first, next(i for i, k in enumerate(keys) if k >= window[0]))
        last = min(last, next(i for i, k in enumerate(keys) if k >= window[1]))

    for t_index in range(first, last):
        key_t, key_next = keys[t_index], keys[t_index + 1]

        # record equity BEFORE any trading so the curve reflects month-end state
        stats.equity.append((key_t, equity_at(key_t)))

        if protocol:
            targets = build_targets(series_map, keys, t_index, incumbents)
            if targets is None:
                continue
        else:
            if book.lots:  # buy and hold: only invest once
                continue
            targets = {s: 1.0 / len(series_map) for s in series_map}

        nav = equity_at(key_t)
        fills = {s: price_at(s, key_next, "open") for s in set(list(book.lots) + list(targets))}

        # --- sells first
        for symbol in list(book.lots):
            price = fills.get(symbol)
            if price is None:
                continue
            held = book.shares(symbol)
            want = targets.get(symbol, 0.0) * nav / price
            if held - want > 1e-9:
                shares = held - want
                fill = price * (1 - SLIPPAGE_BPS / 10_000)
                fee = commission(shares, fill)
                gain = book.sell(symbol, shares, fill) - fee
                year = key_next[:4]
                stats.realised_by_year[year] = stats.realised_by_year.get(year, 0.0) + gain
                cash += shares * fill - fee
                stats.commissions += fee
                stats.turnover_notional += shares * fill

        # --- buys with actual available cash
        for symbol, weight in sorted(targets.items(), key=lambda kv: -kv[1]):
            price = fills.get(symbol)
            if price is None or weight <= 0:
                continue
            fill = price * (1 + SLIPPAGE_BPS / 10_000)
            want_value = weight * nav
            have_value = book.shares(symbol) * fill
            need = want_value - have_value
            if need <= 1e-9:
                continue
            spend = min(need, cash)
            if spend <= 1.0:
                continue
            fee = commission(spend / fill, fill)
            shares = (spend - fee) / fill
            book.buy(symbol, shares, fill)
            cash -= spend
            stats.commissions += fee
            stats.turnover_notional += spend

        stats.rebalances += 1
        new_set = {s for s, w in targets.items() if w > 0}
        stats.name_changes += len(new_set ^ incumbents)
        incumbents = new_set

    stats.equity.append((keys[last], equity_at(keys[last])))
    return stats


# ---------------------------------------------------------------- reporting
def metrics(stats: RunStats) -> dict[str, float]:
    values = [value for _, value in stats.equity]
    years = len(values) / 12.0
    final_pre = values[-1]
    final_post = final_pre - stats.tax_total()
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = max(mdd, (peak - value) / peak)
    return {
        "final_pretax": final_pre,
        "final_aftertax": final_post,
        "cagr_pretax": (final_pre / START_CAPITAL) ** (1 / years) - 1,
        "cagr_aftertax": (final_post / START_CAPITAL) ** (1 / years) - 1,
        "mdd": mdd,
        "tax": stats.tax_total(),
        "fees": stats.commissions,
        "turnover_x": stats.turnover_notional / START_CAPITAL,
        "years": years,
    }


BUCKETS = {
    "growth": SYMBOLS[0:10],
    "defensive": SYMBOLS[10:20],
    "cyclical": SYMBOLS[20:30],
}


def main(universe: list[str] | None = None, tag: str = "all30") -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    symbols = universe or SYMBOLS
    series_map = {symbol: load_series(symbol) for symbol in symbols}
    keys = sorted(set.intersection(*[set(month_keys(s)) for s in series_map.values()]))

    hold = simulate(series_map, keys, protocol=False)
    proto = simulate(series_map, keys, protocol=True)
    m_hold, m_proto = metrics(hold), metrics(proto)

    print("=" * 84)
    print(f"EXP-002  protocol v0.1  |  universe={tag} ({len(symbols)})  |  N={N_HOLDINGS}  |  {m_proto['years']:.1f}y")
    print("=" * 84)
    print(f"{'':22s}{'equal-wt hold':>16s}{'protocol':>16s}{'diff':>14s}")
    print("-" * 84)
    for name, key in (
        ("CAGR pre-tax", "cagr_pretax"),
        ("CAGR after-tax", "cagr_aftertax"),
        ("max drawdown", "mdd"),
    ):
        a, b = m_hold[key], m_proto[key]
        print(f"{name:22s}{a * 100:15.2f}%{b * 100:15.2f}%{(b - a) * 100:13.2f}%p")
    print(f"{'total tax paid':22s}{m_hold['tax']:15,.0f}${m_proto['tax']:15,.0f}$")
    print(f"{'total commissions':22s}{m_hold['fees']:15,.0f}${m_proto['fees']:15,.0f}$")
    print(f"{'turnover (x capital)':22s}{m_hold['turnover_x']:15.2f}x{m_proto['turnover_x']:15.2f}x")
    print(f"{'rebalances':22s}{hold.rebalances:16d}{proto.rebalances:16d}")
    print(f"{'name changes/month':22s}{'-':>16s}{proto.name_changes / max(1, proto.rebalances):16.2f}")

    print("=" * 84)
    print("VERDICT")
    pre = m_proto["cagr_pretax"] - m_hold["cagr_pretax"]
    post = m_proto["cagr_aftertax"] - m_hold["cagr_aftertax"]
    print(f"  pre-tax edge   {pre * 100:+6.2f}%p   -> {'beats hold' if pre > 0 else 'loses to hold'}")
    print(f"  after-tax edge {post * 100:+6.2f}%p   -> {'beats hold' if post > 0 else 'loses to hold'}")
    print(f"  tax drag       {(pre - post) * 100:+6.2f}%p")
    print("=" * 84)

    with (RESULTS_DIR / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "equal_weight_hold", "protocol"])
        for key in m_hold:
            writer.writerow([key, m_hold[key], m_proto[key]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
