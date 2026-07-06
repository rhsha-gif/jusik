from __future__ import annotations

from fastapi.testclient import TestClient

from quantpilot.packages.core.execution import ExecutionStatus
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import OrderIntent, OrderPlan, OrderStatus, OrderType
from quantpilot.services.api.dependencies import get_harness_service
from quantpilot.services.api.main import app


def _client_for_service(service: HarnessService) -> TestClient:
    app.dependency_overrides[get_harness_service] = lambda: service
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def _service_with_orders() -> tuple[HarnessService, list[OrderPlan]]:
    service = HarnessService()
    policy = service.parse_policy()
    signals = service.run_signals()
    plan = service.create_portfolio_plan(policy_id=policy.policy_id, signals=signals)
    orders = service.create_order_plans(portfolio_plan_id=plan.plan_id)
    return service, orders


def _approved_order(service: HarnessService, orders: list[OrderPlan]) -> OrderPlan:
    proposed = next(order for order in orders if order.status == OrderStatus.proposed)
    return service.approve_order_plan(proposed.order_plan_id)


def _audit_actions(service: HarnessService) -> list[str]:
    return [event.action for event in service.repositories.audit_logs.list()]


def test_preview_simulates_approved_order_without_side_effects() -> None:
    service, orders = _service_with_orders()
    approved = _approved_order(service, orders)
    fills_before = len(service.repositories.fills.list())
    broker_orders_before = len(service.repositories.broker_orders.list())

    result = service.preview_order_execution(approved.order_plan_id)

    assert result.status in {
        ExecutionStatus.simulated,
        ExecutionStatus.partially_filled,
        ExecutionStatus.filled,
    }
    assert result.broker_order_sent is False
    assert result.live_trading_enabled is False
    assert result.market_orders_enabled is False
    assert result.filled_quantity > 0
    # Preview leaves order state, fills, and broker orders untouched.
    stored = service.repositories.order_plans.require(approved.order_plan_id)
    assert stored.status == OrderStatus.user_approved
    assert len(service.repositories.fills.list()) == fills_before
    assert len(service.repositories.broker_orders.list()) == broker_orders_before
    assert "execution_simulation_previewed" in _audit_actions(service)


def test_preview_fails_closed_for_non_approved_order() -> None:
    service, orders = _service_with_orders()
    proposed = next(order for order in orders if order.status == OrderStatus.proposed)

    result = service.preview_order_execution(proposed.order_plan_id)

    assert result.status == ExecutionStatus.blocked
    assert result.filled_quantity == 0
    assert result.broker_order_sent is False
    assert result.events[0].reason_code == "order_not_approved"
    stored = service.repositories.order_plans.require(proposed.order_plan_id)
    assert stored.status == OrderStatus.proposed
    assert "execution_simulation_blocked" in _audit_actions(service)


def test_preview_fails_closed_when_quote_is_unavailable() -> None:
    service = HarnessService()
    policy = service.parse_policy()
    order_plan = OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        status=OrderStatus.user_approved,
        idempotency_key="preview-unknown-symbol",
        intent=OrderIntent(
            symbol="ZZZ_UNKNOWN",
            side="buy",
            order_type=OrderType.limit,
            quantity=10,
            limit_price=100.0,
            notional=1000.0,
            target_weight=0.05,
            reason="execution preview test",
        ),
    )
    service.repositories.order_plans.add(order_plan)

    result = service.preview_order_execution(order_plan.order_plan_id)

    assert result.status == ExecutionStatus.unavailable
    assert result.filled_quantity == 0
    assert result.broker_order_sent is False
    assert result.events[0].reason_code == "quote_unavailable"
    assert "execution_simulation_blocked" in _audit_actions(service)


def test_simulate_api_route_returns_preview_for_approved_order() -> None:
    service, orders = _service_with_orders()
    approved = _approved_order(service, orders)
    client = _client_for_service(service)
    try:
        response = client.post(
            f"/api/orders/{approved.order_plan_id}/simulate",
            json={"config": {"algorithm": "twap", "slice_count": 3}},
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["broker_order_sent"] is False
    assert body["live_trading_enabled"] is False
    assert body["market_orders_enabled"] is False
    assert body["schedule"]["algorithm"] == "twap"
    assert len(body["schedule"]["slices"]) == 3
    stored = service.repositories.order_plans.require(approved.order_plan_id)
    assert stored.status == OrderStatus.user_approved


def test_simulate_api_route_returns_404_for_missing_order() -> None:
    client = _client_for_service(HarnessService())
    try:
        response = client.post("/api/orders/missing-order/simulate")
    finally:
        _clear_overrides()

    assert response.status_code == 404
