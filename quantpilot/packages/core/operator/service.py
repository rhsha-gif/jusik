from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from quantpilot.packages.core.execution.fallback_manager import FallbackDecision, FallbackManager
from quantpilot.packages.core.execution.safety_flags import (
    fully_automated_operator_flag_enabled,
    guarded_autopilot_flag_enabled,
    live_trading_flag_enabled,
    market_orders_enabled,
    operator_kill_switch_engaged,
)
from quantpilot.packages.core.execution.state_machine import (
    ApprovalRequired,
    RiskCheckRequired,
    authorize_level5,
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
    OperatorRunStatus,
)
from quantpilot.packages.core.policy.versioning import PolicyVersionGuard, PolicyVersioningService
from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    OrderPlan,
    OrderStatus,
    PortfolioSnapshot,
    StrategyRecipe,
    UserPolicy,
    new_id,
    utc_now,
)
from quantpilot.packages.core.signals.service import generate_provider_bound_signals
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.registry import (
    StrategyRegistry,
    StrategyRegistryEntry,
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
    "order_type_allowed": "market_orders_disabled",
    "monthly_loss_stop_not_triggered": "monthly_loss_stop_engaged",
    "monthly_loss_pause_allows_order": "monthly_loss_pause_engaged",
    "fresh_risk_check_passed": "risk_check_failed",
}


def _empty_selection(reason: str) -> StrategySelectionDecision:
    return StrategySelectionDecision(
        selected_strategy_id=None,
        selected_version=None,
        eligible_strategy_ids=[],
        rejected={},
        reason=reason,
    )


OperatorDecisionAction = Literal["submit", "block", "fallback", "noop"]


@dataclass(frozen=True)
class _SelectedStrategy:
    selection: StrategySelectionDecision
    registry_entry: StrategyRegistryEntry


@dataclass(frozen=True)
class _PreparedOperatorRun:
    recipe: StrategyRecipe
    snapshot: PortfolioSnapshot
    proposals: list[OrderPlan]


@dataclass
class _OperatorSubmissionState:
    submitted: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    broker_order_ids: list[str] = field(default_factory=list)
    risk_check_ids: list[str] = field(default_factory=list)
    fallback: FallbackDecision | None = None

    def status(self) -> OperatorRunStatus:
        if self.submitted:
            return "completed"
        if self.fallback is not None:
            return "fallback" if self.fallback.to_level > 0 else "blocked"
        return "blocked"


