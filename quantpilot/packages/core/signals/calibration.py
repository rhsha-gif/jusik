from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from quantpilot.packages.core.marketdata.types import MarketDataQuality, ProviderStatus
from quantpilot.packages.core.normalization import symbol_key
from quantpilot.packages.core.schemas import DataMode, Signal, SignalAction, UserPolicy
from quantpilot.packages.core.signals.multifactor import build_multi_factor_score
from quantpilot.packages.core.signals.types import (
    CalibratedSignal,
    CalibratedSignalSet,
    CalibrationStatus,
    CalibrationGuardResult,
    EnsembleVote,
    ExpectedReturnRiskProxy,
    MultiFactorScore,
)
from quantpilot.packages.core.universe.builder import build_candidate_universe
from quantpilot.packages.core.universe.ranking import CandidateRankingEngine
from quantpilot.packages.core.universe.ranking_types import RankedCandidate


HORIZON_MULTIPLIERS = {
    "daily": 1.0,
    "short": 1.0,
    "weekly": 1.35,
    "swing": 1.35,
    "monthly": 1.8,
    "position": 1.8,
}


def _symbol(value: str) -> str:
    return symbol_key(value)


def _clamp_unit(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def _unique_codes(codes: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(code for code in codes if code))


def _provider_status_dump(provider_status: Mapping[str, ProviderStatus]) -> dict[str, dict[str, Any]]:
    return {
        channel: status.model_dump(mode="json")
        for channel, status in provider_status.items()
    }


def _latest_bar_by_symbol(bars: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    latest_dates: dict[str, str] = {}
    for bar in bars:
        symbol = _symbol(str(bar.get("symbol", bar.get("ticker", ""))))
        if not symbol:
            continue
        bar_date = str(bar.get("date", ""))
        if symbol not in latest or not bar_date or bar_date >= latest_dates.get(symbol, ""):
            latest[symbol] = bar
            latest_dates[symbol] = bar_date
    return latest


def _ranked_candidates_by_symbol(
    *,
    policy: UserPolicy | None,
    securities: list[dict[str, Any]] | None,
    ranked_candidates: list[RankedCandidate] | None,
) -> dict[str, RankedCandidate]:
    if ranked_candidates is None and policy is not None:
        candidates = build_candidate_universe(policy, securities)
        ranked_candidates = CandidateRankingEngine().rank(
            policy=policy,
            candidates=candidates,
            securities=securities,
        )
    return {
        _symbol(item.candidate.ticker): item
        for item in ranked_candidates or []
    }


def calculate_signal_decay(signal: Signal, *, as_of: date | None = None) -> float:
    if signal.valid_until is None:
        return 1.0
    observed_date = as_of or signal.signal_date
    if observed_date > signal.valid_until:
        return 0.0
    total_days = max(1, (signal.valid_until - signal.signal_date).days)
    remaining_days = max(0, (signal.valid_until - observed_date).days)
    return _clamp_unit(remaining_days / total_days)


def _score_action(score: MultiFactorScore) -> SignalAction:
    if score.regime == "risk_off":
        return SignalAction.blocked
    if score.final_score >= 72 and score.regime not in {"volatile", "downtrend"}:
        return SignalAction.buy_ready
    if score.final_score >= 58 and score.regime != "downtrend":
        return SignalAction.buy_wait
    return SignalAction.watch


def _regime_action(base_action: SignalAction, score: MultiFactorScore) -> SignalAction:
    if score.regime == "risk_off":
        return SignalAction.blocked
    if base_action in {SignalAction.exit, SignalAction.trim, SignalAction.blocked}:
        return base_action
    if score.regime in {"volatile", "downtrend"}:
        return SignalAction.watch
    return _score_action(score)


def build_ensemble_vote(signal: Signal, score: MultiFactorScore) -> EnsembleVote:
    votes: dict[str, float] = {}
    for action, weight in (
        (signal.action, 0.40),
        (_score_action(score), 0.35),
        (_regime_action(signal.action, score), 0.25),
    ):
        votes[action.value] = round(votes.get(action.value, 0.0) + weight, 6)
    selected_value = max(votes.items(), key=lambda item: (item[1], item[0]))[0]
    reason_codes = ["ensemble_vote"]
    if len(votes) > 1:
        reason_codes.append("ensemble_disagreement")
    return EnsembleVote(
        symbol=signal.symbol,
        votes=votes,
        selected_action=SignalAction(selected_value),
        reason_codes=reason_codes,
    )


def _calibrated_confidence(
    *,
    signal: Signal,
    score: MultiFactorScore,
    vote: EnsembleVote,
    decay: float,
    market_data_quality: MarketDataQuality,
) -> float:
    consensus = max(vote.votes.values()) if vote.votes else 0.0
    confidence = (score.final_score / 100.0) * 0.55 + signal.strength * 0.25 + consensus * 0.20
    if market_data_quality.degraded:
        confidence *= 0.75
    if not market_data_quality.usable:
        confidence = 0.0
    if signal.action == SignalAction.blocked:
        confidence = 0.0
    return _clamp_unit(confidence * decay)


def apply_calibration_guard(
    *,
    signal: Signal,
    score: MultiFactorScore,
    confidence: float,
    decay: float,
    market_data_quality: MarketDataQuality,
) -> CalibrationGuardResult:
    reason_codes: list[str] = []
    if not market_data_quality.usable:
        reason_codes.append("market_data_unusable")
    if "provider_unavailable" in market_data_quality.reason_codes:
        reason_codes.append("provider_unavailable")
    if "provider_stale" in market_data_quality.reason_codes:
        reason_codes.append("provider_stale")
    if decay <= 0:
        reason_codes.append("signal_expired")
    if signal.action in {SignalAction.buy_ready, SignalAction.buy_wait} and score.final_score < 58:
        reason_codes.append("score_below_buy_threshold")
    if signal.action == SignalAction.buy_ready and confidence < 0.45:
        reason_codes.append("confidence_below_buy_ready_threshold")

    status: CalibrationStatus
    if "signal_expired" in reason_codes:
        status = "expired"
    elif not market_data_quality.usable or "provider_unavailable" in reason_codes:
        status = "unavailable"
    elif signal.action == SignalAction.blocked:
        status = "blocked"
    elif reason_codes:
        status = "guarded"
    else:
        status = "available"

    action_allowed = status == "available"
    return CalibrationGuardResult(
        passed=action_allowed,
        status=status,
        action_allowed=action_allowed,
        reason_codes=_unique_codes(reason_codes),
    )


def _compatible_action(
    *,
    base_action: SignalAction,
    voted_action: SignalAction,
    guard: CalibrationGuardResult,
) -> SignalAction:
    if base_action in {SignalAction.blocked, SignalAction.exit, SignalAction.trim}:
        return base_action
    if "market_data_unusable" in guard.reason_codes or "provider_unavailable" in guard.reason_codes:
        return SignalAction.blocked
    if "signal_expired" in guard.reason_codes:
        return SignalAction.watch
    if base_action == SignalAction.hold:
        return SignalAction.hold if voted_action in {SignalAction.buy_ready, SignalAction.buy_wait} else voted_action
    if base_action == SignalAction.buy_wait and voted_action == SignalAction.buy_ready:
        return SignalAction.buy_wait
    if base_action == SignalAction.watch and voted_action in {SignalAction.buy_ready, SignalAction.buy_wait}:
        return SignalAction.watch
    if base_action == SignalAction.buy_ready and not guard.action_allowed:
        return SignalAction.buy_wait if "confidence_below_buy_ready_threshold" in guard.reason_codes else SignalAction.watch
    return voted_action


def build_expected_return_risk_proxy(
    *,
    signal: Signal,
    action: SignalAction,
    score: MultiFactorScore,
    confidence: float,
    horizon: str | None,
    data_mode: DataMode,
) -> ExpectedReturnRiskProxy:
    normalized_horizon = (horizon or "daily").strip().lower() or "daily"
    horizon_multiplier = HORIZON_MULTIPLIERS.get(normalized_horizon, 1.0)
    edge = max(-1.0, min(1.0, (score.final_score - 50.0) / 50.0))
    action_multiplier = {
        SignalAction.buy_ready: 1.0,
        SignalAction.buy_wait: 0.35,
        SignalAction.hold: 0.10,
        SignalAction.watch: 0.0,
        SignalAction.trim: -0.15,
        SignalAction.exit: -0.50,
        SignalAction.blocked: 0.0,
    }[action]
    expected_return = round(edge * confidence * action_multiplier * horizon_multiplier, 6)
    risk = round(
        max(
            0.01,
            ((100.0 - score.volatility) / 100.0) * 0.65
            + ((100.0 - score.data_quality) / 100.0) * 0.35,
        ),
        6,
    )
    return ExpectedReturnRiskProxy(
        symbol=signal.symbol,
        horizon=normalized_horizon,
        expected_return=expected_return,
        risk=risk,
        risk_adjusted_return=round(expected_return / risk, 6) if risk else 0.0,
        confidence=confidence,
        data_mode=data_mode,
        metadata={
            "signal_id": signal.signal_id,
            "base_action": signal.action.value,
            "calibrated_action": action.value,
            "horizon_multiplier": horizon_multiplier,
        },
    )


def calibrate_signal(
    *,
    signal: Signal,
    score: MultiFactorScore,
    market_data_quality: MarketDataQuality,
    horizon: str | None = None,
    as_of: date | None = None,
) -> CalibratedSignal:
    decay = calculate_signal_decay(signal, as_of=as_of)
    vote = build_ensemble_vote(signal, score)
    confidence = _calibrated_confidence(
        signal=signal,
        score=score,
        vote=vote,
        decay=decay,
        market_data_quality=market_data_quality,
    )
    guard = apply_calibration_guard(
        signal=signal,
        score=score,
        confidence=confidence,
        decay=decay,
        market_data_quality=market_data_quality,
    )
    action = _compatible_action(
        base_action=signal.action,
        voted_action=vote.selected_action,
        guard=guard,
    )
    proxy = build_expected_return_risk_proxy(
        signal=signal,
        action=action,
        score=score,
        confidence=confidence,
        horizon=horizon,
        data_mode=market_data_quality.data_mode,
    )
    reason_codes = _unique_codes([*score.reason_codes, *vote.reason_codes, *guard.reason_codes])
    return CalibratedSignal(
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        base_action=signal.action,
        calibrated_action=action,
        strength=signal.strength,
        confidence=confidence,
        decay=decay,
        multi_factor_score=score,
        expected_return_risk=proxy,
        ensemble_vote=vote,
        guard=guard,
        target_weight_hint=signal.target_weight_hint,
        reason_codes=reason_codes,
    )


def calibrate_signal_set(
    *,
    signals: list[Signal],
    bars: Sequence[Mapping[str, Any]],
    provider_status: Mapping[str, ProviderStatus],
    market_data_quality: MarketDataQuality,
    policy: UserPolicy | None = None,
    securities: list[dict[str, Any]] | None = None,
    ranked_candidates: list[RankedCandidate] | None = None,
    horizon: str | None = None,
    as_of: date | None = None,
) -> CalibratedSignalSet:
    bars_by_symbol = _latest_bar_by_symbol(bars)
    ranked_by_symbol = _ranked_candidates_by_symbol(
        policy=policy,
        securities=securities,
        ranked_candidates=ranked_candidates,
    )
    calibrated: list[CalibratedSignal] = []
    for signal in signals:
        symbol = _symbol(signal.symbol)
        score = build_multi_factor_score(
            signal=signal,
            bar=bars_by_symbol.get(symbol),
            market_data_quality=market_data_quality,
            ranked_candidate=ranked_by_symbol.get(symbol),
        )
        calibrated.append(
            calibrate_signal(
                signal=signal,
                score=score,
                market_data_quality=market_data_quality,
                horizon=horizon,
                as_of=as_of,
            )
        )
    return CalibratedSignalSet(
        signals=calibrated,
        provider_status=_provider_status_dump(provider_status),
        data_quality=market_data_quality.model_dump(mode="json"),
    )
