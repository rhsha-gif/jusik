"""Opt-in SQLite persistence for paper operator recovery state.

The fixture and mock repository registry remains in-memory.  This module opens
SQLite only when ``PaperStateStore`` is explicitly constructed.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from math import isfinite
from pathlib import Path
import sqlite3
from typing import Iterator

from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    OperatorCycleClaim,
    OperatorSafetyState,
    PaperRunCheckpoint,
    PendingLiquidationCheckpoint,
    StrategyOperatorState,
)
from quantpilot.packages.core.schemas import ProcessedFillRecord


class PaperStateError(RuntimeError):
    pass


class PaperStateConflictError(PaperStateError):
    pass


class PaperStateNotFoundError(PaperStateError):
    pass


class PaperStateCorruptionError(PaperStateError):
    pass


class PaperStateMigrationRequired(PaperStateError):
    pass


PENDING_LIQUIDATION_TRANSITIONS: dict[str, set[str]] = {
    "prepared": {"submitted", "accepted", "filled", "failed", "outcome_unknown"},
    "submitted": {"accepted", "partially_filled", "filled", "failed", "outcome_unknown"},
    "accepted": {"partially_filled", "filled", "cancelled", "rejected", "failed", "outcome_unknown"},
    "partially_filled": {"filled", "cancelled", "failed", "outcome_unknown"},
    "outcome_unknown": {"accepted", "partially_filled", "filled", "cancelled", "rejected", "failed"},
    "filled": {"reconciled"},
    "cancelled": {"reconciled"},
    "rejected": {"reconciled"},
    "failed": {"reconciled"},
    "reconciled": set(),
}


class PaperStateStore:
    """Narrow SQLite store for managed positions and run checkpoints."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        allow_fixture_seed: bool = False,
    ) -> None:
        target = str(database_path)
        self._allow_fixture_seed = allow_fixture_seed
        if target != ":memory:":
            Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(target, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._closed = False
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA synchronous = FULL")
        if target != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        try:
            self._initialize_schema()
        except Exception:
            self.close()
            raise

    def __enter__(self) -> "PaperStateStore":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def _initialize_schema(self) -> None:
        with self._connection:
            existing_position_columns = {
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(managed_positions)"
                )
            }
            if existing_position_columns and "policy_id" not in existing_position_columns:
                row_count = self._connection.execute(
                    "SELECT COUNT(*) FROM managed_positions"
                ).fetchone()[0]
                if row_count:
                    raise PaperStateMigrationRequired(
                        "legacy managed-position rows cannot be attributed safely; "
                        "archive the fixture paper-state database and start a new paper session"
                    )
                self._connection.execute("DROP TABLE managed_positions")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS managed_positions (
                    policy_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (policy_id, strategy_id, strategy_version, symbol)
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS paper_run_checkpoints (
                    run_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS strategy_operator_states (
                    policy_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (policy_id, strategy_id, strategy_version)
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS pending_liquidations (
                    order_plan_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    policy_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS operator_cycle_claims (
                    policy_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    cycle_kind TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (
                        policy_id,
                        strategy_id,
                        strategy_version,
                        cycle_kind,
                        bucket
                    )
                ) WITHOUT ROWID;

                CREATE UNIQUE INDEX IF NOT EXISTS uq_weekly_policy_cycle
                ON operator_cycle_claims (
                    policy_id,
                    cycle_kind,
                    bucket
                )
                WHERE cycle_kind = 'weekly_rebalance';

                CREATE TABLE IF NOT EXISTS processed_fill_ledger (
                    fill_id TEXT PRIMARY KEY,
                    broker_order_id TEXT NOT NULL,
                    order_plan_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS operator_safety_states (
                    policy_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                ) WITHOUT ROWID;
                """
            )
            self._connection.execute("PRAGMA user_version = 5")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    @staticmethod
    def _serialize(
        model: (
            ManagedPositionState
            | PaperRunCheckpoint
            | StrategyOperatorState
            | PendingLiquidationCheckpoint
            | OperatorCycleClaim
            | OperatorSafetyState
            | ProcessedFillRecord
        ),
    ) -> str:
        return json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _decode_position(row: sqlite3.Row) -> ManagedPositionState:
        try:
            model = ManagedPositionState.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid managed-position JSON") from exc
        metadata_key = (
            row["policy_id"],
            row["strategy_id"],
            row["strategy_version"],
            row["symbol"],
        )
        if model.storage_key != metadata_key:
            raise PaperStateCorruptionError("managed-position identity does not match its key")
        return model

    @staticmethod
    def _decode_checkpoint(row: sqlite3.Row) -> PaperRunCheckpoint:
        try:
            model = PaperRunCheckpoint.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid paper-run checkpoint JSON") from exc
        if model.run_id != row["run_id"] or model.idempotency_key != row["idempotency_key"]:
            raise PaperStateCorruptionError("paper-run checkpoint identity does not match its key")
        return model

    @staticmethod
    def _decode_strategy_operator_state(row: sqlite3.Row) -> StrategyOperatorState:
        try:
            model = StrategyOperatorState.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid strategy-operator state JSON") from exc
        metadata_key = row["policy_id"], row["strategy_id"], row["strategy_version"]
        if model.storage_key != metadata_key:
            raise PaperStateCorruptionError(
                "strategy-operator state identity does not match its key"
            )
        return model

    @staticmethod
    def _decode_pending_liquidation(
        row: sqlite3.Row,
    ) -> PendingLiquidationCheckpoint:
        try:
            model = PendingLiquidationCheckpoint.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid pending-liquidation JSON") from exc
        metadata = (
            row["order_plan_id"],
            row["idempotency_key"],
            row["policy_id"],
            row["strategy_id"],
            row["strategy_version"],
            row["symbol"],
        )
        expected = (
            model.order_plan_id,
            model.idempotency_key,
            model.policy_id,
            model.strategy_id,
            model.strategy_version,
            model.symbol,
        )
        if metadata != expected:
            raise PaperStateCorruptionError(
                "pending-liquidation identity does not match its key"
            )
        return model

    @staticmethod
    def _decode_cycle_claim(row: sqlite3.Row) -> OperatorCycleClaim:
        try:
            model = OperatorCycleClaim.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid operator-cycle claim JSON") from exc
        metadata = (
            row["policy_id"],
            row["strategy_id"],
            row["strategy_version"],
            row["cycle_kind"],
            row["bucket"],
        )
        if model.storage_key != metadata:
            raise PaperStateCorruptionError(
                "operator-cycle claim identity does not match its key"
            )
        return model

    @staticmethod
    def _decode_processed_fill(row: sqlite3.Row) -> ProcessedFillRecord:
        try:
            model = ProcessedFillRecord.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid processed-fill JSON") from exc
        metadata = (
            row["fill_id"],
            row["broker_order_id"],
            row["order_plan_id"],
            row["policy_id"],
            row["user_id"],
            row["strategy_id"],
            row["strategy_version"],
            row["symbol"],
        )
        expected = (
            model.fill_id,
            model.broker_order_id,
            model.order_plan_id,
            model.policy_id,
            model.user_id,
            model.strategy_id,
            model.strategy_version,
            model.symbol,
        )
        if metadata != expected:
            raise PaperStateCorruptionError(
                "processed-fill identity does not match its key"
            )
        return model

    @staticmethod
    def _decode_operator_safety_state(row: sqlite3.Row) -> OperatorSafetyState:
        try:
            model = OperatorSafetyState.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid operator-safety JSON") from exc
        if model.policy_id != row["policy_id"]:
            raise PaperStateCorruptionError(
                "operator-safety identity does not match its key"
            )
        return model

    def save_operator_safety_state(
        self,
        state: OperatorSafetyState,
    ) -> OperatorSafetyState:
        state = OperatorSafetyState.model_validate(state.model_dump())
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT policy_id, state_json FROM operator_safety_states
                WHERE policy_id = ?
                """,
                (state.policy_id,),
            ).fetchone()
            if row is not None:
                existing = self._decode_operator_safety_state(row)
                if state == existing:
                    return existing
                if state.updated_at <= existing.updated_at:
                    raise PaperStateConflictError(
                        "operator-safety update must have a newer timestamp"
                    )
                if state.revision != existing.revision + 1:
                    raise PaperStateConflictError(
                        "operator-safety revision must advance by exactly one"
                    )
            elif state.revision != 0:
                raise PaperStateConflictError(
                    "new operator-safety revision must start at zero"
                )
            self._connection.execute(
                """
                INSERT INTO operator_safety_states (
                    policy_id, state_json, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(policy_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    state.policy_id,
                    self._serialize(state),
                    state.updated_at.isoformat(),
                ),
            )
        return state

    def patch_operator_safety_state(
        self,
        *,
        policy_id: str,
        autopilot_paused: bool | None = None,
        broker_healthy: bool | None = None,
        last_blocked_reason: str | None = None,
        set_last_blocked_reason: bool = False,
        require_healthy_broker: bool = False,
        updated_at: datetime,
    ) -> OperatorSafetyState:
        """Atomically merge independent safety fields under one write lock."""

        normalized_policy_id = policy_id.strip()
        if not normalized_policy_id:
            raise ValueError("operator safety policy_id must not be blank")
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT policy_id, state_json FROM operator_safety_states
                WHERE policy_id = ?
                """,
                (normalized_policy_id,),
            ).fetchone()
            existing = (
                None
                if row is None
                else self._decode_operator_safety_state(row)
            )
            current_paused = (
                False if existing is None else existing.autopilot_paused
            )
            current_healthy = (
                True if existing is None else existing.broker_healthy
            )
            current_reason = (
                None if existing is None else existing.last_blocked_reason
            )
            if require_healthy_broker and not current_healthy:
                raise PaperStateConflictError(
                    "broker health must recover before autopilot can resume"
                )
            next_paused = (
                current_paused
                if autopilot_paused is None
                else autopilot_paused
            )
            next_healthy = (
                current_healthy
                if broker_healthy is None
                else broker_healthy
            )
            next_reason = (
                last_blocked_reason
                if set_last_blocked_reason
                else current_reason
            )
            if (
                existing is not None
                and next_paused == current_paused
                and next_healthy == current_healthy
                and next_reason == current_reason
            ):
                return existing
            write_at = (
                updated_at
                if existing is None
                else max(
                    updated_at,
                    existing.updated_at + timedelta(microseconds=1),
                )
            )
            state = OperatorSafetyState(
                policy_id=normalized_policy_id,
                autopilot_paused=next_paused,
                broker_healthy=next_healthy,
                last_blocked_reason=next_reason,
                updated_at=write_at,
                revision=(0 if existing is None else existing.revision + 1),
            )
            self._connection.execute(
                """
                INSERT INTO operator_safety_states (
                    policy_id, state_json, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(policy_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    state.policy_id,
                    self._serialize(state),
                    state.updated_at.isoformat(),
                ),
            )
        return state

    def load_operator_safety_state(
        self,
        policy_id: str,
    ) -> OperatorSafetyState | None:
        row = self._connection.execute(
            """
            SELECT policy_id, state_json FROM operator_safety_states
            WHERE policy_id = ?
            """,
            (policy_id.strip(),),
        ).fetchone()
        return None if row is None else self._decode_operator_safety_state(row)

    def save_position(self, position: ManagedPositionState) -> ManagedPositionState:
        """Update operator metadata for an existing managed position."""

        position = ManagedPositionState.model_validate(position.model_dump())
        with self._transaction():
            existing_row = self._connection.execute(
                """
                SELECT policy_id, strategy_id, strategy_version, symbol, state_json
                FROM managed_positions
                WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ? AND symbol = ?
                """,
                position.storage_key,
            ).fetchone()
            if existing_row is not None:
                existing = self._decode_position(existing_row)
                if position == existing:
                    return existing
                immutable_attribution = (
                    position.policy_version == existing.policy_version
                    and position.quantity == existing.quantity
                    and position.average_entry_price == existing.average_entry_price
                    and position.atr14 == existing.atr14
                    and position.opened_at == existing.opened_at
                    and position.processed_fill_ids == existing.processed_fill_ids
                )
                if not immutable_attribution:
                    raise PaperStateConflictError(
                        "position attribution changes require atomic fill reconciliation"
                    )
                if position.active_stop + 0.000001 < existing.active_stop:
                    raise PaperStateConflictError(
                        "managed-position active stop cannot be loosened"
                    )
                if existing.attribution_status == "conflicted":
                    if position.attribution_status != "conflicted":
                        raise PaperStateConflictError(
                            "attribution conflict requires an explicit audited reset"
                        )
                    if (
                        position.attribution_conflict_reason
                        != existing.attribution_conflict_reason
                        or position.attribution_conflicted_at
                        != existing.attribution_conflicted_at
                    ):
                        raise PaperStateConflictError(
                            "attribution conflict evidence is immutable"
                        )
                if position.updated_at < existing.updated_at:
                    raise PaperStateConflictError(
                        "managed-position update is older than persisted state"
                    )
                if position.updated_at == existing.updated_at:
                    raise PaperStateConflictError(
                        "managed-position update has the same timestamp but different state"
                    )
                if position.revision != existing.revision + 1:
                    raise PaperStateConflictError(
                        "managed-position revision must advance by exactly one"
                    )
            else:
                raise PaperStateConflictError(
                    "new managed positions require atomic fill reconciliation"
                )

            self._connection.execute(
                """
                INSERT INTO managed_positions (
                    policy_id, strategy_id, strategy_version, symbol, state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id, strategy_id, strategy_version, symbol) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (*position.storage_key, self._serialize(position), position.updated_at.isoformat()),
            )
        return position

    def seed_fixture_position(
        self,
        position: ManagedPositionState,
        *,
        data_mode: str,
    ) -> ManagedPositionState:
        """Seed deterministic fixture state without opening a runtime insert path."""

        if not self._allow_fixture_seed:
            raise PaperStateConflictError(
                "fixture managed-position seeding is disabled for this store"
            )
        if data_mode != "fixture":
            raise PaperStateConflictError(
                "managed-position seeding is restricted to fixture data mode"
            )
        position = ManagedPositionState.model_validate(position.model_dump())
        if position.revision != 0:
            raise PaperStateConflictError(
                "fixture managed-position revision must start at zero"
            )
        if position.processed_fill_ids:
            raise PaperStateConflictError(
                "fixture managed positions cannot pre-seed processed fill IDs"
            )
        if position.attribution_status != "active":
            raise PaperStateConflictError(
                "fixture managed positions must start with active attribution"
            )
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO managed_positions (
                        policy_id, strategy_id, strategy_version, symbol,
                        state_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        *position.storage_key,
                        self._serialize(position),
                        position.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PaperStateConflictError(
                    "fixture managed position already exists"
                ) from exc
        return position

    def load_position(
        self,
        policy_id: str,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
    ) -> ManagedPositionState | None:
        row = self._connection.execute(
            """
            SELECT policy_id, strategy_id, strategy_version, symbol, state_json
            FROM managed_positions
            WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ? AND symbol = ?
            """,
            (
                policy_id.strip(),
                strategy_id.strip(),
                strategy_version.strip(),
                symbol.strip().upper(),
            ),
        ).fetchone()
        return None if row is None else self._decode_position(row)

    def list_positions(self) -> list[ManagedPositionState]:
        rows = self._connection.execute(
            """
            SELECT policy_id, strategy_id, strategy_version, symbol, state_json
            FROM managed_positions
            ORDER BY policy_id, strategy_id, strategy_version, symbol
            """
        ).fetchall()
        return [self._decode_position(row) for row in rows]

    def delete_position(
        self,
        policy_id: str,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
    ) -> bool:
        key = (
            policy_id.strip(),
            strategy_id.strip(),
            strategy_version.strip(),
            symbol.strip().upper(),
        )
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT policy_id, strategy_id, strategy_version, symbol, state_json
                FROM managed_positions
                WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ? AND symbol = ?
                """,
                key,
            ).fetchone()
            if row is None:
                return False
            self._decode_position(row)
            raise PaperStateConflictError(
                "managed positions may be deleted only by atomic fill reconciliation"
            )

    def load_processed_fill(self, fill_id: str) -> ProcessedFillRecord | None:
        row = self._connection.execute(
            """
            SELECT fill_id, broker_order_id, order_plan_id, policy_id, user_id,
                   strategy_id, strategy_version, symbol, state_json
            FROM processed_fill_ledger
            WHERE fill_id = ?
            """,
            (fill_id.strip(),),
        ).fetchone()
        return None if row is None else self._decode_processed_fill(row)

    def list_processed_fills(self) -> list[ProcessedFillRecord]:
        rows = self._connection.execute(
            """
            SELECT fill_id, broker_order_id, order_plan_id, policy_id, user_id,
                   strategy_id, strategy_version, symbol, state_json
            FROM processed_fill_ledger
            ORDER BY fill_id
            """
        ).fetchall()
        return [self._decode_processed_fill(row) for row in rows]

    def apply_fill_reconciliation(
        self,
        *,
        records: list[ProcessedFillRecord],
        expected_position: ManagedPositionState | None,
        next_position: ManagedPositionState | None,
        reconciled_account_quantity: float,
    ) -> ManagedPositionState | None:
        """Atomically claim fill IDs and compare-and-swap attributed position state."""

        normalized = [
            ProcessedFillRecord.model_validate(record.model_dump())
            for record in records
        ]
        if not normalized:
            raise PaperStateConflictError("fill reconciliation requires durable records")
        if (
            not isfinite(reconciled_account_quantity)
            or reconciled_account_quantity < 0
        ):
            raise PaperStateConflictError(
                "fill reconciliation requires finite account quantity evidence"
            )
        fill_ids = [record.fill_id for record in normalized]
        if len(fill_ids) != len(set(fill_ids)):
            raise PaperStateConflictError("fill reconciliation contains duplicate fill IDs")

        first = normalized[0]
        group_identity = (
            first.policy_id,
            first.policy_version,
            first.user_id,
            first.strategy_id,
            first.strategy_version,
            first.symbol,
            first.side,
            first.order_plan_id,
            first.broker_order_id,
        )
        for record in normalized[1:]:
            if (
                record.policy_id,
                record.policy_version,
                record.user_id,
                record.strategy_id,
                record.strategy_version,
                record.symbol,
                record.side,
                record.order_plan_id,
                record.broker_order_id,
            ) != group_identity:
                raise PaperStateConflictError(
                    "fill reconciliation records must share one attributed order scope"
                )

        position_key = (
            first.policy_id,
            first.strategy_id,
            first.strategy_version,
            first.symbol,
        )
        if expected_position is not None:
            expected_position = ManagedPositionState.model_validate(
                expected_position.model_dump()
            )
            if expected_position.storage_key != position_key:
                raise PaperStateConflictError(
                    "expected position does not match processed-fill scope"
                )
        if next_position is not None:
            next_position = ManagedPositionState.model_validate(
                next_position.model_dump()
            )
            if next_position.storage_key != position_key:
                raise PaperStateConflictError(
                    "next position does not match processed-fill scope"
                )
            if (
                first.side == "buy"
                and next_position.policy_version != first.policy_version
            ):
                raise PaperStateConflictError(
                    "buy fill policy version must match the next position"
                )
            if (
                first.side == "sell"
                and expected_position is not None
                and next_position.policy_version != expected_position.policy_version
            ):
                raise PaperStateConflictError(
                    "sell reconciliation cannot change position policy version"
                )

        with self._transaction():
            position_row = self._connection.execute(
                """
                SELECT policy_id, strategy_id, strategy_version, symbol, state_json
                FROM managed_positions
                WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ? AND symbol = ?
                """,
                position_key,
            ).fetchone()
            current_position = (
                None if position_row is None else self._decode_position(position_row)
            )

            existing_records: dict[str, ProcessedFillRecord] = {}
            for fill_id in fill_ids:
                row = self._connection.execute(
                    """
                    SELECT fill_id, broker_order_id, order_plan_id, policy_id, user_id,
                           strategy_id, strategy_version, symbol, state_json
                    FROM processed_fill_ledger
                    WHERE fill_id = ?
                    """,
                    (fill_id,),
                ).fetchone()
                if row is not None:
                    existing_records[fill_id] = self._decode_processed_fill(row)

            if existing_records:
                if len(existing_records) != len(normalized):
                    raise PaperStateConflictError(
                        "fill replay mixes processed and new fill IDs"
                    )
                expected_records = {record.fill_id: record for record in normalized}
                if existing_records != expected_records:
                    raise PaperStateConflictError(
                        "processed fill ID was reused with different attribution"
                    )
                return current_position

            if (
                expected_position is not None
                and expected_position.attribution_status == "conflicted"
            ):
                raise PaperStateConflictError(
                    "conflicted attribution cannot accept new fill reconciliation"
                )
            if first.side == "sell" and expected_position is None:
                raise PaperStateConflictError(
                    "new sell fills require an attributed managed position"
                )
            if current_position != expected_position:
                raise PaperStateConflictError(
                    "managed position changed before fill reconciliation"
                )

            incoming_ids = set(fill_ids)
            prior_ids = set(
                expected_position.processed_fill_ids
                if expected_position is not None
                else []
            )
            if incoming_ids.intersection(prior_ids):
                raise PaperStateConflictError(
                    "position claims processed fills missing from the global ledger"
                )
            total_quantity = sum(record.quantity for record in normalized)
            previous_quantity = (
                0.0 if expected_position is None else expected_position.quantity
            )
            resulting_quantity = (
                previous_quantity + total_quantity
                if first.side == "buy"
                else previous_quantity - total_quantity
            )
            if resulting_quantity < -0.000001:
                raise PaperStateConflictError(
                    "sell fills exceed the attributed managed quantity"
                )
            if resulting_quantity <= 0.000001:
                if next_position is not None:
                    raise PaperStateConflictError(
                        "fully closed fills must delete the managed position"
                    )
            elif next_position is None or abs(
                next_position.quantity - resulting_quantity
            ) > 0.000001:
                raise PaperStateConflictError(
                    "next managed quantity does not match reconciled fills"
                )

            if next_position is not None and set(
                next_position.processed_fill_ids
            ) != prior_ids.union(incoming_ids):
                raise PaperStateConflictError(
                    "next position processed fill IDs must exactly match reconciliation"
                )
            if next_position is not None:
                expected_revision = (
                    0 if expected_position is None else expected_position.revision + 1
                )
                if next_position.revision != expected_revision:
                    raise PaperStateConflictError(
                        "reconciled position revision must advance by exactly one"
                    )
                if (
                    expected_position is not None
                    and next_position.updated_at <= expected_position.updated_at
                ):
                    raise PaperStateConflictError(
                        "reconciled position timestamp must advance"
                    )

            sibling_rows = self._connection.execute(
                """
                SELECT policy_id, strategy_id, strategy_version, symbol, state_json
                FROM managed_positions
                WHERE policy_id = ? AND symbol = ?
                """,
                (first.policy_id, first.symbol),
            ).fetchall()
            sibling_positions = [
                self._decode_position(row) for row in sibling_rows
            ]
            if any(
                item.storage_key != position_key
                and item.attribution_status == "conflicted"
                for item in sibling_positions
            ):
                raise PaperStateConflictError(
                    "related attribution conflict requires explicit resolution"
                )
            aggregate_before = sum(item.quantity for item in sibling_positions)
            aggregate_after = (
                aggregate_before
                - (current_position.quantity if current_position is not None else 0.0)
                + max(0.0, resulting_quantity)
            )
            if aggregate_after > reconciled_account_quantity + 0.000001:
                raise PaperStateConflictError(
                    "aggregate attributed quantity exceeds reconciled account quantity"
                )

            try:
                for record in normalized:
                    self._connection.execute(
                        """
                        INSERT INTO processed_fill_ledger (
                            fill_id, broker_order_id, order_plan_id, policy_id, user_id,
                            strategy_id, strategy_version, symbol, state_json, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.fill_id,
                            record.broker_order_id,
                            record.order_plan_id,
                            record.policy_id,
                            record.user_id,
                            record.strategy_id,
                            record.strategy_version,
                            record.symbol,
                            self._serialize(record),
                            record.recorded_at.isoformat(),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise PaperStateConflictError(
                    "processed fill ID already exists"
                ) from exc

            if next_position is None:
                cursor = self._connection.execute(
                    """
                    DELETE FROM managed_positions
                    WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ? AND symbol = ?
                    """,
                    position_key,
                )
                if expected_position is not None and cursor.rowcount != 1:
                    raise PaperStateConflictError(
                        "managed position disappeared during fill reconciliation"
                    )
            else:
                if expected_position is None:
                    self._connection.execute(
                        """
                        INSERT INTO managed_positions (
                            policy_id, strategy_id, strategy_version, symbol,
                            state_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            *next_position.storage_key,
                            self._serialize(next_position),
                            next_position.updated_at.isoformat(),
                        ),
                    )
                else:
                    cursor = self._connection.execute(
                        """
                        UPDATE managed_positions
                        SET state_json = ?, updated_at = ?
                        WHERE policy_id = ? AND strategy_id = ?
                          AND strategy_version = ? AND symbol = ?
                        """,
                        (
                            self._serialize(next_position),
                            next_position.updated_at.isoformat(),
                            *next_position.storage_key,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise PaperStateConflictError(
                            "managed position disappeared during fill reconciliation"
                        )
        return next_position

    def insert_run_checkpoint(self, checkpoint: PaperRunCheckpoint) -> PaperRunCheckpoint:
        """Claim a run and idempotency key; neither identity may be reused."""

        checkpoint = PaperRunCheckpoint.model_validate(checkpoint.model_dump())
        with self._transaction():
            if self._connection.execute(
                "SELECT 1 FROM paper_run_checkpoints WHERE run_id = ?",
                (checkpoint.run_id,),
            ).fetchone():
                raise PaperStateConflictError("run_id already exists")
            if self._connection.execute(
                "SELECT 1 FROM paper_run_checkpoints WHERE idempotency_key = ?",
                (checkpoint.idempotency_key,),
            ).fetchone():
                raise PaperStateConflictError("idempotency key already exists")
            try:
                self._connection.execute(
                    """
                    INSERT INTO paper_run_checkpoints (
                        run_id, idempotency_key, state_json, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        checkpoint.run_id,
                        checkpoint.idempotency_key,
                        self._serialize(checkpoint),
                        checkpoint.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PaperStateConflictError(
                    "run_id or idempotency key already exists"
                ) from exc
        return checkpoint

    def update_run_checkpoint(self, checkpoint: PaperRunCheckpoint) -> PaperRunCheckpoint:
        """Advance mutable run state while preserving its claimed identity."""

        checkpoint = PaperRunCheckpoint.model_validate(checkpoint.model_dump())
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT run_id, idempotency_key, state_json
                FROM paper_run_checkpoints
                WHERE run_id = ?
                """,
                (checkpoint.run_id,),
            ).fetchone()
            if row is None:
                raise PaperStateNotFoundError(f"missing run checkpoint: {checkpoint.run_id}")
            existing = self._decode_checkpoint(row)
            immutable_identity = (
                checkpoint.idempotency_key == existing.idempotency_key
                and checkpoint.policy_id == existing.policy_id
                and checkpoint.user_id == existing.user_id
                and checkpoint.policy_version == existing.policy_version
                and checkpoint.run_mode == existing.run_mode
                and checkpoint.requested_at == existing.requested_at
                and checkpoint.request_fingerprint
                == existing.request_fingerprint
                and checkpoint.data_mode == existing.data_mode
                and checkpoint.started_at == existing.started_at
            )
            if not immutable_identity:
                raise PaperStateConflictError("run checkpoint identity fields are immutable")
            if checkpoint == existing:
                return existing
            if existing.status != "started" or checkpoint.status == "started":
                raise PaperStateConflictError(
                    "run checkpoint may advance exactly once from started"
                )
            if checkpoint.updated_at <= existing.updated_at:
                raise PaperStateConflictError(
                    "run checkpoint update must have a newer timestamp"
                )

            self._connection.execute(
                """
                UPDATE paper_run_checkpoints
                SET state_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    self._serialize(checkpoint),
                    checkpoint.updated_at.isoformat(),
                    checkpoint.run_id,
                ),
            )
        return checkpoint

    def load_run_checkpoint(self, run_id: str) -> PaperRunCheckpoint | None:
        row = self._connection.execute(
            """
            SELECT run_id, idempotency_key, state_json
            FROM paper_run_checkpoints
            WHERE run_id = ?
            """,
            (run_id.strip(),),
        ).fetchone()
        return None if row is None else self._decode_checkpoint(row)

    def find_run_checkpoint_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> PaperRunCheckpoint | None:
        row = self._connection.execute(
            """
            SELECT run_id, idempotency_key, state_json
            FROM paper_run_checkpoints
            WHERE idempotency_key = ?
            """,
            (idempotency_key.strip(),),
        ).fetchone()
        return None if row is None else self._decode_checkpoint(row)

    def save_strategy_operator_state(
        self,
        state: StrategyOperatorState,
    ) -> StrategyOperatorState:
        """Upsert one natural-key strategy state unless the write is stale."""

        state = StrategyOperatorState.model_validate(state.model_dump())
        with self._transaction():
            existing_row = self._connection.execute(
                """
                SELECT policy_id, strategy_id, strategy_version, state_json
                FROM strategy_operator_states
                WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ?
                """,
                state.storage_key,
            ).fetchone()
            if existing_row is not None:
                existing = self._decode_strategy_operator_state(existing_row)
                if state == existing:
                    return existing
                if state.updated_at < existing.updated_at:
                    raise PaperStateConflictError(
                        "strategy-operator state update is older than persisted state"
                    )
                if state.updated_at == existing.updated_at:
                    raise PaperStateConflictError(
                        "strategy-operator state has the same timestamp but different state"
                    )
                if state.revision != existing.revision + 1:
                    raise PaperStateConflictError(
                        "strategy-operator revision must advance by exactly one"
                    )
            elif state.revision != 0:
                raise PaperStateConflictError(
                    "new strategy-operator revision must start at zero"
                )

            self._connection.execute(
                """
                INSERT INTO strategy_operator_states (
                    policy_id, strategy_id, strategy_version, state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(policy_id, strategy_id, strategy_version) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (*state.storage_key, self._serialize(state), state.updated_at.isoformat()),
            )
        return state

    def load_strategy_operator_state(
        self,
        policy_id: str,
        strategy_id: str,
        strategy_version: str,
    ) -> StrategyOperatorState | None:
        row = self._connection.execute(
            """
            SELECT policy_id, strategy_id, strategy_version, state_json
            FROM strategy_operator_states
            WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ?
            """,
            (policy_id.strip(), strategy_id.strip(), strategy_version.strip()),
        ).fetchone()
        return None if row is None else self._decode_strategy_operator_state(row)

    def list_strategy_operator_states(self) -> list[StrategyOperatorState]:
        rows = self._connection.execute(
            """
            SELECT policy_id, strategy_id, strategy_version, state_json
            FROM strategy_operator_states
            ORDER BY policy_id, strategy_id, strategy_version
            """
        ).fetchall()
        return [self._decode_strategy_operator_state(row) for row in rows]

    def insert_pending_liquidation(
        self,
        checkpoint: PendingLiquidationCheckpoint,
    ) -> PendingLiquidationCheckpoint:
        """Persist the reservation before the first broker submission attempt."""

        checkpoint = PendingLiquidationCheckpoint.model_validate(
            checkpoint.model_dump()
        )
        if checkpoint.revision != 0 or checkpoint.status != "prepared":
            raise PaperStateConflictError(
                "new pending liquidation must start prepared at revision zero"
            )
        with self._transaction():
            try:
                self._connection.execute(
                    """
                    INSERT INTO pending_liquidations (
                        order_plan_id,
                        idempotency_key,
                        policy_id,
                        strategy_id,
                        strategy_version,
                        symbol,
                        state_json,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        checkpoint.order_plan_id,
                        checkpoint.idempotency_key,
                        checkpoint.policy_id,
                        checkpoint.strategy_id,
                        checkpoint.strategy_version,
                        checkpoint.symbol,
                        self._serialize(checkpoint),
                        checkpoint.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PaperStateConflictError(
                    "pending liquidation order or idempotency key already exists"
                ) from exc
        return checkpoint

    def update_pending_liquidation(
        self,
        checkpoint: PendingLiquidationCheckpoint,
    ) -> PendingLiquidationCheckpoint:
        checkpoint = PendingLiquidationCheckpoint.model_validate(
            checkpoint.model_dump()
        )
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT order_plan_id, idempotency_key, policy_id, strategy_id,
                       strategy_version, symbol, state_json
                FROM pending_liquidations
                WHERE order_plan_id = ?
                """,
                (checkpoint.order_plan_id,),
            ).fetchone()
            if row is None:
                raise PaperStateNotFoundError(
                    f"missing pending liquidation: {checkpoint.order_plan_id}"
                )
            existing = self._decode_pending_liquidation(row)
            immutable_identity = (
                checkpoint.idempotency_key == existing.idempotency_key
                and checkpoint.policy_id == existing.policy_id
                and checkpoint.policy_version == existing.policy_version
                and checkpoint.strategy_id == existing.strategy_id
                and checkpoint.strategy_version == existing.strategy_version
                and checkpoint.symbol == existing.symbol
                and checkpoint.purpose == existing.purpose
                and checkpoint.quantity_before == existing.quantity_before
                and checkpoint.quantity_requested == existing.quantity_requested
                and checkpoint.expected_quantity_after
                == existing.expected_quantity_after
                and checkpoint.account_quantity_before
                == existing.account_quantity_before
                and checkpoint.expected_account_quantity_after
                == existing.expected_account_quantity_after
                and checkpoint.limit_price == existing.limit_price
                and checkpoint.quote_as_of == existing.quote_as_of
                and checkpoint.reconciled_snapshot_id
                == existing.reconciled_snapshot_id
                and checkpoint.created_at == existing.created_at
            )
            if not immutable_identity:
                raise PaperStateConflictError(
                    "pending liquidation recovery identity is immutable"
                )
            if checkpoint == existing:
                return existing
            if (
                existing.broker_order_id is not None
                and checkpoint.broker_order_id != existing.broker_order_id
            ):
                raise PaperStateConflictError(
                    "pending liquidation broker order ID is immutable once assigned"
                )
            if (
                existing.risk_check_id is not None
                and checkpoint.risk_check_id != existing.risk_check_id
            ):
                raise PaperStateConflictError(
                    "pending liquidation final risk check ID is immutable once assigned"
                )
            if (
                existing.risk_check_id is None
                and checkpoint.risk_check_id is not None
                and not checkpoint.broker_submission_attempted
            ):
                raise PaperStateConflictError(
                    "final risk check ID may bind only to a broker submission attempt"
                )
            if (
                existing.broker_submission_attempted
                and not checkpoint.broker_submission_attempted
            ):
                raise PaperStateConflictError(
                    "pending liquidation submission evidence cannot be removed"
                )
            existing_fill_ids = set(existing.fill_ids)
            next_fill_ids = set(checkpoint.fill_ids)
            existing_evidence = {
                fill.fill_id: fill for fill in existing.fill_evidence
            }
            next_evidence = {
                fill.fill_id: fill for fill in checkpoint.fill_evidence
            }
            if checkpoint.cumulative_filled_quantity < (
                existing.cumulative_filled_quantity - 0.000001
            ):
                raise PaperStateConflictError(
                    "pending liquidation cumulative fills cannot decrease"
                )
            if not existing_fill_ids.issubset(next_fill_ids):
                raise PaperStateConflictError(
                    "pending liquidation fill IDs cannot be removed"
                )
            if any(
                next_evidence[fill_id] != evidence
                for fill_id, evidence in existing_evidence.items()
            ):
                raise PaperStateConflictError(
                    "pending liquidation fill evidence is immutable"
                )
            quantity_increased = checkpoint.cumulative_filled_quantity > (
                existing.cumulative_filled_quantity + 0.000001
            )
            added_fill_ids = next_fill_ids - existing_fill_ids
            fill_ids_added = bool(added_fill_ids)
            if quantity_increased != fill_ids_added:
                raise PaperStateConflictError(
                    "new fill IDs and cumulative fill quantity must advance together"
                )
            quantity_delta = (
                checkpoint.cumulative_filled_quantity
                - existing.cumulative_filled_quantity
            )
            added_evidence_quantity = sum(
                next_evidence[fill_id].quantity for fill_id in added_fill_ids
            )
            if abs(quantity_delta - added_evidence_quantity) > 0.000001:
                raise PaperStateConflictError(
                    "cumulative fill increase must equal newly added fill evidence"
                )
            if checkpoint.updated_at <= existing.updated_at:
                raise PaperStateConflictError(
                    "pending liquidation update must have a newer timestamp"
                )
            if checkpoint.revision != existing.revision + 1:
                raise PaperStateConflictError(
                    "pending liquidation revision must advance by exactly one"
                )
            if checkpoint.status not in PENDING_LIQUIDATION_TRANSITIONS[existing.status]:
                raise PaperStateConflictError(
                    f"invalid pending liquidation transition: {existing.status} -> {checkpoint.status}"
                )
            self._connection.execute(
                """
                UPDATE pending_liquidations
                SET state_json = ?, updated_at = ?
                WHERE order_plan_id = ?
                """,
                (
                    self._serialize(checkpoint),
                    checkpoint.updated_at.isoformat(),
                    checkpoint.order_plan_id,
                ),
            )
        return checkpoint

    def load_pending_liquidation(
        self,
        order_plan_id: str,
    ) -> PendingLiquidationCheckpoint | None:
        row = self._connection.execute(
            """
            SELECT order_plan_id, idempotency_key, policy_id, strategy_id,
                   strategy_version, symbol, state_json
            FROM pending_liquidations
            WHERE order_plan_id = ?
            """,
            (order_plan_id.strip(),),
        ).fetchone()
        return None if row is None else self._decode_pending_liquidation(row)

    def list_pending_liquidations(
        self,
        *,
        include_reconciled: bool = False,
    ) -> list[PendingLiquidationCheckpoint]:
        rows = self._connection.execute(
            """
            SELECT order_plan_id, idempotency_key, policy_id, strategy_id,
                   strategy_version, symbol, state_json
            FROM pending_liquidations
            ORDER BY policy_id, strategy_id, strategy_version, symbol, order_plan_id
            """
        ).fetchall()
        checkpoints = [self._decode_pending_liquidation(row) for row in rows]
        if include_reconciled:
            return checkpoints
        return [item for item in checkpoints if item.status != "reconciled"]

    def claim_operator_cycle(self, claim: OperatorCycleClaim) -> bool:
        """Atomically acquire a minute bucket or a renewable weekly lease."""

        claim = OperatorCycleClaim.model_validate(claim.model_dump())
        with self._transaction():
            if claim.cycle_kind == "weekly_rebalance":
                existing_row = self._connection.execute(
                    """
                    SELECT policy_id, strategy_id, strategy_version, cycle_kind,
                           bucket, state_json
                    FROM operator_cycle_claims
                    WHERE policy_id = ? AND cycle_kind = ? AND bucket = ?
                    """,
                    (
                        claim.policy_id,
                        claim.cycle_kind,
                        claim.bucket,
                    ),
                ).fetchone()
            else:
                existing_row = self._connection.execute(
                    """
                    SELECT policy_id, strategy_id, strategy_version, cycle_kind,
                           bucket, state_json
                    FROM operator_cycle_claims
                    WHERE policy_id = ? AND strategy_id = ?
                      AND strategy_version = ? AND cycle_kind = ? AND bucket = ?
                    """,
                    claim.storage_key,
                ).fetchone()
            if existing_row is None:
                self._connection.execute(
                    """
                    INSERT INTO operator_cycle_claims (
                        policy_id,
                        strategy_id,
                        strategy_version,
                        cycle_kind,
                        bucket,
                        state_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (*claim.storage_key, self._serialize(claim)),
                )
                return True
            existing = self._decode_cycle_claim(existing_row)
            if claim.cycle_kind != "weekly_rebalance":
                return False
            if existing.completed_at is not None:
                return False
            state_row = self._connection.execute(
                """
                SELECT policy_id, strategy_id, strategy_version, state_json
                FROM strategy_operator_states
                WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ?
                """,
                claim.storage_key[:3],
            ).fetchone()
            state = (
                None
                if state_row is None
                else self._decode_strategy_operator_state(state_row)
            )
            if state is not None and state.last_rebalance_session == claim.bucket:
                return False
            if (
                existing.lease_expires_at is None
                or existing.lease_expires_at > claim.claimed_at
            ):
                return False
            self._connection.execute(
                """
                DELETE FROM operator_cycle_claims
                WHERE policy_id = ? AND strategy_id = ?
                  AND strategy_version = ? AND cycle_kind = ? AND bucket = ?
                """,
                existing.storage_key,
            )
            self._connection.execute(
                """
                INSERT INTO operator_cycle_claims (
                    policy_id,
                    strategy_id,
                    strategy_version,
                    cycle_kind,
                    bucket,
                    state_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (*claim.storage_key, self._serialize(claim)),
            )
        return True

    def release_operator_cycle_claim(self, claim: OperatorCycleClaim) -> bool:
        """Release only the caller's incomplete weekly lease."""

        claim = OperatorCycleClaim.model_validate(claim.model_dump())
        if claim.cycle_kind != "weekly_rebalance":
            raise PaperStateConflictError("risk cycle claims cannot be released")
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT policy_id, strategy_id, strategy_version, cycle_kind,
                       bucket, state_json
                FROM operator_cycle_claims
                WHERE policy_id = ? AND strategy_id = ?
                  AND strategy_version = ? AND cycle_kind = ? AND bucket = ?
                """,
                claim.storage_key,
            ).fetchone()
            if row is None or self._decode_cycle_claim(row) != claim:
                return False
            if claim.completed_at is not None:
                return False
            state_row = self._connection.execute(
                """
                SELECT policy_id, strategy_id, strategy_version, state_json
                FROM strategy_operator_states
                WHERE policy_id = ? AND strategy_id = ? AND strategy_version = ?
                """,
                claim.storage_key[:3],
            ).fetchone()
            if state_row is not None:
                state = self._decode_strategy_operator_state(state_row)
                if state.last_rebalance_session == claim.bucket:
                    return False
            cursor = self._connection.execute(
                """
                DELETE FROM operator_cycle_claims
                WHERE policy_id = ? AND strategy_id = ?
                  AND strategy_version = ? AND cycle_kind = ? AND bucket = ?
                """,
                claim.storage_key,
            )
        return cursor.rowcount == 1

    def complete_operator_cycle_claim(
        self,
        claim: OperatorCycleClaim,
        *,
        completed_at: datetime,
    ) -> OperatorCycleClaim:
        """Fence weekly completion to the exact live lease owner."""

        claim = OperatorCycleClaim.model_validate(claim.model_dump())
        if claim.cycle_kind != "weekly_rebalance" or claim.completed_at is not None:
            raise PaperStateConflictError(
                "only an active weekly lease can be completed"
            )
        completed = OperatorCycleClaim.model_validate(
            claim.model_copy(update={"completed_at": completed_at}).model_dump()
        )
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT policy_id, strategy_id, strategy_version, cycle_kind,
                       bucket, state_json
                FROM operator_cycle_claims
                WHERE policy_id = ? AND strategy_id = ?
                  AND strategy_version = ? AND cycle_kind = ? AND bucket = ?
                """,
                claim.storage_key,
            ).fetchone()
            if row is None or self._decode_cycle_claim(row) != claim:
                raise PaperStateConflictError(
                    "weekly rebalance lease ownership changed before completion"
                )
            self._connection.execute(
                """
                UPDATE operator_cycle_claims SET state_json = ?
                WHERE policy_id = ? AND strategy_id = ?
                  AND strategy_version = ? AND cycle_kind = ? AND bucket = ?
                """,
                (self._serialize(completed), *claim.storage_key),
            )
        return completed

    def list_operator_cycle_claims(self) -> list[OperatorCycleClaim]:
        rows = self._connection.execute(
            """
            SELECT policy_id, strategy_id, strategy_version, cycle_kind,
                   bucket, state_json
            FROM operator_cycle_claims
            ORDER BY policy_id, strategy_id, strategy_version, cycle_kind, bucket
            """
        ).fetchall()
        return [self._decode_cycle_claim(row) for row in rows]
