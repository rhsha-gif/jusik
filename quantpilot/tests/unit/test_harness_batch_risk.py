from __future__ import annotations

from datetime import timedelta

import pytest

from quantpilot.packages.core.execution.state_machine import RiskCheckRequired
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionBinding,
    ManagedPositionState,
)
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    Fill,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    OrderType,
    PortfolioPosition,
    PortfolioSnapshot,
    ProposalExplanation,
    UserPolicy,
    utc_now,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


class _ControlledFillBroker:
    def __init__(
        self,
        fill_quantities: list[float],
        *,
        corruption: str | None = None,
    ) -> None:
        self.fill_quantities = fill_quantities
        self.corruption = corruption

    def submit_order(self, order_plan: OrderPlan) -> tuple[BrokerOrder, list[Fill]]:
        broker_order = BrokerOrder(
            broker_order_id="broker-controlled",
            order_plan_id=(
                "oplan-other"
                if self.corruption == "broker_order_plan"
                else order_plan.order_plan_id
            ),
            broker_mode=(
                BrokerMode.paper
                if self.corruption == "broker_mode"
                else BrokerMode.mock
            ),
            accepted_at=order_plan.intent.quote_time,
        )
        fills: list[Fill] = []
        for index, quantity in enumerate(self.fill_quantities):
            fills.append(
                Fill(
                    fill_id=(
                        "fill-duplicate"
                        if self.corruption == "duplicate_fill"
                        else f"fill-controlled-{index}"
                    ),
                    broker_order_id=(
                        "broker-other"
                        if self.corruption == "fill_broker_order"
                        else broker_order.broker_order_id
                    ),
                    order_plan_id=(
                        "oplan-other"
                        if self.corruption == "fill_order_plan"
                        else order_plan.order_plan_id
                    ),
                    symbol=(
                        "OTHER"
                        if self.corruption == "fill_symbol"
                        else order_plan.intent.symbol
                    ),
                    quantity=quantity,
                    price=100,
                    notional=(
                        quantity * 100 + 10
                        if self.corruption == "fill_notional"
                        else quantity * 100
                    ),
                    filled_at=order_plan.intent.quote_time,
                )
            )
        return broker_order, fills


def _approved_order(
    policy: UserPolicy,
    symbol: str,
    notional: float,
    *,
    side: str = "buy",
    purpose: str = "rebalance",
    target_weight: float | None = None,
) -> OrderPlan:
    intent = OrderIntent(
        symbol=symbol,
        side=side,
        order_type=OrderType.limit,
        quantity=notional / 100,
        limit_price=100,
        notional=notional,
        target_weight=(
            round(notional / 10_000_000, 6)
            if target_weight is None
            else target_weight
        ),
        reason="submit batch gate test",
    )
    key = f"idem-{symbol}"
    explanation = None
    if purpose != "rebalance":
        explanation = ProposalExplanation(
            symbol=symbol,
            action=side,
            quantity=intent.quantity,
            target_weight_delta=intent.target_weight - 0.10,
            reference_price=100,
            estimated_cash_impact=-notional,
            strategy_id="pullback_trend_v1",
            strategy_version="1.0",
            signal_reason="protective submit test",
            current_weight=0.10,
            target_weight=intent.target_weight,
            weight_delta=intent.target_weight - 0.10,
            quote_price=100,
            quote_age_seconds=0,
            estimated_notional=notional,
            idempotency_key=key,
            policy_version=policy.version,
        )
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=intent,
        purpose=purpose,
        status=OrderStatus.user_approved,
        idempotency_key=key,
        risk_check_id=f"risk-{symbol}",
        risk_check_expires_at=utc_now() + timedelta(minutes=10),
        explanation=explanation,
    )


