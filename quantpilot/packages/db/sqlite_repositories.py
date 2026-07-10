"""Opt-in SQLite persistence for paper operator recovery state.

The fixture and mock repository registry remains in-memory.  This module opens
SQLite only when ``PaperStateStore`` is explicitly constructed.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
from math import isfinite
from pathlib import Path
import sqlite3
from typing import Iterator

from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    OperatorCycleClaim,
    OperatorSafetyState,
    PaperExecutionSession,
    PaperOrderDispatch,
    PaperPortfolioLossBaseline,
    PaperRunCheckpoint,
    PendingLiquidationCheckpoint,
    StateStoreProvenance,
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


class PaperStateProvenanceError(PaperStateError):
    pass


PAPER_STATE_SCHEMA_VERSION = 8
PAPER_STATE_PREVIOUS_SCHEMA_VERSION = 7
PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS = frozenset({6, 7})

PAPER_DISPATCH_TRANSITIONS: dict[str, set[str]] = {
    "prepared": {"expired_pre_dispatch", "failed_pre_dispatch"},
    "dispatch_claimed": {
        "outcome_unknown",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
    },
    "outcome_unknown": {
        "outcome_unknown",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
        "cancelled",
    },
    "accepted": {"accepted", "partially_filled", "filled", "rejected", "cancelled"},
    "partially_filled": {"partially_filled", "filled", "cancelled"},
    "filled": {"filled"},
    "rejected": {"rejected"},
    "cancelled": {"cancelled"},
    "expired_pre_dispatch": {"expired_pre_dispatch"},
    "failed_pre_dispatch": {"failed_pre_dispatch"},
}


def _require_aware_timestamp(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value


PENDING_LIQUIDATION_TRANSITIONS: dict[str, set[str]] = {
    "prepared": {"submitted", "accepted", "filled", "failed", "outcome_unknown"},
    "submitted": {"accepted", "partially_filled", "filled", "cancelled", "rejected", "failed", "outcome_unknown"},
    "accepted": {"accepted", "partially_filled", "filled", "cancelled", "rejected", "failed", "outcome_unknown"},
    "partially_filled": {"partially_filled", "filled", "cancelled", "failed", "outcome_unknown"},
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
        data_mode: str = "fixture",
        broker_environment: str | None = None,
        account_scope_fingerprint: str | None = None,
    ) -> None:
        selected_environment = broker_environment or (
            "fixture_mock" if data_mode == "fixture" else "kis_paper"
        )
        try:
            requested = StateStoreProvenance(
                store_id="requested-store-binding",
                schema_version=PAPER_STATE_SCHEMA_VERSION,
                data_mode=data_mode,  # type: ignore[arg-type]
                broker_environment=selected_environment,  # type: ignore[arg-type]
                account_scope_fingerprint=account_scope_fingerprint,
                created_at=datetime.now(timezone.utc),
            )
        except ValueError as exc:
            raise PaperStateProvenanceError(
                "invalid paper-state store provenance"
            ) from exc
        if allow_fixture_seed and requested.data_mode != "fixture":
            raise PaperStateProvenanceError(
                "fixture seeding cannot be enabled for a paper-bound store"
            )
        target = str(database_path)
        self._allow_fixture_seed = allow_fixture_seed
        self._requested_provenance = requested
        self._provenance: StateStoreProvenance | None = None
        if target != ":memory:":
            Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(target, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._closed = False
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA synchronous = FULL")
        try:
            self._initialize_schema()
            if target != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
        except Exception:
            self.close()
            raise

    @property
    def provenance(self) -> StateStoreProvenance:
        if self._provenance is None:
            raise PaperStateCorruptionError("paper-state provenance is unavailable")
        return StateStoreProvenance.model_validate(self._provenance.model_dump())

    def __enter__(self) -> "PaperStateStore":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def _initialize_schema(self) -> None:
        user_version = int(
            self._connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if user_version > PAPER_STATE_SCHEMA_VERSION:
            raise PaperStateMigrationRequired(
                "paper-state database was created by a newer schema version"
            )
        table_names = {
            row[0]
            for row in self._connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        persisted: StateStoreProvenance | None = None
        if "state_store_metadata" in table_names:
            rows = self._connection.execute(
                """
                SELECT singleton_id, store_id, schema_version, data_mode,
                       broker_environment, account_scope_fingerprint,
                       state_json, created_at
                FROM state_store_metadata
                """
            ).fetchall()
            if len(rows) != 1:
                raise PaperStateCorruptionError(
                    "paper-state database must contain exactly one provenance row"
                )
            persisted = self._decode_store_provenance(rows[0])
            requested = self._requested_provenance
            if (
                persisted.data_mode != requested.data_mode
                or persisted.broker_environment != requested.broker_environment
                or persisted.account_scope_fingerprint
                != requested.account_scope_fingerprint
            ):
                raise PaperStateProvenanceError(
                    "paper-state database provenance does not match the requested mode, environment, or account"
                )
            if persisted.schema_version not in {
                *PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS,
                PAPER_STATE_SCHEMA_VERSION,
            }:
                raise PaperStateMigrationRequired(
                    "paper-state metadata schema version requires an explicit migration"
                )
            if user_version != persisted.schema_version:
                raise PaperStateCorruptionError(
                    "paper-state PRAGMA and metadata schema versions disagree"
                )
        elif self._requested_provenance.data_mode == "paper_trading":
            populated_tables: list[str] = []
            for table_name in sorted(table_names):
                quoted = table_name.replace('"', '""')
                if self._connection.execute(
                    f'SELECT 1 FROM "{quoted}" LIMIT 1'
                ).fetchone() is not None:
                    populated_tables.append(table_name)
            if populated_tables:
                raise PaperStateMigrationRequired(
                    "a populated legacy state database cannot be promoted to KIS paper mode; archive it and create a new paper database"
                )

        with self._transaction():
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
            schema_statements = [
                """
                CREATE TABLE IF NOT EXISTS state_store_metadata (
                    singleton_id INTEGER NOT NULL PRIMARY KEY CHECK (singleton_id = 1),
                    store_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL,
                    data_mode TEXT NOT NULL,
                    broker_environment TEXT NOT NULL,
                    account_scope_fingerprint TEXT,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                ) WITHOUT ROWID
                """,
                """
                CREATE TABLE IF NOT EXISTS managed_positions (
                    policy_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (policy_id, strategy_id, strategy_version, symbol)
                ) WITHOUT ROWID
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_run_checkpoints (
                    run_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                ) WITHOUT ROWID
                """,
                """
                CREATE TABLE IF NOT EXISTS strategy_operator_states (
                    policy_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (policy_id, strategy_id, strategy_version)
                ) WITHOUT ROWID
                """,
                """
                CREATE TABLE IF NOT EXISTS pending_liquidations (
                    order_plan_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    policy_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                ) WITHOUT ROWID
                """,
                """
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
                ) WITHOUT ROWID
                """,
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_weekly_policy_cycle
                ON operator_cycle_claims (
                    policy_id,
                    cycle_kind,
                    bucket
                )
                WHERE cycle_kind = 'weekly_rebalance'
                """,
                """
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
                ) WITHOUT ROWID
                """,
                """
                CREATE TABLE IF NOT EXISTS operator_safety_states (
                    policy_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                ) WITHOUT ROWID
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_execution_sessions (
                    session_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (session_id, store_id, fencing_token),
                    UNIQUE (store_id, fencing_token),
                    FOREIGN KEY (store_id) REFERENCES state_store_metadata(store_id)
                ) WITHOUT ROWID
                """,
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_paper_execution_session
                ON paper_execution_sessions (store_id)
                WHERE status = 'active'
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_order_dispatches (
                    order_plan_id TEXT PRIMARY KEY,
                    broker_order_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    store_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id, store_id, fencing_token)
                        REFERENCES paper_execution_sessions(
                            session_id, store_id, fencing_token
                        )
                ) WITHOUT ROWID
                """,
                """
                CREATE INDEX IF NOT EXISTS ix_paper_dispatch_status
                ON paper_order_dispatches (store_id, status, updated_at)
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_portfolio_loss_baselines (
                    store_id TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    account_scope_fingerprint TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY (store_id, business_date),
                    FOREIGN KEY (store_id) REFERENCES state_store_metadata(store_id)
                ) WITHOUT ROWID
                """,
            ]
            for statement in schema_statements:
                self._connection.execute(statement)

            if persisted is None:
                requested = self._requested_provenance
                persisted = StateStoreProvenance(
                    schema_version=PAPER_STATE_SCHEMA_VERSION,
                    data_mode=requested.data_mode,
                    broker_environment=requested.broker_environment,
                    account_scope_fingerprint=requested.account_scope_fingerprint,
                    created_at=datetime.now(timezone.utc),
                )
                self._connection.execute(
                    """
                    INSERT INTO state_store_metadata (
                        singleton_id, store_id, schema_version, data_mode,
                        broker_environment, account_scope_fingerprint,
                        state_json, created_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        persisted.store_id,
                        persisted.schema_version,
                        persisted.data_mode,
                        persisted.broker_environment,
                        persisted.account_scope_fingerprint,
                        self._serialize(persisted),
                        persisted.created_at.isoformat(),
                    ),
                )
            elif persisted.schema_version in PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS:
                previous = persisted
                persisted = StateStoreProvenance.model_validate(
                    previous.model_copy(
                        update={"schema_version": PAPER_STATE_SCHEMA_VERSION}
                    ).model_dump()
                )
                cursor = self._connection.execute(
                    """
                    UPDATE state_store_metadata
                    SET schema_version = ?, state_json = ?
                    WHERE singleton_id = 1 AND store_id = ?
                      AND schema_version = ? AND state_json = ?
                    """,
                    (
                        persisted.schema_version,
                        self._serialize(persisted),
                        previous.store_id,
                        previous.schema_version,
                        self._serialize(previous),
                    ),
                )
                if cursor.rowcount != 1:
                    raise PaperStateConflictError(
                        "paper-state provenance changed during schema migration"
                    )
            self._connection.execute(
                f"PRAGMA user_version = {PAPER_STATE_SCHEMA_VERSION}"
            )
        self._provenance = persisted

    @staticmethod
    def _decode_store_provenance(row: sqlite3.Row) -> StateStoreProvenance:
        try:
            model = StateStoreProvenance.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError(
                "invalid paper-state provenance JSON"
            ) from exc
        metadata = (
            row["singleton_id"],
            row["store_id"],
            row["schema_version"],
            row["data_mode"],
            row["broker_environment"],
            row["account_scope_fingerprint"],
            row["created_at"],
        )
        expected = (
            1,
            model.store_id,
            model.schema_version,
            model.data_mode,
            model.broker_environment,
            model.account_scope_fingerprint,
            model.created_at.isoformat(),
        )
        if metadata != expected:
            raise PaperStateCorruptionError(
                "paper-state provenance does not match its metadata columns"
            )
        return model

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
            | StateStoreProvenance
            | PaperExecutionSession
            | PaperOrderDispatch
            | PaperPortfolioLossBaseline
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

    def _decode_checkpoint(self, row: sqlite3.Row) -> PaperRunCheckpoint:
        try:
            model = PaperRunCheckpoint.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid paper-run checkpoint JSON") from exc
        if model.run_id != row["run_id"] or model.idempotency_key != row["idempotency_key"]:
            raise PaperStateCorruptionError("paper-run checkpoint identity does not match its key")
        if model.data_mode != self.provenance.data_mode:
            raise PaperStateCorruptionError(
                "paper-run checkpoint data mode does not match its state store"
            )
        return model

    @staticmethod
    def _decode_paper_execution_session(
        row: sqlite3.Row,
    ) -> PaperExecutionSession:
        try:
            model = PaperExecutionSession.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError(
                "invalid paper-execution session JSON"
            ) from exc
        metadata = (
            row["session_id"],
            row["store_id"],
            row["fencing_token"],
            row["status"],
            row["updated_at"],
        )
        expected = (
            model.session_id,
            model.store_id,
            model.fencing_token,
            model.status,
            model.updated_at.isoformat(),
        )
        if metadata != expected:
            raise PaperStateCorruptionError(
                "paper-execution session identity does not match its metadata"
            )
        return model

    @staticmethod
    def _decode_paper_order_dispatch(row: sqlite3.Row) -> PaperOrderDispatch:
        try:
            model = PaperOrderDispatch.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError(
                "invalid paper-order dispatch JSON"
            ) from exc
        metadata = (
            row["order_plan_id"],
            row["broker_order_id"],
            row["idempotency_key"],
            row["store_id"],
            row["session_id"],
            row["fencing_token"],
            row["status"],
            row["revision"],
            row["updated_at"],
        )
        expected = (
            model.order_plan_id,
            model.broker_order_id,
            model.idempotency_key,
            model.store_id,
            model.session_id,
            model.fencing_token,
            model.status,
            model.revision,
            model.updated_at.isoformat(),
        )
        if metadata != expected:
            raise PaperStateCorruptionError(
                "paper-order dispatch identity does not match its metadata"
            )
        return model

    @staticmethod
    def _decode_paper_portfolio_loss_baseline(
        row: sqlite3.Row,
    ) -> PaperPortfolioLossBaseline:
        try:
            model = PaperPortfolioLossBaseline.model_validate_json(
                row["state_json"]
            )
        except ValueError as exc:
            raise PaperStateCorruptionError(
                "invalid paper portfolio loss-baseline JSON"
            ) from exc
        metadata = (
            row["store_id"],
            row["business_date"],
            row["account_scope_fingerprint"],
            row["captured_at"],
        )
        expected = (
            model.store_id,
            model.business_date.isoformat(),
            model.account_scope_fingerprint,
            model.captured_at.isoformat(),
        )
        if metadata != expected:
            raise PaperStateCorruptionError(
                "paper portfolio loss baseline does not match its metadata"
            )
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

    def _require_paper_store(self) -> StateStoreProvenance:
        provenance = self.provenance
        if (
            provenance.data_mode != "paper_trading"
            or provenance.broker_environment != "kis_paper"
            or provenance.account_scope_fingerprint is None
        ):
            raise PaperStateProvenanceError(
                "paper dispatch requires a KIS-paper-bound state store"
            )
        return provenance

    def _validate_loss_baseline_provenance(
        self,
        baseline: PaperPortfolioLossBaseline,
    ) -> None:
        provenance = self._require_paper_store()
        if (
            baseline.store_id != provenance.store_id
            or baseline.data_mode != provenance.data_mode
            or baseline.broker_environment != provenance.broker_environment
            or baseline.account_scope_fingerprint
            != provenance.account_scope_fingerprint
        ):
            raise PaperStateProvenanceError(
                "paper loss baseline does not match its state-store provenance"
            )

    def insert_paper_portfolio_loss_baseline(
        self,
        baseline: PaperPortfolioLossBaseline,
    ) -> PaperPortfolioLossBaseline:
        """Persist one immutable, explicitly sourced daily loss baseline."""

        baseline = PaperPortfolioLossBaseline.model_validate(
            baseline.model_dump()
        )
        self._validate_loss_baseline_provenance(baseline)
        with self._transaction():
            existing_row = self._connection.execute(
                """
                SELECT store_id, business_date, account_scope_fingerprint,
                       state_json, captured_at
                FROM paper_portfolio_loss_baselines
                WHERE store_id = ? AND business_date = ?
                """,
                (baseline.store_id, baseline.business_date.isoformat()),
            ).fetchone()
            if existing_row is not None:
                existing = self._decode_paper_portfolio_loss_baseline(
                    existing_row
                )
                if existing == baseline:
                    return existing
                raise PaperStateConflictError(
                    "paper loss baseline already exists with different evidence"
                )

            if baseline.source == "prior_session_close":
                source_row = self._connection.execute(
                    """
                    SELECT store_id, business_date, account_scope_fingerprint,
                           state_json, captured_at
                    FROM paper_portfolio_loss_baselines
                    WHERE store_id = ? AND business_date = ?
                    """,
                    (
                        baseline.store_id,
                        baseline.source_business_date.isoformat(),
                    ),
                ).fetchone()
                if source_row is None:
                    raise PaperStateConflictError(
                        "prior-session loss baseline requires its durable source day"
                    )
                source = self._decode_paper_portfolio_loss_baseline(source_row)
                self._validate_loss_baseline_provenance(source)
                if source.month_key != baseline.month_key:
                    raise PaperStateConflictError(
                        "month rollover requires manual loss-baseline confirmation"
                    )
                if source.month_start_equity != baseline.month_start_equity:
                    raise PaperStateConflictError(
                        "prior-session baseline cannot change month-start equity"
                    )

            try:
                self._connection.execute(
                    """
                    INSERT INTO paper_portfolio_loss_baselines (
                        store_id, business_date, account_scope_fingerprint,
                        state_json, captured_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        baseline.store_id,
                        baseline.business_date.isoformat(),
                        baseline.account_scope_fingerprint,
                        self._serialize(baseline),
                        baseline.captured_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PaperStateConflictError(
                    "paper loss baseline already exists"
                ) from exc
        return baseline

    def load_paper_portfolio_loss_baseline(
        self,
        business_date: date,
    ) -> PaperPortfolioLossBaseline | None:
        provenance = self._require_paper_store()
        row = self._connection.execute(
            """
            SELECT store_id, business_date, account_scope_fingerprint,
                   state_json, captured_at
            FROM paper_portfolio_loss_baselines
            WHERE store_id = ? AND business_date = ?
            """,
            (provenance.store_id, business_date.isoformat()),
        ).fetchone()
        if row is None:
            return None
        baseline = self._decode_paper_portfolio_loss_baseline(row)
        self._validate_loss_baseline_provenance(baseline)
        return baseline

    def list_paper_portfolio_loss_baselines(
        self,
    ) -> list[PaperPortfolioLossBaseline]:
        provenance = self._require_paper_store()
        rows = self._connection.execute(
            """
            SELECT store_id, business_date, account_scope_fingerprint,
                   state_json, captured_at
            FROM paper_portfolio_loss_baselines
            WHERE store_id = ?
            ORDER BY business_date
            """,
            (provenance.store_id,),
        ).fetchall()
        baselines = [
            self._decode_paper_portfolio_loss_baseline(row) for row in rows
        ]
        for baseline in baselines:
            self._validate_loss_baseline_provenance(baseline)
        return baselines

    def _validate_session_provenance(
        self,
        session: PaperExecutionSession,
    ) -> None:
        provenance = self._require_paper_store()
        if (
            session.store_id != provenance.store_id
            or session.data_mode != provenance.data_mode
            or session.broker_environment != provenance.broker_environment
            or session.account_scope_fingerprint
            != provenance.account_scope_fingerprint
        ):
            raise PaperStateProvenanceError(
                "paper session does not match its state-store provenance"
            )

    def _validate_dispatch_provenance(self, dispatch: PaperOrderDispatch) -> None:
        provenance = self._require_paper_store()
        if (
            dispatch.store_id != provenance.store_id
            or dispatch.data_mode != provenance.data_mode
            or dispatch.broker_environment != provenance.broker_environment
            or dispatch.account_scope_fingerprint
            != provenance.account_scope_fingerprint
        ):
            raise PaperStateProvenanceError(
                "paper dispatch does not match its state-store provenance"
            )

    def _load_session_row(self, session_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT session_id, store_id, fencing_token, status, state_json, updated_at
            FROM paper_execution_sessions
            WHERE session_id = ?
            """,
            (session_id.strip(),),
        ).fetchone()

    def _require_exact_active_session(
        self,
        session: PaperExecutionSession,
        *,
        checked_at: datetime,
    ) -> PaperExecutionSession:
        session = PaperExecutionSession.model_validate(session.model_dump())
        self._validate_session_provenance(session)
        row = self._load_session_row(session.session_id)
        if row is None:
            raise PaperStateConflictError("paper execution session does not exist")
        current = self._decode_paper_execution_session(row)
        if current != session:
            raise PaperStateConflictError(
                "paper execution session fencing ownership changed"
            )
        if current.status != "active" or current.lease_expires_at <= checked_at:
            raise PaperStateConflictError(
                "paper execution session lease is not active"
            )
        return current

    def start_paper_execution_session(
        self,
        *,
        started_at: datetime,
        lease_expires_at: datetime,
        session_id: str | None = None,
    ) -> PaperExecutionSession:
        """Start one fenced paper session, abandoning only an expired predecessor."""

        _require_aware_timestamp(started_at, field_name="started_at")
        _require_aware_timestamp(
            lease_expires_at,
            field_name="lease_expires_at",
        )
        provenance = self._require_paper_store()
        with self._transaction():
            active_row = self._connection.execute(
                """
                SELECT session_id, store_id, fencing_token, status, state_json, updated_at
                FROM paper_execution_sessions
                WHERE store_id = ? AND status = 'active'
                """,
                (provenance.store_id,),
            ).fetchone()
            if active_row is not None:
                active = self._decode_paper_execution_session(active_row)
                if active.lease_expires_at > started_at:
                    raise PaperStateConflictError(
                        "an unexpired paper execution session already owns the store"
                    )
                abandoned = PaperExecutionSession.model_validate(
                    active.model_copy(
                        update={
                            "status": "abandoned",
                            "updated_at": started_at,
                            "ended_at": started_at,
                            "revision": active.revision + 1,
                        }
                    ).model_dump()
                )
                cursor = self._connection.execute(
                    """
                    UPDATE paper_execution_sessions
                    SET status = ?, state_json = ?, updated_at = ?
                    WHERE session_id = ? AND status = 'active' AND fencing_token = ?
                    """,
                    (
                        abandoned.status,
                        self._serialize(abandoned),
                        abandoned.updated_at.isoformat(),
                        active.session_id,
                        active.fencing_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PaperStateConflictError(
                        "paper execution session changed during lease takeover"
                    )
            token = int(
                self._connection.execute(
                    """
                    SELECT COALESCE(MAX(fencing_token), 0)
                    FROM paper_execution_sessions
                    WHERE store_id = ?
                    """,
                    (provenance.store_id,),
                ).fetchone()[0]
            ) + 1
            values: dict[str, object] = {
                "store_id": provenance.store_id,
                "account_scope_fingerprint": provenance.account_scope_fingerprint,
                "fencing_token": token,
                "started_at": started_at,
                "lease_expires_at": lease_expires_at,
                "updated_at": started_at,
            }
            if session_id is not None:
                values["session_id"] = session_id
            session = PaperExecutionSession(**values)
            self._connection.execute(
                """
                INSERT INTO paper_execution_sessions (
                    session_id, store_id, fencing_token, status,
                    state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.store_id,
                    session.fencing_token,
                    session.status,
                    self._serialize(session),
                    session.updated_at.isoformat(),
                ),
            )
        return session

    def load_paper_execution_session(
        self,
        session_id: str,
    ) -> PaperExecutionSession | None:
        row = self._load_session_row(session_id)
        if row is None:
            return None
        session = self._decode_paper_execution_session(row)
        self._validate_session_provenance(session)
        return session

    def list_paper_execution_sessions(self) -> list[PaperExecutionSession]:
        rows = self._connection.execute(
            """
            SELECT session_id, store_id, fencing_token, status, state_json, updated_at
            FROM paper_execution_sessions
            ORDER BY fencing_token, session_id
            """
        ).fetchall()
        sessions = [self._decode_paper_execution_session(row) for row in rows]
        for session in sessions:
            self._validate_session_provenance(session)
        return sessions

    def renew_paper_execution_session(
        self,
        session: PaperExecutionSession,
        *,
        renewed_at: datetime,
        lease_expires_at: datetime,
    ) -> PaperExecutionSession:
        """Extend only the exact unexpired session owner; expired leases stay dead."""

        _require_aware_timestamp(renewed_at, field_name="renewed_at")
        _require_aware_timestamp(
            lease_expires_at,
            field_name="lease_expires_at",
        )
        session = PaperExecutionSession.model_validate(session.model_dump())
        self._validate_session_provenance(session)
        with self._transaction():
            current = self._require_exact_active_session(
                session,
                checked_at=renewed_at,
            )
            if renewed_at <= current.updated_at:
                raise PaperStateConflictError(
                    "paper-session renewal timestamp must advance"
                )
            if lease_expires_at <= current.lease_expires_at:
                raise PaperStateConflictError(
                    "paper-session renewal must extend the lease"
                )
            renewed = PaperExecutionSession.model_validate(
                current.model_copy(
                    update={
                        "lease_expires_at": lease_expires_at,
                        "updated_at": renewed_at,
                        "revision": current.revision + 1,
                    }
                ).model_dump()
            )
            cursor = self._connection.execute(
                """
                UPDATE paper_execution_sessions
                SET state_json = ?, updated_at = ?
                WHERE session_id = ? AND status = 'active'
                  AND fencing_token = ? AND state_json = ?
                """,
                (
                    self._serialize(renewed),
                    renewed.updated_at.isoformat(),
                    current.session_id,
                    current.fencing_token,
                    self._serialize(current),
                ),
            )
            if cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "paper execution session changed before lease renewal"
                )
        return renewed

    def close_paper_execution_session(
        self,
        session: PaperExecutionSession,
        *,
        closed_at: datetime,
    ) -> PaperExecutionSession:
        _require_aware_timestamp(closed_at, field_name="closed_at")
        session = PaperExecutionSession.model_validate(session.model_dump())
        self._validate_session_provenance(session)
        with self._transaction():
            current = self._require_exact_active_session(
                session,
                checked_at=closed_at,
            )
            if closed_at <= current.updated_at:
                raise PaperStateConflictError(
                    "paper-session close timestamp must advance"
                )
            closed = PaperExecutionSession.model_validate(
                current.model_copy(
                    update={
                        "status": "closed",
                        "updated_at": closed_at,
                        "ended_at": closed_at,
                        "revision": current.revision + 1,
                    }
                ).model_dump()
            )
            cursor = self._connection.execute(
                """
                UPDATE paper_execution_sessions
                SET status = ?, state_json = ?, updated_at = ?
                WHERE session_id = ? AND status = 'active' AND fencing_token = ?
                """,
                (
                    closed.status,
                    self._serialize(closed),
                    closed.updated_at.isoformat(),
                    current.session_id,
                    current.fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "paper execution session changed before close"
                )
        return closed

    def _load_dispatch_row(self, order_plan_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT order_plan_id, broker_order_id, idempotency_key, store_id, session_id,
                   fencing_token, status, revision, state_json, updated_at
            FROM paper_order_dispatches
            WHERE order_plan_id = ?
            """,
            (order_plan_id.strip(),),
        ).fetchone()

    def insert_paper_order_dispatch(
        self,
        dispatch: PaperOrderDispatch,
    ) -> PaperOrderDispatch:
        """Prepare one exact journal row without granting POST authority."""

        dispatch = PaperOrderDispatch.model_validate(dispatch.model_dump())
        self._validate_dispatch_provenance(dispatch)
        if (
            dispatch.status != "prepared"
            or dispatch.revision != 0
            or dispatch.attempt_count != 0
        ):
            raise PaperStateConflictError(
                "new paper dispatches must start prepared at revision zero"
            )
        with self._transaction():
            session_row = self._load_session_row(dispatch.session_id)
            if session_row is None:
                raise PaperStateConflictError(
                    "paper dispatch requires a persisted execution session"
                )
            session = self._decode_paper_execution_session(session_row)
            self._require_exact_active_session(
                session,
                checked_at=dispatch.prepared_at,
            )
            if (
                dispatch.store_id != session.store_id
                or dispatch.fencing_token != session.fencing_token
                or dispatch.account_scope_fingerprint
                != session.account_scope_fingerprint
            ):
                raise PaperStateProvenanceError(
                    "paper dispatch does not match its execution session"
                )
            existing_row = self._connection.execute(
                """
                SELECT order_plan_id, broker_order_id, idempotency_key, store_id, session_id,
                       fencing_token, status, revision, state_json, updated_at
                FROM paper_order_dispatches
                WHERE order_plan_id = ? OR idempotency_key = ?
                """,
                (dispatch.order_plan_id, dispatch.idempotency_key),
            ).fetchone()
            if existing_row is not None:
                existing = self._decode_paper_order_dispatch(existing_row)
                if existing == dispatch:
                    return existing
                raise PaperStateConflictError(
                    "paper dispatch identity is already bound to different evidence"
                )
            try:
                self._connection.execute(
                    """
                    INSERT INTO paper_order_dispatches (
                        order_plan_id, broker_order_id, idempotency_key, store_id, session_id,
                        fencing_token, status, revision, state_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dispatch.order_plan_id,
                        dispatch.broker_order_id,
                        dispatch.idempotency_key,
                        dispatch.store_id,
                        dispatch.session_id,
                        dispatch.fencing_token,
                        dispatch.status,
                        dispatch.revision,
                        self._serialize(dispatch),
                        dispatch.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PaperStateConflictError(
                    "paper dispatch order or idempotency key already exists"
                ) from exc
        return dispatch

    def claim_dispatch_attempt(
        self,
        order_plan_id: str,
        *,
        session: PaperExecutionSession,
        claimed_at: datetime,
    ) -> PaperOrderDispatch:
        """CAS prepared to dispatch_claimed; commit must precede any external POST."""

        _require_aware_timestamp(claimed_at, field_name="claimed_at")
        session = PaperExecutionSession.model_validate(session.model_dump())
        with self._transaction():
            current_session = self._require_exact_active_session(
                session,
                checked_at=claimed_at,
            )
            row = self._load_dispatch_row(order_plan_id)
            if row is None:
                raise PaperStateNotFoundError(
                    f"missing paper dispatch: {order_plan_id.strip()}"
                )
            current = self._decode_paper_order_dispatch(row)
            self._validate_dispatch_provenance(current)
            if (
                current.session_id != current_session.session_id
                or current.fencing_token != current_session.fencing_token
            ):
                raise PaperStateConflictError(
                    "paper dispatch belongs to a different session fence"
                )
            if current.status != "prepared" or current.attempt_count != 0:
                raise PaperStateConflictError(
                    "paper dispatch has already claimed its only external attempt"
                )
            if current.submission_evidence_expires_at <= claimed_at:
                raise PaperStateConflictError(
                    "paper dispatch submission evidence expired before claim"
                )
            if claimed_at <= current.updated_at:
                raise PaperStateConflictError(
                    "dispatch claim timestamp must advance durable state"
                )
            unresolved_rows = self._connection.execute(
                """
                SELECT order_plan_id, broker_order_id, idempotency_key, store_id,
                       session_id, fencing_token, status, revision, state_json,
                       updated_at
                FROM paper_order_dispatches
                WHERE store_id = ? AND order_plan_id <> ?
                  AND status IN ('dispatch_claimed', 'outcome_unknown')
                ORDER BY order_plan_id
                """,
                (current.store_id, current.order_plan_id),
            ).fetchall()
            unresolved = [
                self._decode_paper_order_dispatch(row) for row in unresolved_rows
            ]
            for item in unresolved:
                self._validate_dispatch_provenance(item)
            risk_reducing_sell = (
                current.side == "sell"
                and current.purpose
                in {"protective_exit", "strategy_retirement"}
                and current.quantity
                <= current.snapshot_symbol_orderable_quantity + 0.000001
            )
            if unresolved and (
                any(item.side == "sell" for item in unresolved)
                or not risk_reducing_sell
            ):
                raise PaperStateConflictError(
                    "an unresolved paper dispatch blocks new external attempts"
                )
            claimed = PaperOrderDispatch.model_validate(
                current.model_copy(
                    update={
                        "status": "dispatch_claimed",
                        "attempt_count": 1,
                        "dispatch_claimed_at": claimed_at,
                        "updated_at": claimed_at,
                        "revision": current.revision + 1,
                    }
                ).model_dump()
            )
            cursor = self._connection.execute(
                """
                UPDATE paper_order_dispatches
                SET status = ?, revision = ?, state_json = ?, updated_at = ?
                WHERE order_plan_id = ? AND status = 'prepared'
                  AND revision = ?
                """,
                (
                    claimed.status,
                    claimed.revision,
                    self._serialize(claimed),
                    claimed.updated_at.isoformat(),
                    current.order_plan_id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "paper dispatch claim lost its compare-and-swap race"
                )
        return claimed

    def takeover_prepared_paper_order_dispatch(
        self,
        order_plan_id: str,
        *,
        session: PaperExecutionSession,
        taken_over_at: datetime,
    ) -> PaperOrderDispatch:
        """Rebind only an unattempted prepared row from an expired predecessor."""

        _require_aware_timestamp(taken_over_at, field_name="taken_over_at")
        session = PaperExecutionSession.model_validate(session.model_dump())
        with self._transaction():
            successor = self._require_exact_active_session(
                session,
                checked_at=taken_over_at,
            )
            row = self._load_dispatch_row(order_plan_id)
            if row is None:
                raise PaperStateNotFoundError(
                    f"missing paper dispatch: {order_plan_id.strip()}"
                )
            current = self._decode_paper_order_dispatch(row)
            self._validate_dispatch_provenance(current)
            if current.status != "prepared" or current.attempt_count != 0:
                raise PaperStateConflictError(
                    "only an unattempted prepared dispatch can change session fence"
                )
            if (
                current.session_id == successor.session_id
                and current.fencing_token == successor.fencing_token
            ):
                return current
            owner_row = self._load_session_row(current.session_id)
            if owner_row is None:
                raise PaperStateCorruptionError(
                    "prepared paper dispatch lost its owning execution session"
                )
            owner = self._decode_paper_execution_session(owner_row)
            if owner.fencing_token != current.fencing_token:
                raise PaperStateCorruptionError(
                    "prepared paper dispatch fence does not match its owner"
                )
            if owner.status == "active" and owner.lease_expires_at > taken_over_at:
                raise PaperStateConflictError(
                    "a live predecessor still owns the prepared paper dispatch"
                )
            if taken_over_at <= current.updated_at:
                raise PaperStateConflictError(
                    "paper dispatch takeover timestamp must advance durable state"
                )
            rebound = PaperOrderDispatch.model_validate(
                current.model_copy(
                    update={
                        "session_id": successor.session_id,
                        "fencing_token": successor.fencing_token,
                        "updated_at": taken_over_at,
                        "revision": current.revision + 1,
                    }
                ).model_dump()
            )
            cursor = self._connection.execute(
                """
                UPDATE paper_order_dispatches
                SET session_id = ?, fencing_token = ?, revision = ?,
                    state_json = ?, updated_at = ?
                WHERE order_plan_id = ? AND session_id = ?
                  AND fencing_token = ? AND status = 'prepared'
                  AND revision = ?
                """,
                (
                    rebound.session_id,
                    rebound.fencing_token,
                    rebound.revision,
                    self._serialize(rebound),
                    rebound.updated_at.isoformat(),
                    current.order_plan_id,
                    current.session_id,
                    current.fencing_token,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "prepared paper dispatch changed during fenced takeover"
                )
        return rebound

    @staticmethod
    def _dispatch_immutable_identity(dispatch: PaperOrderDispatch) -> tuple[object, ...]:
        return (
            dispatch.order_plan_id,
            dispatch.broker_order_id,
            dispatch.run_id,
            dispatch.idempotency_key,
            dispatch.request_fingerprint,
            dispatch.policy_id,
            dispatch.policy_version,
            dispatch.user_id,
            dispatch.strategy_id,
            dispatch.strategy_version,
            dispatch.purpose,
            dispatch.symbol,
            dispatch.side,
            dispatch.order_type,
            dispatch.quantity,
            dispatch.limit_price,
            dispatch.quote_as_of,
            dispatch.quote_last,
            dispatch.quote_bid,
            dispatch.quote_ask,
            dispatch.quote_reference_basis,
            dispatch.risk_check_id,
            dispatch.risk_check_expires_at,
            dispatch.submission_evidence_expires_at,
            dispatch.reconciled_snapshot_id,
            dispatch.reconciled_snapshot_at,
            dispatch.snapshot_cash,
            dispatch.snapshot_equity,
            dispatch.snapshot_symbol_quantity,
            dispatch.snapshot_symbol_orderable_quantity,
            dispatch.snapshot_daily_loss_ratio,
            dispatch.snapshot_monthly_loss_ratio,
            dispatch.broker_orderable_cash,
            dispatch.broker_orderable_buy_quantity,
            dispatch.entry_atr14,
            dispatch.store_id,
            dispatch.session_id,
            dispatch.fencing_token,
            dispatch.data_mode,
            dispatch.broker_environment,
            dispatch.account_scope_fingerprint,
            dispatch.prepared_at,
        )

    def update_paper_order_dispatch(
        self,
        dispatch: PaperOrderDispatch,
    ) -> PaperOrderDispatch:
        """Persist monotonic broker evidence; this method never grants a retry."""

        dispatch = PaperOrderDispatch.model_validate(dispatch.model_dump())
        self._validate_dispatch_provenance(dispatch)
        with self._transaction():
            row = self._load_dispatch_row(dispatch.order_plan_id)
            if row is None:
                raise PaperStateNotFoundError(
                    f"missing paper dispatch: {dispatch.order_plan_id}"
                )
            existing = self._decode_paper_order_dispatch(row)
            if dispatch == existing:
                return existing
            if self._dispatch_immutable_identity(dispatch) != self._dispatch_immutable_identity(existing):
                raise PaperStateConflictError(
                    "paper dispatch immutable order and provenance evidence changed"
                )
            if dispatch.revision != existing.revision + 1:
                raise PaperStateConflictError(
                    "paper dispatch revision must advance by exactly one"
                )
            if dispatch.updated_at <= existing.updated_at:
                raise PaperStateConflictError(
                    "paper dispatch update timestamp must advance"
                )
            if dispatch.attempt_count != existing.attempt_count:
                raise PaperStateConflictError(
                    "only claim_dispatch_attempt may change attempt count"
                )
            if dispatch.dispatch_claimed_at != existing.dispatch_claimed_at:
                raise PaperStateConflictError(
                    "paper dispatch claim evidence is immutable"
                )
            if dispatch.status not in PAPER_DISPATCH_TRANSITIONS[existing.status]:
                raise PaperStateConflictError(
                    f"invalid paper dispatch transition: {existing.status} -> {dispatch.status}"
                )
            if (
                existing.broker_order_reference is not None
                and dispatch.broker_order_reference
                != existing.broker_order_reference
            ):
                raise PaperStateConflictError(
                    "paper broker order reference is immutable once assigned"
                )
            if (
                existing.broker_business_date is not None
                and dispatch.broker_business_date
                != existing.broker_business_date
            ):
                raise PaperStateConflictError(
                    "paper broker business date is immutable once assigned"
                )
            if (
                existing.broker_forwarding_order_org_number is not None
                and dispatch.broker_forwarding_order_org_number
                != existing.broker_forwarding_order_org_number
            ):
                raise PaperStateConflictError(
                    "paper broker forwarding organization is immutable once assigned"
                )
            if (
                existing.broker_order_branch_number is not None
                and dispatch.broker_order_branch_number
                != existing.broker_order_branch_number
            ):
                raise PaperStateConflictError(
                    "paper broker order branch is immutable once assigned"
                )
            if (
                existing.broker_order_time is not None
                and dispatch.broker_order_time != existing.broker_order_time
            ):
                raise PaperStateConflictError(
                    "paper broker order time is immutable once assigned"
                )
            existing_fills = {
                item.broker_fill_reference: item for item in existing.fill_evidence
            }
            next_fills = {
                item.broker_fill_reference: item for item in dispatch.fill_evidence
            }
            if not set(existing_fills).issubset(next_fills) or any(
                next_fills[reference] != evidence
                for reference, evidence in existing_fills.items()
            ):
                raise PaperStateConflictError(
                    "paper dispatch fill evidence cannot be removed or changed"
                )
            if dispatch.cumulative_filled_quantity < (
                existing.cumulative_filled_quantity - 0.000001
            ):
                raise PaperStateConflictError(
                    "paper dispatch cumulative fills cannot decrease"
                )
            reconciliation_transitions = {
                "pending": {"pending", "blocked", "reconciled"},
                "blocked": {"blocked", "reconciled"},
                "reconciled": {"reconciled"},
            }
            if dispatch.reconciliation_status not in reconciliation_transitions[
                existing.reconciliation_status
            ]:
                raise PaperStateConflictError(
                    "paper dispatch reconciliation cannot move backward"
                )
            cursor = self._connection.execute(
                """
                UPDATE paper_order_dispatches
                SET status = ?, revision = ?, state_json = ?, updated_at = ?
                WHERE order_plan_id = ? AND status = ? AND revision = ?
                """,
                (
                    dispatch.status,
                    dispatch.revision,
                    self._serialize(dispatch),
                    dispatch.updated_at.isoformat(),
                    dispatch.order_plan_id,
                    existing.status,
                    existing.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "paper dispatch changed before monotonic evidence update"
                )
        return dispatch

    def recover_interrupted_dispatches(
        self,
        *,
        session: PaperExecutionSession,
        recovered_at: datetime,
    ) -> list[PaperOrderDispatch]:
        """Recover only claims fenced behind an expired predecessor session."""

        _require_aware_timestamp(recovered_at, field_name="recovered_at")
        session = PaperExecutionSession.model_validate(session.model_dump())
        recovered: list[PaperOrderDispatch] = []
        with self._transaction():
            current_session = self._require_exact_active_session(
                session,
                checked_at=recovered_at,
            )
            rows = self._connection.execute(
                """
                SELECT order_plan_id, broker_order_id, idempotency_key, store_id, session_id,
                       fencing_token, status, revision, state_json, updated_at
                FROM paper_order_dispatches
                WHERE store_id = ? AND status = 'dispatch_claimed'
                  AND session_id <> ?
                ORDER BY order_plan_id
                """,
                (self.provenance.store_id, current_session.session_id),
            ).fetchall()
            for row in rows:
                existing = self._decode_paper_order_dispatch(row)
                owner_row = self._load_session_row(existing.session_id)
                if owner_row is None:
                    raise PaperStateCorruptionError(
                        "paper dispatch lost its owning execution session"
                    )
                owner = self._decode_paper_execution_session(owner_row)
                if (
                    owner.status == "active"
                    and owner.lease_expires_at > recovered_at
                ):
                    continue
                write_at = max(
                    recovered_at,
                    existing.updated_at + timedelta(microseconds=1),
                )
                unknown = PaperOrderDispatch.model_validate(
                    existing.model_copy(
                        update={
                            "status": "outcome_unknown",
                            "last_error_code": "process_interrupted",
                            "updated_at": write_at,
                            "revision": existing.revision + 1,
                        }
                    ).model_dump()
                )
                cursor = self._connection.execute(
                    """
                    UPDATE paper_order_dispatches
                    SET status = ?, revision = ?, state_json = ?, updated_at = ?
                    WHERE order_plan_id = ? AND status = 'dispatch_claimed'
                      AND revision = ?
                    """,
                    (
                        unknown.status,
                        unknown.revision,
                        self._serialize(unknown),
                        unknown.updated_at.isoformat(),
                        unknown.order_plan_id,
                        existing.revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PaperStateConflictError(
                        "paper dispatch changed during interrupted recovery"
                    )
                recovered.append(unknown)
        return recovered

    def load_paper_order_dispatch(
        self,
        order_plan_id: str,
    ) -> PaperOrderDispatch | None:
        row = self._load_dispatch_row(order_plan_id)
        if row is None:
            return None
        dispatch = self._decode_paper_order_dispatch(row)
        self._validate_dispatch_provenance(dispatch)
        return dispatch

    def find_paper_order_dispatch_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> PaperOrderDispatch | None:
        row = self._connection.execute(
            """
            SELECT order_plan_id, broker_order_id, idempotency_key, store_id, session_id,
                   fencing_token, status, revision, state_json, updated_at
            FROM paper_order_dispatches
            WHERE idempotency_key = ?
            """,
            (idempotency_key.strip(),),
        ).fetchone()
        if row is None:
            return None
        dispatch = self._decode_paper_order_dispatch(row)
        self._validate_dispatch_provenance(dispatch)
        return dispatch

    def list_paper_order_dispatches(self) -> list[PaperOrderDispatch]:
        rows = self._connection.execute(
            """
            SELECT order_plan_id, broker_order_id, idempotency_key, store_id, session_id,
                   fencing_token, status, revision, state_json, updated_at
            FROM paper_order_dispatches
            ORDER BY updated_at, order_plan_id
            """
        ).fetchall()
        dispatches = [self._decode_paper_order_dispatch(row) for row in rows]
        for dispatch in dispatches:
            self._validate_dispatch_provenance(dispatch)
        return dispatches

    def list_unresolved_paper_order_dispatches(self) -> list[PaperOrderDispatch]:
        return [
            dispatch
            for dispatch in self.list_paper_order_dispatches()
            if dispatch.reconciliation_status != "reconciled"
        ]

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
        if checkpoint.data_mode != self.provenance.data_mode:
            raise PaperStateProvenanceError(
                "paper-run checkpoint data mode does not match its state store"
            )
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
        if checkpoint.data_mode != self.provenance.data_mode:
            raise PaperStateProvenanceError(
                "paper-run checkpoint data mode does not match its state store"
            )
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
