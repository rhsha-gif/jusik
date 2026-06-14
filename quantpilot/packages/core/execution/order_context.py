from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from quantpilot.packages.core.schemas import (
    GuardrailState,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    UserPolicy,
)


@dataclass(frozen=True)
class SubmitBatchContext:
    batch_orders: list[OrderPlan]
    quotes: dict[str, float]
    guardrail_state: GuardrailState
    seen_idempotency_keys: set[str]


SUBMITTED_ORDER_STATES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.submitted,
        OrderStatus.accepted,
        OrderStatus.partially_filled,
        OrderStatus.filled,
    }
)

UNFILLED_ORDER_STATES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.proposed,
        OrderStatus.user_approved,
        OrderStatus.submitted,
        OrderStatus.accepted,
        OrderStatus.partially_filled,
    }
)

SUBMIT_BATCH_ORDER_STATES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.proposed,
        OrderStatus.user_approved,
        OrderStatus.submitted,
        OrderStatus.accepted,
        OrderStatus.partially_filled,
        OrderStatus.filled,
    }
)


def excluded_order_ids(
    *,
    exclude_order_plan_id: str | None = None,
    exclude_order_plan_ids: set[str] | None = None,
) -> set[str]:
    excluded = set(exclude_order_plan_ids or set())
    if exclude_order_plan_id is not None:
        excluded.add(exclude_order_plan_id)
    return excluded


def collect_seen_idempotency_keys(
    order_plans: Sequence[OrderPlan],
    *,
    exclude_order_plan_id: str | None = None,
    exclude_order_plan_ids: set[str] | None = None,
    submitted_only: bool = False,
) -> set[str]:
    excluded = excluded_order_ids(
        exclude_order_plan_id=exclude_order_plan_id,
        exclude_order_plan_ids=exclude_order_plan_ids,
    )
    return {
        order.idempotency_key
        for order in order_plans
        if order.order_plan_id not in excluded
        and (not submitted_only or order.status in SUBMITTED_ORDER_STATES)
    }


def build_guardrail_state(
    *,
    order_plans: Sequence[OrderPlan],
    policy: UserPolicy,
    strategy_id: str,
    autopilot_paused: bool = False,
    last_blocked_reason: str | None = None,
    exclude_order_plan_id: str | None = None,
    exclude_order_plan_ids: set[str] | None = None,
) -> GuardrailState:
    excluded = excluded_order_ids(
        exclude_order_plan_id=exclude_order_plan_id,
        exclude_order_plan_ids=exclude_order_plan_ids,
    )
    policy_orders = [
        order
        for order in order_plans
        if order.policy_id == policy.policy_id and order.order_plan_id not in excluded
    ]
    submitted_orders = [
        order for order in policy_orders if order.status in SUBMITTED_ORDER_STATES
    ]
    unfilled_order_keys = [
        f"{order.explanation.strategy_id if order.explanation else strategy_id}:{order.intent.symbol}:{order.intent.side}"
        for order in policy_orders
        if order.status in UNFILLED_ORDER_STATES
    ]
    return GuardrailState(
        daily_order_count=len(submitted_orders),
        daily_turnover_used=round(sum(order.intent.notional for order in submitted_orders), 2),
        kill_switch_engaged=policy.kill_switch_engaged,
        autopilot_paused=autopilot_paused,
        last_blocked_reason=last_blocked_reason,
        unfilled_order_keys=unfilled_order_keys,
        submitted_idempotency_keys=[order.idempotency_key for order in submitted_orders],
    )


def quotes_for_intents(intents: Sequence[OrderIntent]) -> dict[str, float]:
    return {
        intent.symbol: float(intent.limit_price)
        for intent in intents
        if intent.limit_price is not None
    }


def orders_for_submit_batch(order_plans: Sequence[OrderPlan], order_plan: OrderPlan) -> list[OrderPlan]:
    batch: list[OrderPlan] = []
    current_seen = False
    for existing in order_plans:
        if existing.policy_id != order_plan.policy_id or existing.status not in SUBMIT_BATCH_ORDER_STATES:
            continue
        if existing.order_plan_id == order_plan.order_plan_id:
            batch.append(order_plan)
            current_seen = True
        else:
            batch.append(existing)
    if not current_seen:
        batch.append(order_plan)
    return batch


def build_submit_batch_context(
    *,
    order_plans: Sequence[OrderPlan],
    order_plan: OrderPlan,
    policy: UserPolicy,
    strategy_id: str,
    autopilot_paused: bool = False,
    last_blocked_reason: str | None = None,
) -> SubmitBatchContext:
    batch_orders = orders_for_submit_batch(order_plans, order_plan)
    batch_order_ids = {order.order_plan_id for order in batch_orders}
    return SubmitBatchContext(
        batch_orders=batch_orders,
        quotes=quotes_for_intents([order.intent for order in batch_orders]),
        guardrail_state=build_guardrail_state(
            order_plans=order_plans,
            policy=policy,
            strategy_id=strategy_id,
            autopilot_paused=autopilot_paused,
            last_blocked_reason=last_blocked_reason,
            exclude_order_plan_ids=batch_order_ids,
        ),
        seen_idempotency_keys=collect_seen_idempotency_keys(
            order_plans,
            exclude_order_plan_ids=batch_order_ids,
            submitted_only=True,
        ),
    )
