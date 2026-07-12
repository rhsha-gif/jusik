from __future__ import annotations

import sqlite3
import shutil

import pytest

from quantpilot.packages.core.execution.events import (
    canonical_import_event_id,
    canonical_sha256,
    event_canonical_bytes,
    payload_after,
)
from quantpilot.packages.db.sqlite_repositories import (
    PAPER_STATE_SCHEMA_VERSION,
    PaperStateCorruptionError,
    PaperStateMigrationRequired,
    PaperStateStore,
)
from quantpilot.tests.paper_execution_event_store_fixtures import (
    NOW,
    create_v10_legacy_corpus,
    downgrade_to_schema,
    insert_reserved_dispatch,
    make_dispatch,
    paper_store,
    start_session,
)


EVENT_COLUMNS = [
    "event_id",
    "store_id",
    "aggregate_type",
    "aggregate_id",
    "aggregate_version",
    "event_type",
    "account_scope_fingerprint",
    "data_mode",
    "broker_environment",
    "source",
    "occurred_at",
    "received_at",
    "correlation_id",
    "causation_id",
    "idempotency_key",
    "local_broker_order_id",
    "broker_order_id",
    "original_client_order_id",
    "venue_order_id",
    "broker_sequence",
    "source_revision",
    "event_schema_version",
    "payload_json",
    "payload_hash",
]

IDENTITY_COLUMNS = [
    "event_id",
    "identity_kind",
    "identity_scope_hash",
    "external_id",
    "evidence_payload_hash",
]


def _index_columns(connection: sqlite3.Connection, table_name: str) -> list[list[str]]:
    indexes: list[list[str]] = []
    for row in connection.execute(f"PRAGMA index_list({table_name})").fetchall():
        if not row[2]:
            continue
        indexes.append(
            [
                info[2]
                for info in connection.execute(
                    f'PRAGMA index_info("{row[1]}")'
                ).fetchall()
            ]
        )
    return indexes


def test_fresh_schema_v11_has_exact_event_tables_indexes_and_foreign_keys(
    tmp_path,
) -> None:
    path = tmp_path / "fresh-v11.sqlite3"
    with paper_store(path) as store:
        assert store.provenance.schema_version == PAPER_STATE_SCHEMA_VERSION == 11

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 11
        assert [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(paper_execution_events)"
            ).fetchall()
        ] == EVENT_COLUMNS
        assert [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(paper_execution_event_identity_keys)"
            ).fetchall()
        ] == IDENTITY_COLUMNS

        assert [
            "store_id",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
        ] in _index_columns(connection, "paper_execution_events")
        assert ["identity_kind", "identity_scope_hash"] in _index_columns(
            connection,
            "paper_execution_event_identity_keys",
        )

        event_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(paper_execution_events)"
        ).fetchall()
        identity_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(paper_execution_event_identity_keys)"
        ).fetchall()
        assert any(
            row[2] == "state_store_metadata"
            and row[3] == "store_id"
            and row[4] == "store_id"
            for row in event_foreign_keys
        )
        assert any(
            row[2] == "paper_execution_events"
            and row[3] == "event_id"
            and row[4] == "event_id"
            for row in identity_foreign_keys
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("case", "corruption_sql"),
    [
        ("missing-table", "DROP TABLE paper_execution_event_identity_keys"),
        ("missing-index", "DROP INDEX ix_paper_execution_event_stream"),
        (
            "missing-foreign-key",
            """
            DROP TABLE paper_execution_event_identity_keys;
            CREATE TABLE paper_execution_event_identity_keys (
                event_id TEXT NOT NULL,
                identity_kind TEXT NOT NULL,
                identity_scope_hash TEXT NOT NULL,
                external_id TEXT NOT NULL,
                evidence_payload_hash TEXT NOT NULL,
                PRIMARY KEY (event_id, identity_kind, identity_scope_hash),
                UNIQUE (identity_kind, identity_scope_hash)
            ) WITHOUT ROWID
            """,
        ),
        (
            "cascade-foreign-key",
            """
            DROP TABLE paper_execution_event_identity_keys;
            CREATE TABLE paper_execution_event_identity_keys (
                event_id TEXT NOT NULL,
                identity_kind TEXT NOT NULL,
                identity_scope_hash TEXT NOT NULL,
                external_id TEXT NOT NULL,
                evidence_payload_hash TEXT NOT NULL,
                PRIMARY KEY (event_id, identity_kind, identity_scope_hash),
                UNIQUE (identity_kind, identity_scope_hash),
                FOREIGN KEY (event_id) REFERENCES paper_execution_events(event_id)
                    ON DELETE CASCADE
            ) WITHOUT ROWID
            """,
        ),
    ],
)
def test_current_v11_schema_corruption_fails_closed_without_recreation(
    tmp_path,
    case: str,
    corruption_sql: str,
) -> None:
    path = tmp_path / "corrupt-v11.sqlite3"
    with paper_store(path):
        pass

    connection = sqlite3.connect(path)
    try:
        connection.executescript(corruption_sql)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperStateCorruptionError):
        paper_store(path)

    connection = sqlite3.connect(path)
    try:
        if case == "missing-table":
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' "
                "AND name = 'paper_execution_event_identity_keys'"
            ).fetchone()
            assert table is None
        elif case == "missing-index":
            index = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'index' "
                "AND name = 'ix_paper_execution_event_stream'"
            ).fetchone()
            assert index is None
        elif case == "missing-foreign-key":
            assert connection.execute(
                "PRAGMA foreign_key_list(paper_execution_event_identity_keys)"
            ).fetchall() == []
        else:
            foreign_key = connection.execute(
                "PRAGMA foreign_key_list(paper_execution_event_identity_keys)"
            ).fetchone()
            assert foreign_key is not None
            assert foreign_key[6] == "CASCADE"
    finally:
        connection.close()


