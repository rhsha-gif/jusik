from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperReconciliationResult,
)
from quantpilot.packages.core.execution.paper_reconciliation_apply import (
    PaperReconciliationApplier,
)
from quantpilot.packages.core.kis_paper import KisBalanceResult, KisBalanceSummary
from quantpilot.packages.core.operator.position_ledger import (
    PaperDispatchFillEvidence,
    PaperOrderDispatch,
)
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    Fill,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    ProposalExplanation,
    UserPolicy,
)
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
BROKER_AT = datetime(2026, 7, 10, 10, 0, 1, tzinfo=timezone(timedelta(hours=9)))
FINGERPRINT = "sha256:" + "a" * 64


def _repositories(*, status: OrderStatus = OrderStatus.submitted) -> RepositoryRegistry:
    repositories = RepositoryRegistry()
    repositories.policies.add(
        UserPolicy(
            policy_id="policy-001",
            user_id="paper-user",
            broker=BrokerMode.paper,
        )
    )
    repositories.order_plans.add(_order(status=status))
    return repositories


def _order(*, status: OrderStatus = OrderStatus.submitted) -> OrderPlan:
    return OrderPlan(
        order_plan_id="plan-001",
        policy_id="policy-001",
        policy_version=1,
        intent=OrderIntent(
            symbol="005930",
            side="buy",
            quantity=2,
            limit_price=70_000,
            notional=140_000,
            target_weight=0.14,
            reason="fixture reconciliation",
            quote_time=NOW - timedelta(seconds=5),
        ),
        status=status,
        idempotency_key="paper-key-001",
        risk_check_id="risk-001",
        risk_check_expires_at=NOW + timedelta(minutes=5),
        explanation=ProposalExplanation(
            symbol="005930",
            action="buy",
            quantity=2,
            target_weight_delta=0.14,
            reference_price=70_000,
            estimated_cash_impact=-140_000,
            strategy_id="pullback_trend_v2",
            strategy_version="2.0",
            signal_reason="fixture reconciliation",
            current_weight=0,
            target_weight=0.14,
            weight_delta=0.14,
            quote_price=70_000,
            quote_age_seconds=5,
            limit_price=70_000,
            estimated_notional=140_000,
            risk_checks_passed=["all"],
            risk_check_id="risk-initial",
            risk_check_expires_at=NOW + timedelta(minutes=2),
            idempotency_key="paper-key-001",
            policy_version=1,
        ),
    )


def _evidence(
    *,
    reference: str = "kisagg-fill-001",
    quantity: float = 1,
    price: float = 70_000,
) -> PaperDispatchFillEvidence:
    return PaperDispatchFillEvidence(
        broker_fill_reference=reference,
        broker_order_id="broker-internal-001",
        broker_order_reference="0000012345",
        symbol="005930",
        side="buy",
        quantity=quantity,
        price=price,
        notional=quantity * price,
        evidence_at=NOW + timedelta(seconds=20),
        time_basis="broker_daily_aggregate_first_observed",
    )


def _dispatch(
    *,
    status: str = "accepted",
    reconciliation_status: str | None = None,
    fill_evidence: list[PaperDispatchFillEvidence] | None = None,
    **updates: object,
) -> PaperOrderDispatch:
    fills = fill_evidence or []
    cumulative = sum(item.quantity for item in fills)
    terminal = status in {"filled", "rejected", "cancelled"}
    values: dict[str, object] = {
        "order_plan_id": "plan-001",
        "broker_order_id": "broker-internal-001",
        "run_id": "run-001",
        "idempotency_key": "paper-key-001",
        "request_fingerprint": "sha256:" + "b" * 64,
        "policy_id": "policy-001",
        "policy_version": 1,
        "user_id": "paper-user",
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "purpose": "rebalance",
        "symbol": "005930",
        "side": "buy",
        "quantity": 2,
        "limit_price": 70_000,
        "quote_as_of": NOW - timedelta(seconds=4),
        "quote_last": 70_000,
        "quote_bid": 69_900,
        "quote_ask": 70_100,
        "quote_reference_basis": "l2_midpoint",
        "risk_check_id": "risk-001",
        "risk_check_expires_at": NOW + timedelta(minutes=5),
        "submission_evidence_expires_at": NOW + timedelta(seconds=25),
        "reconciled_snapshot_id": "snapshot-001",
        "reconciled_snapshot_at": NOW - timedelta(seconds=4),
        "snapshot_cash": 300_000,
        "snapshot_equity": 1_000_000,
        "snapshot_symbol_quantity": 0,
        "snapshot_symbol_orderable_quantity": 0,
        "snapshot_daily_loss_ratio": -0.01,
        "snapshot_monthly_loss_ratio": -0.02,
        "broker_orderable_cash": 500_000,
        "broker_orderable_buy_quantity": 6,
        "entry_atr14": 1200,
        "store_id": "store-001",
        "session_id": "session-001",
        "fencing_token": 1,
        "account_scope_fingerprint": FINGERPRINT,
        "status": status,
        "reconciliation_status": reconciliation_status
        or ("reconciled" if terminal else "pending"),
        "attempt_count": 1,
        "dispatch_claimed_at": NOW + timedelta(seconds=1),
        "broker_business_date": date(2026, 7, 10),
        "broker_order_reference": "0000012345",
        "broker_forwarding_order_org_number": "70001",
        "broker_order_branch_number": "91234",
        "broker_order_time": "100001",
        "cumulative_filled_quantity": cumulative,
        "fill_evidence": fills,
        "prepared_at": NOW,
        "updated_at": NOW + timedelta(seconds=20),
        "reconciled_at": NOW + timedelta(seconds=20) if terminal else None,
        "revision": 2,
    }
    values.update(updates)
    return PaperOrderDispatch(**values)


