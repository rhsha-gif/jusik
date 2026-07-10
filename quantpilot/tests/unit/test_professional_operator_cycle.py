from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from quantpilot.packages.core.execution.state_machine import RiskCheckRequired
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    PendingLiquidationCheckpoint,
    StrategyOperatorState,
)
from quantpilot.packages.core.operator.professional_cycle import (
    ProfessionalOperatorCoordinator,
    rebalance_week_bucket,
    risk_evaluation_due,
)
from quantpilot.packages.core.operator.service import OperatorService
from quantpilot.packages.core.risk.position_exit import PositionRiskInput
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    ExecutionMode,
    Fill,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    OrderType,
    PortfolioPosition,
    PortfolioSnapshot,
    ProposalExplanation,
    StrategyRecipe,
    UserPolicy,
)
from quantpilot.packages.core.strategies.performance_review import StrategyHealthInput
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.registry import (
    StrategyRegistry,
    StrategyRegistryEntry,
)
from quantpilot.packages.db.sqlite_repositories import (
    PaperStateStore as RuntimePaperStateStore,
)


NOW = datetime(2026, 7, 10, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))


class PaperStateStore(RuntimePaperStateStore):
    """Test-only store with explicit fixture-seeding capability."""

    def __init__(self, database_path: object) -> None:
        super().__init__(database_path, allow_fixture_seed=True)


def _policy(**updates: object) -> UserPolicy:
    values: dict[str, object] = {
        "execution_mode": ExecutionMode.fully_automated,
        "broker": BrokerMode.mock,
        "authority_level": 5,
        "fully_automated_operator_enabled": True,
    }
    values.update(updates)
    return UserPolicy(**values)


def _entry(**updates: object) -> StrategyRegistryEntry:
    values: dict[str, object] = {
        "strategy_id": "pullback_trend_v2",
        "version": "2.0",
        "status": "validated_l5",
        "allowed_execution_levels": ["level_5", "fully_automated"],
    }
    values.update(updates)
    return StrategyRegistryEntry(**values)


def _recipe() -> StrategyRecipe:
    return load_strategy_recipe("pullback_trend_v2")


def _snapshot(
    policy: UserPolicy,
    *,
    positions: list[PortfolioPosition],
    captured_at: datetime = NOW,
    monthly_loss_ratio: float = 0.0,
) -> PortfolioSnapshot:
    value = sum(item.market_value for item in positions)
    return PortfolioSnapshot(
        user_id=policy.user_id,
        cash=10_000_000 - value,
        equity=10_000_000,
        positions=positions,
        monthly_loss_ratio=monthly_loss_ratio,
        captured_at=captured_at,
        source="reconciled_test_broker",
    )


def _managed(
    policy: UserPolicy,
    symbol: str,
    quantity: float,
    *,
    average_entry_price: float = 100.0,
    atr14: float = 5.0,
) -> ManagedPositionState:
    prior = NOW - timedelta(minutes=2)
    return ManagedPositionState(
        policy_id=policy.policy_id,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol=symbol,
        quantity=quantity,
        average_entry_price=average_entry_price,
        atr14=atr14,
        active_stop=max(average_entry_price * 0.92, average_entry_price - 2 * atr14),
        policy_version=policy.version,
        opened_at=prior - timedelta(days=1),
        updated_at=prior,
        reconciled_snapshot_id="prior-snapshot",
        reconciled_at=prior,
    )


def _state(policy: UserPolicy, **updates: object) -> StrategyOperatorState:
    values: dict[str, object] = {
        "policy_id": policy.policy_id,
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "health_status": "active",
        "reason_codes": ["healthy"],
        "retirement_phase": "none",
        "pending_order_plan_ids": [],
        "last_risk_evaluated_at": None,
        "updated_at": NOW - timedelta(minutes=2),
    }
    values.update(updates)
    return StrategyOperatorState(**values)


def _risk_input(
    managed: ManagedPositionState,
    *,
    price: float,
    evaluated_at: datetime = NOW,
) -> PositionRiskInput:
    return PositionRiskInput(
        strategy_id=managed.strategy_id,
        strategy_version=managed.strategy_version,
        symbol=managed.symbol,
        quantity=managed.quantity,
        average_entry_price=managed.average_entry_price,
        current_price=price,
        completed_close=100,
        atr14=managed.atr14,
        sma20=100,
        rsi14=50,
        quote_as_of=evaluated_at,
        evaluated_at=evaluated_at,
    )


class _DispatchProvider:
    def __init__(self, dispatch: object | None) -> None:
        self.dispatch = dispatch
        self.loaded_order_plan_ids: list[str] = []

    def load_paper_order_dispatch(self, order_plan_id: str):
        self.loaded_order_plan_ids.append(order_plan_id)
        return self.dispatch


def _seed_submitted_pending_checkpoint(
    store: PaperStateStore,
    *,
    policy: UserPolicy,
    managed: ManagedPositionState,
    order_plan_id: str,
) -> PendingLiquidationCheckpoint:
    created = NOW - timedelta(minutes=1)
    prepared = PendingLiquidationCheckpoint(
        order_plan_id=order_plan_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        strategy_id=managed.strategy_id,
        strategy_version=managed.strategy_version,
        symbol=managed.symbol,
        purpose="protective_exit",
        idempotency_key="sha256:" + "d" * 64,
        quantity_before=managed.quantity,
        quantity_requested=managed.quantity,
        expected_quantity_after=0,
        account_quantity_before=managed.quantity,
        expected_account_quantity_after=0,
        limit_price=90,
        quote_as_of=created,
        reconciled_snapshot_id="prior-snapshot",
        created_at=created,
        updated_at=created,
    )
    submitted = PendingLiquidationCheckpoint.model_validate(
        prepared.model_copy(
            update={
                "status": "submitted",
                "broker_submission_attempted": True,
                "risk_check_id": "risk-final-before-dispatch-claim",
                "updated_at": created + timedelta(microseconds=1),
                "revision": 1,
            }
        ).model_dump()
    )
    store.seed_fixture_position(managed, data_mode="fixture")
    store.save_strategy_operator_state(
        _state(
            policy,
            retirement_phase="awaiting_reconciliation",
            pending_order_plan_ids=[order_plan_id],
            last_risk_evaluated_at=created,
            updated_at=created,
        )
    )
    store.insert_pending_liquidation(prepared)
    store.update_pending_liquidation(submitted)
    return submitted


def _dispatch_evidence(
    checkpoint: PendingLiquidationCheckpoint,
    *,
    attempt_count: int,
    status: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        order_plan_id=checkpoint.order_plan_id,
        policy_id=checkpoint.policy_id,
        policy_version=checkpoint.policy_version,
        strategy_id=checkpoint.strategy_id,
        strategy_version=checkpoint.strategy_version,
        symbol=checkpoint.symbol,
        side="sell",
        purpose=checkpoint.purpose,
        idempotency_key=checkpoint.idempotency_key,
        quantity=checkpoint.quantity_requested,
        limit_price=checkpoint.limit_price,
        quote_as_of=checkpoint.quote_as_of,
        risk_check_id=checkpoint.risk_check_id,
        reconciled_snapshot_id=checkpoint.reconciled_snapshot_id,
        attempt_count=attempt_count,
        status=status,
    )


