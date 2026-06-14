from __future__ import annotations

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.brokers.paper_broker import PaperBroker
from quantpilot.packages.core.execution.state_machine import authorize_level4, transition_order_plan
from quantpilot.packages.core.execution.order_context import (
    build_guardrail_state,
    collect_seen_idempotency_keys,
    quotes_for_intents,
)
from quantpilot.packages.core.execution.proposal_service import ProposalService
from quantpilot.packages.core.execution.submission_service import SubmissionService
from quantpilot.packages.core.policy.parser import DEFAULT_POLICY_TEXT, parse_policy_text
from quantpilot.packages.core.level12.service import Level12RunResult, Level12Service
from quantpilot.packages.core.ledger.service import ReconciliationLedgerService
from quantpilot.packages.core.portfolio.planner import (
    build_portfolio_plan,
    fixture_portfolio_snapshot,
)
from quantpilot.packages.core.reports.service import build_operation_report
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.risk.types import BatchRiskConfig
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    ExecutionMode,
    Fill,
    GuardrailState,
    OperationReport,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    PortfolioPlan,
    PortfolioSnapshot,
    Signal,
    UserPolicy,
    utc_now,
)
from quantpilot.packages.core.data.providers import (
    FixtureMarketDataProvider,
    FixtureSecurityProvider,
    MarketDataProvider,
    SecurityProvider,
    build_providers_from_env,
)
from quantpilot.packages.core.signals.service import generate_signals
from quantpilot.packages.core.strategies.loader import load_default_strategy
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


