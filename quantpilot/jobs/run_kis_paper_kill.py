"""Explicit, paper-only cancel-all kill command for QuantPilot-managed orders."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from quantpilot.packages.core.execution.paper_kill import (
    PaperKillResult,
    PaperKillService,
)
from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperBrokerReconciler,
)
from quantpilot.packages.core.execution.paper_submission import (
    DurablePaperSubmissionCoordinator,
)
from quantpilot.packages.core.kis_paper import KisPaperClient, KisPaperConfig
from quantpilot.packages.core.marketdata.paper_session import (
    ExplicitPaperTradingSessionAuthority,
)
from quantpilot.packages.core.operator.position_ledger import PaperExecutionSession
from quantpilot.packages.core.schemas import utc_now
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


ENGAGE_CONFIRMATION = "cancel managed paper orders"
RELEASE_CONFIRMATION = "release paper kill"


class PaperKillJobError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class KisPaperKillConfig:
    database_path: Path
    lease_seconds: int
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    account_number: str = field(repr=False)
    product_code: str = field(repr=False)
    access_token: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        action: str,
        environment: Mapping[str, str] | None = None,
    ) -> "KisPaperKillConfig":
        env = environment or os.environ
        reason = paper_kill_gate_reason(action, env)
        if reason is not None:
            raise PaperKillJobError(reason)
        try:
            database_path = Path(_required(env, "KIS_PAPER_STATE_DB")).expanduser()
            lease_seconds = int(env.get("KIS_PAPER_SESSION_LEASE_SECONDS", "300"))
            config = cls(
                database_path=database_path,
                lease_seconds=lease_seconds,
                app_key=_required(env, "KIS_PAPER_APP_KEY"),
                app_secret=_required(env, "KIS_PAPER_APP_SECRET"),
                account_number=_required(env, "KIS_PAPER_ACCOUNT_NUMBER"),
                product_code=_required(env, "KIS_PAPER_PRODUCT_CODE"),
                access_token=_required(env, "KIS_PAPER_ACCESS_TOKEN"),
            )
        except (TypeError, ValueError):
            raise PaperKillJobError("paper_kill_configuration_invalid") from None
        if (
            str(config.database_path) == ":memory:"
            or not config.database_path.is_absolute()
            or not 60 <= config.lease_seconds <= 900
        ):
            raise PaperKillJobError("paper_kill_configuration_invalid")
        return config


@dataclass
class KisPaperKillRuntime:
    config: KisPaperKillConfig
    store: PaperStateStore
    session: PaperExecutionSession
    service: PaperKillService


def paper_kill_gate_reason(
    action: str,
    environment: Mapping[str, str],
) -> str | None:
    if action not in {"engage", "release"}:
        return "paper_kill_action_invalid"
    if _flag(environment, "KIS_PAPER_KILL_ENABLED") is not True:
        return "paper_kill_disabled"
    confirmation = environment.get("KIS_PAPER_KILL_CONFIRMATION", "").strip()
    expected = ENGAGE_CONFIRMATION if action == "engage" else RELEASE_CONFIRMATION
    if confirmation != expected:
        return "paper_kill_confirmation_required"
    if environment.get("LIVE_TRADING_ENABLED", "false").strip().lower() != "false":
        return "live_trading_flag_engaged"
    if environment.get("MARKET_ORDERS_ENABLED", "false").strip().lower() != "false":
        return "market_orders_flag_engaged"
    if environment.get("BROKER_MODE", "mock").strip().lower() != "paper":
        return "paper_broker_mode_required"
    if environment.get("DATA_MODE", "fixture").strip().lower() != "paper_trading":
        return "paper_data_mode_required"
    return None


def build_runtime(
    config: KisPaperKillConfig,
    *,
    evaluated_at: datetime,
    client_builder: Callable[[KisPaperKillConfig], KisPaperClient] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> KisPaperKillRuntime:
    client = client_builder(config) if client_builder is not None else _client(config)
    store: PaperStateStore | None = None
    session: PaperExecutionSession | None = None
    try:
        store = PaperStateStore(
            config.database_path,
            data_mode="paper_trading",
            broker_environment="kis_paper",
            account_scope_fingerprint=client.account_scope_fingerprint,
        )
        session = store.start_paper_execution_session(
            started_at=evaluated_at,
            lease_expires_at=evaluated_at + timedelta(seconds=config.lease_seconds),
        )
        authority = ExplicitPaperTradingSessionAuthority(
            evaluated_at.astimezone(ZoneInfo("Asia/Seoul")).date()
        )
        coordinator = DurablePaperSubmissionCoordinator(
            store=store,
            session=session,
            client=client,
            session_authority=authority,
            clock=clock,
        )
        reconciler = PaperBrokerReconciler(
            store=store,
            client=client,
            clock=clock,
        )
        return KisPaperKillRuntime(
            config=config,
            store=store,
            session=session,
            service=PaperKillService(
                store=store,
                session=session,
                client=client,
                submission_coordinator=coordinator,
                reconciler=reconciler,
                clock=clock,
            ),
        )
    except Exception:
        if store is not None:
            if session is not None:
                _close_session_if_owned(store, session, evaluated_at)
            store.close()
        raise


def run_from_environment(
    action: str,
    *,
    environment: Mapping[str, str] | None = None,
    client_builder: Callable[[KisPaperKillConfig], KisPaperClient] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> PaperKillResult:
    config = KisPaperKillConfig.from_environment(action, environment)
    evaluated_at = clock()
    runtime = build_runtime(
        config,
        evaluated_at=evaluated_at,
        client_builder=client_builder,
        clock=clock,
    )
    try:
        if action == "engage":
            return runtime.service.engage(reason="operator_requested")
        return runtime.service.release()
    finally:
        try:
            closed_at = max(
                clock(),
                runtime.session.updated_at + timedelta(microseconds=1),
            )
            if closed_at >= runtime.session.lease_expires_at:
                raise PaperKillJobError("paper_kill_session_lease_expired")
            runtime.store.close_paper_execution_session(
                runtime.session,
                closed_at=closed_at,
            )
        finally:
            runtime.store.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("engage", "release"))
    args = parser.parse_args(argv)
    try:
        result = run_from_environment(args.action)
    except PaperKillJobError as exc:
        print(json.dumps({"status": "blocked", "reason_code": exc.reason_code}))
        return 0 if exc.reason_code == "paper_kill_disabled" else 2
    except Exception as exc:
        reason_code = getattr(exc, "reason_code", None)
        if not isinstance(reason_code, str) or not reason_code.startswith("paper_"):
            reason_code = "paper_kill_internal_failure"
        print(json.dumps({"status": "blocked", "reason_code": reason_code}))
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.status in {"killed", "released"} else 1


def _client(config: KisPaperKillConfig) -> KisPaperClient:
    return KisPaperClient(
        KisPaperConfig(
            app_key=config.app_key,
            app_secret=config.app_secret,
            account_number=config.account_number,
            product_code=config.product_code,
            access_token=config.access_token,
        )
    )


def _close_session_if_owned(
    store: PaperStateStore,
    session: PaperExecutionSession,
    at: datetime,
) -> None:
    closed_at = max(at, session.updated_at + timedelta(microseconds=1))
    if closed_at >= session.lease_expires_at:
        return
    try:
        store.close_paper_execution_session(session, closed_at=closed_at)
    except Exception:
        return


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise PaperKillJobError("paper_kill_configuration_incomplete")
    return value


def _flag(environment: Mapping[str, str], name: str) -> bool | None:
    value = environment.get(name, "false").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


if __name__ == "__main__":
    raise SystemExit(main())
