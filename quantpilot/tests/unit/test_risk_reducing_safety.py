from __future__ import annotations

from datetime import datetime, timedelta
from math import inf, nan
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.execution.state_machine import (
    RiskCheckRequired,
    authorize_level5,
)
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionBinding,
    ManagedPositionState,
    PendingLiquidationCheckpoint,
)
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    Fill,
    GuardrailState,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    OrderType,
    PortfolioPlan,
    PortfolioPosition,
    PortfolioSnapshot,
    ProposalExplanation,
    UserPolicy,
)
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.registry import StrategyRegistryEntry
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


NOW = datetime(2026, 7, 10, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def _policy(**updates: object) -> UserPolicy:
    values: dict[str, object] = {
        "execution_mode": ExecutionMode.fully_automated,
        "broker": BrokerMode.mock,
        "authority_level": 5,
        "fully_automated_operator_enabled": True,
        "single_order_cash_limit": 2_000_000,
        "max_daily_turnover": 5_000_000,
    }
    values.update(updates)
    return UserPolicy(**values)


def _snapshot(
    policy: UserPolicy,
    *,
    quantity: float = 10_000,
    market_price: float = 100,
    monthly_loss_ratio: float = -0.11,
    captured_at: datetime = NOW,
    user_id: str | None = None,
) -> PortfolioSnapshot:
    value = quantity * market_price
    return PortfolioSnapshot(
        user_id=user_id or policy.user_id,
        cash=10_000_000 - value,
        equity=10_000_000,
        positions=[
            PortfolioPosition(
                symbol="CCC",
                quantity=quantity,
                market_price=market_price,
                sector="tech",
            )
        ],
        monthly_loss_ratio=monthly_loss_ratio,
        captured_at=captured_at,
        source="reconciled_safety_test",
    )


def _order(
    policy: UserPolicy,
    *,
    quantity: float,
    limit_price: float,
    purpose: str = "protective_exit",
    symbol: str = "CCC",
) -> OrderPlan:
    notional = quantity * limit_price
    key = f"risk-{symbol}-{quantity}-{limit_price}-{purpose}"
    intent = OrderIntent(
        symbol=symbol,
        side="sell",
        order_type=OrderType.limit,
        quantity=quantity,
        limit_price=limit_price,
        notional=notional,
        target_weight=max(0.0, 0.10 - notional / 10_000_000),
        reason="risk safety test",
        quote_time=NOW,
    )
    explanation = None
    if purpose != "rebalance":
        explanation = ProposalExplanation(
            symbol=symbol,
            action="sell",
            quantity=quantity,
            target_weight_delta=-notional / 10_000_000,
            reference_price=limit_price,
            estimated_cash_impact=-notional,
            strategy_id="pullback_trend_v2",
            strategy_version="2.0",
            signal_reason="protective stop",
            current_weight=0.10,
            target_weight=intent.target_weight,
            weight_delta=intent.target_weight - 0.10,
            quote_price=limit_price,
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
        idempotency_key=key,
        explanation=explanation,
    )


def _binding(
    policy: UserPolicy,
    snapshot: PortfolioSnapshot,
    *,
    quantity: float | None = None,
    symbol: str = "CCC",
) -> ManagedPositionBinding:
    position = ManagedPositionState(
        policy_id=policy.policy_id,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol=symbol,
        quantity=quantity or snapshot.positions[0].quantity,
        average_entry_price=100,
        atr14=5,
        active_stop=92,
        policy_version=policy.version,
        opened_at=NOW - timedelta(days=1),
        updated_at=snapshot.captured_at,
        reconciled_snapshot_id=snapshot.snapshot_id,
        reconciled_at=snapshot.captured_at,
    )
    return ManagedPositionBinding.from_position(position)


def _plan(policy: UserPolicy, orders: list[OrderPlan]) -> PortfolioPlan:
    return PortfolioPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        target_weights={},
        cash_target_weight=0,
        order_intents=[order.intent for order in orders],
        created_at=NOW,
    )