def _applier(repositories: RepositoryRegistry) -> PaperReconciliationApplier:
    return PaperReconciliationApplier(
        repositories=repositories,
        audit=AuditRecorder(repositories.audit_logs),
    )


def test_partial_fill_is_applied_exactly_and_replay_makes_no_changes() -> None:
    repositories = _repositories()
    dispatch = _dispatch(
        status="partially_filled",
        fill_evidence=[_evidence()],
    )
    applier = _applier(repositories)

    first = applier.apply([dispatch])

    assert first.applied_order_plan_ids == ("plan-001",)
    assert first.new_fill_ids == ("kisagg-fill-001",)
    assert first.missing_order_plan_ids == ()
    assert first.blocked_order_plan_ids == ()
    assert repositories.order_plans.require("plan-001").status == (
        OrderStatus.partially_filled
    )
    assert repositories.broker_orders.require("broker-internal-001") == BrokerOrder(
        broker_order_id="broker-internal-001",
        order_plan_id="plan-001",
        broker_mode=BrokerMode.paper,
        status=OrderStatus.partially_filled,
        accepted_at=BROKER_AT,
        broker_reference="0000012345",
    )
    assert repositories.fills.require("kisagg-fill-001") == Fill(
        fill_id="kisagg-fill-001",
        broker_order_id="broker-internal-001",
        order_plan_id="plan-001",
        symbol="005930",
        quantity=1,
        price=70_000,
        notional=70_000,
        filled_at=NOW + timedelta(seconds=20),
    )
    audit_count = len(repositories.audit_logs.list())

    replay = applier.apply([dispatch])

    assert replay.applied_order_plan_ids == ("plan-001",)
    assert replay.new_fill_ids == ()
    assert len(repositories.audit_logs.list()) == audit_count
    assert len(repositories.broker_orders.list()) == 1
    assert len(repositories.fills.list()) == 1


def test_reconciliation_result_input_applies_its_updated_dispatches() -> None:
    repositories = _repositories()
    reconciliation = PaperReconciliationResult(
        updated_dispatches=(_dispatch(status="accepted"),),
        pending_order_plan_ids=("plan-001",),
        blocked_order_plan_ids=(),
        broker_balance=KisBalanceResult(
            positions=(),
            summary=KisBalanceSummary(
                deposit_amount=Decimal("300000"),
                next_day_settlement_amount=Decimal("300000"),
                total_purchase_amount=Decimal("0"),
                total_evaluation_amount=Decimal("1000000"),
                net_asset_amount=Decimal("1000000"),
                evaluation_profit_loss=Decimal("0"),
            ),
            pages_fetched=1,
        ),
        reconciled_at=NOW + timedelta(seconds=20),
    )

    result = _applier(repositories).apply(reconciliation)

    assert result.applied_order_plan_ids == ("plan-001",)
    assert repositories.order_plans.require("plan-001").status == (
        OrderStatus.accepted
    )


@pytest.mark.parametrize(
    ("dispatch", "expected_status", "expected_fill_count"),
    [
        (_dispatch(status="accepted"), OrderStatus.accepted, 0),
        (
            _dispatch(
                status="filled",
                fill_evidence=[_evidence(quantity=2)],
            ),
            OrderStatus.filled,
            1,
        ),
        (_dispatch(status="rejected"), OrderStatus.rejected, 0),
        (
            _dispatch(
                status="cancelled",
                fill_evidence=[_evidence()],
            ),
            OrderStatus.cancelled,
            1,
        ),
    ],
)
def test_reconciled_statuses_follow_valid_order_state_paths(
    dispatch: PaperOrderDispatch,
    expected_status: OrderStatus,
    expected_fill_count: int,
) -> None:
    repositories = _repositories()

    result = _applier(repositories).apply([dispatch])

    assert result.applied_order_plan_ids == ("plan-001",)
    assert repositories.order_plans.require("plan-001").status == expected_status
    assert repositories.broker_orders.require("broker-internal-001").status == (
        expected_status
    )
    assert len(repositories.fills.list()) == expected_fill_count