def test_schema_v10_import_is_deterministic_single_clock_and_reopen_noop(
    tmp_path,
) -> None:
    path = tmp_path / "v10-import.sqlite3"
    corpus = create_v10_legacy_corpus(path)

    with paper_store(path) as migrated:
        assert migrated.provenance.schema_version == 11
        events = migrated.list_paper_execution_events()

    assert len(events) == 3
    assert {event.aggregate_version for event in events} == {1}
    assert {event.received_at for event in events} == {events[0].received_at}
    assert {event.source for event in events} == {"schema_migration"}
    assert {event.causation_id for event in events} == {None}

    expected_after = {
        ("order_dispatch", corpus.dispatch.order_plan_id): corpus.dispatch,
        (
            "risk_reservation",
            corpus.reservation.reservation_id,
        ): corpus.reservation,
        ("cancel_request", corpus.cancel.cancel_id): corpus.cancel,
    }
    expected_types = {
        "order_dispatch": "LegacyOrderDispatchImported",
        "risk_reservation": "LegacyRiskReservationImported",
        "cancel_request": "LegacyCancelRequestImported",
    }
    for event in events:
        after = expected_after[(event.aggregate_type, event.aggregate_id)]
        assert payload_after(event.payload) == after
        assert event.payload.legacy_snapshot is True
        assert event.event_type == expected_types[event.aggregate_type]
        assert event.source_revision == after.revision
        assert event.occurred_at == after.updated_at
        assert event.payload_hash == canonical_sha256(event.payload)
        assert event.event_id == canonical_import_event_id(
            store_id=after.store_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            source_revision=after.revision,
            payload_hash=event.payload_hash,
        )

    dispatch_event = next(
        event for event in events if event.aggregate_type == "order_dispatch"
    )
    assert [key.external_id for key in dispatch_event.identity_keys] == [
        "aggregate-event-store-002",
        "fill-event-store-001",
    ]
    assert {key.kind for key in dispatch_event.identity_keys} == {
        "broker_cumulative_delta",
        "venue_execution",
    }

    first_bytes = [event_canonical_bytes(event) for event in events]
    connection = sqlite3.connect(path)
    try:
        identity_rows = connection.execute(
            """
            SELECT identity_kind, identity_scope_hash, external_id,
                   evidence_payload_hash
            FROM paper_execution_event_identity_keys
            WHERE event_id = ?
            ORDER BY identity_kind, identity_scope_hash
            """,
            (dispatch_event.event_id,),
        ).fetchall()
        assert len(identity_rows) == 2
    finally:
        connection.close()

    with paper_store(path) as reopened:
        reopened_events = reopened.list_paper_execution_events()
    assert [event_canonical_bytes(event) for event in reopened_events] == first_bytes