def _coordinator(
    policy: UserPolicy,
    store: PaperStateStore,
    *,
    entry: StrategyRegistryEntry | None = None,
) -> tuple[ProfessionalOperatorCoordinator, HarnessService, StrategyRegistry]:
    harness = HarnessService()
    harness.repositories.policies.add(policy)
    registry = StrategyRegistry([entry or _entry()])
    return (
        ProfessionalOperatorCoordinator(
            harness=harness,
            registry=registry,
            state_store=store,
        ),
        harness,
        registry,
    )


def test_risk_cadence_exact_boundary_and_kst_week_boundary() -> None:
    assert risk_evaluation_due(NOW, NOW + timedelta(seconds=59, microseconds=999000)) == (
        False,
        "risk_evaluation_not_due",
    )
    assert risk_evaluation_due(NOW, NOW + timedelta(seconds=60)) == (
        True,
        "risk_evaluation_due",
    )
    assert risk_evaluation_due(NOW, NOW - timedelta(microseconds=1)) == (
        False,
        "clock_regression",
    )
    assert risk_evaluation_due(None, NOW.replace(tzinfo=None)) == (
        False,
        "evaluation_timestamp_naive",
    )
    utc_sunday = datetime(2026, 7, 12, 15, 30, tzinfo=timezone.utc)
    assert rebalance_week_bucket(utc_sunday) == "2026-W29"


def test_health_disable_is_persisted_and_blocks_weekly_rebalance(tmp_path) -> None:
    policy = _policy()
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        coordinator, _harness, registry = _coordinator(policy, store)
        result = coordinator.review_strategy_health(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            evidence=StrategyHealthInput(
                strategy_id="pullback_trend_v2",
                strategy_version="2.0",
                backtest_max_drawdown=0.08,
                realized_max_drawdown=0.20,
                realized_return=0.0,
                benchmark_return=0.0,
            ),
            performance_record_id="performance-001",
            evaluated_at=NOW,
        )

        assert result.state.health_status == "disabled"
        assert result.state.retirement_phase == "risk_first"
        assert registry.require("pullback_trend_v2").status == "disabled"
        weekly = coordinator.claim_weekly_rebalance(
            policy=policy,
            strategy_id="pullback_trend_v2",
            strategy_version="2.0",
            evaluated_at=NOW + timedelta(minutes=1),
        )
        assert not weekly.claimed
        assert weekly.reason_code == "strategy_health_blocks_rebalance"


def test_missing_benchmark_blocks_buys_without_starting_retirement(tmp_path) -> None:
    policy = _policy()
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        coordinator, _harness, registry = _coordinator(policy, store)
        result = coordinator.review_strategy_health(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            evidence=StrategyHealthInput(
                strategy_id="pullback_trend_v2",
                strategy_version="2.0",
                backtest_max_drawdown=0.08,
                realized_max_drawdown=0.05,
                realized_return=0.02,
                benchmark_return=None,
            ),
            performance_record_id="performance-002",
            evaluated_at=NOW,
        )

        assert result.state.health_status == "review_unavailable"
        assert result.state.retirement_phase == "none"
        assert registry.require("pullback_trend_v2").status == "validated_l5"


def test_weekly_rebalance_claim_requires_fresh_risk_and_is_once_per_week(tmp_path) -> None:
    policy = _policy()
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.save_strategy_operator_state(
            _state(
                policy,
                last_risk_evaluated_at=NOW,
                updated_at=NOW,
            )
        )
        coordinator, _harness, _registry = _coordinator(policy, store)
        first = coordinator.claim_weekly_rebalance(
            policy=policy,
            strategy_id="pullback_trend_v2",
            strategy_version="2.0",
            evaluated_at=NOW + timedelta(seconds=30),
        )
        assert first.claim is not None
        committed = coordinator.commit_weekly_rebalance_submission(
            claim=first.claim,
            committed_at=NOW + timedelta(seconds=30, microseconds=1),
        )
        completed = coordinator.complete_weekly_rebalance(
            policy=policy,
            claim=committed,
            # Completion may safely finish after lease expiry because the exact
            # claim was permanently committed before the broker side effect.
            completed_at=NOW + timedelta(minutes=10),
        )
        duplicate = coordinator.claim_weekly_rebalance(
            policy=policy,
            strategy_id="pullback_trend_v2",
            strategy_version="2.0",
            evaluated_at=NOW + timedelta(seconds=31),
        )

        assert first.claimed
        assert completed.last_rebalance_session == first.bucket
        assert not duplicate.claimed
        assert duplicate.reason_code == "weekly_rebalance_already_claimed"


def test_paused_health_is_sticky_and_stale_reapproval_is_rejected(tmp_path) -> None:
    policy = _policy()
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.save_strategy_operator_state(
            _state(
                policy,
                health_status="paused_reapproval",
                reason_codes=["max_drawdown_reapproval_threshold_breached"],
                updated_at=NOW - timedelta(minutes=2),
            )
        )
        coordinator, _harness, registry = _coordinator(policy, store)
        unavailable = coordinator.review_strategy_health(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            evidence=StrategyHealthInput(
                strategy_id="pullback_trend_v2",
                strategy_version="2.0",
                backtest_max_drawdown=0.08,
                realized_max_drawdown=0.05,
                realized_return=0.01,
                benchmark_return=None,
            ),
            performance_record_id="performance-new",
            evaluated_at=NOW - timedelta(minutes=1),
        )
        assert unavailable.state.health_status == "paused_reapproval"

        with pytest.raises(ValueError, match="not newer"):
            coordinator.review_strategy_health(
                policy=policy,
                registry_entry=registry.require("pullback_trend_v2"),
                evidence=StrategyHealthInput(
                    strategy_id="pullback_trend_v2",
                    strategy_version="2.0",
                    backtest_max_drawdown=0.08,
                    realized_max_drawdown=0.01,
                    realized_return=0.02,
                    benchmark_return=0.01,
                ),
                performance_record_id="performance-stale",
                evaluated_at=NOW - timedelta(hours=1),
                reapproved=True,
            )


def test_monthly_stop_still_submits_attributed_best_bid_protective_exit(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10_000)
    snapshot = _snapshot(
        policy,
        positions=[
            PortfolioPosition(symbol="ccc", quantity=4_000, market_price=90),
            PortfolioPosition(symbol="CCC", quantity=6_000, market_price=90),
        ],
        monthly_loss_ratio=-0.11,
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        coordinator, harness, registry = _coordinator(policy, store)
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=snapshot,
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )

        assert result.status == "submitted"
        order = harness.repositories.order_plans.require(
            result.submitted_order_plan_ids[0]
        )
        assert order.purpose == "protective_exit"
        assert order.intent.side == "sell"
        assert order.intent.order_type == OrderType.limit
        assert order.intent.limit_price == 90
        assert order.status.value == "filled"
        assert result.state.retirement_phase == "awaiting_reconciliation"
        checkpoint = store.load_pending_liquidation(order.order_plan_id)
        assert checkpoint is not None
        assert checkpoint.account_quantity_before == 10_000
        assert checkpoint.risk_check_id == order.risk_check_id


