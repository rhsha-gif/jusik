from __future__ import annotations

from datetime import timedelta

import pytest

from quantpilot.packages.core.execution.state_machine import RiskCheckRequired
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import OrderIntent, OrderPlan, OrderStatus, OrderType, UserPolicy, utc_now


def _approved_order(policy: UserPolicy, symbol: str, notional: float) -> OrderPlan:
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=OrderIntent(
            symbol=symbol,
            side="buy",
            order_type=OrderType.limit,
            quantity=notional / 100,
            limit_price=100,
            notional=notional,
            target_weight=round(notional / 10_000_000, 6),
            reason="submit batch gate test",
        ),
        status=OrderStatus.user_approved,
        idempotency_key=f"idem-{symbol}",
        risk_check_id=f"risk-{symbol}",
        risk_check_expires_at=utc_now() + timedelta(minutes=10),
    )


def test_submit_batch_risk_rejects_before_broker_submit() -> None:
    service = HarnessService()
    policy = UserPolicy(
        max_position_weight=0.30,
        max_sector_weight=0.50,
        single_order_cash_limit=3_000_000,
        max_daily_turnover=10_000_000,
    )
    service.repositories.policies.add(policy)
    first = _approved_order(policy, "AAA", 2_100_000)
    second = _approved_order(policy, "BBB", 2_100_000)
    service.repositories.order_plans.add(first)
    service.repositories.order_plans.add(second)

    with pytest.raises(RiskCheckRequired, match="batch risk check failed"):
        service.submit_order_plan(first.order_plan_id)

    blocked = service.repositories.order_plans.require(first.order_plan_id)
    assert blocked.status == OrderStatus.user_approved
    assert blocked.blocked_reason == "batch_risk_rejected"
    assert service.repositories.broker_orders.list() == []
    assert service.repositories.fills.list() == []
