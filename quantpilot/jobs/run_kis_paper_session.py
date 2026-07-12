"""One-shot, fail-closed KIS paper operator session.

Run this command from a scheduler at one-minute cadence.  It never enables
itself, never accepts fixture OHLCV, never targets a live broker, and performs
no automatic retry.  Credentials are read only after every non-secret safety
gate and the explicitly approved KRX session window have passed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quantpilot.packages.brokers.kis_paper import (
    KisPaperBrokerAdapter,
    SecurityMetadataSectorProvider,
)
from quantpilot.packages.core.data.providers import (
    MarketDataProvider,
    SecurityProvider,
    build_providers_from_env,
)
from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperBrokerReconciler,
)
from quantpilot.packages.core.execution.paper_reconciliation_apply import (
    PaperReconciliationApplier,
)
from quantpilot.packages.core.execution.paper_submission import (
    DurablePaperSubmissionCoordinator,
)
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.kis_paper import KisPaperClient, KisPaperConfig
from quantpilot.packages.core.marketdata.kis_paper import (
    KisPaperMarketDataProvider,
)
from quantpilot.packages.core.marketdata.paper_session import (
    ExplicitPaperTradingSessionAuthority,
)
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.paper_loss import (
    PersistentPaperPortfolioLossProvider,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperExecutionSession,
    PaperOrderDispatch,
    PendingLiquidationCheckpoint,
)
from quantpilot.packages.core.operator.schemas import OperatorRunRequest
from quantpilot.packages.core.operator.service import OperatorService
from quantpilot.packages.core.risk.position_exit import PositionRiskInput
from quantpilot.packages.core.schemas import (
    BrokerMode,
    DataMode,
    ExecutionMode,
    OrderPlan,
    OrderType,
    PortfolioSnapshot,
    StrategyRecipe,
    UserPolicy,
    utc_now,
)
from quantpilot.packages.core.signals.pullback_trend import (
    PullbackBar,
    PullbackSignalInput,
    PullbackTrendParameters,
    build_pullback_indicators,
)
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.promotion import (
    PROMOTION_CONFIRMATION,
    PROMOTION_LADDER,
    REQUIRED_EVIDENCE,
    StrategyLifecycleRecord,
    StrategyLifecycleStatus,
    compute_spec_hash,
)
from quantpilot.packages.core.strategies.registry import (
    StrategyRegistry,
    StrategyRegistryEntry,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


KST = ZoneInfo("Asia/Seoul")
_MAX_POLICY_BYTES = 1_000_000
_SAFE_CONTINUE_POSITION_STATUSES = {
    "no_action",
    "not_due",
    "duplicate_cycle",
    "reconciled",
}


class PaperSessionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class KisPaperSessionConfig:
    database_path: Path
    policy_path: Path
    registry_path: Path
    approved_business_date: date
    lease_seconds: int
    historical_data_mode: str
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    account_number: str = field(repr=False)
    product_code: str = field(repr=False)
    access_token: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "KisPaperSessionConfig":
        env = environment or os.environ
        reason = paper_session_gate_reason(env)
        if reason is not None:
            raise PaperSessionError(reason)
        try:
            database_path = Path(_required(env, "KIS_PAPER_STATE_DB")).expanduser()
            policy_path = Path(_required(env, "KIS_PAPER_POLICY_FILE")).expanduser()
            registry_path = Path(
                _required(env, "KIS_PAPER_REGISTRY_FILE")
            ).expanduser()
            approved_date = date.fromisoformat(
                _required(env, "KIS_PAPER_APPROVED_BUSINESS_DATE")
            )
            lease_seconds = int(env.get("KIS_PAPER_SESSION_LEASE_SECONDS", "300"))
            historical_data_mode = env.get("DATA_MODE", "fixture").strip().lower()
            app_key = _required(env, "KIS_PAPER_APP_KEY")
            app_secret = _required(env, "KIS_PAPER_APP_SECRET")
            account_number = _required(env, "KIS_PAPER_ACCOUNT_NUMBER")
            product_code = _required(env, "KIS_PAPER_PRODUCT_CODE")
            access_token = _required(env, "KIS_PAPER_ACCESS_TOKEN")
        except (TypeError, ValueError):
            raise PaperSessionError("paper_session_configuration_invalid") from None
        if (
            str(database_path) == ":memory:"
            or not database_path.is_absolute()
            or not policy_path.is_absolute()
            or not registry_path.is_absolute()
            or not 60 <= lease_seconds <= 900
        ):
            raise PaperSessionError("paper_session_configuration_invalid")
        return cls(
            database_path=database_path,
            policy_path=policy_path,
            registry_path=registry_path,
            approved_business_date=approved_date,
            lease_seconds=lease_seconds,
            historical_data_mode=historical_data_mode,
            app_key=app_key,
            app_secret=app_secret,
            account_number=account_number,
            product_code=product_code,
            access_token=access_token,
        )


@dataclass
class KisPaperSessionRuntime:
    config: KisPaperSessionConfig
    store: PaperStateStore
    session: PaperExecutionSession
    coordinator: DurablePaperSubmissionCoordinator
    reconciler: PaperBrokerReconciler
    applier: PaperReconciliationApplier
    broker: KisPaperBrokerAdapter
    market_data: KisPaperMarketDataProvider
    historical_market_data: MarketDataProvider
    operator: OperatorService
    policy: UserPolicy


@dataclass(frozen=True)
class PaperSessionCycleResult:
    status: str
    reason_code: str
    expired_pre_dispatch_order_plan_ids: tuple[str, ...] = ()
    reconciliation_pending_order_plan_ids: tuple[str, ...] = ()
    reconciliation_blocked_order_plan_ids: tuple[str, ...] = ()
    applied_order_plan_ids: tuple[str, ...] = ()
    new_fill_ids: tuple[str, ...] = ()
    position_cycle_status: str | None = None
    operator_run_id: str | None = None
    operator_status: str | None = None


def paper_session_gate_reason(environment: Mapping[str, str]) -> str | None:
    if _flag(environment, "KIS_PAPER_SESSION_ENABLED") is not True:
        return "paper_session_disabled"
    if _flag(environment, "KIS_PAPER_ORDER_SUBMISSION_ENABLED") is not True:
        return "paper_order_submission_gate_disabled"
    if _flag(environment, "FULLY_AUTOMATED_OPERATOR_ENABLED") is not True:
        return "level5_flag_disabled"
    for name, reason in (
        ("LIVE_TRADING_ENABLED", "live_trading_flag_engaged"),
        ("MARKET_ORDERS_ENABLED", "market_orders_flag_engaged"),
        ("GUARDED_AUTOPILOT_ENABLED", "guarded_autopilot_flag_engaged"),
    ):
        if environment.get(name, "false").strip().lower() != "false":
            return reason
    if environment.get("BROKER_MODE", "mock").strip().lower() != "paper":
        return "paper_broker_mode_required"
    return _paper_historical_data_gate_reason(
        environment.get("DATA_MODE", "fixture")
    )


def _paper_historical_data_gate_reason(data_mode: str) -> str | None:
    normalized = str(data_mode).strip().lower()
    if normalized == "external_historical":
        return "paper_external_historical_origin_not_hardened"
    if normalized != "local_historical":
        return "paper_historical_data_mode_required"
    return None


def load_explicit_paper_policy(path: Path) -> UserPolicy:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_POLICY_BYTES:
            raise ValueError
        payload = json.loads(path.read_text(encoding="utf-8"))
        policy = UserPolicy.model_validate(payload)
    except Exception:
        raise PaperSessionError("paper_policy_invalid") from None
    if (
        policy.broker != BrokerMode.paper
        or policy.authority_level != 5
        or policy.execution_mode != ExecutionMode.fully_automated
        or not policy.fully_automated_operator_enabled
        or policy.guarded_autopilot_enabled
        or policy.kill_switch_engaged
        or policy.allowed_order_types != [OrderType.limit]
    ):
        raise PaperSessionError("paper_policy_not_explicitly_promoted")
    return policy


def load_explicit_paper_registry(
    path: Path,
    *,
    policy_version: int,
) -> StrategyRegistry:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_POLICY_BYTES:
            raise ValueError
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {
            "entries",
            "lifecycle_records",
        }:
            raise ValueError
        entries = [
            StrategyRegistryEntry.model_validate(item)
            for item in payload["entries"]
        ]
        lifecycle_records = [
            StrategyLifecycleRecord.model_validate(item)
            for item in payload["lifecycle_records"]
        ]
        registry = StrategyRegistry(
            entries,
            lifecycle_records=lifecycle_records,
        )
    except Exception:
        raise PaperSessionError("paper_strategy_registry_invalid") from None
    selection = registry.select_for_level5(policy_version=policy_version)
    if (
        selection.selected_strategy_id != "pullback_trend_v2"
        or selection.eligible_strategy_ids != ["pullback_trend_v2"]
    ):
        raise PaperSessionError("paper_strategy_not_explicitly_promoted")
    entry = registry.require("pullback_trend_v2")
    recipe = load_strategy_recipe(entry.strategy_id)
    matching = [
        item
        for item in lifecycle_records
        if item.strategy_id == entry.strategy_id
        and item.version == entry.version
        and item.spec_hash == entry.spec_hash
    ]
    if (
        recipe.version != entry.version
        or entry.spec_hash != compute_spec_hash(recipe)
        or len(matching) != 1
        or matching[0].status != StrategyLifecycleStatus.live_candidate
        or not {"paper_track_record", "risk_review"}.issubset(
            matching[0].evidence_kinds()
        )
        or not _promotion_history_is_complete(matching[0])
    ):
        raise PaperSessionError("paper_strategy_evidence_incomplete")
    return registry


def _promotion_history_is_complete(record: StrategyLifecycleRecord) -> bool:
    """Revalidate a serialized lifecycle against PromotionService rules."""

    current = StrategyLifecycleStatus.draft
    for transition in record.history:
        expected_target = PROMOTION_LADDER.get(current)
        if (
            expected_target is None
            or transition.from_status != current
            or transition.to_status != expected_target
            or transition.confirmation != PROMOTION_CONFIRMATION
            or not transition.confirmed_by.strip()
        ):
            return False
        required_kinds = REQUIRED_EVIDENCE.get(
            expected_target,
            frozenset(),
        )
        expected_evidence_ids = [
            evidence.evidence_id
            for evidence in record.evidence
            if evidence.kind in required_kinds
            and evidence.recorded_at <= transition.promoted_at
        ]
        if transition.evidence_ids != expected_evidence_ids:
            return False
        current = expected_target
    return bool(record.history) and current == record.status


def build_runtime(
    config: KisPaperSessionConfig,
    *,
    evaluated_at: datetime,
    provider_builder: Callable[
        [], tuple[SecurityProvider, MarketDataProvider]
    ] = build_providers_from_env,
    client_builder: Callable[[KisPaperSessionConfig], KisPaperClient] | None = None,
) -> KisPaperSessionRuntime:
    historical_gate_reason = _paper_historical_data_gate_reason(
        config.historical_data_mode
    )
    if historical_gate_reason is not None:
        raise PaperSessionError(historical_gate_reason)
    authority = ExplicitPaperTradingSessionAuthority(
        config.approved_business_date
    )
    if authority.current_open_session_date(evaluated_at) is None:
        raise PaperSessionError("paper_session_not_authorized_now")
    policy = load_explicit_paper_policy(config.policy_path)
    registry = load_explicit_paper_registry(
        config.registry_path,
        policy_version=policy.version,
    )
    security_provider, historical_market_data = provider_builder()
    client = (
        client_builder(config)
        if client_builder is not None
        else _authenticated_client(config)
    )
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
            lease_expires_at=evaluated_at
            + timedelta(seconds=config.lease_seconds),
        )
        market_data = KisPaperMarketDataProvider(
            client,
            session_authority=authority,
            clock=utc_now,
            max_age_seconds=policy.stale_quote_max_age_seconds,
        )
        coordinator = DurablePaperSubmissionCoordinator(
            store=store,
            session=session,
            client=client,
            session_authority=authority,
            clock=utc_now,
        )
        loss_provider = PersistentPaperPortfolioLossProvider(
            store,
            session_authority=authority,
            clock=utc_now,
            balance_max_age_seconds=policy.stale_quote_max_age_seconds,
        )
        sector_provider = SecurityMetadataSectorProvider(
            security_provider.get_securities()
        )
        broker = KisPaperBrokerAdapter(
            client,
            submission_gateway=coordinator,
            loss_provider=loss_provider,
            sector_provider=sector_provider,
            market_data_provider=market_data,
            clock=utc_now,
            loss_max_age_seconds=policy.stale_quote_max_age_seconds,
        )
        harness = HarnessService(
            security_provider=security_provider,
            market_data_provider=historical_market_data,
            data_mode=DataMode(config.historical_data_mode),
            pending_liquidation_provider=store,
            external_paper_broker=broker,
            paper_submission_coordinator=coordinator,
        )
        harness.operator_safety_state_provider = store
        harness.repositories.policies.add(policy)
        operator = OperatorService(
            harness,
            registry=registry,
            quote_provider=market_data,
            professional_state_store=store,
        )
        return KisPaperSessionRuntime(
            config=config,
            store=store,
            session=session,
            coordinator=coordinator,
            reconciler=PaperBrokerReconciler(
                store=store,
                client=client,
                clock=utc_now,
            ),
            applier=PaperReconciliationApplier(
                repositories=harness.repositories,
                audit=harness.audit,
            ),
            broker=broker,
            market_data=market_data,
            historical_market_data=historical_market_data,
            operator=operator,
            policy=policy,
        )
    except Exception:
        if store is not None:
            if session is not None:
                _close_session_if_owned(store, session, evaluated_at)
            store.close()
        raise


def execute_runtime(
    runtime: KisPaperSessionRuntime,
    *,
    evaluated_at: datetime,
    clock: Callable[[], datetime] = utc_now,
) -> PaperSessionCycleResult:
    expired = runtime.coordinator.expire_stale_prepared_dispatches()
    reconciliation = runtime.reconciler.reconcile_unresolved()
    journal_dispatches = _dispatches_requiring_local_recovery(
        runtime,
        evaluated_at=evaluated_at,
    )
    _hydrate_durable_order_plans(runtime, journal_dispatches)
    applied = runtime.applier.apply(journal_dispatches)
    reconciliation_blocked_ids = set(reconciliation.blocked_order_plan_ids)
    local_only_blocked_ids = (
        set(applied.blocked_order_plan_ids) - reconciliation_blocked_ids
    )
    if local_only_blocked_ids or applied.missing_order_plan_ids:
        return PaperSessionCycleResult(
            status="blocked",
            reason_code="paper_reconciliation_local_state_blocked",
            expired_pre_dispatch_order_plan_ids=tuple(
                item.order_plan_id for item in expired
            ),
            reconciliation_pending_order_plan_ids=(
                reconciliation.pending_order_plan_ids
            ),
            reconciliation_blocked_order_plan_ids=tuple(
                sorted(
                    set(reconciliation.blocked_order_plan_ids)
                    | set(applied.blocked_order_plan_ids)
                    | set(applied.missing_order_plan_ids)
                )
            ),
            applied_order_plan_ids=applied.applied_order_plan_ids,
            new_fill_ids=applied.new_fill_ids,
        )
    if reconciliation_blocked_ids:
        blocked_errors = {
            item.last_error_code
            for item in reconciliation.updated_dispatches
            if item.order_plan_id in reconciliation_blocked_ids
        }
        history_window_only = blocked_errors == {
            "broker_history_window_manual_resolution_required"
        }
        return PaperSessionCycleResult(
            status="blocked",
            reason_code=(
                "paper_broker_history_manual_resolution_required"
                if history_window_only
                else "paper_broker_reconciliation_ambiguous"
            ),
            expired_pre_dispatch_order_plan_ids=tuple(
                item.order_plan_id for item in expired
            ),
            reconciliation_pending_order_plan_ids=(
                reconciliation.pending_order_plan_ids
            ),
            reconciliation_blocked_order_plan_ids=tuple(
                sorted(
                    reconciliation_blocked_ids
                    | set(applied.blocked_order_plan_ids)
                )
            ),
            applied_order_plan_ids=applied.applied_order_plan_ids,
            new_fill_ids=applied.new_fill_ids,
        )

    _synchronize_pending_liquidations(
        runtime,
        journal_dispatches,
    )
    snapshot = runtime.broker.get_positions(runtime.policy.user_id)
    if applied.new_fill_ids:
        _attribute_reconciled_fills(
            runtime,
            journal_dispatches,
            new_fill_ids=set(applied.new_fill_ids),
            snapshot=snapshot,
        )

    selection = runtime.operator.registry.select_for_level5(
        policy_version=runtime.policy.version
    )
    if selection.selected_strategy_id is None:
        return PaperSessionCycleResult(
            status="blocked",
            reason_code="no_level5_strategy_eligible",
        )
    registry_entry = runtime.operator.registry.require(
        selection.selected_strategy_id
    )
    recipe = load_strategy_recipe(registry_entry.strategy_id)
    if recipe.version != registry_entry.version:
        raise PaperSessionError("paper_strategy_registry_mismatch")
    if runtime.operator.repositories.strategies.get(recipe.strategy_id) is None:
        runtime.operator.repositories.strategies.add(recipe)

    risk_inputs, quotes, risk_evaluated_at = _build_position_risk_evidence(
        runtime,
        strategy=recipe,
        minimum_evaluated_at=evaluated_at,
        clock=clock,
    )
    position_cycle = runtime.operator.run_professional_position_cycle(
        policy=runtime.policy,
        registry_entry=registry_entry,
        strategy=recipe,
        snapshot=snapshot,
        risk_inputs=risk_inputs,
        quotes=quotes,
        evaluated_at=risk_evaluated_at,
    )
    if position_cycle.status not in _SAFE_CONTINUE_POSITION_STATUSES:
        return PaperSessionCycleResult(
            status="blocked",
            reason_code=(
                position_cycle.reason_codes[0]
                if position_cycle.reason_codes
                else "paper_position_cycle_blocked"
            ),
            expired_pre_dispatch_order_plan_ids=tuple(
                item.order_plan_id for item in expired
            ),
            reconciliation_pending_order_plan_ids=(
                reconciliation.pending_order_plan_ids
            ),
            applied_order_plan_ids=applied.applied_order_plan_ids,
            new_fill_ids=applied.new_fill_ids,
            position_cycle_status=position_cycle.status,
        )

    operator_at = max(risk_evaluated_at, clock())
    request = _operator_request(runtime.policy, operator_at)
    operator_result = runtime.operator.run_once(request)
    return PaperSessionCycleResult(
        status="completed" if operator_result.status == "completed" else "blocked",
        reason_code=(
            "paper_session_cycle_completed"
            if operator_result.status == "completed"
            else (
                operator_result.fallback.reason_code
                if operator_result.fallback is not None
                else "paper_operator_blocked"
            )
        ),
        expired_pre_dispatch_order_plan_ids=tuple(
            item.order_plan_id for item in expired
        ),
        reconciliation_pending_order_plan_ids=(
            reconciliation.pending_order_plan_ids
        ),
        applied_order_plan_ids=applied.applied_order_plan_ids,
        new_fill_ids=applied.new_fill_ids,
        position_cycle_status=position_cycle.status,
        operator_run_id=operator_result.run_id,
        operator_status=operator_result.status,
    )


def run_from_environment() -> PaperSessionCycleResult:
    config = KisPaperSessionConfig.from_environment()
    evaluated_at = utc_now()
    runtime = build_runtime(config, evaluated_at=evaluated_at)
    try:
        return execute_runtime(runtime, evaluated_at=evaluated_at)
    finally:
        try:
            closed_at = max(
                utc_now(),
                runtime.session.updated_at + timedelta(microseconds=1),
            )
            if closed_at >= runtime.session.lease_expires_at:
                raise PaperSessionError("paper_session_lease_expired")
            runtime.store.close_paper_execution_session(
                runtime.session,
                closed_at=closed_at,
            )
        finally:
            runtime.store.close()


def main() -> int:
    try:
        result = run_from_environment()
    except PaperSessionError as exc:
        print(json.dumps({"status": "blocked", "reason_code": exc.reason_code}))
        return 0 if exc.reason_code == "paper_session_disabled" else 2
    except Exception as exc:
        reason_code = getattr(exc, "reason_code", None)
        if not isinstance(reason_code, str) or not reason_code.startswith(
            "paper_"
        ):
            reason_code = "paper_session_internal_failure"
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason_code": reason_code,
                }
            )
        )
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.status == "completed" else 1


def _authenticated_client(config: KisPaperSessionConfig) -> KisPaperClient:
    client_config = KisPaperConfig(
        app_key=config.app_key,
        app_secret=config.app_secret,
        account_number=config.account_number,
        product_code=config.product_code,
        access_token=config.access_token,
    )
    return KisPaperClient(client_config)


def _build_position_risk_evidence(
    runtime: KisPaperSessionRuntime,
    *,
    strategy: StrategyRecipe,
    minimum_evaluated_at: datetime,
    clock: Callable[[], datetime],
) -> tuple[dict[str, PositionRiskInput], dict[str, Quote], datetime]:
    positions = [
        item
        for item in runtime.store.list_positions()
        if item.policy_id == runtime.policy.policy_id
        and item.strategy_id == strategy.strategy_id
    ]
    if not positions:
        return {}, {}, max(minimum_evaluated_at, clock())
    symbols = sorted({item.symbol for item in positions})
    quote_snapshot = runtime.market_data.get_quotes(symbols)
    if (
        not quote_snapshot.data_quality.usable
        or set(quote_snapshot.quotes) != set(symbols)
    ):
        raise PaperSessionError("paper_position_quote_unavailable")
    if strategy.decision_rules is None:
        raise PaperSessionError("paper_position_rules_missing")
    parameters = PullbackTrendParameters.model_validate(
        strategy.decision_rules.model_dump()
    )
    rows = runtime.historical_market_data.get_price_history()
    evidence_at = max(minimum_evaluated_at, clock())
    local_date = evidence_at.astimezone(KST).date()
    inputs: dict[str, PositionRiskInput] = {}
    for position in positions:
        quote = quote_snapshot.quotes[position.symbol]
        bars = _completed_pullback_bars(
            rows,
            symbol=position.symbol,
            before_date=local_date,
        )
        if not bars:
            raise PaperSessionError("paper_position_history_unavailable")
        signal_date = bars[-1].session_date
        if not 1 <= (local_date - signal_date).days <= 7:
            raise PaperSessionError("paper_position_history_stale")
        indicators = build_pullback_indicators(
            PullbackSignalInput(
                strategy_id=position.strategy_id,
                recipe_version=position.strategy_version,
                symbol=position.symbol,
                signal_date=signal_date,
                bars=bars,
                current_weight=0,
                max_position_weight=runtime.policy.max_position_weight,
                multifactor_score=100,
                quote_price=quote.last,
                quote_as_of=quote.as_of,
                evaluated_at=evidence_at,
            ),
            parameters,
        )
        inputs[position.symbol] = PositionRiskInput(
            strategy_id=position.strategy_id,
            strategy_version=position.strategy_version,
            symbol=position.symbol,
            quantity=position.quantity,
            average_entry_price=position.average_entry_price,
            current_price=quote.last,
            completed_close=indicators.close,
            atr14=position.atr14,
            sma20=indicators.sma20,
            rsi14=indicators.rsi14,
            quote_as_of=quote.as_of,
            evaluated_at=evidence_at,
        )
    return inputs, quote_snapshot.quotes, evidence_at


def _completed_pullback_bars(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    before_date: date,
) -> list[PullbackBar]:
    by_date: dict[date, PullbackBar] = {}
    for row in rows:
        row_symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
        if row_symbol != symbol:
            continue
        session_date = _coerce_date(row.get("date"))
        if session_date >= before_date:
            continue
        bar = PullbackBar(
            symbol=symbol,
            session_date=session_date,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0)),
        )
        existing = by_date.get(session_date)
        if existing is not None and existing != bar:
            raise PaperSessionError("paper_position_history_conflict")
        by_date[session_date] = bar
    return [by_date[key] for key in sorted(by_date)]


def _synchronize_pending_liquidations(
    runtime: KisPaperSessionRuntime,
    dispatches: tuple[PaperOrderDispatch, ...],
) -> None:
    for dispatch in dispatches:
        if dispatch.purpose not in {"protective_exit", "strategy_retirement"}:
            continue
        checkpoint = runtime.store.load_pending_liquidation(
            dispatch.order_plan_id
        )
        if checkpoint is None:
            raise PaperSessionError("paper_liquidation_checkpoint_missing")
        _require_checkpoint_identity(checkpoint, dispatch)
        if dispatch.status not in {
            "accepted",
            "partially_filled",
            "filled",
            "rejected",
            "cancelled",
        }:
            continue
        fills = [
            runtime.operator.repositories.fills.require(
                evidence.broker_fill_reference
            )
            for evidence in dispatch.fill_evidence
        ]
        if (
            checkpoint.status == dispatch.status
            and checkpoint.broker_order_id == dispatch.broker_order_id
            and checkpoint.cumulative_filled_quantity
            == dispatch.cumulative_filled_quantity
            and checkpoint.fill_evidence == fills
        ):
            continue
        updated = PendingLiquidationCheckpoint.model_validate(
            checkpoint.model_copy(
                update={
                    "status": dispatch.status,
                    "broker_submission_attempted": True,
                    "risk_check_id": dispatch.risk_check_id,
                    "broker_order_id": dispatch.broker_order_id,
                    "cumulative_filled_quantity": (
                        dispatch.cumulative_filled_quantity
                    ),
                    "fill_ids": [item.fill_id for item in fills],
                    "fill_evidence": fills,
                    "last_error_code": dispatch.last_error_code,
                    "updated_at": max(
                        dispatch.updated_at,
                        checkpoint.updated_at + timedelta(microseconds=1),
                    ),
                    "revision": checkpoint.revision + 1,
                }
            ).model_dump()
        )
        runtime.store.update_pending_liquidation(updated)


def _dispatches_requiring_local_recovery(
    runtime: KisPaperSessionRuntime,
    *,
    evaluated_at: datetime,
) -> tuple[PaperOrderDispatch, ...]:
    trading_date = evaluated_at.astimezone(KST).date()
    selected: list[PaperOrderDispatch] = []
    for dispatch in runtime.store.list_paper_order_dispatches():
        if dispatch.attempt_count != 1:
            continue
        attempted_today = (
            dispatch.dispatch_claimed_at is not None
            and dispatch.dispatch_claimed_at.astimezone(KST).date()
            == trading_date
        )
        unprocessed_fill = any(
            runtime.store.load_processed_fill(
                evidence.broker_fill_reference
            )
            is None
            for evidence in dispatch.fill_evidence
        )
        pending_liquidation = False
        if dispatch.purpose in {"protective_exit", "strategy_retirement"}:
            checkpoint = runtime.store.load_pending_liquidation(
                dispatch.order_plan_id
            )
            pending_liquidation = (
                checkpoint is None or checkpoint.status != "reconciled"
            )
        if (
            dispatch.reconciliation_status != "reconciled"
            or attempted_today
            or unprocessed_fill
            or pending_liquidation
        ):
            selected.append(dispatch)
    return tuple(sorted(selected, key=lambda item: item.order_plan_id))


def _hydrate_durable_order_plans(
    runtime: KisPaperSessionRuntime,
    dispatches: tuple[PaperOrderDispatch, ...],
) -> None:
    for dispatch in dispatches:
        if dispatch.order_plan_payload is None:
            continue
        try:
            recovered = OrderPlan.model_validate(dispatch.order_plan_payload)
        except ValueError:
            raise PaperSessionError("paper_durable_order_payload_invalid") from None
        existing = runtime.operator.repositories.order_plans.get(
            dispatch.order_plan_id
        )
        if existing is not None:
            if existing != recovered:
                raise PaperSessionError("paper_local_order_recovery_conflict")
            continue
        runtime.operator.repositories.order_plans.add(recovered)
        runtime.operator.audit.emit(
            user_id=dispatch.user_id,
            entity_type="order_plan",
            entity_id=dispatch.order_plan_id,
            action="paper_order_recovered",
            after_state={
                "dispatch_status": dispatch.status,
                "dispatch_revision": dispatch.revision,
            },
            source="paper_session_recovery",
        )


def _attribute_reconciled_fills(
    runtime: KisPaperSessionRuntime,
    dispatches: tuple[PaperOrderDispatch, ...],
    *,
    new_fill_ids: set[str],
    snapshot: PortfolioSnapshot,
) -> None:
    if runtime.operator.professional is None:
        raise PaperSessionError("paper_professional_operator_missing")
    for dispatch in dispatches:
        ids = [
            evidence.broker_fill_reference
            for evidence in dispatch.fill_evidence
            if evidence.broker_fill_reference in new_fill_ids
        ]
        if not ids:
            continue
        order = runtime.operator.repositories.order_plans.get(
            dispatch.order_plan_id
        )
        if order is None:
            raise PaperSessionError("paper_reconciled_order_missing")
        fills = [runtime.operator.repositories.fills.require(item) for item in ids]
        runtime.operator.professional.record_reconciled_fills(
            policy=runtime.policy,
            order=order,
            fills=fills,
            snapshot=snapshot,
            entry_atr14=dispatch.entry_atr14,
        )


def _require_checkpoint_identity(
    checkpoint: PendingLiquidationCheckpoint,
    dispatch: PaperOrderDispatch,
) -> None:
    if (
        checkpoint.order_plan_id != dispatch.order_plan_id
        or checkpoint.policy_id != dispatch.policy_id
        or checkpoint.policy_version != dispatch.policy_version
        or checkpoint.strategy_id != dispatch.strategy_id
        or checkpoint.strategy_version != dispatch.strategy_version
        or checkpoint.symbol != dispatch.symbol
        or checkpoint.purpose != dispatch.purpose
        or checkpoint.idempotency_key != dispatch.idempotency_key
        or checkpoint.quantity_requested != dispatch.quantity
        or checkpoint.limit_price != dispatch.limit_price
        or checkpoint.quote_as_of != dispatch.quote_as_of
        or checkpoint.reconciled_snapshot_id
        != dispatch.reconciled_snapshot_id
    ):
        raise PaperSessionError("paper_liquidation_checkpoint_mismatch")


def _operator_request(
    policy: UserPolicy,
    evaluated_at: datetime,
) -> OperatorRunRequest:
    bucket = evaluated_at.astimezone(timezone.utc).replace(
        second=0,
        microsecond=0,
    )
    bucket_text = bucket.strftime("%Y-%m-%dT%H:%MZ")
    return OperatorRunRequest(
        user_id=policy.user_id,
        policy_id=policy.policy_id,
        requested_policy_version=policy.version,
        run_mode="paper_submit",
        requested_at=bucket,
        idempotency_key=(
            f"kis-paper:{policy.policy_id}:v{policy.version}:{bucket_text}"
        ),
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


def _coerce_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise PaperSessionError("paper_position_history_invalid")


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise PaperSessionError("paper_session_configuration_incomplete")
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
