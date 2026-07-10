from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    PaperRunCheckpoint,
    StrategyOperatorState,
)
from quantpilot.packages.db.sqlite_repositories import (
    PaperStateConflictError,
    PaperStateCorruptionError,
    PaperStateStore,
)
from quantpilot.packages.db.repositories import InMemoryRepository, RepositoryRegistry


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)


def _position(**updates: object) -> ManagedPositionState:
    values: dict[str, object] = {
        "policy_id": "policy-main",
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
        "reconciled_snapshot_id": "snapshot-001",
        "reconciled_at": NOW,
    }
    values.update(updates)
    return ManagedPositionState(**values)


def _checkpoint(**updates: object) -> PaperRunCheckpoint:
    values: dict[str, object] = {
        "run_id": "run-001",
        "idempotency_key": "paper-cycle-2026-07-10T01:00:00Z",
        "policy_id": "policy-main",
        "user_id": "fixture-user",
        "policy_version": 3,
        "run_mode": "paper_submit",
        "requested_at": NOW,
        "request_fingerprint": "sha256:" + "1" * 64,
        "status": "started",
        "started_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return PaperRunCheckpoint(**values)


def _strategy_operator_state(**updates: object) -> StrategyOperatorState:
    values: dict[str, object] = {
        "policy_id": "policy-main",
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "health_status": "active",
        "reason_codes": ["healthy"],
        "performance_record_id": "performance-001",
        "retirement_phase": "none",
        "pending_order_plan_ids": [],
        "last_risk_evaluated_at": NOW,
        "last_rebalance_session": "2026-W28",
        "updated_at": NOW,
    }
    values.update(updates)
    return StrategyOperatorState(**values)


def test_paper_state_survives_close_and_reopen(tmp_path) -> None:
    database_path = tmp_path / "paper-state.sqlite3"
    position = _position()
    checkpoint = _checkpoint()

    with PaperStateStore(database_path, allow_fixture_seed=True) as store:
        assert (
            store.seed_fixture_position(position, data_mode="fixture")
            == position
        )
        assert store.insert_run_checkpoint(checkpoint) == checkpoint

    with PaperStateStore(database_path) as reopened:
        assert reopened.load_position("policy-main", "pullback_trend_v2", "2.0", "005930") == position
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
            "active_stop": 97.0,
            "updated_at": NOW + timedelta(minutes=1),
            "reconciled_snapshot_id": "snapshot-002",
            "reconciled_at": NOW + timedelta(minutes=1),
            "revision": 1,
        }
    )

    with PaperStateStore(database_path, allow_fixture_seed=True) as store:
        store.seed_fixture_position(original, data_mode="fixture")
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
        "policy_id",
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
        "reconciled_snapshot_id",
        "reconciled_at",
        "attribution_status",
        "attribution_conflict_reason",
        "attribution_conflicted_at",
        "processed_fill_ids",
        "revision",
    }
    expected_checkpoint_fields = {
        "run_id",
        "idempotency_key",
        "policy_id",
        "user_id",
        "policy_version",
        "run_mode",
        "requested_at",
        "request_fingerprint",
        "status",
        "data_mode",
        "started_at",
        "updated_at",
        "result_payload",
    }
    assert set(ManagedPositionState.model_fields) == expected_position_fields
    assert set(PaperRunCheckpoint.model_fields) == expected_checkpoint_fields

    with pytest.raises(ValidationError):
        ManagedPositionState(**_position().model_dump(), api_key="must-not-persist")

    database_path = tmp_path / "paper-state.sqlite3"
    with PaperStateStore(database_path, allow_fixture_seed=True) as store:
        store.seed_fixture_position(_position(), data_mode="fixture")
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
    current = _position(
        updated_at=NOW + timedelta(minutes=2),
        reconciled_snapshot_id="snapshot-003",
        reconciled_at=NOW + timedelta(minutes=2),
        revision=0,
    )
    older = current.model_copy(
        update={
            "updated_at": NOW + timedelta(minutes=1),
            "reconciled_snapshot_id": "snapshot-002",
            "reconciled_at": NOW + timedelta(minutes=1),
            "revision": 1,
        }
    )

    with PaperStateStore(
        tmp_path / "paper-state.sqlite3",
        allow_fixture_seed=True,
    ) as store:
        store.seed_fixture_position(current, data_mode="fixture")
        with pytest.raises(PaperStateConflictError, match="older"):
            store.save_position(older)
        assert store.load_position(*current.storage_key) == current


def test_runtime_position_insert_and_generic_delete_are_closed(tmp_path) -> None:
    position = _position()
    path = tmp_path / "paper-state.sqlite3"
    with PaperStateStore(path) as store:
        with pytest.raises(PaperStateConflictError, match="atomic fill reconciliation"):
            store.save_position(position)
        with pytest.raises(PaperStateConflictError, match="seeding is disabled"):
            store.seed_fixture_position(position, data_mode="fixture")

    with PaperStateStore(path, allow_fixture_seed=True) as store:
        with pytest.raises(PaperStateConflictError, match="fixture data mode"):
            store.seed_fixture_position(position, data_mode="paper_trading")

        store.seed_fixture_position(position, data_mode="fixture")
        with pytest.raises(PaperStateConflictError, match="atomic fill reconciliation"):
            store.delete_position(*position.storage_key)

        assert store.load_position(*position.storage_key) == position


