from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperDispatchFillEvidence,
    PaperOrderDispatch,
    PaperRiskReservation,
)
from quantpilot.packages.core.execution.paper_kill import PaperKillService
from quantpilot.packages.core.kis_paper import (
    KisCancelableOrder,
    KisCancelableOrdersResult,
    KisCancelOrderResult,
    KisPaperBusinessError,
    KisPaperCancelOutcomeUnknown,
)
from quantpilot.packages.db.sqlite_repositories import (
    PAPER_STATE_SCHEMA_VERSION,
    PaperStateConflictError,
    PaperStateStore,
)


NOW = datetime(2026, 7, 11, 1, 0, tzinfo=timezone.utc)
ACCOUNT = "sha256:" + "a" * 64


def _store(path) -> PaperStateStore:
    return PaperStateStore(
        path,
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=ACCOUNT,
    )


def _session(store: PaperStateStore, at: datetime = NOW):
    return store.start_paper_execution_session(
        started_at=at,
        lease_expires_at=at + timedelta(minutes=10),
    )


def _accepted_dispatch(
    store: PaperStateStore,
    session,
    *,
    broker_business_date=None,
    prepared_only: bool = False,
) -> PaperOrderDispatch:
    prepared_at = NOW + timedelta(seconds=1)
    prepared = PaperOrderDispatch(
        order_plan_id="oplan-kill-001",
        run_id="run-kill-001",
        idempotency_key="kill-order-001",
        request_fingerprint="sha256:" + "c" * 64,
        policy_id="policy-paper",
        policy_version=1,
        user_id="local-user",
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        purpose="rebalance",
        symbol="005930",
        side="buy",
        quantity=10,
        limit_price=70_000,
        quote_as_of=NOW,
        quote_last=69_900,
        quote_bid=69_800,
        quote_ask=70_000,
        quote_reference_basis="l2_midpoint",
        risk_check_id="risk-kill-001",
        risk_check_expires_at=NOW + timedelta(minutes=9),
        submission_evidence_expires_at=NOW + timedelta(minutes=8),
        reconciled_snapshot_id="snapshot-kill-001",
        reconciled_snapshot_at=NOW,
        snapshot_cash=2_000_000,
        snapshot_equity=10_000_000,
        snapshot_symbol_quantity=0,
        snapshot_symbol_orderable_quantity=0,
        snapshot_daily_loss_ratio=0,
        snapshot_monthly_loss_ratio=0,
        broker_orderable_cash=1_000_000,
        broker_orderable_buy_quantity=14,
        minimum_cash_reserve_krw=0,
        entry_atr14=1_200,
        store_id=store.provenance.store_id,
        session_id=session.session_id,
        fencing_token=session.fencing_token,
        account_scope_fingerprint=ACCOUNT,
        prepared_at=prepared_at,
        updated_at=prepared_at,
    )
    notional = int(prepared.quantity) * int(prepared.limit_price)
    reservation = PaperRiskReservation(
        reservation_id=f"presv-{prepared.order_plan_id}",
        order_plan_id=prepared.order_plan_id,
        idempotency_key=prepared.idempotency_key,
        kind="cash_buy",
        symbol=prepared.symbol,
        side="buy",
        reserved_cash_krw=notional,
        reserved_gross_exposure_krw=notional,
        broker_orderable_cash_basis_krw=int(
            prepared.broker_orderable_cash or 0
        ),
        broker_orderable_buy_quantity_basis=int(
            prepared.broker_orderable_buy_quantity or 0
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
    store.reserve_and_insert_paper_order_dispatch(prepared, reservation)
    if prepared_only:
        return prepared
    claimed = store.claim_dispatch_attempt(
        prepared.order_plan_id,
        session=session,
        claimed_at=prepared_at + timedelta(seconds=1),
    )
    accepted = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "accepted",
                "broker_business_date": broker_business_date or NOW.date(),
                "broker_order_reference": "0000001234",
                "broker_forwarding_order_org_number": "06010",
                "broker_order_time": "100001",
                "updated_at": claimed.updated_at + timedelta(seconds=1),
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    return store.update_paper_order_dispatch(accepted)


def _cancel(store: PaperStateStore, kill, dispatch, at: datetime) -> PaperCancelRequest:
    return PaperCancelRequest(
        kill_id=kill.kill_id,
        order_plan_id=dispatch.order_plan_id,
        broker_order_id=dispatch.broker_order_id,
        broker_order_reference=dispatch.broker_order_reference,
        broker_forwarding_order_org_number=dispatch.broker_forwarding_order_org_number,
        symbol=dispatch.symbol,
        side=dispatch.side,
        cancelable_quantity=dispatch.quantity,
        original_limit_price=dispatch.limit_price,
        store_id=store.provenance.store_id,
        account_scope_fingerprint=ACCOUNT,
        created_at=at,
        updated_at=at,
    )


def test_kill_state_blocks_submission_until_verified_release(tmp_path) -> None:
    with _store(tmp_path / "kill.sqlite3") as store:
        session = _session(store)
        kill = store.start_paper_kill_operation(
            session=session,
            reason="operator_requested",
            started_at=NOW + timedelta(seconds=1),
        )
        assert kill.status == "killing"
        assert store.paper_kill_blocks_submission() is True

        killed = kill.model_copy(
            update={
                "status": "killed",
                "completed_at": NOW + timedelta(seconds=2),
                "updated_at": NOW + timedelta(seconds=2),
                "revision": 1,
            }
        )
        killed = store.update_paper_kill_operation(killed, session=session)
        released = killed.model_copy(
            update={
                "status": "released",
                "released_at": NOW + timedelta(seconds=3),
                "updated_at": NOW + timedelta(seconds=3),
                "revision": 2,
            }
        )
        store.update_paper_kill_operation(released, session=session)
        assert store.paper_kill_blocks_submission() is False


def test_recovery_required_resumes_same_kill_id(tmp_path) -> None:
    with _store(tmp_path / "resume.sqlite3") as store:
        session = _session(store)
        kill = store.start_paper_kill_operation(
            session=session,
            reason="operator_requested",
            started_at=NOW + timedelta(seconds=1),
        )
        blocked = kill.model_copy(
            update={
                "status": "recovery_required",
                "unresolved_reason_codes": ["cancel_outcome_unknown"],
                "updated_at": NOW + timedelta(seconds=2),
                "revision": 1,
            }
        )
        store.update_paper_kill_operation(blocked, session=session)
        resumed = store.start_paper_kill_operation(
            session=session,
            reason="operator_retry",
            started_at=NOW + timedelta(seconds=3),
        )
        assert resumed.kill_id == kill.kill_id
        assert resumed.status == "killing"
        assert resumed.unresolved_reason_codes == []


def test_cancel_attempt_can_be_claimed_exactly_once(tmp_path) -> None:
    with _store(tmp_path / "cancel.sqlite3") as store:
        session = _session(store)
        dispatch = _accepted_dispatch(store, session)
        kill = store.start_paper_kill_operation(
            session=session,
            reason="operator_requested",
            started_at=NOW + timedelta(seconds=4),
        )
        request = store.create_paper_cancel_request(
            _cancel(store, kill, dispatch, NOW + timedelta(seconds=5)),
            session=session,
        )
        claimed = store.claim_paper_cancel_attempt(
            request.cancel_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=6),
        )
        assert claimed.attempt_count == 1
        with pytest.raises(PaperStateConflictError, match="already claimed"):
            store.claim_paper_cancel_attempt(
                request.cancel_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=7),
            )


