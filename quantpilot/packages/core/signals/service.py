from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quantpilot.packages.core.schemas import CandidateUniverseItem, Signal, SignalAction, StrategyRecipe, TechnicalIndicatorSnapshot, UserPolicy
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators, fixture_price_history
from quantpilot.packages.core.universe.builder import build_candidate_universe


def default_ohlcv_fixture_path() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "ohlcv.json"


def load_fixture_ohlcv(path: Path | None = None) -> list[dict[str, Any]]:
    fixture_path = path or default_ohlcv_fixture_path()
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def classify_fixture_bar(bar: dict[str, Any]) -> tuple[SignalAction, float, str]:
    if bar.get("blocked", False):
        return SignalAction.blocked, 0.0, "blocked by fixture trading halt"

    close = float(bar["close"])
    ma20 = float(bar["ma20"])
    rsi = float(bar["rsi"])
    volume_ratio = float(bar.get("volume_ratio", 1.0))
    position_weight = float(bar.get("position_weight", 0.0))

    if position_weight > 0 and close <= ma20 * 0.94:
        return SignalAction.exit, 1.0, "risk moving average broke"
    if position_weight > 0 and (rsi >= 72 or close >= ma20 * 1.2):
        return SignalAction.trim, 0.7, "position overheated"
    if position_weight > 0:
        return SignalAction.hold, 0.4, "position remains within risk band"
    if close > ma20 and rsi <= 35 and volume_ratio >= 1.1:
        return SignalAction.buy_ready, 0.8, "pullback recovered with volume"
    if close > ma20 and rsi <= 45:
        return SignalAction.buy_wait, 0.3, "setup is forming but not ready"
    return SignalAction.watch, 0.1, "no actionable setup"


def classify_level2_action(
    policy: UserPolicy,
    candidate: CandidateUniverseItem,
    indicator: TechnicalIndicatorSnapshot,
    *,
    current_weight: float = 0.0,
) -> SignalAction:
    if candidate.block_reason is not None:
        return SignalAction.blocked
    if current_weight > 0 and indicator.close <= indicator.moving_averages["ma20"] * 0.94:
        return SignalAction.exit
    if current_weight > policy.max_position_weight:
        return SignalAction.trim
    if current_weight > 0 and (indicator.rsi >= 72 or indicator.close >= indicator.moving_averages["ma20"] * 1.2):
        return SignalAction.trim
    if current_weight > 0:
        return SignalAction.hold
    if indicator.technical_score >= 68 and indicator.volume_ratio >= 1.05 and indicator.rsi <= 65:
        return SignalAction.buy_ready
    if indicator.technical_score >= 52 and indicator.liquidity_score >= 40:
        return SignalAction.buy_wait
    if not candidate.theme_match:
        return SignalAction.watch
    return SignalAction.watch


def _score_from_fixture(bar: dict[str, Any]) -> tuple[float, float]:
    close = float(bar["close"])
    ma20 = float(bar["ma20"])
    rsi = float(bar["rsi"])
    volume_ratio = float(bar.get("volume_ratio", 1.0))
    trend_score = max(0.0, min(100.0, 50 + (close / ma20 - 1) * 250))
    rsi_score = max(0.0, min(100.0, 100 - abs(rsi - 50) * 1.7))
    volume_score = max(0.0, min(100.0, 50 + (volume_ratio - 1) * 35))
    technical_score = max(0.0, min(100.0, trend_score * 0.45 + rsi_score * 0.35 + volume_score * 0.20))
    quant_score = max(0.0, min(100.0, technical_score * 0.75 + volume_score * 0.25))
    return round(technical_score, 6), round(quant_score, 6)


def _target_weight_hint(policy: UserPolicy | None, action: SignalAction, strength: float, position_weight: float) -> float:
    if policy is None:
        max_weight = 0.15
    else:
        max_weight = policy.max_position_weight
    if action == SignalAction.buy_ready:
        return round(min(max_weight, max(0.01, strength * max_weight)), 6)
    if action == SignalAction.buy_wait:
        return 0.0
    if action == SignalAction.trim:
        return round(max(0.0, min(position_weight * 0.5, max_weight)), 6)
    if action == SignalAction.hold:
        return round(min(position_weight, max_weight), 6)
    if action in {SignalAction.exit, SignalAction.blocked}:
        return 0.0
    return round(position_weight, 6)