def test_operator_service_exposes_risk_first_cycle_before_ordinary_planning(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 5_000)
    snapshot = _snapshot(
        policy,
        positions=[PortfolioPosition(symbol="CCC", quantity=5_000, market_price=90)],
        monthly_loss_ratio=-0.11,
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        harness = HarnessService()
        harness.repositories.policies.add(policy)
        registry = StrategyRegistry([_entry()])
        service = OperatorService(
            harness,
            registry=registry,
            professional_state_store=store,
        )

        result = service.run_professional_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=snapshot,
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )

        assert result.status == "submitted"
        assert all(
            order.intent.side == "sell"
            for order in service.repositories.order_plans.list()
        )


def test_duplicate_snapshot_rows_with_conflicting_prices_fail_closed(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    snapshot = _snapshot(
        policy,
        positions=[
            PortfolioPosition(symbol="CCC", quantity=5, market_price=90),
            PortfolioPosition(symbol="ccc", quantity=5, market_price=91),
        ],
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        coordinator, harness, registry = _coordinator(policy, store)
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=snapshot,
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )

        assert result.status == "blocked"
        assert result.reason_codes == ["reconciled_position_price_conflict:CCC"]
        assert harness.repositories.broker_orders.list() == []


def test_forged_registry_entry_cannot_grant_level5_authority(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    actual_entry = _entry(status="draft", allowed_execution_levels=[])
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        coordinator, _harness, _registry = _coordinator(
            policy,
            store,
            entry=actual_entry,
        )

        with pytest.raises(ValueError, match="strategy registry"):
            coordinator.run_position_cycle(
                policy=policy,
                registry_entry=_entry(),
                strategy=_recipe(),
                snapshot=_snapshot(
                    policy,
                    positions=[
                        PortfolioPosition(symbol="CCC", quantity=10, market_price=90)
                    ],
                ),
                risk_inputs={"CCC": _risk_input(managed, price=90)},
                quotes={
                    "CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)
                },
                evaluated_at=NOW,
            )


def test_execution_journals_snapshot_nested_models_on_write_and_read() -> None:
    policy = _policy()
    harness = HarnessService()
    harness.repositories.policies.add(policy)
    order = OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=OrderIntent(
            symbol="CCC",
            side="buy",
            quantity=1,
            limit_price=100,
            notional=100,
            target_weight=0.01,
            reason="entry",
            quote_time=NOW,
        ),
        idempotency_key="immutable-journal-entry",
    )
    harness.repositories.order_plans.add(order)

    order.intent.side = "sell"
    order.intent.reason = "forged-after-add"
    stored = harness.repositories.order_plans.require(order.order_plan_id)
    assert stored.intent.side == "buy"
    assert stored.intent.reason == "entry"

    stored.intent.side = "sell"
    assert harness.repositories.order_plans.require(order.order_plan_id).intent.side == "buy"

    policy.user_id = "forged-user"
    assert harness.repositories.policies.require(policy.policy_id).user_id == "fixture-user"


def test_large_protective_exit_is_tranched_to_single_order_limit(tmp_path) -> None:
    policy = _policy(single_order_cash_limit=1_000_000)
    managed = _managed(policy, "CCC", 15_000)
    snapshot = _snapshot(
        policy,
        positions=[PortfolioPosition(symbol="CCC", quantity=15_000, market_price=90)],
        monthly_loss_ratio=-0.11,
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        coordinator, harness, registry = _coordinator(policy, store)
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=snapshot,
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )

        order = harness.repositories.order_plans.require(
            result.submitted_order_plan_ids[0]
        )
        assert order.intent.quantity == 11_111
        assert order.intent.notional == 999_990
        assert order.intent.notional <= policy.single_order_cash_limit


def test_risk_decision_price_must_match_trusted_quote_evidence(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 5_000)
    snapshot = _snapshot(
        policy,
        positions=[PortfolioPosition(symbol="CCC", quantity=5_000, market_price=100)],
        monthly_loss_ratio=-0.11,
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        coordinator, harness, registry = _coordinator(policy, store)

        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=snapshot,
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=100, bid=100, as_of=NOW)},
            evaluated_at=NOW,
        )

        assert result.status == "no_action"
        assert "position_quote_evidence_mismatch:CCC" in result.reason_codes
        assert harness.repositories.order_plans.list() == []


def test_retirement_waits_for_protective_reconciliation_then_sells_remaining(tmp_path) -> None:
    policy = _policy()
    risky = _managed(policy, "CCC", 5_000)
    held = _managed(policy, "DDD", 4_000)
    first_snapshot = _snapshot(
        policy,
        positions=[
            PortfolioPosition(symbol="CCC", quantity=5_000, market_price=90),
            PortfolioPosition(symbol="DDD", quantity=4_000, market_price=100),
        ],
        monthly_loss_ratio=-0.11,
    )
    disabled_entry = _entry(status="disabled", allowed_execution_levels=[])
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(risky, data_mode="fixture")
        store.seed_fixture_position(held, data_mode="fixture")
        store.save_strategy_operator_state(
            _state(
                policy,
                health_status="disabled",
                retirement_phase="risk_first",
            )
        )
        coordinator, harness, registry = _coordinator(
            policy,
            store,
            entry=disabled_entry,
        )
        first = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=first_snapshot,
            risk_inputs={
                "CCC": _risk_input(risky, price=90),
                "DDD": _risk_input(held, price=100),
            },
            quotes={
                "CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW),
                "DDD": Quote(symbol="DDD", last=100, bid=100, as_of=NOW),
            },
            evaluated_at=NOW,
        )
        first_order = harness.repositories.order_plans.require(
            first.submitted_order_plan_ids[0]
        )
        assert first_order.purpose == "protective_exit"

        later = NOW + timedelta(seconds=61)
        second_snapshot = _snapshot(
            policy,
            positions=[
                PortfolioPosition(symbol="DDD", quantity=4_000, market_price=100)
            ],
            captured_at=later,
            monthly_loss_ratio=-0.11,
        )
        second = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=second_snapshot,
            risk_inputs={"DDD": _risk_input(held, price=100, evaluated_at=later)},
            quotes={
                "DDD": Quote(symbol="DDD", last=100, bid=100, as_of=later)
            },
            evaluated_at=later,
        )
        second_order = harness.repositories.order_plans.require(
            second.submitted_order_plan_ids[0]
        )
        assert second_order.purpose == "strategy_retirement"
        assert all(order.intent.side == "sell" for order in harness.repositories.order_plans.list())


