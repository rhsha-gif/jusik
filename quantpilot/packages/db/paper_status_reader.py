"""Secret-free, read-only projection reader for the KIS paper state store."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sqlite3

from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    OperatorCycleClaim,
    OperatorSafetyState,
    PaperExecutionSession,
    PaperOrderDispatch,
    PendingLiquidationCheckpoint,
    StateStoreProvenance,
    StrategyOperatorState,
)
from quantpilot.packages.core.operator.status_snapshot import (
    ProfessionalOperatorStatusSnapshot,
    build_professional_operator_status,
    unavailable_professional_operator_status,
)
from quantpilot.packages.db.sqlite_repositories import PAPER_STATE_SCHEMA_VERSION


_REQUIRED_TABLES = frozenset(
    {
        "state_store_metadata",
        "managed_positions",
        "strategy_operator_states",
        "pending_liquidations",
        "operator_cycle_claims",
        "operator_safety_states",
        "paper_execution_sessions",
        "paper_order_dispatches",
    }
)


class PaperStatusReaderError(RuntimeError):
    """Fail-closed reader error containing no path, account, or row payload."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class PaperStatusReader:
    """Read one consistent SQLite snapshot without creating or mutating it."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = database_path

    def read_strict(
        self,
        *,
        observed_at: datetime,
        stale_after_seconds: int = 180,
    ) -> ProfessionalOperatorStatusSnapshot:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a UTC offset")
        if isinstance(stale_after_seconds, bool) or stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")

        path = self._configured_path()
        connection = self._open_read_only(path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise PaperStatusReaderError("paper_state_database_unavailable")
            connection.execute("BEGIN")

            user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if user_version != PAPER_STATE_SCHEMA_VERSION:
                raise PaperStatusReaderError("paper_state_schema_mismatch")
            self._require_tables(connection)
            provenance = self._read_provenance(connection)

            safety_states = self._read_safety_states(connection)
            positions = self._read_positions(connection)
            strategy_states = self._read_strategy_states(connection)
            pending_liquidations = self._read_pending_liquidations(connection)
            cycle_claims = self._read_cycle_claims(connection)
            sessions = self._read_sessions(connection, provenance=provenance)
            dispatches = self._read_dispatches(
                connection,
                provenance=provenance,
                sessions=sessions,
            )

            return build_professional_operator_status(
                observed_at=observed_at,
                provenance=provenance,
                safety_states=safety_states,
                positions=positions,
                strategy_states=strategy_states,
                cycle_claims=cycle_claims,
                sessions=sessions,
                dispatches=dispatches,
                pending_liquidations=pending_liquidations,
                stale_after_seconds=stale_after_seconds,
            )
        except PaperStatusReaderError:
            raise
        except sqlite3.Error as exc:
            raise PaperStatusReaderError(_sqlite_reason_code(exc)) from None
        except (KeyError, TypeError, ValueError):
            raise PaperStatusReaderError("paper_state_corrupt") from None
        finally:
            connection.close()

    def _configured_path(self) -> Path:
        configured = self._database_path
        if configured is None:
            configured = os.environ.get("KIS_PAPER_STATE_DB")
        if configured is None or not str(configured).strip():
            raise PaperStatusReaderError("paper_state_path_unset")
        path = Path(configured)
        if not path.is_absolute():
            raise PaperStatusReaderError("paper_state_path_invalid")
        try:
            if not path.is_file():
                raise PaperStatusReaderError("paper_state_database_missing")
        except OSError:
            raise PaperStatusReaderError("paper_state_database_unavailable") from None
        return path

    @staticmethod
    def _open_read_only(path: Path) -> sqlite3.Connection:
        try:
            return sqlite3.connect(
                f"{path.as_uri()}?mode=ro",
                uri=True,
                timeout=0.0,
                isolation_level=None,
            )
        except sqlite3.Error as exc:
            raise PaperStatusReaderError(_sqlite_reason_code(exc)) from None
        except (OSError, ValueError):
            raise PaperStatusReaderError("paper_state_database_unavailable") from None

    @staticmethod
    def _require_tables(connection: sqlite3.Connection) -> None:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not _REQUIRED_TABLES.issubset(names):
            raise PaperStatusReaderError("paper_state_corrupt")

    @staticmethod
    def _read_provenance(connection: sqlite3.Connection) -> StateStoreProvenance:
        rows = connection.execute(
            """
            SELECT singleton_id, store_id, schema_version, data_mode,
                   broker_environment, account_scope_fingerprint,
                   state_json, created_at
            FROM state_store_metadata
            """
        ).fetchall()
        if len(rows) != 1:
            raise PaperStatusReaderError("paper_state_corrupt")
        row = rows[0]
        if row["schema_version"] != PAPER_STATE_SCHEMA_VERSION:
            raise PaperStatusReaderError("paper_state_schema_mismatch")
        provenance = StateStoreProvenance.model_validate_json(row["state_json"])
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
            provenance.store_id,
            provenance.schema_version,
            provenance.data_mode,
            provenance.broker_environment,
            provenance.account_scope_fingerprint,
            provenance.created_at.isoformat(),
        )
        if metadata != expected:
            raise PaperStatusReaderError("paper_state_corrupt")
        if (
            provenance.schema_version != PAPER_STATE_SCHEMA_VERSION
            or provenance.data_mode != "paper_trading"
            or provenance.broker_environment != "kis_paper"
        ):
            raise PaperStatusReaderError("paper_state_provenance_mismatch")
        return provenance

    @staticmethod
    def _read_safety_states(
        connection: sqlite3.Connection,
    ) -> list[OperatorSafetyState]:
        models: list[OperatorSafetyState] = []
        for row in connection.execute(
            "SELECT policy_id, state_json, updated_at FROM operator_safety_states "
            "ORDER BY policy_id"
        ):
            model = OperatorSafetyState.model_validate_json(row["state_json"])
            if (row["policy_id"], row["updated_at"]) != (
                model.policy_id,
                model.updated_at.isoformat(),
            ):
                raise PaperStatusReaderError("paper_state_corrupt")
            models.append(model)
        return models

    @staticmethod
    def _read_positions(
        connection: sqlite3.Connection,
    ) -> list[ManagedPositionState]:
        models: list[ManagedPositionState] = []
        for row in connection.execute(
            """
            SELECT policy_id, strategy_id, strategy_version, symbol,
                   state_json, updated_at
            FROM managed_positions
            ORDER BY policy_id, strategy_id, strategy_version, symbol
            """
        ):
            model = ManagedPositionState.model_validate_json(row["state_json"])
            if (
                row["policy_id"],
                row["strategy_id"],
                row["strategy_version"],
                row["symbol"],
                row["updated_at"],
            ) != (*model.storage_key, model.updated_at.isoformat()):
                raise PaperStatusReaderError("paper_state_corrupt")
            models.append(model)
        return models

    @staticmethod
    def _read_strategy_states(
        connection: sqlite3.Connection,
    ) -> list[StrategyOperatorState]:
        models: list[StrategyOperatorState] = []
        for row in connection.execute(
            """
            SELECT policy_id, strategy_id, strategy_version, state_json, updated_at
            FROM strategy_operator_states
            ORDER BY policy_id, strategy_id, strategy_version
            """
        ):
            model = StrategyOperatorState.model_validate_json(row["state_json"])
            if (
                row["policy_id"],
                row["strategy_id"],
                row["strategy_version"],
                row["updated_at"],
            ) != (*model.storage_key, model.updated_at.isoformat()):
                raise PaperStatusReaderError("paper_state_corrupt")
            models.append(model)
        return models

    @staticmethod
    def _read_pending_liquidations(
        connection: sqlite3.Connection,
    ) -> list[PendingLiquidationCheckpoint]:
        models: list[PendingLiquidationCheckpoint] = []
        for row in connection.execute(
            """
            SELECT order_plan_id, idempotency_key, policy_id, strategy_id,
                   strategy_version, symbol, state_json, updated_at
            FROM pending_liquidations
            ORDER BY order_plan_id
            """
        ):
            model = PendingLiquidationCheckpoint.model_validate_json(
                row["state_json"]
            )
            if (
                row["order_plan_id"],
                row["idempotency_key"],
                row["policy_id"],
                row["strategy_id"],
                row["strategy_version"],
                row["symbol"],
                row["updated_at"],
            ) != (
                model.order_plan_id,
                model.idempotency_key,
                model.policy_id,
                model.strategy_id,
                model.strategy_version,
                model.symbol,
                model.updated_at.isoformat(),
            ):
                raise PaperStatusReaderError("paper_state_corrupt")
            models.append(model)
        return models

    @staticmethod
    def _read_cycle_claims(
        connection: sqlite3.Connection,
    ) -> list[OperatorCycleClaim]:
        models: list[OperatorCycleClaim] = []
        for row in connection.execute(
            """
            SELECT policy_id, strategy_id, strategy_version, cycle_kind,
                   bucket, state_json
            FROM operator_cycle_claims
            ORDER BY policy_id, strategy_id, strategy_version, cycle_kind, bucket
            """
        ):
            model = OperatorCycleClaim.model_validate_json(row["state_json"])
            metadata = (
                row["policy_id"],
                row["strategy_id"],
                row["strategy_version"],
                row["cycle_kind"],
                row["bucket"],
            )
            if metadata != model.storage_key:
                raise PaperStatusReaderError("paper_state_corrupt")
            models.append(model)
        return models

    @staticmethod
    def _read_sessions(
        connection: sqlite3.Connection,
        *,
        provenance: StateStoreProvenance,
    ) -> list[PaperExecutionSession]:
        models: list[PaperExecutionSession] = []
        for row in connection.execute(
            """
            SELECT session_id, store_id, fencing_token, status, state_json, updated_at
            FROM paper_execution_sessions
            ORDER BY fencing_token, session_id
            """
        ):
            model = PaperExecutionSession.model_validate_json(row["state_json"])
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
                raise PaperStatusReaderError("paper_state_corrupt")
            if not _matches_provenance(model, provenance):
                raise PaperStatusReaderError("paper_state_provenance_mismatch")
            models.append(model)
        return models

    @staticmethod
    def _read_dispatches(
        connection: sqlite3.Connection,
        *,
        provenance: StateStoreProvenance,
        sessions: list[PaperExecutionSession],
    ) -> list[PaperOrderDispatch]:
        session_keys = {
            (item.session_id, item.store_id, item.fencing_token) for item in sessions
        }
        models: list[PaperOrderDispatch] = []
        for row in connection.execute(
            """
            SELECT order_plan_id, broker_order_id, idempotency_key, store_id,
                   session_id, fencing_token, status, revision, state_json, updated_at
            FROM paper_order_dispatches
            ORDER BY updated_at, order_plan_id
            """
        ):
            model = PaperOrderDispatch.model_validate_json(row["state_json"])
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
                raise PaperStatusReaderError("paper_state_corrupt")
            if not _matches_provenance(model, provenance):
                raise PaperStatusReaderError("paper_state_provenance_mismatch")
            if (model.session_id, model.store_id, model.fencing_token) not in session_keys:
                raise PaperStatusReaderError("paper_state_corrupt")
            models.append(model)
        return models


def _matches_provenance(
    model: PaperExecutionSession | PaperOrderDispatch,
    provenance: StateStoreProvenance,
) -> bool:
    return (
        model.store_id == provenance.store_id
        and model.data_mode == provenance.data_mode
        and model.broker_environment == provenance.broker_environment
        and model.account_scope_fingerprint == provenance.account_scope_fingerprint
    )


def _sqlite_reason_code(exc: sqlite3.Error) -> str:
    error_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(error_code, int):
        base_code = error_code & 0xFF
        if base_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return "paper_state_database_locked"
        if base_code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
            return "paper_state_corrupt"
    return "paper_state_database_unavailable"


def read_professional_operator_status(
    *,
    observed_at: datetime,
    stale_after_seconds: int = 180,
    database_path: str | Path | None = None,
) -> ProfessionalOperatorStatusSnapshot:
    """Return a typed unavailable projection instead of leaking reader failures."""

    try:
        return PaperStatusReader(database_path).read_strict(
            observed_at=observed_at,
            stale_after_seconds=stale_after_seconds,
        )
    except PaperStatusReaderError as exc:
        return unavailable_professional_operator_status(
            observed_at=observed_at,
            reason_code=exc.reason_code,
            stale_after_seconds=stale_after_seconds,
        )
