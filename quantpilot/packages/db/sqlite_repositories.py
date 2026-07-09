"""Opt-in SQLite persistence for paper operator recovery state.

The fixture and mock repository registry remains in-memory.  This module opens
SQLite only when ``PaperStateStore`` is explicitly constructed.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    PaperRunCheckpoint,
)


class PaperStateError(RuntimeError):
    pass


class PaperStateConflictError(PaperStateError):
    pass


class PaperStateNotFoundError(PaperStateError):
    pass


class PaperStateCorruptionError(PaperStateError):
    pass


class PaperStateStore:
    """Narrow SQLite store for managed positions and run checkpoints."""

    def __init__(self, database_path: str | Path) -> None:
        target = str(database_path)
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
        self._initialize_schema()

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
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS managed_positions (
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (strategy_id, strategy_version, symbol)
                ) WITHOUT ROWID;

                CREATE TABLE IF NOT EXISTS paper_run_checkpoints (
                    run_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                ) WITHOUT ROWID;
                """
            )

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
    def _serialize(model: ManagedPositionState | PaperRunCheckpoint) -> str:
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
        metadata_key = row["strategy_id"], row["strategy_version"], row["symbol"]
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

    def save_position(self, position: ManagedPositionState) -> ManagedPositionState:
        """Insert or replace one natural-key position in a single transaction."""

        position = ManagedPositionState.model_validate(position.model_dump())
        with self._transaction():
            existing_row = self._connection.execute(
                """
                SELECT strategy_id, strategy_version, symbol, state_json
                FROM managed_positions
                WHERE strategy_id = ? AND strategy_version = ? AND symbol = ?
                """,
                position.storage_key,
            ).fetchone()
            if existing_row is not None:
                existing = self._decode_position(existing_row)
                if position.updated_at < existing.updated_at:
                    raise PaperStateConflictError(
                        "managed-position update is older than persisted state"
                    )

            self._connection.execute(
                """
                INSERT INTO managed_positions (
                    strategy_id, strategy_version, symbol, state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id, strategy_version, symbol) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (*position.storage_key, self._serialize(position), position.updated_at.isoformat()),
            )
        return position

    def load_position(
        self,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
    ) -> ManagedPositionState | None:
        row = self._connection.execute(
            """
            SELECT strategy_id, strategy_version, symbol, state_json
            FROM managed_positions
            WHERE strategy_id = ? AND strategy_version = ? AND symbol = ?
            """,
            (strategy_id.strip(), strategy_version.strip(), symbol.strip().upper()),
        ).fetchone()
        return None if row is None else self._decode_position(row)

    def list_positions(self) -> list[ManagedPositionState]:
        rows = self._connection.execute(
            """
            SELECT strategy_id, strategy_version, symbol, state_json
            FROM managed_positions
            ORDER BY strategy_id, strategy_version, symbol
            """
        ).fetchall()
        return [self._decode_position(row) for row in rows]

    def delete_position(self, strategy_id: str, strategy_version: str, symbol: str) -> bool:
        with self._transaction():
            cursor = self._connection.execute(
                """
                DELETE FROM managed_positions
                WHERE strategy_id = ? AND strategy_version = ? AND symbol = ?
                """,
                (strategy_id.strip(), strategy_version.strip(), symbol.strip().upper()),
            )
        return cursor.rowcount == 1

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
                and checkpoint.policy_version == existing.policy_version
                and checkpoint.data_mode == existing.data_mode
                and checkpoint.started_at == existing.started_at
            )
            if not immutable_identity:
                raise PaperStateConflictError("run checkpoint identity fields are immutable")
            if checkpoint.updated_at < existing.updated_at:
                raise PaperStateConflictError("run checkpoint update is older than persisted state")

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
