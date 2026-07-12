from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quantpilot.packages.brokers.kis_paper import KisPaperBrokerAdapter
from quantpilot.packages.core.execution.paper_submission import (
    DurablePaperSubmissionCoordinator,
    PaperSubmissionOutcomeUnknown,
)
from quantpilot.packages.core.execution.paper_reconciliation_apply import (
    PaperReconciliationApplier,
)
from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperBrokerReconciler,
)
from quantpilot.packages.core.execution.reducer import replay_paper_execution_events
from quantpilot.jobs.run_kis_paper_session import _hydrate_durable_order_plans
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.kis_paper import (
    KisBuyingPower,
    KisCashOrderResult,
    KisDailyOrderFill,
    KisPaperOrderOutcomeUnknown,
)
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import PaperOrderDispatch
from quantpilot.packages.core.operator.service import OperatorService
from quantpilot.packages.core.schemas import (
    BrokerMode,
    DataMode,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    PortfolioPosition,
    PortfolioSnapshot,
    ProposalExplanation,
    UserPolicy,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
FINGERPRINT = "sha256:" + "a" * 64


class _SessionAuthority:
    def current_open_session_date(self, observed_at: datetime) -> date | None:
        return observed_at.astimezone(timezone(timedelta(hours=9))).date()


class _Client:
    account_scope_fingerprint = FINGERPRINT

    def __init__(self, *, outcome_unknown: bool = False) -> None:
        self.outcome_unknown = outcome_unknown
        self.order_calls = 0
        self.store: PaperStateStore | None = None

    def get_buying_power(
        self,
        symbol: str,
        limit_price: Decimal,
        *,
        exchange: str = "KRX",
    ) -> KisBuyingPower:
        assert (symbol, limit_price, exchange) == (
            "005930",
            Decimal("70000"),
            "KRX",
        )
        return KisBuyingPower(
            symbol=symbol,
            limit_price=limit_price,
            orderable_cash=Decimal("500000"),
            no_receivable_buy_amount=Decimal("450000"),
            no_receivable_buy_quantity=6,
            maximum_buy_amount=Decimal("900000"),
            maximum_buy_quantity=12,
            calculation_price=limit_price,
        )

    def place_limit_cash_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        limit_price: Decimal,
        exchange: str = "KRX",
    ) -> KisCashOrderResult:
        assert self.store is not None
        dispatch = self.store.load_paper_order_dispatch("plan-001")
        assert dispatch is not None
        assert dispatch.status == "dispatch_claimed"
        assert dispatch.attempt_count == 1
        self.order_calls += 1
        if self.outcome_unknown:
            raise KisPaperOrderOutcomeUnknown("ambiguous transport")
        return KisCashOrderResult(
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            quantity=quantity,
            limit_price=limit_price,
            order_number="0000012345",
            krx_forwarding_order_org_number="91234",
            order_time="100001",
            message_code="APBK0013",
            transaction_id="VTTC0012U",
        )


class _UnusedLossProvider:
    pass


class _UnusedSectorProvider:
    pass


class _UnusedMarketDataProvider:
    pass


def _policy() -> UserPolicy:
    return UserPolicy(
        policy_id="policy-001",
        user_id="paper-user",
        broker=BrokerMode.paper,
        min_cash_weight=0.20,
        max_position_weight=0.20,
        max_sector_weight=0.50,
        single_order_cash_limit=500_000,
        max_daily_turnover=1_000_000,
    )


def _order(policy: UserPolicy) -> OrderPlan:
    explanation = ProposalExplanation(
        symbol="005930",
        action="buy",
        quantity=1,
        target_weight_delta=0.07,
        reference_price=70000,
        estimated_cash_impact=-70000,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        signal_reason="fixture integration evidence",
        current_weight=0,
        target_weight=0.07,
        weight_delta=0.07,
        quote_price=70000,
        quote_age_seconds=5,
        limit_price=70000,
        estimated_notional=70000,
        risk_checks_passed=["all"],
        risk_check_id="risk-001",
        risk_check_expires_at=NOW + timedelta(minutes=5),
        idempotency_key="paper-order-key-001",
        policy_version=policy.version,
        account_equity_at_proposal=1_000_000,
        portfolio_snapshot_id="snapshot-001",
    )
    return OrderPlan(
        order_plan_id="plan-001",
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=OrderIntent(
            symbol="005930",
            side="buy",
            quantity=1,
            limit_price=70000,
            notional=70000,
            target_weight=0.07,
            reason="fixture integration evidence",
            quote_time=NOW - timedelta(seconds=5),
        ),
        status=OrderStatus.user_approved,
        idempotency_key="paper-order-key-001",
        risk_check_id="risk-001",
        risk_check_expires_at=NOW + timedelta(minutes=5),
        explanation=explanation,
    )


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="snapshot-001",
        user_id="paper-user",
        cash=300000,
        equity=1000000,
        positions=[],
        daily_loss_ratio=-0.01,
        monthly_loss_ratio=-0.02,
        captured_at=NOW - timedelta(seconds=4),
        source="kis_paper_balance_reconciled",
    )


