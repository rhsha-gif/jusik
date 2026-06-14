from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from quantpilot.packages.core.schemas import HarnessModel, new_id, utc_now


LearningStatus = Literal["available", "unavailable", "blocked"]
OutcomeStatus = Literal["no_action", "intent", "submitted", "filled", "partial_fill", "rejected", "trimmed"]
LearningSource = Literal["mock", "paper"]
LearningDataMode = Literal["fixture", "paper_trading"]
PromotionStatus = Literal["pending_review"]


class PredictionOutcomeRecord(HarnessModel):
    record_id: str = Field(default_factory=lambda: new_id("pout"))
    signal_id: str | None = None
    symbol: str
    strategy_id: str | None = None
    recipe_version: str | None = None
    predicted_action: str
    calibrated_action: str | None = None
    prediction_source: str
    predicted_strength: float | None = Field(default=None, ge=0, le=1)
    predicted_confidence: float | None = Field(default=None, ge=0, le=1)
    predicted_expected_return: float | None = None
    predicted_risk: float | None = Field(default=None, ge=0)
    predicted_risk_adjusted_return: float | None = None
    target_weight_hint: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    signal_generated_at: datetime | None = None
    realized_outcome: OutcomeStatus
    realized_return: float | None = None
    realized_side: str | None = None
    intended_notional: float = Field(default=0.0, ge=0)
    submitted_notional: float = Field(default=0.0, ge=0)
    filled_notional: float = Field(default=0.0, ge=0)
    rejected_notional: float = Field(default=0.0, ge=0)
    fill_ratio: float | None = Field(default=None, ge=0)
    order_plan_ids: list[str] = Field(default_factory=list)
    ledger_entry_ids: list[str] = Field(default_factory=list)
    broker_order_ids: list[str] = Field(default_factory=list)
    fill_ids: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    source_modes: list[LearningSource] = Field(default_factory=list)
    data_modes: list[LearningDataMode] = Field(default_factory=list)
    paper_metric_features: dict[str, Any] = Field(default_factory=dict)
    validation_evidence: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None
    live_auto_update: bool = False

    @model_validator(mode="after")
    def forbid_live_auto_update(self) -> "PredictionOutcomeRecord":
        if self.live_auto_update:
            raise ValueError("offline learning records cannot enable live auto-update")
        return self


class SignalOutcomeLog(HarnessModel):
    outcome_log_id: str = Field(default_factory=lambda: new_id("sol"))
    status: LearningStatus
    unavailable_reason: str | None = None
    records: list[PredictionOutcomeRecord] = Field(default_factory=list)
    ledger_event_count: int = Field(default=0, ge=0)
    allowed_sources: list[str] = Field(default_factory=lambda: ["mock", "paper"])
    data_modes: list[LearningDataMode] = Field(default_factory=list)
    source: str = "offline_signal_outcome_logger"
    mock_paper_only: bool = True
    live_auto_update: bool = False
    live_trading_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def preserve_offline_guards(self) -> "SignalOutcomeLog":
        if self.live_auto_update or self.live_trading_enabled or not self.mock_paper_only:
            raise ValueError("signal outcome logs must remain offline mock/paper only")
        return self


class CalibrationDataset(HarnessModel):
    dataset_id: str = Field(default_factory=lambda: new_id("cds"))
    status: LearningStatus
    unavailable_reason: str | None = None
    source_log_id: str
    records: list[PredictionOutcomeRecord] = Field(default_factory=list)
    feature_rows: list[dict[str, Any]] = Field(default_factory=list)
    feature_schema: list[str] = Field(default_factory=list)
    label_field: str = "realized_outcome"
    source: str = "offline_calibration_dataset_builder"
    mock_paper_only: bool = True
    live_auto_update: bool = False
    live_trading_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def preserve_offline_guards(self) -> "CalibrationDataset":
        if self.live_auto_update or self.live_trading_enabled or not self.mock_paper_only:
            raise ValueError("calibration datasets must remain offline mock/paper only")
        return self


class PromotionCandidate(HarnessModel):
    candidate_id: str = Field(default_factory=lambda: new_id("pcand"))
    dataset_id: str
    status: PromotionStatus = "pending_review"
    human_review_required: bool = True
    live_auto_update: bool = False
    model_update_allowed: bool = False
    config_update_allowed: bool = False
    broker_update_allowed: bool = False
    record_count: int = Field(default=0, ge=0)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    metrics_summary: dict[str, Any] = Field(default_factory=dict)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def preserve_review_gate(self) -> "PromotionCandidate":
        if self.status != "pending_review":
            raise ValueError("offline promotion candidates must stay pending_review")
        if not self.human_review_required:
            raise ValueError("offline promotion candidates require human review")
        if self.live_auto_update:
            raise ValueError("offline promotion candidates cannot enable live auto-update")
        if self.model_update_allowed or self.config_update_allowed or self.broker_update_allowed:
            raise ValueError("offline promotion candidates cannot authorize automatic updates")
        return self


class OfflineLearningReport(HarnessModel):
    report_id: str = Field(default_factory=lambda: new_id("olr"))
    status: LearningStatus
    unavailable_reason: str | None = None
    signal_outcome_log: SignalOutcomeLog
    calibration_dataset: CalibrationDataset
    promotion_candidate: PromotionCandidate | None = None
    review_flags: list[str] = Field(default_factory=list)
    allowed_sources: list[str] = Field(default_factory=lambda: ["mock", "paper"])
    data_modes: list[LearningDataMode] = Field(default_factory=list)
    source: str = "offline_learning_loop"
    mock_paper_only: bool = True
    live_auto_update: bool = False
    live_trading_enabled: bool = False
    model_update_applied: bool = False
    config_update_applied: bool = False
    broker_update_applied: bool = False
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def preserve_offline_guards(self) -> "OfflineLearningReport":
        if self.live_auto_update or self.live_trading_enabled or not self.mock_paper_only:
            raise ValueError("offline learning reports must remain offline mock/paper only")
        if self.model_update_applied or self.config_update_applied or self.broker_update_applied:
            raise ValueError("offline learning reports cannot apply automatic updates")
        return self