def test_low_limit_ordinary_sell_cannot_exceed_reconciled_shares() -> None:
    policy = _policy()
    snapshot = _snapshot(policy)
    order = _order(
        policy,
        quantity=15_000,
        limit_price=50,
        purpose="rebalance",
    )

    risk = run_risk_check(
        policy=policy,
        order_plan=order,
        snapshot=snapshot,
        now=NOW,
    )

    assert not risk.passed
    assert "no_short_sell" in risk.failed_checks


def test_final_submission_rejects_low_limit_ordinary_oversell_before_broker() -> None:
    policy = _policy()
    snapshot = _snapshot(policy, monthly_loss_ratio=0)
    order = _order(
        policy,
        quantity=15_000,
        limit_price=50,
        purpose="rebalance",
    ).model_copy(
        update={
            "status": OrderStatus.user_approved,
            "risk_check_id": "risk-prechecked",
            "risk_check_expires_at": NOW + timedelta(minutes=5),
        }
    )
    harness = HarnessService()
    harness.repositories.policies.add(policy)
    harness.repositories.order_plans.add(order)

    with pytest.raises(RiskCheckRequired, match="fresh risk check failed"):
        harness.submit_order_plan(order.order_plan_id, snapshot=snapshot, now=NOW)
    assert harness.repositories.broker_orders.list() == []


@pytest.mark.parametrize(
    ("gate", "failed_check"),
    [
        ("operator_paused", "operator_not_paused"),
        ("broker_unhealthy", "broker_health"),
        ("operator_kill_switch", "operator_kill_switch_not_engaged"),
        ("live_trading_flag", "live_trading_disabled"),
    ],
)
def test_final_submission_rechecks_runtime_safety_gates_before_broker(
    monkeypatch,
    gate: str,
    failed_check: str,
) -> None:
    monkeypatch.setenv("OPERATOR_KILL_SWITCH", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    policy = _policy()
    snapshot = _snapshot(policy)
    order = _order(
        policy,
        quantity=1_000,
        limit_price=100,
    ).model_copy(
        update={
            "status": OrderStatus.user_approved,
            "risk_check_id": "risk-prechecked",
            "risk_check_expires_at": NOW + timedelta(minutes=5),
        }
    )
    harness = HarnessService()
    harness.repositories.policies.add(policy)
    harness.repositories.order_plans.add(order)
    if gate == "operator_paused":
        harness.autopilot_paused = True
    elif gate == "broker_unhealthy":
        harness.broker_healthy = False
        assert not harness._guardrail_state(
            policy=policy,
            strategy_id="pullback_trend_v2",
            now=NOW,
        ).broker_healthy
    elif gate == "operator_kill_switch":
        monkeypatch.setenv("OPERATOR_KILL_SWITCH", "true")
    else:
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")

    with pytest.raises(RiskCheckRequired, match="final submission safety gate failed"):
        harness.submit_order_plan(
            order.order_plan_id,
            snapshot=snapshot,
            position_binding=_binding(policy, snapshot),
            market_quote=Quote(symbol="CCC", last=100, bid=100, as_of=NOW),
            now=NOW,
        )

    assert harness.repositories.broker_orders.list() == []
    assert harness.repositories.fills.list() == []
    assert harness.repositories.order_plans.require(order.order_plan_id).status == (
        OrderStatus.failed
    )
    risk_event = next(
        event
        for event in reversed(harness.repositories.audit_logs.list())
        if event.action == "risk_check_failed"
    )
    assert risk_event.after_state == {"failed_checks": [failed_check]}


def test_risk_reducing_snapshot_uses_quote_freshness_window_at_authority_and_submit() -> None:
    policy = _policy(stale_quote_max_age_seconds=30)
    snapshot = _snapshot(policy, captured_at=NOW - timedelta(seconds=31))
    binding = _binding(policy, snapshot)
    quote = Quote(symbol="CCC", last=100, bid=100, as_of=NOW)
    order = _order(
        policy,
        quantity=1_000,
        limit_price=100,
    ).model_copy(
        update={
            "status": OrderStatus.user_approved,
            "risk_check_id": "risk-prechecked",
            "risk_check_expires_at": NOW + timedelta(minutes=5),
        }
    )
    entry = StrategyRegistryEntry(
        strategy_id="pullback_trend_v2",
        version="2.0",
        status="validated_l5",
        allowed_execution_levels=["level_5", "fully_automated"],
    )

    authority = authorize_level5(
        order_plan=order,
        policy=policy,
        registry_entry=entry,
        strategy=load_strategy_recipe("pullback_trend_v2"),
        snapshot=snapshot,
        state=GuardrailState(),
        position_binding=binding,
        market_quote=quote,
        now=NOW,
    )
    assert not authority.authorized
    assert authority.first_failed_check == "snapshot_not_stale"

    harness = HarnessService()
    harness.repositories.policies.add(policy)
    harness.repositories.order_plans.add(order)
    with pytest.raises(RiskCheckRequired, match="fresh risk check failed"):
        harness.submit_order_plan(
            order.order_plan_id,
            snapshot=snapshot,
            position_binding=binding,
            market_quote=quote,
            now=NOW,
        )
    assert harness.repositories.broker_orders.list() == []


def test_inconsistent_limit_notional_fails_single_batch_and_final_submit() -> None:
    policy = _policy()
    snapshot = _snapshot(policy, monthly_loss_ratio=0)
    order = OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=OrderIntent(
            symbol="AAA",
            side="buy",
            order_type=OrderType.limit,
            quantity=100_000,
            limit_price=100,
            notional=100,
            target_weight=0.01,
            reason="forged notional",
            quote_time=NOW,
        ),
        status=OrderStatus.user_approved,
        idempotency_key="forged-notional",
        risk_check_id="risk-forged",
        risk_check_expires_at=NOW + timedelta(minutes=5),
    )
    single = run_risk_check(
        policy=policy,
        order_plan=order,
        snapshot=snapshot,
        now=NOW,
    )
    batch = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, [order]),
        snapshot=snapshot,
        order_plans=[order],
        now=NOW,
    )
    harness = HarnessService()
    harness.repositories.policies.add(policy)
    harness.repositories.order_plans.add(order)

    assert "order_notional_matches_quantity_price" in single.failed_checks
    assert "order_notional_matches_quantity_price" in batch.failed_checks
    with pytest.raises(RiskCheckRequired, match="fresh risk check failed"):
        harness.submit_order_plan(order.order_plan_id, snapshot=snapshot, now=NOW)
    assert harness.repositories.fills.list() == []


