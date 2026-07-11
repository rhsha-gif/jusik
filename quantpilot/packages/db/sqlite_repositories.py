"""Opt-in SQLite persistence for paper operator recovery state.

The fixture and mock repository registry remains in-memory.  This module opens
SQLite only when ``PaperStateStore`` is explicitly constructed.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
from math import isfinite
from pathlib import Path
import sqlite3
from typing import Callable, Iterator, Mapping, Sequence

from quantpilot.packages.core.execution.events import (
    PaperCancelRequestEventPayload,
    PaperEventSchemaUnsupported,
    PaperEventStreamConflict,
    PaperEventStreamCorruption,
    PaperExecutionAfter,
    PaperExecutionAggregateType,
    PaperExecutionEvent,
    PaperExecutionEventProvenance,
    PaperExecutionSource,
    PaperMutationOrigin,
    PAPER_MUTATION_ORIGIN_SOURCES,
    PaperOrderDispatchEventPayload,
    PaperRiskReservationEventPayload,
    build_paper_execution_event,
    canonical_import_event_id,
    canonical_json_bytes,
    canonical_sha256,
    decode_paper_execution_event,
    event_canonical_bytes,
    payload_after,
)
from quantpilot.packages.core.execution.reducer import (
    PaperExecutionProjection,
    reduce_paper_execution_event,
    replay_paper_execution_events,
)
from quantpilot.packages.core.execution.transitions import (
    CANCEL_STATUS_EVENT_TYPES,
    IMPORT_EVENT_TYPES,
    PAPER_CANCEL_TRANSITIONS,
    PAPER_DISPATCH_RECONCILIATION_TRANSITIONS,
    PAPER_DISPATCH_TRANSITIONS,
    PAPER_EXECUTION_EVENT_TYPES,
    PAPER_RESERVATION_RELEASE_BY_DISPATCH,
    PAPER_RESERVATION_TERMINALS,
    classify_dispatch_event_type,
    validate_cancel_event_transition,
    validate_reservation_event_transition,
)
from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    OperatorCycleClaim,
    OperatorSafetyState,
    PaperCancelRequest,
    PaperExecutionSession,
    PaperKillOperation,
    PaperOrderDispatch,
    PaperPortfolioLossBaseline,
    PaperRiskReservation,
    PaperRunCheckpoint,
    PendingLiquidationCheckpoint,
    StateStoreProvenance,
    StrategyOperatorState,
)
from quantpilot.packages.core.schemas import ProcessedFillRecord, new_id


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


class PaperRiskReservationRejected(PaperStateConflictError):
    pass


PAPER_STATE_SCHEMA_VERSION = 11
PAPER_STATE_PREVIOUS_SCHEMA_VERSION = 10
PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS = frozenset({6, 7, 8, 9, 10})


@dataclass(frozen=True, order=True)
class _PaperAggregateKey:
    aggregate_type: PaperExecutionAggregateType
    aggregate_id: str


@dataclass
class _PaperEventMutationGuard:
    load_authoritative: Callable[
        [_PaperAggregateKey], PaperExecutionAfter | None
    ] = field(repr=False)
    load_state_json: Callable[[_PaperAggregateKey], str | None] = field(repr=False)
    before_images: dict[
        _PaperAggregateKey, PaperExecutionAfter | None
    ] = field(default_factory=dict)
    before_state_json: dict[_PaperAggregateKey, str | None] = field(
        default_factory=dict
    )
    changes: dict[_PaperAggregateKey, tuple[PaperExecutionAfter, str]] = field(
        default_factory=dict
    )
    candidates: dict[_PaperAggregateKey, PaperExecutionEvent] = field(
        default_factory=dict
    )
    append_results: dict[_PaperAggregateKey, bool] = field(default_factory=dict)

    def capture_before(self, key: _PaperAggregateKey) -> None:
        if key in self.before_images or key in self.changes:
            raise PaperStateConflictError(
                "paper event mutation captured one aggregate more than once"
            )
        before = self.load_authoritative(key)
        persisted_state_json = self.load_state_json(key)
        if before is None:
            if persisted_state_json is not None:
                raise PaperStateCorruptionError(
                    "paper event mutation found raw state without an aggregate"
                )
        elif persisted_state_json != canonical_json_bytes(before).decode("utf-8"):
            raise PaperStateCorruptionError(
                "paper event mutation before-state JSON is not canonical"
            )
        self.before_images[key] = (
            None if before is None else before.model_copy(deep=True)
        )
        self.before_state_json[key] = persisted_state_json

    def register_change(
        self,
        *,
        after: PaperExecutionAfter,
        state_json: str,
        rowcount: int,
    ) -> None:
        key = _paper_aggregate_key(after)
        if key not in self.before_images:
            raise PaperStateConflictError(
                "paper event mutation requires a transaction-local before image"
            )
        if key in self.changes:
            raise PaperStateConflictError(
                "paper event mutation changed one aggregate more than once"
            )
        if isinstance(rowcount, bool) or rowcount != 1:
            raise PaperStateConflictError(
                "paper event mutation requires one authoritative SQL row change"
            )
        before = self.before_images[key]
        if before is None:
            if after.revision != 0:
                raise PaperStateConflictError(
                    "new paper event aggregate must start at source revision zero"
                )
        else:
            if type(before) is not type(after):
                raise PaperStateCorruptionError(
                    "paper event mutation changed aggregate model type"
                )
            if canonical_json_bytes(before) == canonical_json_bytes(after):
                raise PaperStateConflictError(
                    "paper event mutation did not change its authoritative row"
                )
            if after.revision != before.revision + 1:
                raise PaperStateConflictError(
                    "paper event mutation source revision must advance by one"
                )
        canonical_state = canonical_json_bytes(after).decode("utf-8")
        if state_json != canonical_state:
            raise PaperStateCorruptionError(
                "paper event mutation state JSON is not canonical"
            )
        current = self.load_authoritative(key)
        persisted_state_json = self.load_state_json(key)
        if (
            current is None
            or canonical_json_bytes(current) != canonical_json_bytes(after)
            or persisted_state_json != state_json
        ):
            raise PaperStateCorruptionError(
                "paper event mutation did not persist its registered after-state"
            )
        self.changes[key] = (after.model_copy(deep=True), state_json)

    def register_candidate(self, event: PaperExecutionEvent) -> None:
        key = _PaperAggregateKey(event.aggregate_type, event.aggregate_id)
        if key in self.candidates:
            raise PaperStateConflictError(
                "paper event mutation produced multiple events for one aggregate"
            )
        self.candidates[key] = PaperExecutionEvent.model_validate(event.model_dump())

    def register_append_result(
        self,
        event: PaperExecutionEvent,
        *,
        advanced: bool,
    ) -> None:
        key = _PaperAggregateKey(event.aggregate_type, event.aggregate_id)
        if key not in self.candidates or key in self.append_results:
            raise PaperStateConflictError("paper event append result is not registered")
        self.append_results[key] = advanced

    def assert_complete(self) -> None:
        if set(self.before_images) != set(self.changes):
            raise PaperStateConflictError(
                "paper event mutation before-images and row changes are not one-to-one"
            )
        if set(self.candidates) != set(self.append_results):
            raise PaperStateConflictError("paper event mutation has an incomplete append batch")
        advanced = {key for key, value in self.append_results.items() if value}
        if advanced != set(self.changes):
            raise PaperStateConflictError(
                "paper event mutation rows and advancing events are not one-to-one"
            )
        for key, (after, state_json) in self.changes.items():
            event = self.candidates[key]
            current = self.load_authoritative(key)
            persisted_state_json = self.load_state_json(key)
            if (
                payload_after(event.payload) != after
                or event.source_revision != after.revision
                or canonical_json_bytes(after).decode("utf-8") != state_json
                or current is None
                or canonical_json_bytes(current) != canonical_json_bytes(after)
                or persisted_state_json != state_json
            ):
                raise PaperStateCorruptionError(
                    "paper event mutation payload does not equal its authoritative row"
                )


def _paper_aggregate_key(after: PaperExecutionAfter) -> _PaperAggregateKey:
    if isinstance(after, PaperOrderDispatch):
        return _PaperAggregateKey("order_dispatch", after.order_plan_id)
    if isinstance(after, PaperRiskReservation):
        return _PaperAggregateKey("risk_reservation", after.reservation_id)
    return _PaperAggregateKey("cancel_request", after.cancel_id)


def _paper_event_provenance(
    provenance: StateStoreProvenance,
) -> PaperExecutionEventProvenance:
    try:
        return PaperExecutionEventProvenance(
            store_id=provenance.store_id,
            account_scope_fingerprint=provenance.account_scope_fingerprint,
            data_mode=provenance.data_mode,
            broker_environment=provenance.broker_environment,
        )
    except ValueError as exc:
        raise PaperStateProvenanceError(
            "paper execution events require KIS paper provenance"
        ) from exc

PAPER_KILL_TRANSITIONS: dict[str, set[str]] = {
    "killing": {"killing", "killed", "recovery_required"},
    "recovery_required": {"killing", "recovery_required"},
    "killed": {"killed", "recovery_required", "released"},
    "released": {"released"},
}

def _require_aware_timestamp(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value


def _require_whole_int(
    value: object,
    *,
    field_name: str,
    positive: bool = True,
    migration_error: bool = True,
) -> int:
    number = Decimal(str(value))
    whole = number.to_integral_value()
    if number != whole or (positive and whole <= 0) or (not positive and whole < 0):
        qualifier = "positive " if positive else "nonnegative "
        error_type = (
            PaperStateMigrationRequired
            if migration_error
            else PaperStateConflictError
        )
        raise error_type(f"{field_name} must be a {qualifier}whole number")
    return int(whole)


def _ceil_nonnegative_krw(value: object) -> int:
    number = max(Decimal("0"), Decimal(str(value)))
    return int(number.to_integral_value(rounding=ROUND_CEILING))


def _floor_nonnegative_krw(value: object) -> int:
    number = max(Decimal("0"), Decimal(str(value)))
    return int(number.to_integral_value(rounding=ROUND_FLOOR))


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

    def _validate_execution_event_schema(self) -> None:
        expected_columns = {
            "paper_execution_events": (
                ("event_id", "TEXT", 1, 1),
                ("store_id", "TEXT", 1, 0),
                ("aggregate_type", "TEXT", 1, 0),
                ("aggregate_id", "TEXT", 1, 0),
                ("aggregate_version", "INTEGER", 1, 0),
                ("event_type", "TEXT", 1, 0),
                ("account_scope_fingerprint", "TEXT", 1, 0),
                ("data_mode", "TEXT", 1, 0),
                ("broker_environment", "TEXT", 1, 0),
                ("source", "TEXT", 1, 0),
                ("occurred_at", "TEXT", 1, 0),
                ("received_at", "TEXT", 1, 0),
                ("correlation_id", "TEXT", 1, 0),
                ("causation_id", "TEXT", 0, 0),
                ("idempotency_key", "TEXT", 0, 0),
                ("local_broker_order_id", "TEXT", 0, 0),
                ("broker_order_id", "TEXT", 0, 0),
                ("original_client_order_id", "TEXT", 0, 0),
                ("venue_order_id", "TEXT", 0, 0),
                ("broker_sequence", "INTEGER", 0, 0),
                ("source_revision", "INTEGER", 1, 0),
                ("event_schema_version", "INTEGER", 1, 0),
                ("payload_json", "TEXT", 1, 0),
                ("payload_hash", "TEXT", 1, 0),
            ),
            "paper_execution_event_identity_keys": (
                ("event_id", "TEXT", 1, 1),
                ("identity_kind", "TEXT", 1, 2),
                ("identity_scope_hash", "TEXT", 1, 3),
                ("external_id", "TEXT", 1, 0),
                ("evidence_payload_hash", "TEXT", 1, 0),
            ),
        }
        expected_indexes = {
            "paper_execution_events": {
                (False, "c", ("store_id", "aggregate_type", "aggregate_id", "aggregate_version")),
                (True, "u", ("store_id", "aggregate_type", "aggregate_id", "aggregate_version")),
                (True, "pk", ("event_id",)),
            },
            "paper_execution_event_identity_keys": {
                (True, "u", ("identity_kind", "identity_scope_hash")),
                (True, "pk", ("event_id", "identity_kind", "identity_scope_hash")),
            },
        }
        expected_foreign_keys = {
            "paper_execution_events": {
                (
                    0,
                    0,
                    "state_store_metadata",
                    "store_id",
                    "store_id",
                    "NO ACTION",
                    "NO ACTION",
                    "NONE",
                )
            },
            "paper_execution_event_identity_keys": {
                (
                    0,
                    0,
                    "paper_execution_events",
                    "event_id",
                    "event_id",
                    "NO ACTION",
                    "NO ACTION",
                    "NONE",
                )
            },
        }
        for table_name, expected in expected_columns.items():
            table_row = self._connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            if table_row is None or "WITHOUT ROWID" not in (table_row["sql"] or "").upper():
                raise PaperStateCorruptionError(
                    "paper execution event schema is missing a required table"
                )
            actual_columns = tuple(
                (row["name"], row["type"].upper(), row["notnull"], row["pk"])
                for row in self._connection.execute(
                    f'PRAGMA table_info("{table_name}")'
                )
            )
            if actual_columns != expected:
                raise PaperStateCorruptionError(
                    "paper execution event table columns do not match schema v11"
                )
            actual_indexes: set[tuple[bool, str, tuple[str, ...]]] = set()
            for index_row in self._connection.execute(
                f'PRAGMA index_list("{table_name}")'
            ):
                index_name = str(index_row["name"]).replace('"', '""')
                columns = tuple(
                    row["name"]
                    for row in self._connection.execute(
                        f'PRAGMA index_info("{index_name}")'
                    )
                )
                actual_indexes.add(
                    (bool(index_row["unique"]), index_row["origin"], columns)
                )
            if not expected_indexes[table_name].issubset(actual_indexes):
                raise PaperStateCorruptionError(
                    "paper execution event indexes do not match schema v11"
                )
            actual_foreign_keys = {
                (
                    row["id"],
                    row["seq"],
                    row["table"],
                    row["from"],
                    row["to"],
                    row["on_update"],
                    row["on_delete"],
                    row["match"],
                )
                for row in self._connection.execute(
                    f'PRAGMA foreign_key_list("{table_name}")'
                )
            }
            if actual_foreign_keys != expected_foreign_keys[table_name]:
                raise PaperStateCorruptionError(
                    "paper execution event foreign keys do not match schema v11"
                )

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

        if persisted is not None and persisted.schema_version == PAPER_STATE_SCHEMA_VERSION:
            self._validate_execution_event_schema()

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
                CREATE TABLE IF NOT EXISTS paper_risk_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    order_plan_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    store_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (order_plan_id)
                        REFERENCES paper_order_dispatches(order_plan_id)
                        DEFERRABLE INITIALLY DEFERRED,
                    FOREIGN KEY (store_id)
                        REFERENCES state_store_metadata(store_id),
                    FOREIGN KEY (session_id, store_id, fencing_token)
                        REFERENCES paper_execution_sessions(
                            session_id, store_id, fencing_token
                        )
                ) WITHOUT ROWID
                """,
                """
                CREATE INDEX IF NOT EXISTS ix_paper_reservation_status
                ON paper_risk_reservations (store_id, status, symbol)
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_kill_operations (
                    kill_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (store_id) REFERENCES state_store_metadata(store_id)
                ) WITHOUT ROWID
                """,
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_paper_kill
                ON paper_kill_operations (store_id)
                WHERE status <> 'released'
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_cancel_requests (
                    cancel_id TEXT PRIMARY KEY,
                    kill_id TEXT NOT NULL,
                    order_plan_id TEXT NOT NULL,
                    broker_order_reference TEXT NOT NULL,
                    store_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (store_id, order_plan_id, broker_order_reference),
                    FOREIGN KEY (kill_id) REFERENCES paper_kill_operations(kill_id),
                    FOREIGN KEY (store_id) REFERENCES state_store_metadata(store_id)
                ) WITHOUT ROWID
                """,
                """
                CREATE INDEX IF NOT EXISTS ix_paper_cancel_status
                ON paper_cancel_requests (store_id, status, updated_at)
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_execution_events (
                    event_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    aggregate_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    account_scope_fingerprint TEXT NOT NULL,
                    data_mode TEXT NOT NULL,
                    broker_environment TEXT NOT NULL,
                    source TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT,
                    idempotency_key TEXT,
                    local_broker_order_id TEXT,
                    broker_order_id TEXT,
                    original_client_order_id TEXT,
                    venue_order_id TEXT,
                    broker_sequence INTEGER,
                    source_revision INTEGER NOT NULL,
                    event_schema_version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    UNIQUE (store_id, aggregate_type, aggregate_id, aggregate_version),
                    FOREIGN KEY (store_id) REFERENCES state_store_metadata(store_id)
                ) WITHOUT ROWID
                """,
                """
                CREATE INDEX IF NOT EXISTS ix_paper_execution_event_stream
                ON paper_execution_events (
                    store_id, aggregate_type, aggregate_id, aggregate_version
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS paper_execution_event_identity_keys (
                    event_id TEXT NOT NULL,
                    identity_kind TEXT NOT NULL,
                    identity_scope_hash TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    evidence_payload_hash TEXT NOT NULL,
                    PRIMARY KEY (event_id, identity_kind, identity_scope_hash),
                    UNIQUE (identity_kind, identity_scope_hash),
                    FOREIGN KEY (event_id) REFERENCES paper_execution_events(event_id)
                ) WITHOUT ROWID
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

            try:
                self._validate_execution_event_schema()
            except PaperStateCorruptionError as exc:
                if (
                    persisted is not None
                    and persisted.schema_version in PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS
                ):
                    raise PaperStateMigrationRequired(
                        "paper execution event schema could not be created"
                    ) from exc
                raise

            if (
                persisted is not None
                and persisted.schema_version in PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS
                and persisted.schema_version < 10
            ):
                self._backfill_open_dispatch_reservations()

            if (
                persisted is not None
                and persisted.schema_version in PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS
                and persisted.data_mode == "paper_trading"
            ):
                migration_received_at = datetime.now(timezone.utc)
                try:
                    self._import_legacy_execution_events(
                        persisted=persisted,
                        received_at=migration_received_at,
                    )
                except Exception as exc:
                    raise PaperStateMigrationRequired(
                        "paper execution events could not be imported"
                    ) from exc

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

    @contextmanager
    def _event_transaction(self) -> Iterator[_PaperEventMutationGuard]:
        with self._transaction():
            guard = _PaperEventMutationGuard(
                load_authoritative=self._load_paper_execution_authoritative_after,
                load_state_json=self._load_paper_execution_authoritative_state_json,
            )
            yield guard
            guard.assert_complete()

    @staticmethod
    def _serialize(
        model: (
            ManagedPositionState
            | StateStoreProvenance
            | PaperExecutionSession
            | PaperOrderDispatch
            | PaperRiskReservation
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
    def _decode_paper_risk_reservation(row: sqlite3.Row) -> PaperRiskReservation:
        try:
            model = PaperRiskReservation.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError(
                "invalid paper-risk reservation JSON"
            ) from exc
        metadata = (
            row["reservation_id"],
            row["order_plan_id"],
            row["idempotency_key"],
            row["store_id"],
            row["session_id"],
            row["fencing_token"],
            row["symbol"],
            row["kind"],
            row["status"],
            row["revision"],
            row["updated_at"],
        )
        expected = (
            model.reservation_id,
            model.order_plan_id,
            model.idempotency_key,
            model.store_id,
            model.session_id,
            model.fencing_token,
            model.symbol,
            model.kind,
            model.status,
            model.revision,
            model.updated_at.isoformat(),
        )
        if metadata != expected:
            raise PaperStateCorruptionError(
                "paper-risk reservation identity does not match its metadata"
            )
        return model

    def _backfill_open_dispatch_reservations(self) -> None:
        open_statuses = {
            "prepared",
            "dispatch_claimed",
            "outcome_unknown",
            "accepted",
            "partially_filled",
        }
        rows = self._connection.execute(
            """
            SELECT order_plan_id, broker_order_id, idempotency_key, store_id,
                   session_id, fencing_token, status, revision, state_json,
                   updated_at
            FROM paper_order_dispatches
            ORDER BY order_plan_id
            """
        ).fetchall()
        for row in rows:
            dispatch = self._decode_paper_order_dispatch(row)
            canonical_dispatch_json = self._serialize(dispatch)
            if row["state_json"] != canonical_dispatch_json:
                cursor = self._connection.execute(
                    """
                    UPDATE paper_order_dispatches
                    SET state_json = ?
                    WHERE order_plan_id = ? AND state_json = ?
                    """,
                    (
                        canonical_dispatch_json,
                        dispatch.order_plan_id,
                        row["state_json"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise PaperStateMigrationRequired(
                        "legacy dispatch changed during canonical migration"
                    )
            if dispatch.status not in open_statuses:
                continue
            existing = self._connection.execute(
                """
                SELECT reservation_id, order_plan_id, idempotency_key, store_id,
                       session_id, fencing_token, symbol, kind, status, revision,
                       state_json, updated_at
                FROM paper_risk_reservations
                WHERE order_plan_id = ? OR idempotency_key = ?
                """,
                (dispatch.order_plan_id, dispatch.idempotency_key),
            ).fetchone()
            if existing is not None:
                reservation = self._decode_paper_risk_reservation(existing)
                if (
                    reservation.order_plan_id != dispatch.order_plan_id
                    or reservation.idempotency_key != dispatch.idempotency_key
                    or reservation.status != "held"
                ):
                    raise PaperStateMigrationRequired(
                        "existing reservation conflicts with open dispatch backfill"
                    )
                continue

            quantity = _require_whole_int(
                dispatch.quantity,
                field_name="legacy dispatch quantity",
            )
            limit_price = _require_whole_int(
                dispatch.limit_price,
                field_name="legacy dispatch limit price",
            )
            current_gross = _ceil_nonnegative_krw(
                Decimal(str(dispatch.snapshot_equity))
                - Decimal(str(dispatch.snapshot_cash))
            )
            snapshot_equity = _floor_nonnegative_krw(
                dispatch.snapshot_equity
            )
            reservation_id = "presv_" + canonical_sha256(
                {
                    "derivation": "legacy_paper_risk_reservation_v1",
                    "store_id": dispatch.store_id,
                    "order_plan_id": dispatch.order_plan_id,
                    "idempotency_key": dispatch.idempotency_key,
                }
            ).removeprefix("sha256:")
            values: dict[str, object] = {
                "reservation_id": reservation_id,
                "order_plan_id": dispatch.order_plan_id,
                "idempotency_key": dispatch.idempotency_key,
                "symbol": dispatch.symbol,
                "side": dispatch.side,
                "store_id": dispatch.store_id,
                "session_id": dispatch.session_id,
                "fencing_token": dispatch.fencing_token,
                "account_scope_fingerprint": dispatch.account_scope_fingerprint,
                "snapshot_gross_exposure_basis_krw": current_gross,
                "status": "held",
                "created_at": dispatch.prepared_at,
                "updated_at": dispatch.prepared_at,
            }
            if dispatch.side == "buy":
                if (
                    dispatch.broker_orderable_cash is None
                    or dispatch.broker_orderable_buy_quantity is None
                ):
                    raise PaperStateMigrationRequired(
                        "open legacy buy dispatch lacks buying-power evidence"
                    )
                reserved_cash = quantity * limit_price
                gross_limit = current_gross + reserved_cash
                if gross_limit > snapshot_equity:
                    raise PaperStateMigrationRequired(
                        "open legacy buy dispatch exceeds cash-account gross capacity"
                    )
                values.update(
                    {
                        "kind": "cash_buy",
                        "reserved_cash_krw": reserved_cash,
                        "reserved_sell_quantity": None,
                        "reserved_gross_exposure_krw": reserved_cash,
                        "broker_orderable_cash_basis_krw": _floor_nonnegative_krw(
                            dispatch.broker_orderable_cash
                        ),
                        "broker_orderable_buy_quantity_basis": _require_whole_int(
                            dispatch.broker_orderable_buy_quantity,
                            field_name="legacy broker orderable buy quantity",
                            positive=False,
                        ),
                        "snapshot_orderable_quantity_basis": None,
                        "minimum_cash_reserve_krw": snapshot_equity - gross_limit,
                        "gross_exposure_limit_krw": gross_limit,
                    }
                )
            else:
                orderable = _require_whole_int(
                    dispatch.snapshot_symbol_orderable_quantity,
                    field_name="legacy snapshot orderable quantity",
                    positive=False,
                )
                values.update(
                    {
                        "kind": "sell_quantity",
                        "reserved_cash_krw": None,
                        "reserved_sell_quantity": quantity,
                        "reserved_gross_exposure_krw": 0,
                        "broker_orderable_cash_basis_krw": None,
                        "broker_orderable_buy_quantity_basis": None,
                        "snapshot_orderable_quantity_basis": orderable,
                        "minimum_cash_reserve_krw": snapshot_equity - current_gross,
                        "gross_exposure_limit_krw": current_gross,
                    }
                )
            try:
                reservation = PaperRiskReservation(**values)
            except ValueError as exc:
                raise PaperStateMigrationRequired(
                    "open legacy dispatch cannot be promoted to a valid risk reservation"
                ) from exc
            self._connection.execute(
                """
                INSERT INTO paper_risk_reservations (
                    reservation_id, order_plan_id, idempotency_key, store_id,
                    session_id, fencing_token, symbol, kind, status, revision,
                    state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation.reservation_id,
                    reservation.order_plan_id,
                    reservation.idempotency_key,
                    reservation.store_id,
                    reservation.session_id,
                    reservation.fencing_token,
                    reservation.symbol,
                    reservation.kind,
                    reservation.status,
                    reservation.revision,
                    self._serialize(reservation),
                    reservation.updated_at.isoformat(),
                ),
            )

    @staticmethod
    def _decode_paper_kill_operation(row: sqlite3.Row) -> PaperKillOperation:
        try:
            model = PaperKillOperation.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid paper-kill JSON") from exc
        metadata = (
            row["kill_id"],
            row["store_id"],
            row["status"],
            row["revision"],
            row["updated_at"],
        )
        expected = (
            model.kill_id,
            model.store_id,
            model.status,
            model.revision,
            model.updated_at.isoformat(),
        )
        if metadata != expected:
            raise PaperStateCorruptionError(
                "paper-kill identity does not match its metadata"
            )
        return model

    @staticmethod
    def _decode_paper_cancel_request(row: sqlite3.Row) -> PaperCancelRequest:
        try:
            model = PaperCancelRequest.model_validate_json(row["state_json"])
        except ValueError as exc:
            raise PaperStateCorruptionError("invalid paper-cancel JSON") from exc
        metadata = (
            row["cancel_id"],
            row["kill_id"],
            row["order_plan_id"],
            row["broker_order_reference"],
            row["store_id"],
            row["status"],
            row["revision"],
            row["updated_at"],
        )
        expected = (
            model.cancel_id,
            model.kill_id,
            model.order_plan_id,
            model.broker_order_reference,
            model.store_id,
            model.status,
            model.revision,
            model.updated_at.isoformat(),
        )
        if metadata != expected:
            raise PaperStateCorruptionError(
                "paper-cancel identity does not match its metadata"
            )
        return model

    @staticmethod
    def _raise_paper_event_error(exc: Exception) -> None:
        if isinstance(exc, PaperEventStreamConflict):
            raise PaperStateConflictError(str(exc)) from exc
        if isinstance(exc, PaperEventStreamCorruption):
            raise PaperStateCorruptionError(str(exc)) from exc
        if isinstance(exc, PaperEventSchemaUnsupported):
            raise PaperStateMigrationRequired(str(exc)) from exc
        raise exc

    def _decode_paper_execution_event_row(
        self,
        row: sqlite3.Row,
    ) -> PaperExecutionEvent:
        if (
            isinstance(row["event_schema_version"], bool)
            or row["event_schema_version"] != 1
            or not isinstance(row["event_type"], str)
            or row["event_type"] not in PAPER_EXECUTION_EVENT_TYPES
        ):
            try:
                raise PaperEventSchemaUnsupported(
                    "unsupported persisted paper execution event discriminator"
                )
            except PaperEventSchemaUnsupported as exc:
                self._raise_paper_event_error(exc)
                raise AssertionError("unreachable")
        identity_rows = self._connection.execute(
            """
            SELECT identity_kind, identity_scope_hash, external_id,
                   evidence_payload_hash
            FROM paper_execution_event_identity_keys
            WHERE event_id = ?
            ORDER BY identity_kind, identity_scope_hash
            """,
            (row["event_id"],),
        ).fetchall()
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError) as exc:
            raise PaperStateCorruptionError(
                "invalid paper execution event payload JSON"
            ) from exc
        raw = {
            "event_id": row["event_id"],
            "store_id": row["store_id"],
            "aggregate_type": row["aggregate_type"],
            "aggregate_id": row["aggregate_id"],
            "aggregate_version": row["aggregate_version"],
            "event_type": row["event_type"],
            "account_scope_fingerprint": row["account_scope_fingerprint"],
            "data_mode": row["data_mode"],
            "broker_environment": row["broker_environment"],
            "source": row["source"],
            "occurred_at": row["occurred_at"],
            "received_at": row["received_at"],
            "correlation_id": row["correlation_id"],
            "causation_id": row["causation_id"],
            "idempotency_key": row["idempotency_key"],
            "local_broker_order_id": row["local_broker_order_id"],
            "broker_order_id": row["broker_order_id"],
            "original_client_order_id": row["original_client_order_id"],
            "venue_order_id": row["venue_order_id"],
            "broker_sequence": row["broker_sequence"],
            "source_revision": row["source_revision"],
            "event_schema_version": row["event_schema_version"],
            "payload": payload,
            "payload_hash": row["payload_hash"],
            "identity_keys": [
                {
                    "kind": identity_row["identity_kind"],
                    "scope_hash": identity_row["identity_scope_hash"],
                    "external_id": identity_row["external_id"],
                    "evidence_payload_hash": identity_row[
                        "evidence_payload_hash"
                    ],
                }
                for identity_row in identity_rows
            ],
        }
        try:
            event = decode_paper_execution_event(raw)
        except (
            PaperEventStreamConflict,
            PaperEventStreamCorruption,
            PaperEventSchemaUnsupported,
        ) as exc:
            self._raise_paper_event_error(exc)
            raise AssertionError("unreachable")
        if canonical_json_bytes(event.payload).decode("utf-8") != row["payload_json"]:
            raise PaperStateCorruptionError(
                "paper execution event payload JSON is not canonical"
            )
        return event

    def _list_paper_execution_events(
        self,
        *,
        expected_provenance: PaperExecutionEventProvenance,
        aggregate_type: PaperExecutionAggregateType | None = None,
        aggregate_id: str | None = None,
    ) -> list[PaperExecutionEvent]:
        clauses = ["store_id = ?"]
        parameters: list[object] = [expected_provenance.store_id]
        if aggregate_type is not None:
            if aggregate_type not in {
                "order_dispatch",
                "risk_reservation",
                "cancel_request",
            }:
                raise PaperStateConflictError(
                    "unsupported paper execution aggregate type"
                )
            clauses.append("aggregate_type = ?")
            parameters.append(aggregate_type)
        if aggregate_id is not None:
            normalized_id = aggregate_id.strip()
            if not normalized_id:
                raise PaperStateConflictError(
                    "paper execution aggregate id must not be blank"
                )
            clauses.append("aggregate_id = ?")
            parameters.append(normalized_id)
        rows = self._connection.execute(
            f"""
            SELECT event_id, store_id, aggregate_type, aggregate_id,
                   aggregate_version, event_type, account_scope_fingerprint,
                   data_mode, broker_environment, source, occurred_at,
                   received_at, correlation_id, causation_id, idempotency_key,
                   local_broker_order_id, broker_order_id,
                   original_client_order_id, venue_order_id, broker_sequence,
                   source_revision, event_schema_version, payload_json,
                   payload_hash
            FROM paper_execution_events
            WHERE {' AND '.join(clauses)}
            ORDER BY aggregate_type, aggregate_id, aggregate_version, event_id
            """,
            tuple(parameters),
        ).fetchall()
        events = [self._decode_paper_execution_event_row(row) for row in rows]
        for event in events:
            if (
                event.store_id != expected_provenance.store_id
                or event.account_scope_fingerprint
                != expected_provenance.account_scope_fingerprint
                or event.data_mode != expected_provenance.data_mode
                or event.broker_environment
                != expected_provenance.broker_environment
            ):
                raise PaperStateCorruptionError(
                    "paper execution event provenance does not match its store"
                )
        return events

    def list_paper_execution_events(
        self,
        aggregate_type: PaperExecutionAggregateType | None = None,
        aggregate_id: str | None = None,
    ) -> list[PaperExecutionEvent]:
        """Return typed journal facts in deterministic stream order."""

        provenance = _paper_event_provenance(self._require_paper_store())
        return self._list_paper_execution_events(
            expected_provenance=provenance,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
        )

    def _load_paper_execution_projection(
        self,
        *,
        key: _PaperAggregateKey,
        expected_provenance: PaperExecutionEventProvenance,
    ) -> PaperExecutionProjection | None:
        events = self._list_paper_execution_events(
            expected_provenance=expected_provenance,
            aggregate_type=key.aggregate_type,
            aggregate_id=key.aggregate_id,
        )
        if not events:
            return None
        try:
            return replay_paper_execution_events(
                events,
                expected_provenance=expected_provenance,
            )
        except (
            PaperEventStreamConflict,
            PaperEventStreamCorruption,
            PaperEventSchemaUnsupported,
        ) as exc:
            self._raise_paper_event_error(exc)
            raise AssertionError("unreachable")

    def _load_paper_execution_authoritative_after(
        self,
        key: _PaperAggregateKey,
    ) -> PaperExecutionAfter | None:
        if key.aggregate_type == "order_dispatch":
            row = self._connection.execute(
                """
                SELECT order_plan_id, broker_order_id, idempotency_key, store_id,
                       session_id, fencing_token, status, revision, state_json,
                       updated_at
                FROM paper_order_dispatches
                WHERE order_plan_id = ?
                """,
                (key.aggregate_id,),
            ).fetchone()
            return None if row is None else self._decode_paper_order_dispatch(row)
        if key.aggregate_type == "risk_reservation":
            row = self._connection.execute(
                """
                SELECT reservation_id, order_plan_id, idempotency_key, store_id,
                       session_id, fencing_token, symbol, kind, status, revision,
                       state_json, updated_at
                FROM paper_risk_reservations
                WHERE reservation_id = ?
                """,
                (key.aggregate_id,),
            ).fetchone()
            return None if row is None else self._decode_paper_risk_reservation(row)
        row = self._connection.execute(
            """
            SELECT cancel_id, kill_id, order_plan_id, broker_order_reference,
                   store_id, status, revision, state_json, updated_at
            FROM paper_cancel_requests
            WHERE cancel_id = ?
            """,
            (key.aggregate_id,),
        ).fetchone()
        return None if row is None else self._decode_paper_cancel_request(row)

    def _load_paper_execution_authoritative_state_json(
        self,
        key: _PaperAggregateKey,
    ) -> str | None:
        if key.aggregate_type == "order_dispatch":
            row = self._connection.execute(
                "SELECT state_json FROM paper_order_dispatches WHERE order_plan_id = ?",
                (key.aggregate_id,),
            ).fetchone()
        elif key.aggregate_type == "risk_reservation":
            row = self._connection.execute(
                "SELECT state_json FROM paper_risk_reservations WHERE reservation_id = ?",
                (key.aggregate_id,),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT state_json FROM paper_cancel_requests WHERE cancel_id = ?",
                (key.aggregate_id,),
            ).fetchone()
        return None if row is None else str(row["state_json"])

    @staticmethod
    def _legacy_paper_execution_event(
        *,
        after: PaperExecutionAfter,
        received_at: datetime,
    ) -> PaperExecutionEvent:
        if isinstance(after, PaperOrderDispatch):
            event_type = "LegacyOrderDispatchImported"
            payload = PaperOrderDispatchEventPayload(
                after=after,
                legacy_snapshot=True,
            )
        elif isinstance(after, PaperRiskReservation):
            event_type = "LegacyRiskReservationImported"
            payload = PaperRiskReservationEventPayload(
                after=after,
                legacy_snapshot=True,
            )
        else:
            event_type = "LegacyCancelRequestImported"
            payload = PaperCancelRequestEventPayload(
                after=after,
                legacy_snapshot=True,
            )
        payload_hash = canonical_sha256(payload)
        key = _paper_aggregate_key(after)
        return build_paper_execution_event(
            event_id=canonical_import_event_id(
                store_id=after.store_id,
                aggregate_type=key.aggregate_type,
                aggregate_id=key.aggregate_id,
                source_revision=after.revision,
                payload_hash=payload_hash,
            ),
            aggregate_version=1,
            event_type=event_type,
            source="schema_migration",
            after=after,
            causation_id=None,
            legacy_snapshot=True,
            migration_received_at=received_at,
        )

    @staticmethod
    def _new_runtime_paper_execution_event(
        *,
        aggregate_version: int,
        event_type: str,
        source: PaperExecutionSource,
        after: PaperExecutionAfter,
        causation_id: str | None,
        before: PaperOrderDispatch | None = None,
    ) -> PaperExecutionEvent:
        if source == "schema_migration" or event_type in IMPORT_EVENT_TYPES:
            raise PaperStateConflictError(
                "runtime event candidates cannot claim migration authority"
            )
        try:
            return build_paper_execution_event(
                event_id=new_id("pevt"),
                aggregate_version=aggregate_version,
                event_type=event_type,
                source=source,
                after=after,
                causation_id=causation_id,
                before=before,
            )
        except ValueError as exc:
            raise PaperStateCorruptionError(
                "authoritative row cannot form a canonical runtime event"
            ) from exc

    def _prepare_runtime_paper_execution_event(
        self,
        *,
        before: PaperExecutionAfter | None,
        after: PaperExecutionAfter,
        event_type: str,
        source: PaperExecutionSource,
        causation_id: str | None = None,
        use_stream_causation: bool = True,
    ) -> tuple[_PaperAggregateKey, PaperExecutionEvent, int]:
        """Bind one runtime fact to the exact pre-mutation stream projection."""

        key = _paper_aggregate_key(after)
        projection = self._load_paper_execution_projection(
            key=key,
            expected_provenance=_paper_event_provenance(self._require_paper_store()),
        )
        if before is None:
            if projection is not None:
                raise PaperStateCorruptionError(
                    "new paper aggregate already has an execution event stream"
                )
            expected_previous_version = 0
            stream_causation = None
        else:
            if (
                projection is None
                or canonical_json_bytes(projection.after)
                != canonical_json_bytes(before)
            ):
                raise PaperStateCorruptionError(
                    "paper execution stream does not match its authoritative before-state"
                )
            expected_previous_version = projection.aggregate_version
            stream_causation = projection.last_event_id
        event = self._new_runtime_paper_execution_event(
            aggregate_version=expected_previous_version + 1,
            event_type=event_type,
            source=source,
            after=after,
            causation_id=(stream_causation if use_stream_causation else causation_id),
            before=before if isinstance(before, PaperOrderDispatch) else None,
        )
        return key, event, expected_previous_version

    def _append_runtime_paper_execution_events(
        self,
        guard: _PaperEventMutationGuard,
        candidates: Sequence[
            tuple[_PaperAggregateKey, PaperExecutionEvent, int]
        ],
    ) -> None:
        if not candidates:
            return
        events = [event for _key, event, _version in candidates]
        expected_previous_versions = {
            key: version for key, _event, version in candidates
        }
        if len(expected_previous_versions) != len(candidates):
            raise PaperStateConflictError(
                "runtime event batch cannot advance one aggregate twice"
            )
        for _key, event, _version in candidates:
            guard.register_candidate(event)
        self._append_paper_execution_events(
            events,
            expected_previous_versions=expected_previous_versions,
            guard=guard,
        )

    def _load_paper_execution_event_by_id(
        self,
        event_id: str,
    ) -> PaperExecutionEvent | None:
        row = self._connection.execute(
            """
            SELECT event_id, store_id, aggregate_type, aggregate_id,
                   aggregate_version, event_type, account_scope_fingerprint,
                   data_mode, broker_environment, source, occurred_at,
                   received_at, correlation_id, causation_id, idempotency_key,
                   local_broker_order_id, broker_order_id,
                   original_client_order_id, venue_order_id, broker_sequence,
                   source_revision, event_schema_version, payload_json,
                   payload_hash
            FROM paper_execution_events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        return None if row is None else self._decode_paper_execution_event_row(row)

    def _insert_paper_execution_event(self, event: PaperExecutionEvent) -> None:
        self._connection.execute(
            """
            INSERT INTO paper_execution_events (
                event_id, store_id, aggregate_type, aggregate_id,
                aggregate_version, event_type, account_scope_fingerprint,
                data_mode, broker_environment, source, occurred_at, received_at,
                correlation_id, causation_id, idempotency_key,
                local_broker_order_id, broker_order_id,
                original_client_order_id, venue_order_id, broker_sequence,
                source_revision, event_schema_version, payload_json, payload_hash
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?
            )
            """,
            (
                event.event_id,
                event.store_id,
                event.aggregate_type,
                event.aggregate_id,
                event.aggregate_version,
                event.event_type,
                event.account_scope_fingerprint,
                event.data_mode,
                event.broker_environment,
                event.source,
                event.occurred_at.isoformat(),
                event.received_at.isoformat(),
                event.correlation_id,
                event.causation_id,
                event.idempotency_key,
                event.local_broker_order_id,
                event.broker_order_id,
                event.original_client_order_id,
                event.venue_order_id,
                event.broker_sequence,
                event.source_revision,
                event.event_schema_version,
                canonical_json_bytes(event.payload).decode("utf-8"),
                event.payload_hash,
            ),
        )
        for identity in event.identity_keys:
            self._connection.execute(
                """
                INSERT INTO paper_execution_event_identity_keys (
                    event_id, identity_kind, identity_scope_hash, external_id,
                    evidence_payload_hash
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    identity.kind,
                    identity.scope_hash,
                    identity.external_id,
                    identity.evidence_payload_hash,
                ),
            )

    @staticmethod
    def _validate_paper_event_batch_causation(
        events: Sequence[PaperExecutionEvent],
        *,
        append_results: Sequence[bool] | None = None,
    ) -> None:
        if append_results is not None and len(append_results) != len(events):
            raise PaperStateConflictError(
                "paired paper execution event append results are incomplete"
            )
        result_by_event_id = (
            None
            if append_results is None
            else {
                event.event_id: advanced
                for event, advanced in zip(events, append_results, strict=True)
            }
        )
        if result_by_event_id is not None and len(result_by_event_id) != len(events):
            raise PaperStateConflictError(
                "paired paper execution events must have distinct identifiers"
            )

        def require_same_append_result(
            event: PaperExecutionEvent,
            paired: PaperExecutionEvent,
        ) -> None:
            if result_by_event_id is not None and (
                result_by_event_id[event.event_id]
                != result_by_event_id[paired.event_id]
            ):
                raise PaperStateCorruptionError(
                    "paired paper execution events must advance or retry together"
                )

        def require_paired(
            event: PaperExecutionEvent,
            *,
            paired_event_type: str,
        ) -> PaperExecutionEvent:
            matches = [
                candidate
                for candidate in events
                if candidate.correlation_id == event.correlation_id
                and candidate.event_type == paired_event_type
            ]
            if len(matches) != 1 or event.causation_id != matches[0].event_id:
                raise PaperStateCorruptionError(
                    "paired paper execution event causation is not store-closed"
                )
            paired = matches[0]
            require_same_append_result(event, paired)
            return paired

        terminal_dispatch_types = {
            "OrderFilled",
            "OrderRejected",
            "OrderCancelled",
            "OrderExpiredPreDispatch",
            "OrderFailedPreDispatch",
        }
        for event in events:
            if event.event_type == "OrderPrepared":
                require_paired(event, paired_event_type="RiskReserved")
            elif event.event_type == "RiskReservationFenceRebound":
                require_paired(event, paired_event_type="DispatchFenceRebound")
            elif event.event_type == "RiskReservationReleased":
                matches = [
                    candidate
                    for candidate in events
                    if candidate.correlation_id == event.correlation_id
                    and candidate.aggregate_type == "order_dispatch"
                    and candidate.event_type in terminal_dispatch_types
                ]
                if (
                    len(matches) != 1
                    or event.causation_id != matches[0].event_id
                    or event.source != matches[0].source
                ):
                    raise PaperStateCorruptionError(
                        "reservation release causation does not match its terminal dispatch"
                    )
                require_same_append_result(event, matches[0])

    def _append_paper_execution_events(
        self,
        events: Sequence[PaperExecutionEvent],
        *,
        expected_previous_versions: Mapping[_PaperAggregateKey, int],
        guard: _PaperEventMutationGuard | None,
        expected_provenance: PaperExecutionEventProvenance | None = None,
        import_mode: bool = False,
    ) -> tuple[bool, ...]:
        """Append a validated batch inside the caller's existing transaction."""

        if not self._connection.in_transaction:
            raise PaperStateConflictError(
                "paper execution events require an existing write transaction"
            )
        if import_mode:
            if guard is not None:
                raise PaperStateConflictError(
                    "schema import cannot use a runtime mutation guard"
                )
        elif guard is None:
            raise PaperStateConflictError(
                "runtime event append requires a mutation guard"
            )
        provenance = expected_provenance
        if provenance is None:
            provenance = _paper_event_provenance(self._require_paper_store())

        candidates: list[PaperExecutionEvent] = []
        keys: list[_PaperAggregateKey] = []
        for raw_event in events:
            try:
                event = decode_paper_execution_event(raw_event)
            except (
                PaperEventStreamConflict,
                PaperEventStreamCorruption,
                PaperEventSchemaUnsupported,
            ) as exc:
                self._raise_paper_event_error(exc)
                raise AssertionError("unreachable")
            key = _PaperAggregateKey(event.aggregate_type, event.aggregate_id)
            if key in keys:
                raise PaperStateConflictError(
                    "an append batch cannot advance one aggregate twice"
                )
            if import_mode:
                if (
                    event.event_type not in IMPORT_EVENT_TYPES
                    or event.source != "schema_migration"
                ):
                    raise PaperStateConflictError(
                        "schema import accepts only deterministic import anchors"
                    )
            elif (
                event.event_type in IMPORT_EVENT_TYPES
                or event.source == "schema_migration"
            ):
                raise PaperStateConflictError(
                    "runtime append cannot claim migration authority"
                )
            if (
                event.store_id != provenance.store_id
                or event.account_scope_fingerprint
                != provenance.account_scope_fingerprint
                or event.data_mode != provenance.data_mode
                or event.broker_environment != provenance.broker_environment
            ):
                raise PaperStateCorruptionError(
                    "paper execution event provenance does not match its store"
                )
            if guard is not None:
                registered = guard.candidates.get(key)
                if (
                    registered is None
                    or event_canonical_bytes(registered)
                    != event_canonical_bytes(event)
                ):
                    raise PaperStateConflictError(
                        "runtime event candidate was not registered by its mutation"
                    )
            candidates.append(event)
            keys.append(key)

        if not import_mode:
            self._validate_paper_event_batch_causation(candidates)

        if set(expected_previous_versions) != set(keys):
            raise PaperStateConflictError(
                "expected event versions do not match the append batch"
            )

        append_results: list[bool] = []
        for event, key in zip(candidates, keys, strict=True):
            persisted_event = self._load_paper_execution_event_by_id(event.event_id)
            if persisted_event is not None:
                if event_canonical_bytes(persisted_event) != event_canonical_bytes(event):
                    raise PaperStateCorruptionError(
                        "paper execution event_id was reused with divergent bytes"
                    )
                authoritative = self._load_paper_execution_authoritative_after(key)
                authoritative_state_json = (
                    self._load_paper_execution_authoritative_state_json(key)
                )
                canonical_after_json = canonical_json_bytes(
                    payload_after(persisted_event.payload)
                ).decode("utf-8")
                if (
                    authoritative is None
                    or canonical_json_bytes(authoritative)
                    != canonical_json_bytes(payload_after(persisted_event.payload))
                    or authoritative_state_json != canonical_after_json
                ):
                    raise PaperStateCorruptionError(
                        "duplicate event no longer matches its authoritative row"
                    )
                if guard is not None:
                    guard.register_append_result(event, advanced=False)
                append_results.append(False)
                continue

            projection = self._load_paper_execution_projection(
                key=key,
                expected_provenance=provenance,
            )
            current_version = 0 if projection is None else projection.aggregate_version
            expected_previous = expected_previous_versions[key]
            if (
                isinstance(expected_previous, bool)
                or not isinstance(expected_previous, int)
                or expected_previous < 0
                or expected_previous != current_version
            ):
                raise PaperStateConflictError(
                    "paper execution stream expected version changed"
                )
            try:
                reduce_paper_execution_event(
                    projection,
                    event,
                    expected_provenance=provenance,
                )
            except (
                PaperEventStreamConflict,
                PaperEventStreamCorruption,
                PaperEventSchemaUnsupported,
            ) as exc:
                self._raise_paper_event_error(exc)
                raise AssertionError("unreachable")

            authoritative = self._load_paper_execution_authoritative_after(key)
            after = payload_after(event.payload)
            authoritative_state_json = (
                self._load_paper_execution_authoritative_state_json(key)
            )
            canonical_after_json = canonical_json_bytes(after).decode("utf-8")
            if (
                authoritative is None
                or canonical_json_bytes(authoritative) != canonical_json_bytes(after)
                or authoritative_state_json != canonical_after_json
            ):
                raise PaperStateConflictError(
                    "event payload does not equal its authoritative row"
                )
            if guard is not None:
                changed = guard.changes.get(key)
                if (
                    changed is None
                    or changed[0] != after
                    or changed[1] != canonical_after_json
                    or event.source_revision != after.revision
                ):
                    raise PaperStateCorruptionError(
                        "event candidate does not match its registered row mutation"
                    )
            try:
                self._insert_paper_execution_event(event)
            except sqlite3.IntegrityError as exc:
                raise PaperStateConflictError(
                    "paper execution event identity or version already exists"
                ) from exc
            if guard is not None:
                guard.register_append_result(event, advanced=True)
            append_results.append(True)
        if not import_mode:
            self._validate_paper_event_batch_causation(
                candidates,
                append_results=append_results,
            )
        return tuple(append_results)

    def _import_legacy_execution_events(
        self,
        *,
        persisted: StateStoreProvenance,
        received_at: datetime,
    ) -> None:
        """Seed truthful version-one anchors during the v6-v10 transaction."""

        _require_aware_timestamp(received_at, field_name="migration received_at")
        provenance = _paper_event_provenance(persisted)
        after_states: list[PaperExecutionAfter] = []
        dispatch_rows = self._connection.execute(
            """
            SELECT order_plan_id, broker_order_id, idempotency_key, store_id,
                   session_id, fencing_token, status, revision, state_json,
                   updated_at
            FROM paper_order_dispatches
            ORDER BY order_plan_id
            """
        ).fetchall()
        after_states.extend(
            self._decode_paper_order_dispatch(row) for row in dispatch_rows
        )
        reservation_rows = self._connection.execute(
            """
            SELECT reservation_id, order_plan_id, idempotency_key, store_id,
                   session_id, fencing_token, symbol, kind, status, revision,
                   state_json, updated_at
            FROM paper_risk_reservations
            ORDER BY reservation_id
            """
        ).fetchall()
        after_states.extend(
            self._decode_paper_risk_reservation(row) for row in reservation_rows
        )
        cancel_rows = self._connection.execute(
            """
            SELECT cancel_id, kill_id, order_plan_id, broker_order_reference,
                   store_id, status, revision, state_json, updated_at
            FROM paper_cancel_requests
            ORDER BY cancel_id
            """
        ).fetchall()
        after_states.extend(
            self._decode_paper_cancel_request(row) for row in cancel_rows
        )
        if not after_states:
            return

        events = [
            self._legacy_paper_execution_event(
                after=after,
                received_at=received_at,
            )
            for after in sorted(
                after_states,
                key=lambda value: (
                    _paper_aggregate_key(value).aggregate_type,
                    _paper_aggregate_key(value).aggregate_id,
                ),
            )
        ]
        expected_versions = {
            _PaperAggregateKey(event.aggregate_type, event.aggregate_id): 0
            for event in events
        }
        self._append_paper_execution_events(
            events,
            expected_previous_versions=expected_versions,
            guard=None,
            expected_provenance=provenance,
            import_mode=True,
        )

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

    def _validate_reservation_provenance(
        self,
        reservation: PaperRiskReservation,
    ) -> None:
        provenance = self._require_paper_store()
        if (
            reservation.store_id != provenance.store_id
            or reservation.data_mode != provenance.data_mode
            or reservation.broker_environment != provenance.broker_environment
            or reservation.account_scope_fingerprint
            != provenance.account_scope_fingerprint
        ):
            raise PaperStateProvenanceError(
                "paper reservation does not match its state-store provenance"
            )

    def _validate_kill_provenance(self, operation: PaperKillOperation) -> None:
        provenance = self._require_paper_store()
        if (
            operation.store_id != provenance.store_id
            or operation.data_mode != provenance.data_mode
            or operation.broker_environment != provenance.broker_environment
            or operation.account_scope_fingerprint
            != provenance.account_scope_fingerprint
        ):
            raise PaperStateProvenanceError(
                "paper kill does not match its state-store provenance"
            )

    def _validate_cancel_provenance(self, request: PaperCancelRequest) -> None:
        provenance = self._require_paper_store()
        if (
            request.store_id != provenance.store_id
            or request.data_mode != provenance.data_mode
            or request.broker_environment != provenance.broker_environment
            or request.account_scope_fingerprint
            != provenance.account_scope_fingerprint
        ):
            raise PaperStateProvenanceError(
                "paper cancel does not match its state-store provenance"
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

    def _load_reservation_row(self, order_plan_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT reservation_id, order_plan_id, idempotency_key, store_id,
                   session_id, fencing_token, symbol, kind, status, revision,
                   state_json, updated_at
            FROM paper_risk_reservations
            WHERE order_plan_id = ?
            """,
            (order_plan_id.strip(),),
        ).fetchone()

    def insert_paper_order_dispatch(
        self,
        dispatch: PaperOrderDispatch,
    ) -> PaperOrderDispatch:
        """Reject the pre-v10 bypass; paper dispatches require a reservation."""

        raise PaperStateConflictError(
            "paper dispatch requires atomic risk reservation"
        )

    def reserve_and_insert_paper_order_dispatch(
        self,
        dispatch: PaperOrderDispatch,
        reservation: PaperRiskReservation,
    ) -> tuple[PaperOrderDispatch, PaperRiskReservation]:
        """Atomically reserve capacity and prepare one broker dispatch."""

        dispatch = PaperOrderDispatch.model_validate(dispatch.model_dump())
        reservation = PaperRiskReservation.model_validate(reservation.model_dump())
        self._validate_dispatch_provenance(dispatch)
        self._validate_reservation_provenance(reservation)
        if (
            dispatch.status != "prepared"
            or dispatch.revision != 0
            or dispatch.attempt_count != 0
            or reservation.status != "held"
            or reservation.revision != 0
        ):
            raise PaperStateConflictError(
                "new paper dispatch and reservation must start prepared/held at revision zero"
            )
        if (
            reservation.order_plan_id != dispatch.order_plan_id
            or reservation.idempotency_key != dispatch.idempotency_key
            or reservation.symbol != dispatch.symbol
            or reservation.side != dispatch.side
            or reservation.store_id != dispatch.store_id
            or reservation.session_id != dispatch.session_id
            or reservation.fencing_token != dispatch.fencing_token
            or reservation.account_scope_fingerprint
            != dispatch.account_scope_fingerprint
        ):
            raise PaperStateConflictError(
                "paper reservation identity does not match its dispatch"
            )
        quantity = _require_whole_int(
            dispatch.quantity,
            field_name="paper dispatch quantity",
            migration_error=False,
        )
        limit_price = _require_whole_int(
            dispatch.limit_price,
            field_name="paper dispatch limit price",
            migration_error=False,
        )
        request_notional = quantity * limit_price
        expected_gross_basis = _ceil_nonnegative_krw(
            Decimal(str(dispatch.snapshot_equity))
            - Decimal(str(dispatch.snapshot_cash))
        )
        if reservation.snapshot_gross_exposure_basis_krw != expected_gross_basis:
            raise PaperStateConflictError(
                "paper reservation gross basis does not match its snapshot"
            )
        expected_gross_limit = _floor_nonnegative_krw(
            Decimal(str(dispatch.snapshot_equity))
            - Decimal(dispatch.minimum_cash_reserve_krw or 0)
        )
        if (
            dispatch.minimum_cash_reserve_krw is None
            or dispatch.minimum_cash_reserve_krw
            > _ceil_nonnegative_krw(dispatch.snapshot_equity)
            or reservation.minimum_cash_reserve_krw
            != dispatch.minimum_cash_reserve_krw
            or reservation.gross_exposure_limit_krw != expected_gross_limit
        ):
            raise PaperStateConflictError(
                "paper reservation gross limit does not match its cash reserve evidence"
            )
        if dispatch.side == "buy":
            expected_broker_cash = _floor_nonnegative_krw(
                dispatch.broker_orderable_cash
            )
            expected_broker_quantity = _require_whole_int(
                dispatch.broker_orderable_buy_quantity,
                field_name="paper broker orderable buy quantity",
                positive=False,
                migration_error=False,
            )
            if (
                reservation.kind != "cash_buy"
                or reservation.reserved_cash_krw != request_notional
                or reservation.reserved_gross_exposure_krw != request_notional
                or reservation.reserved_sell_quantity is not None
                or reservation.broker_orderable_cash_basis_krw
                != expected_broker_cash
                or reservation.broker_orderable_buy_quantity_basis
                != expected_broker_quantity
            ):
                raise PaperStateConflictError(
                    "paper buy reservation does not match dispatch notional"
                )
        else:
            expected_orderable_quantity = _require_whole_int(
                dispatch.snapshot_symbol_orderable_quantity,
                field_name="paper snapshot orderable quantity",
                positive=False,
                migration_error=False,
            )
            if (
                reservation.kind != "sell_quantity"
                or reservation.reserved_sell_quantity != quantity
                or reservation.reserved_cash_krw is not None
                or reservation.reserved_gross_exposure_krw != 0
                or reservation.snapshot_orderable_quantity_basis
                != expected_orderable_quantity
            ):
                raise PaperStateConflictError(
                    "paper sell reservation does not match dispatch quantity"
                )
        with self._event_transaction() as guard:
            if self._connection.execute(
                """
                SELECT 1 FROM paper_kill_operations
                WHERE store_id = ? AND status <> 'released'
                LIMIT 1
                """,
                (dispatch.store_id,),
            ).fetchone() is not None:
                raise PaperStateConflictError("paper kill blocks reservation")
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
            existing_dispatch_row = self._connection.execute(
                """
                SELECT order_plan_id, broker_order_id, idempotency_key, store_id, session_id,
                       fencing_token, status, revision, state_json, updated_at
                FROM paper_order_dispatches
                WHERE order_plan_id = ? OR idempotency_key = ? OR broker_order_id = ?
                """,
                (
                    dispatch.order_plan_id,
                    dispatch.idempotency_key,
                    dispatch.broker_order_id,
                ),
            ).fetchone()
            existing_reservation_row = self._connection.execute(
                """
                SELECT reservation_id, order_plan_id, idempotency_key, store_id,
                       session_id, fencing_token, symbol, kind, status, revision,
                       state_json, updated_at
                FROM paper_risk_reservations
                WHERE order_plan_id = ? OR idempotency_key = ?
                """,
                (reservation.order_plan_id, reservation.idempotency_key),
            ).fetchone()
            if (
                existing_dispatch_row is not None
                and existing_dispatch_row["broker_order_id"]
                == dispatch.broker_order_id
                and existing_dispatch_row["idempotency_key"]
                != dispatch.idempotency_key
            ):
                raise PaperStateConflictError(
                    "paper dispatch broker order identity already exists"
                )
            if existing_dispatch_row is not None or existing_reservation_row is not None:
                if existing_dispatch_row is None or existing_reservation_row is None:
                    raise PaperStateConflictError(
                        "paper dispatch and reservation pair is incomplete"
                    )
                existing_dispatch = self._decode_paper_order_dispatch(
                    existing_dispatch_row
                )
                existing_reservation = self._decode_paper_risk_reservation(
                    existing_reservation_row
                )
                if existing_dispatch == dispatch and existing_reservation == reservation:
                    return existing_dispatch, existing_reservation
                raise PaperStateConflictError(
                    "paper dispatch or reservation identity is already bound to different evidence"
                )

            held_rows = self._connection.execute(
                """
                SELECT reservation_id, order_plan_id, idempotency_key, store_id,
                       session_id, fencing_token, symbol, kind, status, revision,
                       state_json, updated_at
                FROM paper_risk_reservations
                WHERE store_id = ? AND status = 'held'
                ORDER BY reservation_id
                """,
                (reservation.store_id,),
            ).fetchall()
            held = [self._decode_paper_risk_reservation(row) for row in held_rows]
            for item in held:
                self._validate_reservation_provenance(item)
            held_cash = sum(item.reserved_cash_krw or 0 for item in held)
            held_gross = sum(item.reserved_gross_exposure_krw for item in held)
            held_sell = sum(
                item.reserved_sell_quantity or 0
                for item in held
                if item.kind == "sell_quantity" and item.symbol == reservation.symbol
            )
            if reservation.kind == "cash_buy":
                cash_basis = reservation.broker_orderable_cash_basis_krw
                buy_quantity_basis = (
                    reservation.broker_orderable_buy_quantity_basis
                )
                if cash_basis is None or buy_quantity_basis is None:
                    raise PaperStateConflictError(
                        "paper buy reservation lacks buying-power basis"
                    )
                if (reservation.reserved_cash_krw or 0) + held_cash > cash_basis:
                    raise PaperRiskReservationRejected(
                        "paper buy exceeds durable cash availability"
                    )
                if quantity > buy_quantity_basis:
                    raise PaperRiskReservationRejected(
                        "paper buy exceeds broker quantity availability"
                    )
                if (
                    reservation.snapshot_gross_exposure_basis_krw
                    + held_gross
                    + reservation.reserved_gross_exposure_krw
                    > reservation.gross_exposure_limit_krw
                ):
                    raise PaperRiskReservationRejected(
                        "paper buy exceeds durable gross exposure availability"
                    )
            else:
                sell_basis = reservation.snapshot_orderable_quantity_basis
                if sell_basis is None:
                    raise PaperStateConflictError(
                        "paper sell reservation lacks orderable quantity basis"
                    )
                if (reservation.reserved_sell_quantity or 0) + held_sell > sell_basis:
                    raise PaperRiskReservationRejected(
                        "paper sell exceeds durable quantity availability"
                    )
            dispatch_key = _paper_aggregate_key(dispatch)
            reservation_key = _paper_aggregate_key(reservation)
            guard.capture_before(reservation_key)
            guard.capture_before(dispatch_key)
            try:
                reservation_cursor = self._connection.execute(
                    """
                    INSERT INTO paper_risk_reservations (
                        reservation_id, order_plan_id, idempotency_key, store_id,
                        session_id, fencing_token, symbol, kind, status, revision,
                        state_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reservation.reservation_id,
                        reservation.order_plan_id,
                        reservation.idempotency_key,
                        reservation.store_id,
                        reservation.session_id,
                        reservation.fencing_token,
                        reservation.symbol,
                        reservation.kind,
                        reservation.status,
                        reservation.revision,
                        self._serialize(reservation),
                        reservation.updated_at.isoformat(),
                    ),
                )
                dispatch_cursor = self._connection.execute(
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
                    "paper dispatch or reservation identity already exists"
                ) from exc
            reservation_state_json = self._serialize(reservation)
            dispatch_state_json = self._serialize(dispatch)
            guard.register_change(
                after=reservation,
                state_json=reservation_state_json,
                rowcount=reservation_cursor.rowcount,
            )
            guard.register_change(
                after=dispatch,
                state_json=dispatch_state_json,
                rowcount=dispatch_cursor.rowcount,
            )
            reservation_candidate = self._prepare_runtime_paper_execution_event(
                before=None,
                after=reservation,
                event_type="RiskReserved",
                source="local_prepare",
                causation_id=None,
                use_stream_causation=False,
            )
            dispatch_candidate = self._prepare_runtime_paper_execution_event(
                before=None,
                after=dispatch,
                event_type="OrderPrepared",
                source="local_prepare",
                causation_id=reservation_candidate[1].event_id,
                use_stream_causation=False,
            )
            self._append_runtime_paper_execution_events(
                guard,
                [reservation_candidate, dispatch_candidate],
            )
        return dispatch, reservation

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
        with self._event_transaction() as guard:
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
            reservation_row = self._load_reservation_row(current.order_plan_id)
            if reservation_row is None:
                raise PaperStateCorruptionError(
                    "paper dispatch lost its risk reservation before claim"
                )
            reservation = self._decode_paper_risk_reservation(reservation_row)
            self._validate_reservation_provenance(reservation)
            if (
                reservation.status != "held"
                or reservation.session_id != current.session_id
                or reservation.fencing_token != current.fencing_token
            ):
                raise PaperStateConflictError(
                    "paper dispatch and reservation claim evidence disagree"
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
            key = _paper_aggregate_key(claimed)
            guard.capture_before(key)
            state_json = self._serialize(claimed)
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
                    state_json,
                    claimed.updated_at.isoformat(),
                    current.order_plan_id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "paper dispatch claim lost its compare-and-swap race"
                )
            guard.register_change(
                after=claimed,
                state_json=state_json,
                rowcount=cursor.rowcount,
            )
            candidate = self._prepare_runtime_paper_execution_event(
                before=current,
                after=claimed,
                event_type="DispatchClaimed",
                source="local_dispatch_claim",
            )
            self._append_runtime_paper_execution_events(guard, [candidate])
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
        with self._event_transaction() as guard:
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
            dispatch_key = _paper_aggregate_key(rebound)
            guard.capture_before(dispatch_key)
            dispatch_state_json = self._serialize(rebound)
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
                    dispatch_state_json,
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
            guard.register_change(
                after=rebound,
                state_json=dispatch_state_json,
                rowcount=cursor.rowcount,
            )
            reservation_row = self._load_reservation_row(current.order_plan_id)
            if reservation_row is None:
                raise PaperStateCorruptionError(
                    "prepared paper dispatch lost its risk reservation"
                )
            reservation = self._decode_paper_risk_reservation(reservation_row)
            self._validate_reservation_provenance(reservation)
            if (
                reservation.status != "held"
                or reservation.session_id != current.session_id
                or reservation.fencing_token != current.fencing_token
            ):
                raise PaperStateConflictError(
                    "prepared dispatch and reservation takeover evidence disagree"
                )
            rebound_reservation = PaperRiskReservation.model_validate(
                reservation.model_copy(
                    update={
                        "session_id": successor.session_id,
                        "fencing_token": successor.fencing_token,
                        "updated_at": taken_over_at,
                        "revision": reservation.revision + 1,
                    }
                ).model_dump()
            )
            reservation_key = _paper_aggregate_key(rebound_reservation)
            guard.capture_before(reservation_key)
            reservation_state_json = self._serialize(rebound_reservation)
            reservation_cursor = self._connection.execute(
                """
                UPDATE paper_risk_reservations
                SET session_id = ?, fencing_token = ?, revision = ?,
                    state_json = ?, updated_at = ?
                WHERE reservation_id = ? AND session_id = ?
                  AND fencing_token = ? AND status = 'held' AND revision = ?
                """,
                (
                    rebound_reservation.session_id,
                    rebound_reservation.fencing_token,
                    rebound_reservation.revision,
                    reservation_state_json,
                    rebound_reservation.updated_at.isoformat(),
                    reservation.reservation_id,
                    reservation.session_id,
                    reservation.fencing_token,
                    reservation.revision,
                ),
            )
            if reservation_cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "prepared reservation changed during fenced takeover"
                )
            guard.register_change(
                after=rebound_reservation,
                state_json=reservation_state_json,
                rowcount=reservation_cursor.rowcount,
            )
            dispatch_candidate = self._prepare_runtime_paper_execution_event(
                before=current,
                after=rebound,
                event_type="DispatchFenceRebound",
                source="local_session_takeover",
            )
            reservation_candidate = self._prepare_runtime_paper_execution_event(
                before=reservation,
                after=rebound_reservation,
                event_type="RiskReservationFenceRebound",
                source="local_session_takeover",
                causation_id=dispatch_candidate[1].event_id,
                use_stream_causation=False,
            )
            self._append_runtime_paper_execution_events(
                guard,
                [dispatch_candidate, reservation_candidate],
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
            dispatch.minimum_cash_reserve_krw,
            dispatch.entry_atr14,
            dispatch.store_id,
            dispatch.session_id,
            dispatch.fencing_token,
            dispatch.data_mode,
            dispatch.broker_environment,
            dispatch.account_scope_fingerprint,
            dispatch.prepared_at,
        )

    def _release_reservation_for_terminal_dispatch(
        self,
        dispatch: PaperOrderDispatch,
        *,
        guard: _PaperEventMutationGuard,
    ) -> tuple[PaperRiskReservation, PaperRiskReservation] | None:
        release = PAPER_RESERVATION_RELEASE_BY_DISPATCH.get(dispatch.status)
        if release is None:
            return None
        row = self._load_reservation_row(dispatch.order_plan_id)
        if row is None:
            raise PaperStateCorruptionError(
                "terminal paper dispatch lost its risk reservation"
            )
        current = self._decode_paper_risk_reservation(row)
        self._validate_reservation_provenance(current)
        target_status, reason = release
        if current.status == target_status:
            return None
        if current.status != "held":
            raise PaperStateConflictError(
                "paper reservation cannot change between terminal release reasons"
            )
        if (
            current.session_id != dispatch.session_id
            or current.fencing_token != dispatch.fencing_token
        ):
            raise PaperStateConflictError(
                "terminal dispatch and reservation fences disagree"
            )
        released = PaperRiskReservation.model_validate(
            current.model_copy(
                update={
                    "status": target_status,
                    "release_reason": reason,
                    "released_at": dispatch.updated_at,
                    "updated_at": dispatch.updated_at,
                    "revision": current.revision + 1,
                }
                ).model_dump()
            )
        key = _paper_aggregate_key(released)
        guard.capture_before(key)
        state_json = self._serialize(released)
        cursor = self._connection.execute(
            """
            UPDATE paper_risk_reservations
            SET status = ?, revision = ?, state_json = ?, updated_at = ?
            WHERE reservation_id = ? AND status = 'held' AND revision = ?
            """,
            (
                released.status,
                released.revision,
                state_json,
                released.updated_at.isoformat(),
                current.reservation_id,
                current.revision,
            ),
        )
        if cursor.rowcount != 1:
            raise PaperStateConflictError(
                "paper reservation changed before terminal release"
            )
        guard.register_change(
            after=released,
            state_json=state_json,
            rowcount=cursor.rowcount,
        )
        return current, released

    def update_paper_order_dispatch(
        self,
        dispatch: PaperOrderDispatch,
        *,
        mutation_origin: PaperMutationOrigin,
    ) -> PaperOrderDispatch:
        """Persist monotonic broker evidence; this method never grants a retry."""

        dispatch = PaperOrderDispatch.model_validate(dispatch.model_dump())
        self._validate_dispatch_provenance(dispatch)
        try:
            source = PAPER_MUTATION_ORIGIN_SOURCES[mutation_origin]
        except (KeyError, TypeError) as exc:
            raise PaperStateConflictError(
                "paper dispatch update requires an explicit mutation origin"
            ) from exc
        with self._event_transaction() as guard:
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
            if (
                dispatch.reconciliation_status
                not in PAPER_DISPATCH_RECONCILIATION_TRANSITIONS[
                    existing.reconciliation_status
                ]
            ):
                raise PaperStateConflictError(
                    "paper dispatch reconciliation cannot move backward"
                )
            try:
                event_type = classify_dispatch_event_type(existing, dispatch)
            except ValueError as exc:
                raise PaperStateConflictError(
                    "paper dispatch delta has no canonical runtime event"
                ) from exc
            key = _paper_aggregate_key(dispatch)
            guard.capture_before(key)
            state_json = self._serialize(dispatch)
            cursor = self._connection.execute(
                """
                UPDATE paper_order_dispatches
                SET status = ?, revision = ?, state_json = ?, updated_at = ?
                WHERE order_plan_id = ? AND status = ? AND revision = ?
                """,
                (
                    dispatch.status,
                    dispatch.revision,
                    state_json,
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
            guard.register_change(
                after=dispatch,
                state_json=state_json,
                rowcount=cursor.rowcount,
            )
            released_pair = self._release_reservation_for_terminal_dispatch(
                dispatch,
                guard=guard,
            )
            dispatch_candidate = self._prepare_runtime_paper_execution_event(
                before=existing,
                after=dispatch,
                event_type=event_type,
                source=source,
            )
            candidates = [dispatch_candidate]
            if released_pair is not None:
                before_reservation, released_reservation = released_pair
                try:
                    validate_reservation_event_transition(
                        before_reservation,
                        released_reservation,
                        "RiskReservationReleased",
                    )
                except ValueError as exc:
                    raise PaperStateCorruptionError(
                        "terminal dispatch produced an invalid reservation release"
                    ) from exc
                candidates.append(
                    self._prepare_runtime_paper_execution_event(
                        before=before_reservation,
                        after=released_reservation,
                        event_type="RiskReservationReleased",
                        source=source,
                        causation_id=dispatch_candidate[1].event_id,
                        use_stream_causation=False,
                    )
                )
            self._append_runtime_paper_execution_events(guard, candidates)
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
        candidates: list[
            tuple[_PaperAggregateKey, PaperExecutionEvent, int]
        ] = []
        with self._event_transaction() as guard:
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
                key = _paper_aggregate_key(unknown)
                guard.capture_before(key)
                state_json = self._serialize(unknown)
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
                        state_json,
                        unknown.updated_at.isoformat(),
                        unknown.order_plan_id,
                        existing.revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PaperStateConflictError(
                        "paper dispatch changed during interrupted recovery"
                    )
                guard.register_change(
                    after=unknown,
                    state_json=state_json,
                    rowcount=cursor.rowcount,
                )
                candidates.append(
                    self._prepare_runtime_paper_execution_event(
                        before=existing,
                        after=unknown,
                        event_type="OutcomeUnknown",
                        source="process_recovery",
                    )
                )
                recovered.append(unknown)
            self._append_runtime_paper_execution_events(guard, candidates)
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

    def load_paper_risk_reservation(
        self,
        order_plan_id: str,
    ) -> PaperRiskReservation | None:
        row = self._load_reservation_row(order_plan_id)
        if row is None:
            return None
        reservation = self._decode_paper_risk_reservation(row)
        self._validate_reservation_provenance(reservation)
        return reservation

    def find_paper_risk_reservation_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> PaperRiskReservation | None:
        row = self._connection.execute(
            """
            SELECT reservation_id, order_plan_id, idempotency_key, store_id,
                   session_id, fencing_token, symbol, kind, status, revision,
                   state_json, updated_at
            FROM paper_risk_reservations
            WHERE idempotency_key = ?
            """,
            (idempotency_key.strip(),),
        ).fetchone()
        if row is None:
            return None
        reservation = self._decode_paper_risk_reservation(row)
        self._validate_reservation_provenance(reservation)
        return reservation

    def list_paper_risk_reservations(
        self,
        *,
        held_only: bool = False,
    ) -> list[PaperRiskReservation]:
        where = "WHERE store_id = ? AND status = 'held'" if held_only else "WHERE store_id = ?"
        rows = self._connection.execute(
            f"""
            SELECT reservation_id, order_plan_id, idempotency_key, store_id,
                   session_id, fencing_token, symbol, kind, status, revision,
                   state_json, updated_at
            FROM paper_risk_reservations
            {where}
            ORDER BY updated_at, reservation_id
            """,
            (self._require_paper_store().store_id,),
        ).fetchall()
        reservations = [self._decode_paper_risk_reservation(row) for row in rows]
        for reservation in reservations:
            self._validate_reservation_provenance(reservation)
        return reservations

    def list_unresolved_paper_order_dispatches(self) -> list[PaperOrderDispatch]:
        return [
            dispatch
            for dispatch in self.list_paper_order_dispatches()
            if dispatch.reconciliation_status != "reconciled"
        ]

    def _load_kill_row(self, kill_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT kill_id, store_id, status, revision, state_json, updated_at
            FROM paper_kill_operations
            WHERE kill_id = ?
            """,
            (kill_id.strip(),),
        ).fetchone()

    def load_active_paper_kill_operation(self) -> PaperKillOperation | None:
        provenance = self._require_paper_store()
        row = self._connection.execute(
            """
            SELECT kill_id, store_id, status, revision, state_json, updated_at
            FROM paper_kill_operations
            WHERE store_id = ? AND status <> 'released'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (provenance.store_id,),
        ).fetchone()
        if row is None:
            return None
        operation = self._decode_paper_kill_operation(row)
        self._validate_kill_provenance(operation)
        return operation

    def paper_kill_blocks_submission(self) -> bool:
        return self.load_active_paper_kill_operation() is not None

    def start_paper_kill_operation(
        self,
        *,
        session: PaperExecutionSession,
        reason: str,
        started_at: datetime,
    ) -> PaperKillOperation:
        """Persist the kill fence before any broker inspection or mutation."""

        _require_aware_timestamp(started_at, field_name="started_at")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("paper-kill reason must not be blank")
        with self._transaction():
            self._require_exact_active_session(session, checked_at=started_at)
            active = self.load_active_paper_kill_operation()
            if active is not None:
                if active.status in {"killing", "killed"}:
                    return active
                if started_at <= active.updated_at:
                    raise PaperStateConflictError(
                        "paper-kill resume timestamp must advance durable state"
                    )
                resumed = PaperKillOperation.model_validate(
                    active.model_copy(
                        update={
                            "status": "killing",
                            "reason": normalized_reason,
                            "unresolved_reason_codes": [],
                            "updated_at": started_at,
                            "revision": active.revision + 1,
                        }
                    ).model_dump()
                )
                cursor = self._connection.execute(
                    """
                    UPDATE paper_kill_operations
                    SET status = ?, revision = ?, state_json = ?, updated_at = ?
                    WHERE kill_id = ? AND status = 'recovery_required'
                      AND revision = ?
                    """,
                    (
                        resumed.status,
                        resumed.revision,
                        self._serialize(resumed),
                        resumed.updated_at.isoformat(),
                        active.kill_id,
                        active.revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PaperStateConflictError(
                        "paper-kill state changed during resume"
                    )
                return resumed
            provenance = self._require_paper_store()
            operation = PaperKillOperation(
                store_id=provenance.store_id,
                account_scope_fingerprint=provenance.account_scope_fingerprint,
                reason=normalized_reason,
                requested_at=started_at,
                updated_at=started_at,
            )
            self._connection.execute(
                """
                INSERT INTO paper_kill_operations (
                    kill_id, store_id, status, revision, state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.kill_id,
                    operation.store_id,
                    operation.status,
                    operation.revision,
                    self._serialize(operation),
                    operation.updated_at.isoformat(),
                ),
            )
            return operation

    @staticmethod
    def _kill_immutable_identity(operation: PaperKillOperation) -> tuple[object, ...]:
        return (
            operation.kill_id,
            operation.store_id,
            operation.data_mode,
            operation.broker_environment,
            operation.account_scope_fingerprint,
            operation.requested_at,
        )

    def update_paper_kill_operation(
        self,
        operation: PaperKillOperation,
        *,
        session: PaperExecutionSession,
    ) -> PaperKillOperation:
        operation = PaperKillOperation.model_validate(operation.model_dump())
        self._validate_kill_provenance(operation)
        with self._transaction():
            self._require_exact_active_session(session, checked_at=operation.updated_at)
            row = self._load_kill_row(operation.kill_id)
            if row is None:
                raise PaperStateNotFoundError(
                    f"missing paper kill: {operation.kill_id}"
                )
            existing = self._decode_paper_kill_operation(row)
            if operation == existing:
                return existing
            if self._kill_immutable_identity(operation) != self._kill_immutable_identity(existing):
                raise PaperStateConflictError("paper-kill immutable identity changed")
            if operation.revision != existing.revision + 1:
                raise PaperStateConflictError(
                    "paper-kill revision must advance by exactly one"
                )
            if operation.updated_at <= existing.updated_at:
                raise PaperStateConflictError("paper-kill update timestamp must advance")
            if operation.status not in PAPER_KILL_TRANSITIONS[existing.status]:
                raise PaperStateConflictError(
                    f"invalid paper-kill transition: {existing.status} -> {operation.status}"
                )
            cursor = self._connection.execute(
                """
                UPDATE paper_kill_operations
                SET status = ?, revision = ?, state_json = ?, updated_at = ?
                WHERE kill_id = ? AND status = ? AND revision = ?
                """,
                (
                    operation.status,
                    operation.revision,
                    self._serialize(operation),
                    operation.updated_at.isoformat(),
                    operation.kill_id,
                    existing.status,
                    existing.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "paper-kill state changed before update"
                )
        return operation

    @staticmethod
    def _cancel_immutable_identity(request: PaperCancelRequest) -> tuple[object, ...]:
        return (
            request.kill_id,
            request.order_plan_id,
            request.broker_order_id,
            request.broker_order_reference,
            request.broker_forwarding_order_org_number,
            request.symbol,
            request.side,
            request.cancelable_quantity,
            request.original_limit_price,
            request.exchange_id,
            request.store_id,
            request.data_mode,
            request.broker_environment,
            request.account_scope_fingerprint,
        )

    def create_paper_cancel_request(
        self,
        request: PaperCancelRequest,
        *,
        session: PaperExecutionSession,
    ) -> PaperCancelRequest:
        request = PaperCancelRequest.model_validate(request.model_dump())
        self._validate_cancel_provenance(request)
        try:
            validate_cancel_event_transition(None, request, "CancelPrepared")
        except ValueError as exc:
            raise PaperStateConflictError(
                "new paper cancel request has no canonical create event"
            ) from exc
        with self._event_transaction() as guard:
            self._require_exact_active_session(session, checked_at=request.created_at)
            kill_row = self._load_kill_row(request.kill_id)
            if kill_row is None:
                raise PaperStateNotFoundError(
                    f"missing paper kill: {request.kill_id}"
                )
            operation = self._decode_paper_kill_operation(kill_row)
            if operation.status not in {"killing", "recovery_required"}:
                raise PaperStateConflictError(
                    "paper cancel requires an active blocking kill"
                )
            dispatch = self.load_paper_order_dispatch(request.order_plan_id)
            if dispatch is None or dispatch.status not in {"accepted", "partially_filled"}:
                raise PaperStateConflictError(
                    "paper cancel requires a broker-identified working dispatch"
                )
            if (
                dispatch.broker_order_id != request.broker_order_id
                or dispatch.broker_order_reference != request.broker_order_reference
                or dispatch.broker_forwarding_order_org_number
                != request.broker_forwarding_order_org_number
                or dispatch.symbol != request.symbol
                or dispatch.side != request.side
            ):
                raise PaperStateConflictError(
                    "paper cancel identity does not match its managed dispatch"
                )
            row = self._connection.execute(
                """
                SELECT cancel_id, kill_id, order_plan_id, broker_order_reference,
                       store_id, status, revision, state_json, updated_at
                FROM paper_cancel_requests
                WHERE store_id = ? AND order_plan_id = ?
                  AND broker_order_reference = ?
                """,
                (
                    request.store_id,
                    request.order_plan_id,
                    request.broker_order_reference,
                ),
            ).fetchone()
            if row is not None:
                existing = self._decode_paper_cancel_request(row)
                if existing != request:
                    raise PaperStateConflictError(
                        "paper cancel target already exists with different evidence"
                    )
                return existing
            key = _paper_aggregate_key(request)
            guard.capture_before(key)
            state_json = self._serialize(request)
            cursor = self._connection.execute(
                """
                INSERT INTO paper_cancel_requests (
                    cancel_id, kill_id, order_plan_id, broker_order_reference,
                    store_id, status, revision, state_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.cancel_id,
                    request.kill_id,
                    request.order_plan_id,
                    request.broker_order_reference,
                    request.store_id,
                    request.status,
                    request.revision,
                    state_json,
                    request.updated_at.isoformat(),
                ),
            )
            guard.register_change(
                after=request,
                state_json=state_json,
                rowcount=cursor.rowcount,
            )
            candidate = self._prepare_runtime_paper_execution_event(
                before=None,
                after=request,
                event_type="CancelPrepared",
                source="kill_cancel",
                causation_id=None,
                use_stream_causation=False,
            )
            self._append_runtime_paper_execution_events(guard, [candidate])
        return request

    def _load_cancel_row(self, cancel_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT cancel_id, kill_id, order_plan_id, broker_order_reference,
                   store_id, status, revision, state_json, updated_at
            FROM paper_cancel_requests
            WHERE cancel_id = ?
            """,
            (cancel_id.strip(),),
        ).fetchone()

    def claim_paper_cancel_attempt(
        self,
        cancel_id: str,
        *,
        session: PaperExecutionSession,
        claimed_at: datetime,
    ) -> PaperCancelRequest:
        _require_aware_timestamp(claimed_at, field_name="claimed_at")
        with self._event_transaction() as guard:
            self._require_exact_active_session(session, checked_at=claimed_at)
            row = self._load_cancel_row(cancel_id)
            if row is None:
                raise PaperStateNotFoundError(
                    f"missing paper cancel: {cancel_id.strip()}"
                )
            existing = self._decode_paper_cancel_request(row)
            self._validate_cancel_provenance(existing)
            if existing.status != "prepared" or existing.attempt_count != 0:
                raise PaperStateConflictError(
                    "paper cancel external attempt was already claimed"
                )
            if claimed_at <= existing.updated_at:
                raise PaperStateConflictError(
                    "paper cancel claim timestamp must advance durable state"
                )
            claimed = PaperCancelRequest.model_validate(
                existing.model_copy(
                    update={
                        "status": "cancel_claimed",
                        "attempt_count": 1,
                        "claimed_at": claimed_at,
                        "updated_at": claimed_at,
                        "revision": existing.revision + 1,
                    }
                ).model_dump()
            )
            key = _paper_aggregate_key(claimed)
            guard.capture_before(key)
            state_json = self._serialize(claimed)
            cursor = self._connection.execute(
                """
                UPDATE paper_cancel_requests
                SET status = ?, revision = ?, state_json = ?, updated_at = ?
                WHERE cancel_id = ? AND status = 'prepared' AND revision = ?
                """,
                (
                    claimed.status,
                    claimed.revision,
                    state_json,
                    claimed.updated_at.isoformat(),
                    claimed.cancel_id,
                    existing.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "paper cancel claim lost its compare-and-swap race"
                )
            guard.register_change(
                after=claimed,
                state_json=state_json,
                rowcount=cursor.rowcount,
            )
            candidate = self._prepare_runtime_paper_execution_event(
                before=existing,
                after=claimed,
                event_type="CancelClaimed",
                source="kill_cancel",
            )
            self._append_runtime_paper_execution_events(guard, [candidate])
        return claimed

    def update_paper_cancel_request(
        self,
        request: PaperCancelRequest,
        *,
        session: PaperExecutionSession,
        mutation_origin: PaperMutationOrigin,
    ) -> PaperCancelRequest:
        request = PaperCancelRequest.model_validate(request.model_dump())
        self._validate_cancel_provenance(request)
        if mutation_origin != "kill_cancel_journal":
            raise PaperStateConflictError(
                "paper cancel update requires kill-cancel journal authority"
            )
        with self._event_transaction() as guard:
            self._require_exact_active_session(session, checked_at=request.updated_at)
            row = self._load_cancel_row(request.cancel_id)
            if row is None:
                raise PaperStateNotFoundError(
                    f"missing paper cancel: {request.cancel_id}"
                )
            existing = self._decode_paper_cancel_request(row)
            if request == existing:
                return existing
            if self._cancel_immutable_identity(request) != self._cancel_immutable_identity(existing):
                raise PaperStateConflictError("paper cancel immutable identity changed")
            if request.revision != existing.revision + 1:
                raise PaperStateConflictError(
                    "paper cancel revision must advance by exactly one"
                )
            if request.updated_at <= existing.updated_at:
                raise PaperStateConflictError("paper cancel update timestamp must advance")
            if request.attempt_count != existing.attempt_count:
                raise PaperStateConflictError(
                    "only claim_paper_cancel_attempt may change attempt count"
                )
            if request.claimed_at != existing.claimed_at:
                raise PaperStateConflictError("paper cancel claim evidence is immutable")
            if request.status not in PAPER_CANCEL_TRANSITIONS[existing.status]:
                raise PaperStateConflictError(
                    f"invalid paper-cancel transition: {existing.status} -> {request.status}"
                )
            try:
                event_type = CANCEL_STATUS_EVENT_TYPES[request.status]
                validate_cancel_event_transition(existing, request, event_type)
            except (KeyError, ValueError) as exc:
                raise PaperStateConflictError(
                    "paper cancel delta has no canonical runtime event"
                ) from exc
            key = _paper_aggregate_key(request)
            guard.capture_before(key)
            state_json = self._serialize(request)
            cursor = self._connection.execute(
                """
                UPDATE paper_cancel_requests
                SET status = ?, revision = ?, state_json = ?, updated_at = ?
                WHERE cancel_id = ? AND status = ? AND revision = ?
                """,
                (
                    request.status,
                    request.revision,
                    state_json,
                    request.updated_at.isoformat(),
                    request.cancel_id,
                    existing.status,
                    existing.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperStateConflictError(
                    "paper cancel state changed before update"
                )
            guard.register_change(
                after=request,
                state_json=state_json,
                rowcount=cursor.rowcount,
            )
            candidate = self._prepare_runtime_paper_execution_event(
                before=existing,
                after=request,
                event_type=event_type,
                source="kill_cancel",
            )
            self._append_runtime_paper_execution_events(guard, [candidate])
        return request

    def load_paper_cancel_request(self, cancel_id: str) -> PaperCancelRequest | None:
        row = self._load_cancel_row(cancel_id)
        if row is None:
            return None
        request = self._decode_paper_cancel_request(row)
        self._validate_cancel_provenance(request)
        return request

    def list_paper_cancel_requests(
        self,
        *,
        kill_id: str | None = None,
    ) -> list[PaperCancelRequest]:
        query = """
            SELECT cancel_id, kill_id, order_plan_id, broker_order_reference,
                   store_id, status, revision, state_json, updated_at
            FROM paper_cancel_requests
            WHERE store_id = ?
        """
        params: tuple[object, ...] = (self._require_paper_store().store_id,)
        if kill_id is not None:
            query += " AND kill_id = ?"
            params += (kill_id.strip(),)
        query += " ORDER BY updated_at, cancel_id"
        rows = self._connection.execute(query, params).fetchall()
        requests = [self._decode_paper_cancel_request(row) for row in rows]
        for request in requests:
            self._validate_cancel_provenance(request)
        return requests

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
