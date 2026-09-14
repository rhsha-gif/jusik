"""Market-structure evidence computed from daily bars, in code, without look-ahead.

Every number the strategist/designer agents may quote about the universe —
breadth above moving averages, the realized-volatility regime, pairwise
correlation, sector momentum ranks, investor-flow z-scores and a per-symbol
structure table — is computed here from bars dated at or before `as_of`.
Pure standard library plus pydantic; deterministic; short histories yield
`None` plus a Korean note instead of an exception.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from math import sqrt
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Sequence

from quantpilot.services.research_agents.models import InvestorFlows, SectorMove
from quantpilot.services.strategy_design.models import (
    BreadthPoint,
    CorrelationSummary,
    FlowZScore,
    MarketStructureEvidence,
    SectorMomentum,
    SymbolStructure,
    VolatilityRegime,
)

Bar = dict[str, Any]  # {"symbol", "date": "YYYY-MM-DD", "open", "high", "low", "close", "volume"}

TRADING_DAYS_PER_YEAR = 252
HIGH_LOOKBACK = 252
PERCENTILE_LOOKBACK = 252
PERCENTILE_MIN_SAMPLES = 60
FLOW_LOOKBACK = 60
FLOW_MIN_SAMPLES = 20
FLOW_GROUPS = ("foreign", "institution", "individual")


def _r(value: float) -> float:
    return round(float(value), 6)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_ohlcv_rows(path: str | Path, notes: list[str] | None = None) -> list[Bar]:
    """Read `symbol,date,open,high,low,close,volume` rows; malformed rows are skipped and counted in `notes`."""

    rows: list[Bar] = []
    skipped = 0
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            try:
                symbol = str(raw["symbol"]).strip()
                bar_date = date.fromisoformat(str(raw["date"]).strip()).isoformat()
                parsed: Bar = {
                    "symbol": symbol,
                    "date": bar_date,
                    "open": float(raw["open"]),
                    "high": float(raw["high"]),
                    "low": float(raw["low"]),
                    "close": float(raw["close"]),
                    "volume": int(float(raw["volume"])),
                }
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            if not symbol or parsed["close"] <= 0:
                skipped += 1
                continue
            rows.append(parsed)
    if skipped and notes is not None:
        notes.append(f"OHLCV 로드: 형식이 잘못된 {skipped}행을 건너뜀")
    return rows


def load_securities(path: str | Path) -> dict[str, dict[str, str]]:
    """Read `symbol,name,market,sector,...` into {symbol: {"name", "sector"}}."""

    out: dict[str, dict[str, str]] = {}
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            symbol = str(raw.get("symbol") or "").strip()
            if not symbol:
                continue
            out[symbol] = {
                "name": str(raw.get("name") or "").strip(),
                "sector": str(raw.get("sector") or "").strip(),
            }
    return out


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Population Pearson correlation; None when either series has zero variance."""

    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0.0 or syy == 0.0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sqrt(sxx * syy)


