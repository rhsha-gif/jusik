from __future__ import annotations

from datetime import datetime

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
from quantpilot.packages.core.operator.schemas import (
    OperatorDecision,
    OperatorReport,
    OperatorRunRequest,
    OperatorRunResult,
)
from quantpilot.packages.core.policy.versioning import PolicyVersionGuard, PolicyVersioningService
from quantpilot.packages.core.risk.gatekeeper import market_orders_enabled
from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    OrderPlan,
    OrderStatus,
    Signal,
    StrategyRecipe,
    UserPolicy,
    new_id,
    utc_now,
)
from quantpilot.packages.core.signals.service import generate_signals, load_fixture_ohlcv
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


class OperatorService:
    def __init__(
        self,
        harness: HarnessService | None = None,
        registry: StrategyRegistry | None = None,
    ) -> None:
        self.harness = harness or HarnessService()
        self.registry = registry or default_strategy_registry()
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
        cached = self._runs_by_key.get(request.idempotency_key)
        if cached is not None:
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
            self._runs_by_key[request.idempotency_key] = result
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

        # Gate 8: monthly loss stop halts all automatic trading before any planning.
        if snapshot.monthly_loss_ratio <= policy.monthly_loss_stop_all_autotrading:
            return blocked_by("monthly_loss_stop_engaged", selection=selection)

        signals = self._record_signals(recipe, policy)
        plan = self.harness.create_portfolio_plan(policy_id=policy.policy_id, signals=signals, snapshot=snapshot)
        proposals = self.harness.generate_order_proposals(portfolio_plan_id=plan.plan_id, snapshot=snapshot)

        if not proposals:
            if not plan.order_intents:
                decide("noop", "no_order_intents", strategy_id=registry_entry.strategy_id)
                return finish("completed", selection=selection, order_plan_ids=[])
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
            return finish(
                "completed",
                selection=selection,
                order_plan_ids=[proposal.order_plan_id for proposal in proposals],
                risk_check_ids=[proposal.risk_check_id for proposal in proposals if proposal.risk_check_id],
            )

        return self._submit_proposals(
            policy=policy,
            registry_entry=registry_entry,
            recipe=recipe,
            snapshot=snapshot,
            proposals=proposals,
            selection=selection,
            now=now,
            decide=decide,
            finish=finish,
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

    def _record_signals(self, recipe: StrategyRecipe, policy: UserPolicy) -> list[Signal]:
        bars = load_fixture_ohlcv()
        signals = generate_signals(recipe, bars, policy=policy)
        for signal in signals:
            self.repositories.signals.add(signal)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="signal",
                entity_id=signal.signal_id,
                action="signal_generated",
                after_state=signal,
                source="operator_service",
            )
        return signals

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
                order_plan, broker_order, fills = self.harness.submit_order_plan(proposal.order_plan_id)
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
