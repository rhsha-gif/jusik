from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperBrokerReconciler,
)
from quantpilot.packages.core.kis_paper import (
    KisBalanceResult,
    KisBalanceSummary,
    KisDailyOrderFill,
    KisDailyOrdersResult,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperOrderDispatch,
    PaperRiskReservation,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
FINGERPRINT = "sha256:" + "c" * 64


def _prepared(**updates: object) -> PaperOrderDispatch:
    values: dict[str, object] = {
        "order_plan_id": "plan-001",
        "broker_order_id": "broker-internal-001",
        "run_id": "run-001",
        "idempotency_key": "paper-idempotency-001",
        "request_fingerprint": "sha256:" + "d" * 64,
        "policy_id": "policy-001",
        "policy_version": 1,
        "user_id": "paper-user",
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "purpose": "rebalance",
        "symbol": "005930",
        "side": "buy",
        "quantity": 2,
        "limit_price": 70000,
        "quote_as_of": NOW - timedelta(seconds=5),
        "quote_last": 70000,
        "quote_bid": 69900,
        "quote_ask": 70100,
        "quote_reference_basis": "l2_midpoint",
        "risk_check_id": "risk-001",
        "risk_check_expires_at": NOW + timedelta(minutes=5),
        "submission_evidence_expires_at": NOW + timedelta(seconds=25),
        "reconciled_snapshot_id": "snapshot-001",
        "reconciled_snapshot_at": NOW - timedelta(seconds=4),
        "snapshot_cash": 300000,
        "snapshot_equity": 1000000,
        "snapshot_symbol_quantity": 0,
        "snapshot_symbol_orderable_quantity": 0,
        "snapshot_daily_loss_ratio": -0.01,
        "snapshot_monthly_loss_ratio": -0.02,
        "broker_orderable_cash": 500000,
        "broker_orderable_buy_quantity": 6,
        "minimum_cash_reserve_krw": 0,
        "entry_atr14": 1200,
        "store_id": "replace-from-store",
        "session_id": "replace-from-session",
        "fencing_token": 1,
        "account_scope_fingerprint": FINGERPRINT,
        "prepared_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return PaperOrderDispatch(**values)


def _insert_reserved(
    store: PaperStateStore,
    prepared: PaperOrderDispatch,
) -> PaperOrderDispatch:
    notional = int(prepared.quantity) * int(prepared.limit_price)
    reservation = PaperRiskReservation(
        reservation_id=f"presv-{prepared.order_plan_id}",
        order_plan_id=prepared.order_plan_id,
        idempotency_key=prepared.idempotency_key,
        kind="cash_buy" if prepared.side == "buy" else "sell_quantity",
        symbol=prepared.symbol,
        side=prepared.side,
        reserved_cash_krw=notional if prepared.side == "buy" else None,
        reserved_sell_quantity=(
            int(prepared.quantity) if prepared.side == "sell" else None
        ),
        reserved_gross_exposure_krw=(
            notional if prepared.side == "buy" else 0
        ),
        broker_orderable_cash_basis_krw=int(
            prepared.broker_orderable_cash or 0
        ) if prepared.side == "buy" else None,
        broker_orderable_buy_quantity_basis=int(
            prepared.broker_orderable_buy_quantity or 0
        ) if prepared.side == "buy" else None,
        snapshot_orderable_quantity_basis=(
            int(prepared.snapshot_symbol_orderable_quantity)
            if prepared.side == "sell"
            else None
        ),
        snapshot_gross_exposure_basis_krw=int(
            prepared.snapshot_equity - prepared.snapshot_cash
        ),
        minimum_cash_reserve_krw=0,
        gross_exposure_limit_krw=int(prepared.snapshot_equity),
        store_id=prepared.store_id,
        session_id=prepared.session_id,
        fencing_token=prepared.fencing_token,
        account_scope_fingerprint=prepared.account_scope_fingerprint,
        created_at=prepared.prepared_at,
        updated_at=prepared.prepared_at,
    )
    persisted, _ = store.reserve_and_insert_paper_order_dispatch(
        prepared,
        reservation,
    )
    return persisted


def _claimed(store: PaperStateStore) -> PaperOrderDispatch:
    session = store.start_paper_execution_session(
        started_at=NOW - timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(hours=1),
    )
    prepared = _prepared(
        store_id=store.provenance.store_id,
        session_id=session.session_id,
        fencing_token=session.fencing_token,
    )
    _insert_reserved(store, prepared)
    return store.claim_dispatch_attempt(
        prepared.order_plan_id,
        session=session,
        claimed_at=NOW + timedelta(microseconds=1),
    )


def _unknown(store: PaperStateStore) -> PaperOrderDispatch:
    claimed = _claimed(store)
    unknown = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "outcome_unknown",
                "last_error_code": "broker_response_ambiguous",
                "updated_at": NOW + timedelta(microseconds=2),
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    return store.update_paper_order_dispatch(
        unknown,
        mutation_origin="broker_post_result",
    )


def _accepted_from_post(store: PaperStateStore) -> PaperOrderDispatch:
    claimed = _claimed(store)
    accepted = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "accepted",
                "broker_business_date": date(2026, 7, 10),
                "broker_order_reference": "0000012345",
                "broker_forwarding_order_org_number": "70001",
                "broker_order_time": "100001",
                "updated_at": NOW + timedelta(microseconds=2),
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    return store.update_paper_order_dispatch(
        accepted,
        mutation_origin="broker_post_result",
    )


def _row(
    *,
    filled: int = 1,
    average: str = "70000",
    remaining: int = 1,
    amount: str = "70000",
    rejected: int = 0,
    cancelled: bool = False,
    cancel_quantity: int = 0,
    order_number: str = "0000012345",
    order_branch_number: str = "91234",
) -> KisDailyOrderFill:
    return KisDailyOrderFill(
        order_number=order_number,
        original_order_number="",
        order_branch_number=order_branch_number,
        order_date="20260710",
        order_time="100001",
        symbol="005930",
        product_name="fixture security",
        side="buy",
        order_quantity=2,
        order_price=Decimal("70000"),
        total_filled_quantity=filled,
        average_fill_price=Decimal(average),
        remaining_quantity=remaining,
        rejected_quantity=rejected,
        cancelled=cancelled,
        confirmed_cancel_quantity=cancel_quantity,
        total_filled_amount=Decimal(amount),
    )


def _balance() -> KisBalanceResult:
    return KisBalanceResult(
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
    )


class FakeClient:
    account_scope_fingerprint = FINGERPRINT

    def __init__(
        self,
        *rows: KisDailyOrderFill,
        expected_start: str = "2026-07-10",
    ) -> None:
        self.rows = tuple(rows)
        self.expected_start = expected_start
        self.daily_calls = 0
        self.balance_calls = 0

    def get_balance(self, *, exchange: str = "KRX") -> KisBalanceResult:
        assert exchange == "KRX"
        self.balance_calls += 1
        return _balance()

    def get_daily_orders_and_fills(
        self,
        start_date,
        end_date,
        *,
        exchange: str = "KRX",
        as_of_date=None,
    ) -> KisDailyOrdersResult:
        assert start_date.isoformat() == self.expected_start
        assert end_date.isoformat() == "2026-07-10"
        assert as_of_date == end_date
        assert exchange == "KRX"
        self.daily_calls += 1
        return KisDailyOrdersResult(rows=self.rows, pages_fetched=1)


def _reconciler(store: PaperStateStore, client: FakeClient) -> PaperBrokerReconciler:
    return PaperBrokerReconciler(
        store=store,
        client=client,  # type: ignore[arg-type]
        clock=lambda: NOW + timedelta(seconds=20),
    )


def _old_unknown(store: PaperStateStore) -> PaperOrderDispatch:
    old = datetime(2026, 3, 1, 1, 0, tzinfo=timezone.utc)
    session = store.start_paper_execution_session(
        started_at=old - timedelta(minutes=1),
        lease_expires_at=old + timedelta(hours=1),
    )
    prepared = _prepared(
        order_plan_id="plan-old",
        broker_order_id="broker-old",
        idempotency_key="paper-idempotency-old",
        quote_as_of=old - timedelta(seconds=5),
        risk_check_expires_at=old + timedelta(minutes=5),
        submission_evidence_expires_at=old + timedelta(seconds=25),
        reconciled_snapshot_at=old - timedelta(seconds=4),
        store_id=store.provenance.store_id,
        session_id=session.session_id,
        fencing_token=session.fencing_token,
        prepared_at=old,
        updated_at=old,
    )
    _insert_reserved(store, prepared)
    claimed = store.claim_dispatch_attempt(
        prepared.order_plan_id,
        session=session,
        claimed_at=old + timedelta(microseconds=1),
    )
    unknown = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "outcome_unknown",
                "last_error_code": "broker_response_ambiguous",
                "updated_at": old + timedelta(microseconds=2),
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    return store.update_paper_order_dispatch(
        unknown,
        mutation_origin="broker_post_result",
    )


def _current_protective_claimed(store: PaperStateStore) -> PaperOrderDispatch:
    session = store.start_paper_execution_session(
        started_at=NOW - timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(hours=1),
    )
    prepared = _prepared(
        order_plan_id="plan-current-protective",
        broker_order_id="broker-current-protective",
        idempotency_key="paper-idempotency-current-protective",
        purpose="protective_exit",
        side="sell",
        quote_last=69900,
        quote_reference_basis="best_bid",
        snapshot_symbol_quantity=2,
        snapshot_symbol_orderable_quantity=2,
        broker_orderable_cash=None,
        broker_orderable_buy_quantity=None,
        store_id=store.provenance.store_id,
        session_id=session.session_id,
        fencing_token=session.fencing_token,
    )
    _insert_reserved(store, prepared)
    return store.claim_dispatch_attempt(
        prepared.order_plan_id,
        session=session,
        claimed_at=NOW + timedelta(microseconds=1),
    )


def test_expired_history_window_isolated_for_manual_resolution(tmp_path) -> None:
    with PaperStateStore(
        tmp_path / "history-window.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        old = _old_unknown(store)
        current = _current_protective_claimed(store)
        client = FakeClient()

        result = _reconciler(store, client).reconcile_unresolved()

        by_id = {item.order_plan_id: item for item in result.updated_dispatches}
        assert client.balance_calls == 1
        assert client.daily_calls == 1
        assert by_id[old.order_plan_id].reconciliation_status == "blocked"
        assert by_id[old.order_plan_id].last_error_code == (
            "broker_history_window_manual_resolution_required"
        )
        assert by_id[current.order_plan_id].status == "dispatch_claimed"
        assert by_id[current.order_plan_id].reconciliation_status == "pending"
        assert result.blocked_order_plan_ids == (old.order_plan_id,)


def test_all_expired_history_skips_daily_query_and_persists_block(tmp_path) -> None:
    database_path = tmp_path / "all-history-expired.sqlite3"
    with PaperStateStore(
        database_path,
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        old = _old_unknown(store)
        client = FakeClient()

        result = _reconciler(store, client).reconcile_unresolved()

        assert client.balance_calls == 1
        assert client.daily_calls == 0
        assert result.pending_order_plan_ids == ()
        assert result.blocked_order_plan_ids == (old.order_plan_id,)

    with PaperStateStore(
        database_path,
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as reopened:
        persisted = reopened.load_paper_order_dispatch(old.order_plan_id)
        assert persisted is not None
        assert persisted.status == "outcome_unknown"
        assert persisted.attempt_count == 1
        assert persisted.reconciliation_status == "blocked"
        assert persisted.last_error_code == (
            "broker_history_window_manual_resolution_required"
        )


def test_unique_match_creates_idempotent_delta_evidence_then_completes(tmp_path) -> None:
    with PaperStateStore(
        tmp_path / "delta.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        unknown = _unknown(store)
        reconciler = _reconciler(store, FakeClient())

        partial = reconciler.reconcile_dispatch(
            unknown,
            (_row(),),
            reconciled_at=NOW + timedelta(seconds=20),
        )

        assert partial.status == "partially_filled"
        assert partial.reconciliation_status == "pending"
        assert partial.cumulative_filled_quantity == 1
        assert len(partial.fill_evidence) == 1
        assert partial.fill_evidence[0].time_basis == (
            "broker_daily_aggregate_first_observed"
        )
        held = store.load_paper_risk_reservation(partial.order_plan_id)
        assert held is not None
        assert held.status == "held"
        assert held.reserved_cash_krw == 140_000
        same = reconciler.reconcile_dispatch(
            partial,
            (_row(),),
            reconciled_at=NOW + timedelta(seconds=21),
        )
        assert same == partial

        filled_row = _row(
            filled=2,
            average="70500",
            remaining=0,
            amount="141000",
        )
        filled = reconciler.reconcile_dispatch(
            partial,
            (filled_row,),
            reconciled_at=NOW + timedelta(seconds=22),
        )
        assert filled.status == "filled"
        assert filled.reconciliation_status == "reconciled"
        assert filled.cumulative_filled_quantity == 2
        assert len(filled.fill_evidence) == 2
        assert filled.fill_evidence[1].quantity == 1
        assert filled.fill_evidence[1].price == 71000
        released = store.load_paper_risk_reservation(filled.order_plan_id)
        assert released is not None
        assert released.status == "released_filled"
        assert released.release_reason == "filled"


def test_post_forwarding_org_and_daily_branch_are_preserved_independently(
    tmp_path,
) -> None:
    with PaperStateStore(
        tmp_path / "split-organizations.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        accepted = _accepted_from_post(store)
        assert accepted.broker_forwarding_order_org_number == "70001"
        assert accepted.broker_order_branch_number is None

        filled = _reconciler(store, FakeClient()).reconcile_dispatch(
            accepted,
            (
                _row(
                    filled=2,
                    average="70000",
                    remaining=0,
                    amount="140000",
                    order_branch_number="91234",
                ),
            ),
            reconciled_at=NOW + timedelta(seconds=20),
        )

        assert filled.status == "filled"
        assert filled.reconciliation_status == "reconciled"
        assert filled.broker_forwarding_order_org_number == "70001"
        assert filled.broker_order_branch_number == "91234"


def test_known_daily_order_branch_mismatch_blocks_reconciliation(tmp_path) -> None:
    with PaperStateStore(
        tmp_path / "branch-mismatch.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        accepted = _accepted_from_post(store)
        reconciler = _reconciler(store, FakeClient())
        branch_bound = reconciler.reconcile_dispatch(
            accepted,
            (_row(filled=0, average="0", remaining=2, amount="0"),),
            reconciled_at=NOW + timedelta(seconds=20),
        )
        assert branch_bound.broker_order_branch_number == "91234"

        blocked = reconciler.reconcile_dispatch(
            branch_bound,
            (_row(order_branch_number="99999"),),
            reconciled_at=NOW + timedelta(seconds=21),
        )

        assert blocked.status == "accepted"
        assert blocked.reconciliation_status == "blocked"
        assert blocked.last_error_code == "broker_order_branch_mismatch"
        assert blocked.broker_forwarding_order_org_number == "70001"
        assert blocked.broker_order_branch_number == "91234"
        assert blocked.fill_evidence == []


def test_zero_match_stays_unknown_and_multiple_match_blocks_without_guessing(tmp_path) -> None:
    with PaperStateStore(
        tmp_path / "matching.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        unknown = _unknown(store)
        reconciler = _reconciler(store, FakeClient())

        assert reconciler.reconcile_dispatch(unknown, ()) == unknown
        blocked = reconciler.reconcile_dispatch(
            unknown,
            (_row(), _row(order_number="0000099999")),
            reconciled_at=NOW + timedelta(seconds=20),
        )
        assert blocked.status == "outcome_unknown"
        assert blocked.reconciliation_status == "blocked"
        assert blocked.last_error_code == "broker_match_ambiguous"


def test_inconsistent_aggregate_fill_blocks_and_preserves_existing_evidence(tmp_path) -> None:
    with PaperStateStore(
        tmp_path / "invalid.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        unknown = _unknown(store)
        invalid = _row(filled=1, average="70000", remaining=1, amount="1")

        blocked = _reconciler(store, FakeClient()).reconcile_dispatch(
            unknown,
            (invalid,),
            reconciled_at=NOW + timedelta(seconds=20),
        )

        assert blocked.reconciliation_status == "blocked"
        assert blocked.last_error_code == "broker_fill_amount_inconsistent"
        assert blocked.fill_evidence == []


def test_reconcile_unresolved_queries_full_pages_and_returns_fresh_balance(tmp_path) -> None:
    client = FakeClient(_row(filled=0, average="0", remaining=2, amount="0"))
    with PaperStateStore(
        tmp_path / "all.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        _unknown(store)

        result = _reconciler(store, client).reconcile_unresolved()

        assert client.balance_calls == 1
        assert client.daily_calls == 1
        assert result.updated_dispatches[0].status == "accepted"
        assert result.pending_order_plan_ids == ("plan-001",)
        assert result.blocked_order_plan_ids == ()
        assert result.broker_balance.summary.net_asset_amount == Decimal("1000000")


def test_process_kill_after_claim_is_reconciled_without_resubmission(tmp_path) -> None:
    client = FakeClient(_row(filled=0, average="0", remaining=2, amount="0"))
    with PaperStateStore(
        tmp_path / "claimed.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        claimed = _claimed(store)
        assert claimed.status == "dispatch_claimed"

        result = _reconciler(store, client).reconcile_unresolved()

        assert result.updated_dispatches[0].status == "accepted"
        assert result.pending_order_plan_ids == ("plan-001",)
        assert client.daily_calls == 1


@pytest.mark.parametrize(
    ("row", "expected_status"),
    [
        (
            _row(
                filled=0,
                average="0",
                remaining=0,
                amount="0",
                rejected=2,
            ),
            "rejected",
        ),
        (
            _row(
                filled=0,
                average="0",
                remaining=0,
                amount="0",
                cancelled=True,
                cancel_quantity=2,
            ),
            "cancelled",
        ),
    ],
)
def test_terminal_rejection_and_cancellation_are_mapped_exactly(
    tmp_path,
    row: KisDailyOrderFill,
    expected_status: str,
) -> None:
    with PaperStateStore(
        tmp_path / f"{expected_status}.sqlite3",
        data_mode="paper_trading",
        account_scope_fingerprint=FINGERPRINT,
    ) as store:
        terminal = _reconciler(store, FakeClient()).reconcile_dispatch(
            _unknown(store),
            (row,),
            reconciled_at=NOW + timedelta(seconds=20),
        )

        assert terminal.status == expected_status
        assert terminal.reconciliation_status == "reconciled"
        assert terminal.cumulative_filled_quantity == 0
        assert terminal.fill_evidence == []
        released = store.load_paper_risk_reservation(terminal.order_plan_id)
        assert released is not None
        assert released.status == (
            "released_rejected"
            if expected_status == "rejected"
            else "released_cancelled"
        )
