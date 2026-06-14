from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from quantpilot.packages.core.marketdata.types import MarketDataQuality
from quantpilot.packages.core.schemas import Signal
from quantpilot.packages.core.signals.types import MultiFactorScore, RegimeLabel
from quantpilot.packages.core.universe.ranking_types import RankedCandidate


REGIME_WEIGHTS: dict[RegimeLabel, dict[str, float]] = {
    "uptrend": {"momentum": 0.24, "trend": 0.30, "volume": 0.18, "volatility": 0.16, "data_quality": 0.12},
    "pullback": {"momentum": 0.22, "trend": 0.24, "volume": 0.22, "volatility": 0.16, "data_quality": 0.16},
    "range": {"momentum": 0.18, "trend": 0.22, "volume": 0.16, "volatility": 0.22, "data_quality": 0.22},
    "volatile": {"momentum": 0.16, "trend": 0.18, "volume": 0.14, "volatility": 0.30, "data_quality": 0.22},
    "downtrend": {"momentum": 0.14, "trend": 0.18, "volume": 0.12, "volatility": 0.26, "data_quality": 0.30},
    "risk_off": {"momentum": 0.10, "trend": 0.10, "volume": 0.10, "volatility": 0.30, "data_quality": 0.40},
}


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 6)


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _score_from_ratio(ratio: float, *, midpoint: float = 1.0, slope: float = 100.0) -> float:
    return _clamp_score(50.0 + (ratio - midpoint) * slope)


def _base_data_quality_score(
    *,
    market_data_quality: MarketDataQuality,
    ranked_candidate: RankedCandidate | None,
) -> float:
    if not market_data_quality.usable:
        score = 0.0
    elif market_data_quality.degraded:
        score = 60.0
    else:
        score = 100.0
    if ranked_candidate is not None:
        score = min(score, ranked_candidate.score.data_quality)
    return _clamp_score(score)


def _volatility_score_from_bar(bar: Mapping[str, Any] | None, signal: Signal) -> float:
    if bar is not None:
        close = _as_float(bar.get("close"))
        high = _as_float(bar.get("high"))
        low = _as_float(bar.get("low"))
        if close and high is not None and low is not None and close > 0:
            intraday_range = max(0.0, high - low) / close
            return _clamp_score(100.0 - intraday_range * 600.0)
    scores = [score for score in (signal.technical_score, signal.quant_score) if score is not None]
    if scores:
        return _clamp_score(100.0 - max(0.0, 70.0 - sum(scores) / len(scores)))
    return 50.0


def _trend_score_from_bar(bar: Mapping[str, Any] | None, signal: Signal) -> float:
    if bar is not None:
        close = _as_float(bar.get("close"))
        ma20 = _as_float(bar.get("ma20"))
        if close and ma20 and ma20 > 0:
            return _score_from_ratio(close / ma20, slope=250.0)
    if signal.technical_score is not None:
        return _clamp_score(signal.technical_score)
    return _clamp_score(signal.strength * 100.0)


def _momentum_score_from_signal(signal: Signal) -> float:
    if signal.quant_score is not None:
        return _clamp_score(signal.quant_score)
    if signal.technical_score is not None:
        return _clamp_score(signal.technical_score)
    return _clamp_score(signal.strength * 100.0)


def _volume_score_from_bar(bar: Mapping[str, Any] | None) -> float:
    if bar is None:
        return 50.0
    ratio = _as_float(bar.get("volume_ratio"))
    if ratio is not None:
        return _score_from_ratio(ratio, slope=35.0)
    volume = _as_float(bar.get("volume"))
    if volume is None:
        return 50.0
    if volume <= 0:
        return 0.0
    return 50.0


def infer_regime(
    *,
    momentum: float,
    trend: float,
    volatility: float,
    data_quality: float,
) -> RegimeLabel:
    if data_quality < 50:
        return "risk_off"
    if volatility < 45:
        return "volatile"
    if trend < 43 and momentum < 50:
        return "downtrend"
    if trend >= 60 and momentum >= 55:
        return "uptrend"
    if trend >= 55 and momentum < 55:
        return "pullback"
    return "range"


def build_multi_factor_score(
    *,
    signal: Signal,
    bar: Mapping[str, Any] | None = None,
    market_data_quality: MarketDataQuality,
    ranked_candidate: RankedCandidate | None = None,
) -> MultiFactorScore:
    momentum = _momentum_score_from_signal(signal)
    trend = _trend_score_from_bar(bar, signal)
    volume = _volume_score_from_bar(bar)
    volatility = _volatility_score_from_bar(bar, signal)
    data_quality = _base_data_quality_score(
        market_data_quality=market_data_quality,
        ranked_candidate=ranked_candidate,
    )
    regime = infer_regime(
        momentum=momentum,
        trend=trend,
        volatility=volatility,
        data_quality=data_quality,
    )
    weights = REGIME_WEIGHTS[regime]
    final_score = _clamp_score(
        momentum * weights["momentum"]
        + trend * weights["trend"]
        + volume * weights["volume"]
        + volatility * weights["volatility"]
        + data_quality * weights["data_quality"]
    )
    reason_codes = [f"regime_{regime}"]
    if market_data_quality.degraded:
        reason_codes.append("market_data_degraded")
    reason_codes.extend(market_data_quality.reason_codes)
    if ranked_candidate is not None and ranked_candidate.exclusion_reason:
        reason_codes.append(f"candidate_{ranked_candidate.exclusion_reason}")

    return MultiFactorScore(
        symbol=signal.symbol,
        momentum=momentum,
        trend=trend,
        volume=volume,
        volatility=volatility,
        data_quality=data_quality,
        final_score=final_score,
        regime=regime,
        weights=weights,
        reason_codes=list(dict.fromkeys(reason_codes)),
    )