def test_missing_order_plan_is_reported_without_recreating_local_state() -> None:
    repositories = RepositoryRegistry()
    dispatch = _dispatch(status="accepted")

    result = _applier(repositories).apply([dispatch])

    assert result.applied_order_plan_ids == ()
    assert result.missing_order_plan_ids == ("plan-001",)
    assert repositories.order_plans.list() == []
    assert repositories.broker_orders.list() == []
    assert repositories.fills.list() == []
    assert repositories.audit_logs.list() == []


@pytest.mark.parametrize(
    ("dispatch_update", "reason"),
    [
        ({"idempotency_key": "different-key"}, "order_identity_mismatch"),
        ({"limit_price": 70_100}, "order_identity_mismatch"),
        ({"risk_check_id": "risk-other"}, "order_identity_mismatch"),
        (
            {"risk_check_expires_at": NOW + timedelta(minutes=4)},
            "order_identity_mismatch",
        ),
    ],
)
def test_durable_order_identity_mismatch_blocks_before_any_local_write(
    dispatch_update: dict[str, object],
    reason: str,
) -> None:
    repositories = _repositories()
    before = repositories.order_plans.require("plan-001")

    result = _applier(repositories).apply(
        [_dispatch(status="accepted", **dispatch_update)]
    )

    assert result.blocked_order_plan_ids == ("plan-001",)
    assert result.blocked_reasons == (("plan-001", reason),)
    assert repositories.order_plans.require("plan-001") == before
    assert repositories.broker_orders.list() == []
    assert repositories.fills.list() == []
    assert repositories.audit_logs.list() == []


def test_existing_broker_or_fill_evidence_mismatch_blocks_without_mutation() -> None:
    dispatch = _dispatch(
        status="partially_filled",
        fill_evidence=[_evidence()],
    )

    broker_conflict = _repositories()
    broker_conflict.broker_orders.add(
        BrokerOrder(
            broker_order_id="broker-internal-001",
            order_plan_id="another-plan",
            broker_mode=BrokerMode.paper,
            status=OrderStatus.accepted,
            accepted_at=BROKER_AT,
            broker_reference="0000012345",
        )
    )
    broker_result = _applier(broker_conflict).apply([dispatch])
    assert broker_result.blocked_reasons == (
        ("plan-001", "broker_order_identity_mismatch"),
    )
    assert broker_conflict.order_plans.require("plan-001").status == (
        OrderStatus.submitted
    )

    fill_conflict = _repositories()
    fill_conflict.fills.add(
        Fill(
            fill_id="kisagg-fill-001",
            broker_order_id="broker-internal-001",
            order_plan_id="plan-001",
            symbol="005930",
            quantity=1,
            price=69_000,
            notional=69_000,
            filled_at=NOW + timedelta(seconds=20),
        )
    )
    fill_result = _applier(fill_conflict).apply([dispatch])
    assert fill_result.blocked_reasons == (
        ("plan-001", "fill_evidence_mismatch"),
    )
    assert fill_conflict.order_plans.require("plan-001").status == (
        OrderStatus.submitted
    )
    assert fill_conflict.broker_orders.list() == []


def test_unknown_and_blocked_reconciliation_never_become_local_failures() -> None:
    repositories = _repositories()
    unknown = _dispatch(
        status="outcome_unknown",
        reconciliation_status="pending",
        broker_business_date=None,
        broker_order_reference=None,
        broker_forwarding_order_org_number=None,
        broker_order_branch_number=None,
        broker_order_time=None,
        updated_at=NOW + timedelta(seconds=2),
        reconciled_at=None,
    )

    pending = _applier(repositories).apply([unknown])

    assert pending.pending_order_plan_ids == ("plan-001",)
    assert pending.applied_order_plan_ids == ()
    assert repositories.order_plans.require("plan-001").status == (
        OrderStatus.submitted
    )
    assert repositories.broker_orders.list() == []
    assert repositories.fills.list() == []

    blocked_dispatch = PaperOrderDispatch.model_validate(
        unknown.model_copy(
            update={
                "reconciliation_status": "blocked",
                "last_error_code": "broker_match_ambiguous",
                "revision": unknown.revision + 1,
            }
        ).model_dump()
    )
    blocked = _applier(repositories).apply([blocked_dispatch])

    assert blocked.blocked_reasons == (
        ("plan-001", "broker_reconciliation_blocked"),
    )
    assert repositories.order_plans.require("plan-001").status == (
        OrderStatus.submitted
    )
    assert repositories.audit_logs.list() == []
