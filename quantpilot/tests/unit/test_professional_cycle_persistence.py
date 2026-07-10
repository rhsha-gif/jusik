from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timedelta, timezone
from math import inf

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    OperatorCycleClaim,
    PendingLiquidationCheckpoint,
    StrategyOperatorState,
)
from quantpilot.packages.core.schemas import Fill, ProcessedFillRecord
from quantpilot.packages.db.sqlite_repositories import (
    PaperStateConflictError,
    PaperStateMigrationRequired,
    PaperStateStore as RuntimePaperStateStore,
)


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)


class PaperStateStore(RuntimePaperStateStore):
    """Test-only store with explicit fixture-seeding capability."""

    def __init__(self, database_path: object) -> None:
        super().__init__(database_path, allow_fixture_seed=True)


def test_fill_evidence_rejects_non_finite_values() -> None:
    with pytest.raises(ValidationError):
        Fill(
            fill_id="fill-infinite",
            broker_order_id="broker-infinite",
            order_plan_id="oplan-infinite",
            symbol="005930",
            quantity=1,
            price=inf,
            notional=inf,
            filled_at=NOW,
        )


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
        "opened_at": NOW - timedelta(days=1),
        "updated_at": NOW,
        "reconciled_snapshot_id": "snapshot-001",
        "reconciled_at": NOW,
    }
    values.update(updates)
    return ManagedPositionState(**values)