def test_batch_aggregates_two_individually_valid_protective_sells() -> None:
    policy = _policy()
    snapshot = _snapshot(policy)
    first = _order(policy, quantity=6_000, limit_price=50)
    second = _order(policy, quantity=6_000, limit_price=50).model_copy(
        update={
            "order_plan_id": "oplan-second",
            "idempotency_key": "risk-second",
        }
    )
    binding = _binding(policy, snapshot)
    quote = Quote(symbol="CCC", last=100, bid=50, as_of=NOW)

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, [first, second]),
        snapshot=snapshot,
        order_plans=[first, second],
        position_bindings={
            first.order_plan_id: binding,
            second.order_plan_id: binding,
        },
        market_quotes={
            first.order_plan_id: quote,
            second.order_plan_id: quote,
        },
        now=NOW,
    )

    assert not decision.passed
    assert "no_short_sell_after_batch" in decision.failed_checks


def test_reserved_sell_quantity_and_overweight_reduction_are_enforced() -> None:
    policy = _policy(max_position_weight=0.15, max_sector_weight=0.20)
    snapshot = _snapshot(policy, quantity=30_000)
    partial = _order(policy, quantity=5_000, limit_price=100)
    binding = _binding(policy, snapshot)
    quote = Quote(symbol="CCC", last=100, bid=100, as_of=NOW)

    allowed = run_risk_check(
        policy=policy,
        order_plan=partial,
        snapshot=snapshot,
        position_binding=binding,
        market_quote=quote,
        now=NOW,
    )
    reserved = run_risk_check(
        policy=policy,
        order_plan=_order(policy, quantity=26_000, limit_price=50),
        snapshot=snapshot,
        position_binding=binding,
        market_quote=Quote(symbol="CCC", last=100, bid=50, as_of=NOW),
        guardrail_state=GuardrailState(reserved_sell_quantities={"ccc": 5_000}),
        now=NOW,
    )

    assert allowed.passed
    assert not reserved.passed
    assert "no_short_sell" in reserved.failed_checks


