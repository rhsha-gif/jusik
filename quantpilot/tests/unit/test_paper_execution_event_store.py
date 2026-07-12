from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import sqlite3
from threading import Barrier

import pytest

from quantpilot.packages.core.execution.events import (
    PaperExecutionEvent,
    canonical_json_bytes,
)
from quantpilot.packages.core.operator.position_ledger import PaperOrderDispatch
from quantpilot.packages.db.sqlite_repositories import (
    PaperStateConflictError,
    PaperStateCorruptionError,
    PaperStateMigrationRequired,
    _PaperAggregateKey,
    _PaperEventMutationGuard,
)
from quantpilot.tests.paper_execution_event_store_fixtures import (
    NOW,
    create_v10_legacy_corpus,
    downgrade_to_schema,
    insert_reserved_dispatch,
    make_dispatch,
    make_reservation,
    paper_store,
    start_session,
)


def _create_v10_prepared(path) -> PaperOrderDispatch:
    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix=path.stem)
        insert_reserved_dispatch(store, prepared)
    downgrade_to_schema(path, target_version=10)
    return prepared


def _claimed(dispatch: PaperOrderDispatch) -> PaperOrderDispatch:
    claimed_at = dispatch.updated_at + timedelta(seconds=1)
    return PaperOrderDispatch.model_validate(
        dispatch.model_copy(
            update={
                "status": "dispatch_claimed",
                "attempt_count": 1,
                "dispatch_claimed_at": claimed_at,
                "updated_at": claimed_at,
                "revision": dispatch.revision + 1,
            }
        ).model_dump()
    )


def _dispatch_key(dispatch: PaperOrderDispatch) -> _PaperAggregateKey:
    return _PaperAggregateKey("order_dispatch", dispatch.order_plan_id)


def _write_dispatch_row(store, dispatch: PaperOrderDispatch) -> int:
    cursor = store._connection.execute(  # noqa: SLF001 - intentional fault harness
        """
        UPDATE paper_order_dispatches
        SET status = ?, revision = ?, state_json = ?, updated_at = ?
        WHERE order_plan_id = ?
        """,
        (
            dispatch.status,
            dispatch.revision,
            store._serialize(dispatch),  # noqa: SLF001
            dispatch.updated_at.isoformat(),
            dispatch.order_plan_id,
        ),
    )
    return cursor.rowcount


def _order_import_event(store, order_plan_id: str):
    return next(
        event
        for event in store.list_paper_execution_events(
            aggregate_type="order_dispatch",
            aggregate_id=order_plan_id,
        )
    )


def _claim_candidate(store, before: PaperOrderDispatch, after: PaperOrderDispatch):
    predecessor = _order_import_event(store, before.order_plan_id)
    event = store._new_runtime_paper_execution_event(  # noqa: SLF001
        aggregate_version=predecessor.aggregate_version + 1,
        event_type="DispatchClaimed",
        source="local_dispatch_claim",
        after=after,
        causation_id=predecessor.event_id,
        before=before,
    )
    return predecessor, event


def test_runtime_append_advances_contiguous_version_and_derives_causation_and_time(
    tmp_path,
) -> None:
    path = tmp_path / "runtime-append.sqlite3"
    prepared = _create_v10_prepared(path)

    with paper_store(path) as store:
        claimed = _claimed(prepared)
        predecessor, event = _claim_candidate(store, prepared, claimed)
        key = _dispatch_key(claimed)
        with store._event_transaction() as guard:  # noqa: SLF001
            guard.capture_before(key)
            rowcount = _write_dispatch_row(store, claimed)
            guard.register_change(
                after=claimed,
                state_json=store._serialize(claimed),  # noqa: SLF001
                rowcount=rowcount,
            )
            guard.register_candidate(event)
            result = store._append_paper_execution_events(  # noqa: SLF001
                [event],
                expected_previous_versions={key: predecessor.aggregate_version},
                guard=guard,
            )

        assert result == (True,)
        persisted = store.load_paper_order_dispatch(claimed.order_plan_id)
        stream = store.list_paper_execution_events(
            aggregate_type="order_dispatch",
            aggregate_id=claimed.order_plan_id,
        )

    assert persisted == claimed
    assert [item.aggregate_version for item in stream] == [1, 2]
    assert stream[-1].causation_id == stream[0].event_id
    assert stream[-1].received_at == claimed.updated_at
    assert stream[-1].occurred_at == claimed.updated_at