def _risk_evidence(
    policy: UserPolicy,
    snapshot: PortfolioSnapshot,
    order: OrderPlan,
) -> tuple[ManagedPositionBinding, Quote]:
    position = next(
        position
        for position in snapshot.positions
        if position.symbol == order.intent.symbol
    )
    state = ManagedPositionState(
        policy_id=policy.policy_id,
        strategy_id="pullback_trend_v1",
        strategy_version="1.0",
        symbol=position.symbol,
        quantity=position.quantity,
        average_entry_price=100,
        atr14=2,
        active_stop=96,
        policy_version=policy.version,
        opened_at=snapshot.captured_at - timedelta(days=1),
        updated_at=snapshot.captured_at,
        reconciled_snapshot_id=snapshot.snapshot_id,
        reconciled_at=snapshot.captured_at,
    )
    return ManagedPositionBinding.from_position(state), Quote(
        symbol=order.intent.symbol,
        last=100,
        bid=100,
        as_of=order.intent.quote_time,
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
    assert blocked.status == OrderStatus.failed
    assert blocked.blocked_reason == "batch_risk_rejected"
    assert service.repositories.broker_orders.list() == []
    assert service.repositories.fills.list() == []


def test_protective_submit_is_isolated_from_failed_ordinary_buy_batch() -> None:
    service = HarnessService()
    policy = UserPolicy()
    service.repositories.policies.add(policy)
    failed_buy = _approved_order(policy, "AAA", 4_000_000)
    protective = _approved_order(
        policy,
        "CCC",
        500_000,
        side="sell",
        purpose="protective_exit",
        target_weight=0.05,
    )
    service.repositories.order_plans.add(failed_buy)
    service.repositories.order_plans.add(protective)
    snapshot = PortfolioSnapshot(
        cash=6_000_000,
        equity=10_000_000,
        positions=[
            PortfolioPosition(symbol="CCC", quantity=10_000, market_price=100, sector="tech"),
            PortfolioPosition(symbol="DDD", quantity=20_000, market_price=100, sector="tech"),
            PortfolioPosition(symbol="EEE", quantity=10_000, market_price=100, sector="industrial"),
        ],
        monthly_loss_ratio=-0.11,
    )

    binding, market_quote = _risk_evidence(policy, snapshot, protective)
    submitted, _, fills = service.submit_order_plan(
        protective.order_plan_id,
        snapshot=snapshot,
        position_binding=binding,
        market_quote=market_quote,
    )

    assert submitted.status == OrderStatus.filled
    assert fills
    assert failed_buy.status == OrderStatus.user_approved


@pytest.mark.parametrize(
    ("fill_quantities", "expected_status", "expected_reserved_quantity"),
    [
        ([], OrderStatus.accepted, 5_000),
        ([2_000], OrderStatus.partially_filled, 3_000),
        ([5_000], OrderStatus.filled, None),
    ],
)
def test_submit_status_and_sell_reservation_follow_filled_quantity(
    monkeypatch: pytest.MonkeyPatch,
    fill_quantities: list[float],
    expected_status: OrderStatus,
    expected_reserved_quantity: float | None,
) -> None:
    service = HarnessService()
    policy = UserPolicy()
    service.repositories.policies.add(policy)
    protective = _approved_order(
        policy,
        "CCC",
        500_000,
        side="sell",
        purpose="protective_exit",
        target_weight=0.05,
    )
    service.repositories.order_plans.add(protective)
    snapshot = PortfolioSnapshot(
        cash=9_000_000,
        equity=10_000_000,
        positions=[
            PortfolioPosition(symbol="CCC", quantity=10_000, market_price=100)
        ],
        captured_at=protective.intent.quote_time,
        source="broker_reconciled_test",
    )
    binding, market_quote = _risk_evidence(policy, snapshot, protective)
    monkeypatch.setattr(
        service,
        "_broker_for_policy",
        lambda _policy: _ControlledFillBroker(fill_quantities),
    )

    submitted, _, fills = service.submit_order_plan(
        protective.order_plan_id,
        snapshot=snapshot,
        position_binding=binding,
        market_quote=market_quote,
        now=protective.intent.quote_time,
    )

    assert submitted.status == expected_status
    assert sum(fill.quantity for fill in fills) == sum(fill_quantities)
    guardrail = service._guardrail_state(
        policy=policy,
        strategy_id="pullback_trend_v1",
        now=protective.intent.quote_time,
    )
    if expected_reserved_quantity is None:
        assert "CCC" not in guardrail.reserved_sell_quantities
    else:
        assert guardrail.reserved_sell_quantities == {
            "CCC": expected_reserved_quantity
        }
        assert guardrail.unfilled_order_keys == ["pullback_trend_v1:CCC:sell"]


@pytest.mark.parametrize(
    ("fill_quantities", "corruption", "failure_code"),
    [
        ([6_000], None, "aggregate_fill_quantity_exceeded"),
        ([2_000], "broker_order_plan", "broker_order_plan_mismatch"),
        ([2_000], "broker_mode", "broker_mode_mismatch"),
        ([2_000], "fill_order_plan", "fill_order_plan_mismatch"),
        ([2_000], "fill_broker_order", "fill_broker_order_mismatch"),
        ([2_000], "fill_symbol", "fill_symbol_mismatch"),
        ([2_000], "fill_notional", "fill_notional_mismatch"),
        ([1_000, 1_000], "duplicate_fill", "duplicate_fill_id"),
    ],
)
def test_invalid_broker_fill_evidence_fails_closed_with_full_reservation(
    monkeypatch: pytest.MonkeyPatch,
    fill_quantities: list[float],
    corruption: str | None,
    failure_code: str,
) -> None:
    service = HarnessService()
    policy = UserPolicy()
    service.repositories.policies.add(policy)
    protective = _approved_order(
        policy,
        "CCC",
        500_000,
        side="sell",
        purpose="protective_exit",
        target_weight=0.05,
    )
    service.repositories.order_plans.add(protective)
    snapshot = PortfolioSnapshot(
        cash=9_000_000,
        equity=10_000_000,
        positions=[
            PortfolioPosition(symbol="CCC", quantity=10_000, market_price=100)
        ],
        captured_at=protective.intent.quote_time,
        source="broker_reconciled_test",
    )
    binding, market_quote = _risk_evidence(policy, snapshot, protective)
    monkeypatch.setattr(
        service,
        "_broker_for_policy",
        lambda _policy: _ControlledFillBroker(
            fill_quantities,
            corruption=corruption,
        ),
    )

    with pytest.raises(RuntimeError, match=failure_code):
        service.submit_order_plan(
            protective.order_plan_id,
            snapshot=snapshot,
            position_binding=binding,
            market_quote=market_quote,
            now=protective.intent.quote_time,
        )

    unresolved = service.repositories.order_plans.require(
        protective.order_plan_id
    )
    assert unresolved.status == OrderStatus.submitted
    assert unresolved.blocked_reason == "broker_submission_evidence_invalid"
    assert service.repositories.broker_orders.list() == []
    assert service.repositories.fills.list() == []
    guardrail = service._guardrail_state(
        policy=policy,
        strategy_id="pullback_trend_v1",
        now=protective.intent.quote_time,
    )
    assert guardrail.reserved_sell_quantities == {"CCC": 5_000}
    assert guardrail.unfilled_order_keys == ["pullback_trend_v1:CCC:sell"]


def test_terminal_orders_never_reenter_submit_batch() -> None:
    service = HarnessService()
    policy = UserPolicy()
    filled = _approved_order(policy, "AAA", 100_000).model_copy(
        update={"status": OrderStatus.filled}
    )
    current = _approved_order(policy, "BBB", 100_000)
    service.repositories.order_plans.add(filled)
    service.repositories.order_plans.add(current)

    batch = service._orders_for_submit_batch(current)

    assert [order.order_plan_id for order in batch] == [current.order_plan_id]


@pytest.mark.parametrize(
    "purpose",
    ["protective_exit", "strategy_retirement"],
)
@pytest.mark.parametrize(
    "status",
    [OrderStatus.filled, OrderStatus.cancelled, OrderStatus.failed],
)
def test_terminal_liquidation_orders_never_reenter_submit_batch(
    purpose: str,
    status: OrderStatus,
) -> None:
    service = HarnessService()
    policy = UserPolicy()
    terminal = _approved_order(
        policy,
        "CCC",
        100_000,
        side="sell",
        purpose=purpose,
    ).model_copy(update={"status": status})

    assert service._orders_for_submit_batch(terminal) == []


def test_submit_rechecks_against_supplied_broker_snapshot_not_fixture() -> None:
    service = HarnessService()
    policy = UserPolicy()
    service.repositories.policies.add(policy)
    order = _approved_order(policy, "AAA", 500_000)
    service.repositories.order_plans.add(order)
    no_cash_snapshot = PortfolioSnapshot(
        cash=0,
        equity=10_000_000,
        positions=[
            PortfolioPosition(
                symbol="EXISTING",
                quantity=100_000,
                market_price=100,
                sector="other",
            )
        ],
        source="broker_reconciled_test",
    )

    with pytest.raises(RiskCheckRequired, match="fresh risk check failed"):
        service.submit_order_plan(order.order_plan_id, snapshot=no_cash_snapshot)

    assert service.repositories.broker_orders.list() == []


def test_submit_rehydrates_durable_safety_state_before_broker_call(tmp_path) -> None:
    with PaperStateStore(tmp_path / "state.sqlite3") as state_store:
        submitting = HarnessService()
        safety_writer = HarnessService()
        submitting.operator_safety_state_provider = state_store
        safety_writer.operator_safety_state_provider = state_store
        policy = UserPolicy()
        submitting.repositories.policies.add(policy)
        order = _approved_order(policy, "AAA", 500_000)
        submitting.repositories.order_plans.add(order)
        observed_statuses: list[OrderStatus] = []

        def pause_after_risk_checks(submitted: OrderPlan) -> None:
            observed_statuses.append(submitted.status)
            safety_writer.record_broker_health(
                policy_id=policy.policy_id,
                healthy=False,
                reason="concurrent_broker_failure",
            )

        with pytest.raises(
            RiskCheckRequired,
            match="final submission safety gate failed",
        ):
            submitting.submit_order_plan(
                order.order_plan_id,
                before_broker_submit=pause_after_risk_checks,
            )

        blocked = submitting.repositories.order_plans.require(order.order_plan_id)
        durable_state = state_store.load_operator_safety_state(policy.policy_id)
        assert observed_statuses == [OrderStatus.submitted]
        assert durable_state is not None
        assert durable_state.autopilot_paused is True
        assert durable_state.broker_healthy is False
        assert blocked.status == OrderStatus.failed
        assert blocked.blocked_reason == "final_submission_safety_gate_failed"
        assert submitting.repositories.broker_orders.list() == []
        assert submitting.repositories.fills.list() == []


def test_prebroker_guard_failure_terminalizes_and_reraises_original_error() -> None:
    service = HarnessService()
    policy = UserPolicy()
    service.repositories.policies.add(policy)
    order = _approved_order(policy, "AAA", 500_000)
    service.repositories.order_plans.add(order)

    def reject_submission(_order: OrderPlan) -> None:
        raise RiskCheckRequired("weekly lease ownership changed")

    with pytest.raises(RiskCheckRequired, match="lease ownership changed"):
        service.submit_order_plan(
            order.order_plan_id,
            before_broker_submit=reject_submission,
        )

    blocked = service.repositories.order_plans.require(order.order_plan_id)
    assert blocked.status == OrderStatus.failed
    assert blocked.blocked_reason == "prebroker_submission_guard_failed"
    assert service.repositories.broker_orders.list() == []
    assert service.repositories.fills.list() == []
    assert "prebroker_submission_guard_failed" in {
        event.action for event in service.repositories.audit_logs.list()
    }


def test_pause_and_resume_work_without_durable_state_provider() -> None:
    service = HarnessService()
    policy = UserPolicy()
    service.repositories.policies.add(policy)

    service.pause_guarded_autopilot(policy_id=policy.policy_id)
    assert service.autopilot_paused is True
    service.resume_guarded_autopilot(policy_id=policy.policy_id)

    assert service.autopilot_paused is False
    assert service.broker_healthy is True
    assert service.last_blocked_reason is None