@pytest.mark.parametrize("legacy_version", [8, 9])
def test_direct_pre_v10_schema_backfills_reservation_before_import(
    tmp_path,
    legacy_version: int,
) -> None:
    path = tmp_path / f"v{legacy_version}-backfill.sqlite3"
    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix=f"v{legacy_version}")
        insert_reserved_dispatch(store, prepared)
    downgrade_to_schema(path, target_version=legacy_version)

    with paper_store(path) as migrated:
        reservation = migrated.load_paper_risk_reservation(prepared.order_plan_id)
        events = migrated.list_paper_execution_events()

    assert reservation is not None
    assert reservation.status == "held"
    assert reservation.order_plan_id == prepared.order_plan_id
    assert {
        (event.aggregate_type, event.aggregate_id, event.event_type)
        for event in events
    } == {
        (
            "order_dispatch",
            prepared.order_plan_id,
            "LegacyOrderDispatchImported",
        ),
        (
            "risk_reservation",
            reservation.reservation_id,
            "LegacyRiskReservationImported",
        ),
    }
    assert {event.received_at for event in events} == {events[0].received_at}


@pytest.mark.parametrize("legacy_version", [8, 9])
def test_pre_v10_backfill_and_import_are_deterministic_across_clone_and_retry(
    tmp_path,
    monkeypatch,
    legacy_version: int,
) -> None:
    source = tmp_path / f"v{legacy_version}-source.sqlite3"
    with paper_store(source) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix=f"clone-v{legacy_version}")
        insert_reserved_dispatch(store, prepared)
    downgrade_to_schema(source, target_version=legacy_version)

    clean_path = tmp_path / f"v{legacy_version}-clean.sqlite3"
    retry_path = tmp_path / f"v{legacy_version}-retry.sqlite3"
    shutil.copyfile(source, clean_path)
    shutil.copyfile(source, retry_path)
    assert clean_path.read_bytes() == retry_path.read_bytes()

    with paper_store(clean_path) as clean:
        clean_reservation = clean.load_paper_risk_reservation(
            prepared.order_plan_id
        )
        clean_events = clean.list_paper_execution_events()
    assert clean_reservation is not None

    original_insert = PaperStateStore._insert_paper_execution_event
    failed = False

    def fail_first_insert(self, event):
        nonlocal failed
        if not failed:
            failed = True
            raise sqlite3.IntegrityError("injected deterministic retry failure")
        return original_insert(self, event)

    with monkeypatch.context() as patch:
        patch.setattr(
            PaperStateStore,
            "_insert_paper_execution_event",
            fail_first_insert,
        )
        with pytest.raises(PaperStateMigrationRequired):
            paper_store(retry_path)

    with paper_store(retry_path) as retried:
        retry_reservation = retried.load_paper_risk_reservation(
            prepared.order_plan_id
        )
        retry_events = retried.list_paper_execution_events()
    assert retry_reservation is not None
    assert retry_reservation.reservation_id == clean_reservation.reservation_id
    assert [
        event.model_dump(mode="json", exclude={"received_at"})
        for event in retry_events
    ] == [
        event.model_dump(mode="json", exclude={"received_at"})
        for event in clean_events
    ]


@pytest.mark.parametrize("legacy_version", [6, 7])
def test_schema_v6_and_v7_migration_creates_no_fabricated_import_events(
    tmp_path,
    legacy_version: int,
) -> None:
    path = tmp_path / f"v{legacy_version}-empty.sqlite3"
    with paper_store(path):
        pass
    downgrade_to_schema(path, target_version=legacy_version)

    with paper_store(path) as migrated:
        assert migrated.provenance.schema_version == 11
        assert migrated.list_paper_execution_events() == []


def test_import_failure_rolls_back_ddl_events_metadata_and_user_version(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "v10-rollback.sqlite3"
    create_v10_legacy_corpus(path)
    original_insert = PaperStateStore._insert_paper_execution_event
    calls = 0

    def fail_second_insert(self, event):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.IntegrityError("injected import failure")
        return original_insert(self, event)

    monkeypatch.setattr(
        PaperStateStore,
        "_insert_paper_execution_event",
        fail_second_insert,
    )
    with pytest.raises(PaperStateMigrationRequired):
        paper_store(path)
    assert calls == 2

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        metadata_version = connection.execute(
            "SELECT schema_version FROM state_store_metadata WHERE singleton_id = 1"
        ).fetchone()[0]
        assert metadata_version == 10
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "paper_execution_events" not in tables
        assert "paper_execution_event_identity_keys" not in tables
    finally:
        connection.close()


def test_schema_migration_uses_persisted_provenance_before_runtime_install(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "persisted-provenance.sqlite3"
    create_v10_legacy_corpus(path)

    def fail_runtime_accessor(_self):
        raise AssertionError("migration accessed runtime paper provenance")

    monkeypatch.setattr(
        PaperStateStore,
        "_require_paper_store",
        fail_runtime_accessor,
    )
    store = paper_store(path)
    try:
        assert store.provenance.schema_version == 11
    finally:
        store.close()