@pytest.mark.parametrize("expected_previous", [0, 2])
def test_stale_or_future_expected_version_rolls_back_row_and_event(
    tmp_path,
    expected_previous: int,
) -> None:
    path = tmp_path / f"version-{expected_previous}.sqlite3"
    prepared = _create_v10_prepared(path)

    with paper_store(path) as store:
        claimed = _claimed(prepared)
        _predecessor, event = _claim_candidate(store, prepared, claimed)
        key = _dispatch_key(claimed)
        with pytest.raises(PaperStateConflictError):
            with store._event_transaction() as guard:  # noqa: SLF001
                guard.capture_before(key)
                rowcount = _write_dispatch_row(store, claimed)
                guard.register_change(
                    after=claimed,
                    state_json=store._serialize(claimed),  # noqa: SLF001
                    rowcount=rowcount,
                )
                guard.register_candidate(event)
                store._append_paper_execution_events(  # noqa: SLF001
                    [event],
                    expected_previous_versions={key: expected_previous},
                    guard=guard,
                )

        assert store.load_paper_order_dispatch(prepared.order_plan_id) == prepared
        assert len(store.list_paper_execution_events()) == 2


def test_exact_event_id_retry_is_noop_only_while_authoritative_row_matches(
    tmp_path,
) -> None:
    path = tmp_path / "exact-retry.sqlite3"
    prepared = _create_v10_prepared(path)

    with paper_store(path) as store:
        event = _order_import_event(store, prepared.order_plan_id)
        key = _dispatch_key(prepared)
        before_count = len(store.list_paper_execution_events())
        with store._transaction():  # noqa: SLF001 - duplicate fault harness
            result = store._append_paper_execution_events(  # noqa: SLF001
                [event],
                expected_previous_versions={key: event.aggregate_version},
                guard=None,
                import_mode=True,
            )
        assert result == (False,)
        assert len(store.list_paper_execution_events()) == before_count

        claimed = _claimed(prepared)
        with pytest.raises(PaperStateCorruptionError):
            with store._transaction():  # noqa: SLF001
                _write_dispatch_row(store, claimed)
                store._append_paper_execution_events(  # noqa: SLF001
                    [event],
                    expected_previous_versions={key: event.aggregate_version},
                    guard=None,
                    import_mode=True,
                )

        assert store.load_paper_order_dispatch(prepared.order_plan_id) == prepared
        assert len(store.list_paper_execution_events()) == before_count


def test_divergent_event_id_reuse_is_corruption_and_writes_nothing(tmp_path) -> None:
    path = tmp_path / "divergent-event-id.sqlite3"
    prepared = _create_v10_prepared(path)

    with paper_store(path) as store:
        event = _order_import_event(store, prepared.order_plan_id)
        divergent = PaperExecutionEvent.model_validate(
            event.model_copy(
                update={"received_at": event.received_at + timedelta(microseconds=1)}
            ).model_dump()
        )
        key = _dispatch_key(prepared)
        before_count = len(store.list_paper_execution_events())
        with pytest.raises(PaperStateCorruptionError):
            with store._transaction():  # noqa: SLF001
                store._append_paper_execution_events(  # noqa: SLF001
                    [divergent],
                    expected_previous_versions={key: event.aggregate_version},
                    guard=None,
                    import_mode=True,
                )
        assert len(store.list_paper_execution_events()) == before_count