def test_unattempted_cancel_can_terminalize_when_fill_wins_race(tmp_path) -> None:
    with _store(tmp_path / "fill-race.sqlite3") as store:
        session = _session(store)
        dispatch = _accepted_dispatch(store, session)
        kill = store.start_paper_kill_operation(
            session=session,
            reason="operator_requested",
            started_at=NOW + timedelta(seconds=4),
        )
        request = store.create_paper_cancel_request(
            _cancel(store, kill, dispatch, NOW + timedelta(seconds=5)),
            session=session,
        )
        filled = PaperCancelRequest.model_validate(
            request.model_copy(
                update={
                    "status": "reconciled_filled",
                    "reconciled_at": NOW + timedelta(seconds=6),
                    "updated_at": NOW + timedelta(seconds=6),
                    "revision": 1,
                }
            ).model_dump()
        )
        persisted = store.update_paper_cancel_request(filled, session=session)
        assert persisted.status == "reconciled_filled"
        assert persisted.attempt_count == 0


def test_schema_v9_migrates_to_v11_and_backfills_open_dispatch(tmp_path) -> None:
    path = tmp_path / "migration.sqlite3"
    with _store(path) as store:
        original_store_id = store.provenance.store_id
        session = _session(store)
        accepted = _accepted_dispatch(store, session)
        write_at = accepted.updated_at + timedelta(microseconds=1)
        partial = PaperOrderDispatch.model_validate(
            accepted.model_copy(
                update={
                    "status": "partially_filled",
                    "broker_order_branch_number": "06010",
                    "cumulative_filled_quantity": 2,
                    "fill_evidence": [
                        PaperDispatchFillEvidence(
                            broker_fill_reference="fill-migration-001",
                            broker_order_id=accepted.broker_order_id,
                            broker_order_reference=accepted.broker_order_reference,
                            symbol=accepted.symbol,
                            side=accepted.side,
                            quantity=2,
                            price=70_000,
                            notional=140_000,
                            evidence_at=write_at,
                            time_basis="broker_execution",
                        )
                    ],
                    "updated_at": write_at,
                    "revision": accepted.revision + 1,
                }
            ).model_dump()
        )
        store.update_paper_order_dispatch(partial)
        original_session_id = session.session_id
        original_fencing_token = session.fencing_token

    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT state_json FROM state_store_metadata WHERE singleton_id = 1"
        ).fetchone()
        state = json.loads(row[0])
        state["schema_version"] = 9
        connection.execute("DROP TABLE paper_risk_reservations")
        connection.execute(
            "UPDATE state_store_metadata SET schema_version = 9, state_json = ?",
            (json.dumps(state, separators=(",", ":"), sort_keys=True),),
        )
        connection.execute("PRAGMA user_version = 9")
        connection.commit()
    finally:
        connection.close()

    with _store(path) as migrated:
        assert migrated.provenance.store_id == original_store_id
        assert migrated.provenance.schema_version == PAPER_STATE_SCHEMA_VERSION == 11
        assert migrated.list_paper_cancel_requests() == []
        restored = migrated.load_paper_order_dispatch("oplan-kill-001")
        assert restored.status == "partially_filled"
        assert restored.fill_evidence[0].broker_fill_reference == "fill-migration-001"
        reservation = migrated.load_paper_risk_reservation("oplan-kill-001")
        assert reservation is not None
        assert reservation.status == "held"
        assert reservation.reserved_cash_krw == 700_000
        assert reservation.reserved_gross_exposure_krw == 700_000
        restored_session = migrated.load_paper_execution_session(original_session_id)
        assert restored_session.fencing_token == original_fencing_token