def _returns(closes: Sequence[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]


def _annualized_vol_pct(returns: Sequence[float]) -> float:
    return pstdev(returns) * sqrt(TRADING_DAYS_PER_YEAR) * 100.0


def _vol_label(percentile: float | None) -> str:
    if percentile is None:
        return "normal"
    if percentile < 25.0:
        return "low"
    if percentile < 75.0:
        return "normal"
    if percentile < 95.0:
        return "high"
    return "extreme"


def _corr_label(mean_pairwise: float) -> str:
    if mean_pairwise < 0.3:
        return "dispersed"
    if mean_pairwise < 0.6:
        return "normal"
    return "crowded"


def _group_bars(rows: Sequence[Bar], as_of: str) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = {}
    for row in rows:
        bar_date = str(row["date"])
        if bar_date > as_of:
            continue  # no look-ahead: bars after as_of are never seen
        grouped.setdefault(str(row["symbol"]), []).append(row)
    for bars in grouped.values():
        bars.sort(key=lambda b: str(b["date"]))
    return grouped


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _breadth(closes_by_symbol: dict[str, list[float]], windows: Sequence[int], notes: list[str]) -> list[BreadthPoint]:
    universe = len(closes_by_symbol)
    points: list[BreadthPoint] = []
    for window in windows:
        eligible = {s: c for s, c in closes_by_symbol.items() if len(c) >= window}
        above = sum(1 for c in eligible.values() if c[-1] > mean(c[-window:]))
        total = len(eligible)
        pct = _r(above / total * 100.0) if total else 0.0
        if total == 0:
            notes.append(f"폭(SMA{window}): {window}일 바를 갖춘 종목이 없어 0%로 기록")
        elif total < universe:
            notes.append(f"폭(SMA{window}): 전체 {universe}개 중 {total}개만 {window}일 바를 갖춰 나머지는 제외")
        points.append(BreadthPoint(window=window, pct_above_sma=pct, count_above=above, count_total=total))
    return points


def _equal_weight_returns(bars_by_symbol: dict[str, list[Bar]]) -> tuple[list[str], list[float], dict[str, dict[str, float]]]:
    """Mean of per-symbol daily returns per date, on dates where at least half the universe has a return."""

    per_symbol: dict[str, dict[str, float]] = {}
    for symbol, bars in bars_by_symbol.items():
        series: dict[str, float] = {}
        for i in range(1, len(bars)):
            prev = float(bars[i - 1]["close"])
            if prev > 0:
                series[str(bars[i]["date"])] = float(bars[i]["close"]) / prev - 1.0
        per_symbol[symbol] = series
    universe = len(bars_by_symbol)
    all_dates = sorted({d for series in per_symbol.values() for d in series})
    dates: list[str] = []
    values: list[float] = []
    for d in all_dates:
        day = [series[d] for series in per_symbol.values() if d in series]
        if universe and len(day) * 2 >= universe:
            dates.append(d)
            values.append(mean(day))
    return dates, values, per_symbol


def _volatility(ew_returns: Sequence[float], vol_window: int, notes: list[str]) -> VolatilityRegime:
    if len(ew_returns) < 2:
        notes.append(f"변동성: 동일가중 일간 수익률이 {len(ew_returns)}개뿐이라 실현변동성을 0으로 기록")
        return VolatilityRegime(window=vol_window, realized_vol_annualized=0.0, percentile_1y=None, label="normal")
    if len(ew_returns) < vol_window:
        notes.append(f"변동성: 수익률이 {len(ew_returns)}개로 {vol_window}일 창에 못 미쳐 가용 구간으로 계산")
        latest = _annualized_vol_pct(ew_returns)
        rolling: list[float] = [latest]
    else:
        rolling = [
            _annualized_vol_pct(ew_returns[i - vol_window + 1 : i + 1]) for i in range(vol_window - 1, len(ew_returns))
        ]
        latest = rolling[-1]
    trailing = rolling[-PERCENTILE_LOOKBACK:]
    percentile: float | None
    if len(trailing) < PERCENTILE_MIN_SAMPLES:
        percentile = None
        notes.append(
            f"변동성: 1년 백분위에 필요한 롤링 값이 {len(trailing)}개로 {PERCENTILE_MIN_SAMPLES}개 미만이라 생략, 라벨은 normal로 기록"
        )
    else:
        below = sum(1 for v in trailing if v < latest)
        percentile = _r(below / (len(trailing) - 1) * 100.0)
    return VolatilityRegime(
        window=vol_window,
        realized_vol_annualized=_r(latest),
        percentile_1y=percentile,
        label=_vol_label(percentile),  # type: ignore[arg-type]
    )


def _correlation(
    ew_dates: Sequence[str],
    per_symbol_returns: dict[str, dict[str, float]],
    corr_window: int,
    notes: list[str],
) -> CorrelationSummary | None:
    if len(ew_dates) < corr_window:
        notes.append(f"상관행렬: 유효 거래일이 {len(ew_dates)}일로 {corr_window}일에 못 미쳐 생략")
        return None
    window_dates = list(ew_dates[-corr_window:])
    full = {
        symbol: [series[d] for d in window_dates]
        for symbol, series in per_symbol_returns.items()
        if all(d in series for d in window_dates)
    }
    if len(full) < 2:
        notes.append(f"상관행렬: {corr_window}일 전체 데이터가 있는 종목이 2개 미만이라 생략")
        return None
    symbols = sorted(full)
    pairs: list[float] = []
    undefined = 0
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            rho = _pearson(full[symbols[i]], full[symbols[j]])
            if rho is None:
                undefined += 1
            else:
                pairs.append(rho)
    if undefined:
        notes.append(f"상관행렬: 분산이 0인 종목이 포함된 {undefined}쌍은 제외")
    if not pairs:
        notes.append("상관행렬: 계산 가능한 종목 쌍이 없어 생략")
        return None
    avg = mean(pairs)
    return CorrelationSummary(
        window=corr_window,
        mean_pairwise=_r(avg),
        max_pairwise=_r(max(pairs)),
        min_pairwise=_r(min(pairs)),
        n_symbols=len(symbols),
        label=_corr_label(avg),  # type: ignore[arg-type]
    )


def _sector_momentum(sector_moves: Sequence[SectorMove]) -> list[SectorMomentum]:
    ordered = sorted(sector_moves, key=lambda m: m.change_pct, reverse=True)
    return [SectorMomentum(name=m.name, change_pct=_r(m.change_pct), rank=i + 1) for i, m in enumerate(ordered)]


def _flows(flow_history: Sequence[InvestorFlows], notes: list[str]) -> list[FlowZScore]:
    if not flow_history:
        notes.append("수급: 투자자별 순매수 이력이 없어 z-점수를 생략")
        return []
    out: list[FlowZScore] = []
    for group in FLOW_GROUPS:
        values = [float(getattr(f, group)) for f in flow_history]
        latest = values[-1]
        trailing = values[-(FLOW_LOOKBACK + 1) : -1]
        if len(trailing) < FLOW_MIN_SAMPLES:
            notes.append(f"수급({group}): 직전 이력이 {len(trailing)}일로 {FLOW_MIN_SAMPLES}일 미만이라 z-점수 생략")
            avg = mean(trailing) if trailing else 0.0
            sd = pstdev(trailing) if len(trailing) >= 1 else 0.0
            z: float | None = None
        else:
            avg = mean(trailing)
            sd = pstdev(trailing)
            if sd == 0.0:
                z = None
                notes.append(f"수급({group}): 직전 {len(trailing)}일 표준편차가 0이라 z-점수 생략")
            else:
                z = _r((latest - avg) / sd)
        out.append(FlowZScore(group=group, latest=_r(latest), mean_60d=_r(avg), stdev_60d=_r(sd), z=z))
    return out


def _symbol_structure(symbol: str, bars: Sequence[Bar], meta: dict[str, str]) -> SymbolStructure:
    closes = [float(b["close"]) for b in bars]
    close = closes[-1]

    def ret(periods: int) -> float | None:
        if len(closes) <= periods or closes[-periods - 1] <= 0:
            return None
        return _r((close / closes[-periods - 1] - 1.0) * 100.0)

    def above(window: int) -> bool | None:
        if len(closes) < window:
            return None
        return close > mean(closes[-window:])

    returns = _returns(closes)
    vol20 = _r(_annualized_vol_pct(returns[-20:])) if len(returns) >= 20 else None
    distance: float | None = None
    if len(bars) >= HIGH_LOOKBACK:
        high = max(float(b.get("high", b["close"])) for b in bars[-HIGH_LOOKBACK:])
        if high > 0:
            distance = _r((close / high - 1.0) * 100.0)
    return SymbolStructure(
        symbol=symbol,
        name=meta.get("name", ""),
        sector=meta.get("sector", ""),
        close=_r(close),
        ret_20d=ret(20),
        ret_60d=ret(60),
        ret_120d=ret(120),
        above_sma20=above(20),
        above_sma60=above(60),
        above_sma120=above(120),
        realized_vol_20d=vol20,
        distance_from_high_252d_pct=distance,
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def build_market_structure(
    rows: Sequence[Bar],
    *,
    as_of: str | None = None,
    securities: dict[str, dict[str, str]] | None = None,
    sector_moves: Sequence[SectorMove] = (),
    flow_history: Sequence[InvestorFlows] = (),
    windows: Sequence[int] = (20, 60, 120),
    corr_window: int = 60,
    vol_window: int = 20,
) -> MarketStructureEvidence:
    """Compute the evidence from bars dated <= `as_of` (default: the latest bar date). Never raises on short history."""

    if as_of is None:
        if not rows:
            raise ValueError("build_market_structure needs at least one bar or an explicit as_of")
        as_of = max(str(r["date"]) for r in rows)
    as_of = date.fromisoformat(as_of).isoformat()
    notes: list[str] = []
    bars_by_symbol = _group_bars(rows, as_of)
    if not bars_by_symbol:
        notes.append(f"{as_of} 이전 바가 없어 빈 증거를 반환")
        return MarketStructureEvidence(
            as_of=as_of,
            universe_size=0,
            date_range=(as_of, as_of),
            volatility=VolatilityRegime(window=vol_window, realized_vol_annualized=0.0, percentile_1y=None, label="normal"),
            notes=notes,
        )
    securities = securities or {}
    closes_by_symbol = {s: [float(b["close"]) for b in bars] for s, bars in bars_by_symbol.items()}
    first_date = min(str(bars[0]["date"]) for bars in bars_by_symbol.values())
    last_date = max(str(bars[-1]["date"]) for bars in bars_by_symbol.values())

    breadth = _breadth(closes_by_symbol, windows, notes)
    ew_dates, ew_returns, per_symbol_returns = _equal_weight_returns(bars_by_symbol)
    volatility = _volatility(ew_returns, vol_window, notes)
    correlation = _correlation(ew_dates, per_symbol_returns, corr_window, notes)
    sector_momentum = _sector_momentum(sector_moves)
    flows = _flows(flow_history, notes)
    symbols = [
        _symbol_structure(symbol, bars_by_symbol[symbol], securities.get(symbol, {})) for symbol in sorted(bars_by_symbol)
    ]
    short_high = [s.symbol for s in symbols if s.distance_from_high_252d_pct is None]
    if short_high:
        notes.append(f"252일 고점 거리: 바가 {HIGH_LOOKBACK}개 미만인 {len(short_high)}개 종목은 생략")
    short_120 = [s.symbol for s in symbols if s.ret_120d is None]
    if short_120:
        notes.append(f"120일 수익률: 바가 부족한 {len(short_120)}개 종목은 생략")

    return MarketStructureEvidence(
        as_of=as_of,
        universe_size=len(bars_by_symbol),
        date_range=(first_date, last_date),
        breadth=breadth,
        volatility=volatility,
        correlation=correlation,
        sector_momentum=sector_momentum,
        flows=flows,
        symbols=symbols,
        notes=notes,
    )


def evidence_to_json(evidence: MarketStructureEvidence) -> str:
    return json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False, indent=2)
