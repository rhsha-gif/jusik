from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    StrategyOperatorState,
)
from quantpilot.packages.core.operator.professional_cycle import (
    ProfessionalOperatorCoordinator,
)
from quantpilot.packages.core.portfolio.planner import build_portfolio_plan
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.risk.position_exit import PositionRiskInput
from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    GuardrailState,
    OrderIntent,
    OrderPlan,
    OrderType,
    PortfolioPlan,
    PortfolioPosition,
    PortfolioSnapshot,
    Signal,
    SignalAction,
    UserPolicy,
)
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.registry import (
    StrategyRegistry,
    StrategyRegistryEntry,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)


def _snapshot(*, orderable_quantity: float | None = 3) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=9_000,
        equity=10_000,
        positions=[
            PortfolioPosition(
                symbol="AAA",
                quantity=10,
                orderable_quantity=orderable_quantity,
                market_price=100,
                sector="tech",
            )
        ],
        captured_at=NOW,
        source="reconciled_orderable_quantity_test",
    )


def _sell_intent(quantity: float) -> OrderIntent:
    return OrderIntent(
        symbol="AAA",
        side="sell",
        order_type=OrderType.limit,
        quantity=quantity,
        limit_price=100,
        notional=quantity * 100,
        target_weight=max(0, 0.10 - quantity * 100 / 10_000),
        reason="orderable quantity safety test",
        quote_time=NOW,
    )


def _sell_order(policy: UserPolicy, quantity: float) -> OrderPlan:
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=_sell_intent(quantity),
        purpose="rebalance",
        idempotency_key=f"orderable-sell-{quantity}",
    )


def _plan(policy: UserPolicy, quantities: list[float]) -> PortfolioPlan:
    intents = [_sell_intent(quantity) for quantity in quantities]
    return PortfolioPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        target_weights={"AAA": 0.10},
        cash_target_weight=0.90,
        order_intents=intents,
        created_at=NOW,
    )


def test_position_validates_orderable_quantity_without_changing_holding_value() -> None:
    position = _snapshot().positions[0]

    assert position.effective_orderable_quantity == 3
    assert position.market_value == 1_000

    legacy = PortfolioPosition(symbol="AAA", quantity=10, market_price=100)
    assert legacy.effective_orderable_quantity == legacy.quantity == 10

    with pytest.raises(ValidationError, match="orderable quantity"):
        PortfolioPosition(
            symbol="AAA",
            quantity=10,
            orderable_quantity=10.000001,
            market_price=100,
        )


def test_single_sell_and_reservations_are_limited_by_orderable_quantity() -> None:
    policy = UserPolicy()
    snapshot = _snapshot()

    oversell = run_risk_check(
        policy=policy,
        order_plan=_sell_order(policy, 4),
        snapshot=snapshot,
        now=NOW,
    )
    reserved_oversell = run_risk_check(
        policy=policy,
        order_plan=_sell_order(policy, 2),
        snapshot=snapshot,
        guardrail_state=GuardrailState(reserved_sell_quantities={"aaa": 2}),
        now=NOW,
    )
    reserved_boundary = run_risk_check(
        policy=policy,
        order_plan=_sell_order(policy, 1),
        snapshot=snapshot,
        guardrail_state=GuardrailState(reserved_sell_quantities={"aaa": 2}),
        now=NOW,
    )

    assert "no_short_sell" in oversell.failed_checks
    assert "no_short_sell" in reserved_oversell.failed_checks
    assert "no_short_sell" in reserved_boundary.passed_checks


def test_batch_cumulative_sells_cap_at_orderable_but_exposure_uses_holding() -> None:
    policy = UserPolicy()
    snapshot = _snapshot()

    boundary = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, [3]),
        snapshot=snapshot,
        quotes={"AAA": 100},
        now=NOW,
    )
    cumulative_oversell = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, [2, 2]),
        snapshot=snapshot,
        quotes={"AAA": 100},
        now=NOW,
    )

    assert boundary.passed
    assert boundary.portfolio_after_batch.position_values["AAA"] == 700
    assert boundary.portfolio_after_batch.cash == 9_300
    assert not cumulative_oversell.passed
    assert "no_short_sell_after_batch" in cumulative_oversell.failed_checks


