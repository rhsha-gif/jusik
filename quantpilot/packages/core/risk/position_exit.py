"""Pure protective-position decision contract for QP-120."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from quantpilot.packages.core.schemas import HarnessModel, SignalAction


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
    protective_stop: float = Field(gt=0)
    quantity_held: float = Field(gt=0)
    quantity_to_exit: float = Field(ge=0)
    exit_fraction: float = Field(ge=0, le=1)
    current_price: float = Field(gt=0)
    quote_age_seconds: float
    reason_codes: list[str] = Field(default_factory=list)
    reason: str


def evaluate_position_risk(
    request: PositionRiskInput,
    parameters: PositionRiskParameters | None = None,
) -> PositionRiskDecision:
    """Return a deterministic risk-reducing decision without creating an order."""

    raise NotImplementedError("QP-120 implementation pending")