def _quote() -> Quote:
    return Quote(
        symbol="005930",
        last=70000,
        bid=69900,
        ask=70100,
        as_of=NOW - timedelta(seconds=5),
    )


def _harness(
    store: PaperStateStore,
    *,
    outcome_unknown: bool = False,
) -> tuple[HarnessService, _Client, DurablePaperSubmissionCoordinator]:
    client = _Client(outcome_unknown=outcome_unknown)
    session = store.start_paper_execution_session(
        started_at=NOW - timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(hours=1),
    )
    coordinator = DurablePaperSubmissionCoordinator(
        store=store,
        session=session,
        client=client,  # type: ignore[arg-type]
        session_authority=_SessionAuthority(),
        clock=lambda: NOW,
    )
    client.store = store
    broker = KisPaperBrokerAdapter(
        client,  # type: ignore[arg-type]
        submission_gateway=coordinator,
        loss_provider=_UnusedLossProvider(),  # type: ignore[arg-type]
        sector_provider=_UnusedSectorProvider(),  # type: ignore[arg-type]
        market_data_provider=_UnusedMarketDataProvider(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    return (
        HarnessService(
            external_paper_broker=broker,
            paper_submission_coordinator=coordinator,
        ),
        client,
        coordinator,
    )


def test_external_paper_submit_prepares_and_claims_before_single_post(
    tmp_path,
) -> None:
    database_path = tmp_path / "paper.sqlite3"
    policy = _policy()
    order = _order(policy)
    with PaperStateStore(
        database_path,
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        harness, client, _ = _harness(store)
        harness.repositories.policies.add(policy)
        harness.repositories.order_plans.add(order)

        submitted, broker_order, fills = harness.submit_order_plan(
            order.order_plan_id,
            snapshot=_snapshot(),
            market_quote=_quote(),
            paper_run_id="run-001",
            entry_atr14=1200,
            now=NOW,
        )

        dispatch = store.load_paper_order_dispatch(order.order_plan_id)
        assert dispatch is not None
        assert dispatch.status == "accepted"
        assert dispatch.entry_atr14 == 1200
        assert dispatch.quote_reference_basis == "l2_midpoint"
        assert dispatch.reconciled_snapshot_id == "snapshot-001"
        assert client.order_calls == 1
        assert submitted.status == OrderStatus.accepted
        assert broker_order.broker_order_id == dispatch.broker_order_id
        assert fills == []

        dispatch_events = store.list_paper_execution_events(
            "order_dispatch",
            order.order_plan_id,
        )
        projection = replay_paper_execution_events(dispatch_events)
        assert isinstance(projection.after, PaperOrderDispatch)
        assert projection.after == dispatch
        replayed_order = OrderPlan.model_validate(
            projection.after.order_plan_payload
        )
        assert replayed_order.explanation is not None
        assert replayed_order.explanation.account_equity_at_proposal == 1_000_000
        assert replayed_order.explanation.portfolio_snapshot_id == "snapshot-001"
        assert client.order_calls == 1

    with PaperStateStore(
        database_path,
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as reopened:
        persisted = reopened.load_paper_order_dispatch(order.order_plan_id)
        assert persisted is not None
        restart_projection = replay_paper_execution_events(
            reopened.list_paper_execution_events(
                "order_dispatch",
                order.order_plan_id,
            )
        )
        assert restart_projection.after == persisted

        restarted_harness = HarnessService(
            data_mode=DataMode.local_historical,
            market_data_provider=SimpleNamespace(
                get_price_history=lambda: [
                    {
                        "symbol": "005930",
                        "date": "2026-07-10",
                        "open": 70_000,
                        "high": 70_000,
                        "low": 70_000,
                        "close": 70_000,
                        "volume": 10_000,
                    }
                ]
            ),  # type: ignore[arg-type]
            performance_clock=lambda: datetime(
                2026,
                7,
                10,
                7,
                0,
                tzinfo=timezone.utc,
            ),
        )
        restarted_harness.repositories.policies.add(policy)
        restarted_operator = OperatorService(
            restarted_harness,
            professional_state_store=reopened,
        )
        restarted_runtime = SimpleNamespace(
            operator=restarted_operator,
            store=reopened,
        )
        _hydrate_durable_order_plans(  # type: ignore[arg-type]
            restarted_runtime,
            (persisted,),
        )

        recovered = restarted_harness.repositories.order_plans.require(
            order.order_plan_id
        )
        assert recovered.explanation is not None
        assert recovered.explanation.account_equity_at_proposal == 1_000_000
        assert recovered.explanation.portfolio_snapshot_id == "snapshot-001"

        filled_dispatch = PaperBrokerReconciler(
            store=reopened,
            client=client,  # type: ignore[arg-type]
            clock=lambda: NOW + timedelta(seconds=20),
        ).reconcile_dispatch(
            persisted,
            (
                KisDailyOrderFill(
                    order_number="0000012345",
                    original_order_number="",
                    order_branch_number="91234",
                    order_date="20260710",
                    order_time="100001",
                    symbol="005930",
                    product_name="fixture security",
                    side="buy",
                    order_quantity=1,
                    order_price=Decimal("70000"),
                    total_filled_quantity=1,
                    average_fill_price=Decimal("70000"),
                    remaining_quantity=0,
                    rejected_quantity=0,
                    cancelled=False,
                    confirmed_cancel_quantity=0,
                    total_filled_amount=Decimal("70000"),
                ),
            ),
            reconciled_at=NOW + timedelta(seconds=20),
        )
        assert filled_dispatch.status == "filled"
        filled_projection = replay_paper_execution_events(
            reopened.list_paper_execution_events(
                "order_dispatch",
                order.order_plan_id,
            )
        )
        assert filled_projection.after == filled_dispatch

        applied = PaperReconciliationApplier(
            repositories=restarted_harness.repositories,
            audit=restarted_harness.audit,
        ).apply((filled_dispatch,))
        assert applied.applied_order_plan_ids == (order.order_plan_id,)
        assert len(applied.new_fill_ids) == 1
        recovered = restarted_harness.repositories.order_plans.require(
            order.order_plan_id
        )
        assert recovered.status == OrderStatus.filled
        reconciled_fills = [
            restarted_harness.repositories.fills.require(fill_id)
            for fill_id in applied.new_fill_ids
        ]
        assert restarted_operator.professional is not None
        restarted_operator.professional.record_reconciled_fills(
            policy=policy,
            order=recovered,
            fills=reconciled_fills,
            snapshot=PortfolioSnapshot(
                snapshot_id="snapshot-after-fill",
                user_id=policy.user_id,
                cash=930_000,
                equity=1_000_000,
                positions=[
                    PortfolioPosition(
                        symbol="005930",
                        quantity=1,
                        market_price=70_000,
                    )
                ],
                captured_at=NOW + timedelta(seconds=30),
                source="reconciled_test_broker",
            ),
            entry_atr14=1_200,
        )

        performance = restarted_harness.compute_strategy_performance(
            "pullback_trend_v2",
            "2.0",
        )
        assert performance is not None
        assert performance.normalization_basis == "first_order_account_equity"
        assert performance.normalization_equity == 1_000_000
        assert performance.normalization_snapshot_id == "snapshot-001"
        assert performance.data_mode == DataMode.local_historical
        assert performance.included_fill_count == 1
        assert performance.included_fill_fingerprint is not None
        assert client.order_calls == 1


def test_outcome_unknown_is_never_relabelled_failed_and_blocks_more_buys(
    tmp_path,
) -> None:
    with PaperStateStore(
        tmp_path / "paper.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        harness, client, _ = _harness(store, outcome_unknown=True)
        policy = _policy()
        order = _order(policy)
        harness.repositories.policies.add(policy)
        harness.repositories.order_plans.add(order)

        with pytest.raises(PaperSubmissionOutcomeUnknown) as caught:
            harness.submit_order_plan(
                order.order_plan_id,
                snapshot=_snapshot(),
                market_quote=_quote(),
                paper_run_id="run-001",
                entry_atr14=1200,
                now=NOW,
            )

        assert client.order_calls == 1
        assert harness.repositories.order_plans.require(
            order.order_plan_id
        ).status == OrderStatus.submitted
        dispatch = store.load_paper_order_dispatch(order.order_plan_id)
        assert dispatch is not None and dispatch.status == "outcome_unknown"

        operator = OperatorService(
            harness,
            professional_state_store=store,
        )
        fallback = operator._handle_paper_outcome_unknown(
            policy=policy,
            proposal=order,
            error=caught.value,
        )
        assert fallback.reason_code == "paper_submission_outcome_unknown"
        assert harness.repositories.order_plans.require(
            order.order_plan_id
        ).status == OrderStatus.submitted
        guardrail = harness._guardrail_state(
            policy=policy,
            strategy_id="pullback_trend_v2",
            now=NOW,
        )
        assert guardrail.unresolved_paper_buy_order is True
        assert guardrail.daily_order_count == 1
        assert guardrail.daily_turnover_used == 70000


def test_external_broker_and_coordinator_are_an_atomic_configuration() -> None:
    with pytest.raises(ValueError, match="configured together"):
        HarnessService(external_paper_broker=object())  # type: ignore[arg-type]