def test_event_insert_failure_rolls_back_authoritative_row_and_event(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "insert-failure.sqlite3"
    prepared = _create_v10_prepared(path)

    with paper_store(path) as store:
        claimed = _claimed(prepared)
        predecessor, event = _claim_candidate(store, prepared, claimed)
        key = _dispatch_key(claimed)
        original_insert = store._insert_paper_execution_event  # noqa: SLF001

        def insert_then_fail(candidate):
            original_insert(candidate)
            raise sqlite3.IntegrityError("injected append failure")

        monkeypatch.setattr(store, "_insert_paper_execution_event", insert_then_fail)
        with pytest.raises(PaperStateConflictError):
            with store._event_transaction() as guard:  # noqa: SLF001
                guard.capture_before(key)
                rowcount = _write_dispatch_row(store, claimed)
                guard.register_change(
                    after=claimed,
                    state_json=store._serialize(claimed),  # noqa: SLF001
                    rowcount=rowcount,
                )
                guard.register_candidate(event)
                store._append_paper_execution_events(  # noqa: SLF001
                    [event],
                    expected_previous_versions={
                        key: predecessor.aggregate_version
                    },
                    guard=guard,
                )

        assert store.load_paper_order_dispatch(prepared.order_plan_id) == prepared
        assert len(store.list_paper_execution_events()) == 2


def test_mutation_guard_rejects_committed_row_self_declaration_attack(
    tmp_path,
) -> None:
    path = tmp_path / "guard-self-declaration.sqlite3"
    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="guard-attack")
        _persisted_dispatch, reservation = insert_reserved_dispatch(store, prepared)
        before_events = store.list_paper_execution_events()
        assert {event.event_type for event in before_events} == {
            "RiskReserved",
            "OrderPrepared",
        }

        dispatch_key = _dispatch_key(prepared)
        reservation_key = _PaperAggregateKey(
            "risk_reservation",
            reservation.reservation_id,
        )
        with pytest.raises(PaperStateConflictError):
            with store._event_transaction() as guard:  # noqa: SLF001
                guard.capture_before(dispatch_key)
                guard.capture_before(reservation_key)
                cursor = store._connection.execute(  # noqa: SLF001
                    "UPDATE paper_order_dispatches SET state_json = state_json "
                    "WHERE order_plan_id = ?",
                    (prepared.order_plan_id,),
                )
                guard.register_change(
                    after=prepared,
                    state_json=store._serialize(prepared),  # noqa: SLF001
                    rowcount=cursor.rowcount,
                )

        assert store.load_paper_order_dispatch(prepared.order_plan_id) == prepared
        assert store.load_paper_risk_reservation(prepared.order_plan_id) == reservation
        assert store.list_paper_execution_events() == before_events


def test_mutation_guard_rejects_missing_extra_and_noncanonical_changes(
    tmp_path,
) -> None:
    path = tmp_path / "guard-completeness.sqlite3"
    prepared = _create_v10_prepared(path)
    with paper_store(path) as store:
        event = _order_import_event(store, prepared.order_plan_id)
        key = _dispatch_key(prepared)

        missing = _PaperEventMutationGuard(
            load_authoritative=lambda _key: prepared,
            load_state_json=lambda _key: canonical_json_bytes(prepared).decode(
                "utf-8"
            ),
        )
        missing.capture_before(key)
        claimed = _claimed(prepared)
        missing.load_authoritative = lambda _key: claimed
        missing.load_state_json = lambda _key: canonical_json_bytes(claimed).decode(
            "utf-8"
        )
        missing.register_change(
            after=claimed,
            state_json=canonical_json_bytes(claimed).decode("utf-8"),
            rowcount=1,
        )
        with pytest.raises(PaperStateConflictError):
            missing.assert_complete()

        extra = _PaperEventMutationGuard(
            load_authoritative=lambda _key: prepared,
            load_state_json=lambda _key: canonical_json_bytes(prepared).decode(
                "utf-8"
            ),
        )
        extra.register_candidate(event)
        extra.register_append_result(event, advanced=True)
        with pytest.raises(PaperStateConflictError):
            extra.assert_complete()

        noncanonical = _PaperEventMutationGuard(
            load_authoritative=lambda _key: prepared,
            load_state_json=lambda _key: canonical_json_bytes(prepared).decode(
                "utf-8"
            ),
        )
        noncanonical.capture_before(key)
        noncanonical.load_authoritative = lambda _key: claimed
        noncanonical.load_state_json = lambda _key: canonical_json_bytes(
            claimed
        ).decode("utf-8")
        with pytest.raises(PaperStateCorruptionError):
            noncanonical.register_change(
                after=claimed,
                state_json="{}",
                rowcount=1,
            )