@dataclass
class _OperatorRunContext:
    service: OperatorService
    request: OperatorRunRequest
    run_id: str
    started_at: datetime
    policy: UserPolicy | None
    decisions: list[OperatorDecision] = field(default_factory=list)

    @property
    def policy_version(self) -> int:
        return self.policy.version if self.policy else self.request.requested_policy_version

    def decide(
        self,
        action: OperatorDecisionAction,
        reason: str,
        *,
        strategy_id: str | None = None,
        order_plan_id: str | None = None,
        risk_check_id: str | None = None,
    ) -> OperatorDecision:
        decision = OperatorDecision(
            run_id=self.run_id,
            policy_id=self.request.policy_id,
            policy_version=self.policy_version,
            strategy_id=strategy_id,
            order_plan_id=order_plan_id,
            action=action,
            reason=reason,
            risk_check_id=risk_check_id,
        )
        self.decisions.append(decision)
        return decision

    def finish(
        self,
        status: OperatorRunStatus,
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
            self.service.audit.emit(
                user_id=self.request.user_id,
                entity_type="operator_run",
                entity_id=self.run_id,
                action="operator_fallback_engaged",
                after_state=fallback,
                source="operator_service",
            )
        report = OperatorReport(
            run_id=self.run_id,
            user_id=self.request.user_id,
            policy_id=self.request.policy_id,
            policy_version=self.policy_version,
            started_at=self.started_at,
            completed_at=utc_now(),
            status=status,
            strategy_selection=selection or _empty_selection("strategy_selection_not_reached"),
            decisions=self.decisions,
            fallback=fallback,
            order_plan_ids=order_plan_ids or [],
            broker_order_ids=broker_order_ids or [],
            risk_check_ids=risk_check_ids or [],
            safety_flags=self.service._safety_flags(self.policy, self.request),
            live_trading_enabled=False,
            audit_event_count=len(self.service.repositories.audit_logs.list()),
        )
        self.service.reports.append(report)
        self.service.audit.emit(
            user_id=self.request.user_id,
            entity_type="operator_report",
            entity_id=report.report_id,
            action="operator_report_generated",
            after_state=report,
            source="operator_service",
        )
        self.service.audit.emit(
            user_id=self.request.user_id,
            entity_type="operator_run",
            entity_id=self.run_id,
            action="operator_run_completed" if status == "completed" else "operator_run_blocked",
            after_state={"status": status, "fallback": fallback.reason_code if fallback else None},
            source="operator_service",
        )
        result = OperatorRunResult(
            run_id=self.run_id,
            status=status,
            submitted_order_plan_ids=submitted or [],
            blocked_order_plan_ids=blocked or [],
            fallback=fallback,
            report=report,
        )
        self.service._runs_by_key[self.request.idempotency_key] = result
        return result

    def blocked_by(
        self,
        reason_code: str,
        *,
        selection: StrategySelectionDecision | None = None,
    ) -> OperatorRunResult:
        fallback = self.service.fallbacks.for_reason(reason_code)
        self.decide("fallback" if fallback.to_level > 0 else "block", reason_code)
        status: OperatorRunStatus = "fallback" if fallback.to_level > 0 else "blocked"
        return self.finish(status, fallback=fallback, selection=selection)


class OperatorService:
    def __init__(
        self,
        harness: HarnessService | None = None,
        registry: StrategyRegistry | None = None,
        *,
        ohlcv_provider: OHLCVProvider | None = None,
        quote_provider: QuoteProvider | None = None,
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
        cached = self._cached_run_if_replay_allowed(request)
        if cached is not None:
            return cached

        context = self._start_context(request=request, now=now)
        blocked = self._preflight(context)
        if blocked is not None:
            return blocked
        policy = context.policy
        assert policy is not None

        selected = self._select_strategy(context=context, policy=policy)
        if isinstance(selected, OperatorRunResult):
            return selected
        prepared = self._prepare_run(context=context, policy=policy, selected=selected)
        if isinstance(prepared, OperatorRunResult):
            return prepared

        if request.run_mode == "dry_run":
            return self._finish_dry_run(context=context, selected=selected, proposals=prepared.proposals)
        return self._submit_proposals(
            policy=policy,
            registry_entry=selected.registry_entry,
            recipe=prepared.recipe,
            snapshot=prepared.snapshot,
            proposals=prepared.proposals,
            selection=selected.selection,
            now=now,
            context=context,
        )

    def _cached_run_if_replay_allowed(self, request: OperatorRunRequest) -> OperatorRunResult | None:
        cached = self._runs_by_key.get(request.idempotency_key)
        if cached is None:
            return None
        cached_policy = self.repositories.policies.get(request.policy_id)
        kill_switch_now = operator_kill_switch_engaged() or (
            cached_policy is not None and cached_policy.kill_switch_engaged
        )
        if kill_switch_now:
            return None
        self.audit.emit(
            user_id=request.user_id,
            entity_type="operator_run",
            entity_id=cached.run_id,
            action="operator_duplicate_run_ignored",
            after_state={"idempotency_key": request.idempotency_key},
            source="operator_service",
        )
        return cached

    def _start_context(self, *, request: OperatorRunRequest, now: datetime | None) -> _OperatorRunContext:
        context = _OperatorRunContext(
            service=self,
            request=request,
            run_id=new_id("oprun"),
            started_at=now or utc_now(),
            policy=self.repositories.policies.get(request.policy_id),
        )
        self.audit.emit(
            user_id=request.user_id,
            entity_type="operator_run",
            entity_id=context.run_id,
            action="operator_run_started",
            after_state={"request": request.model_dump(mode="json")},
            source="operator_service",
        )
        return context

    def _preflight(self, context: _OperatorRunContext) -> OperatorRunResult | None:
        policy = context.policy
        request = context.request
        if not fully_automated_operator_flag_enabled(policy):
            return context.blocked_by("level5_flag_disabled")
        if policy is None:
            return context.blocked_by("policy_not_found")
        if live_trading_flag_enabled():
            return context.blocked_by("live_trading_flag_engaged")
        if policy.kill_switch_engaged:
            return context.blocked_by("kill_switch_engaged")
        if operator_kill_switch_engaged():
            return context.blocked_by("operator_kill_switch_engaged")
        if policy.broker not in {BrokerMode.mock, BrokerMode.paper}:
            return context.blocked_by("broker_mode_unsafe")
        if request.run_mode == "mock_submit" and policy.broker != BrokerMode.mock:
            return context.blocked_by("run_mode_broker_mismatch")
        if request.run_mode == "paper_submit" and policy.broker != BrokerMode.paper:
            return context.blocked_by("run_mode_broker_mismatch")

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
            return context.blocked_by("policy_review_required")
        if policy.authority_level != 5 or policy.execution_mode != ExecutionMode.fully_automated:
            return context.blocked_by("policy_not_promoted")
        return None

    def _select_strategy(
        self,
        *,
        context: _OperatorRunContext,
        policy: UserPolicy,
    ) -> _SelectedStrategy | OperatorRunResult:
        selection = self.registry.select_for_level5(policy_version=policy.version)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="operator_run",
            entity_id=context.run_id,
            action="operator_strategy_selected",
            after_state=selection,
            source="operator_service",
        )
        if selection.selected_strategy_id is None:
            if self.registry.level4_available():
                return context.blocked_by("no_level5_strategy_eligible", selection=selection)
            return context.blocked_by("no_approved_strategy_available", selection=selection)
        registry_entry = self.registry.require(selection.selected_strategy_id)
        context.decide("noop", "strategy_selected", strategy_id=registry_entry.strategy_id)
        return _SelectedStrategy(selection=selection, registry_entry=registry_entry)

    def _prepare_run(
        self,
        *,
        context: _OperatorRunContext,
        policy: UserPolicy,
        selected: _SelectedStrategy,
    ) -> _PreparedOperatorRun | OperatorRunResult:
        recipe = self._load_recipe(selected.registry_entry.strategy_id)
        if recipe is None:
            return context.blocked_by("no_level5_strategy_eligible", selection=selected.selection)

        broker = self.harness._broker_for_policy(policy)
        snapshot = broker.get_positions(context.request.user_id)
        if snapshot.monthly_loss_ratio <= policy.monthly_loss_stop_all_autotrading:
            return context.blocked_by("monthly_loss_stop_engaged", selection=selected.selection)

        signal_set = self._record_signal_set(recipe, policy)
        if not signal_set.data_quality.usable:
            reason = signal_set.data_quality.reason_codes[0] if signal_set.data_quality.reason_codes else "signal_provider_unavailable"
            context.decide("noop", reason, strategy_id=selected.registry_entry.strategy_id)
            return context.finish("completed", selection=selected.selection, order_plan_ids=[])

        plan = self.harness.create_portfolio_plan(policy_id=policy.policy_id, signals=signal_set.signals, snapshot=snapshot)
        proposals = self.harness.generate_order_proposals(portfolio_plan_id=plan.plan_id, snapshot=snapshot)
        if not proposals:
            if not plan.order_intents:
                context.decide("noop", "no_order_intents", strategy_id=selected.registry_entry.strategy_id)
                return context.finish("completed", selection=selected.selection, order_plan_ids=[])
            return context.blocked_by("risk_check_failed", selection=selected.selection)
        return _PreparedOperatorRun(recipe=recipe, snapshot=snapshot, proposals=proposals)

    def _finish_dry_run(
        self,
        *,
        context: _OperatorRunContext,
        selected: _SelectedStrategy,
        proposals: list[OrderPlan],
    ) -> OperatorRunResult:
        for proposal in proposals:
            context.decide(
                "noop",
                "dry_run_no_submission",
                strategy_id=selected.registry_entry.strategy_id,
                order_plan_id=proposal.order_plan_id,
                risk_check_id=proposal.risk_check_id,
            )
        return context.finish(
            "completed",
            selection=selected.selection,
            order_plan_ids=[proposal.order_plan_id for proposal in proposals],
            risk_check_ids=[proposal.risk_check_id for proposal in proposals if proposal.risk_check_id],
        )

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

    def _record_signal_set(self, recipe: StrategyRecipe, policy: UserPolicy) -> SignalSet:
        signal_set = generate_provider_bound_signals(
            recipe,
            self.ohlcv_provider,
            quote_provider=self.quote_provider,
            policy=policy,
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

    def _submit_proposals(
        self,
        *,
        policy: UserPolicy,
        registry_entry: StrategyRegistryEntry,
        recipe: StrategyRecipe,
        snapshot: PortfolioSnapshot,
        proposals: list[OrderPlan],
        selection: StrategySelectionDecision,
        now: datetime | None,
        context: _OperatorRunContext,
    ) -> OperatorRunResult:
        authorization_time = now or utc_now()
        state = _OperatorSubmissionState()

        for proposal in proposals:
            result = self._authorize_proposal(
                policy=policy,
                registry_entry=registry_entry,
                recipe=recipe,
                snapshot=snapshot,
                proposal=proposal,
                authorization_time=authorization_time,
            )
            if not result.authorized:
                self._record_operator_block(
                    policy=policy,
                    registry_entry=registry_entry,
                    proposal=proposal,
                    result=result,
                    context=context,
                    state=state,
                )
                continue

            self._mark_operator_authorized(policy=policy, proposal=proposal)
            try:
                order_plan, broker_order, fills = self.harness.submit_order_plan(proposal.order_plan_id)
            except (RiskCheckRequired, ApprovalRequired) as exc:
                self._record_submission_gate_exception(
                    registry_entry=registry_entry,
                    proposal=proposal,
                    error=exc,
                    context=context,
                    state=state,
                )
                continue
            except Exception as exc:
                state.fallback = self._handle_broker_failure(policy=policy, proposal=proposal, error=exc)
                context.decide("fallback", "broker_failure", strategy_id=registry_entry.strategy_id, order_plan_id=proposal.order_plan_id)
                state.blocked.append(proposal.order_plan_id)
                break

            self._record_operator_submission(
                policy=policy,
                registry_entry=registry_entry,
                order_plan=order_plan,
                broker_order_id=broker_order.broker_order_id,
                fill_count=len(fills),
                context=context,
                state=state,
            )

        return context.finish(
            state.status(),
            fallback=state.fallback,
            selection=selection,
            submitted=state.submitted,
            blocked=state.blocked,
            order_plan_ids=state.submitted + state.blocked,
            broker_order_ids=state.broker_order_ids,
            risk_check_ids=state.risk_check_ids,
        )

    def _authorize_proposal(
        self,
        *,
        policy: UserPolicy,
        registry_entry: StrategyRegistryEntry,
        recipe: StrategyRecipe,
        snapshot: PortfolioSnapshot,
        proposal: OrderPlan,
        authorization_time: datetime,
    ):
        guardrail_state = self.harness._guardrail_state(
            policy=policy,
            strategy_id=registry_entry.strategy_id,
            exclude_order_plan_id=proposal.order_plan_id,
        )
        return authorize_level5(
            order_plan=proposal,
            policy=policy,
            registry_entry=registry_entry,
            strategy=recipe,
            snapshot=snapshot,
            state=guardrail_state,
            seen_idempotency_keys=self.harness._seen_idempotency_keys(
                exclude_order_plan_id=proposal.order_plan_id,
                submitted_only=True,
            ),
            now=authorization_time,
        )

    def _record_operator_block(
        self,
        *,
        policy: UserPolicy,
        registry_entry: StrategyRegistryEntry,
        proposal: OrderPlan,
        result,
        context: _OperatorRunContext,
        state: _OperatorSubmissionState,
    ) -> None:
        reason = result.first_failed_check or "operator_order_blocked"
        proposal.blocked_reason = reason
        self.repositories.order_plans.update(proposal)
        self.harness.last_blocked_reason = reason
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="order_plan",
            entity_id=proposal.order_plan_id,
            action="operator_order_blocked",
            after_state={"reason": reason, "checks": result.model_dump(mode="json")},
            source="operator_service",
        )
        context.decide("block", reason, strategy_id=registry_entry.strategy_id, order_plan_id=proposal.order_plan_id)
        state.blocked.append(proposal.order_plan_id)
        if state.fallback is None and reason in CHECK_TO_FALLBACK_REASON:
            state.fallback = self.fallbacks.for_reason(CHECK_TO_FALLBACK_REASON[reason])

    def _mark_operator_authorized(self, *, policy: UserPolicy, proposal: OrderPlan) -> None:
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

    def _record_submission_gate_exception(
        self,
        *,
        registry_entry: StrategyRegistryEntry,
        proposal: OrderPlan,
        error: Exception,
        context: _OperatorRunContext,
        state: _OperatorSubmissionState,
    ) -> None:
        context.decide("block", str(error), strategy_id=registry_entry.strategy_id, order_plan_id=proposal.order_plan_id)
        state.blocked.append(proposal.order_plan_id)
        if state.fallback is None:
            state.fallback = self.fallbacks.for_reason("risk_check_failed")

    def _record_operator_submission(
        self,
        *,
        policy: UserPolicy,
        registry_entry: StrategyRegistryEntry,
        order_plan: OrderPlan,
        broker_order_id: str,
        fill_count: int,
        context: _OperatorRunContext,
        state: _OperatorSubmissionState,
    ) -> None:
        state.submitted.append(order_plan.order_plan_id)
        state.broker_order_ids.append(broker_order_id)
        if order_plan.risk_check_id:
            state.risk_check_ids.append(order_plan.risk_check_id)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="order_plan",
            entity_id=order_plan.order_plan_id,
            action="operator_order_submitted",
            after_state={"broker_order_id": broker_order_id, "fills": fill_count},
            source="operator_service",
        )
        context.decide(
            "submit",
            "operator_order_submitted",
            strategy_id=registry_entry.strategy_id,
            order_plan_id=order_plan.order_plan_id,
            risk_check_id=order_plan.risk_check_id,
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
        self.harness.autopilot_paused = True
        self.harness.last_blocked_reason = "broker_failure"
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="order_plan",
            entity_id=proposal.order_plan_id,
            action="broker_health_failed",
            after_state={"error": str(error)},
            source="operator_service",
        )
        return self.fallbacks.for_reason("broker_failure")