def _reason_codes(action: SignalAction, reason: str) -> list[str]:
    if action == SignalAction.blocked:
        return ["blocked", reason.replace(" ", "_")]
    if action == SignalAction.exit:
        return ["risk_exit", "moving_average_break"]
    if action == SignalAction.trim:
        return ["rebalance_trim", "overheat_or_policy_cap"]
    if action == SignalAction.hold:
        return ["position_hold", "within_risk_band"]
    if action == SignalAction.buy_ready:
        return ["setup_ready", "trend_volume_pullback"]
    if action == SignalAction.buy_wait:
        return ["setup_forming", "confirmation_needed"]
    return ["watchlist", "no_actionable_setup"]


def _signal_from_fixture_bar(
    recipe: StrategyRecipe,
    bar: dict[str, Any],
    *,
    policy: UserPolicy | None,
    signal_date: date,
) -> Signal:
    action, strength, reason = classify_fixture_bar(bar)
    technical_score, quant_score = _score_from_fixture(bar)
    close = float(bar["close"])
    position_weight = float(bar.get("position_weight", 0.0))
    return Signal(
        strategy_id=recipe.strategy_id,
        recipe_version=recipe.version,
        symbol=str(bar["symbol"]),
        ticker=str(bar["symbol"]),
        signal_date=signal_date,
        action=action,
        strength=strength,
        technical_score=technical_score,
        quant_score=quant_score,
        target_weight_hint=_target_weight_hint(policy, action, strength, position_weight),
        stop_price_hint=round(close * 0.92, 4) if action in {SignalAction.buy_ready, SignalAction.buy_wait, SignalAction.hold} else None,
        take_profit_hint=round(close * 1.18, 4) if action in {SignalAction.buy_ready, SignalAction.buy_wait, SignalAction.hold, SignalAction.trim} else None,
        valid_until=signal_date + fixture_signal_validity(),
        policy_version=policy.version if policy else None,
        reason_codes=_reason_codes(action, reason),
        reason=reason,
        source="fixture_level_1_2_signal_engine",
    )


def load_signal_date() -> date:
    return date.fromisoformat(str(fixture_price_history()[-1]["date"]))


def fixture_signal_validity() -> timedelta:
    return timedelta(days=5)


def _legacy_fixture_bars(bars: list[dict[str, Any]]) -> bool:
    return bool(bars) and "ma20" in bars[0] and "rsi" in bars[0]


def generate_signals(
    recipe: StrategyRecipe,
    bars: list[dict[str, Any]],
    *,
    policy: UserPolicy | None = None,
    securities: list[dict[str, Any]] | None = None,
) -> list[Signal]:
    signals: list[Signal] = []
    signal_date = load_signal_date()
    if _legacy_fixture_bars(bars):
        for bar in bars:
            signals.append(_signal_from_fixture_bar(recipe, bar, policy=policy, signal_date=signal_date))
        return signals

    if policy is None:
        policy = UserPolicy()
    candidates = {candidate.ticker: candidate for candidate in build_candidate_universe(policy, securities)}
    for ticker, candidate in candidates.items():
        indicator = calculate_technical_indicators(bars, ticker=ticker, signal_date=signal_date)
        current_weight = 0.0
        action = classify_level2_action(policy, candidate, indicator, current_weight=current_weight)
        strength = round(indicator.quant_score / 100, 6)
        close = indicator.close
        reason = f"technical_score={indicator.technical_score:.1f}; quant_score={indicator.momentum_score:.1f}"
        signals.append(
            Signal(
                strategy_id=recipe.strategy_id,
                recipe_version=recipe.version,
                symbol=ticker,
                ticker=ticker,
                signal_date=indicator.signal_date,
                action=action,
                strength=strength,
                technical_score=indicator.technical_score,
                quant_score=indicator.momentum_score,
                target_weight_hint=_target_weight_hint(policy, action, strength, current_weight),
                stop_price_hint=round(close * 0.92, 4) if action in {SignalAction.buy_ready, SignalAction.buy_wait, SignalAction.hold} else None,
                take_profit_hint=round(close * 1.18, 4) if action in {SignalAction.buy_ready, SignalAction.buy_wait, SignalAction.hold, SignalAction.trim} else None,
                valid_until=indicator.signal_date + fixture_signal_validity(),
                policy_version=policy.version,
                reason_codes=_reason_codes(action, reason),
                reason=reason,
                source="fixture_level_1_2_signal_engine",
            )
        )
    return signals
