from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from quantpilot.packages.core.execution.fallback_manager import FallbackDecision, FallbackManager
from quantpilot.packages.core.execution.state_machine import (
    ApprovalRequired,
    RiskCheckRequired,
    authorize_level5,
    fully_automated_operator_flag_enabled,
    guarded_autopilot_flag_enabled,
    live_trading_flag_enabled,
    operator_kill_switch_engaged,
    transition_order_plan,
)
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.marketdata.providers import BarOHLCVProvider, BarQuoteProvider, OHLCVProvider, QuoteProvider
from quantpilot.packages.core.marketdata.types import SignalSet
from quantpilot.packages.core.operator.schemas import (
    OperatorDecision,
    OperatorReport,
    OperatorRunRequest,
    OperatorRunResult,
)
from quantpilot.packages.core.operator.position_ledger import (
    OperatorCycleClaim,
    PaperRunCheckpoint,
)
from quantpilot.packages.core.operator.professional_cycle import (
    ProfessionalOperatorCoordinator,
    ProfessionalPositionCycleResult,
    ProfessionalStateStore,
    StrategyHealthReviewResult,
    risk_evaluation_due,
)
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.risk.position_exit import PositionRiskInput
from quantpilot.packages.core.strategies.performance_review import StrategyHealthInput
from quantpilot.packages.core.policy.versioning import PolicyVersionGuard, PolicyVersioningService
from quantpilot.packages.core.risk.gatekeeper import market_orders_enabled
from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    OrderPlan,
    OrderStatus,
    PortfolioSnapshot,
    Signal,
    StrategyRecipe,
    UserPolicy,
    new_id,
    utc_now,
)
from quantpilot.packages.core.signals.service import generate_provider_bound_signals
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.registry import (
    StrategyRegistry,
    StrategySelectionDecision,
    default_strategy_registry,
)


# Maps authorize_level5 check names to deterministic fallback reason codes. Checks that
# do not appear here produce a blocked decision without changing the operator level.
CHECK_TO_FALLBACK_REASON = {
    "fully_automated_operator_enabled": "level5_flag_disabled",
    "live_trading_disabled": "live_trading_flag_engaged",
    "kill_switch_not_engaged": "kill_switch_engaged",
    "broker_mode_safe": "broker_mode_unsafe",
    "authority_level_5": "policy_not_promoted",
    "policy_version_match": "policy_review_required",
    "broker_health": "broker_unhealthy",
    "quote_not_stale": "stale_market_data",
    "snapshot_not_stale": "stale_market_data",
    "order_type_allowed": "market_orders_disabled",
    "monthly_loss_stop_not_triggered": "monthly_loss_stop_engaged",
    "monthly_loss_pause_allows_order": "monthly_loss_pause_engaged",
    "fresh_risk_check_passed": "risk_check_failed",
}


def _run_request_fingerprint(request: OperatorRunRequest) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _empty_selection(reason: str) -> StrategySelectionDecision:
    return StrategySelectionDecision(
        selected_strategy_id=None,
        selected_version=None,
        eligible_strategy_ids=[],
        rejected={},
        reason=reason,
    )


