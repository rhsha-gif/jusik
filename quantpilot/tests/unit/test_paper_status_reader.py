from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from quantpilot.packages.core.operator.position_ledger import (
    OperatorSafetyState,
    StrategyOperatorState,
)
from quantpilot.packages.db.paper_status_reader import (
    PaperStatusReader,
    PaperStatusReaderError,
    read_professional_operator_status,
)
from quantpilot.packages.db.sqlite_repositories import (
    PAPER_STATE_SCHEMA_VERSION,
    PaperStateStore,
)


NOW = datetime(2026, 7, 10, 2, 0, tzinfo=timezone.utc)
ACCOUNT_FINGERPRINT = "sha256:" + "a" * 64


def _paper_store(path) -> PaperStateStore:
    return PaperStateStore(
        path,
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=ACCOUNT_FINGERPRINT,
    )


def _create_paper_database(path) -> None:
    with _paper_store(path):
        pass


def _assert_strict_reason(path, reason_code: str) -> PaperStatusReaderError:
    with pytest.raises(PaperStatusReaderError) as caught:
        PaperStatusReader(path).read_strict(observed_at=NOW)
    assert caught.value.reason_code == reason_code
    return caught.value


def test_reader_uses_environment_path_reads_wal_and_omits_account_fingerprint(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "paper-status-wal.sqlite3"
    store = _paper_store(path)
    try:
        store.save_operator_safety_state(
            OperatorSafetyState(
                policy_id="policy-paper",
                autopilot_paused=False,
                broker_healthy=True,
                updated_at=NOW - timedelta(seconds=5),
            )
        )
        store.save_strategy_operator_state(
            StrategyOperatorState(
                policy_id="policy-paper",
                strategy_id="pullback_trend_v2",
                strategy_version="2.0",
                updated_at=NOW - timedelta(seconds=5),
            )
        )
        store.start_paper_execution_session(
            started_at=NOW - timedelta(seconds=30),
            lease_expires_at=NOW + timedelta(minutes=5),
        )
        wal_path = path.with_name(f"{path.name}-wal")
        assert wal_path.is_file()
        assert wal_path.stat().st_size > 0

        monkeypatch.setenv("KIS_PAPER_STATE_DB", str(path))
        snapshot = PaperStatusReader().read_strict(observed_at=NOW)
    finally:
        store.close()

    assert snapshot.available is True
    assert snapshot.schema_version == PAPER_STATE_SCHEMA_VERSION
    assert [item.policy_id for item in snapshot.safety.policies] == ["policy-paper"]
    assert snapshot.safety.latest_session.status == "active"
    assert ACCOUNT_FINGERPRINT not in snapshot.model_dump_json()


def test_strict_reader_does_not_modify_database_file(tmp_path) -> None:
    path = tmp_path / "paper-status-immutable.sqlite3"
    _create_paper_database(path)
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    snapshot = PaperStatusReader(path).read_strict(observed_at=NOW)

    assert snapshot.available is True
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_missing_database_is_unavailable_and_is_not_created(tmp_path) -> None:
    path = tmp_path / "does-not-exist.sqlite3"

    error = _assert_strict_reason(path, "paper_state_database_missing")
    snapshot = read_professional_operator_status(
        observed_at=NOW,
        stale_after_seconds=77,
        database_path=path,
    )

    assert not path.exists()
    assert str(path) not in str(error)
    assert snapshot.available is False
    assert snapshot.overall_status == "unavailable"
    assert snapshot.reason_code == "paper_state_database_missing"
    assert snapshot.freshness.stale_after_seconds == 77


@pytest.mark.parametrize(
    ("configured_path", "reason_code"),
    [
        (None, "paper_state_path_unset"),
        ("relative-paper-state.sqlite3", "paper_state_path_invalid"),
    ],
)
def test_unset_or_relative_database_path_fails_closed(
    configured_path,
    reason_code,
    monkeypatch,
) -> None:
    monkeypatch.delenv("KIS_PAPER_STATE_DB", raising=False)

    snapshot = read_professional_operator_status(
        observed_at=NOW,
        database_path=configured_path,
    )

    assert snapshot.available is False
    assert snapshot.overall_status == "unavailable"
    assert snapshot.reason_code == reason_code


def test_locked_database_fails_closed(tmp_path) -> None:
    path = tmp_path / "paper-status-locked.sqlite3"
    _create_paper_database(path)
    blocker = sqlite3.connect(path, timeout=0.0)
    try:
        blocker.execute("PRAGMA journal_mode = DELETE").fetchone()
        blocker.execute("BEGIN EXCLUSIVE")

        _assert_strict_reason(path, "paper_state_database_locked")
        snapshot = read_professional_operator_status(
            observed_at=NOW,
            database_path=path,
        )
    finally:
        blocker.rollback()
        blocker.close()

    assert snapshot.available is False
    assert snapshot.overall_status == "unavailable"
    assert snapshot.reason_code == "paper_state_database_locked"


def test_schema_version_mismatch_fails_closed(tmp_path) -> None:
    path = tmp_path / "paper-status-old-schema.sqlite3"
    _create_paper_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {PAPER_STATE_SCHEMA_VERSION - 1}")

    _assert_strict_reason(path, "paper_state_schema_mismatch")
    snapshot = read_professional_operator_status(
        observed_at=NOW,
        database_path=path,
    )

    assert snapshot.available is False
    assert snapshot.overall_status == "unavailable"
    assert snapshot.reason_code == "paper_state_schema_mismatch"


def test_non_paper_provenance_fails_closed(tmp_path) -> None:
    path = tmp_path / "fixture-state.sqlite3"
    with PaperStateStore(
        path,
        data_mode="fixture",
        broker_environment="fixture_mock",
    ):
        pass

    error = _assert_strict_reason(path, "paper_state_provenance_mismatch")
    snapshot = read_professional_operator_status(
        observed_at=NOW,
        database_path=path,
    )

    assert ACCOUNT_FINGERPRINT not in str(error)
    assert snapshot.available is False
    assert snapshot.overall_status == "unavailable"
    assert snapshot.reason_code == "paper_state_provenance_mismatch"


@pytest.mark.parametrize(
    "table_name",
    [
        "operator_safety_states",
        "managed_positions",
        "strategy_operator_states",
        "pending_liquidations",
        "operator_cycle_claims",
        "paper_execution_sessions",
        "paper_order_dispatches",
    ],
)
def test_every_projected_state_row_is_pydantic_validated(
    tmp_path,
    table_name,
) -> None:
    path = tmp_path / f"invalid-{table_name}.sqlite3"
    with _paper_store(path) as store:
        store_id = store.provenance.store_id
    _insert_invalid_row(path, table_name=table_name, store_id=store_id)

    _assert_strict_reason(path, "paper_state_corrupt")
    snapshot = read_professional_operator_status(
        observed_at=NOW,
        database_path=path,
    )

    assert snapshot.available is False
    assert snapshot.overall_status == "unavailable"
    assert snapshot.reason_code == "paper_state_corrupt"


def test_metadata_mismatch_and_raw_payload_never_leak(tmp_path) -> None:
    path = tmp_path / "paper-status-secret-corrupt.sqlite3"
    _create_paper_database(path)
    raw_payload = json.dumps(
        {
            "policy_id": "policy-paper",
            "account_scope_fingerprint": ACCOUNT_FINGERPRINT,
        }
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO operator_safety_states (policy_id, state_json, updated_at)
            VALUES (?, ?, ?)
            """,
            ("policy-paper", raw_payload, NOW.isoformat()),
        )

    error = _assert_strict_reason(path, "paper_state_corrupt")
    snapshot = read_professional_operator_status(
        observed_at=NOW,
        database_path=path,
    )
    serialized = snapshot.model_dump_json()

    assert ACCOUNT_FINGERPRINT not in str(error)
    assert ACCOUNT_FINGERPRINT not in repr(error)
    assert ACCOUNT_FINGERPRINT not in serialized
    assert str(path) not in str(error)
    assert snapshot.overall_status == "unavailable"


def _insert_invalid_row(path, *, table_name: str, store_id: str) -> None:
    now = NOW.isoformat()
    inserts = {
        "operator_safety_states": (
            "INSERT INTO operator_safety_states VALUES (?, ?, ?)",
            ("policy-invalid", "{}", now),
        ),
        "managed_positions": (
            "INSERT INTO managed_positions VALUES (?, ?, ?, ?, ?, ?)",
            ("policy-invalid", "strategy-invalid", "1", "005930", "{}", now),
        ),
        "strategy_operator_states": (
            "INSERT INTO strategy_operator_states VALUES (?, ?, ?, ?, ?)",
            ("policy-invalid", "strategy-invalid", "1", "{}", now),
        ),
        "pending_liquidations": (
            "INSERT INTO pending_liquidations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "plan-invalid",
                "idempotency-invalid",
                "policy-invalid",
                "strategy-invalid",
                "1",
                "005930",
                "{}",
                now,
            ),
        ),
        "operator_cycle_claims": (
            "INSERT INTO operator_cycle_claims VALUES (?, ?, ?, ?, ?, ?)",
            (
                "policy-invalid",
                "strategy-invalid",
                "1",
                "risk_evaluation",
                "2026-07-10T02:00Z",
                "{}",
            ),
        ),
        "paper_execution_sessions": (
            "INSERT INTO paper_execution_sessions VALUES (?, ?, ?, ?, ?, ?)",
            ("session-invalid", store_id, 99, "closed", "{}", now),
        ),
        "paper_order_dispatches": (
            "INSERT INTO paper_order_dispatches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "plan-invalid",
                "broker-invalid",
                "idempotency-invalid",
                store_id,
                "session-invalid",
                99,
                "prepared",
                0,
                "{}",
                now,
            ),
        ),
    }
    statement, parameters = inserts[table_name]
    with sqlite3.connect(path) as connection:
        connection.execute(statement, parameters)
