from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from quantpilot.packages.core.backtest.schemas import BacktestResult
from quantpilot.packages.core.schemas import HarnessModel, new_id, utc_now


class BacktestAcceptanceRuleResult(HarnessModel):
    rule_id: str
    passed: bool
    observed: float | int | str | bool | None = None
    threshold: float | int | str | bool | None = None
    detail: str


class BacktestAcceptanceDecision(HarnessModel):
    decision_id: str = Field(default_factory=lambda: new_id("btacc"))
    status: Literal["accepted_for_review", "blocked"]
    strategy_id: str
    strategy_version: str
    policy_id: str = "quantpilot_backtest_acceptance_policy_v1"
    rule_results: list[BacktestAcceptanceRuleResult]
    blocking_reasons: list[str] = Field(default_factory=list)
    research_only: bool = True
    live_trading_approval: bool = False
    evaluated_at: datetime = Field(default_factory=utc_now)


class BacktestAcceptanceEvidence(HarnessModel):
    strategy_id: str
    strategy_family_id: str | None = None
    strategy_version: str
    spec_hash: str | None = None
    code_commit: str | None = None
    researcher: str | None = None
    approved_by: str | None = None
    notes: str | None = None
    backtest_result: BacktestResult | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    tested_variants: int | None = Field(default=None, ge=0)

    @field_validator(
        "strategy_id",
        "strategy_family_id",
        "strategy_version",
        "spec_hash",
        "code_commit",
        "researcher",
        "approved_by",
        "notes",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


DEFAULT_ACCEPTANCE_POLICY: dict[str, float | int] = {
    "min_filled_trades": 3,
    "max_drawdown": 0.35,
    "max_turnover": 2.0,
}


def load_backtest_acceptance_policy() -> dict[str, float | int]:
    return dict(DEFAULT_ACCEPTANCE_POLICY)


def _metric(evidence: BacktestAcceptanceEvidence, name: str) -> float | int | None:
    if name in evidence.metrics:
        value = evidence.metrics[name]
        if isinstance(value, (int, float)):
            return value
    if evidence.backtest_result is None:
        return None
    value = getattr(evidence.backtest_result.metrics, name, None)
    return value if isinstance(value, (int, float)) else None


def _rule_min(rule_id: str, observed: float | int | None, threshold: float | int) -> BacktestAcceptanceRuleResult:
    passed = observed is not None and observed >= threshold
    return BacktestAcceptanceRuleResult(
        rule_id=rule_id,
        passed=passed,
        observed=observed,
        threshold=threshold,
        detail=f"{rule_id}: observed {observed} >= threshold {threshold}",
    )


def _rule_max(rule_id: str, observed: float | int | None, threshold: float | int) -> BacktestAcceptanceRuleResult:
    passed = observed is not None and observed <= threshold
    return BacktestAcceptanceRuleResult(
        rule_id=rule_id,
        passed=passed,
        observed=observed,
        threshold=threshold,
        detail=f"{rule_id}: observed {observed} <= threshold {threshold}",
    )


def evaluate_backtest_acceptance(
    evidence: BacktestAcceptanceEvidence,
    policy: dict[str, float | int] | None = None,
) -> BacktestAcceptanceDecision:
    policy_values = policy or load_backtest_acceptance_policy()
    rule_results = [
        _rule_min("min_filled_trades", _metric(evidence, "filled_trades"), int(policy_values["min_filled_trades"])),
        _rule_max("max_drawdown", _metric(evidence, "max_drawdown"), float(policy_values["max_drawdown"])),
        _rule_max("max_turnover", _metric(evidence, "turnover"), float(policy_values["max_turnover"])),
    ]
    if evidence.backtest_result is not None and (
        not evidence.backtest_result.research_only or evidence.backtest_result.live_trading_approval
    ):
        rule_results.append(
            BacktestAcceptanceRuleResult(
                rule_id="research_only",
                passed=False,
                observed=False,
                threshold=True,
                detail="backtest acceptance cannot grant live-trading approval",
            )
        )
    blocking = [rule.rule_id for rule in rule_results if not rule.passed]
    return BacktestAcceptanceDecision(
        status="blocked" if blocking else "accepted_for_review",
        strategy_id=evidence.strategy_id,
        strategy_version=evidence.strategy_version,
        rule_results=rule_results,
        blocking_reasons=blocking,
        research_only=True,
        live_trading_approval=False,
    )
