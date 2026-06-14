from __future__ import annotations

from quantpilot.packages.core.execution.order_context import (
    build_guardrail_state,
    build_submit_batch_context,
    collect_seen_idempotency_keys,
    orders_for_submit_batch,
    quotes_for_intents,
)
from quantpilot.packages.core.schemas import (
    OrderIntent,
    OrderPlan,
    OrderStatus,
    OrderType,
    UserPolicy,
)


def _order(
    policy: UserPolicy,
    *,
    order_plan_id: str,
    idempotency_key: str,
    status: OrderStatus,
    symbol: str = "AAA",
    side: str = "buy",
    notional: float = 100_000,
    limit_price: float | None = 100.0,
) -> OrderPlan:
    intent = OrderIntent(
        symbol=symbol,
        side=side,
        order_type=OrderType.limit if limit_price is not None else OrderType.market,
        quantity=notional / (limit_price or 100.0),
        limit_price=limit_price,
        notional=notional,
        target_weight=0.01,
        reason="test order context",
    )
    return OrderPlan(
        order_plan_id=order_plan_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=intent,
        status=status,
        idempotency_key=idempotency_key,
    )


def test_collect_seen_idempotency_keys_respects_submitted_only_and_exclusions() -> None:
    policy = UserPolicy()
    orders = [
        _order(policy, order_plan_id="draft", idempotency_key="key-draft", status=OrderStatus.draft),
        _order(policy, order_plan_id="proposed", idempotency_key="key-proposed", status=OrderStatus.proposed),
        _order(policy, order_plan_id="submitted", idempotency_key="key-submitted", status=OrderStatus.submitted),
        _order(policy, order_plan_id="filled", idempotency_key="key-filled", status=OrderStatus.filled),
    ]

    assert collect_seen_idempotency_keys(orders) == {
        "key-draft",
        "key-proposed",
        "key-submitted",
        "key-filled",
    }
    assert collect_seen_idempotency_keys(
        orders,
        exclude_order_plan_id="submitted",
        exclude_order_plan_ids={"draft"},
        submitted_only=True,
    ) == {"key-filled"}


def test_build_guardrail_state_counts_submitted_orders_and_unfilled_conflicts() -> None:
    policy = UserPolicy(kill_switch_engaged=True)
    other_policy = UserPolicy()
    orders = [
        _order(policy, order_plan_id="accepted", idempotency_key="key-accepted", status=OrderStatus.accepted, notional=120_000),
        _order(policy, order_plan_id="filled", idempotency_key="key-filled", status=OrderStatus.filled, notional=80_000),
        _order(policy, order_plan_id="proposed", idempotency_key="key-proposed", status=OrderStatus.proposed, symbol="BBB", side="sell"),
        _order(policy, order_plan_id="excluded", idempotency_key="key-excluded", status=OrderStatus.submitted, notional=999_000),
        _order(other_policy, order_plan_id="other", idempotency_key="key-other", status=OrderStatus.submitted, notional=777_000),
    ]

    state = build_guardrail_state(
        order_plans=orders,
        policy=policy,
        strategy_id="strategy-x",
        autopilot_paused=True,
        last_blocked_reason="risk_check_failed",
        exclude_order_plan_id="excluded",
    )

    assert state.daily_order_count == 2
    assert state.daily_turnover_used == 200_000
    assert state.kill_switch_engaged is True
    assert state.autopilot_paused is True
    assert state.last_blocked_reason == "risk_check_failed"
    assert state.submitted_idempotency_keys == ["key-accepted", "key-filled"]
    assert state.unfilled_order_keys == ["strategy-x:AAA:buy", "strategy-x:BBB:sell"]


def test_quotes_for_intents_uses_only_limit_prices() -> None:
    limit_intent = OrderIntent(
        symbol="AAA",
        side="buy",
        order_type=OrderType.limit,
        quantity=10,
        limit_price=123.45,
        notional=1_234.5,
        target_weight=0.01,
        reason="limit quote",
    )
    market_intent = OrderIntent(
        symbol="BBB",
        side="sell",
        order_type=OrderType.market,
        quantity=10,
        limit_price=None,
        notional=1_000,
        target_weight=0.0,
        reason="market quote omitted",
    )

    assert quotes_for_intents([limit_intent, market_intent]) == {"AAA": 123.45}


def test_orders_for_submit_batch_filters_policy_and_replaces_current_order() -> None:
    policy = UserPolicy()
    other_policy = UserPolicy()
    original_current = _order(
        policy,
        order_plan_id="current",
        idempotency_key="key-current-old",
        status=OrderStatus.proposed,
    )
    current = original_current.model_copy(update={"idempotency_key": "key-current-new"})
    same_policy_submitted = _order(
        policy,
        order_plan_id="submitted",
        idempotency_key="key-submitted",
        status=OrderStatus.submitted,
    )
    same_policy_draft = _order(
        policy,
        order_plan_id="draft",
        idempotency_key="key-draft",
        status=OrderStatus.draft,
    )
    other_policy_submitted = _order(
        other_policy,
        order_plan_id="other",
        idempotency_key="key-other",
        status=OrderStatus.submitted,
    )

    batch = orders_for_submit_batch(
        [same_policy_submitted, same_policy_draft, other_policy_submitted, original_current],
        current,
    )

    assert [order.order_plan_id for order in batch] == ["submitted", "current"]
    assert batch[-1].idempotency_key == "key-current-new"


def test_build_submit_batch_context_reuses_batch_exclusions_for_guardrails_and_seen_keys() -> None:
    policy = UserPolicy()
    existing_submitted = _order(
        policy,
        order_plan_id="submitted",
        idempotency_key="key-submitted",
        status=OrderStatus.submitted,
        notional=50_000,
    )
    existing_unfilled = _order(
        policy,
        order_plan_id="unfilled",
        idempotency_key="key-unfilled",
        status=OrderStatus.proposed,
        symbol="BBB",
        side="sell",
    )
    current = _order(
        policy,
        order_plan_id="current",
        idempotency_key="key-current",
        status=OrderStatus.user_approved,
        notional=10_000,
    )

    context = build_submit_batch_context(
        order_plans=[existing_submitted, existing_unfilled, current],
        order_plan=current,
        policy=policy,
        strategy_id="strategy-x",
    )

    assert [order.order_plan_id for order in context.batch_orders] == ["submitted", "unfilled", "current"]
    assert context.quotes == {"AAA": 100.0, "BBB": 100.0}
    assert context.seen_idempotency_keys == set()
    assert context.guardrail_state.daily_order_count == 0
    assert context.guardrail_state.daily_turnover_used == 0
    assert context.guardrail_state.unfilled_order_keys == []
