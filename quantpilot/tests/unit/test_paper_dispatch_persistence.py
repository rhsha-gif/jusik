from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.operator.position_ledger import (
    PaperDispatchFillEvidence,
    PaperExecutionSession,
    PaperOrderDispatch,
    PaperRiskReservation,
    PaperRunCheckpoint,
)
from quantpilot.packages.core.schemas import UserPolicy
from quantpilot.packages.db.sqlite_repositories import (
    PAPER_STATE_SCHEMA_VERSION,
    PaperRiskReservationRejected,
    PaperStateConflictError,
    PaperStateMigrationRequired,
    PaperStateProvenanceError,
    PaperStateStore,
)


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
ACCOUNT_A = "sha256:" + "a" * 64
ACCOUNT_B = "sha256:" + "b" * 64


def _paper_store(path, *, account: str = ACCOUNT_A) -> PaperStateStore:
    return PaperStateStore(
        path,
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=account,
    )


def _session(
    store: PaperStateStore,
    *,
    started_at: datetime = NOW,
    lease_expires_at: datetime | None = None,
) -> PaperExecutionSession:
    return store.start_paper_execution_session(
        started_at=started_at,
        lease_expires_at=lease_expires_at or started_at + timedelta(minutes=5),
    )


def _dispatch(
    store: PaperStateStore,
    session: PaperExecutionSession,
    **updates: object,
) -> PaperOrderDispatch:
    prepared_at = NOW + timedelta(seconds=1)
    values: dict[str, object] = {
        "order_plan_id": "oplan-paper-001",
        "run_id": "run-paper-001",
        "idempotency_key": "paper-order-001",
        "request_fingerprint": "sha256:" + "c" * 64,
        "policy_id": "policy-paper",
        "policy_version": 3,
        "user_id": "local-user",
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "purpose": "rebalance",
        "symbol": "005930",
        "side": "buy",
        "quantity": 10.0,
        "limit_price": 70_000.0,
        "quote_as_of": NOW,
        "quote_last": 69_900.0,
        "quote_bid": 69_800.0,
        "quote_ask": 70_000.0,
        "quote_reference_basis": "l2_midpoint",
        "risk_check_id": "risk-final-001",
        "risk_check_expires_at": NOW + timedelta(minutes=10),
        "submission_evidence_expires_at": NOW + timedelta(minutes=9),
        "reconciled_snapshot_id": "snapshot-paper-001",
        "reconciled_snapshot_at": NOW,
        "snapshot_cash": 2_000_000.0,
        "snapshot_equity": 10_000_000.0,
        "snapshot_symbol_quantity": 5.0,
        "snapshot_symbol_orderable_quantity": 4.0,
        "snapshot_daily_loss_ratio": -0.01,
        "snapshot_monthly_loss_ratio": -0.02,
        "broker_orderable_cash": 1_000_000.0,
        "broker_orderable_buy_quantity": 14.0,
        "minimum_cash_reserve_krw": 0,
        "entry_atr14": 1_200.0,
        "store_id": store.provenance.store_id,
        "session_id": session.session_id,
        "fencing_token": session.fencing_token,
        "account_scope_fingerprint": ACCOUNT_A,
        "prepared_at": prepared_at,
        "updated_at": prepared_at,
    }
    values.update(updates)
    return PaperOrderDispatch(**values)


def _reservation(
    dispatch: PaperOrderDispatch,
    **updates: object,
) -> PaperRiskReservation:
    quantity = int(dispatch.quantity)
    notional = quantity * int(dispatch.limit_price)
    current_gross = max(0, int(dispatch.snapshot_equity - dispatch.snapshot_cash))
    minimum_cash_reserve = dispatch.minimum_cash_reserve_krw or 0
    values: dict[str, object] = {
        "reservation_id": f"presv-{dispatch.order_plan_id}",
        "order_plan_id": dispatch.order_plan_id,
        "idempotency_key": dispatch.idempotency_key,
        "kind": "cash_buy" if dispatch.side == "buy" else "sell_quantity",
        "symbol": dispatch.symbol,
        "side": dispatch.side,
        "reserved_cash_krw": notional if dispatch.side == "buy" else None,
        "reserved_sell_quantity": quantity if dispatch.side == "sell" else None,
        "reserved_gross_exposure_krw": notional if dispatch.side == "buy" else 0,
        "broker_orderable_cash_basis_krw": (
            int(dispatch.broker_orderable_cash or 0)
            if dispatch.side == "buy"
            else None
        ),
        "broker_orderable_buy_quantity_basis": (
            int(dispatch.broker_orderable_buy_quantity or 0)
            if dispatch.side == "buy"
            else None
        ),
        "snapshot_orderable_quantity_basis": (
            int(dispatch.snapshot_symbol_orderable_quantity)
            if dispatch.side == "sell"
            else None
        ),
        "snapshot_gross_exposure_basis_krw": current_gross,
        "minimum_cash_reserve_krw": minimum_cash_reserve,
        "gross_exposure_limit_krw": max(
            0,
            int(dispatch.snapshot_equity) - minimum_cash_reserve,
        ),
        "store_id": dispatch.store_id,
        "session_id": dispatch.session_id,
        "fencing_token": dispatch.fencing_token,
        "account_scope_fingerprint": dispatch.account_scope_fingerprint,
        "created_at": dispatch.prepared_at,
        "updated_at": dispatch.prepared_at,
    }
    values.update(updates)
    if (
        "gross_exposure_limit_krw" in updates
        and "minimum_cash_reserve_krw" not in updates
    ):
        values["minimum_cash_reserve_krw"] = max(
            0,
            int(dispatch.snapshot_equity)
            - int(values["gross_exposure_limit_krw"]),
        )
    return PaperRiskReservation(**values)


def _insert_reserved_dispatch(
    store: PaperStateStore,
    dispatch: PaperOrderDispatch,
) -> PaperOrderDispatch:
    prepared, _ = store.reserve_and_insert_paper_order_dispatch(
        dispatch,
        _reservation(dispatch),
    )
    return prepared


def _sell_dispatch(
    store: PaperStateStore,
    session: PaperExecutionSession,
    *,
    order_plan_id: str,
    idempotency_key: str,
    purpose: str,
    prepared_at: datetime | None = None,
) -> PaperOrderDispatch:
    prepared = prepared_at or NOW + timedelta(seconds=1)
    return _dispatch(
        store,
        session,
        order_plan_id=order_plan_id,
        idempotency_key=idempotency_key,
        request_fingerprint="sha256:" + "d" * 64,
        side="sell",
        purpose=purpose,
        quantity=3.0,
        broker_orderable_cash=None,
        broker_orderable_buy_quantity=None,
        entry_atr14=None,
        prepared_at=prepared,
        updated_at=prepared,
    )