def test_runtime_append_rejects_noncanonical_authoritative_state_json(
    tmp_path,
) -> None:
    path = tmp_path / "raw-state-json.sqlite3"
    prepared = _create_v10_prepared(path)
    with paper_store(path) as store:
        claimed = _claimed(prepared)
        key = _dispatch_key(claimed)
        noncanonical_state_json = json.dumps(
            claimed.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        with pytest.raises(PaperStateCorruptionError):
            with store._event_transaction() as guard:  # noqa: SLF001
                guard.capture_before(key)
                cursor = store._connection.execute(  # noqa: SLF001
                    """
                    UPDATE paper_order_dispatches
                    SET status = ?, revision = ?, state_json = ?, updated_at = ?
                    WHERE order_plan_id = ?
                    """,
                    (
                        claimed.status,
                        claimed.revision,
                        noncanonical_state_json,
                        claimed.updated_at.isoformat(),
                        claimed.order_plan_id,
                    ),
                )
                guard.register_change(
                    after=claimed,
                    state_json=store._serialize(claimed),  # noqa: SLF001
                    rowcount=cursor.rowcount,
                )

        assert store.load_paper_order_dispatch(prepared.order_plan_id) == prepared
        assert len(store.list_paper_execution_events()) == 2


def test_mutation_guard_rejects_noncanonical_before_state_without_healing(
    tmp_path,
) -> None:
    path = tmp_path / "raw-before-state-json.sqlite3"
    prepared = _create_v10_prepared(path)
    with paper_store(path) as store:
        noncanonical_state_json = json.dumps(
            prepared.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        store._connection.execute(  # noqa: SLF001 - persisted corruption harness
            "UPDATE paper_order_dispatches SET state_json = ? "
            "WHERE order_plan_id = ?",
            (noncanonical_state_json, prepared.order_plan_id),
        )
        store._connection.commit()  # noqa: SLF001

        with pytest.raises(PaperStateCorruptionError):
            with store._event_transaction() as guard:  # noqa: SLF001
                guard.capture_before(_dispatch_key(prepared))

        raw = store._connection.execute(  # noqa: SLF001
            "SELECT state_json FROM paper_order_dispatches WHERE order_plan_id = ?",
            (prepared.order_plan_id,),
        ).fetchone()[0]
        assert raw == noncanonical_state_json
        assert len(store.list_paper_execution_events()) == 2


def test_source_delta_mismatch_rolls_back_row_and_event(tmp_path) -> None:
    path = tmp_path / "source-delta.sqlite3"
    prepared = _create_v10_prepared(path)
    with paper_store(path) as store:
        claimed = _claimed(prepared)
        predecessor = _order_import_event(store, prepared.order_plan_id)
        mismatched = store._new_runtime_paper_execution_event(  # noqa: SLF001
            aggregate_version=2,
            event_type="OutcomeUnknown",
            source="process_recovery",
            after=claimed,
            causation_id=predecessor.event_id,
            before=prepared,
        )
        key = _dispatch_key(claimed)
        with pytest.raises(PaperStateCorruptionError):
            with store._event_transaction() as guard:  # noqa: SLF001
                guard.capture_before(key)
                rowcount = _write_dispatch_row(store, claimed)
                guard.register_change(
                    after=claimed,
                    state_json=store._serialize(claimed),  # noqa: SLF001
                    rowcount=rowcount,
                )
                guard.register_candidate(mismatched)
                store._append_paper_execution_events(  # noqa: SLF001
                    [mismatched],
                    expected_previous_versions={key: 1},
                    guard=guard,
                )
        assert store.load_paper_order_dispatch(prepared.order_plan_id) == prepared
        assert len(store.list_paper_execution_events()) == 2


def test_unknown_event_schema_is_migration_required_and_writes_nothing(
    tmp_path,
) -> None:
    path = tmp_path / "unknown-schema.sqlite3"
    prepared = _create_v10_prepared(path)
    with paper_store(path) as store:
        event = _order_import_event(store, prepared.order_plan_id)
        raw = event.model_dump(mode="json")
        raw["event_schema_version"] = 99
        key = _dispatch_key(prepared)
        with pytest.raises(PaperStateMigrationRequired):
            with store._transaction():  # noqa: SLF001
                store._append_paper_execution_events(  # type: ignore[list-item]  # noqa: SLF001
                    [raw],
                    expected_previous_versions={key: event.aggregate_version},
                    guard=None,
                    import_mode=True,
                )
        assert len(store.list_paper_execution_events()) == 2


@pytest.mark.parametrize(
    ("column", "value"),
    [("event_schema_version", 99), ("event_type", "FutureOrderEvent")],
)
def test_persisted_unknown_discriminator_precedes_malformed_payload(
    tmp_path,
    column: str,
    value: object,
) -> None:
    path = tmp_path / f"persisted-unknown-{column}.sqlite3"
    prepared = _create_v10_prepared(path)
    with paper_store(path) as store:
        event = _order_import_event(store, prepared.order_plan_id)
        store._connection.execute(  # noqa: SLF001 - persisted corruption harness
            f"UPDATE paper_execution_events SET {column} = ?, payload_json = ? "
            "WHERE event_id = ?",
            (value, "{", event.event_id),
        )
        store._connection.commit()  # noqa: SLF001

        with pytest.raises(PaperStateMigrationRequired):
            store.list_paper_execution_events()


def test_order_prepared_requires_exact_risk_reserved_causation(tmp_path) -> None:
    path = tmp_path / "forged-prepare-causation.sqlite3"
    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="forged-causation")
        reservation = make_reservation(prepared)
        risk_event = store._new_runtime_paper_execution_event(  # noqa: SLF001
            aggregate_version=1,
            event_type="RiskReserved",
            source="local_prepare",
            after=reservation,
            causation_id=None,
        )
        order_event = store._new_runtime_paper_execution_event(  # noqa: SLF001
            aggregate_version=1,
            event_type="OrderPrepared",
            source="local_prepare",
            after=prepared,
            causation_id="pevt-forged-cause",
        )

        with pytest.raises(PaperStateCorruptionError):
            store._validate_paper_event_batch_causation(  # noqa: SLF001
                [risk_event, order_event]
            )


def test_paired_facts_cannot_mix_duplicate_and_advancing_results(tmp_path) -> None:
    path = tmp_path / "mixed-paired-append-results.sqlite3"
    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="mixed-pair-results")
        reservation = make_reservation(prepared)
        risk_event = store._new_runtime_paper_execution_event(  # noqa: SLF001
            aggregate_version=1,
            event_type="RiskReserved",
            source="local_prepare",
            after=reservation,
            causation_id=None,
        )
        order_event = store._new_runtime_paper_execution_event(  # noqa: SLF001
            aggregate_version=1,
            event_type="OrderPrepared",
            source="local_prepare",
            after=prepared,
            causation_id=risk_event.event_id,
        )

        with pytest.raises(PaperStateCorruptionError):
            store._validate_paper_event_batch_causation(  # noqa: SLF001
                [risk_event, order_event],
                append_results=[False, True],
            )


