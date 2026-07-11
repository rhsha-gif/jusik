from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperOrderDispatch,
)
from quantpilot.packages.core.execution.paper_kill import PaperKillService
from quantpilot.packages.core.kis_paper import (
    KisCancelableOrder,
    KisCancelableOrdersResult,
    KisCancelOrderResult,
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


def _accepted_dispatch(store: PaperStateStore, session) -> PaperOrderDispatch:
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
        entry_atr14=1_200,
        store_id=store.provenance.store_id,
        session_id=session.session_id,
        fencing_token=session.fencing_token,
        account_scope_fingerprint=ACCOUNT,
        prepared_at=prepared_at,
        updated_at=prepared_at,
    )
    store.insert_paper_order_dispatch(prepared)
    claimed = store.claim_dispatch_attempt(
        prepared.order_plan_id,
        session=session,
        claimed_at=prepared_at + timedelta(seconds=1),
    )
    accepted = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "accepted",
                "broker_business_date": NOW.date(),
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


def test_schema_v8_migrates_to_v9_with_existing_provenance(tmp_path) -> None:
    path = tmp_path / "migration.sqlite3"
    with _store(path) as store:
        original_store_id = store.provenance.store_id

    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT state_json FROM state_store_metadata WHERE singleton_id = 1"
        ).fetchone()
        state = json.loads(row[0])
        state["schema_version"] = 8
        connection.execute("DROP TABLE paper_cancel_requests")
        connection.execute("DROP TABLE paper_kill_operations")
        connection.execute(
            "UPDATE state_store_metadata SET schema_version = 8, state_json = ?",
            (json.dumps(state, separators=(",", ":"), sort_keys=True),),
        )
        connection.execute("PRAGMA user_version = 8")
        connection.commit()
    finally:
        connection.close()

    with _store(path) as migrated:
        assert migrated.provenance.store_id == original_store_id
        assert migrated.provenance.schema_version == PAPER_STATE_SCHEMA_VERSION == 9
        assert migrated.list_paper_cancel_requests() == []


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
    ) -> None:
        self.account_scope_fingerprint = ACCOUNT
        self.rows = rows
        self.timeout = timeout
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
        self.cancel_succeeded = True
        return KisCancelOrderResult(
            original_order_number="0000001234",
            cancel_order_number="0000001235",
            order_branch_number="06010",
            cancelled_quantity=10,
            order_time="100002",
            message_code="APBK0013",
        )


class _Reconciler:
    def __init__(self, store: PaperStateStore, client: _Client) -> None:
        self.store = store
        self.client = client

    def reconcile_unresolved(self):
        if self.client.cancel_succeeded:
            for dispatch in self.store.list_paper_order_dispatches():
                if dispatch.status not in {"accepted", "partially_filled"}:
                    continue
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
        _accepted_dispatch(store, session)
        client = _Client(rows=(_cancelable_row(),))
        service = _kill_service(store, session, client)
        result = service.engage(reason="operator_requested")
        replay = service.engage(reason="operator_requested")

        assert result.status == "killed"
        assert result.reconciled_cancelled_count == 1
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
