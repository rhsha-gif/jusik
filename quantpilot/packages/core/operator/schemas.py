from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from quantpilot.packages.core.execution.fallback_manager import FallbackDecision
from quantpilot.packages.core.policy.versioning import PolicyReviewRequest, PolicyVersionChange
from quantpilot.packages.core.schemas import HarnessModel, new_id, utc_now
from quantpilot.packages.core.strategies.registry import StrategyRegistryEntry, StrategySelectionDecision


__all__ = [
    "FallbackDecision",
    "OperatorDecision",
    "OperatorReport",
    "OperatorRunRequest",
    "OperatorRunResult",
    "PolicyReviewRequest",
    "PolicyVersionChange",
    "StrategyRegistryEntry",
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

    @property
    def is_submission_mode(self) -> bool:
        return self.run_mode in {"mock_submit", "paper_submit"}


class OperatorDecision(HarnessModel):
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
    broker_order_ids: list[str] = Field(default_factory=list)
    risk_check_ids: list[str] = Field(default_factory=list)
    safety_flags: dict[str, bool | str] = Field(default_factory=dict)
    live_trading_enabled: bool = False
    audit_event_count: int = 0


class OperatorRunResult(HarnessModel):
    run_id: str
    status: OperatorRunStatus
    submitted_order_plan_ids: list[str] = Field(default_factory=list)
    blocked_order_plan_ids: list[str] = Field(default_factory=list)
    fallback: FallbackDecision | None = None
    report: OperatorReport
