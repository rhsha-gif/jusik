"""Derived macro statistics and the growth × inflation quadrant, computed by code only.

Regime method: each growth proxy and each inflation proxy is turned into a
year-over-year rate of change; the latest YoY value is scored as a blended
z-score against its own trailing 24- and 60-month history
(`0.6·z24 + 0.4·z60`, the blend used by public four-quadrant implementations).
The quadrant is the sign pair (growth score, inflation score). This is a
rate-of-change classification, not a level one: "growth up" means growth is
accelerating relative to its recent history. Agents quote the label and the
scores; they do not re-derive them.
"""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Any, Sequence

from quantpilot.services.research_agents.models import MacroPoint, MacroSeries, RegimeCall

_RATE_ROLES = {"policy", "rates", "risk", "credit"}  # levels in %, so YoY % is meaningless


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _points_per_month(cycle: str) -> float:
    return {"D": 21.0, "M": 1.0, "Q": 1.0 / 3.0, "A": 1.0 / 12.0}.get(cycle, 1.0)


def _lag(points: Sequence[MacroPoint], months: float, cycle: str) -> MacroPoint | None:
    """The point ~`months` before the last one, by count (calendar gaps in daily data are tolerated)."""

    offset = int(round(months * _points_per_month(cycle)))
    if offset <= 0 or len(points) <= offset:
        return None
    return points[-1 - offset]


def percentile_rank(values: Sequence[float], target: float) -> float | None:
    if len(values) < 2:
        return None
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    return _round(100.0 * (below + 0.5 * equal) / len(values), 1)


def zscore(values: Sequence[float], target: float) -> float | None:
    if len(values) < 6:
        return None
    sd = pstdev(values)
    if sd == 0:
        return None
    return _round((target - mean(values)) / sd)


def yoy_series(points: Sequence[MacroPoint], cycle: str) -> list[MacroPoint]:
    """Percent change versus the point ~12 months earlier, for level series (CPI, exports, production)."""

    lag = int(round(12 * _points_per_month(cycle)))
    out: list[MacroPoint] = []
    for index in range(lag, len(points)):
        base = points[index - lag].value
        if base == 0:
            continue
        out.append(MacroPoint(time=points[index].time, value=100.0 * (points[index].value / base - 1)))
    return out


def enrich_series(series: MacroSeries) -> MacroSeries:
    """Fill latest / change_3m / yoy_pct / percentile_5y / z-scores from `points`; never raises on short history."""

    points = sorted(series.points, key=lambda p: p.time)
    data = series.model_dump()
    data["points"] = [p.model_dump() for p in points]
    if not points:
        data["note"] = (series.note + " no data").strip()
        return MacroSeries.model_validate(data)
    latest = points[-1]
    data["latest"] = _round(latest.value)
    data["latest_time"] = latest.time
    three = _lag(points, 3, series.cycle)
    data["change_3m"] = _round(latest.value - three.value) if three else None
    if series.role not in _RATE_ROLES:
        twelve = _lag(points, 12, series.cycle)
        data["yoy_pct"] = _round(100.0 * (latest.value / twelve.value - 1), 2) if twelve and twelve.value else None
    window_5y = int(round(60 * _points_per_month(series.cycle)))
    trailing = [p.value for p in points[-window_5y:]]
    data["percentile_5y"] = percentile_rank(trailing, latest.value) if len(trailing) >= 12 else None
    w24 = int(round(24 * _points_per_month(series.cycle)))
    w60 = window_5y
    data["zscore_24m"] = zscore([p.value for p in points[-w24:]], latest.value)
    data["zscore_60m"] = zscore([p.value for p in points[-w60:]], latest.value)
    return MacroSeries.model_validate(data)


def _blended_roc_score(series: MacroSeries, short_months: int, long_months: int, short_weight: float) -> float | None:
    yoy = yoy_series(sorted(series.points, key=lambda p: p.time), series.cycle)
    if len(yoy) < 6:
        return None
    ppm = _points_per_month(series.cycle)
    latest = yoy[-1].value
    z_short = zscore([p.value for p in yoy[-int(round(short_months * ppm)) :]], latest)
    z_long = zscore([p.value for p in yoy[-int(round(long_months * ppm)) :]], latest)
    if z_short is None and z_long is None:
        return None
    if z_short is None:
        return z_long
    if z_long is None:
        return z_short
    return short_weight * z_short + (1 - short_weight) * z_long


def classify_regime(series_by_id: dict[str, MacroSeries], config: dict[str, Any]) -> RegimeCall:
    short_m = int(config.get("zscore_short_months", 24))
    long_m = int(config.get("zscore_long_months", 60))
    weight = float(config.get("short_weight", 0.6))
    method = f"blended YoY z-score {weight:.1f}·z{short_m}m + {1 - weight:.1f}·z{long_m}m; quadrant = sign(growth), sign(inflation)"

    def _score(ids: Sequence[str]) -> tuple[float | None, list[str]]:
        scores: list[float] = []
        used: list[str] = []
        for sid in ids:
            s = series_by_id.get(sid)
            if s is None:
                continue
            value = _blended_roc_score(s, short_m, long_m, weight)
            if value is not None:
                scores.append(value)
                used.append(sid)
        return (_round(mean(scores)) if scores else None), used

    growth, g_used = _score(config.get("growth_ids", []))
    inflation, i_used = _score(config.get("inflation_ids", []))
    if growth is None or inflation is None:
        missing = [name for name, val in (("growth", growth), ("inflation", inflation)) if val is None]
        return RegimeCall(
            quadrant="undetermined",
            growth_score=growth,
            inflation_score=inflation,
            growth_inputs=g_used,
            inflation_inputs=i_used,
            method=method,
            note=f"판정 불가: {', '.join(missing)} 축 입력 없음(키 미발급 또는 시계열 부족)",
        )
    if growth >= 0 and inflation < 0:
        quadrant = "Q1_growth_up_inflation_down"
    elif growth >= 0 and inflation >= 0:
        quadrant = "Q2_growth_up_inflation_up"
    elif growth < 0 and inflation >= 0:
        quadrant = "Q3_growth_down_inflation_up"
    else:
        quadrant = "Q4_growth_down_inflation_down"
    return RegimeCall(
        quadrant=quadrant,
        growth_score=growth,
        inflation_score=inflation,
        growth_inputs=g_used,
        inflation_inputs=i_used,
        method=method,
        note="rate-of-change 분류: 성장/물가의 YoY가 최근 이력 대비 가속(+)인지 감속(−)인지",
    )
