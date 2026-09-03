from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from quantpilot.packages.core.execution.fallback_manager import FallbackDecision
from quantpilot.packages.core.schemas import HarnessModel, new_id, utc_now
from quantpilot.packages.core.strategies.registry import StrategySelectionDecision


__all__ = [
    "FallbackDecision",
    "OperatorDecision",
    "OperatorReport",
    "OperatorRunRequest",
    "OperatorRunResult",
    "StrategySelectionDecision",
]


OperatorRunMode = Literal["dry_run", "mock_submit", "paper_submit"]
OperatorRunStatus = Literal["completed", "blocked", "fallback", "failed"]


class OperatorRunRequest(HarnessModel):
    user_id: str = "fixture-user"
    policy_id: str
    requested_policy_version: int
    run_mode: OperatorRunMode = "dry_run"
    requested_at: datetime = Field(default_factory=utc_now)
    idempotency_key: str


class OperatorDecision(HarnessModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_serialization_defaults_required=True,
    )

    decision_id: str = Field(default_factory=lambda: new_id("opdec"))
    run_id: str
    policy_id: str
    policy_version: int
    strategy_id: str | None = None
    order_plan_id: str | None = None
    action: Literal["submit", "block", "fallback", "noop"]
    reason: str
    risk_check_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class OperatorReport(HarnessModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_serialization_defaults_required=True,
    )

    report_id: str = Field(default_factory=lambda: new_id("oprpt"))
    run_id: str
    user_id: str
    policy_id: str
    policy_version: int
    started_at: datetime
    completed_at: datetime
    status: OperatorRunStatus
    strategy_selection: StrategySelectionDecision
    decisions: list[OperatorDecision]
    fallback: FallbackDecision | None = None
    order_plan_ids: list[str] = Field(default_factory=list)
    broker_order_ids: list[str]
    risk_check_ids: list[str]
    safety_flags: dict[str, bool | str]
    live_trading_enabled: bool = False
    audit_event_count: int = 0


class OperatorRunResult(HarnessModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_serialization_defaults_required=True,
    )

    run_id: str
    status: OperatorRunStatus
    submitted_order_plan_ids: list[str]
    blocked_order_plan_ids: list[str]
    fallback: FallbackDecision | None = None
    report: OperatorReport