def test_restart_with_filled_but_unreconciled_snapshot_never_resubmits(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 5_000)
    snapshot = _snapshot(
        policy,
        positions=[PortfolioPosition(symbol="CCC", quantity=5_000, market_price=90)],
        monthly_loss_ratio=-0.11,
    )
    path = tmp_path / "state.sqlite3"
    with PaperStateStore(path) as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        coordinator, _harness, registry = _coordinator(policy, store)
        first = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=snapshot,
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )
        assert first.status == "submitted"

    with PaperStateStore(path) as reopened:
        coordinator, harness, registry = _coordinator(policy, reopened)
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=snapshot.model_copy(
                update={"captured_at": NOW + timedelta(seconds=61)}
            ),
            risk_inputs={},
            quotes={},
            evaluated_at=NOW + timedelta(seconds=61),
        )

        assert result.status == "awaiting_reconciliation"
        assert harness.repositories.order_plans.list() == []


def test_prepared_checkpoint_without_attempt_is_released_after_restart(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    created = NOW - timedelta(minutes=1)
    checkpoint = PendingLiquidationCheckpoint(
        order_plan_id="oplan-crash-before-attempt",
        policy_id=policy.policy_id,
        policy_version=policy.version,
        strategy_id=managed.strategy_id,
        strategy_version=managed.strategy_version,
        symbol=managed.symbol,
        purpose="protective_exit",
        idempotency_key="sha256:" + "c" * 64,
        quantity_before=10,
        quantity_requested=10,
        expected_quantity_after=0,
        account_quantity_before=10,
        expected_account_quantity_after=0,
        limit_price=100,
        quote_as_of=created,
        reconciled_snapshot_id="prior-snapshot",
        created_at=created,
        updated_at=created,
    )
    path = tmp_path / "state.sqlite3"
    with PaperStateStore(path) as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(
            _state(
                policy,
                retirement_phase="awaiting_reconciliation",
                pending_order_plan_ids=[checkpoint.order_plan_id],
                last_risk_evaluated_at=created,
                updated_at=created,
            )
        )
        store.insert_pending_liquidation(checkpoint)

    with PaperStateStore(path) as reopened:
        coordinator, harness, registry = _coordinator(policy, reopened)
        explanation = ProposalExplanation(
            symbol=checkpoint.symbol,
            action="sell",
            quantity=checkpoint.quantity_requested,
            target_weight_delta=-0.01,
            reference_price=checkpoint.limit_price,
            estimated_cash_impact=-checkpoint.quantity_requested
            * checkpoint.limit_price,
            strategy_id=checkpoint.strategy_id,
            strategy_version=checkpoint.strategy_version,
            signal_reason="protective recovery",
            current_weight=0.01,
            target_weight=0,
            weight_delta=-0.01,
            quote_price=checkpoint.limit_price,
            quote_age_seconds=0,
            estimated_notional=checkpoint.quantity_requested
            * checkpoint.limit_price,
            idempotency_key=checkpoint.idempotency_key,
            policy_version=checkpoint.policy_version,
        )
        orphaned_order = OrderPlan(
            order_plan_id=checkpoint.order_plan_id,
            policy_id=checkpoint.policy_id,
            policy_version=checkpoint.policy_version,
            intent=OrderIntent(
                symbol=checkpoint.symbol,
                side="sell",
                order_type=OrderType.limit,
                quantity=checkpoint.quantity_requested,
                limit_price=checkpoint.limit_price,
                notional=checkpoint.quantity_requested * checkpoint.limit_price,
                target_weight=0,
                reason="protective recovery",
                quote_time=checkpoint.quote_as_of,
            ),
            purpose=checkpoint.purpose,
            status=OrderStatus.submitted,
            idempotency_key=checkpoint.idempotency_key,
            risk_check_id="risk-orphaned-before-callback",
            explanation=explanation,
            created_at=checkpoint.created_at,
            updated_at=checkpoint.created_at,
        )
        harness.repositories.order_plans.add(orphaned_order)
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=10, market_price=100)
                ],
            ),
            risk_inputs={},
            quotes={},
            evaluated_at=NOW,
        )

        recovered = reopened.load_pending_liquidation(checkpoint.order_plan_id)
        assert recovered is not None
        assert recovered.status == "reconciled"
        assert recovered.broker_submission_attempted is False
        assert result.state.pending_order_plan_ids == []
        assert harness.repositories.broker_orders.list() == []
        assert harness.repositories.order_plans.require(
            checkpoint.order_plan_id
        ).status == OrderStatus.failed
        guardrail = harness._guardrail_state(
            policy=policy,
            strategy_id=checkpoint.strategy_id,
            now=NOW,
        )
        assert guardrail.reserved_sell_quantities == {}
        assert guardrail.unfilled_order_keys == []
        assert guardrail.daily_order_count == 0


@pytest.mark.parametrize(
    ("dispatch_kind", "expected_reason"),
    [
        ("missing", "durable_dispatch_missing_before_claim"),
        ("prepared", "durable_dispatch_unclaimed_before_post"),
    ],
)
def test_restart_recovers_submitted_callback_marker_without_dispatch_claim(
    tmp_path,
    dispatch_kind: str,
    expected_reason: str,
) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    path = tmp_path / "state.sqlite3"
    with PaperStateStore(path) as store:
        submitted = _seed_submitted_pending_checkpoint(
            store,
            policy=policy,
            managed=managed,
            order_plan_id=f"oplan-killed-{dispatch_kind}",
        )

    dispatch = (
        None
        if dispatch_kind == "missing"
        else _dispatch_evidence(
            submitted,
            attempt_count=0,
            status="prepared",
        )
    )
    provider = _DispatchProvider(dispatch)
    with PaperStateStore(path) as reopened:
        coordinator, harness, registry = _coordinator(policy, reopened)
        harness.paper_dispatch_provider = provider
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=10, market_price=90)
                ],
            ),
            risk_inputs={},
            quotes={},
            evaluated_at=NOW,
        )

        recovered = reopened.load_pending_liquidation(submitted.order_plan_id)
        assert recovered is not None
        assert recovered.status == "reconciled"
        assert recovered.last_error_code == expected_reason
        assert recovered.broker_submission_attempted is True
        assert result.state.pending_order_plan_ids == []
        assert expected_reason + ":CCC" in result.state.reason_codes
        assert provider.loaded_order_plan_ids == [submitted.order_plan_id]
        assert harness.repositories.broker_orders.list() == []
        assert harness.repositories.order_plans.list() == []