class HarnessService:
    def __init__(
        self,
        repositories: RepositoryRegistry | None = None,
        *,
        security_provider: SecurityProvider | None = None,
        market_data_provider: MarketDataProvider | None = None,
    ) -> None:
        self.repositories = repositories or RepositoryRegistry()
        self.audit = AuditRecorder(self.repositories.audit_logs)
        self.ledger = ReconciliationLedgerService(self.repositories.reconciliation_ledger)
        self.autopilot_paused = False
        self.last_blocked_reason: str | None = None
        # Fixtures stay the default; local/historical providers are injected explicitly.
        self.security_provider: SecurityProvider = security_provider or FixtureSecurityProvider()
        self.market_data_provider: MarketDataProvider = market_data_provider or FixtureMarketDataProvider()

    @classmethod
    def from_environment(cls, repositories: RepositoryRegistry | None = None) -> "HarnessService":
        security_provider, market_data_provider = build_providers_from_env()
        return cls(
            repositories,
            security_provider=security_provider,
            market_data_provider=market_data_provider,
        )

    def parse_policy(self, text: str = DEFAULT_POLICY_TEXT, *, user_id: str = "fixture-user") -> UserPolicy:
        policy = parse_policy_text(text, user_id=user_id)
        self.repositories.policies.add(policy)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="policy",
            entity_id=policy.policy_id,
            action="policy_created",
            after_state=policy,
            source="policy_parser_stub",
        )
        return policy

    def confirm_policy(self, policy_id: str) -> UserPolicy:
        policy = self.repositories.policies.require(policy_id)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="policy",
            entity_id=policy.policy_id,
            action="policy_confirmed",
            after_state=policy,
            source="api_or_smoke",
        )
        return policy

    def load_strategy(self):
        recipe = load_default_strategy()
        if self.repositories.strategies.get(recipe.strategy_id) is None:
            self.repositories.strategies.add(recipe)
        self.audit.emit(
            user_id="system",
            entity_type="strategy",
            entity_id=recipe.strategy_id,
            action="strategy_loaded",
            after_state=recipe,
            source="strategy_loader",
        )
        return recipe

    def run_signals(self) -> list[Signal]:
        recipe = self.load_strategy()
        bars = self.market_data_provider.get_bars()
        securities = self.security_provider.get_securities()
        signals = generate_signals(recipe, bars, securities=securities)
        for signal in signals:
            self.repositories.signals.add(signal)
            self.audit.emit(
                user_id="fixture-user",
                entity_type="signal",
                entity_id=signal.signal_id,
                action="signal_generated",
                after_state=signal,
                source="signal_stub",
            )
        return signals

    def _level12_service(self) -> Level12Service:
        return Level12Service(
            repositories=self.repositories,
            audit=self.audit,
            security_provider=self.security_provider,
            market_data_provider=self.market_data_provider,
            load_strategy=self.load_strategy,
        )

    def run_level_1_2_result(self, *, policy_id: str) -> Level12RunResult:
        return self._level12_service().run(policy_id=policy_id)

    def run_level_1_2(self, *, policy_id: str) -> dict[str, object]:
        return self.run_level_1_2_result(policy_id=policy_id).as_dict()

    def create_portfolio_plan(
        self,
        *,
        policy_id: str,
        signals: list[Signal] | None = None,
        snapshot: PortfolioSnapshot | None = None,
    ) -> PortfolioPlan:
        policy = self.repositories.policies.require(policy_id)
        selected_signals = signals or self.repositories.signals.list()
        portfolio_snapshot = snapshot or fixture_portfolio_snapshot()
        quotes = {bar["symbol"]: float(bar["close"]) for bar in self.market_data_provider.get_bars()}
        plan = build_portfolio_plan(
            policy=policy,
            signals=selected_signals,
            snapshot=portfolio_snapshot,
            quotes=quotes,
        )
        self.repositories.portfolio_plans.add(plan)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="portfolio_plan",
            entity_id=plan.plan_id,
            action="portfolio_plan_created",
            after_state=plan,
            source="portfolio_planner_stub",
        )
        return plan

    def create_order_plans(
        self,
        *,
        portfolio_plan_id: str,
        snapshot: PortfolioSnapshot | None = None,
        run_risk: bool = True,
        propose_passed: bool = True,
        partial_allow: bool = False,
    ) -> list[OrderPlan]:
        portfolio_plan = self.repositories.portfolio_plans.require(portfolio_plan_id)
        policy = self.repositories.policies.require(portfolio_plan.policy_id)
        portfolio_snapshot = snapshot or fixture_portfolio_snapshot()
        candidate_orders = self._candidate_order_plans(policy=policy, portfolio_plan=portfolio_plan)
        accepted_order_ids = {order.order_plan_id for order in candidate_orders}
        if run_risk:
            decision = self._order_creation_batch_decision(
                policy=policy,
                portfolio_plan=portfolio_plan,
                snapshot=portfolio_snapshot,
                candidate_orders=candidate_orders,
                partial_allow=partial_allow,
            )
            if not decision.passed:
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="portfolio_plan",
                    entity_id=portfolio_plan.plan_id,
                    action="batch_risk_rejected",
                    after_state=decision,
                    source="batch_risk_gate",
                )
                return []
            accepted_order_ids = set(decision.accepted_order_plan_ids)
            if decision.mode == "partial_batch":
                self._record_partial_batch(policy=policy, portfolio_plan=portfolio_plan, decision=decision)

        return self._persist_order_plan_candidates(
            policy=policy,
            candidate_orders=candidate_orders,
            accepted_order_ids=accepted_order_ids,
            portfolio_plan=portfolio_plan,
            portfolio_snapshot=portfolio_snapshot,
            run_risk=run_risk,
            propose_passed=propose_passed,
        )

    def _candidate_order_plans(self, *, policy: UserPolicy, portfolio_plan: PortfolioPlan) -> list[OrderPlan]:
        return [
            OrderPlan(
                policy_id=policy.policy_id,
                policy_version=policy.version,
                intent=intent,
                idempotency_key=f"{policy.policy_id}:{portfolio_plan.plan_id}:{intent.intent_id}",
            )
            for intent in portfolio_plan.order_intents
        ]

    def _order_creation_batch_decision(
        self,
        *,
        policy: UserPolicy,
        portfolio_plan: PortfolioPlan,
        snapshot: PortfolioSnapshot,
        candidate_orders: list[OrderPlan],
        partial_allow: bool,
    ):
        return run_batch_risk_gate(
            policy=policy,
            portfolio_plan=portfolio_plan,
            snapshot=snapshot,
            quotes=self._quotes_for_intents([order.intent for order in candidate_orders]),
            order_plans=candidate_orders,
            config=BatchRiskConfig(
                partial_allow=partial_allow,
                quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
            ),
            guardrail_state=self._guardrail_state(policy=policy, strategy_id="order_planner_stub"),
            seen_idempotency_keys=self._seen_idempotency_keys(),
        )

    def _record_partial_batch(self, *, policy: UserPolicy, portfolio_plan: PortfolioPlan, decision) -> None:
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="portfolio_plan",
            entity_id=portfolio_plan.plan_id,
            action="batch_risk_partial_allowed",
            after_state=decision,
            source="batch_risk_gate",
        )

    def _persist_order_plan_candidates(
        self,
        *,
        policy: UserPolicy,
        candidate_orders: list[OrderPlan],
        accepted_order_ids: set[str],
        portfolio_plan: PortfolioPlan,
        portfolio_snapshot: PortfolioSnapshot,
        run_risk: bool,
        propose_passed: bool,
    ) -> list[OrderPlan]:
        created: list[OrderPlan] = []
        for order_plan in candidate_orders:
            if order_plan.order_plan_id not in accepted_order_ids:
                continue
            self.repositories.order_plans.add(order_plan)
            self.ledger.record_order_intent(
                policy=policy,
                order_plan=order_plan,
                metadata={"portfolio_plan_id": portfolio_plan.plan_id, "source": "order_planner_stub"},
            )
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=order_plan.order_plan_id,
                action="order_plan_created",
                after_state=order_plan,
                source="order_planner_stub",
            )
            if run_risk:
                self.apply_risk_check(order_plan.order_plan_id, snapshot=portfolio_snapshot)
                order_plan = self.repositories.order_plans.require(order_plan.order_plan_id)
                if propose_passed and order_plan.status == OrderStatus.risk_checked:
                    transition_order_plan(
                        order_plan=order_plan,
                        new_status=OrderStatus.proposed,
                        audit=self.audit,
                        user_id=policy.user_id,
                        source="order_planner_stub",
                    )
                    self.repositories.order_plans.update(order_plan)
            created.append(self.repositories.order_plans.require(order_plan.order_plan_id))
        return created

    def _seen_idempotency_keys(
        self,
        *,
        exclude_order_plan_id: str | None = None,
        exclude_order_plan_ids: set[str] | None = None,
        submitted_only: bool = False,
    ) -> set[str]:
        return collect_seen_idempotency_keys(
            self.repositories.order_plans.list(),
            exclude_order_plan_id=exclude_order_plan_id,
            exclude_order_plan_ids=exclude_order_plan_ids,
            submitted_only=submitted_only,
        )

    def _guardrail_state(
        self,
        *,
        policy: UserPolicy,
        strategy_id: str,
        exclude_order_plan_id: str | None = None,
        exclude_order_plan_ids: set[str] | None = None,
    ) -> GuardrailState:
        return build_guardrail_state(
            order_plans=self.repositories.order_plans.list(),
            policy=policy,
            strategy_id=strategy_id,
            autopilot_paused=self.autopilot_paused,
            last_blocked_reason=self.last_blocked_reason,
            exclude_order_plan_id=exclude_order_plan_id,
            exclude_order_plan_ids=exclude_order_plan_ids,
        )

    def _quotes_for_intents(self, intents: list[OrderIntent]) -> dict[str, float]:
        return quotes_for_intents(intents)

    def _proposal_service(self) -> ProposalService:
        return ProposalService(
            repositories=self.repositories,
            audit=self.audit,
            ledger=self.ledger,
            load_strategy=self.load_strategy,
            seen_idempotency_keys=self._seen_idempotency_keys,
            guardrail_state=self._guardrail_state,
            risk_check=run_risk_check,
            now=utc_now,
        )

    def generate_order_proposals(
        self,
        *,
        portfolio_plan_id: str,
        snapshot: PortfolioSnapshot | None = None,
        partial_allow: bool = False,
    ) -> list[OrderPlan]:
        return self._proposal_service().generate_order_proposals(
            portfolio_plan_id=portfolio_plan_id,
            snapshot=snapshot,
            partial_allow=partial_allow,
        )

    def apply_risk_check(self, order_plan_id: str, *, snapshot: PortfolioSnapshot | None = None):
        order_plan = self.repositories.order_plans.require(order_plan_id)
        policy = self.repositories.policies.require(order_plan.policy_id)
        seen_keys = {
            existing.idempotency_key
            for existing in self.repositories.order_plans.list()
            if existing.order_plan_id != order_plan.order_plan_id and existing.risk_check_id is not None
        }
        risk_check = run_risk_check(
            policy=policy,
            order_plan=order_plan,
            snapshot=snapshot or fixture_portfolio_snapshot(),
            seen_idempotency_keys=seen_keys,
        )
        if risk_check.passed:
            order_plan.risk_check_id = risk_check.risk_check_id
            order_plan.risk_check_expires_at = risk_check.expires_at
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.risk_checked,
                audit=self.audit,
                user_id=policy.user_id,
                source="risk_gatekeeper",
            )
            self.repositories.order_plans.update(order_plan)
        else:
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=order_plan.order_plan_id,
                action="risk_check_failed",
                before_state=order_plan,
                after_state={"failed_checks": risk_check.failed_checks},
                source="risk_gatekeeper",
            )
        return risk_check

    def approve_order_plan(self, order_plan_id: str) -> OrderPlan:
        order_plan = self.repositories.order_plans.require(order_plan_id)
        policy = self.repositories.policies.require(order_plan.policy_id)
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.user_approved,
            audit=self.audit,
            user_id=policy.user_id,
            source="user_approval",
            action="proposal_approved",
        )
        return self.repositories.order_plans.update(order_plan)

    def reject_order_plan(self, order_plan_id: str, *, reason: str = "user_rejected") -> OrderPlan:
        order_plan = self.repositories.order_plans.require(order_plan_id)
        policy = self.repositories.policies.require(order_plan.policy_id)
        order_plan.blocked_reason = reason
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.rejected,
            audit=self.audit,
            user_id=policy.user_id,
            source="user_rejection",
            action="proposal_rejected",
        )
        self.ledger.record_reject(policy=policy, order_plan=order_plan, reason=reason)
        return self.repositories.order_plans.update(order_plan)

    def cancel_order_plan(self, order_plan_id: str, *, reason: str = "user_cancelled") -> OrderPlan:
        order_plan = self.repositories.order_plans.require(order_plan_id)
        policy = self.repositories.policies.require(order_plan.policy_id)
        order_plan.blocked_reason = reason
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.cancelled,
            audit=self.audit,
            user_id=policy.user_id,
            source="user_cancel",
            action="order_cancelled",
        )
        self.ledger.record_cancel(policy=policy, order_plan=order_plan, reason=reason)
        return self.repositories.order_plans.update(order_plan)

    def modify_order_plan(self, order_plan_id: str, *, quantity: float, limit_price: float | None) -> OrderPlan:
        return self._proposal_service().modify_order_plan(
            order_plan_id,
            quantity=quantity,
            limit_price=limit_price,
        )

    def _broker_for_policy(self, policy: UserPolicy):
        if policy.broker == BrokerMode.paper:
            return PaperBroker()
        if policy.broker == BrokerMode.mock:
            return MockBroker()
        raise RuntimeError("live broker mode is disabled in the pre-harness")

    def _submission_service(self) -> SubmissionService:
        return SubmissionService(
            repositories=self.repositories,
            audit=self.audit,
            ledger=self.ledger,
            broker_for_policy=self._broker_for_policy,
            guardrail_state=self._guardrail_state,
            autopilot_paused=lambda: self.autopilot_paused,
            last_blocked_reason=lambda: self.last_blocked_reason,
            now=utc_now,
        )

    def submit_order_plan(self, order_plan_id: str) -> tuple[OrderPlan, BrokerOrder, list[Fill]]:
        return self._submission_service().submit_order_plan(order_plan_id)

    def pause_guarded_autopilot(self, *, policy_id: str, reason: str = "user_paused") -> dict[str, object]:
        policy = self.repositories.policies.require(policy_id)
        self.autopilot_paused = True
        self.last_blocked_reason = "autopilot_paused"
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="policy",
            entity_id=policy.policy_id,
            action="autopilot_paused",
            after_state={"reason": reason},
            source="autopilot_service",
        )
        return self.autopilot_status(policy_id=policy_id)

    def resume_guarded_autopilot(self, *, policy_id: str) -> dict[str, object]:
        policy = self.repositories.policies.require(policy_id)
        self.autopilot_paused = False
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="policy",
            entity_id=policy.policy_id,
            action="autopilot_resumed",
            after_state=policy,
            source="autopilot_service",
        )
        return self.autopilot_status(policy_id=policy_id)

    def engage_kill_switch(self, *, policy_id: str, reason: str = "user_requested") -> dict[str, object]:
        policy = self.repositories.policies.require(policy_id)
        before = policy.model_copy(deep=True)
        policy.kill_switch_engaged = True
        policy.authority_level = 2
        policy.guarded_autopilot_enabled = False
        policy.execution_mode = ExecutionMode.approval_required
        self.last_blocked_reason = "kill_switch_not_engaged"
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="policy",
            entity_id=policy.policy_id,
            action="kill_switch_engaged",
            before_state=before,
            after_state={"reason": reason, "policy": policy.model_dump(mode="json")},
            source="autopilot_service",
        )
        self.repositories.policies.update(policy)
        return self.autopilot_status(policy_id=policy_id)

    def release_kill_switch(self, *, policy_id: str, confirmation: str) -> dict[str, object]:
        policy = self.repositories.policies.require(policy_id)
        if confirmation != "release kill switch":
            raise RuntimeError("explicit confirmation is required to release kill switch")
        before = policy.model_copy(deep=True)
        policy.kill_switch_engaged = False
        policy.authority_level = 2
        policy.execution_mode = ExecutionMode.approval_required
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="policy",
            entity_id=policy.policy_id,
            action="kill_switch_released",
            before_state=before,
            after_state=policy,
            source="autopilot_service",
        )
        self.repositories.policies.update(policy)
        return self.autopilot_status(policy_id=policy_id)

    def autopilot_status(self, *, policy_id: str | None = None) -> dict[str, object]:
        policy = self.repositories.policies.require(policy_id) if policy_id else (self.repositories.policies.list()[-1] if self.repositories.policies.list() else UserPolicy())
        return {
            "kill_switch_engaged": policy.kill_switch_engaged,
            "guarded_autopilot_enabled": policy.guarded_autopilot_enabled,
            "guarded_autopilot_paused": self.autopilot_paused,
            "broker_mode": policy.broker.value,
            "live_trading_enabled": False,
            "execution_mode": policy.execution_mode.value,
            "authority_level": policy.authority_level,
            "monthly_loss_pause_new_buys": policy.monthly_loss_pause_new_buys,
            "monthly_loss_stop_all_autotrading": policy.monthly_loss_stop_all_autotrading,
            "last_blocked_reason": self.last_blocked_reason,
            "feature_flags": {
                "GUARDED_AUTOPILOT_ENABLED": policy.guarded_autopilot_enabled,
                "LIVE_TRADING_ENABLED": False,
                "MARKET_ORDERS_ENABLED": False,
            },
        }

    def run_guarded_autopilot_once(self, *, policy_id: str) -> dict[str, object]:
        policy = self.repositories.policies.require(policy_id)
        start_failure = self._record_guarded_run_start(policy)
        if start_failure is not None:
            return start_failure

        signals = self._signals_for_guarded_run()
        snapshot = fixture_portfolio_snapshot()
        plan = self.create_portfolio_plan(policy_id=policy.policy_id, signals=signals, snapshot=snapshot)
        proposals = self.generate_order_proposals(portfolio_plan_id=plan.plan_id, snapshot=snapshot)
        strategy = self.load_strategy()
        result = self._execute_guarded_proposals(
            policy=policy,
            strategy=strategy,
            snapshot=snapshot,
            proposals=proposals,
        )
        if not proposals and not result["blocked"]:
            result["blocked"].append({"reason": self.last_blocked_reason or "no_proposals"})
        return result

    def _record_guarded_run_start(self, policy: UserPolicy) -> dict[str, object] | None:
        try:
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="policy",
                entity_id=policy.policy_id,
                action="autopilot_run_started",
                after_state=policy,
                source="autopilot_service",
            )
        except Exception:
            policy.kill_switch_engaged = True
            self.last_blocked_reason = "audit_log_unwritable"
            self.repositories.policies.update(policy)
            return {"submitted": [], "blocked": [{"reason": "audit_log_unwritable"}]}
        return None

    def _signals_for_guarded_run(self) -> list[Signal]:
        signals = self.repositories.signals.list()
        if not signals:
            signals = self.run_signals()
        return signals

    def _execute_guarded_proposals(
        self,
        *,
        policy: UserPolicy,
        strategy,
        snapshot: PortfolioSnapshot,
        proposals: list[OrderPlan],
    ) -> dict[str, object]:
        submitted: list[dict[str, object]] = []
        blocked: list[dict[str, object]] = []

        for proposal in proposals:
            result = self._authorize_guarded_proposal(policy=policy, strategy=strategy, snapshot=snapshot, proposal=proposal)
            if not result.authorized:
                self._record_guarded_block(policy=policy, proposal=proposal, result=result, blocked=blocked)
                continue
            submitted.append(self._submit_guarded_proposal(policy=policy, proposal=proposal))

        return {"submitted": submitted, "blocked": blocked, "live_trading_enabled": False}

    def _authorize_guarded_proposal(
        self,
        *,
        policy: UserPolicy,
        strategy,
        snapshot: PortfolioSnapshot,
        proposal: OrderPlan,
    ):
        state = self._guardrail_state(policy=policy, strategy_id=strategy.strategy_id, exclude_order_plan_id=proposal.order_plan_id)
        return authorize_level4(
            order_plan=proposal,
            policy=policy,
            strategy=strategy,
            snapshot=snapshot,
            state=state,
            seen_idempotency_keys=self._seen_idempotency_keys(exclude_order_plan_id=proposal.order_plan_id, submitted_only=True),
        )

    def _record_guarded_block(
        self,
        *,
        policy: UserPolicy,
        proposal: OrderPlan,
        result,
        blocked: list[dict[str, object]],
    ) -> None:
        reason = result.first_failed_check or "autopilot_order_blocked"
        proposal.blocked_reason = reason
        self.repositories.order_plans.update(proposal)
        self.last_blocked_reason = reason
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="order_plan",
            entity_id=proposal.order_plan_id,
            action="autopilot_order_blocked",
            after_state={"reason": reason, "checks": result.model_dump(mode="json")},
            source="autopilot_service",
        )
        blocked.append({"order_plan_id": proposal.order_plan_id, "reason": reason})

    def _submit_guarded_proposal(self, *, policy: UserPolicy, proposal: OrderPlan) -> dict[str, object]:
        proposal.approved_by = f"policy_authority_v{policy.version}"
        transition_order_plan(
            order_plan=proposal,
            new_status=OrderStatus.user_approved,
            audit=self.audit,
            user_id=policy.user_id,
            source="autopilot_service",
            action="autopilot_order_authorized",
        )
        self.repositories.order_plans.update(proposal)
        order_plan, broker_order, fills = self.submit_order_plan(proposal.order_plan_id)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="order_plan",
            entity_id=order_plan.order_plan_id,
            action="autopilot_order_submitted",
            after_state=order_plan,
            source="autopilot_service",
        )
        return {
            "order_plan_id": order_plan.order_plan_id,
            "broker_order_id": broker_order.broker_order_id,
            "fills": len(fills),
        }

    def create_daily_report(self, *, policy_id: str) -> OperationReport:
        policy = self.repositories.policies.require(policy_id)
        orders = [order for order in self.repositories.order_plans.list() if order.policy_id == policy_id]
        fills = self.repositories.fills.list()
        report = build_operation_report(
            user_id=policy.user_id,
            policy=policy,
            orders=orders,
            fills=fills,
            repositories=self.repositories,
        )
        self.repositories.operation_reports.add(report)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="operation_report",
            entity_id=report.report_id,
            action="operation_report_generated",
            after_state=report,
            source="report_service",
        )
        return report

    def run_smoke(self, *, user_id: str = "fixture-user") -> dict[str, object]:
        self.repositories.clear()
        policy = self.parse_policy(DEFAULT_POLICY_TEXT, user_id=user_id)
        self.confirm_policy(policy.policy_id)
        signals = self.run_signals()
        snapshot = fixture_portfolio_snapshot()
        portfolio_plan = self.create_portfolio_plan(policy_id=policy.policy_id, signals=signals, snapshot=snapshot)
        orders = self.create_order_plans(portfolio_plan_id=portfolio_plan.plan_id, snapshot=snapshot)
        for order in orders:
            if order.status == OrderStatus.proposed:
                self.approve_order_plan(order.order_plan_id)
                self.submit_order_plan(order.order_plan_id)
        report = self.create_daily_report(policy_id=policy.policy_id)
        return {
            "policy_id": policy.policy_id,
            "broker": policy.broker.value,
            "execution_mode": policy.execution_mode.value,
            "signals": len(signals),
            "portfolio_plan_id": portfolio_plan.plan_id,
            "orders": [
                {"order_plan_id": order.order_plan_id, "status": self.repositories.order_plans.require(order.order_plan_id).status.value}
                for order in orders
            ],
            "fills": len(self.repositories.fills.list()),
            "audit_events": len(self.repositories.audit_logs.list()),
            "report_id": report.report_id,
            "live_trading_enabled": False,
        }