def test_risk_exception_requires_exact_trusted_bid_and_strategy_attribution() -> None:
    policy = _policy()
    snapshot = _snapshot(policy)
    order = _order(policy, quantity=5_000, limit_price=100)
    binding = _binding(policy, snapshot)

    mismatched_bid = run_risk_check(
        policy=policy,
        order_plan=order,
        snapshot=snapshot,
        position_binding=binding,
        market_quote=Quote(symbol="CCC", last=100, bid=99, as_of=NOW),
        now=NOW,
    )
    unrelated = run_risk_check(
        policy=policy,
        order_plan=order,
        snapshot=snapshot,
        position_binding=binding.model_copy(update={"symbol": "UNRELATED"}),
        market_quote=Quote(symbol="CCC", last=100, bid=100, as_of=NOW),
        now=NOW,
    )

    assert "risk_reducing_purpose_verified" in mismatched_bid.failed_checks
    assert "risk_reducing_purpose_verified" in unrelated.failed_checks


def test_stale_naive_and_cross_identity_snapshots_fail_closed() -> None:
    policy = _policy()
    order = _order(policy, quantity=1_000, limit_price=100, purpose="rebalance")
    entry = StrategyRegistryEntry(
        strategy_id="pullback_trend_v2",
        version="2.0",
        status="validated_l5",
        allowed_execution_levels=["level_5", "fully_automated"],
    )
    stale = _snapshot(policy, captured_at=NOW - timedelta(hours=1))
    naive = _snapshot(policy, captured_at=NOW.replace(tzinfo=None))
    future = _snapshot(policy, captured_at=NOW + timedelta(seconds=1))
    cross_user = _snapshot(policy, user_id="another-user")

    for snapshot in (stale, naive, future):
        risk = run_risk_check(
            policy=policy,
            order_plan=order,
            snapshot=snapshot,
            now=NOW,
        )
        batch = run_batch_risk_gate(
            policy=policy,
            portfolio_plan=_plan(policy, [order]),
            snapshot=snapshot,
            order_plans=[order],
            now=NOW,
        )
        authority = authorize_level5(
            order_plan=order,
            policy=policy,
            registry_entry=entry,
            strategy=load_strategy_recipe("pullback_trend_v2"),
            snapshot=snapshot,
            now=NOW,
        )
        assert "snapshot_not_stale" in risk.failed_checks
        assert "snapshot_not_stale" in batch.failed_checks
        assert authority.first_failed_check == "snapshot_not_stale"

    cross = run_risk_check(
        policy=policy,
        order_plan=order,
        snapshot=cross_user,
        now=NOW,
    )
    assert "policy_identity_match" in cross.failed_checks


def test_level5_rejects_registry_recipe_version_mismatch() -> None:
    policy = _policy()
    snapshot = _snapshot(policy, monthly_loss_ratio=0)
    order = _order(
        policy,
        quantity=1_000,
        limit_price=100,
        purpose="rebalance",
    )
    result = authorize_level5(
        order_plan=order,
        policy=policy,
        registry_entry=StrategyRegistryEntry(
            strategy_id="pullback_trend_v2",
            version="999.0",
            status="validated_l5",
            allowed_execution_levels=["level_5", "fully_automated"],
        ),
        strategy=load_strategy_recipe("pullback_trend_v2"),
        snapshot=snapshot,
        now=NOW,
    )
    assert not result.authorized
    assert result.first_failed_check == "strategy_recipe_matches_registry"