@pytest.mark.parametrize(
    "dispatch_status",
    ["dispatch_claimed", "outcome_unknown", "accepted", "partially_filled"],
)
def test_restart_preserves_submitted_checkpoint_for_any_attempted_dispatch(
    tmp_path,
    dispatch_status: str,
) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    path = tmp_path / "state.sqlite3"
    with PaperStateStore(path) as store:
        submitted = _seed_submitted_pending_checkpoint(
            store,
            policy=policy,
            managed=managed,
            order_plan_id=f"oplan-attempted-{dispatch_status}",
        )

    provider = _DispatchProvider(
        _dispatch_evidence(
            submitted,
            attempt_count=1,
            status=dispatch_status,
        )
    )
    with PaperStateStore(path) as reopened:
        coordinator, harness, registry = _coordinator(policy, reopened)
        harness.paper_dispatch_provider = provider
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=10, market_price=90)
                ],
            ),
            risk_inputs={},
            quotes={},
            evaluated_at=NOW,
        )

        preserved = reopened.load_pending_liquidation(submitted.order_plan_id)
        assert preserved == submitted
        assert result.status == "awaiting_reconciliation"
        assert result.state.pending_order_plan_ids == [submitted.order_plan_id]
        assert provider.loaded_order_plan_ids == [submitted.order_plan_id]
        assert harness.repositories.broker_orders.list() == []
        assert harness.repositories.order_plans.list() == []


def test_submission_attempt_is_durable_before_broker_call(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        coordinator, harness, registry = _coordinator(policy, store)

        class OutcomeUnknownBroker:
            def submit_order(self, order: OrderPlan):
                durable = store.load_pending_liquidation(order.order_plan_id)
                assert durable is not None
                assert durable.status == "submitted"
                assert durable.broker_submission_attempted is True
                assert durable.risk_check_id == order.risk_check_id
                raise TimeoutError("simulated response loss")

        monkeypatch.setattr(
            harness,
            "_broker_for_policy",
            lambda _policy: OutcomeUnknownBroker(),
        )
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=10, market_price=90)
                ],
            ),
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )

        durable = store.load_pending_liquidation(
            result.created_order_plan_ids[0]
        )
        assert result.status == "awaiting_reconciliation"
        assert durable is not None
        assert durable.status == "outcome_unknown"
        assert durable.broker_submission_attempted is True
        assert durable.risk_check_id is not None


def test_partial_fill_then_cancel_reconciles_only_cumulative_quantity(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    order_plan_id = "oplan-partial-cancel"
    created = NOW - timedelta(minutes=1)
    checkpoint = PendingLiquidationCheckpoint(
        order_plan_id=order_plan_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol="CCC",
        purpose="strategy_retirement",
        idempotency_key="sha256:" + "b" * 64,
        quantity_before=10,
        quantity_requested=10,
        expected_quantity_after=0,
        account_quantity_before=10,
        expected_account_quantity_after=0,
        limit_price=100,
        quote_as_of=created,
        reconciled_snapshot_id="prior-snapshot",
        status="prepared",
        created_at=created,
        updated_at=created,
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(
            _state(
                policy,
                retirement_phase="awaiting_reconciliation",
                pending_order_plan_ids=[order_plan_id],
                last_risk_evaluated_at=created,
                updated_at=created,
            )
        )
        store.insert_pending_liquidation(checkpoint)
        accepted = checkpoint.model_copy(
            update={
                "status": "accepted",
                "broker_submission_attempted": True,
                "risk_check_id": "risk-final-partial",
                "broker_order_id": "broker-partial",
                "updated_at": created + timedelta(microseconds=1),
                "revision": 1,
            }
        )
        store.update_pending_liquidation(accepted)
        partial_fill = Fill(
            fill_id="fill-partial-1",
            broker_order_id="broker-partial",
            order_plan_id=order_plan_id,
            symbol="CCC",
            quantity=4,
            price=100,
            notional=400,
            filled_at=created + timedelta(microseconds=1),
        )
        partial = accepted.model_copy(
            update={
                "status": "partially_filled",
                "cumulative_filled_quantity": 4,
                "fill_ids": ["fill-partial-1"],
                "fill_evidence": [partial_fill],
                "updated_at": created + timedelta(microseconds=2),
                "revision": 2,
            }
        )
        store.update_pending_liquidation(partial)
        cancelled = partial.model_copy(
            update={
                "status": "cancelled",
                "updated_at": created + timedelta(microseconds=3),
                "revision": 3,
            }
        )
        store.update_pending_liquidation(cancelled)
        coordinator, _harness, registry = _coordinator(policy, store)
        later = NOW + timedelta(seconds=1)

        coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[PortfolioPosition(symbol="CCC", quantity=6, market_price=100)],
                captured_at=later,
            ),
            risk_inputs={},
            quotes={},
            evaluated_at=later,
        )

        remaining = store.load_position(
            policy.policy_id,
            "pullback_trend_v2",
            "2.0",
            "CCC",
        )
        assert remaining is not None
        assert remaining.quantity == 6
        assert remaining.processed_fill_ids == ["fill-partial-1"]


def test_account_shortfall_creates_sticky_attribution_conflict(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        coordinator, harness, registry = _coordinator(policy, store)

        first = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=5, market_price=90)
                ],
            ),
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )
        assert first.status == "no_action"
        conflicted = store.load_position(*managed.storage_key)
        assert conflicted is not None
        assert conflicted.attribution_status == "conflicted"
        assert conflicted.attribution_conflict_reason == "managed_quantity_exceeds_account"
        assert harness.repositories.broker_orders.list() == []

        later = NOW + timedelta(seconds=61)
        restored = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=10, market_price=90)
                ],
                captured_at=later,
            ),
            risk_inputs={
                "CCC": _risk_input(conflicted, price=90, evaluated_at=later)
            },
            quotes={
                "CCC": Quote(symbol="CCC", last=90, bid=90, as_of=later)
            },
            evaluated_at=later,
        )
        assert restored.status == "no_action"
        assert "attribution_conflict_sticky:CCC" in restored.reason_codes
        assert harness.repositories.broker_orders.list() == []
        assert store.load_position(*managed.storage_key).attribution_status == "conflicted"


def test_pending_fill_with_account_below_managed_remainder_conflicts(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    created = NOW - timedelta(minutes=1)
    order_plan_id = "oplan-partial-account-conflict"
    prepared = PendingLiquidationCheckpoint(
        order_plan_id=order_plan_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        strategy_id=managed.strategy_id,
        strategy_version=managed.strategy_version,
        symbol=managed.symbol,
        purpose="protective_exit",
        idempotency_key="sha256:" + "7" * 64,
        quantity_before=10,
        quantity_requested=10,
        expected_quantity_after=0,
        account_quantity_before=10,
        expected_account_quantity_after=0,
        limit_price=100,
        quote_as_of=created,
        reconciled_snapshot_id="prior-snapshot",
        created_at=created,
        updated_at=created,
    )
    accepted = prepared.model_copy(
        update={
            "status": "accepted",
            "broker_submission_attempted": True,
            "risk_check_id": "risk-final-account-conflict",
            "broker_order_id": "broker-account-conflict",
            "updated_at": created + timedelta(microseconds=1),
            "revision": 1,
        }
    )
    fill = Fill(
        fill_id="fill-account-conflict",
        broker_order_id="broker-account-conflict",
        order_plan_id=order_plan_id,
        symbol="CCC",
        quantity=4,
        price=100,
        notional=400,
        filled_at=created + timedelta(microseconds=1),
    )
    cancelled = accepted.model_copy(
        update={
            "status": "cancelled",
            "cumulative_filled_quantity": 4,
            "fill_ids": [fill.fill_id],
            "fill_evidence": [fill],
            "updated_at": created + timedelta(microseconds=2),
            "revision": 2,
        }
    )
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(
            _state(
                policy,
                retirement_phase="awaiting_reconciliation",
                pending_order_plan_ids=[order_plan_id],
                last_risk_evaluated_at=created,
                updated_at=created,
            )
        )
        store.insert_pending_liquidation(prepared)
        store.update_pending_liquidation(accepted)
        store.update_pending_liquidation(cancelled)
        coordinator, _harness, registry = _coordinator(policy, store)

        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=5, market_price=100)
                ],
                captured_at=NOW,
            ),
            risk_inputs={},
            quotes={},
            evaluated_at=NOW,
        )

        assert result.status == "awaiting_reconciliation"
        conflicted = store.load_position(*managed.storage_key)
        assert conflicted is not None
        assert conflicted.attribution_status == "conflicted"
        assert conflicted.attribution_conflict_reason == "managed_remainder_exceeds_account"
        assert store.load_processed_fill(fill.fill_id) is None


