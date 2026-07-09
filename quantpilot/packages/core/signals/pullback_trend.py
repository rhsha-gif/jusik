"""Pure pullback-trend decision contract.

QP-110 (Claude Code) implements the functions in this module. This contract is
intentionally isolated from repositories, brokers, order plans, and APIs.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field, model_validator

from quantpilot.packages.core.schemas import HarnessModel, PullbackTrendDecisionRules, SignalAction


class PullbackTrendParameters(PullbackTrendDecisionRules):
    """Pure-engine view of the canonical typed strategy rules."""


class PullbackBar(HarnessModel):
    symbol: str
    session_date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def ohlc_is_consistent(self) -> "PullbackBar":
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be at least open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be at most open, close, and high")
        return self


class PullbackSignalInput(HarnessModel):
    strategy_id: str
    recipe_version: str
    symbol: str
    signal_date: date
    bars: list[PullbackBar] = Field(min_length=2)
    current_weight: float = Field(default=0.0, ge=0, le=1)
    max_position_weight: float = Field(default=0.15, gt=0, le=1)
    candidate_eligible: bool = True
    candidate_block_reason: str | None = None
    data_usable: bool = True
    multifactor_score: float = Field(ge=0, le=100)
    quote_price: float = Field(gt=0)
    quote_as_of: datetime
    evaluated_at: datetime


class PullbackIndicatorSnapshot(HarnessModel):
    symbol: str
    signal_date: date
    completed_sessions: int = Field(ge=1)
    close: float = Field(gt=0)
    sma20: float = Field(gt=0)
    sma120: float = Field(gt=0)
    prior_rsi14: float = Field(ge=0, le=100)
    rsi14: float = Field(ge=0, le=100)
    atr14: float = Field(ge=0)
    volume_ratio20: float = Field(ge=0)


class PullbackSignalDecision(HarnessModel):
    strategy_id: str
    recipe_version: str
    symbol: str
    signal_date: date
    action: SignalAction
    strength: float = Field(ge=0, le=1)
    current_weight: float = Field(ge=0, le=1)
    target_weight_hint: float = Field(ge=0, le=1)
    reference_price: float = Field(gt=0)
    quote_price: float = Field(gt=0)
    quote_age_seconds: float
    indicators: PullbackIndicatorSnapshot | None = None
    reason_codes: list[str] = Field(default_factory=list)
    reason: str


def _completed_bars(request: PullbackSignalInput) -> list[PullbackBar]:
    completed = [bar for bar in request.bars if bar.session_date <= request.signal_date]
    completed.sort(key=lambda bar: bar.session_date)
    return completed


def _required_sessions(parameters: PullbackTrendParameters) -> int:
    return max(
        parameters.trend_window,
        parameters.risk_window,
        parameters.rsi_window + 2,
        parameters.atr_window + 1,
        parameters.volume_window + 1,
    )


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0.0:
        return 100.0 if average_gain > 0.0 else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def _wilder_rsi_pair(closes: list[float], window: int) -> tuple[float, float]:
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:window]) / window
    average_loss = sum(losses[:window]) / window
    rsi = _rsi_value(average_gain, average_loss)
    prior_rsi = rsi
    for index in range(window, len(changes)):
        prior_rsi = rsi
        average_gain = (average_gain * (window - 1) + gains[index]) / window
        average_loss = (average_loss * (window - 1) + losses[index]) / window
        rsi = _rsi_value(average_gain, average_loss)
    return prior_rsi, rsi


def _wilder_atr(bars: list[PullbackBar], window: int) -> float:
    true_ranges = [bars[0].high - bars[0].low]
    for index in range(1, len(bars)):
        prior_close = bars[index - 1].close
        bar = bars[index]
        true_ranges.append(
            max(bar.high - bar.low, abs(bar.high - prior_close), abs(bar.low - prior_close))
        )
    atr = sum(true_ranges[:window]) / window
    for true_range in true_ranges[window:]:
        atr = (atr * (window - 1) + true_range) / window
    return atr


def build_pullback_indicators(
    request: PullbackSignalInput,
    parameters: PullbackTrendParameters | None = None,
) -> PullbackIndicatorSnapshot:
    """Build a no-lookahead indicator snapshot as of ``request.signal_date``."""

    params = parameters or PullbackTrendParameters()
    bars = _completed_bars(request)
    required = _required_sessions(params)
    if len(bars) < required:
        raise ValueError(f"insufficient completed sessions: {len(bars)} < {required}")

    closes = [bar.close for bar in bars]
    sma20 = sum(closes[-params.risk_window :]) / params.risk_window
    sma120 = sum(closes[-params.trend_window :]) / params.trend_window
    prior_rsi14, rsi14 = _wilder_rsi_pair(closes, params.rsi_window)
    atr14 = _wilder_atr(bars, params.atr_window)

    prior_volumes = [bar.volume for bar in bars[-(params.volume_window + 1) : -1]]
    mean_prior_volume = sum(prior_volumes) / params.volume_window
    if mean_prior_volume <= 0.0:
        raise ValueError("prior-session volume history is zero; volume ratio is undefined")
    volume_ratio20 = bars[-1].volume / mean_prior_volume

    return PullbackIndicatorSnapshot(
        symbol=request.symbol,
        signal_date=request.signal_date,
        completed_sessions=len(bars),
        close=round(closes[-1], 6),
        sma20=round(sma20, 6),
        sma120=round(sma120, 6),
        prior_rsi14=round(prior_rsi14, 6),
        rsi14=round(rsi14, 6),
        atr14=round(atr14, 6),
        volume_ratio20=round(volume_ratio20, 6),
    )


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _decision(
    request: PullbackSignalInput,
    *,
    action: SignalAction,
    strength: float,
    target_weight_hint: float,
    reference_price: float,
    quote_age_seconds: float,
    indicators: PullbackIndicatorSnapshot | None,
    reason_codes: list[str],
    reason: str,
) -> PullbackSignalDecision:
    return PullbackSignalDecision(
        strategy_id=request.strategy_id,
        recipe_version=request.recipe_version,
        symbol=request.symbol,
        signal_date=request.signal_date,
        action=action,
        strength=round(strength, 6),
        current_weight=request.current_weight,
        target_weight_hint=round(target_weight_hint, 6),
        reference_price=reference_price,
        quote_price=request.quote_price,
        quote_age_seconds=round(quote_age_seconds, 6),
        indicators=indicators,
        reason_codes=reason_codes,
        reason=reason,
    )


def evaluate_pullback_signal(
    request: PullbackSignalInput,
    parameters: PullbackTrendParameters | None = None,
) -> PullbackSignalDecision:
    """Return a deterministic broker-free pullback strategy decision."""

    params = parameters or PullbackTrendParameters()
    completed = _completed_bars(request)
    reference_price = completed[-1].close if completed else request.quote_price

    timestamps_aware = _is_timezone_aware(request.quote_as_of) and _is_timezone_aware(
        request.evaluated_at
    )
    quote_age_seconds = (
        (request.evaluated_at - request.quote_as_of).total_seconds() if timestamps_aware else 0.0
    )

    blocked_codes: list[str] = []
    if not request.candidate_eligible:
        blocked_codes.append("candidate_ineligible")
        if request.candidate_block_reason:
            blocked_codes.append(request.candidate_block_reason)
    if not request.data_usable:
        blocked_codes.append("data_unusable")
    if len(completed) < _required_sessions(params):
        blocked_codes.append("insufficient_history")
    if not timestamps_aware:
        blocked_codes.append("quote_timestamp_naive")
    elif quote_age_seconds < 0:
        blocked_codes.append("quote_future")
    elif quote_age_seconds > params.max_quote_age_seconds:
        blocked_codes.append("quote_stale")

    if blocked_codes:
        return _decision(
            request,
            action=SignalAction.blocked,
            strength=0.0,
            target_weight_hint=request.current_weight,
            reference_price=reference_price,
            quote_age_seconds=quote_age_seconds,
            indicators=None,
            reason_codes=blocked_codes,
            reason="fail-closed: " + ", ".join(blocked_codes),
        )

    try:
        indicators = build_pullback_indicators(request, params)
    except ValueError as error:
        return _decision(
            request,
            action=SignalAction.blocked,
            strength=0.0,
            target_weight_hint=request.current_weight,
            reference_price=reference_price,
            quote_age_seconds=quote_age_seconds,
            indicators=None,
            reason_codes=["indicator_failure"],
            reason=f"fail-closed: indicator computation failed ({error})",
        )

    if request.current_weight > 0:
        if indicators.close <= indicators.sma20 * params.risk_ma_ratio:
            return _decision(
                request,
                action=SignalAction.exit,
                strength=1.0,
                target_weight_hint=0.0,
                reference_price=reference_price,
                quote_age_seconds=quote_age_seconds,
                indicators=indicators,
                reason_codes=["technical_exit_close_below_sma20_band"],
                reason="close is at or below the SMA20 protective band; full exit",
            )
        overheat_codes: list[str] = []
        if indicators.rsi14 >= params.overheat_rsi:
            overheat_codes.append("overheat_rsi")
        if indicators.close >= indicators.sma20 * params.overheat_ma_ratio:
            overheat_codes.append("overheat_extension_above_sma20")
        if overheat_codes:
            return _decision(
                request,
                action=SignalAction.trim,
                strength=params.trim_fraction,
                target_weight_hint=request.current_weight * (1.0 - params.trim_fraction),
                reference_price=reference_price,
                quote_age_seconds=quote_age_seconds,
                indicators=indicators,
                reason_codes=overheat_codes,
                reason="overheat condition met; trim position by 50%",
            )
        return _decision(
            request,
            action=SignalAction.hold,
            strength=0.0,
            target_weight_hint=request.current_weight,
            reference_price=reference_price,
            quote_age_seconds=quote_age_seconds,
            indicators=indicators,
            reason_codes=["position_within_bands"],
            reason="existing position remains within protective and overheat bands",
        )

    uptrend = indicators.close > indicators.sma120
    confirmations = {
        "rsi_pullback_cross": indicators.prior_rsi14 < params.oversold_rsi
        and indicators.rsi14 >= params.oversold_rsi,
        "volume_confirmation": indicators.volume_ratio20 >= params.min_volume_ratio,
        "multifactor_score": request.multifactor_score >= params.min_multifactor_score,
        "quote_premium": request.quote_price
        <= indicators.close * (1.0 + params.max_quote_premium),
    }
    missing = [name for name, satisfied in confirmations.items() if not satisfied]

    if uptrend and not missing:
        return _decision(
            request,
            action=SignalAction.buy_ready,
            strength=1.0,
            target_weight_hint=request.max_position_weight,
            reference_price=reference_price,
            quote_age_seconds=quote_age_seconds,
            indicators=indicators,
            reason_codes=["entry_confirmed"],
            reason="uptrend pullback entry fully confirmed",
        )
    if uptrend:
        return _decision(
            request,
            action=SignalAction.buy_wait,
            strength=(len(confirmations) - len(missing)) / len(confirmations),
            target_weight_hint=request.current_weight,
            reference_price=reference_price,
            quote_age_seconds=quote_age_seconds,
            indicators=indicators,
            reason_codes=[f"awaiting_{name}" for name in missing],
            reason="uptrend intact; pullback confirmation incomplete: " + ", ".join(missing),
        )
    return _decision(
        request,
        action=SignalAction.watch,
        strength=0.0,
        target_weight_hint=request.current_weight,
        reference_price=reference_price,
        quote_age_seconds=quote_age_seconds,
        indicators=indicators,
        reason_codes=["no_uptrend"],
        reason="close is not above SMA120; keep watching",
    )