def test_fixture_repository_registry_remains_in_memory_by_default() -> None:
    registry = RepositoryRegistry()

    assert isinstance(registry.policies, InMemoryRepository)
    assert isinstance(registry.order_plans, InMemoryRepository)


def test_strategy_operator_state_survives_restart(tmp_path) -> None:
    database_path = tmp_path / "paper-state.sqlite3"
    state = _strategy_operator_state(
        health_status="paused_reapproval",
        reason_codes=["mdd_review_required", "mdd_review_required", "benchmark_missing"],
        retirement_phase="awaiting_reconciliation",
        pending_order_plan_ids=["plan-002", "plan-001", "plan-002"],
    )

    assert state.reason_codes == ["benchmark_missing", "mdd_review_required"]
    assert state.pending_order_plan_ids == ["plan-001", "plan-002"]

    with PaperStateStore(database_path) as store:
        assert store.save_strategy_operator_state(state) == state

    with PaperStateStore(database_path) as reopened:
        assert reopened.load_strategy_operator_state(*state.storage_key) == state
        assert reopened.list_strategy_operator_states() == [state]


def test_strategy_operator_state_upserts_one_natural_key(tmp_path) -> None:
    original = _strategy_operator_state()
    updated = original.model_copy(
        update={
            "health_status": "disabled",
            "reason_codes": ["mdd_limit_breached"],
            "retirement_phase": "awaiting_reconciliation",
            "pending_order_plan_ids": ["plan-003"],
            "updated_at": NOW + timedelta(minutes=1),
            "revision": 1,
        }
    )
    other_policy = _strategy_operator_state(
        policy_id="policy-secondary",
        performance_record_id=None,
    )

    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        store.save_strategy_operator_state(original)
        store.save_strategy_operator_state(updated)
        store.save_strategy_operator_state(other_policy)

        assert store.load_strategy_operator_state(*updated.storage_key) == updated
        assert store.list_strategy_operator_states() == [updated, other_policy]


def test_older_strategy_operator_state_is_rejected_without_replacement(tmp_path) -> None:
    current = _strategy_operator_state(updated_at=NOW + timedelta(minutes=2))
    older = current.model_copy(
        update={
            "retirement_phase": "remaining",
            "updated_at": NOW + timedelta(minutes=1),
            "revision": 1,
        }
    )

    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        store.save_strategy_operator_state(current)
        with pytest.raises(PaperStateConflictError, match="older"):
            store.save_strategy_operator_state(older)
        assert store.load_strategy_operator_state(*current.storage_key) == current


def test_strategy_operator_state_is_revalidated_before_persistence(tmp_path) -> None:
    invalid = _strategy_operator_state().model_copy(
        update={"last_rebalance_session": "2026-W54"}
    )

    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        with pytest.raises(ValidationError):
            store.save_strategy_operator_state(invalid)
        assert store.list_strategy_operator_states() == []


@pytest.mark.parametrize("timestamp_field", ["last_risk_evaluated_at", "updated_at"])
def test_strategy_operator_state_requires_aware_timestamps(
    tmp_path,
    timestamp_field: str,
) -> None:
    invalid = _strategy_operator_state().model_copy(
        update={timestamp_field: NOW.replace(tzinfo=None)}
    )

    with PaperStateStore(tmp_path / f"{timestamp_field}.sqlite3") as store:
        with pytest.raises(ValidationError, match="UTC offset"):
            store.save_strategy_operator_state(invalid)
        assert store.list_strategy_operator_states() == []


def test_strategy_operator_state_schema_and_rows_reject_secret_fields(tmp_path) -> None:
    expected_fields = {
        "policy_id",
        "strategy_id",
        "strategy_version",
        "health_status",
        "reason_codes",
        "performance_record_id",
        "retirement_phase",
        "pending_order_plan_ids",
        "last_risk_evaluated_at",
        "last_rebalance_session",
        "updated_at",
        "revision",
    }
    assert set(StrategyOperatorState.model_fields) == expected_fields

    with pytest.raises(ValidationError):
        StrategyOperatorState(
            **_strategy_operator_state().model_dump(),
            account_id="must-not-persist",
        )

    database_path = tmp_path / "paper-state.sqlite3"
    state = _strategy_operator_state()
    with PaperStateStore(database_path) as store:
        store.save_strategy_operator_state(state)

    connection = sqlite3.connect(database_path)
    try:
        payload = json.loads(
            connection.execute(
                "SELECT state_json FROM strategy_operator_states"
            ).fetchone()[0]
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(strategy_operator_states)")
        }
    finally:
        connection.close()

    assert set(payload) == expected_fields
    assert not columns.intersection(
        {"api_key", "api_secret", "account_id", "credential", "access_token"}
    )


def test_corrupt_strategy_operator_state_row_fails_closed(tmp_path) -> None:
    database_path = tmp_path / "paper-state.sqlite3"
    state = _strategy_operator_state()
    with PaperStateStore(database_path) as store:
        store.save_strategy_operator_state(state)

    connection = sqlite3.connect(database_path)
    try:
        payload = state.model_dump(mode="json")
        payload["account_id"] = "injected-secret-field"
        connection.execute(
            """
            UPDATE strategy_operator_states
            SET state_json = ?
            WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ?
            """,
            (json.dumps(payload), *state.storage_key),
        )
        connection.commit()
    finally:
        connection.close()

    with PaperStateStore(database_path) as reopened:
        with pytest.raises(PaperStateCorruptionError, match="strategy-operator"):
            reopened.load_strategy_operator_state(*state.storage_key)