class OperatorService:
    def __init__(
        self,
        harness: HarnessService | None = None,
        registry: StrategyRegistry | None = None,
        *,
        ohlcv_provider: OHLCVProvider | None = None,
        quote_provider: QuoteProvider | None = None,
        professional_state_store: ProfessionalStateStore | None = None,
    ) -> None:
        self.harness = harness or HarnessService()
        self.registry = registry or default_strategy_registry()
        self.ohlcv_provider = ohlcv_provider or BarOHLCVProvider(
            self.harness.market_data_provider,
            provider_name="operator_ohlcv",
        )
        self.quote_provider = quote_provider or BarQuoteProvider(
            self.harness.market_data_provider,
            provider_name="operator_quote",
        )
        self.fallbacks = FallbackManager()
        self.version_guard = PolicyVersionGuard()
        self.policy_versioning = PolicyVersioningService(self.harness.repositories, self.harness.audit)
        self.reports: list[OperatorReport] = []
        self._runs_by_key: dict[str, OperatorRunResult] = {}
        self._run_fingerprints_by_key: dict[str, str] = {}
        self.professional_state_store = professional_state_store
        self.professional = (
            ProfessionalOperatorCoordinator(
                harness=self.harness,
                registry=self.registry,
                state_store=professional_state_store,
            )
            if professional_state_store is not None
            else None
        )

    def review_professional_strategy_health(
        self,
        *,
        policy: UserPolicy,
        registry_entry,
        evidence: StrategyHealthInput,
        performance_record_id: str,
        evaluated_at: datetime,
        reapproved: bool = False,
    ) -> StrategyHealthReviewResult:
        if self.professional is None:
            raise RuntimeError("professional state store is not configured")
        return self.professional.review_strategy_health(
            policy=policy,
            registry_entry=registry_entry,
            evidence=evidence,
            performance_record_id=performance_record_id,
            evaluated_at=evaluated_at,
            reapproved=reapproved,
        )

    def run_professional_position_cycle(
        self,
        *,
        policy: UserPolicy,
        registry_entry,
        strategy: StrategyRecipe,
        snapshot: PortfolioSnapshot,
        risk_inputs: dict[str, PositionRiskInput],
        quotes: dict[str, Quote],
        evaluated_at: datetime,
    ) -> ProfessionalPositionCycleResult:
        if self.professional is None:
            raise RuntimeError("professional state store is not configured")
        return self.professional.run_position_cycle(
            policy=policy,
            registry_entry=registry_entry,
            strategy=strategy,
            snapshot=snapshot,
            risk_inputs=risk_inputs,
            quotes=quotes,
            evaluated_at=evaluated_at,
        )

    @property
    def repositories(self):
        return self.harness.repositories

    @property
    def audit(self):
        return self.harness.audit

    def _safety_flags(self, policy: UserPolicy | None, request: OperatorRunRequest) -> dict[str, bool | str]:
        return {
            "LIVE_TRADING_ENABLED": live_trading_flag_enabled(),
            "GUARDED_AUTOPILOT_ENABLED": guarded_autopilot_flag_enabled(policy) if policy else False,
            "FULLY_AUTOMATED_OPERATOR_ENABLED": fully_automated_operator_flag_enabled(policy),
            "MARKET_ORDERS_ENABLED": market_orders_enabled(),
            "OPERATOR_KILL_SWITCH": operator_kill_switch_engaged(),
            "BROKER_MODE": policy.broker.value if policy else "unknown",
            "kill_switch_engaged": bool(policy.kill_switch_engaged) if policy else False,
            "run_mode": request.run_mode,
        }

    def run_once(self, request: OperatorRunRequest, *, now: datetime | None = None) -> OperatorRunResult:
        request_fingerprint = _run_request_fingerprint(request)
        cached = self._runs_by_key.get(request.idempotency_key)
        if cached is not None:
            if (
                self._run_fingerprints_by_key.get(request.idempotency_key)
                != request_fingerprint
            ):
                raise ValueError(
                    "operator idempotency key is bound to a different request"
                )
            cached_policy = self.repositories.policies.get(request.policy_id)
            kill_switch_now = operator_kill_switch_engaged() or (
                cached_policy is not None and cached_policy.kill_switch_engaged
            )
            if not kill_switch_now:
                self.audit.emit(
                    user_id=request.user_id,
                    entity_type="operator_run",
                    entity_id=cached.run_id,
                    action="operator_duplicate_run_ignored",
                    after_state={"idempotency_key": request.idempotency_key},
                    source="operator_service",
                )
                return cached
            # A kill switch engaged after the cached run must not be masked by a
            # replayed result; fall through so the gate chain blocks and re-records.

        run_id = new_id("oprun")
        started_at = now or utc_now()
        decisions: list[OperatorDecision] = []
        policy = self.repositories.policies.get(request.policy_id)
        if self.professional_state_store is not None:
            durable = (
                self.professional_state_store.find_run_checkpoint_by_idempotency_key(
                    request.idempotency_key
                )
            )
            if durable is not None:
                if (
                    durable.policy_id != request.policy_id
                    or durable.user_id != request.user_id
                    or durable.policy_version != request.requested_policy_version
                    or durable.run_mode != request.run_mode
                    or durable.requested_at != request.requested_at
                    or durable.request_fingerprint != request_fingerprint
                ):
                    raise ValueError(
                        "operator idempotency key is bound to a different durable request"
                    )
                if operator_kill_switch_engaged() or (
                    policy is not None and policy.kill_switch_engaged
                ):
                    raise RuntimeError(
                        "active kill switch blocks durable operator-run replay"
                    )
                if durable.result_payload is None:
                    raise RuntimeError(
                        "durable operator run requires recovery before retry"
                    )
                return OperatorRunResult.model_validate(durable.result_payload)
            self.professional_state_store.insert_run_checkpoint(
                PaperRunCheckpoint(
                    run_id=run_id,
                    idempotency_key=request.idempotency_key,
                    policy_id=request.policy_id,
                    user_id=request.user_id,
                    # This checkpoint binds the request identity.  The authoritative
                    # policy version remains available in the terminal report.
                    policy_version=request.requested_policy_version,
                    run_mode=request.run_mode,
                    requested_at=request.requested_at,
                    request_fingerprint=request_fingerprint,
                    status="started",
                    data_mode=(
                        "paper_trading"
                        if request.run_mode == "paper_submit"
                        else "fixture"
                    ),
                    started_at=started_at,
                    updated_at=started_at,
                )
            )

        self.audit.emit(
            user_id=request.user_id,
            entity_type="operator_run",
            entity_id=run_id,
            action="operator_run_started",
            after_state={"request": request.model_dump(mode="json")},
            source="operator_service",
        )

        def decide(
            action: str,
            reason: str,
            *,
            strategy_id: str | None = None,
            order_plan_id: str | None = None,
            risk_check_id: str | None = None,
        ) -> OperatorDecision:
            decision = OperatorDecision(
                run_id=run_id,
                policy_id=request.policy_id,
                policy_version=policy.version if policy else request.requested_policy_version,
                strategy_id=strategy_id,
                order_plan_id=order_plan_id,
                action=action,  # type: ignore[arg-type]
                reason=reason,
                risk_check_id=risk_check_id,
            )
            decisions.append(decision)
            return decision

        def finish(
            status: str,
            *,
            fallback: FallbackDecision | None = None,
            selection: StrategySelectionDecision | None = None,
            submitted: list[str] | None = None,
            blocked: list[str] | None = None,
            order_plan_ids: list[str] | None = None,
            broker_order_ids: list[str] | None = None,
            risk_check_ids: list[str] | None = None,
        ) -> OperatorRunResult:
            if fallback is not None:
                self.audit.emit(
                    user_id=request.user_id,
                    entity_type="operator_run",
                    entity_id=run_id,
                    action="operator_fallback_engaged",
                    after_state=fallback,
                    source="operator_service",
                )
            report = OperatorReport(
                run_id=run_id,
                user_id=request.user_id,
                policy_id=request.policy_id,
                policy_version=policy.version if policy else request.requested_policy_version,
                started_at=started_at,
                completed_at=utc_now(),
                status=status,  # type: ignore[arg-type]
                strategy_selection=selection or _empty_selection("strategy_selection_not_reached"),
                decisions=decisions,
                fallback=fallback,
                order_plan_ids=order_plan_ids or [],
                broker_order_ids=broker_order_ids or [],
                risk_check_ids=risk_check_ids or [],
                safety_flags=self._safety_flags(policy, request),
                live_trading_enabled=False,
                audit_event_count=len(self.repositories.audit_logs.list()),
            )
            self.reports.append(report)
            self.audit.emit(
                user_id=request.user_id,
                entity_type="operator_report",
                entity_id=report.report_id,
                action="operator_report_generated",
                after_state=report,
                source="operator_service",
            )
            self.audit.emit(
                user_id=request.user_id,
                entity_type="operator_run",
                entity_id=run_id,
                action="operator_run_completed" if status == "completed" else "operator_run_blocked",
                after_state={"status": status, "fallback": fallback.reason_code if fallback else None},
                source="operator_service",
            )
            result = OperatorRunResult(
                run_id=run_id,
                status=status,  # type: ignore[arg-type]
                submitted_order_plan_ids=submitted or [],
                blocked_order_plan_ids=blocked or [],
                fallback=fallback,
                report=report,
            )
            if self.professional_state_store is not None:
                durable = self.professional_state_store.find_run_checkpoint_by_idempotency_key(
                    request.idempotency_key
                )
                if durable is None or durable.run_id != run_id:
                    raise RuntimeError("durable operator run checkpoint disappeared")
                terminal = durable.model_copy(
                    update={
                        "status": (
                            "completed" if status == "completed" else "blocked"
                        ),
                        "updated_at": max(
                            utc_now(),
                            durable.updated_at + timedelta(microseconds=1),
                        ),
                        "result_payload": result.model_dump(mode="json"),
                    }
                )
                self.professional_state_store.update_run_checkpoint(
                    PaperRunCheckpoint.model_validate(terminal.model_dump())
                )
            self._runs_by_key[request.idempotency_key] = result
            self._run_fingerprints_by_key[request.idempotency_key] = (
                request_fingerprint
            )
            return result

        def blocked_by(reason_code: str, *, selection: StrategySelectionDecision | None = None) -> OperatorRunResult:
            fallback = self.fallbacks.for_reason(reason_code)
            decide("fallback" if fallback.to_level > 0 else "block", reason_code)
            status = "fallback" if fallback.to_level > 0 else "blocked"
            return finish(status, fallback=fallback, selection=selection)

        # Gate 1: Level 5 feature flag (env or explicit policy field).
        if not fully_automated_operator_flag_enabled(policy):
            return blocked_by("level5_flag_disabled")

        # Gate 2: an active policy must exist.
        if policy is None:
            return blocked_by("policy_not_found")

        # Gate 3: live trading must remain disabled; the operator refuses to run otherwise.
        if live_trading_flag_enabled():
            return blocked_by("live_trading_flag_engaged")

        # Gate 4: kill switches (policy-level and operator-level env switch).
        if policy.kill_switch_engaged:
            return blocked_by("kill_switch_engaged")
        if operator_kill_switch_engaged():
            return blocked_by("operator_kill_switch_engaged")

        # Gate 5: only mock or paper brokers are reachable from the operator.
        if policy.broker not in {BrokerMode.mock, BrokerMode.paper}:
            return blocked_by("broker_mode_unsafe")
        if request.run_mode == "mock_submit" and policy.broker != BrokerMode.mock:
            return blocked_by("run_mode_broker_mismatch")
        if request.run_mode == "paper_submit" and policy.broker != BrokerMode.paper:
            return blocked_by("run_mode_broker_mismatch")
        if (
            request.run_mode == "paper_submit"
            and self.professional_state_store is not None
        ):
            return blocked_by("paper_submission_journal_required")

        # Gate 6: the run must bind to the current policy version.
        review = self.version_guard.require_current_version(
            policy_id=policy.policy_id,
            current_version=policy.version,
            requested_version=request.requested_policy_version,
        )
        if review.blocks_automatic_submission:
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="policy",
                entity_id=policy.policy_id,
                action="policy_version_mismatch",
                after_state=review,
                source="operator_service",
            )
            return blocked_by("policy_review_required")

        # Gate 7: the policy must be explicitly promoted to Level 5.
        if policy.authority_level != 5 or policy.execution_mode != ExecutionMode.fully_automated:
            return blocked_by("policy_not_promoted")

        # Step: deterministic strategy selection from the approved registry.
        selection = self.registry.select_for_level5(policy_version=policy.version)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="operator_run",
            entity_id=run_id,
            action="operator_strategy_selected",
            after_state=selection,
            source="operator_service",
        )
        if selection.selected_strategy_id is None:
            # Spec: fall back to Level 4 when a guarded-ready strategy exists,
            # otherwise degrade all the way to Level 2 suggestions.
            if self.registry.level4_available():
                return blocked_by("no_level5_strategy_eligible", selection=selection)
            return blocked_by("no_approved_strategy_available", selection=selection)
        registry_entry = self.registry.require(selection.selected_strategy_id)
        decide("noop", "strategy_selected", strategy_id=registry_entry.strategy_id)

        recipe = self._load_recipe(registry_entry.strategy_id)
        if recipe is None:
            return blocked_by("no_level5_strategy_eligible", selection=selection)

        # Step: sync portfolio snapshot from the mock/paper broker and build the plan.
        broker = self.harness._broker_for_policy(policy)
        snapshot = broker.get_positions(request.user_id)
        professional_weekly_eligible = False

        # Professional runs are risk-first: ordinary planning cannot race ahead of
        # the durable one-minute position cycle or a retirement/reconciliation phase.
        if recipe.strategy_id == "pullback_trend_v2" and self.professional is not None:
            state = self.professional._load_state(
                policy_id=policy.policy_id,
                strategy_id=registry_entry.strategy_id,
                strategy_version=registry_entry.version,
            )
            if state is None:
                decide(
                    "noop",
                    "professional_strategy_state_missing",
                    strategy_id=registry_entry.strategy_id,
                )
                return finish("completed", selection=selection, order_plan_ids=[])
            risk_due, risk_reason = risk_evaluation_due(
                state.last_risk_evaluated_at,
                started_at,
            )
            if risk_due or risk_reason not in {"risk_evaluation_not_due"}:
                decide(
                    "noop",
                    "protective_risk_evaluation_required",
                    strategy_id=registry_entry.strategy_id,
                )
                return finish("completed", selection=selection, order_plan_ids=[])
            weekly = self.professional.claim_weekly_rebalance(
                policy=policy,
                strategy_id=registry_entry.strategy_id,
                strategy_version=registry_entry.version,
                evaluated_at=started_at,
                acquire=False,
            )
            if not weekly.claimed:
                decide(
                    "noop",
                    weekly.reason_code,
                    strategy_id=registry_entry.strategy_id,
                )
                return finish("completed", selection=selection, order_plan_ids=[])
            professional_weekly_eligible = True

        # Gate 8: monthly loss stop halts all automatic trading before any planning.
        if snapshot.monthly_loss_ratio <= policy.monthly_loss_stop_all_autotrading:
            return blocked_by("monthly_loss_stop_engaged", selection=selection)

        signal_set = self._record_signal_set(
            recipe,
            policy,
            snapshot=snapshot,
            evaluated_at=now,
        )
        signals = signal_set.signals
        if not signal_set.data_quality.usable:
            reason = signal_set.data_quality.reason_codes[0] if signal_set.data_quality.reason_codes else "signal_provider_unavailable"
            decide("noop", reason, strategy_id=registry_entry.strategy_id)
            return finish("completed", selection=selection, order_plan_ids=[])

        if recipe.strategy_id == "pullback_trend_v2":
            rules = recipe.decision_rules
            if rules is None:
                decide("noop", "typed_decision_rules_missing", strategy_id=registry_entry.strategy_id)
                return finish("completed", selection=selection, order_plan_ids=[])
            plan = self.harness.create_portfolio_plan(
                policy_id=policy.policy_id,
                signals=signals,
                snapshot=snapshot,
                quotes={symbol.strip().upper(): quote.last for symbol, quote in signal_set.quotes.items()},
                quote_times={symbol.strip().upper(): quote.as_of for symbol, quote in signal_set.quotes.items()},
                require_explicit_quotes=True,
                rebalance_band=rules.rebalance_band,
            )
        else:
            plan = self.harness.create_portfolio_plan(
                policy_id=policy.policy_id,
                signals=signals,
                snapshot=snapshot,
            )
        if not plan.order_intents:
            decide("noop", "no_order_intents", strategy_id=registry_entry.strategy_id)
            return finish("completed", selection=selection, order_plan_ids=[])

        weekly_claim: OperatorCycleClaim | None = None
        if professional_weekly_eligible and request.run_mode != "dry_run":
            weekly = self.professional.claim_weekly_rebalance(
                policy=policy,
                strategy_id=registry_entry.strategy_id,
                strategy_version=registry_entry.version,
                evaluated_at=started_at,
            )
            if not weekly.claimed:
                decide(
                    "noop",
                    weekly.reason_code,
                    strategy_id=registry_entry.strategy_id,
                )
                return finish("completed", selection=selection, order_plan_ids=[])
            if weekly.claim is None:
                raise RuntimeError("weekly rebalance claim token is missing")
            weekly_claim = weekly.claim

        try:
            proposals = self.harness.generate_order_proposals(
                portfolio_plan_id=plan.plan_id,
                snapshot=snapshot,
            )
        except Exception:
            if weekly_claim is not None:
                self.professional.release_weekly_rebalance(
                    claim=weekly_claim,
                )
            raise

        if not proposals:
            if weekly_claim is not None:
                self.professional.release_weekly_rebalance(
                    claim=weekly_claim,
                )
            return blocked_by("risk_check_failed", selection=selection)

        if request.run_mode == "dry_run":
            for proposal in proposals:
                decide(
                    "noop",
                    "dry_run_no_submission",
                    strategy_id=registry_entry.strategy_id,
                    order_plan_id=proposal.order_plan_id,
                    risk_check_id=proposal.risk_check_id,
                )
                proposal.blocked_reason = "dry_run_no_submission"
                transition_order_plan(
                    order_plan=proposal,
                    new_status=OrderStatus.cancelled,
                    audit=self.audit,
                    user_id=policy.user_id,
                    source="operator_service",
                )
                self.repositories.order_plans.update(proposal)
            return finish(
                "completed",
                selection=selection,
                order_plan_ids=[proposal.order_plan_id for proposal in proposals],
                risk_check_ids=[proposal.risk_check_id for proposal in proposals if proposal.risk_check_id],
            )

        result = self._submit_proposals(
            policy=policy,
            registry_entry=registry_entry,
            recipe=recipe,
            snapshot=snapshot,
            proposals=proposals,
            selection=selection,
            now=now,
            decide=decide,
            finish=finish,
            weekly_claim=weekly_claim,
        )
        return result

    def _load_recipe(self, strategy_id: str) -> StrategyRecipe | None:
        recipe = self.repositories.strategies.get(strategy_id)
        if recipe is not None:
            return recipe
        try:
            recipe = load_strategy_recipe(strategy_id)
        except FileNotFoundError:
            return None
        self.repositories.strategies.add(recipe)
        return recipe

    def _record_signal_set(
        self,
        recipe: StrategyRecipe,
        policy: UserPolicy,
        *,
        snapshot: PortfolioSnapshot | None = None,
        evaluated_at: datetime | None = None,
    ) -> SignalSet:
        professional = recipe.strategy_id == "pullback_trend_v2"
        signal_set = generate_provider_bound_signals(
            recipe,
            self.ohlcv_provider,
            quote_provider=self.quote_provider,
            policy=policy,
            securities=self.harness.security_provider.get_securities(),
            horizon="completed_history" if professional else None,
            portfolio_snapshot=snapshot,
            evaluated_at=evaluated_at,
        )
        for signal in signal_set.signals:
            self.repositories.signals.add(signal)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="signal",
                entity_id=signal.signal_id,
                action="signal_generated",
                after_state=signal,
                source="operator_service",
            )
        return signal_set

    def _record_signals(self, recipe: StrategyRecipe, policy: UserPolicy) -> list[Signal]:
        return self._record_signal_set(recipe, policy).signals

    def _submit_proposals(
        self,
        *,
        policy: UserPolicy,
        registry_entry,
        recipe: StrategyRecipe,
        snapshot,
        proposals: list[OrderPlan],
        selection: StrategySelectionDecision,
        now: datetime | None,
        decide,
        finish,
        weekly_claim: OperatorCycleClaim | None = None,
    ) -> OperatorRunResult:
        # Authorization must use the wall clock at decision time: proposals are created
        # after the run starts, so reusing the run start time would make every quote
        # look stale (negative age). Tests may still inject a fixed `now`.
        authorization_time = now or utc_now()
        submitted: list[str] = []
        blocked: list[str] = []
        broker_order_ids: list[str] = []
        risk_check_ids: list[str] = []
        fallback: FallbackDecision | None = None

        def fence_weekly_submission(_order: OrderPlan) -> None:
            nonlocal weekly_claim
            if weekly_claim is None:
                return
            if self.professional is None:
                raise RuntimeError("weekly rebalance coordinator is missing")
            fence_time = authorization_time if now is not None else utc_now()
            if weekly_claim.completed_at is None:
                weekly_claim = self.professional.commit_weekly_rebalance_submission(
                    claim=weekly_claim,
                    committed_at=fence_time,
                )
            else:
                self.professional.require_weekly_rebalance_fence(
                    weekly_claim,
                    checked_at=fence_time,
                )

        for proposal in proposals:
            state = self.harness._guardrail_state(
                policy=policy,
                strategy_id=registry_entry.strategy_id,
                exclude_order_plan_id=proposal.order_plan_id,
            )
            result = authorize_level5(
                order_plan=proposal,
                policy=policy,
                registry_entry=registry_entry,
                strategy=recipe,
                snapshot=snapshot,
                state=state,
                seen_idempotency_keys=self.harness._seen_idempotency_keys(
                    exclude_order_plan_id=proposal.order_plan_id, submitted_only=True
                ),
                now=authorization_time,
            )
            if not result.authorized:
                reason = result.first_failed_check or "operator_order_blocked"
                proposal.blocked_reason = reason
                self.harness.last_blocked_reason = reason
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="order_plan",
                    entity_id=proposal.order_plan_id,
                    action="operator_order_blocked",
                    after_state={"reason": reason, "checks": result.model_dump(mode="json")},
                    source="operator_service",
                )
                transition_order_plan(
                    order_plan=proposal,
                    new_status=OrderStatus.failed,
                    audit=self.audit,
                    user_id=policy.user_id,
                    source="operator_service",
                )
                self.repositories.order_plans.update(proposal)
                decide("block", reason, strategy_id=registry_entry.strategy_id, order_plan_id=proposal.order_plan_id)
                blocked.append(proposal.order_plan_id)
                if fallback is None and reason in CHECK_TO_FALLBACK_REASON:
                    fallback = self.fallbacks.for_reason(CHECK_TO_FALLBACK_REASON[reason])
                continue

            proposal.approved_by = f"operator_policy_v{policy.version}"
            transition_order_plan(
                order_plan=proposal,
                new_status=OrderStatus.user_approved,
                audit=self.audit,
                user_id=policy.user_id,
                source="operator_service",
                action="operator_order_authorized",
            )
            self.repositories.order_plans.update(proposal)
            try:
                order_plan, broker_order, fills = self.harness.submit_order_plan(
                    proposal.order_plan_id,
                    snapshot=snapshot,
                    before_broker_submit=(
                        fence_weekly_submission
                        if weekly_claim is not None
                        else None
                    ),
                )
            except (RiskCheckRequired, ApprovalRequired) as exc:
                decide("block", str(exc), strategy_id=registry_entry.strategy_id, order_plan_id=proposal.order_plan_id)
                blocked.append(proposal.order_plan_id)
                if fallback is None:
                    fallback = self.fallbacks.for_reason("risk_check_failed")
                continue
            except Exception as exc:
                fallback = self._handle_broker_failure(policy=policy, proposal=proposal, error=exc)
                decide("fallback", "broker_failure", strategy_id=registry_entry.strategy_id, order_plan_id=proposal.order_plan_id)
                blocked.append(proposal.order_plan_id)
                break

            submitted.append(order_plan.order_plan_id)
            broker_order_ids.append(broker_order.broker_order_id)
            if order_plan.risk_check_id:
                risk_check_ids.append(order_plan.risk_check_id)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=order_plan.order_plan_id,
                action="operator_order_submitted",
                after_state={"broker_order_id": broker_order.broker_order_id, "fills": len(fills)},
                source="operator_service",
            )
            decide(
                "submit",
                "operator_order_submitted",
                strategy_id=registry_entry.strategy_id,
                order_plan_id=order_plan.order_plan_id,
                risk_check_id=order_plan.risk_check_id,
            )

        if submitted:
            status = "completed"
        elif fallback is not None:
            status = "fallback" if fallback.to_level > 0 else "blocked"
        else:
            status = "blocked"
        if weekly_claim is not None:
            if self.professional is None:
                raise RuntimeError("weekly rebalance completion context is missing")
            if weekly_claim.completed_at is not None:
                self.professional.complete_weekly_rebalance(
                    policy=policy,
                    claim=weekly_claim,
                    completed_at=(authorization_time if now is not None else utc_now()),
                )
            else:
                self.professional.release_weekly_rebalance(
                    claim=weekly_claim,
                )
        return finish(
            status,
            fallback=fallback,
            selection=selection,
            submitted=submitted,
            blocked=blocked,
            order_plan_ids=submitted + blocked,
            broker_order_ids=broker_order_ids,
            risk_check_ids=risk_check_ids,
        )

    def _handle_broker_failure(self, *, policy: UserPolicy, proposal: OrderPlan, error: Exception) -> FallbackDecision:
        current = self.repositories.order_plans.require(proposal.order_plan_id)
        if current.status == OrderStatus.submitted:
            transition_order_plan(
                order_plan=current,
                new_status=OrderStatus.failed,
                audit=self.audit,
                user_id=policy.user_id,
                source="operator_service",
                action="order_failed",
            )
            self.repositories.order_plans.update(current)
        self.harness.record_broker_health(
            policy_id=policy.policy_id,
            healthy=False,
            reason="broker_failure",
        )
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="order_plan",
            entity_id=proposal.order_plan_id,
            action="broker_health_failed",
            after_state={"error_type": type(error).__name__},
            source="operator_service",
        )
        return self.fallbacks.for_reason("broker_failure")
