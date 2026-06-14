from __future__ import annotations

from collections.abc import Iterable

from quantpilot.packages.core.execution.state_machine import TERMINAL_STATES
from quantpilot.packages.core.schemas import GuardrailState, OrderPlan, OrderStatus, UserPolicy


SUBMITTED_STATUSES = {
    OrderStatus.submitted,
    OrderStatus.accepted,
    OrderStatus.partially_filled,
    OrderStatus.filled,
}

UNFILLED_STATUSES = {
    OrderStatus.risk_checked,
    OrderStatus.proposed,
    OrderStatus.user_approved,
    OrderStatus.submitted,
    OrderStatus.accepted,
    OrderStatus.partially_filled,
}


def _is_active_plan(order_plan: OrderPlan) -> bool:
    return order_plan.status not in TERMINAL_STATES


def _strategy_id_for_plan(order_plan: OrderPlan, fallback: str) -> str:
    if order_plan.explanation is not None:
        return order_plan.explanation.strategy_id
    return fallback


def seen_idempotency_keys(
    order_plans: Iterable[OrderPlan],
    *,
    exclude_order_plan_id: str | None = None,
    submitted_only: bool = False,
) -> set[str]:
    keys: set[str] = set()
    for order_plan in order_plans:
        if order_plan.order_plan_id == exclude_order_plan_id:
            continue
        if submitted_only:
            if order_plan.status not in SUBMITTED_STATUSES:
                continue
        elif not _is_active_plan(order_plan):
            continue
        keys.add(order_plan.idempotency_key)
    return keys


def guardrail_state(
    order_plans: Iterable[OrderPlan],
    *,
    policy: UserPolicy,
    strategy_id: str,
    exclude_order_plan_id: str | None = None,
    autopilot_paused: bool = False,
    last_blocked_reason: str | None = None,
) -> GuardrailState:
    daily_order_count = 0
    daily_turnover_used = 0.0
    unfilled_order_keys: list[str] = []
    submitted_keys: set[str] = set()

    for order_plan in order_plans:
        if order_plan.order_plan_id == exclude_order_plan_id:
            continue
        if order_plan.policy_id != policy.policy_id:
            continue
        if order_plan.status in SUBMITTED_STATUSES:
            daily_order_count += 1
            daily_turnover_used += order_plan.intent.notional
            submitted_keys.add(order_plan.idempotency_key)
        if order_plan.status in UNFILLED_STATUSES:
            plan_strategy_id = _strategy_id_for_plan(order_plan, strategy_id)
            unfilled_order_keys.append(f"{plan_strategy_id}:{order_plan.intent.symbol}:{order_plan.intent.side}")

    return GuardrailState(
        daily_order_count=daily_order_count,
        daily_turnover_used=round(daily_turnover_used, 2),
        monthly_loss_pause_active=False,
        monthly_loss_stop_active=False,
        kill_switch_engaged=policy.kill_switch_engaged,
        broker_healthy=True,
        autopilot_paused=autopilot_paused,
        last_blocked_reason=last_blocked_reason,
        unfilled_order_keys=sorted(set(unfilled_order_keys)),
        submitted_idempotency_keys=sorted(submitted_keys),
    )
