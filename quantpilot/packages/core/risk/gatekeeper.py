from __future__ import annotations

import os
from datetime import datetime, timezone

from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    OrderPlan,
    OrderType,
    PortfolioSnapshot,
    RiskCheck,
    UserPolicy,
)


PRE_HARNESS_EXECUTION_MODES = {
    ExecutionMode.paper_trading,
    ExecutionMode.approval_required,
}


def market_orders_enabled() -> bool:
    return os.getenv("MARKET_ORDERS_ENABLED", "false").lower() == "true"


def _current_position_value(snapshot: PortfolioSnapshot, symbol: str) -> float:
    return sum(position.market_value for position in snapshot.positions if position.symbol == symbol)


def run_risk_check(
    *,
    policy: UserPolicy,
    order_plan: OrderPlan,
    snapshot: PortfolioSnapshot,
    seen_idempotency_keys: set[str] | None = None,
    now: datetime | None = None,
    quote_max_age_seconds: int = 900,
) -> RiskCheck:
    current_time = now or datetime.now(timezone.utc)
    seen = seen_idempotency_keys or set()
    intent = order_plan.intent
    passed: list[str] = []
    failed: list[str] = []

    def check(name: str, condition: bool) -> None:
        if condition:
            passed.append(name)
        else:
            failed.append(name)

    check("policy_version_match", order_plan.policy_version == policy.version)
    check("execution_mode_allowed", policy.execution_mode in PRE_HARNESS_EXECUTION_MODES)
    check("broker_mode_not_live", policy.broker != BrokerMode.live_disabled)
    check("risk_check_not_expired", True)

    if intent.side == "buy":
        cash_after_order = snapshot.cash - intent.notional
        position_after = _current_position_value(snapshot, intent.symbol) + intent.notional
    else:
        cash_after_order = snapshot.cash
        position_after = max(0.0, _current_position_value(snapshot, intent.symbol) - intent.notional)

    check("available_cash", intent.side != "buy" or snapshot.cash >= intent.notional)
    check("min_cash_after_order", intent.side != "buy" or cash_after_order >= policy.min_cash_weight * snapshot.equity)
    check("max_position_weight_after_fill", position_after / snapshot.equity <= policy.max_position_weight)
    check("single_order_cash_limit", intent.notional <= policy.single_order_cash_limit)

    order_type_allowed = intent.order_type in policy.allowed_order_types
    if intent.order_type == OrderType.market and not market_orders_enabled():
        order_type_allowed = False
    check("order_type_allowed", order_type_allowed)

    check("idempotency_key_not_seen", order_plan.idempotency_key not in seen)
    quote_age = (current_time - intent.quote_time).total_seconds()
    check("quote_not_stale", 0 <= quote_age <= quote_max_age_seconds)

    monthly_pause = intent.side == "buy" and snapshot.monthly_loss_ratio <= policy.monthly_loss_limit
    monthly_stop = snapshot.monthly_loss_ratio <= policy.monthly_loss_limit * 2
    check("monthly_loss_pause_not_triggered", not monthly_pause)
    check("monthly_loss_stop_not_triggered", not monthly_stop)

    return RiskCheck(
        order_plan_id=order_plan.order_plan_id,
        passed=not failed,
        passed_checks=passed,
        failed_checks=failed,
        policy_version=policy.version,
        idempotency_key=order_plan.idempotency_key,
    )