def test_identity_scope_reuse_rolls_back_new_aggregate_and_event(tmp_path) -> None:
    path = tmp_path / "identity-collision.sqlite3"
    corpus = create_v10_legacy_corpus(path)
    with paper_store(path) as store:
        second_fills = [
            fill.model_copy(
                update={
                    "broker_order_id": "bord-event-store-identity-second",
                    "broker_order_reference": "0000005678",
                }
            )
            for fill in corpus.dispatch.fill_evidence
        ]
        second = PaperOrderDispatch.model_validate(
            corpus.dispatch.model_copy(
                update={
                    "order_plan_id": "oplan-event-store-identity-second",
                    "broker_order_id": "bord-event-store-identity-second",
                    "run_id": "run-event-store-identity-second",
                    "idempotency_key": "paper-event-store-identity-second",
                    "request_fingerprint": "sha256:" + "d" * 64,
                    "risk_check_id": "risk-event-store-identity-second",
                    "reconciled_snapshot_id": "snapshot-event-store-identity-second",
                    "broker_order_reference": "0000005678",
                    "fill_evidence": second_fills,
                }
            ).model_dump()
        )
        import_event = store._legacy_paper_execution_event(  # noqa: SLF001
            after=second,
            received_at=NOW + timedelta(days=1),
        )
        key = _dispatch_key(second)
        before_event_count = len(store.list_paper_execution_events())

        with pytest.raises(PaperStateConflictError):
            with store._transaction():  # noqa: SLF001
                store._connection.execute(  # noqa: SLF001
                    """
                    INSERT INTO paper_order_dispatches (
                        order_plan_id, broker_order_id, idempotency_key, store_id,
                        session_id, fencing_token, status, revision, state_json,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        second.order_plan_id,
                        second.broker_order_id,
                        second.idempotency_key,
                        second.store_id,
                        second.session_id,
                        second.fencing_token,
                        second.status,
                        second.revision,
                        store._serialize(second),  # noqa: SLF001
                        second.updated_at.isoformat(),
                    ),
                )
                store._append_paper_execution_events(  # noqa: SLF001
                    [import_event],
                    expected_previous_versions={key: 0},
                    guard=None,
                    import_mode=True,
                )

        assert store.load_paper_order_dispatch(second.order_plan_id) is None
        assert len(store.list_paper_execution_events()) == before_event_count


def test_identical_identity_scope_under_distinct_event_id_is_unique_and_atomic(
    tmp_path,
) -> None:
    path = tmp_path / "identical-identity-collision.sqlite3"
    create_v10_legacy_corpus(path)
    with paper_store(path) as store:
        original = next(
            event
            for event in store.list_paper_execution_events()
            if event.identity_keys
        )
        original_key = original.identity_keys[0]
        row = store._connection.execute(  # noqa: SLF001
            "SELECT * FROM paper_execution_events WHERE event_id = ?",
            (original.event_id,),
        ).fetchone()
        assert row is not None
        columns = [
            item[1]
            for item in store._connection.execute(  # noqa: SLF001
                "PRAGMA table_info(paper_execution_events)"
            ).fetchall()
        ]
        values = {column: row[column] for column in columns}
        clone_event_id = "pevt-identical-identity-collision"
        values.update(
            {
                "event_id": clone_event_id,
                "aggregate_id": "oplan-identical-identity-collision",
                "aggregate_version": 1,
            }
        )
        placeholders = ", ".join("?" for _ in columns)
        quoted_columns = ", ".join(f'"{column}"' for column in columns)

        with pytest.raises(sqlite3.IntegrityError):
            with store._transaction():  # noqa: SLF001
                store._connection.execute(  # noqa: SLF001
                    f"INSERT INTO paper_execution_events ({quoted_columns}) "
                    f"VALUES ({placeholders})",
                    tuple(values[column] for column in columns),
                )
                store._connection.execute(  # noqa: SLF001
                    "INSERT INTO paper_execution_event_identity_keys "
                    "(event_id, identity_kind, identity_scope_hash, external_id, "
                    "evidence_payload_hash) VALUES (?, ?, ?, ?, ?)",
                    (
                        clone_event_id,
                        original_key.kind,
                        original_key.scope_hash,
                        original_key.external_id,
                        original_key.evidence_payload_hash,
                    ),
                )

        assert store._connection.execute(  # noqa: SLF001
            "SELECT 1 FROM paper_execution_events WHERE event_id = ?",
            (clone_event_id,),
        ).fetchone() is None


def test_two_connections_same_expected_version_have_one_winner_and_no_gap(
    tmp_path,
) -> None:
    path = tmp_path / "concurrent-version.sqlite3"
    prepared = _create_v10_prepared(path)
    # Complete the schema migration before testing append-version contention.
    # Migration concurrency is a separate invariant from the one-winner append.
    with paper_store(path):
        pass
    claimed = _claimed(prepared)
    barrier = Barrier(2)

    def attempt() -> bool:
        barrier.wait(timeout=10)
        with paper_store(path) as store:
            predecessor, event = _claim_candidate(store, prepared, claimed)
            key = _dispatch_key(claimed)
            try:
                with store._event_transaction() as guard:  # noqa: SLF001
                    guard.capture_before(key)
                    rowcount = _write_dispatch_row(store, claimed)
                    guard.register_change(
                        after=claimed,
                        state_json=store._serialize(claimed),  # noqa: SLF001
                        rowcount=rowcount,
                    )
                    guard.register_candidate(event)
                    store._append_paper_execution_events(  # noqa: SLF001
                        [event],
                        expected_previous_versions={
                            key: predecessor.aggregate_version
                        },
                        guard=guard,
                    )
                return True
            except PaperStateConflictError:
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: attempt(), range(2)))

    assert sorted(results) == [False, True]
    with paper_store(path) as store:
        stream = store.list_paper_execution_events(
            aggregate_type="order_dispatch",
            aggregate_id=prepared.order_plan_id,
        )
        assert [event.aggregate_version for event in stream] == [1, 2]
        assert store.load_paper_order_dispatch(prepared.order_plan_id) == claimed