def _state(**updates: object) -> StrategyOperatorState:
    values: dict[str, object] = {
        "policy_id": "policy-main",
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "health_status": "active",
        "reason_codes": ["healthy"],
        "retirement_phase": "none",
        "pending_order_plan_ids": [],
        "last_risk_evaluated_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return StrategyOperatorState(**values)


def _pending(**updates: object) -> PendingLiquidationCheckpoint:
    values: dict[str, object] = {
        "order_plan_id": "oplan-risk-001",
        "policy_id": "policy-main",
        "policy_version": 3,
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "symbol": "005930",
        "purpose": "protective_exit",
        "idempotency_key": "sha256:" + "a" * 64,
        "quantity_before": 10.0,
        "quantity_requested": 4.0,
        "expected_quantity_after": 6.0,
        "account_quantity_before": 10.0,
        "expected_account_quantity_after": 6.0,
        "limit_price": 99.0,
        "quote_as_of": NOW,
        "reconciled_snapshot_id": "snapshot-001",
        "status": "prepared",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return PendingLiquidationCheckpoint(**values)


def _claim(bucket: str, *, kind: str = "risk_evaluation") -> OperatorCycleClaim:
    return OperatorCycleClaim(
        policy_id="policy-main",
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        cycle_kind=kind,
        bucket=bucket,
        claimed_at=NOW,
        lease_expires_at=(
            NOW + timedelta(minutes=5)
            if kind == "weekly_rebalance"
            else None
        ),
    )


def test_pending_liquidation_survives_restart_and_cannot_be_reinserted(tmp_path) -> None:
    path = tmp_path / "paper-state.sqlite3"
    pending = _pending()
    with PaperStateStore(path) as store:
        store.insert_pending_liquidation(pending)

    with PaperStateStore(path) as reopened:
        assert reopened.load_pending_liquidation(pending.order_plan_id) == pending
        with pytest.raises(PaperStateConflictError, match="already exists"):
            reopened.insert_pending_liquidation(
                _pending(order_plan_id="oplan-risk-duplicate")
            )


def test_pending_liquidation_updates_with_monotonic_revision(tmp_path) -> None:
    pending = _pending()
    accepted = pending.model_copy(
        update={
            "status": "accepted",
            "broker_submission_attempted": True,
            "risk_check_id": "risk-final-001",
            "broker_order_id": "paper-order-001",
            "updated_at": NOW + timedelta(seconds=1),
            "revision": 1,
        }
    )
    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        store.insert_pending_liquidation(pending)
        store.update_pending_liquidation(accepted)
        assert store.list_pending_liquidations() == [accepted]


def test_pending_liquidation_fill_evidence_cannot_regress_or_diverge(tmp_path) -> None:
    pending = _pending()
    accepted = pending.model_copy(
        update={
            "status": "accepted",
            "broker_submission_attempted": True,
            "risk_check_id": "risk-final-001",
            "broker_order_id": "paper-order-001",
            "updated_at": NOW + timedelta(seconds=1),
            "revision": 1,
        }
    )
    fill = Fill(
        fill_id="fill-001",
        broker_order_id="paper-order-001",
        order_plan_id=pending.order_plan_id,
        symbol=pending.symbol,
        quantity=2,
        price=99,
        notional=198,
        filled_at=NOW + timedelta(seconds=1),
    )
    partial = PendingLiquidationCheckpoint.model_validate(
        accepted.model_copy(
            update={
                "status": "partially_filled",
                "cumulative_filled_quantity": 2,
                "fill_ids": [fill.fill_id],
                "fill_evidence": [fill],
                "updated_at": NOW + timedelta(seconds=2),
                "revision": 2,
            }
        ).model_dump()
    )
    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        store.insert_pending_liquidation(pending)
        store.update_pending_liquidation(accepted)
        swapped_fill = fill.model_copy(
            update={"broker_order_id": "paper-order-002"}
        )
        swapped_order = partial.model_copy(
            update={
                "broker_order_id": "paper-order-002",
                "fill_evidence": [swapped_fill],
            }
        )
        with pytest.raises(PaperStateConflictError, match="broker order ID is immutable"):
            store.update_pending_liquidation(swapped_order)
        store.update_pending_liquidation(partial)

        regressed = partial.model_copy(
            update={
                "status": "cancelled",
                "cumulative_filled_quantity": 0,
                "fill_ids": [],
                "fill_evidence": [],
                "updated_at": NOW + timedelta(seconds=3),
                "revision": 3,
            }
        )
        with pytest.raises(PaperStateConflictError, match="cannot decrease"):
            store.update_pending_liquidation(regressed)

        replacement_fill = fill.model_copy(
            update={"fill_id": "fill-002"}
        )
        replaced = partial.model_copy(
            update={
                "status": "cancelled",
                "fill_ids": [replacement_fill.fill_id],
                "fill_evidence": [replacement_fill],
                "updated_at": NOW + timedelta(seconds=3),
                "revision": 3,
            }
        )
        with pytest.raises(PaperStateConflictError, match="cannot be removed"):
            store.update_pending_liquidation(replaced)

        larger_fill = fill.model_copy(
            update={"quantity": 3, "notional": 297}
        )
        increased_without_new_id = partial.model_copy(
            update={
                "status": "cancelled",
                "cumulative_filled_quantity": 3,
                "fill_evidence": [larger_fill],
                "updated_at": NOW + timedelta(seconds=3),
                "revision": 3,
            }
        )
        with pytest.raises(PaperStateConflictError, match="evidence is immutable"):
            store.update_pending_liquidation(increased_without_new_id)

        repriced_fill = fill.model_copy(update={"price": 100, "notional": 200})
        mutated_evidence = partial.model_copy(
            update={
                "status": "cancelled",
                "fill_evidence": [repriced_fill],
                "updated_at": NOW + timedelta(seconds=3),
                "revision": 3,
            }
        )
        with pytest.raises(PaperStateConflictError, match="evidence is immutable"):
            store.update_pending_liquidation(mutated_evidence)


def test_cycle_claim_is_atomic_across_connections_and_allows_next_bucket(tmp_path) -> None:
    path = tmp_path / "paper-state.sqlite3"
    with PaperStateStore(path) as first, PaperStateStore(path) as second:
        assert first.claim_operator_cycle(_claim("2026-07-10T01:00Z"))
        assert not second.claim_operator_cycle(_claim("2026-07-10T01:00Z"))
        assert second.claim_operator_cycle(_claim("2026-07-10T01:01Z"))
        assert first.claim_operator_cycle(
            _claim("2026-W28", kind="weekly_rebalance")
        )
        assert not second.claim_operator_cycle(
            _claim("2026-W28", kind="weekly_rebalance")
        )
        assert second.claim_operator_cycle(
            _claim("2026-W29", kind="weekly_rebalance")
        )


def test_incomplete_weekly_claim_can_release_or_recover_after_lease(tmp_path) -> None:
    path = tmp_path / "paper-state.sqlite3"
    initial = _claim("2026-W28", kind="weekly_rebalance")
    before_expiry = initial.model_copy(
        update={
            "claimed_at": NOW + timedelta(minutes=4),
            "lease_expires_at": NOW + timedelta(minutes=9),
        }
    )
    recovered = initial.model_copy(
        update={
            "claimed_at": NOW + timedelta(minutes=5),
            "lease_expires_at": NOW + timedelta(minutes=10),
        }
    )
    with PaperStateStore(path) as first, PaperStateStore(path) as second:
        assert first.claim_operator_cycle(initial)
        assert not second.claim_operator_cycle(before_expiry)
        assert second.claim_operator_cycle(recovered)
        assert not first.release_operator_cycle_claim(initial)
        assert second.release_operator_cycle_claim(recovered)
        assert first.claim_operator_cycle(recovered)


def test_weekly_completion_is_fenced_to_exact_takeover_owner(tmp_path) -> None:
    path = tmp_path / "paper-state.sqlite3"
    initial = _claim("2026-W28", kind="weekly_rebalance")
    takeover = initial.model_copy(
        update={
            "claimed_at": initial.lease_expires_at,
            "lease_expires_at": initial.lease_expires_at + timedelta(minutes=5),
        }
    )
    assert takeover.claimed_at is not None
    with PaperStateStore(path) as first, PaperStateStore(path) as second:
        assert first.claim_operator_cycle(initial)
        assert second.claim_operator_cycle(takeover)
        with pytest.raises(
            PaperStateConflictError,
            match="ownership changed",
        ):
            first.complete_operator_cycle_claim(
                initial,
                completed_at=initial.lease_expires_at,
            )
        committed = second.complete_operator_cycle_claim(
            takeover,
            completed_at=takeover.claimed_at + timedelta(seconds=1),
        )
        assert committed.completed_at == takeover.claimed_at + timedelta(seconds=1)
        assert not first.release_operator_cycle_claim(initial)
        assert not second.claim_operator_cycle(
            takeover.model_copy(
                update={
                    "claimed_at": takeover.lease_expires_at,
                    "lease_expires_at": takeover.lease_expires_at
                    + timedelta(minutes=5),
                }
            )
        )


def test_weekly_claim_is_exclusive_across_strategy_versions(tmp_path) -> None:
    path = tmp_path / "paper-state.sqlite3"
    version_one = _claim("2026-W28", kind="weekly_rebalance")
    competing_version = version_one.model_copy(
        update={"strategy_version": "2.1"}
    )
    takeover = competing_version.model_copy(
        update={
            "claimed_at": version_one.lease_expires_at,
            "lease_expires_at": version_one.lease_expires_at
            + timedelta(minutes=5),
        }
    )
    with PaperStateStore(path) as first, PaperStateStore(path) as second:
        assert first.claim_operator_cycle(version_one)
        assert not second.claim_operator_cycle(competing_version)
        assert second.claim_operator_cycle(takeover)
        claims = second.list_operator_cycle_claims()

    assert claims == [takeover]


def test_weekly_portfolio_claim_is_exclusive_across_strategy_ids(tmp_path) -> None:
    path = tmp_path / "paper-state.sqlite3"
    first_strategy = _claim("2026-W28", kind="weekly_rebalance")
    competing_strategy = first_strategy.model_copy(
        update={
            "strategy_id": "replacement-strategy",
            "strategy_version": "1.0",
        }
    )
    with PaperStateStore(path) as first, PaperStateStore(path) as second:
        assert first.claim_operator_cycle(first_strategy)
        assert not second.claim_operator_cycle(competing_strategy)
        claims = second.list_operator_cycle_claims()

    assert claims == [first_strategy]


def test_atomic_safety_health_recovery_never_clears_existing_pause(tmp_path) -> None:
    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        failed = store.patch_operator_safety_state(
            policy_id="policy-main",
            autopilot_paused=True,
            broker_healthy=False,
            last_blocked_reason="broker_failure",
            set_last_blocked_reason=True,
            updated_at=NOW,
        )
        recovered_health = store.patch_operator_safety_state(
            policy_id="policy-main",
            broker_healthy=True,
            updated_at=NOW + timedelta(seconds=1),
        )

    assert failed.autopilot_paused is True
    assert failed.broker_healthy is False
    assert recovered_health.autopilot_paused is True
    assert recovered_health.broker_healthy is True
    assert recovered_health.last_blocked_reason == "broker_failure"


def test_equal_time_divergent_state_and_position_writes_are_rejected(tmp_path) -> None:
    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        state = _state()
        store.save_strategy_operator_state(state)
        with pytest.raises(PaperStateConflictError, match="same timestamp"):
            store.save_strategy_operator_state(
                state.model_copy(
                    update={"reason_codes": ["different"], "revision": 1}
                )
            )

        position = _position()
        store.seed_fixture_position(position, data_mode="fixture")
        with pytest.raises(PaperStateConflictError, match="same timestamp"):
            store.save_position(
                position.model_copy(update={"active_stop": 97.0, "revision": 1})
            )


def test_attribution_conflict_cannot_be_cleared_by_generic_position_write(tmp_path) -> None:
    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        position = _position()
        store.seed_fixture_position(position, data_mode="fixture")
        conflict_time = NOW + timedelta(seconds=1)
        conflicted = position.model_copy(
            update={
                "attribution_status": "conflicted",
                "attribution_conflict_reason": "managed_quantity_exceeds_account",
                "attribution_conflicted_at": conflict_time,
                "updated_at": conflict_time,
                "revision": 1,
            }
        )
        store.save_position(
            ManagedPositionState.model_validate(conflicted.model_dump())
        )
        reset_time = NOW + timedelta(seconds=2)
        generic_reset = conflicted.model_copy(
            update={
                "attribution_status": "active",
                "attribution_conflict_reason": None,
                "attribution_conflicted_at": None,
                "updated_at": reset_time,
                "reconciled_at": reset_time,
                "reconciled_snapshot_id": "snapshot-reset",
                "revision": 2,
            }
        )
        with pytest.raises(PaperStateConflictError, match="explicit audited reset"):
            store.save_position(
                ManagedPositionState.model_validate(generic_reset.model_dump())
            )


def test_generic_position_write_cannot_mutate_attribution_or_loosen_stop(tmp_path) -> None:
    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        position = _position()
        store.seed_fixture_position(position, data_mode="fixture")
        later = NOW + timedelta(seconds=1)
        with pytest.raises(PaperStateConflictError, match="atomic fill reconciliation"):
            store.save_position(
                position.model_copy(
                    update={
                        "quantity": 11,
                        "updated_at": later,
                        "reconciled_at": later,
                        "revision": 1,
                    }
                )
            )
        with pytest.raises(PaperStateConflictError, match="cannot be loosened"):
            store.save_position(
                position.model_copy(
                    update={
                        "active_stop": 1,
                        "updated_at": later,
                        "reconciled_at": later,
                        "revision": 1,
                    }
                )
            )
        with pytest.raises(PaperStateConflictError, match="cannot pre-seed"):
            store.seed_fixture_position(
                _position(symbol="000660", processed_fill_ids=["fill-forged"]),
                data_mode="fixture",
            )


def test_atomic_reconciliation_rejects_legacy_preseeded_fill_id(tmp_path) -> None:
    with PaperStateStore(tmp_path / "paper-state.sqlite3") as store:
        position = _position()
        store.seed_fixture_position(position, data_mode="fixture")
        legacy = position.model_copy(update={"processed_fill_ids": ["fill-legacy"]})
        store._connection.execute(
            """
            UPDATE managed_positions SET state_json = ?
            WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ? AND symbol = ?
            """,
            (
                json.dumps(legacy.model_dump(mode="json"), sort_keys=True),
                *legacy.storage_key,
            ),
        )
        store._connection.commit()
        loaded = store.load_position(*legacy.storage_key)
        assert loaded is not None
        record = ProcessedFillRecord(
            fill_id="fill-legacy",
            broker_order_id="broker-legacy",
            order_plan_id="oplan-legacy",
            policy_id=legacy.policy_id,
            policy_version=legacy.policy_version,
            user_id="fixture-user",
            strategy_id=legacy.strategy_id,
            strategy_version=legacy.strategy_version,
            symbol=legacy.symbol,
            side="sell",
            quantity=10,
            price=100,
            notional=1_000,
            filled_at=NOW,
            recorded_at=NOW,
        )
        with pytest.raises(PaperStateConflictError, match="missing from the global ledger"):
            store.apply_fill_reconciliation(
                records=[record],
                expected_position=loaded,
                next_position=loaded,
                reconciled_account_quantity=10,
            )


def test_inconsistent_strategy_state_fails_closed() -> None:
    with pytest.raises(ValidationError, match="later than updated_at"):
        _state(last_risk_evaluated_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="requires pending"):
        _state(retirement_phase="awaiting_reconciliation")
    with pytest.raises(ValidationError, match="require awaiting_reconciliation"):
        _state(
            retirement_phase="complete",
            pending_order_plan_ids=["oplan-risk-001"],
        )


def test_legacy_managed_position_database_with_rows_requires_explicit_reset(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE managed_positions (
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (strategy_id, strategy_version, symbol)
            ) WITHOUT ROWID
            """
        )
        connection.execute(
            "INSERT INTO managed_positions VALUES (?, ?, ?, ?, ?)",
            ("legacy", "1.0", "AAA", "{}", NOW.isoformat()),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperStateMigrationRequired, match="cannot be attributed"):
        PaperStateStore(path)