class _Clock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        self.current += timedelta(microseconds=10)
        return self.current


class _Submission:
    def terminalize_prepared_dispatches_for_kill(self):
        return ()


class _Client:
    def __init__(
        self,
        *,
        rows: tuple[KisCancelableOrder, ...] = (),
        timeout: bool = False,
        response_org_number: str = "06010",
        confirm_terminal: bool = True,
        business_reject: bool = False,
    ) -> None:
        self.account_scope_fingerprint = ACCOUNT
        self.rows = rows
        self.timeout = timeout
        self.response_org_number = response_org_number
        self.confirm_terminal = confirm_terminal
        self.business_reject = business_reject
        self.cancel_calls = 0
        self.cancel_succeeded = False

    def get_cancelable_orders(self) -> KisCancelableOrdersResult:
        return KisCancelableOrdersResult(
            rows=() if self.cancel_succeeded else self.rows,
            pages_fetched=1,
        )

    def cancel_full_remaining_order(self, **_kwargs) -> KisCancelOrderResult:
        self.cancel_calls += 1
        if self.timeout:
            raise KisPaperCancelOutcomeUnknown("ambiguous fake cancel")
        self.cancel_succeeded = self.confirm_terminal
        if self.business_reject:
            raise KisPaperBusinessError("fake business rejection")
        return KisCancelOrderResult(
            original_order_number="0000001234",
            cancel_order_number="0000001235",
            order_branch_number=self.response_org_number,
            cancelled_quantity=10,
            order_time="100002",
            message_code="APBK0013",
        )