def test_retirement_remaining_waits_for_complete_risk_evidence(tmp_path) -> None:
    policy = _policy()
    positions = [_managed(policy, "AAA", 5), _managed(policy, "CCC", 5)]
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        for position in positions:
            store.seed_fixture_position(position, data_mode="fixture")
        store.save_strategy_operator_state(
            _state(
                policy,
                health_status="disabled",
                retirement_phase="remaining",
            )
        )
        coordinator, harness, registry = _coordinator(policy, store)
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="AAA", quantity=5, market_price=100),
                    PortfolioPosition(symbol="CCC", quantity=5, market_price=100),
                ],
            ),
            risk_inputs={},
            quotes={},
            evaluated_at=NOW,
        )

        assert result.status == "no_action"
        assert result.state.retirement_phase == "remaining"
        assert "position_risk_input_missing:AAA" in result.reason_codes
        assert "position_risk_input_missing:CCC" in result.reason_codes
        assert harness.repositories.order_plans.list() == []


def test_retirement_conflict_never_marks_durable_position_complete(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(
            _state(
                policy,
                health_status="disabled",
                retirement_phase="remaining",
            )
        )
        coordinator, harness, registry = _coordinator(policy, store)
        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=5, market_price=100)
                ],
            ),
            risk_inputs={},
            quotes={},
            evaluated_at=NOW,
        )

        assert result.status == "no_action"
        assert result.state.retirement_phase == "remaining"
        assert store.load_position(*managed.storage_key) is not None
        assert harness.repositories.order_plans.list() == []


def test_reconciled_buy_and_sell_fills_manage_position_without_loosening_stop(tmp_path) -> None:
    policy = _policy()
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        coordinator, _harness, _registry = _coordinator(policy, store)
        explanation = ProposalExplanation(
            symbol="CCC",
            action="buy",
            quantity=10,
            target_weight_delta=0.01,
            reference_price=100,
            estimated_cash_impact=1_000,
            strategy_id="pullback_trend_v2",
            strategy_version="2.0",
            signal_reason="entry",
            current_weight=0,
            target_weight=0.01,
            weight_delta=0.01,
            quote_price=100,
            quote_age_seconds=0,
            estimated_notional=1_000,
            idempotency_key="entry-001",
            policy_version=policy.version,
        )
        buy = OrderPlan(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            intent=OrderIntent(
                symbol="CCC",
                side="buy",
                quantity=10,
                limit_price=100,
                notional=1_000,
                target_weight=0.01,
                reason="entry",
                quote_time=NOW,
            ),
            idempotency_key="entry-001",
            explanation=explanation,
            status=OrderStatus.filled,
        )
        coordinator.harness.repositories.order_plans.add(buy)
        coordinator.harness.repositories.broker_orders.add(
            BrokerOrder(
                broker_order_id="broker-001",
                order_plan_id=buy.order_plan_id,
                broker_mode=BrokerMode.mock,
                status=OrderStatus.filled,
                accepted_at=NOW,
            )
        )
        fill = Fill(
            broker_order_id="broker-001",
            order_plan_id=buy.order_plan_id,
            symbol="CCC",
            quantity=10,
            price=100,
            notional=1_000,
            filled_at=NOW,
        )
        coordinator.harness.repositories.fills.add(fill)
        snapshot = _snapshot(
            policy,
            positions=[PortfolioPosition(symbol="CCC", quantity=10, market_price=100)],
        )
        opened = coordinator.record_reconciled_fills(
            policy=policy,
            order=buy,
            fills=[fill],
            snapshot=snapshot,
            entry_atr14=2,
        )
        assert opened is not None
        assert opened.active_stop == 96
        replayed = coordinator.record_reconciled_fills(
            policy=policy,
            order=buy,
            fills=[fill],
            snapshot=snapshot,
            entry_atr14=2,
        )
        assert replayed == opened
        forged_order = buy.model_copy(
            update={
                "intent": buy.intent.model_copy(update={"side": "sell"}),
                "explanation": buy.explanation.model_copy(
                    update={"action": "sell"}
                ),
            }
        )
        with pytest.raises(ValueError, match="order journal"):
            coordinator.record_reconciled_fills(
                policy=policy,
                order=forged_order,
                fills=[fill],
                snapshot=snapshot,
            )
        forged_policy = policy.model_copy(update={"user_id": "other-user"})
        with pytest.raises(ValueError, match="policy journal"):
            coordinator.record_reconciled_fills(
                policy=forged_policy,
                order=buy,
                fills=[fill],
                snapshot=snapshot.model_copy(update={"user_id": "other-user"}),
                entry_atr14=2,
            )

        later = NOW + timedelta(minutes=1)
        add_buy = buy.model_copy(
            update={
                "order_plan_id": "oplan-entry-002",
                "idempotency_key": "entry-002",
                "intent": buy.intent.model_copy(
                    update={
                        "intent_id": "intent-entry-002",
                        "limit_price": 80,
                        "notional": 800,
                        "quote_time": later,
                    }
                ),
                "explanation": explanation.model_copy(
                    update={
                        "reference_price": 80,
                        "quote_price": 80,
                        "estimated_notional": 800,
                        "idempotency_key": "entry-002",
                    }
                ),
            }
        )
        coordinator.harness.repositories.order_plans.add(add_buy)
        coordinator.harness.repositories.broker_orders.add(
            BrokerOrder(
                broker_order_id="broker-002",
                order_plan_id=add_buy.order_plan_id,
                broker_mode=BrokerMode.mock,
                status=OrderStatus.filled,
                accepted_at=later,
            )
        )
        add_fill = fill.model_copy(
            update={
                "fill_id": "fill-002",
                "broker_order_id": "broker-002",
                "order_plan_id": add_buy.order_plan_id,
                "price": 80,
                "notional": 800,
                "filled_at": later,
            }
        )
        coordinator.harness.repositories.fills.add(add_fill)
        averaged = coordinator.record_reconciled_fills(
            policy=policy,
            order=add_buy,
            fills=[add_fill],
            snapshot=_snapshot(
                policy,
                positions=[PortfolioPosition(symbol="CCC", quantity=20, market_price=90)],
                captured_at=later,
            ),
            entry_atr14=10,
        )
        assert averaged is not None
        assert averaged.average_entry_price == 90
        assert averaged.active_stop == 96

        closed_at = later + timedelta(minutes=1)
        sell = add_buy.model_copy(
            update={
                "order_plan_id": "oplan-exit-001",
                "idempotency_key": "exit-001",
                "intent": add_buy.intent.model_copy(
                    update={
                        "intent_id": "intent-exit-001",
                        "side": "sell",
                        "quantity": 20,
                        "limit_price": 90,
                        "notional": 1_800,
                        "target_weight": 0,
                        "quote_time": closed_at,
                    }
                ),
                "explanation": add_buy.explanation.model_copy(
                    update={
                        "action": "sell",
                        "quantity": 20,
                        "reference_price": 90,
                        "estimated_cash_impact": 1_800,
                        "current_weight": 0.02,
                        "target_weight": 0,
                        "weight_delta": -0.02,
                        "target_weight_delta": -0.02,
                        "quote_price": 90,
                        "estimated_notional": 1_800,
                        "idempotency_key": "exit-001",
                    }
                ),
            }
        )
        coordinator.harness.repositories.order_plans.add(sell)
        coordinator.harness.repositories.broker_orders.add(
            BrokerOrder(
                broker_order_id="broker-exit-001",
                order_plan_id=sell.order_plan_id,
                broker_mode=BrokerMode.mock,
                status=OrderStatus.filled,
                accepted_at=closed_at,
            )
        )
        sell_fill = Fill(
            fill_id="fill-exit-001",
            broker_order_id="broker-exit-001",
            order_plan_id=sell.order_plan_id,
            symbol="CCC",
            quantity=20,
            price=90,
            notional=1_800,
            filled_at=closed_at,
        )
        coordinator.harness.repositories.fills.add(sell_fill)
        assert coordinator.record_reconciled_fills(
            policy=policy,
            order=sell,
            fills=[sell_fill],
            snapshot=_snapshot(policy, positions=[], captured_at=closed_at),
        ) is None
        assert store.load_position(
            policy.policy_id,
            "pullback_trend_v2",
            "2.0",
            "CCC",
        ) is None
        assert store.load_processed_fill(fill.fill_id) is not None

        replayed_after_close = coordinator.record_reconciled_fills(
            policy=policy,
            order=buy,
            fills=[fill],
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=10, market_price=100)
                ],
                captured_at=closed_at + timedelta(minutes=1),
            ),
            entry_atr14=2,
        )
        assert replayed_after_close is None
        assert store.load_position(
            policy.policy_id,
            "pullback_trend_v2",
            "2.0",
            "CCC",
        ) is None


