from __future__ import annotations

from typing import Literal

from pydantic import Field

from quantpilot.packages.core.schemas import HarnessModel


MetricsStatus = Literal["available", "unavailable"]
LatencyStatus = Literal["available", "unavailable"]


class ExecutionQualityMetrics(HarnessModel):
    orders_intended: int = Field(default=0, ge=0)
    orders_submitted: int = Field(default=0, ge=0)
    orders_filled: int = Field(default=0, ge=0)
    orders_rejected: int = Field(default=0, ge=0)
    intended_notional: float = Field(default=0.0, ge=0)
    submitted_notional: float = Field(default=0.0, ge=0)
    filled_notional: float = Field(default=0.0, ge=0)
    fill_ratio: float | None = Field(default=None, ge=0)
    submitted_fill_ratio: float | None = Field(default=None, ge=0)
    average_slippage_bps: float | None = None
    signal_to_fill_latency_seconds: float | None = Field(default=None, ge=0)
    latency_status: LatencyStatus = "unavailable"
    latency_sample_count: int = Field(default=0, ge=0)


class RejectedReasonSummary(HarnessModel):
    rejected_count: int = Field(default=0, ge=0)
    reasons: dict[str, int] = Field(default_factory=dict)


class RiskBudgetUsage(HarnessModel):
    turnover_used: float = Field(default=0.0, ge=0)
    max_daily_turnover: float | None = Field(default=None, gt=0)
    daily_turnover_usage: float | None = Field(default=None, ge=0)
    largest_order_notional: float = Field(default=0.0, ge=0)
    single_order_cash_limit: float | None = Field(default=None, gt=0)
    largest_order_usage: float | None = Field(default=None, ge=0)
    failed_check_counts: dict[str, int] = Field(default_factory=dict)


class PaperTrialMetrics(HarnessModel):
    status: MetricsStatus
    unavailable_reason: str | None = None
    calculation_source: str = "reconciliation_ledger"
    ledger_event_count: int = Field(default=0, ge=0)
    ledger_sources: list[str] = Field(default_factory=list)
    data_modes: list[str] = Field(default_factory=list)
    turnover_notional: float = Field(default=0.0, ge=0)
    turnover_weight: float | None = Field(default=None, ge=0)
    exposure_drift: float | None = Field(default=None, ge=0)
    cash_drag: float | None = Field(default=None, ge=0)
    execution_quality: ExecutionQualityMetrics = Field(default_factory=ExecutionQualityMetrics)
    rejected_reasons: RejectedReasonSummary = Field(default_factory=RejectedReasonSummary)
    risk_budget_usage: RiskBudgetUsage = Field(default_factory=RiskBudgetUsage)
    live_trading_enabled: bool = False