class _Reconciler:
    def __init__(self, store: PaperStateStore, client: _Client) -> None:
        self.store = store
        self.client = client

    def reconcile_unresolved(self):
        for dispatch in self.store.list_paper_order_dispatches():
            if dispatch.status not in {"accepted", "partially_filled"}:
                continue
            if dispatch.broker_order_branch_number is None:
                write_at = dispatch.updated_at + timedelta(microseconds=1)
                observed = PaperOrderDispatch.model_validate(
                    dispatch.model_copy(
                        update={
                            "broker_order_branch_number": "06010",
                            "updated_at": write_at,
                            "revision": dispatch.revision + 1,
                        }
                    ).model_dump()
                )
                dispatch = self.store.update_paper_order_dispatch(observed)
            if self.client.cancel_succeeded:
                write_at = dispatch.updated_at + timedelta(microseconds=1)
                cancelled = PaperOrderDispatch.model_validate(
                    dispatch.model_copy(
                        update={
                            "status": "cancelled",
                            "reconciliation_status": "reconciled",
                            "broker_order_branch_number": "06010",
                            "updated_at": write_at,
                            "reconciled_at": write_at,
                            "revision": dispatch.revision + 1,
                        }
                    ).model_dump()
                )
                self.store.update_paper_order_dispatch(cancelled)
        return SimpleNamespace(blocked_order_plan_ids=())


def _cancelable_row(
    *,
    order_number: str = "0000001234",
    symbol: str = "005930",
) -> KisCancelableOrder:
    return KisCancelableOrder(
        order_branch_number="06010",
        order_number=order_number,
        original_order_number="",
        order_division_name="limit",
        symbol=symbol,
        product_name="test",
        revision_cancel_division_name="cancelable",
        order_quantity=10,
        order_price=Decimal("70000"),
        order_time="100001",
        total_filled_quantity=0,
        total_filled_amount=Decimal("0"),
        cancelable_quantity=10,
        side="buy",
        order_division_code="00",
        exchange_division_code="01",
        exchange_id="KRX",
    )


def _kill_service(
    store: PaperStateStore,
    session,
    client: _Client,
) -> PaperKillService:
    return PaperKillService(
        store=store,
        session=session,
        client=client,  # type: ignore[arg-type]
        submission_coordinator=_Submission(),  # type: ignore[arg-type]
        reconciler=_Reconciler(store, client),  # type: ignore[arg-type]
        clock=_Clock(NOW + timedelta(seconds=10)),
    )


def test_kill_service_with_no_working_orders_reaches_killed(tmp_path) -> None:
    with _store(tmp_path / "empty-kill.sqlite3") as store:
        session = _session(store)
        client = _Client()
        result = _kill_service(store, session, client).engage(
            reason="operator_requested"
        )
        assert result.status == "killed"
        assert result.cancel_post_count == 0
        assert client.cancel_calls == 0


def test_kill_service_cancels_managed_order_exactly_once(tmp_path) -> None:
    with _store(tmp_path / "managed-kill.sqlite3") as store:
        session = _session(store)
        dispatch = _accepted_dispatch(store, session)
        client = _Client(rows=(_cancelable_row(),))
        service = _kill_service(store, session, client)
        result = service.engage(reason="operator_requested")
        released = store.load_paper_risk_reservation(dispatch.order_plan_id)
        replay = service.engage(reason="operator_requested")
        replayed = store.load_paper_risk_reservation(dispatch.order_plan_id)

        assert result.status == "killed"
        assert result.reconciled_cancelled_count == 1
        assert released is not None
        assert released.status == "released_cancelled"
        assert released.revision == 1
        assert replayed == released
        assert replay.status == "killed"
        assert client.cancel_calls == 1


