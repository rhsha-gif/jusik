from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from quantpilot.packages.core.schemas import DataMode, HarnessModel, SignalAction, utc_now


RegimeLabel = Literal["uptrend", "pullback", "range", "volatile", "downtrend", "risk_off"]
CalibrationStatus = Literal["available", "guarded", "blocked", "expired", "unavailable"]


class MultiFactorScore(HarnessModel):
    symbol: str
    momentum: float = Field(ge=0, le=100)
    trend: float = Field(ge=0, le=100)
    volume: float = Field(ge=0, le=100)
    volatility: float = Field(ge=0, le=100)
    data_quality: float = Field(ge=0, le=100)
    final_score: float = Field(ge=0, le=100)
    regime: RegimeLabel
    weights: dict[str, float]
    reason_codes: list[str] = Field(default_factory=list)


class ExpectedReturnRiskProxy(HarnessModel):
    symbol: str
    horizon: str
    expected_return: float
    risk: float = Field(ge=0)
    risk_adjusted_return: float
    confidence: float = Field(ge=0, le=1)
    calibrated: bool = True
    data_mode: DataMode = DataMode.fixture
    source: str = "calibrated_multifactor_signal_model"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnsembleVote(HarnessModel):
    symbol: str
    votes: dict[str, float]
    selected_action: SignalAction
    reason_codes: list[str] = Field(default_factory=list)


class CalibrationGuardResult(HarnessModel):
    passed: bool
    status: CalibrationStatus
    action_allowed: bool
    reason_codes: list[str] = Field(default_factory=list)


class CalibratedSignal(HarnessModel):
    signal_id: str
    symbol: str
    base_action: SignalAction
    calibrated_action: SignalAction
    strength: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    decay: float = Field(ge=0, le=1)
    multi_factor_score: MultiFactorScore
    expected_return_risk: ExpectedReturnRiskProxy
    ensemble_vote: EnsembleVote
    guard: CalibrationGuardResult
    target_weight_hint: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    source: str = "calibrated_multifactor_signal_model"
    generated_at: datetime = Field(default_factory=utc_now)


class CalibratedSignalSet(HarnessModel):
    signals: list[CalibratedSignal]
    provider_status: dict[str, dict[str, Any]]
    data_quality: dict[str, Any]
    order_submission_enabled: bool = False
    source: str = "calibrated_multifactor_signal_model"
    created_at: datetime = Field(default_factory=utc_now)