def test_expired_unsubmitted_orders_do_not_reserve_or_consume_daily_limits() -> None:
    policy = _policy()
    harness = HarnessService()
    expired = _order(
        policy,
        quantity=10_000,
        limit_price=100,
        purpose="rebalance",
    ).model_copy(
        update={
            "status": OrderStatus.proposed,
            "risk_check_id": "risk-expired",
            "risk_check_expires_at": NOW - timedelta(seconds=1),
        }
    )
    old_filled = _order(
        policy,
        quantity=1,
        limit_price=100,
        purpose="rebalance",
    ).model_copy(
        update={
            "order_plan_id": "oplan-old-filled",
            "idempotency_key": "old-filled",
            "status": OrderStatus.filled,
            "created_at": NOW - timedelta(days=1),
        }
    )
    harness.repositories.order_plans.add(expired)
    harness.repositories.order_plans.add(old_filled)

    state = harness._guardrail_state(
        policy=policy,
        strategy_id="pullback_trend_v2",
        now=NOW,
    )

    assert state.reserved_sell_quantities == {}
    assert state.unfilled_order_keys == []
    assert state.daily_order_count == 0
    assert state.daily_turnover_used == 0


def test_expired_post_submission_orders_keep_unfilled_sell_quantity_reserved() -> None:
    policy = _policy()
    harness = HarnessService()
    quantities = {
        OrderStatus.submitted: 1_000,
        OrderStatus.accepted: 2_000,
        OrderStatus.partially_filled: 3_000,
    }
    orders: dict[OrderStatus, OrderPlan] = {}
    for index, (status, quantity) in enumerate(quantities.items(), start=1):
        order = _order(
            policy,
            quantity=quantity,
            limit_price=100,
            purpose="rebalance",
        ).model_copy(
            update={
                "order_plan_id": f"oplan-active-{index}",
                "idempotency_key": f"active-{index}",
                "status": status,
                "expires_at": NOW - timedelta(minutes=1),
                "risk_check_expires_at": NOW - timedelta(minutes=1),
                "created_at": NOW,
            }
        )
        harness.repositories.order_plans.add(order)
        orders[status] = order
    partially_filled = orders[OrderStatus.partially_filled]
    harness.repositories.fills.add(
        Fill(
            broker_order_id="broker-partial",
            order_plan_id=partially_filled.order_plan_id,
            symbol="CCC",
            quantity=1_200,
            price=100,
            notional=120_000,
            filled_at=NOW,
        )
    )

    state = harness._guardrail_state(
        policy=policy,
        strategy_id="pullback_trend_v2",
        now=NOW,
    )

    assert state.reserved_sell_quantities == {"CCC": 4_800}
    assert state.unfilled_order_keys == ["pullback_trend_v2:CCC:sell"]
    assert state.daily_order_count == 3
    assert state.daily_turnover_used == 600_000