def test_executed_fill_reconciles_after_safe_policy_and_broker_drift(tmp_path) -> None:
    submitted_policy = _policy(version=1, broker=BrokerMode.mock)
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        coordinator, harness, _registry = _coordinator(submitted_policy, store)
        explanation = ProposalExplanation(
            symbol="CCC",
            action="buy",
            quantity=10,
            target_weight_delta=0.01,
            reference_price=100,
            estimated_cash_impact=1_000,
            strategy_id="pullback_trend_v2",
            strategy_version="2.0",
            signal_reason="entry",
            current_weight=0,
            target_weight=0.01,
            weight_delta=0.01,
            quote_price=100,
            quote_age_seconds=0,
            estimated_notional=1_000,
            idempotency_key="entry-policy-v1",
            policy_version=1,
        )
        order = OrderPlan(
            policy_id=submitted_policy.policy_id,
            policy_version=1,
            intent=OrderIntent(
                symbol="CCC",
                side="buy",
                quantity=10,
                limit_price=100,
                notional=1_000,
                target_weight=0.01,
                reason="entry",
                quote_time=NOW,
            ),
            idempotency_key="entry-policy-v1",
            explanation=explanation,
            status=OrderStatus.filled,
        )
        broker_order = BrokerOrder(
            broker_order_id="broker-policy-v1",
            order_plan_id=order.order_plan_id,
            broker_mode=BrokerMode.mock,
            status=OrderStatus.filled,
            accepted_at=NOW,
        )
        fill = Fill(
            fill_id="fill-policy-v1",
            broker_order_id=broker_order.broker_order_id,
            order_plan_id=order.order_plan_id,
            symbol="CCC",
            quantity=10,
            price=100,
            notional=1_000,
            filled_at=NOW,
        )
        harness.repositories.order_plans.add(order)
        harness.repositories.broker_orders.add(broker_order)
        harness.repositories.fills.add(fill)

        current_policy = UserPolicy.model_validate(
            submitted_policy.model_copy(
                update={"version": 2, "broker": BrokerMode.paper}
            ).model_dump()
        )
        harness.repositories.policies.update(current_policy)
        snapshot = _snapshot(
            current_policy,
            positions=[PortfolioPosition(symbol="CCC", quantity=10, market_price=100)],
        )

        opened = coordinator.record_reconciled_fills(
            policy=current_policy,
            order=order,
            fills=[fill],
            snapshot=snapshot,
            entry_atr14=2,
        )

        assert opened is not None
        assert opened.policy_version == 1
        assert store.load_processed_fill(fill.fill_id).policy_version == 1
        assert coordinator.record_reconciled_fills(
            policy=current_policy,
            order=order,
            fills=[fill],
            snapshot=snapshot,
            entry_atr14=2,
        ) == opened
        future_order = OrderPlan.model_validate(
            order.model_copy(
                update={
                    "order_plan_id": "oplan-future-policy-v3",
                    "policy_version": 3,
                    "idempotency_key": "entry-policy-v3",
                    "explanation": order.explanation.model_copy(
                        update={
                            "policy_version": 3,
                            "idempotency_key": "entry-policy-v3",
                        }
                    ),
                }
            ).model_dump()
        )
        future_broker_order = BrokerOrder(
            broker_order_id="broker-policy-v3",
            order_plan_id=future_order.order_plan_id,
            broker_mode=BrokerMode.mock,
            status=OrderStatus.filled,
            accepted_at=NOW,
        )
        future_fill = fill.model_copy(
            update={
                "fill_id": "fill-policy-v3",
                "broker_order_id": future_broker_order.broker_order_id,
                "order_plan_id": future_order.order_plan_id,
            }
        )
        harness.repositories.order_plans.add(future_order)
        harness.repositories.broker_orders.add(future_broker_order)
        harness.repositories.fills.add(future_fill)
        with pytest.raises(ValueError, match="order policy identity"):
            coordinator.record_reconciled_fills(
                policy=current_policy,
                order=future_order,
                fills=[future_fill],
                snapshot=snapshot,
                entry_atr14=2,
            )
        with pytest.raises(ValueError, match="policy journal"):
            coordinator.record_reconciled_fills(
                policy=submitted_policy,
                order=order,
                fills=[fill],
                snapshot=snapshot,
                entry_atr14=2,
            )