def _downgrade_paper_schema_to_v9(path) -> None:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT state_json FROM state_store_metadata WHERE singleton_id = 1"
        ).fetchone()
        state = json.loads(row[0])
        state["schema_version"] = 9
        connection.execute("DROP TABLE paper_execution_event_identity_keys")
        connection.execute("DROP TABLE paper_execution_events")
        connection.execute("DROP TABLE paper_risk_reservations")
        for order_plan_id, payload in connection.execute(
            "SELECT order_plan_id, state_json FROM paper_order_dispatches"
        ).fetchall():
            dispatch_state = json.loads(payload)
            dispatch_state.pop("minimum_cash_reserve_krw", None)
            connection.execute(
                "UPDATE paper_order_dispatches SET state_json = ? "
                "WHERE order_plan_id = ?",
                (
                    json.dumps(
                        dispatch_state,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    order_plan_id,
                ),
            )
        connection.execute(
            "UPDATE state_store_metadata SET schema_version = 9, state_json = ?",
            (json.dumps(state, separators=(",", ":"), sort_keys=True),),
        )
        connection.execute("PRAGMA user_version = 9")
        connection.commit()
    finally:
        connection.close()


def test_store_provenance_is_immutable_across_reopen(tmp_path) -> None:
    path = tmp_path / "fixture.sqlite3"
    with PaperStateStore(path) as store:
        provenance = store.provenance
        assert provenance.data_mode == "fixture"
        assert provenance.broker_environment == "fixture_mock"
        assert provenance.account_scope_fingerprint is None
        assert provenance.schema_version == PAPER_STATE_SCHEMA_VERSION

    with PaperStateStore(path) as reopened:
        assert reopened.provenance == provenance

    with pytest.raises(PaperStateProvenanceError, match="does not match"):
        _paper_store(path)


def test_paper_store_rejects_account_environment_and_fixture_seed_mismatch(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        assert store.provenance.account_scope_fingerprint == ACCOUNT_A

    with pytest.raises(PaperStateProvenanceError, match="does not match"):
        _paper_store(path, account=ACCOUNT_B)
    with pytest.raises(PaperStateProvenanceError, match="invalid"):
        PaperStateStore(
            path,
            data_mode="paper_trading",
            broker_environment="fixture_mock",
            account_scope_fingerprint=ACCOUNT_A,
        )
    with pytest.raises(PaperStateProvenanceError, match="seeding"):
        PaperStateStore(
            path,
            allow_fixture_seed=True,
            data_mode="paper_trading",
            account_scope_fingerprint=ACCOUNT_A,
        )


def test_populated_legacy_database_cannot_be_promoted_to_paper(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA user_version = 5")
        connection.execute(
            "CREATE TABLE legacy_operator_state (state_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO legacy_operator_state VALUES (?)",
            ('{"source":"fixture"}',),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperStateMigrationRequired, match="cannot be promoted"):
        _paper_store(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute(
            "SELECT COUNT(*) FROM legacy_operator_state"
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'state_store_metadata'
            """
        ).fetchone() is None
    finally:
        connection.close()


def test_migration_backfills_open_buy_and_sell_but_not_terminal(tmp_path) -> None:
    path = tmp_path / "migration-mixed.sqlite3"
    with _paper_store(path) as store:
        session = _session(store)
        open_buy = _dispatch(
            store,
            session,
            broker_orderable_cash=2_000_000.0,
            broker_orderable_buy_quantity=28.0,
            minimum_cash_reserve_krw=1_000_000,
        )
        _insert_reserved_dispatch(store, open_buy)
        open_sell = _sell_dispatch(
            store,
            session,
            order_plan_id="oplan-migration-open-sell",
            idempotency_key="paper-order-migration-open-sell",
            purpose="protective_exit",
        )
        _insert_reserved_dispatch(store, open_sell)
        terminal_prepared = _dispatch(
            store,
            session,
            order_plan_id="oplan-migration-terminal",
            idempotency_key="paper-order-migration-terminal",
            request_fingerprint="sha256:" + "e" * 64,
            broker_orderable_cash=2_000_000.0,
            broker_orderable_buy_quantity=28.0,
        )
        _insert_reserved_dispatch(store, terminal_prepared)
        claimed = store.claim_dispatch_attempt(
            terminal_prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        terminal = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "rejected",
                    "reconciliation_status": "reconciled",
                    "last_error_code": "broker_business_rejected",
                    "updated_at": NOW + timedelta(seconds=3),
                    "reconciled_at": NOW + timedelta(seconds=3),
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        store.update_paper_order_dispatch(
            terminal,
            mutation_origin="broker_post_result",
        )
        original_store_id = store.provenance.store_id
        original_session_id = session.session_id
        original_fence = session.fencing_token

    _downgrade_paper_schema_to_v9(path)

    with _paper_store(path) as migrated:
        assert migrated.provenance.schema_version == 11
        assert migrated.provenance.store_id == original_store_id
        restored_session = migrated.load_paper_execution_session(
            original_session_id
        )
        assert restored_session is not None
        assert restored_session.fencing_token == original_fence
        restored_buy = migrated.load_paper_order_dispatch(open_buy.order_plan_id)
        restored_sell = migrated.load_paper_order_dispatch(open_sell.order_plan_id)
        restored_terminal = migrated.load_paper_order_dispatch(
            terminal.order_plan_id
        )
        assert restored_buy is not None
        assert restored_sell is not None
        assert restored_terminal is not None
        assert restored_buy.minimum_cash_reserve_krw is None
        assert restored_sell.minimum_cash_reserve_krw is None
        assert restored_terminal.minimum_cash_reserve_krw is None
        excluded = {"minimum_cash_reserve_krw"}
        assert restored_buy.model_dump(exclude=excluded) == open_buy.model_dump(
            exclude=excluded
        )
        assert restored_sell.model_dump(exclude=excluded) == open_sell.model_dump(
            exclude=excluded
        )
        assert restored_terminal.model_dump(
            exclude=excluded
        ) == terminal.model_dump(exclude=excluded)

        buy_reservation = migrated.load_paper_risk_reservation(
            open_buy.order_plan_id
        )
        sell_reservation = migrated.load_paper_risk_reservation(
            open_sell.order_plan_id
        )
        assert buy_reservation is not None
        assert buy_reservation.status == "held"
        assert buy_reservation.reserved_cash_krw == 700_000
        assert buy_reservation.minimum_cash_reserve_krw == 1_300_000
        assert sell_reservation is not None
        assert sell_reservation.status == "held"
        assert sell_reservation.reserved_sell_quantity == 3
        assert sell_reservation.minimum_cash_reserve_krw == 2_000_000
        assert migrated.load_paper_risk_reservation(terminal.order_plan_id) is None


def test_migration_backfill_failure_rolls_back_schema_metadata(tmp_path) -> None:
    path = tmp_path / "migration-invalid.sqlite3"
    with _paper_store(path) as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        _insert_reserved_dispatch(store, prepared)

    _downgrade_paper_schema_to_v9(path)
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT state_json FROM paper_order_dispatches WHERE order_plan_id = ?",
            (prepared.order_plan_id,),
        ).fetchone()
        state = json.loads(row[0])
        state["quantity"] = 10.5
        connection.execute(
            "UPDATE paper_order_dispatches SET state_json = ? WHERE order_plan_id = ?",
            (
                json.dumps(state, separators=(",", ":"), sort_keys=True),
                prepared.order_plan_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperStateMigrationRequired, match="whole number"):
        _paper_store(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
        assert connection.execute(
            "SELECT schema_version FROM state_store_metadata"
        ).fetchone()[0] == 9
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'paper_risk_reservations'"
        ).fetchone() is None
    finally:
        connection.close()


def test_migration_fractional_sell_audit_failure_uses_migration_error(
    tmp_path,
) -> None:
    path = tmp_path / "migration-fractional-sell.sqlite3"
    with _paper_store(path) as store:
        session = _session(store)
        prepared = _sell_dispatch(
            store,
            session,
            order_plan_id="oplan-migration-fractional-sell",
            idempotency_key="paper-order-migration-fractional-sell",
            purpose="protective_exit",
        )
        _insert_reserved_dispatch(store, prepared)

    _downgrade_paper_schema_to_v9(path)
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT state_json FROM paper_order_dispatches WHERE order_plan_id = ?",
            (prepared.order_plan_id,),
        ).fetchone()
        state = json.loads(row[0])
        state["snapshot_equity"] = 10_000_000.5
        state["snapshot_cash"] = 0.3
        connection.execute(
            "UPDATE paper_order_dispatches SET state_json = ? WHERE order_plan_id = ?",
            (
                json.dumps(state, separators=(",", ":"), sort_keys=True),
                prepared.order_plan_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        PaperStateMigrationRequired,
        match="cannot be promoted to a valid risk reservation",
    ):
        _paper_store(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
        assert connection.execute(
            "SELECT schema_version FROM state_store_metadata"
        ).fetchone()[0] == 9
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'paper_risk_reservations'"
        ).fetchone() is None
    finally:
        connection.close()


def test_future_paper_schema_fails_closed(tmp_path) -> None:
    path = tmp_path / "future.sqlite3"
    with _paper_store(path):
        pass
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA user_version = 12")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperStateMigrationRequired, match="newer schema"):
        _paper_store(path)


def test_run_checkpoint_data_mode_must_match_store(tmp_path) -> None:
    checkpoint = PaperRunCheckpoint(
        run_id="run-mode-001",
        idempotency_key="run-mode-key-001",
        policy_id="policy-paper",
        user_id="local-user",
        policy_version=3,
        run_mode="paper_submit",
        requested_at=NOW,
        request_fingerprint="sha256:" + "d" * 64,
        status="started",
        data_mode="paper_trading",
        started_at=NOW,
        updated_at=NOW,
    )
    with PaperStateStore(tmp_path / "fixture.sqlite3") as fixture:
        with pytest.raises(PaperStateProvenanceError, match="data mode"):
            fixture.insert_run_checkpoint(checkpoint)
    with _paper_store(tmp_path / "paper.sqlite3") as paper:
        assert paper.insert_run_checkpoint(checkpoint) == checkpoint


def test_session_fence_is_exact_and_tokens_advance(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        first = _session(store)
        prepared = _dispatch(store, first)
        _insert_reserved_dispatch(store, prepared)
        with pytest.raises(PaperStateConflictError, match="unexpired"):
            _session(store, started_at=NOW + timedelta(minutes=1))

        second = _session(
            store,
            started_at=NOW + timedelta(minutes=6),
            lease_expires_at=NOW + timedelta(minutes=11),
        )
        assert second.fencing_token == first.fencing_token + 1
        assert store.load_paper_execution_session(first.session_id).status == "abandoned"  # type: ignore[union-attr]
        with pytest.raises(PaperStateConflictError, match="ownership changed"):
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=first,
                claimed_at=NOW + timedelta(minutes=6, seconds=1),
            )


def test_session_renewal_rejects_stale_owner_and_expired_lease(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as first, _paper_store(path) as second:
        session = _session(first)
        stale_copy = second.load_paper_execution_session(session.session_id)
        assert stale_copy == session

        renewed = first.renew_paper_execution_session(
            session,
            renewed_at=NOW + timedelta(minutes=1),
            lease_expires_at=NOW + timedelta(minutes=10),
        )
        assert renewed.fencing_token == session.fencing_token
        assert renewed.revision == session.revision + 1
        with pytest.raises(PaperStateConflictError, match="ownership changed"):
            second.renew_paper_execution_session(
                stale_copy,  # type: ignore[arg-type]
                renewed_at=NOW + timedelta(minutes=2),
                lease_expires_at=NOW + timedelta(minutes=11),
            )
        with pytest.raises(PaperStateConflictError, match="not active"):
            first.renew_paper_execution_session(
                renewed,
                renewed_at=NOW + timedelta(minutes=10),
                lease_expires_at=NOW + timedelta(minutes=15),
            )


def test_successor_can_take_over_only_unattempted_prepared_dispatch(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        predecessor = _session(store)
        prepared = _dispatch(store, predecessor)
        _insert_reserved_dispatch(store, prepared)

        successor = _session(
            store,
            started_at=NOW + timedelta(minutes=6),
            lease_expires_at=NOW + timedelta(minutes=11),
        )
        rebound = store.takeover_prepared_paper_order_dispatch(
            prepared.order_plan_id,
            session=successor,
            taken_over_at=NOW + timedelta(minutes=6, seconds=1),
        )
        assert rebound.session_id == successor.session_id
        assert rebound.fencing_token == successor.fencing_token
        assert rebound.attempt_count == 0
        assert rebound.status == "prepared"
        assert rebound.revision == prepared.revision + 1
        excluded = {"session_id", "fencing_token", "updated_at", "revision"}
        assert rebound.model_dump(exclude=excluded) == prepared.model_dump(
            exclude=excluded
        )
        rebound_reservation = store.load_paper_risk_reservation(
            rebound.order_plan_id
        )
        assert rebound_reservation is not None
        assert rebound_reservation.status == "held"
        assert rebound_reservation.session_id == successor.session_id
        assert rebound_reservation.fencing_token == successor.fencing_token
        assert rebound_reservation.revision == 1

        claimed = store.claim_dispatch_attempt(
            rebound.order_plan_id,
            session=successor,
            claimed_at=NOW + timedelta(minutes=6, seconds=2),
        )
        next_successor = _session(
            store,
            started_at=NOW + timedelta(minutes=12),
            lease_expires_at=NOW + timedelta(minutes=17),
        )
        with pytest.raises(PaperStateConflictError, match="only an unattempted"):
            store.takeover_prepared_paper_order_dispatch(
                claimed.order_plan_id,
                session=next_successor,
                taken_over_at=NOW + timedelta(minutes=12, seconds=1),
            )


def test_prepare_is_exactly_idempotent_and_divergent_key_conflicts(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        assert _insert_reserved_dispatch(store, prepared) == prepared
        assert _insert_reserved_dispatch(store, prepared) == prepared
        reservations = store.list_paper_risk_reservations()
        assert len(reservations) == 1
        assert reservations[0].order_plan_id == prepared.order_plan_id

        divergent = prepared.model_copy(
            update={
                "order_plan_id": "oplan-paper-002",
                "quantity": 11.0,
            }
        )
        with pytest.raises(PaperStateConflictError, match="different evidence"):
            _insert_reserved_dispatch(
                store,
                PaperOrderDispatch.model_validate(divergent.model_dump())
            )

        duplicate_internal_broker_id = prepared.model_copy(
            update={
                "order_plan_id": "oplan-paper-003",
                "idempotency_key": "paper-order-003",
                "request_fingerprint": "sha256:" + "e" * 64,
            }
        )
        with pytest.raises(PaperStateConflictError, match="already exists"):
            _insert_reserved_dispatch(
                store,
                PaperOrderDispatch.model_validate(
                    duplicate_internal_broker_id.model_dump()
                )
            )
        assert store.load_paper_risk_reservation("oplan-paper-003") is None


def test_buy_and_sell_orderability_evidence_fails_closed(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        buy = _dispatch(store, session)
        for updates in (
            {"broker_orderable_cash": 699_999.0},
            {"broker_orderable_buy_quantity": 9.0},
            {"broker_orderable_cash": None},
        ):
            with pytest.raises(ValidationError, match="broker-orderable|orderability"):
                PaperOrderDispatch.model_validate(
                    buy.model_copy(update=updates).model_dump()
                )

        sell = PaperOrderDispatch.model_validate(
            buy.model_copy(
                update={
                    "side": "sell",
                    "purpose": "protective_exit",
                    "quantity": 3.0,
                    "broker_orderable_cash": None,
                    "broker_orderable_buy_quantity": None,
                    "entry_atr14": None,
                }
            ).model_dump()
        )
        assert sell.quantity <= sell.snapshot_symbol_orderable_quantity
        with pytest.raises(ValidationError, match="orderable quantity"):
            PaperOrderDispatch.model_validate(
                sell.model_copy(update={"quantity": 5.0}).model_dump()
            )


def test_reservation_and_dispatch_commit_as_one_pair(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        dispatch = _dispatch(store, session)
        reservation = _reservation(dispatch)

        persisted_dispatch, persisted_reservation = (
            store.reserve_and_insert_paper_order_dispatch(dispatch, reservation)
        )

        assert persisted_dispatch == dispatch
        assert persisted_reservation == reservation
        assert persisted_dispatch.status == "prepared"
        assert persisted_reservation.status == "held"
        assert persisted_dispatch.revision == 0
        assert persisted_reservation.revision == 0


def test_dispatch_only_insert_path_is_rejected(tmp_path) -> None:
    with _paper_store(tmp_path / "dispatch-only.sqlite3") as store:
        session = _session(store)
        dispatch = _dispatch(store, session)

        with pytest.raises(
            PaperStateConflictError,
            match="requires atomic risk reservation",
        ):
            store.insert_paper_order_dispatch(dispatch)

        assert store.list_paper_order_dispatches() == []
        assert store.list_paper_risk_reservations() == []


@pytest.mark.parametrize(
    ("side", "reservation_updates", "expected_message"),
    [
        (
            "buy",
            {"broker_orderable_cash_basis_krw": 1_400_000},
            "buy reservation does not match",
        ),
        (
            "buy",
            {"broker_orderable_buy_quantity_basis": 99},
            "buy reservation does not match",
        ),
        (
            "buy",
            {"gross_exposure_limit_krw": 20_000_000},
            "gross limit does not match",
        ),
        (
            "sell",
            {"snapshot_orderable_quantity_basis": 99},
            "sell reservation does not match",
        ),
    ],
)
def test_reservation_capacity_evidence_must_match_dispatch(
    tmp_path,
    side: str,
    reservation_updates: dict[str, int],
    expected_message: str,
) -> None:
    with _paper_store(tmp_path / f"{side}.sqlite3") as store:
        session = _session(store)
        dispatch = (
            _dispatch(store, session)
            if side == "buy"
            else _sell_dispatch(
                store,
                session,
                order_plan_id="oplan-forged-sell",
                idempotency_key="paper-order-forged-sell",
                purpose="rebalance",
            )
        )
        reservation = PaperRiskReservation.model_validate(
            _reservation(dispatch).model_copy(
                update=reservation_updates
            ).model_dump()
        )

        with pytest.raises(PaperStateConflictError, match=expected_message):
            store.reserve_and_insert_paper_order_dispatch(
                dispatch,
                reservation,
            )

        assert store.load_paper_order_dispatch(dispatch.order_plan_id) is None
        assert store.load_paper_risk_reservation(dispatch.order_plan_id) is None


def test_cash_reserve_and_gross_limit_cannot_be_forged_together(tmp_path) -> None:
    with _paper_store(tmp_path / "forged-cash-reserve.sqlite3") as store:
        session = _session(store)
        dispatch = _dispatch(
            store,
            session,
            minimum_cash_reserve_krw=1_000_000,
        )
        valid = _reservation(
            dispatch,
            minimum_cash_reserve_krw=1_000_000,
            gross_exposure_limit_krw=9_000_000,
        )
        forged = PaperRiskReservation.model_validate(
            valid.model_copy(
                update={
                    "minimum_cash_reserve_krw": 0,
                    "gross_exposure_limit_krw": 10_000_000,
                }
            ).model_dump()
        )

        with pytest.raises(PaperStateConflictError, match="cash reserve evidence"):
            store.reserve_and_insert_paper_order_dispatch(dispatch, forged)

        assert store.load_paper_order_dispatch(dispatch.order_plan_id) is None
        assert store.load_paper_risk_reservation(dispatch.order_plan_id) is None


def test_dispatch_insert_failure_rolls_back_reservation(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        dispatch = _dispatch(
            store,
            session,
            order_plan_id="oplan-forced-rollback",
            idempotency_key="paper-order-forced-rollback",
        )
        reservation = _reservation(dispatch)
        store._connection.execute(  # noqa: SLF001 - fault-injection boundary
            """
            CREATE TRIGGER force_dispatch_insert_failure
            BEFORE INSERT ON paper_order_dispatches
            WHEN NEW.order_plan_id = 'oplan-forced-rollback'
            BEGIN
                SELECT RAISE(ABORT, 'forced dispatch insert failure');
            END
            """
        )
        store._connection.commit()  # noqa: SLF001 - fault-injection boundary

        with pytest.raises(PaperStateConflictError, match="already exists"):
            store.reserve_and_insert_paper_order_dispatch(dispatch, reservation)

        assert store.load_paper_order_dispatch(dispatch.order_plan_id) is None
        assert store.load_paper_risk_reservation(dispatch.order_plan_id) is None


def test_terminal_dispatch_and_reservation_release_roll_back_together(
    tmp_path,
) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        _insert_reserved_dispatch(store, prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        fill = PaperDispatchFillEvidence(
            broker_fill_reference="kis-fill-atomic-rollback",
            broker_order_id=claimed.broker_order_id,
            broker_order_reference="kis-order-atomic-rollback",
            symbol=claimed.symbol,
            side=claimed.side,
            quantity=claimed.quantity,
            price=claimed.limit_price,
            notional=claimed.quantity * claimed.limit_price,
            evidence_at=NOW + timedelta(seconds=3),
            time_basis="broker_execution",
        )
        filled = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "filled",
                    "broker_business_date": date(2026, 7, 10),
                    "broker_order_reference": "kis-order-atomic-rollback",
                    "broker_order_branch_number": "91234",
                    "broker_order_time": "090001",
                    "cumulative_filled_quantity": claimed.quantity,
                    "fill_evidence": [fill],
                    "reconciliation_status": "reconciled",
                    "updated_at": NOW + timedelta(seconds=3),
                    "reconciled_at": NOW + timedelta(seconds=3),
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        store._connection.execute(  # noqa: SLF001 - fault-injection boundary
            """
            CREATE TRIGGER force_reservation_release_failure
            BEFORE UPDATE ON paper_risk_reservations
            WHEN NEW.status = 'released_filled'
            BEGIN
                SELECT RAISE(ABORT, 'forced reservation release failure');
            END
            """
        )
        store._connection.commit()  # noqa: SLF001 - fault-injection boundary

        with pytest.raises(sqlite3.IntegrityError, match="forced reservation"):
            store.update_paper_order_dispatch(
                filled,
                mutation_origin="broker_reconciliation",
            )

        assert store.load_paper_order_dispatch(claimed.order_plan_id) == claimed
        held = store.load_paper_risk_reservation(claimed.order_plan_id)
        assert held is not None
        assert held.status == "held"
        assert held.revision == 0


def test_competing_terminal_updates_release_once_and_reopen_capacity(
    tmp_path,
) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        _insert_reserved_dispatch(store, prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        session_id = session.session_id

    fill = PaperDispatchFillEvidence(
        broker_fill_reference="kis-fill-terminal-race",
        broker_order_id=claimed.broker_order_id,
        broker_order_reference="kis-order-terminal-race",
        symbol=claimed.symbol,
        side=claimed.side,
        quantity=claimed.quantity,
        price=claimed.limit_price,
        notional=claimed.quantity * claimed.limit_price,
        evidence_at=NOW + timedelta(seconds=3),
        time_basis="broker_execution",
    )
    filled = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "filled",
                "broker_business_date": date(2026, 7, 10),
                "broker_order_reference": "kis-order-terminal-race",
                "broker_order_branch_number": "91234",
                "broker_order_time": "090001",
                "cumulative_filled_quantity": claimed.quantity,
                "fill_evidence": [fill],
                "reconciliation_status": "reconciled",
                "updated_at": NOW + timedelta(seconds=3),
                "reconciled_at": NOW + timedelta(seconds=3),
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    rejected = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "rejected",
                "reconciliation_status": "reconciled",
                "last_error_code": "broker_business_rejected",
                "updated_at": NOW + timedelta(seconds=3),
                "reconciled_at": NOW + timedelta(seconds=3),
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    start = Barrier(2)

    def terminalize(candidate: PaperOrderDispatch) -> str:
        with _paper_store(path) as store:
            start.wait()
            try:
                store.update_paper_order_dispatch(
                    candidate,
                    mutation_origin=(
                        "broker_reconciliation"
                        if candidate.status == "filled"
                        else "broker_post_result"
                    ),
                )
            except PaperStateConflictError:
                return "conflict"
            return candidate.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(terminalize, (filled, rejected)))

    assert results.count("conflict") == 1
    winner_status = next(result for result in results if result != "conflict")
    with _paper_store(path) as store:
        terminal = store.load_paper_order_dispatch(claimed.order_plan_id)
        reservation = store.load_paper_risk_reservation(claimed.order_plan_id)
        assert terminal is not None
        assert terminal.status == winner_status
        assert reservation is not None
        assert reservation.status == (
            "released_filled"
            if winner_status == "filled"
            else "released_rejected"
        )
        assert reservation.revision == 1

        assert store.update_paper_order_dispatch(
            terminal,
            mutation_origin=(
                "broker_reconciliation"
                if terminal.status == "filled"
                else "broker_post_result"
            ),
        ) == terminal
        replayed = store.load_paper_risk_reservation(claimed.order_plan_id)
        assert replayed is not None
        assert replayed.revision == 1

        owned = store.load_paper_execution_session(session_id)
        assert owned is not None
        successor = _dispatch(
            store,
            owned,
            order_plan_id="oplan-after-terminal-release",
            idempotency_key="paper-order-after-terminal-release",
            request_fingerprint="sha256:" + "f" * 64,
            prepared_at=NOW + timedelta(seconds=4),
            updated_at=NOW + timedelta(seconds=4),
        )
        _insert_reserved_dispatch(store, successor)
        assert store.load_paper_risk_reservation(successor.order_plan_id) is not None


def test_concurrent_buy_reservations_cannot_exceed_cash(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        session = _session(store)
        session_id = session.session_id

    start = Barrier(2)

    def reserve(index: int) -> str:
        with _paper_store(path) as store:
            owned = store.load_paper_execution_session(session_id)
            assert owned is not None
            dispatch = _dispatch(
                store,
                owned,
                order_plan_id=f"oplan-concurrent-buy-{index}",
                idempotency_key=f"paper-order-concurrent-buy-{index}",
                request_fingerprint=f"sha256:{index + 1:064x}",
            )
            start.wait()
            try:
                store.reserve_and_insert_paper_order_dispatch(
                    dispatch,
                    _reservation(dispatch),
                )
            except PaperRiskReservationRejected:
                return "rejected"
            return "admitted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(reserve, (1, 2)))

    assert results == ["admitted", "rejected"]
    with _paper_store(path) as store:
        held = store.list_paper_risk_reservations(held_only=True)
        assert len(held) == 1
        assert sum(item.reserved_cash_krw or 0 for item in held) == 700_000


def test_aggregate_gross_reservation_fails_before_partial_write(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        first = _dispatch(
            store,
            session,
            broker_orderable_cash=2_000_000.0,
            broker_orderable_buy_quantity=28.0,
            minimum_cash_reserve_krw=1_000_000,
        )
        first_reservation = _reservation(
            first,
            gross_exposure_limit_krw=9_000_000,
        )
        store.reserve_and_insert_paper_order_dispatch(first, first_reservation)

        second = _dispatch(
            store,
            session,
            order_plan_id="oplan-gross-second",
            idempotency_key="paper-order-gross-second",
            request_fingerprint="sha256:" + "e" * 64,
            broker_orderable_cash=2_000_000.0,
            broker_orderable_buy_quantity=28.0,
            minimum_cash_reserve_krw=1_000_000,
        )
        second_reservation = _reservation(
            second,
            gross_exposure_limit_krw=9_000_000,
        )

        with pytest.raises(
            PaperRiskReservationRejected,
            match="gross exposure availability",
        ):
            store.reserve_and_insert_paper_order_dispatch(
                second,
                second_reservation,
            )

        assert store.load_paper_order_dispatch(second.order_plan_id) is None
        assert store.load_paper_risk_reservation(second.order_plan_id) is None
        held = store.list_paper_risk_reservations(held_only=True)
        assert sum(item.reserved_gross_exposure_krw for item in held) == 700_000


def test_aggregate_sell_reservation_fails_before_partial_write(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        first = _sell_dispatch(
            store,
            session,
            order_plan_id="oplan-sell-first",
            idempotency_key="paper-order-sell-first",
            purpose="rebalance",
        )
        _insert_reserved_dispatch(store, first)
        second = _sell_dispatch(
            store,
            session,
            order_plan_id="oplan-sell-second",
            idempotency_key="paper-order-sell-second",
            purpose="protective_exit",
        )

        with pytest.raises(
            PaperRiskReservationRejected,
            match="durable quantity availability",
        ):
            _insert_reserved_dispatch(store, second)

        assert store.load_paper_order_dispatch(second.order_plan_id) is None
        assert store.load_paper_risk_reservation(second.order_plan_id) is None
        held = store.list_paper_risk_reservations(held_only=True)
        assert sum(item.reserved_sell_quantity or 0 for item in held) == 3


def test_guardrail_reads_only_held_sell_reservations_from_paper_store(
    tmp_path,
) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        sell = _sell_dispatch(
            store,
            session,
            order_plan_id="oplan-guardrail-sell",
            idempotency_key="paper-order-guardrail-sell",
            purpose="protective_exit",
        )
        _insert_reserved_dispatch(store, sell)
        harness = HarnessService()
        harness.paper_dispatch_provider = store
        policy = UserPolicy(policy_id="policy-paper")

        held_state = harness._guardrail_state(
            policy=policy,
            strategy_id=sell.strategy_id,
            now=NOW + timedelta(seconds=2),
        )
        assert held_state.reserved_sell_quantities == {"005930": 3}
        assert held_state.unfilled_order_keys == [
            f"{sell.strategy_id}:005930:sell"
        ]

        expired = PaperOrderDispatch.model_validate(
            sell.model_copy(
                update={
                    "status": "expired_pre_dispatch",
                    "reconciliation_status": "reconciled",
                    "last_error_code": "submission_evidence_expired",
                    "updated_at": NOW + timedelta(seconds=3),
                    "reconciled_at": NOW + timedelta(seconds=3),
                    "revision": sell.revision + 1,
                }
            ).model_dump()
        )
        store.update_paper_order_dispatch(
            expired,
            mutation_origin="local_submission_guard",
        )
        released_state = harness._guardrail_state(
            policy=policy,
            strategy_id=sell.strategy_id,
            now=NOW + timedelta(seconds=4),
        )
        assert released_state.reserved_sell_quantities == {}
        assert released_state.unfilled_order_keys == []

def test_two_connections_allow_only_one_dispatch_claim(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as first, _paper_store(path) as second:
        session = _session(first)
        second_session = second.load_paper_execution_session(session.session_id)
        assert second_session == session
        prepared = _dispatch(first, session)
        _insert_reserved_dispatch(first, prepared)

        claimed = first.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        assert claimed.status == "dispatch_claimed"
        assert claimed.attempt_count == 1
        with pytest.raises(PaperStateConflictError, match="only external attempt"):
            second.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=second_session,  # type: ignore[arg-type]
                claimed_at=NOW + timedelta(seconds=3),
            )


@pytest.mark.parametrize("purpose", ["protective_exit", "strategy_retirement"])
def test_restart_allows_verified_risk_reducing_sell_past_unknown_buy(
    tmp_path,
    purpose: str,
) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        predecessor = _session(store)
        buy = _dispatch(store, predecessor)
        _insert_reserved_dispatch(store, buy)
        store.claim_dispatch_attempt(
            buy.order_plan_id,
            session=predecessor,
            claimed_at=NOW + timedelta(seconds=2),
        )

    with _paper_store(path) as reopened:
        successor = _session(
            reopened,
            started_at=NOW + timedelta(minutes=6),
            lease_expires_at=NOW + timedelta(minutes=11),
        )
        recovered = reopened.recover_interrupted_dispatches(
            session=successor,
            recovered_at=NOW + timedelta(minutes=6, seconds=1),
        )
        assert [item.status for item in recovered] == ["outcome_unknown"]

        prepared_at = NOW + timedelta(minutes=6, seconds=2)
        protective_sell = _sell_dispatch(
            reopened,
            successor,
            order_plan_id=f"oplan-{purpose}",
            idempotency_key=f"paper-order-{purpose}",
            purpose=purpose,
            prepared_at=prepared_at,
        )
        _insert_reserved_dispatch(reopened, protective_sell)
        claimed = reopened.claim_dispatch_attempt(
            protective_sell.order_plan_id,
            session=successor,
            claimed_at=prepared_at + timedelta(seconds=1),
        )

        assert claimed.status == "dispatch_claimed"
        assert claimed.attempt_count == 1
        assert claimed.quantity <= claimed.snapshot_symbol_orderable_quantity
        unknown_buy = reopened.load_paper_order_dispatch(buy.order_plan_id)
        assert unknown_buy is not None
        assert unknown_buy.status == "outcome_unknown"
        assert unknown_buy.attempt_count == 1


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_unknown_buy_still_blocks_ordinary_rebalance_claims(tmp_path, side: str) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        buy = _dispatch(store, session)
        _insert_reserved_dispatch(store, buy)
        claimed = store.claim_dispatch_attempt(
            buy.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        unknown = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "outcome_unknown",
                    "last_error_code": "broker_response_ambiguous",
                    "updated_at": NOW + timedelta(seconds=3),
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        store.update_paper_order_dispatch(
            unknown,
            mutation_origin="broker_post_result",
        )
        held_unknown = store.load_paper_risk_reservation(buy.order_plan_id)
        assert held_unknown is not None
        assert held_unknown.status == "held"
        if side == "buy":
            ordinary = _dispatch(
                store,
                session,
                order_plan_id="oplan-ordinary-buy",
                idempotency_key="paper-order-ordinary-buy",
                request_fingerprint="sha256:" + "e" * 64,
            )
        else:
            ordinary = _sell_dispatch(
                store,
                session,
                order_plan_id="oplan-ordinary-sell",
                idempotency_key="paper-order-ordinary-sell",
                purpose="rebalance",
            )
        if side == "buy":
            with pytest.raises(
                PaperRiskReservationRejected,
                match="durable cash availability",
            ):
                _insert_reserved_dispatch(store, ordinary)
            assert store.load_paper_order_dispatch(ordinary.order_plan_id) is None
            return

        _insert_reserved_dispatch(store, ordinary)

        with pytest.raises(PaperStateConflictError, match="unresolved"):
            store.claim_dispatch_attempt(
                ordinary.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=3),
            )
        assert store.load_paper_order_dispatch(ordinary.order_plan_id) == ordinary


@pytest.mark.parametrize(
    ("side", "purpose"),
    [
        ("buy", "rebalance"),
        ("sell", "rebalance"),
        ("sell", "protective_exit"),
        ("sell", "strategy_retirement"),
    ],
)
def test_unresolved_sell_blocks_every_new_dispatch_claim(
    tmp_path,
    side: str,
    purpose: str,
) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        unresolved_sell = _sell_dispatch(
            store,
            session,
            order_plan_id="oplan-unresolved-sell",
            idempotency_key="paper-order-unresolved-sell",
            purpose="protective_exit",
        )
        _insert_reserved_dispatch(store, unresolved_sell)
        store.claim_dispatch_attempt(
            unresolved_sell.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )

        if side == "buy":
            candidate = _dispatch(
                store,
                session,
                order_plan_id="oplan-candidate-buy",
                idempotency_key="paper-order-candidate-buy",
                request_fingerprint="sha256:" + "f" * 64,
            )
        else:
            candidate = _sell_dispatch(
                store,
                session,
                order_plan_id=f"oplan-candidate-{purpose}",
                idempotency_key=f"paper-order-candidate-{purpose}",
                purpose=purpose,
            )
        if side == "sell":
            with pytest.raises(
                PaperRiskReservationRejected,
                match="durable quantity availability",
            ):
                _insert_reserved_dispatch(store, candidate)
            assert store.load_paper_order_dispatch(candidate.order_plan_id) is None
            return

        _insert_reserved_dispatch(store, candidate)

        with pytest.raises(PaperStateConflictError, match="unresolved"):
            store.claim_dispatch_attempt(
                candidate.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=3),
            )
        assert store.load_paper_order_dispatch(candidate.order_plan_id) == candidate


def test_dispatch_claim_rejects_at_submission_evidence_expiry(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        prepared = _dispatch(
            store,
            session,
            submission_evidence_expires_at=NOW + timedelta(minutes=2),
        )
        _insert_reserved_dispatch(store, prepared)

        with pytest.raises(PaperStateConflictError, match="submission evidence expired"):
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=prepared.submission_evidence_expires_at,
            )
        unchanged = store.load_paper_order_dispatch(prepared.order_plan_id)
        assert unchanged == prepared


def test_restart_converts_claimed_to_unknown_without_redispatch_authority(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        _insert_reserved_dispatch(store, prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        assert claimed.attempt_count == 1

    with _paper_store(path) as reopened:
        live_owner_recovery = reopened.recover_interrupted_dispatches(
            session=session,
            recovered_at=NOW + timedelta(seconds=3),
        )
        assert live_owner_recovery == []
        recovery_session = _session(
            reopened,
            started_at=NOW + timedelta(minutes=6),
            lease_expires_at=NOW + timedelta(minutes=11),
        )
        recovered = reopened.recover_interrupted_dispatches(
            session=recovery_session,
            recovered_at=NOW + timedelta(minutes=6, seconds=1),
        )
        assert [item.status for item in recovered] == ["outcome_unknown"]
        unknown = reopened.load_paper_order_dispatch(prepared.order_plan_id)
        assert unknown is not None
        assert unknown.attempt_count == 1
        assert unknown.last_error_code == "process_interrupted"
        with pytest.raises(PaperStateConflictError, match="different session fence"):
            reopened.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=recovery_session,
                claimed_at=NOW + timedelta(minutes=6, seconds=2),
            )


def test_legacy_broker_org_decodes_only_as_forwarding_and_conflicts_fail() -> None:
    with _paper_store(":memory:") as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        _insert_reserved_dispatch(store, prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )

    legacy_payload = claimed.model_dump()
    legacy_payload.pop("broker_forwarding_order_org_number")
    legacy_payload.pop("broker_order_branch_number")
    legacy_payload.update(
        {
            "status": "accepted",
            "broker_business_date": date(2026, 7, 10),
            "broker_order_reference": "kis-order-legacy",
            "broker_order_org_number": "70001",
            "broker_order_time": "090001",
            "updated_at": NOW + timedelta(seconds=3),
            "revision": claimed.revision + 1,
        }
    )

    migrated = PaperOrderDispatch.model_validate(legacy_payload)
    assert migrated.broker_forwarding_order_org_number == "70001"
    assert migrated.broker_order_branch_number is None
    assert "broker_order_org_number" not in migrated.model_dump()

    with pytest.raises(ValidationError, match="conflicts with forwarding"):
        PaperOrderDispatch.model_validate(
            {
                **legacy_payload,
                "broker_forwarding_order_org_number": "70002",
            }
        )


def test_broker_and_risk_evidence_is_monotonic_and_immutable(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        _insert_reserved_dispatch(store, prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        accepted = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "accepted",
                    "broker_business_date": date(2026, 7, 10),
                    "broker_order_reference": "kis-order-001",
                    "broker_forwarding_order_org_number": "00001",
                    "broker_order_time": "090001",
                    "updated_at": NOW + timedelta(seconds=3),
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        assert store.update_paper_order_dispatch(
            accepted,
            mutation_origin="broker_post_result",
        ) == accepted

        fill = PaperDispatchFillEvidence(
            broker_fill_reference="kis-fill-001",
            broker_order_id=accepted.broker_order_id,
            broker_order_reference="kis-order-001",
            symbol="005930",
            side="buy",
            quantity=4.0,
            price=70_000.0,
            notional=280_000.0,
            evidence_at=NOW + timedelta(seconds=4),
            time_basis="broker_execution",
        )
        partial = PaperOrderDispatch.model_validate(
            accepted.model_copy(
                update={
                    "status": "partially_filled",
                    "broker_order_branch_number": "91234",
                    "cumulative_filled_quantity": 4.0,
                    "fill_evidence": [fill],
                    "updated_at": NOW + timedelta(seconds=4),
                    "revision": accepted.revision + 1,
                }
            ).model_dump()
        )
        assert store.update_paper_order_dispatch(
            partial,
            mutation_origin="broker_reconciliation",
        ) == partial
        held_reservation = store.load_paper_risk_reservation(
            partial.order_plan_id
        )
        assert held_reservation is not None
        assert held_reservation.status == "held"
        assert held_reservation.reserved_cash_krw == 700_000

        changed_quote = partial.model_copy(
            update={
                "quote_bid": 69_700.0,
                "quote_last": 69_850.0,
                "updated_at": NOW + timedelta(seconds=5),
                "revision": partial.revision + 1,
            }
        )
        with pytest.raises(PaperStateConflictError, match="immutable"):
            store.update_paper_order_dispatch(
                PaperOrderDispatch.model_validate(changed_quote.model_dump()),
                mutation_origin="broker_reconciliation",
            )

        for field, value in (
            ("snapshot_daily_loss_ratio", -0.015),
            ("snapshot_monthly_loss_ratio", -0.025),
            ("snapshot_symbol_orderable_quantity", 3.0),
            ("broker_orderable_cash", 900_000.0),
            ("broker_orderable_buy_quantity", 13.0),
            ("submission_evidence_expires_at", NOW + timedelta(minutes=8)),
        ):
            changed_risk_evidence = partial.model_copy(
                update={
                    field: value,
                    "updated_at": NOW + timedelta(seconds=5),
                    "revision": partial.revision + 1,
                }
            )
            with pytest.raises(PaperStateConflictError, match="immutable"):
                store.update_paper_order_dispatch(
                    PaperOrderDispatch.model_validate(
                        changed_risk_evidence.model_dump()
                    ),
                    mutation_origin="broker_reconciliation",
                )

        changed_forwarding_identity = partial.model_copy(
            update={
                "broker_forwarding_order_org_number": "00002",
                "updated_at": NOW + timedelta(seconds=5),
                "revision": partial.revision + 1,
            }
        )
        with pytest.raises(PaperStateConflictError, match="forwarding organization"):
            store.update_paper_order_dispatch(
                PaperOrderDispatch.model_validate(
                    changed_forwarding_identity.model_dump()
                ),
                mutation_origin="broker_reconciliation",
            )

        changed_branch_identity = partial.model_copy(
            update={
                "broker_order_branch_number": "91235",
                "updated_at": NOW + timedelta(seconds=5),
                "revision": partial.revision + 1,
            }
        )
        with pytest.raises(PaperStateConflictError, match="order branch"):
            store.update_paper_order_dispatch(
                PaperOrderDispatch.model_validate(
                    changed_branch_identity.model_dump()
                ),
                mutation_origin="broker_reconciliation",
            )

        removed_fill = partial.model_copy(
            update={
                "status": "accepted",
                "cumulative_filled_quantity": 0.0,
                "fill_evidence": [],
                "updated_at": NOW + timedelta(seconds=5),
                "revision": partial.revision + 1,
            }
        )
        with pytest.raises(PaperStateConflictError):
            store.update_paper_order_dispatch(
                PaperOrderDispatch.model_validate(removed_fill.model_dump()),
                mutation_origin="broker_reconciliation",
            )


def test_dispatch_model_and_schema_store_no_raw_secrets_or_account_id(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        session = _session(store)
        dispatch = _dispatch(store, session)
        _insert_reserved_dispatch(store, dispatch)
        with pytest.raises(ValidationError):
            PaperOrderDispatch.model_validate(
                {**dispatch.model_dump(), "api_key": "must-not-persist"}
            )

    connection = sqlite3.connect(path)
    try:
        metadata_payload = json.loads(
            connection.execute(
                "SELECT state_json FROM state_store_metadata"
            ).fetchone()[0]
        )
        dispatch_payload = json.loads(
            connection.execute(
                "SELECT state_json FROM paper_order_dispatches"
            ).fetchone()[0]
        )
        reservation_payload = json.loads(
            connection.execute(
                "SELECT state_json FROM paper_risk_reservations"
            ).fetchone()[0]
        )
        columns = {
            row[1]
            for table in (
                "state_store_metadata",
                "paper_execution_sessions",
                "paper_order_dispatches",
                "paper_risk_reservations",
            )
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
    finally:
        connection.close()

    forbidden = {
        "account_id",
        "account_number",
        "api_key",
        "api_secret",
        "access_token",
        "authorization",
        "credential",
    }
    assert not forbidden.intersection(metadata_payload)
    assert not forbidden.intersection(dispatch_payload)
    assert not forbidden.intersection(reservation_payload)
    assert not forbidden.intersection(columns)
    assert dispatch_payload["account_scope_fingerprint"] == ACCOUNT_A
    assert reservation_payload["account_scope_fingerprint"] == ACCOUNT_A
    assert datetime.fromisoformat(
        dispatch_payload["submission_evidence_expires_at"].replace("Z", "+00:00")
    ) == NOW + timedelta(minutes=9)
