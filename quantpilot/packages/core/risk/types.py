from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from quantpilot.packages.core.schemas import GuardrailState, HarnessModel, PortfolioPlan, PortfolioSnapshot, new_id, utc_now


class BatchRiskConfig(HarnessModel):
    partial_allow: bool = False
    quote_max_age_seconds: int = Field(default=30, gt=0)
    snapshot_max_age_seconds: int = Field(default=900, gt=0)


class BatchRiskInput(HarnessModel):
    portfolio_plan: PortfolioPlan
    snapshot: PortfolioSnapshot
    quotes: dict[str, float] = Field(default_factory=dict)
    config: BatchRiskConfig = Field(default_factory=BatchRiskConfig)
    guardrail_state: GuardrailState = Field(default_factory=GuardrailState)
    seen_idempotency_keys: list[str] = Field(default_factory=list)
    now: datetime = Field(default_factory=utc_now)


class BatchPortfolioExposure(HarnessModel):
    cash: float
    equity: float
    cash_weight: float
    position_values: dict[str, float]
    position_weights: dict[str, float]
    sector_values: dict[str, float]
    sector_weights: dict[str, float]


class BatchRiskDecision(HarnessModel):
    decision_id: str = Field(default_factory=lambda: new_id("brisk"))
    passed: bool
    mode: Literal["full_batch", "partial_batch", "rejected"]
    policy_version: int
    accepted_intent_ids: list[str] = Field(default_factory=list)
    rejected_intent_ids: list[str] = Field(default_factory=list)
    accepted_order_plan_ids: list[str] = Field(default_factory=list)
    rejected_order_plan_ids: list[str] = Field(default_factory=list)
    passed_checks: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    rejected_reasons: dict[str, list[str]] = Field(default_factory=dict)
    stale_input_reasons: list[str] = Field(default_factory=list)
    portfolio_after_batch: BatchPortfolioExposure
    created_at: datetime = Field(default_factory=utc_now)
