from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from quantpilot.packages.core.operator.schemas import OperatorRunRequest, OperatorRunResult
from quantpilot.packages.core.schemas import DataMode, HarnessModel, new_id, utc_now


class PaperTrialConfig(HarnessModel):
    trial_id: str = Field(default_factory=lambda: new_id("ptrial"))
    user_id: str = "fixture-user"
    policy_id: str
    requested_policy_version: int
    cycles: int = Field(default=1, gt=0, le=30)
    symbols: list[str] = Field(default_factory=list)
    execution_data_mode: DataMode = DataMode.paper_trading
    run_mode: Literal["dry_run", "paper_submit"] = "dry_run"

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        return sorted({symbol.strip().upper() for symbol in value if symbol.strip()})

    @model_validator(mode="after")
    def normalize_execution_data_mode(self) -> "PaperTrialConfig":
        if self.execution_data_mode != DataMode.paper_trading:
            raise ValueError("paper trials must use paper_trading data mode label")
        return self


class PaperTrialCycleResult(HarnessModel):
    cycle_id: str = Field(default_factory=lambda: new_id("pcycle"))
    trial_id: str
    cycle_index: int = Field(ge=1)
    status: Literal["completed", "blocked", "fallback", "failed"]
    operator_run_id: str | None = None
    submitted_order_plan_ids: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    live_trading_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class PaperTrialSummary(HarnessModel):
    trial_id: str
    status: Literal["completed", "blocked", "failed"]
    cycles_requested: int
    cycles_completed: int
    submitted_order_plan_ids: list[str] = Field(default_factory=list)
    cycle_results: list[PaperTrialCycleResult] = Field(default_factory=list)
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    generated_at: datetime = Field(default_factory=utc_now)


class PaperTrialRunner:
    def __init__(self, operator_service) -> None:
        self.operator_service = operator_service

    @property
    def repositories(self):
        return self.operator_service.repositories

    @property
    def audit(self):
        return self.operator_service.audit

    def run(self, config: PaperTrialConfig) -> PaperTrialSummary:
        cycle_results: list[PaperTrialCycleResult] = []
        submitted: list[str] = []
        for index in range(1, config.cycles + 1):
            request = OperatorRunRequest(
                user_id=config.user_id,
                policy_id=config.policy_id,
                requested_policy_version=config.requested_policy_version,
                run_mode=config.run_mode,
                idempotency_key=f"{config.trial_id}:cycle:{index}",
            )
            result: OperatorRunResult = self.operator_service.run_once(request)
            submitted.extend(result.submitted_order_plan_ids)
            cycle_results.append(
                PaperTrialCycleResult(
                    trial_id=config.trial_id,
                    cycle_index=index,
                    status=result.status,
                    operator_run_id=result.run_id,
                    submitted_order_plan_ids=result.submitted_order_plan_ids,
                    fallback_reason=result.fallback.reason_code if result.fallback else None,
                    live_trading_enabled=False,
                )
            )
            if result.status in {"blocked", "failed"}:
                break

        status: Literal["completed", "blocked", "failed"]
        if any(cycle.status == "failed" for cycle in cycle_results):
            status = "failed"
        elif len(cycle_results) < config.cycles or any(cycle.status == "blocked" for cycle in cycle_results):
            status = "blocked"
        else:
            status = "completed"

        return PaperTrialSummary(
            trial_id=config.trial_id,
            status=status,
            cycles_requested=config.cycles,
            cycles_completed=len(cycle_results),
            submitted_order_plan_ids=submitted,
            cycle_results=cycle_results,
            live_trading_enabled=False,
            order_submission_enabled=config.run_mode == "paper_submit",
        )