def test_external_whole_share_planner_caps_sell_to_orderable_quantity() -> None:
    policy = UserPolicy(max_position_weight=0.20, max_sector_weight=0.40)
    signal = Signal(
        strategy_id="pullback_trend_v2",
        recipe_version="2",
        symbol="AAA",
        action=SignalAction.exit,
        strength=1,
        reason="exit orderable position",
        source="realtime_market_data",
    )

    plan = build_portfolio_plan(
        policy=policy,
        signals=[signal],
        snapshot=_snapshot(),
        quotes={"AAA": 100},
        quote_times={"AAA": NOW},
        require_explicit_quotes=True,
        require_whole_shares=True,
        rebalance_band=0.01,
    )

    assert len(plan.order_intents) == 1
    assert plan.order_intents[0].quantity == 3
    assert plan.target_weights["AAA"] == 0.07


def test_professional_exit_caps_to_orderable_while_attribution_uses_holding(
    tmp_path,
) -> None:
    policy = UserPolicy(
        execution_mode=ExecutionMode.fully_automated,
        broker=BrokerMode.mock,
        authority_level=5,
        fully_automated_operator_enabled=True,
    )
    managed = ManagedPositionState(
        policy_id=policy.policy_id,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol="AAA",
        quantity=10,
        average_entry_price=100,
        atr14=5,
        active_stop=92,
        policy_version=policy.version,
        opened_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(minutes=2),
        reconciled_snapshot_id="prior-snapshot",
        reconciled_at=NOW - timedelta(minutes=2),
    )
    snapshot = _snapshot()
    risk_input = PositionRiskInput(
        strategy_id=managed.strategy_id,
        strategy_version=managed.strategy_version,
        symbol=managed.symbol,
        quantity=managed.quantity,
        average_entry_price=managed.average_entry_price,
        current_price=90,
        completed_close=100,
        atr14=managed.atr14,
        sma20=100,
        rsi14=50,
        quote_as_of=NOW,
        evaluated_at=NOW,
    )
    registry = StrategyRegistry(
        [
            StrategyRegistryEntry(
                strategy_id=managed.strategy_id,
                version=managed.strategy_version,
                status="validated_l5",
                allowed_execution_levels=["level_5", "fully_automated"],
            )
        ]
    )
    harness = HarnessService()
    harness.repositories.policies.add(policy)

    with PaperStateStore(
        tmp_path / "orderable.sqlite3",
        allow_fixture_seed=True,
    ) as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(
            StrategyOperatorState(
                policy_id=policy.policy_id,
                strategy_id=managed.strategy_id,
                strategy_version=managed.strategy_version,
                health_status="active",
                reason_codes=["healthy"],
                retirement_phase="none",
                pending_order_plan_ids=[],
                updated_at=NOW - timedelta(minutes=2),
            )
        )
        coordinator = ProfessionalOperatorCoordinator(
            harness=harness,
            registry=registry,
            state_store=store,
        )
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require(managed.strategy_id),
            strategy=load_strategy_recipe(managed.strategy_id),
            snapshot=snapshot.model_copy(
                update={
                    "positions": [
                        snapshot.positions[0].model_copy(update={"market_price": 90})
                    ],
                    "cash": 9_100,
                }
            ),
            risk_inputs={"AAA": risk_input},
            quotes={"AAA": Quote(symbol="AAA", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )

        order = harness.repositories.order_plans.require(
            result.submitted_order_plan_ids[0]
        )
        checkpoint = store.load_pending_liquidation(order.order_plan_id)

        assert result.status == "submitted"
        assert order.intent.quantity == 3
        assert checkpoint is not None
        assert checkpoint.quantity_before == 10
        assert checkpoint.account_quantity_before == 10
        assert checkpoint.expected_quantity_after == 7
        assert checkpoint.expected_account_quantity_after == 7