def test_durable_pending_liquidation_reserves_quantity_after_restart(tmp_path) -> None:
    policy = _policy()
    snapshot = _snapshot(policy, monthly_loss_ratio=0)
    checkpoint = PendingLiquidationCheckpoint(
        order_plan_id="oplan-durable-pending",
        policy_id=policy.policy_id,
        policy_version=policy.version,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol="CCC",
        purpose="protective_exit",
        idempotency_key="sha256:" + "d" * 64,
        quantity_before=10_000,
        quantity_requested=6_000,
        expected_quantity_after=4_000,
        account_quantity_before=10_000,
        expected_account_quantity_after=4_000,
        limit_price=100,
        quote_as_of=NOW,
        reconciled_snapshot_id=snapshot.snapshot_id,
        status="prepared",
        created_at=NOW,
        updated_at=NOW,
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.insert_pending_liquidation(checkpoint)
        harness = HarnessService(pending_liquidation_provider=store)
        harness.repositories.policies.add(policy)
        new_sell = _order(
            policy,
            quantity=5_000,
            limit_price=100,
            purpose="rebalance",
        ).model_copy(
            update={
                "status": OrderStatus.user_approved,
                "risk_check_id": "risk-new-sell",
                "risk_check_expires_at": NOW + timedelta(minutes=5),
            }
        )
        harness.repositories.order_plans.add(new_sell)

        with pytest.raises(RiskCheckRequired, match="fresh risk check failed"):
            harness.submit_order_plan(
                new_sell.order_plan_id,
                snapshot=snapshot,
                now=NOW,
            )
        assert harness.repositories.broker_orders.list() == []


def test_filled_unreconciled_liquidation_fences_stale_snapshot_sell(tmp_path) -> None:
    policy = _policy()
    snapshot = _snapshot(policy, monthly_loss_ratio=0).model_copy(
        update={"captured_at": NOW + timedelta(seconds=1)}
    )
    prepared = PendingLiquidationCheckpoint(
        order_plan_id="oplan-filled-unreconciled",
        policy_id=policy.policy_id,
        policy_version=policy.version,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol="CCC",
        purpose="protective_exit",
        idempotency_key="sha256:" + "f" * 64,
        quantity_before=10_000,
        quantity_requested=6_000,
        expected_quantity_after=4_000,
        account_quantity_before=10_000,
        expected_account_quantity_after=4_000,
        limit_price=100,
        quote_as_of=NOW,
        reconciled_snapshot_id="pre-fill-snapshot",
        created_at=NOW,
        updated_at=NOW,
    )
    fill = Fill(
        fill_id="fill-unreconciled",
        broker_order_id="broker-unreconciled",
        order_plan_id=prepared.order_plan_id,
        symbol="CCC",
        quantity=6_000,
        price=100,
        notional=600_000,
        filled_at=NOW,
    )
    filled = prepared.model_copy(
        update={
            "status": "filled",
            "broker_submission_attempted": True,
            "risk_check_id": "risk-unreconciled",
            "broker_order_id": fill.broker_order_id,
            "cumulative_filled_quantity": 6_000,
            "fill_ids": [fill.fill_id],
            "fill_evidence": [fill],
            "updated_at": NOW + timedelta(microseconds=1),
            "revision": 1,
        }
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.insert_pending_liquidation(prepared)
        store.update_pending_liquidation(filled)
        harness = HarnessService(pending_liquidation_provider=store)
        harness.repositories.policies.add(policy)
        new_sell = _order(
            policy,
            quantity=5_000,
            limit_price=100,
            purpose="rebalance",
        ).model_copy(
            update={
                "status": OrderStatus.user_approved,
                "risk_check_id": "risk-second-sell",
                "risk_check_expires_at": NOW + timedelta(minutes=5),
            }
        )
        harness.repositories.order_plans.add(new_sell)

        with pytest.raises(RiskCheckRequired, match="fresh risk check failed"):
            harness.submit_order_plan(
                new_sell.order_plan_id,
                snapshot=snapshot,
                now=NOW + timedelta(seconds=1),
            )

        assert harness._guardrail_state(
            policy=policy,
            strategy_id="pullback_trend_v2",
            now=NOW + timedelta(seconds=1),
        ).reserved_sell_quantities == {"CCC": 6_000}
        assert harness.repositories.broker_orders.list() == []


def test_prepared_checkpoint_releases_expired_unsubmitted_repository_order() -> None:
    policy = _policy()
    snapshot = _snapshot(policy, monthly_loss_ratio=0)
    checkpoint = PendingLiquidationCheckpoint(
        order_plan_id="oplan-expired-prepared",
        policy_id=policy.policy_id,
        policy_version=policy.version,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol="CCC",
        purpose="protective_exit",
        idempotency_key="sha256:" + "a" * 64,
        quantity_before=10_000,
        quantity_requested=6_000,
        expected_quantity_after=4_000,
        account_quantity_before=10_000,
        expected_account_quantity_after=4_000,
        limit_price=100,
        quote_as_of=NOW - timedelta(minutes=2),
        reconciled_snapshot_id=snapshot.snapshot_id,
        status="prepared",
        created_at=NOW - timedelta(minutes=2),
        updated_at=NOW - timedelta(minutes=2),
    )

    class PreparedCheckpointProvider:
        def list_pending_liquidations(
            self,
            *,
            include_reconciled: bool = False,
        ) -> list[PendingLiquidationCheckpoint]:
            return [checkpoint]

    harness = HarnessService(
        pending_liquidation_provider=PreparedCheckpointProvider()
    )
    expired_order = _order(
        policy,
        quantity=6_000,
        limit_price=100,
        purpose="protective_exit",
    ).model_copy(
        update={
            "order_plan_id": checkpoint.order_plan_id,
            "status": OrderStatus.user_approved,
            "risk_check_id": "risk-expired-prepared",
            "risk_check_expires_at": NOW - timedelta(seconds=1),
        }
    )
    harness.repositories.order_plans.add(expired_order)

    state = harness._guardrail_state(
        policy=policy,
        strategy_id="pullback_trend_v2",
        now=NOW,
    )

    assert state.reserved_sell_quantities == {}
    assert state.unfilled_order_keys == []


def test_reconciled_durable_liquidation_counts_today_without_reserving(
    tmp_path,
) -> None:
    policy = _policy()
    snapshot = _snapshot(policy, monthly_loss_ratio=0)

    def reconciled_checkpoint(
        *,
        order_plan_id: str,
        idempotency_key: str,
        created_at: datetime,
    ) -> PendingLiquidationCheckpoint:
        prepared = PendingLiquidationCheckpoint(
            order_plan_id=order_plan_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            strategy_id="pullback_trend_v2",
            strategy_version="2.0",
            symbol="CCC",
            purpose="protective_exit",
            idempotency_key=idempotency_key,
            quantity_before=10_000,
            quantity_requested=6_000,
            expected_quantity_after=4_000,
            account_quantity_before=10_000,
            expected_account_quantity_after=4_000,
            limit_price=100,
            quote_as_of=created_at,
            reconciled_snapshot_id=snapshot.snapshot_id,
            status="prepared",
            created_at=created_at,
            updated_at=created_at,
        )
        fill = Fill(
            fill_id=f"fill-{order_plan_id}",
            broker_order_id=f"broker-{order_plan_id}",
            order_plan_id=order_plan_id,
            symbol="CCC",
            quantity=6_000,
            price=100,
            notional=600_000,
            filled_at=created_at + timedelta(microseconds=1),
        )
        return PendingLiquidationCheckpoint.model_validate(
            prepared.model_copy(
                update={
                    "status": "reconciled",
                    "broker_submission_attempted": True,
                    "risk_check_id": f"risk-{order_plan_id}",
                    "broker_order_id": fill.broker_order_id,
                    "cumulative_filled_quantity": 6_000,
                    "fill_ids": [fill.fill_id],
                    "fill_evidence": [fill],
                    "updated_at": created_at + timedelta(microseconds=2),
                    "revision": 2,
                }
            ).model_dump()
        )

    today = reconciled_checkpoint(
        order_plan_id="oplan-reconciled-today",
        idempotency_key="sha256:" + "e" * 64,
        created_at=NOW,
    )
    yesterday = reconciled_checkpoint(
        order_plan_id="oplan-reconciled-yesterday",
        idempotency_key="sha256:" + "f" * 64,
        created_at=NOW - timedelta(days=1),
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        for checkpoint in (today, yesterday):
            prepared = checkpoint.model_copy(
                update={
                    "status": "prepared",
                    "cumulative_filled_quantity": 0,
                    "fill_ids": [],
                    "fill_evidence": [],
                    "broker_submission_attempted": False,
                    "broker_order_id": None,
                    "risk_check_id": None,
                    "updated_at": checkpoint.created_at,
                    "revision": 0,
                }
            )
            store.insert_pending_liquidation(
                PendingLiquidationCheckpoint.model_validate(prepared.model_dump())
            )
            filled = checkpoint.model_copy(
                update={
                    "status": "filled",
                    "updated_at": checkpoint.created_at
                    + timedelta(microseconds=1),
                    "revision": 1,
                }
            )
            store.update_pending_liquidation(
                PendingLiquidationCheckpoint.model_validate(filled.model_dump())
            )
            store.update_pending_liquidation(checkpoint)
        harness = HarnessService(pending_liquidation_provider=store)

        state = harness._guardrail_state(
            policy=policy,
            strategy_id="pullback_trend_v2",
            now=NOW,
        )

    assert state.reserved_sell_quantities == {}
    assert state.unfilled_order_keys == []
    assert state.daily_order_count == 1
    assert state.daily_turnover_used == 600_000
    assert state.submitted_idempotency_keys == [today.idempotency_key]


def test_reconciled_prebroker_failure_does_not_consume_daily_limits(tmp_path) -> None:
    policy = _policy()
    snapshot = _snapshot(policy, monthly_loss_ratio=0)
    prepared = PendingLiquidationCheckpoint(
        order_plan_id="oplan-prebroker-failed",
        policy_id=policy.policy_id,
        policy_version=policy.version,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol="CCC",
        purpose="protective_exit",
        idempotency_key="sha256:" + "9" * 64,
        quantity_before=10_000,
        quantity_requested=4,
        expected_quantity_after=9_996,
        account_quantity_before=10_000,
        expected_account_quantity_after=9_996,
        limit_price=99,
        quote_as_of=NOW,
        reconciled_snapshot_id=snapshot.snapshot_id,
        created_at=NOW,
        updated_at=NOW,
    )
    failed = prepared.model_copy(
        update={
            "status": "failed",
            "last_error_code": "RiskCheckRequired",
            "updated_at": NOW + timedelta(microseconds=1),
            "revision": 1,
        }
    )
    reconciled = failed.model_copy(
        update={
            "status": "reconciled",
            "updated_at": NOW + timedelta(microseconds=2),
            "revision": 2,
        }
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.insert_pending_liquidation(prepared)
        store.update_pending_liquidation(failed)
        store.update_pending_liquidation(reconciled)
        state = HarnessService(
            pending_liquidation_provider=store
        )._guardrail_state(
            policy=policy,
            strategy_id="pullback_trend_v2",
            now=NOW,
        )

    assert state.daily_order_count == 0
    assert state.daily_turnover_used == 0
    assert state.submitted_idempotency_keys == []
    assert state.reserved_sell_quantities == {}


def test_broker_attempted_terminal_checkpoint_still_consumes_daily_limits(
    tmp_path,
) -> None:
    policy = _policy()
    snapshot = _snapshot(policy, monthly_loss_ratio=0)
    prepared = PendingLiquidationCheckpoint(
        order_plan_id="oplan-attempted-rejected",
        policy_id=policy.policy_id,
        policy_version=policy.version,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol="CCC",
        purpose="protective_exit",
        idempotency_key="sha256:" + "8" * 64,
        quantity_before=10_000,
        quantity_requested=4,
        expected_quantity_after=9_996,
        account_quantity_before=10_000,
        expected_account_quantity_after=9_996,
        limit_price=99,
        quote_as_of=NOW,
        reconciled_snapshot_id=snapshot.snapshot_id,
        created_at=NOW,
        updated_at=NOW,
    )
    submitted = prepared.model_copy(
        update={
            "status": "submitted",
            "broker_submission_attempted": True,
            "risk_check_id": "risk-attempted-rejected",
            "updated_at": NOW + timedelta(microseconds=1),
            "revision": 1,
        }
    )
    accepted = submitted.model_copy(
        update={
            "status": "accepted",
            "broker_order_id": "broker-attempted-rejected",
            "updated_at": NOW + timedelta(microseconds=2),
            "revision": 2,
        }
    )
    rejected = accepted.model_copy(
        update={
            "status": "rejected",
            "updated_at": NOW + timedelta(microseconds=3),
            "revision": 3,
        }
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.insert_pending_liquidation(prepared)
        store.update_pending_liquidation(submitted)
        store.update_pending_liquidation(accepted)
        store.update_pending_liquidation(rejected)
        state = HarnessService(
            pending_liquidation_provider=store
        )._guardrail_state(
            policy=policy,
            strategy_id="pullback_trend_v2",
            now=NOW,
        )

    assert state.daily_order_count == 1
    assert state.daily_turnover_used == 396
    assert state.submitted_idempotency_keys == [prepared.idempotency_key]
    assert state.reserved_sell_quantities == {}


@pytest.mark.parametrize("value", [-1.0, nan, inf])
def test_reserved_sell_quantities_reject_invalid_values(value: float) -> None:
    with pytest.raises(ValidationError):
        GuardrailState(reserved_sell_quantities={"CCC": value})


def test_reserved_sell_symbols_are_normalized_and_merged() -> None:
    state = GuardrailState(reserved_sell_quantities={"ccc": 2, " CCC ": 3})
    assert state.reserved_sell_quantities == {"CCC": 5}


@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_loss_ratios_reject_non_finite_provider_values(value: float) -> None:
    with pytest.raises(ValidationError):
        PortfolioSnapshot(cash=1, equity=1, monthly_loss_ratio=value)