def test_current_policy_can_submit_protective_exit_for_historical_position(tmp_path) -> None:
    opened_policy = _policy(version=1)
    current_policy = UserPolicy.model_validate(
        opened_policy.model_copy(update={"version": 2}).model_dump()
    )
    managed = _managed(opened_policy, "CCC", 10)
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(current_policy))
        coordinator, harness, registry = _coordinator(current_policy, store)

        result = coordinator.run_position_cycle(
            policy=current_policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                current_policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=10, market_price=90)
                ],
            ),
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )

        assert result.status == "submitted"
        submitted = harness.repositories.order_plans.require(
            result.submitted_order_plan_ids[0]
        )
        assert submitted.policy_version == 2
        assert store.load_position(*managed.storage_key).policy_version == 1


def test_superseded_strategy_positions_retire_before_new_version_rebalance(tmp_path) -> None:
    policy = _policy()
    managed_v2 = _managed(policy, "CCC", 10)
    entry_v21 = _entry(version="2.1")
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed_v2, data_mode="fixture")
        store.save_strategy_operator_state(
            _state(
                policy,
                health_status="disabled",
                retirement_phase="complete",
                reason_codes=["prior_retirement_complete"],
            )
        )
        store.save_strategy_operator_state(
            _state(policy, strategy_version="2.1")
        )
        coordinator, harness, registry = _coordinator(
            policy,
            store,
            entry=entry_v21,
        )

        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=StrategyRecipe.model_validate(
                _recipe().model_copy(update={"version": "2.1"}).model_dump()
            ),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=10, market_price=100)
                ],
            ),
            risk_inputs={"CCC": _risk_input(managed_v2, price=100)},
            quotes={
                "CCC": Quote(symbol="CCC", last=100, bid=100, as_of=NOW)
            },
            evaluated_at=NOW,
        )
        weekly = coordinator.claim_weekly_rebalance(
            policy=policy,
            strategy_id="pullback_trend_v2",
            strategy_version="2.1",
            evaluated_at=NOW,
        )

        assert result.status == "submitted"
        assert result.state.strategy_version == "2.0"
        submitted = harness.repositories.order_plans.require(
            result.submitted_order_plan_ids[0]
        )
        assert submitted.explanation is not None
        assert submitted.explanation.strategy_version == "2.0"
        assert submitted.purpose == "strategy_retirement"
        assert weekly.claimed is False
        assert weekly.reason_code == "strategy_version_migration_required"
        assert len(harness.repositories.order_plans.list()) == 1


def test_current_version_position_reappearing_after_retirement_is_liquidated(
    tmp_path,
) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(
            _state(
                policy,
                health_status="disabled",
                retirement_phase="complete",
                reason_codes=["prior_retirement_complete"],
            )
        )
        coordinator, harness, registry = _coordinator(policy, store)

        result = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=_snapshot(
                policy,
                positions=[
                    PortfolioPosition(symbol="CCC", quantity=10, market_price=100)
                ],
            ),
            risk_inputs={"CCC": _risk_input(managed, price=100)},
            quotes={
                "CCC": Quote(symbol="CCC", last=100, bid=100, as_of=NOW)
            },
            evaluated_at=NOW,
        )

        assert result.status == "submitted"
        submitted = harness.repositories.order_plans.require(
            result.submitted_order_plan_ids[0]
        )
        assert submitted.purpose == "strategy_retirement"
        assert "post_retirement_position_reappeared" in result.state.reason_codes


def test_paused_authority_failure_does_not_starve_next_protective_cycle(tmp_path) -> None:
    policy = _policy()
    managed = _managed(policy, "CCC", 10)
    with PaperStateStore(tmp_path / "state.sqlite3") as store:
        store.seed_fixture_position(managed, data_mode="fixture")
        store.save_strategy_operator_state(_state(policy))
        coordinator, harness, registry = _coordinator(policy, store)
        snapshot = _snapshot(
            policy,
            positions=[PortfolioPosition(symbol="CCC", quantity=10, market_price=90)],
        )
        harness.autopilot_paused = True

        blocked = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=snapshot,
            risk_inputs={"CCC": _risk_input(managed, price=90)},
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=NOW)},
            evaluated_at=NOW,
        )

        assert blocked.status == "blocked"
        assert [item.status for item in harness.repositories.order_plans.list()] == [
            OrderStatus.failed
        ]
        harness.autopilot_paused = False
        later = NOW + timedelta(seconds=61)
        refreshed = store.load_position(*managed.storage_key)
        resumed = coordinator.run_position_cycle(
            policy=policy,
            registry_entry=registry.require("pullback_trend_v2"),
            strategy=_recipe(),
            snapshot=snapshot.model_copy(update={"captured_at": later}),
            risk_inputs={
                "CCC": _risk_input(refreshed, price=90, evaluated_at=later)
            },
            quotes={"CCC": Quote(symbol="CCC", last=90, bid=90, as_of=later)},
            evaluated_at=later,
        )

        assert resumed.status == "submitted"
        assert len(harness.repositories.broker_orders.list()) == 1


def test_operator_pause_and_broker_health_survive_restart(tmp_path) -> None:
    policy = _policy()
    path = tmp_path / "state.sqlite3"
    with PaperStateStore(path) as store:
        _coordinator_instance, harness, _registry = _coordinator(policy, store)
        harness.pause_guarded_autopilot(
            policy_id=policy.policy_id,
            reason="operator_review",
        )
        harness.record_broker_health(
            policy_id=policy.policy_id,
            healthy=False,
            reason="broker_failure",
        )

    with PaperStateStore(path) as reopened:
        _coordinator_instance, harness, _registry = _coordinator(policy, reopened)
        # The mutator itself must hydrate durable state; callers are not required
        # to touch the guardrail first after a restart.
        with pytest.raises(
            RiskCheckRequired,
            match="broker health must recover",
        ):
            harness.resume_guarded_autopilot(policy_id=policy.policy_id)
        restored = reopened.load_operator_safety_state(policy.policy_id)
        assert restored is not None
        assert restored.autopilot_paused is True
        assert restored.broker_healthy is False

    with PaperStateStore(path) as reopened:
        _coordinator_instance, harness, _registry = _coordinator(policy, reopened)
        harness.record_broker_health(
            policy_id=policy.policy_id,
            healthy=True,
        )
        restored = reopened.load_operator_safety_state(policy.policy_id)
        assert restored is not None
        assert restored.autopilot_paused is True
        assert restored.broker_healthy is True
        harness.resume_guarded_autopilot(policy_id=policy.policy_id)

    with PaperStateStore(path) as final_store:
        _coordinator_instance, harness, _registry = _coordinator(
            policy,
            final_store,
        )
        restored = harness._guardrail_state(
            policy=policy,
            strategy_id="pullback_trend_v2",
            now=NOW,
        )
        assert restored.autopilot_paused is False
        assert restored.broker_healthy is True
