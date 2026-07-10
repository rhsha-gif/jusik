"""Pure strategy-health review decisions for the professional operator."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field

from quantpilot.packages.core.schemas import HarnessModel


StrategyHealthStatus = Literal[
    "active",
    "review_unavailable",
    "paused_reapproval",
    "disabled",
]

REAPPROVAL_DRAWDOWN_MULTIPLIER = 1.5
DISABLE_MAX_DRAWDOWN = 0.20
DISABLE_EXCESS_RETURN = -0.10


class StrategyHealthInput(HarnessModel):
    strategy_id: str
    strategy_version: str
    backtest_max_drawdown: float = Field(ge=0, allow_inf_nan=False)
    realized_max_drawdown: float = Field(ge=0, allow_inf_nan=False)
    realized_return: float = Field(allow_inf_nan=False)
    benchmark_return: float | None = Field(default=None, allow_inf_nan=False)


class StrategyHealthDecision(HarnessModel):
    strategy_id: str
    strategy_version: str
    status: StrategyHealthStatus
    backtest_max_drawdown: float = Field(ge=0, allow_inf_nan=False)
    realized_max_drawdown: float = Field(ge=0, allow_inf_nan=False)
    realized_return: float = Field(allow_inf_nan=False)
    benchmark_return: float | None = Field(default=None, allow_inf_nan=False)
    reapproval_drawdown_threshold: float = Field(ge=0, allow_inf_nan=False)
    excess_return: float | None = Field(default=None, allow_inf_nan=False)
    block_new_buys: bool
    start_retirement: bool
    reason_codes: list[str] = Field(default_factory=list)
    reason: str


def evaluate_strategy_health(request: StrategyHealthInput) -> StrategyHealthDecision:
    """Evaluate health without mutating a registry or creating liquidation orders."""

    realized_drawdown = Decimal(str(request.realized_max_drawdown))
    reapproval_threshold_value = (
        Decimal(str(request.backtest_max_drawdown))
        * Decimal(str(REAPPROVAL_DRAWDOWN_MULTIPLIER))
    )
    excess_return_value = (
        None
        if request.benchmark_return is None
        else Decimal(str(request.realized_return))
        - Decimal(str(request.benchmark_return))
    )
    reapproval_threshold = float(reapproval_threshold_value)
    excess_return = None if excess_return_value is None else float(excess_return_value)

    disable_reasons: list[str] = []
    if realized_drawdown >= Decimal(str(DISABLE_MAX_DRAWDOWN)):
        disable_reasons.append("max_drawdown_disable_threshold_reached")
    if (
        excess_return_value is not None
        and excess_return_value <= Decimal(str(DISABLE_EXCESS_RETURN))
    ):
        disable_reasons.append("excess_return_disable_threshold_reached")

    common = {
        "strategy_id": request.strategy_id,
        "strategy_version": request.strategy_version,
        "backtest_max_drawdown": request.backtest_max_drawdown,
        "realized_max_drawdown": request.realized_max_drawdown,
        "realized_return": request.realized_return,
        "benchmark_return": request.benchmark_return,
        "reapproval_drawdown_threshold": reapproval_threshold,
        "excess_return": excess_return,
    }

    if disable_reasons:
        return StrategyHealthDecision(
            **common,
            status="disabled",
            block_new_buys=True,
            start_retirement=True,
            reason_codes=disable_reasons,
            reason="strategy crossed a mandatory disable threshold",
        )

    if realized_drawdown > reapproval_threshold_value:
        return StrategyHealthDecision(
            **common,
            status="paused_reapproval",
            block_new_buys=True,
            start_retirement=False,
            reason_codes=["max_drawdown_reapproval_threshold_breached"],
            reason="strategy drawdown requires reapproval before new buys",
        )

    if request.benchmark_return is None:
        return StrategyHealthDecision(
            **common,
            status="review_unavailable",
            block_new_buys=True,
            start_retirement=False,
            reason_codes=["benchmark_return_missing"],
            reason="benchmark return is required to complete the health review",
        )

    return StrategyHealthDecision(
        **common,
        status="active",
        block_new_buys=False,
        start_retirement=False,
        reason_codes=["strategy_health_within_thresholds"],
        reason="strategy health remains within active thresholds",
    )
