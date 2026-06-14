from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from quantpilot.packages.core.reports.metrics_types import PaperTrialMetrics
from quantpilot.packages.core.schemas import HarnessModel, new_id, utc_now


ReportStatus = Literal["available", "unavailable"]
ReviewFlagSeverity = Literal["info", "warning", "blocked"]
PositionAttributionStatus = Literal["intent", "filled", "partial_fill", "rejected", "trimmed"]
DecisionExplanationType = Literal["rejected", "trimmed"]


class ReviewFlag(HarnessModel):
    code: str
    severity: ReviewFlagSeverity = "info"
    detail: str


class PolicyIntentSummary(HarnessModel):
    policy_id: str
    policy_version: int
    execution_mode: str
    broker: str
    risk_profile: str
    authority_level: int
    max_position_weight: float
    max_sector_weight: float
    min_cash_weight: float
    max_daily_turnover: float
    single_order_cash_limit: float
    allowed_order_types: list[str] = Field(default_factory=list)
    preferred_sectors: list[str] = Field(default_factory=list)
    preferred_themes: list[str] = Field(default_factory=list)
    summary: str


class SignalContribution(HarnessModel):
    symbol: str
    signal_id: str | None = None
    action: str | None = None
    strength: float | None = Field(default=None, ge=0, le=1)
    contribution_score: float = Field(default=0.0, ge=0)
    target_weight_hint: float | None = Field(default=None, ge=0, le=1)
    planned_target_weight: float | None = Field(default=None, ge=0, le=1)
    intended_notional: float = Field(default=0.0, ge=0)
    filled_notional: float = Field(default=0.0, ge=0)
    source: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str


class RiskBudgetAttribution(HarnessModel):
    status: ReportStatus
    unavailable_reason: str | None = None
    turnover_used: float = Field(default=0.0, ge=0)
    max_daily_turnover: float | None = Field(default=None, gt=0)
    daily_turnover_usage: float | None = Field(default=None, ge=0)
    largest_order_notional: float = Field(default=0.0, ge=0)
    single_order_cash_limit: float | None = Field(default=None, gt=0)
    largest_order_usage: float | None = Field(default=None, ge=0)
    failed_check_counts: dict[str, int] = Field(default_factory=dict)
    batch_decision_count: int = Field(default=0, ge=0)
    accepted_order_plan_ids: list[str] = Field(default_factory=list)
    rejected_order_plan_ids: list[str] = Field(default_factory=list)
    stale_input_reasons: list[str] = Field(default_factory=list)
    explanation: str


class SectorAttribution(HarnessModel):
    sector: str
    symbols: list[str] = Field(default_factory=list)
    current_weight: float | None = Field(default=None, ge=0)
    planned_target_weight: float | None = Field(default=None, ge=0)
    intended_notional: float = Field(default=0.0, ge=0)
    filled_notional: float = Field(default=0.0, ge=0)
    rejected_notional: float = Field(default=0.0, ge=0)
    explanation: str


class ThemeAttribution(HarnessModel):
    theme: str
    data_status: ReportStatus
    symbols: list[str] = Field(default_factory=list)
    signal_count: int = Field(default=0, ge=0)
    intended_notional: float = Field(default=0.0, ge=0)
    filled_notional: float = Field(default=0.0, ge=0)
    explanation: str


class PositionAttribution(HarnessModel):
    symbol: str
    order_plan_id: str | None = None
    intent_id: str | None = None
    side: str | None = None
    status: PositionAttributionStatus
    intended_notional: float = Field(default=0.0, ge=0)
    filled_notional: float = Field(default=0.0, ge=0)
    rejected_notional: float = Field(default=0.0, ge=0)
    target_weight: float | None = Field(default=None, ge=0, le=1)
    signal_reason: str | None = None
    risk_reasons: list[str] = Field(default_factory=list)
    ledger_event_ids: list[str] = Field(default_factory=list)
    explanation: str


class RejectedTrimmedExplanation(HarnessModel):
    order_plan_id: str | None = None
    intent_id: str | None = None
    symbol: str | None = None
    decision_type: DecisionExplanationType
    reason_codes: list[str] = Field(default_factory=list)
    notional: float = Field(default=0.0, ge=0)
    source: str
    explanation: str


class AttributionReport(HarnessModel):
    attribution_report_id: str = Field(default_factory=lambda: new_id("attr"))
    status: ReportStatus
    unavailable_reason: str | None = None
    policy_intent: PolicyIntentSummary
    ledger_primary_source: str = "reconciliation_ledger"
    ledger_event_count: int = Field(default=0, ge=0)
    ledger_sources: list[str] = Field(default_factory=list)
    data_modes: list[str] = Field(default_factory=list)
    paper_trial_metrics: PaperTrialMetrics
    signal_contributions: list[SignalContribution] = Field(default_factory=list)
    risk_budget: RiskBudgetAttribution
    sector_attribution: list[SectorAttribution] = Field(default_factory=list)
    theme_attribution: list[ThemeAttribution] = Field(default_factory=list)
    position_attribution: list[PositionAttribution] = Field(default_factory=list)
    rejected_trimmed_explanations: list[RejectedTrimmedExplanation] = Field(default_factory=list)
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    live_trading_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class AttributionOperationReport(HarnessModel):
    report_id: str = Field(default_factory=lambda: new_id("arpt"))
    user_id: str
    policy_id: str
    policy_version: int
    status: ReportStatus
    attribution_report: AttributionReport
    markdown: str
    machine_payload: dict[str, Any]
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    live_trading_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)


# The legacy public OperationReport remains quantpilot.packages.core.schemas.OperationReport.
# This local alias gives the rich report contract the OperationReport name requested by
# the Step 13 brief without changing the legacy service return type.
OperationReport = AttributionOperationReport
RichOperationReport = AttributionOperationReport