def test_cancel_timeout_requires_recovery_and_restart_never_reposts(tmp_path) -> None:
    with _store(tmp_path / "timeout-kill.sqlite3") as store:
        session = _session(store)
        _accepted_dispatch(store, session)
        client = _Client(rows=(_cancelable_row(),), timeout=True)
        service = _kill_service(store, session, client)
        first = service.engage(reason="operator_requested")
        second = service.engage(reason="operator_retry")

        assert first.status == second.status == "recovery_required"
        assert client.cancel_calls == 1
        request = store.list_paper_cancel_requests()[0]
        assert request.status == "cancel_outcome_unknown"


def test_cancel_claim_crash_restarts_query_only_without_post(tmp_path) -> None:
    with _store(tmp_path / "claim-crash.sqlite3") as store:
        session = _session(store)
        dispatch = _accepted_dispatch(store, session)
        client = _Client(rows=(_cancelable_row(),))
        _Reconciler(store, client).reconcile_unresolved()
        dispatch = store.load_paper_order_dispatch(dispatch.order_plan_id)
        kill = store.start_paper_kill_operation(
            session=session,
            reason="operator_requested",
            started_at=NOW + timedelta(seconds=5),
        )
        request = store.create_paper_cancel_request(
            _cancel(store, kill, dispatch, NOW + timedelta(seconds=6)),
            session=session,
        )
        store.claim_paper_cancel_attempt(
            request.cancel_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=7),
        )

        result = _kill_service(store, session, client).engage(
            reason="operator_retry"
        )
        assert result.status == "recovery_required"
        assert client.cancel_calls == 0
        assert store.load_paper_cancel_request(request.cancel_id).status == (
            "cancel_outcome_unknown"
        )


def test_cancel_acceptance_persistence_failure_recovers_by_query(tmp_path) -> None:
    with _store(tmp_path / "acceptance-write-failure.sqlite3") as store:
        session = _session(store)
        _accepted_dispatch(store, session)
        client = _Client(rows=(_cancelable_row(),))
        original_update = store.update_paper_cancel_request
        failed_once = False

        def fail_acceptance_once(request, *, session):
            nonlocal failed_once
            if request.status == "cancel_accepted" and not failed_once:
                failed_once = True
                raise RuntimeError("simulated persistence failure")
            return original_update(request, session=session)

        store.update_paper_cancel_request = fail_acceptance_once  # type: ignore[method-assign]
        result = _kill_service(store, session, client).engage(
            reason="operator_requested"
        )
        assert failed_once is True
        assert result.status == "killed"
        assert client.cancel_calls == 1
        assert store.list_paper_cancel_requests()[0].status == (
            "reconciled_cancelled"
        )


def test_external_working_order_is_quarantined_without_cancel(tmp_path) -> None:
    with _store(tmp_path / "external-kill.sqlite3") as store:
        session = _session(store)
        client = _Client(rows=(_cancelable_row(order_number="9999999999"),))
        result = _kill_service(store, session, client).engage(
            reason="operator_requested"
        )
        assert result.status == "recovery_required"
        assert "external_working_order_detected" in result.unresolved_reason_codes
        assert client.cancel_calls == 0


def test_duplicate_broker_match_fails_closed_without_cancel(tmp_path) -> None:
    with _store(tmp_path / "duplicate-kill.sqlite3") as store:
        session = _session(store)
        _accepted_dispatch(store, session)
        row = _cancelable_row()
        client = _Client(rows=(row, row))
        result = _kill_service(store, session, client).engage(
            reason="operator_requested"
        )
        assert result.status == "recovery_required"
        assert "broker_cancel_identity_ambiguous" in result.unresolved_reason_codes
        assert client.cancel_calls == 0


