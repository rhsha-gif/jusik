from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    PaperRunCheckpoint,
)
from quantpilot.packages.db.sqlite_repositories import (
    PaperStateConflictError,
    PaperStateStore,
)
from quantpilot.packages.db.repositories import InMemoryRepository, RepositoryRegistry


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)


def _position(**updates: object) -> ManagedPositionState:
    values: dict[str, object] = {
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "symbol": "005930",
        "quantity": 10.0,
        "average_entry_price": 100.0,
        "atr14": 2.0,
        "active_stop": 96.0,
        "policy_version": 3,
        "opened_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return ManagedPositionState(**values)


def _checkpoint(**updates: object) -> PaperRunCheckpoint:
    values: dict[str, object] = {
        "run_id": "run-001",
        "idempotency_key": "paper-cycle-2026-07-10T01:00:00Z",
        "policy_version": 3,
        "status": "started",
        "started_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return PaperRunCheckpoint(**values)


def test_paper_state_survives_close_and_reopen(tmp_path) -> None:
    database_path = tmp_path / "paper-state.sqlite3"
    position = _position()
    checkpoint = _checkpoint()

    with PaperStateStore(database_path) as store:
        assert store.save_position(position) == position
        assert store.insert_run_checkpoint(checkpoint) == checkpoint

    with PaperStateStore(database_path) as reopened:
        assert reopened.load_position("pullback_trend_v2", "2.0", "005930") == position
        assert reopened.list_positions() == [position]
        assert reopened.load_run_checkpoint("run-001") == checkpoint
        assert (
            reopened.find_run_checkpoint_by_idempotency_key(checkpoint.idempotency_key)
            == checkpoint
        )


def test_position_update_replaces_one_natural_key_atomically(tmp_path) -> None:
    database_path = tmp_path / "paper-state.sqlite3"
    original = _position()
    updated = original.model_copy(
        update={
            "quantity": 7.0,
            "average_entry_price": 101.5,
            "atr14": 2.25,
            "active_stop": 97.0,
            "updated_at": NOW + timedelta(minutes=1),
        }
    )

    with PaperStateStore(database_path) as store:
        store.save_position(original)
        store.save_position(updated)

        assert store.list_positions() == [updated]
        assert store.load_position(*updated.storage_key) == updated

    with PaperStateStore(database_path) as reopened:
        assert reopened.list_positions() == [updated]


def test_idempotency_key_and_run_id_are_unique_across_restarts(tmp_path) -> None:
    database_path = tmp_path / "paper-state.sqlite3"
    original = _checkpoint()

    with PaperStateStore(database_path) as store:
        store.insert_run_checkpoint(original)
        with pytest.raises(PaperStateConflictError, match="idempotency"):
            store.insert_run_checkpoint(_checkpoint(run_id="run-002"))

        assert store.load_run_checkpoint("run-002") is None
        assert store.load_run_checkpoint(original.run_id) == original

    with PaperStateStore(database_path) as reopened:
        with pytest.raises(PaperStateConflictError, match="run_id"):
            reopened.insert_run_checkpoint(
                _checkpoint(idempotency_key="a-different-idempotency-key")
            )

        assert reopened.load_run_checkpoint(original.run_id) == original


def test_checkpoint_can_advance_without_changing_its_identity(tmp_path) -> None:
    database_path = tmp_path / "paper-state.sqlite3"
    original = _checkpoint()
    completed = original.model_copy(
        update={"status": "completed", "updated_at": NOW + timedelta(minutes=2)}
    )

    with PaperStateStore(database_path) as store:
        store.insert_run_checkpoint(original)
        assert store.update_run_checkpoint(completed) == completed
        assert store.load_run_checkpoint(original.run_id) == completed


def test_models_and_sqlite_payloads_have_no_secret_fields(tmp_path) -> None:
    expected_position_fields = {
        "strategy_id",
        "strategy_version",
        "symbol",
        "quantity",
        "average_entry_price",
        "atr14",
        "active_stop",
        "policy_version",
        "opened_at",
        "updated_at",
    }
    expected_checkpoint_fields = {
        "run_id",
        "idempotency_key",
        "policy_version",
        "status",
        "data_mode",
        "started_at",
        "updated_at",
    }
    assert set(ManagedPositionState.model_fields) == expected_position_fields
    assert set(PaperRunCheckpoint.model_fields) == expected_checkpoint_fields

    with pytest.raises(ValidationError):
        ManagedPositionState(**_position().model_dump(), api_key="must-not-persist")

    database_path = tmp_path / "paper-state.sqlite3"
    with PaperStateStore(database_path) as store:
        store.save_position(_position())
        store.insert_run_checkpoint(_checkpoint())

    connection = sqlite3.connect(database_path)
    try:
        position_json = connection.execute(
            "SELECT state_json FROM managed_positions"
        ).fetchone()[0]
        checkpoint_json = connection.execute(
            "SELECT state_json FROM paper_run_checkpoints"
        ).fetchone()[0]
        columns = {
            row[1]
            for table in ("managed_positions", "paper_run_checkpoints")
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
    finally:
        connection.close()

    assert set(json.loads(position_json)) == expected_position_fields
    assert set(json.loads(checkpoint_json)) == expected_checkpoint_fields
    assert not columns.intersection(
        {"api_key", "api_secret", "account_id", "credential", "access_token"}
    )


def test_invalid_model_copy_is_revalidated_before_persistence(tmp_path) -> None:
    invalid = _position().model_copy(update={"quantity": -1.0})

    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        with pytest.raises(ValidationError):
            store.save_position(invalid)
        assert store.list_positions() == []


def test_older_position_update_is_rejected_without_replacing_current_state(tmp_path) -> None:
    current = _position(updated_at=NOW + timedelta(minutes=2))
    older = current.model_copy(update={"quantity": 5.0, "updated_at": NOW + timedelta(minutes=1)})

    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        store.save_position(current)
        with pytest.raises(PaperStateConflictError, match="older"):
            store.save_position(older)
        assert store.load_position(*current.storage_key) == current


def test_fixture_repository_registry_remains_in_memory_by_default() -> None:
    registry = RepositoryRegistry()

    assert isinstance(registry.policies, InMemoryRepository)
    assert isinstance(registry.order_plans, InMemoryRepository)
