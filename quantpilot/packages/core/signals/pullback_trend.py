"""Pure pullback-trend decision contract.

QP-110 (Claude Code) implements the functions in this module. This contract is
intentionally isolated from repositories, brokers, order plans, and APIs.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field, model_validator

from quantpilot.packages.core.schemas import HarnessModel, SignalAction


class PullbackTrendParameters(HarnessModel):
    trend_window: int = Field(default=120, ge=2)
    risk_window: int = Field(default=20, ge=2)
    rsi_window: int = Field(default=14, ge=2)
    atr_window: int = Field(default=14, ge=2)
    volume_window: int = Field(default=20, ge=2)
    oversold_rsi: float = Field(default=35.0, ge=0, le=100)
    overheat_rsi: float = Field(default=72.0, ge=0, le=100)
    min_volume_ratio: float = Field(default=1.05, gt=0)
    min_multifactor_score: float = Field(default=68.0, ge=0, le=100)
    max_quote_premium: float = Field(default=0.005, ge=0, le=1)
    max_quote_age_seconds: int = Field(default=30, gt=0)
    risk_ma_ratio: float = Field(default=0.94, gt=0, le=1)
    overheat_ma_ratio: float = Field(default=1.20, ge=1)
    trim_fraction: float = Field(default=0.50, gt=0, lt=1)

    @model_validator(mode="after")
    def windows_and_thresholds_are_consistent(self) -> "PullbackTrendParameters":
        if self.risk_window >= self.trend_window:
            raise ValueError("risk_window must be shorter than trend_window")
        if self.oversold_rsi >= self.overheat_rsi:
            raise ValueError("oversold_rsi must be below overheat_rsi")
        return self


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


def build_pullback_indicators(
    request: PullbackSignalInput,
    parameters: PullbackTrendParameters | None = None,
) -> PullbackIndicatorSnapshot:
    """Build a no-lookahead indicator snapshot as of ``request.signal_date``."""

    raise NotImplementedError("QP-110 implementation pending")


def evaluate_pullback_signal(
    request: PullbackSignalInput,
    parameters: PullbackTrendParameters | None = None,
) -> PullbackSignalDecision:
    """Return a deterministic broker-free pullback strategy decision."""

    raise NotImplementedError("QP-110 implementation pending")