@pytest.mark.parametrize(
    "row",
    [
        replace(_cancelable_row(), order_branch_number="99999"),
        replace(_cancelable_row(), order_time="100009"),
        replace(
            _cancelable_row(),
            total_filled_quantity=1,
            total_filled_amount=Decimal("70000"),
            cancelable_quantity=9,
        ),
        replace(_cancelable_row(), order_price=Decimal("70001")),
    ],
)
def test_cross_source_identity_mismatch_never_posts_cancel(tmp_path, row) -> None:
    with _store(tmp_path / f"mismatch-{row.order_time}-{row.order_price}.sqlite3") as store:
        session = _session(store)
        _accepted_dispatch(store, session)
        client = _Client(rows=(row,))
        result = _kill_service(store, session, client).engage(
            reason="operator_requested"
        )
        assert result.status == "recovery_required"
        assert client.cancel_calls == 0


def test_prior_business_date_identity_never_posts_cancel(tmp_path) -> None:
    with _store(tmp_path / "prior-date.sqlite3") as store:
        session = _session(store)
        _accepted_dispatch(
            store,
            session,
            broker_business_date=NOW.date() - timedelta(days=1),
        )
        client = _Client(rows=(_cancelable_row(),))
        result = _kill_service(store, session, client).engage(
            reason="operator_requested"
        )
        assert result.status == "recovery_required"
        assert client.cancel_calls == 0


def test_cancel_response_identity_mismatch_becomes_outcome_unknown(tmp_path) -> None:
    with _store(tmp_path / "response-mismatch.sqlite3") as store:
        session = _session(store)
        _accepted_dispatch(store, session)
        client = _Client(
            rows=(_cancelable_row(),),
            response_org_number="99999",
            confirm_terminal=False,
        )
        result = _kill_service(store, session, client).engage(
            reason="operator_requested"
        )
        assert result.status == "recovery_required"
        assert client.cancel_calls == 1
        assert store.list_paper_cancel_requests()[0].status == (
            "cancel_outcome_unknown"
        )


def test_business_rejection_can_later_reconcile_terminal(tmp_path) -> None:
    with _store(tmp_path / "business-rejection.sqlite3") as store:
        session = _session(store)
        _accepted_dispatch(store, session)
        client = _Client(
            rows=(_cancelable_row(),),
            business_reject=True,
            confirm_terminal=True,
        )
        result = _kill_service(store, session, client).engage(
            reason="operator_requested"
        )
        assert result.status == "killed"
        assert store.list_paper_cancel_requests()[0].status == (
            "reconciled_cancelled"
        )


def test_business_rejection_while_working_never_reposts(tmp_path) -> None:
    with _store(tmp_path / "business-rejection-working.sqlite3") as store:
        session = _session(store)
        _accepted_dispatch(store, session)
        client = _Client(
            rows=(_cancelable_row(),),
            business_reject=True,
            confirm_terminal=False,
        )
        service = _kill_service(store, session, client)
        first = service.engage(reason="operator_requested")
        second = service.engage(reason="operator_retry")

        assert first.status == second.status == "recovery_required"
        assert client.cancel_calls == 1
        assert store.list_paper_cancel_requests()[0].status == "rejected"


def test_release_rechecks_and_detects_new_external_order(tmp_path) -> None:
    with _store(tmp_path / "release-proof.sqlite3") as store:
        session = _session(store)
        clean_client = _Client()
        clean_service = _kill_service(store, session, clean_client)
        assert clean_service.engage(reason="operator_requested").status == "killed"

        external_client = _Client(rows=(_cancelable_row(order_number="9999999999"),))
        blocked = _kill_service(store, session, external_client).release()
        assert blocked.status == "recovery_required"
        assert store.paper_kill_blocks_submission() is True


def test_killed_store_rejects_new_reserved_dispatch_before_release(tmp_path) -> None:
    with _store(tmp_path / "release-prepared.sqlite3") as store:
        session = _session(store)
        client = _Client()
        service = _kill_service(store, session, client)
        assert service.engage(reason="operator_requested").status == "killed"
        with pytest.raises(PaperStateConflictError, match="kill blocks reservation"):
            _accepted_dispatch(store, session, prepared_only=True)

        released = service.release()
        assert released.status == "released"
