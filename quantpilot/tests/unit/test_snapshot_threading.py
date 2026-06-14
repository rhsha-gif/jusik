from __future__ import annotations

from datetime import timedelta

import pytest

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.core.execution.state_machine import RiskCheckRequired
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.schemas import OrderIntent, OrderPlan, PortfolioSnapshot, UserPolicy, utc_now


def _proposal_ready_for_submit(service: HarnessService, snapshot: PortfolioSnapshot) -> str:
    policy = service.parse_policy()
    signals = service.run_signals()
    plan = service.create_portfolio_plan(policy_id=policy.policy_id, signals=signals, snapshot=snapshot)
    proposals = service.generate_order_proposals(portfolio_plan_id=plan.plan_id, snapshot=snapshot)
    assert proposals
    service.approve_order_plan(proposals[0].order_plan_id)
    return proposals[0].order_plan_id


def test_mock_broker_returns_runtime_snapshot_metadata() -> None:
    snapshot = MockBroker().get_positions("runtime-user")

    assert snapshot.user_id == "runtime-user"
    assert snapshot.source == "mock_broker"
    assert snapshot.is_fixture is False
    assert snapshot.is_stale is False
    assert snapshot.as_of <= snapshot.generated_at
    assert snapshot.cash == 6_000_000
    assert {position.symbol for position in snapshot.positions} == {"CCC", "DDD", "EEE"}


def test_submit_uses_threaded_runtime_snapshot_without_fixture_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    service = HarnessService()
    snapshot = MockBroker().get_positions("fixture-user")
    order_plan_id = _proposal_ready_for_submit(service, snapshot)

    def fail_if_fixture_snapshot_is_used(*args: object, **kwargs: object) -> PortfolioSnapshot:
        raise AssertionError("runtime submit path must not call fixture_portfolio_snapshot")

    monkeypatch.setattr("quantpilot.packages.core.harness_service.fixture_portfolio_snapshot", fail_if_fixture_snapshot_is_used)

    order_plan, broker_order, fills = service.submit_order_plan(order_plan_id, snapshot=snapshot)

    assert order_plan.risk_check_id is not None
    assert broker_order.order_plan_id == order_plan_id
    assert fills
    assert service.repositories.broker_orders.list()


def test_submit_rejects_fixture_snapshot_on_runtime_path() -> None:
    service = HarnessService()
    runtime_snapshot = MockBroker().get_positions("fixture-user")
    order_plan_id = _proposal_ready_for_submit(service, runtime_snapshot)

    with pytest.raises(RiskCheckRequired, match="fixture"):
        service.submit_order_plan(order_plan_id, snapshot=fixture_portfolio_snapshot())

    assert service.repositories.broker_orders.list() == []


def test_submit_rejects_stale_snapshot_metadata() -> None:
    service = HarnessService()
    snapshot = MockBroker().get_positions("fixture-user")
    order_plan_id = _proposal_ready_for_submit(service, snapshot)
    stale_snapshot = snapshot.model_copy(
        update={
            "as_of": utc_now() - timedelta(minutes=10),
            "is_stale": True,
            "stale_reason": "unit_test_stale_snapshot",
        }
    )

    with pytest.raises(RiskCheckRequired, match="stale"):
        service.submit_order_plan(order_plan_id, snapshot=stale_snapshot)

    assert service.repositories.broker_orders.list() == []


def test_submit_fails_closed_when_broker_snapshot_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    service = HarnessService()
    snapshot = MockBroker().get_positions("fixture-user")
    order_plan_id = _proposal_ready_for_submit(service, snapshot)

    monkeypatch.setattr(MockBroker, "get_positions", lambda self, user_id: None)

    with pytest.raises(RiskCheckRequired, match="missing"):
        service.submit_order_plan(order_plan_id)

    assert service.repositories.broker_orders.list() == []


def test_risk_check_records_snapshot_source_metadata() -> None:
    policy = UserPolicy()
    snapshot = MockBroker().get_positions(policy.user_id)
    order_plan = OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=OrderIntent(
            symbol="AAA",
            side="buy",
            quantity=100,
            limit_price=100,
            notional=10_000,
            target_weight=0.01,
            reason="unit test",
        ),
        idempotency_key="snapshot-source-risk",
    )

    risk = run_risk_check(policy=policy, order_plan=order_plan, snapshot=snapshot)

    assert risk.passed is True
    assert risk.snapshot_id == snapshot.snapshot_id
    assert risk.snapshot_source == "mock_broker"
    assert risk.snapshot_as_of == snapshot.as_of
    assert risk.snapshot_is_stale is False


def test_risk_check_rejects_stale_snapshot_metadata() -> None:
    policy = UserPolicy()
    snapshot = MockBroker().get_positions(policy.user_id).model_copy(
        update={"is_stale": True, "stale_reason": "unit_test_stale_snapshot"}
    )
    order_plan = OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=OrderIntent(
            symbol="AAA",
            side="buy",
            quantity=100,
            limit_price=100,
            notional=10_000,
            target_weight=0.01,
            reason="unit test",
        ),
        idempotency_key="snapshot-stale-risk",
    )

    risk = run_risk_check(policy=policy, order_plan=order_plan, snapshot=snapshot)

    assert risk.passed is False
    assert "portfolio_snapshot_not_stale" in risk.failed_checks
    assert risk.snapshot_is_stale is True
