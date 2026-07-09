"""Pure protective-position decision contract for QP-120."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from quantpilot.packages.core.schemas import HarnessModel, SignalAction


_ROUND_DECIMALS = 6


class PositionRiskParameters(HarnessModel):
    hard_stop_loss_fraction: float = Field(default=0.08, gt=0, lt=1)
    atr_multiplier: float = Field(default=2.0, gt=0)
    max_quote_age_seconds: int = Field(default=30, gt=0)
    risk_ma_ratio: float = Field(default=0.94, gt=0, le=1)
    overheat_rsi: float = Field(default=72.0, ge=0, le=100)
    overheat_ma_ratio: float = Field(default=1.20, ge=1)
    trim_fraction: float = Field(default=0.50, gt=0, lt=1)


class PositionRiskInput(HarnessModel):
    strategy_id: str
    strategy_version: str
    symbol: str
    quantity: float = Field(gt=0)
    average_entry_price: float = Field(gt=0)
    current_price: float = Field(gt=0)
    completed_close: float = Field(gt=0)
    atr14: float = Field(ge=0)
    sma20: float = Field(gt=0)
    rsi14: float = Field(ge=0, le=100)
    quote_as_of: datetime
    evaluated_at: datetime


class PositionRiskDecision(HarnessModel):
    strategy_id: str
    strategy_version: str
    symbol: str
    action: SignalAction
    fixed_fraction_stop: float = Field(gt=0)
    atr_stop: float
    protective_stop: float = Field(gt=0)
    technical_exit_level: float = Field(gt=0)
    overheat_price_level: float = Field(gt=0)
    quantity_held: float = Field(gt=0)
    quantity_to_exit: float = Field(ge=0)
    exit_fraction: float = Field(ge=0, le=1)
    current_price: float = Field(gt=0)
    completed_close: float = Field(gt=0)
    quote_age_seconds: float
    reason_codes: list[str] = Field(default_factory=list)
    reason: str


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def evaluate_position_risk(
    request: PositionRiskInput,
    parameters: PositionRiskParameters | None = None,
) -> PositionRiskDecision:
    """Return a deterministic risk-reducing decision without creating an order."""

    params = parameters or PositionRiskParameters()
    fixed_fraction_stop_raw = request.average_entry_price * (
        1 - params.hard_stop_loss_fraction
    )
    atr_stop_raw = request.average_entry_price - params.atr_multiplier * request.atr14
    protective_stop_raw = max(fixed_fraction_stop_raw, atr_stop_raw)
    technical_exit_level_raw = request.sma20 * params.risk_ma_ratio
    overheat_price_level_raw = request.sma20 * params.overheat_ma_ratio

    fixed_fraction_stop = round(fixed_fraction_stop_raw, _ROUND_DECIMALS)
    atr_stop = round(atr_stop_raw, _ROUND_DECIMALS)
    protective_stop = round(protective_stop_raw, _ROUND_DECIMALS)
    technical_exit_level = round(technical_exit_level_raw, _ROUND_DECIMALS)
    overheat_price_level = round(overheat_price_level_raw, _ROUND_DECIMALS)

    def decision(
        action: SignalAction,
        *,
        exit_fraction: float,
        quote_age_seconds: float,
        reason_codes: list[str],
        reason: str,
    ) -> PositionRiskDecision:
        return PositionRiskDecision(
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            symbol=request.symbol,
            action=action,
            fixed_fraction_stop=fixed_fraction_stop,
            atr_stop=atr_stop,
            protective_stop=protective_stop,
            technical_exit_level=technical_exit_level,
            overheat_price_level=overheat_price_level,
            quantity_held=request.quantity,
            quantity_to_exit=round(request.quantity * exit_fraction, _ROUND_DECIMALS),
            exit_fraction=exit_fraction,
            current_price=request.current_price,
            completed_close=request.completed_close,
            quote_age_seconds=round(quote_age_seconds, _ROUND_DECIMALS),
            reason_codes=reason_codes,
            reason=reason,
        )

    if not (_is_aware(request.quote_as_of) and _is_aware(request.evaluated_at)):
        return decision(
            SignalAction.blocked,
            exit_fraction=0.0,
            quote_age_seconds=0.0,
            reason_codes=["quote_timestamp_naive"],
            reason="quote and evaluation timestamps must be timezone-aware",
        )

    quote_age_seconds = (request.evaluated_at - request.quote_as_of).total_seconds()
    if quote_age_seconds < 0:
        return decision(
            SignalAction.blocked,
            exit_fraction=0.0,
            quote_age_seconds=quote_age_seconds,
            reason_codes=["quote_future"],
            reason="quote timestamp is later than the evaluation time",
        )
    if quote_age_seconds > params.max_quote_age_seconds:
        return decision(
            SignalAction.blocked,
            exit_fraction=0.0,
            quote_age_seconds=quote_age_seconds,
            reason_codes=["quote_stale"],
            reason="quote age exceeds the protective-risk freshness window",
        )

    if request.current_price <= protective_stop_raw:
        selected_component = (
            "fixed_fraction_stop_selected"
            if fixed_fraction_stop_raw >= atr_stop_raw
            else "atr_stop_selected"
        )
        return decision(
            SignalAction.exit,
            exit_fraction=1.0,
            quote_age_seconds=quote_age_seconds,
            reason_codes=["protective_stop_triggered", selected_component],
            reason="current quote is at or below the protective stop",
        )

    if request.completed_close <= technical_exit_level_raw:
        return decision(
            SignalAction.exit,
            exit_fraction=1.0,
            quote_age_seconds=quote_age_seconds,
            reason_codes=["technical_exit_close_below_sma20_band"],
            reason="completed close is at or below the SMA20 protective band",
        )

    overheat_codes: list[str] = []
    if request.rsi14 >= params.overheat_rsi:
        overheat_codes.append("overheat_rsi")
    if request.completed_close >= overheat_price_level_raw:
        overheat_codes.append("overheat_extension_above_sma20")
    if overheat_codes:
        return decision(
            SignalAction.trim,
            exit_fraction=params.trim_fraction,
            quote_age_seconds=quote_age_seconds,
            reason_codes=overheat_codes,
            reason="completed position state is overheated; trim by the configured fraction",
        )

    return decision(
        SignalAction.hold,
        exit_fraction=0.0,
        quote_age_seconds=quote_age_seconds,
        reason_codes=["position_within_bands"],
        reason="no protective, technical-exit, or overheat trigger fired",
    )
