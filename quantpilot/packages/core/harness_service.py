from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.brokers.paper_broker import PaperBroker
from quantpilot.packages.core.execution.state_machine import ApprovalRequired, RiskCheckRequired, authorize_level4, transition_order_plan
from quantpilot.packages.core.execution.order_context import (
    SubmitBatchContext,
    build_guardrail_state,
    build_submit_batch_context,
    collect_seen_idempotency_keys,
    orders_for_submit_batch,
    quotes_for_intents,
)
from quantpilot.packages.core.policy.parser import DEFAULT_POLICY_TEXT, parse_policy_text
from quantpilot.packages.core.level12.service import Level12RunResult, Level12Service
from quantpilot.packages.core.portfolio.planner import (
    build_portfolio_plan,
    current_weight,
    fixture_portfolio_snapshot,
    proposal_idempotency_key,
)
from quantpilot.packages.core.reports.service import build_operation_report
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.risk.types import BatchRiskConfig, BatchRiskDecision
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
    ProposalExplanation,
    RiskCheck,
    Signal,
    StrategyRecipe,
    UserPolicy,
    new_id,
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


@dataclass(frozen=True)
class _ProposalCandidate:
    order_plan: OrderPlan
    signal: Signal | None
    strategy_id: str
    strategy_version: str


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
        candidate_orders = [
            OrderPlan(
                policy_id=policy.policy_id,
                policy_version=policy.version,
                intent=intent,
                idempotency_key=f"{policy.policy_id}:{portfolio_plan.plan_id}:{intent.intent_id}",
            )
            for intent in portfolio_plan.order_intents
        ]
        accepted_order_ids = {order.order_plan_id for order in candidate_orders}
        if run_risk:
            decision = run_batch_risk_gate(
                policy=policy,
                portfolio_plan=portfolio_plan,
                snapshot=portfolio_snapshot,
                quotes=self._quotes_for_intents([order.intent for order in candidate_orders]),
                order_plans=candidate_orders,
                config=BatchRiskConfig(
                    partial_allow=partial_allow,
                    quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
                ),
                guardrail_state=self._guardrail_state(policy=policy, strategy_id="order_planner_stub"),
                seen_idempotency_keys=self._seen_idempotency_keys(),
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
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="portfolio_plan",
                    entity_id=portfolio_plan.plan_id,
                    action="batch_risk_partial_allowed",
                    after_state=decision,
                    source="batch_risk_gate",
                )

        created: list[OrderPlan] = []
        for order_plan in candidate_orders:
            if order_plan.order_plan_id not in accepted_order_ids:
                continue
            self.repositories.order_plans.add(order_plan)
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

    def _signal_by_symbol(self) -> dict[str, Signal]:
        return {signal.symbol: signal for signal in self.repositories.signals.list()}

    def _latest_strategy_for_signals(self) -> StrategyRecipe:
        strategy = self.load_strategy()
        return strategy

    def _quotes_for_intents(self, intents: list[OrderIntent]) -> dict[str, float]:
        return quotes_for_intents(intents)

    def _proposal_explanation(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        signal: Signal | None,
        strategy_id: str,
        strategy_version: str,
        snapshot: PortfolioSnapshot,
        risk_check: RiskCheck,
        now: datetime,
    ) -> ProposalExplanation:
        intent = order_plan.intent
        current = current_weight(snapshot, intent.symbol)
        quote_age = (now - intent.quote_time).total_seconds()
        warnings = []
        if quote_age > policy.stale_quote_max_age_seconds:
            warnings.append("stale_quote_warning")
        return ProposalExplanation(
            symbol=intent.symbol,
            action=intent.side,
            quantity=intent.quantity,
            target_weight_delta=round(intent.target_weight - current, 6),
            reference_price=float(intent.limit_price or 0),
            estimated_cash_impact=round(intent.notional if intent.side == "buy" else -intent.notional, 2),
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            signal_reason=signal.reason if signal else intent.reason,
            reason_codes=signal.reason_codes if signal else [],
            current_weight=round(current, 6),
            target_weight=intent.target_weight,
            weight_delta=round(intent.target_weight - current, 6),
            quote_price=float(intent.limit_price or 0),
            quote_age_seconds=round(max(0.0, quote_age), 3),
            limit_price=intent.limit_price,
            estimated_notional=intent.notional,
            stop_price_hint=signal.stop_price_hint if signal else None,
            take_profit_hint=signal.take_profit_hint if signal else None,
            risk_checks_passed=risk_check.passed_checks,
            risk_checks_failed=risk_check.failed_checks,
            risk_check_id=risk_check.risk_check_id,
            risk_check_expires_at=risk_check.expires_at,
            idempotency_key=order_plan.idempotency_key,
            policy_version=policy.version,
            warnings=warnings,
        )

    def generate_order_proposals(
        self,
        *,
        portfolio_plan_id: str,
        snapshot: PortfolioSnapshot | None = None,
        partial_allow: bool = False,
    ) -> list[OrderPlan]:
        portfolio_plan = self.repositories.portfolio_plans.require(portfolio_plan_id)
        policy = self.repositories.policies.require(portfolio_plan.policy_id)
        strategy = self._latest_strategy_for_signals()
        signals_by_symbol = self._signal_by_symbol()
        portfolio_snapshot = snapshot or fixture_portfolio_snapshot()
        now = utc_now()
        created: list[OrderPlan] = []

        if policy.kill_switch_engaged:
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="portfolio_plan",
                entity_id=portfolio_plan.plan_id,
                action="proposal_blocked",
                after_state={"reason": "kill_switch_not_engaged"},
                source="level3_proposal_service",
            )
            return []

        ordered_intents = sorted(
            portfolio_plan.order_intents,
            key=lambda intent: abs(intent.target_weight - current_weight(portfolio_snapshot, intent.symbol)),
            reverse=True,
        )
        if not ordered_intents:
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="portfolio_plan",
                entity_id=portfolio_plan.plan_id,
                action="proposal_blocked",
                after_state={"reason": "no_order_intents"},
                source="level3_proposal_service",
            )
            return []

        existing_seen_keys = self._seen_idempotency_keys()
        candidate_records: list[_ProposalCandidate] = []
        for intent in ordered_intents:
            signal = signals_by_symbol.get(intent.symbol)
            strategy_id = signal.strategy_id if signal else strategy.strategy_id
            strategy_version = signal.recipe_version if signal else strategy.version
            trading_date = signal.signal_date if signal else now.date()
            key = proposal_idempotency_key(
                policy=policy,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                symbol=intent.symbol,
                side=intent.side,
                trading_date=trading_date,
            )
            if key in existing_seen_keys:
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="portfolio_plan",
                    entity_id=portfolio_plan.plan_id,
                    action="proposal_blocked",
                    after_state={"reason": "duplicate_order_blocked", "idempotency_key": key},
                    source="level3_proposal_service",
                )
                continue

            order_plan = OrderPlan(
                policy_id=policy.policy_id,
                policy_version=policy.version,
                intent=intent,
                idempotency_key=key,
                auto_order_reference_price=intent.limit_price,
                expires_at=now + timedelta(minutes=policy.order_expiry_minutes),
            )
            candidate_records.append(
                _ProposalCandidate(
                    order_plan=order_plan,
                    signal=signal,
                    strategy_id=strategy_id,
                    strategy_version=strategy_version,
                )
            )

        if not candidate_records:
            return []

        candidate_orders = [candidate.order_plan for candidate in candidate_records]
        batch_decision = run_batch_risk_gate(
            policy=policy,
            portfolio_plan=portfolio_plan,
            snapshot=portfolio_snapshot,
            quotes=self._quotes_for_intents([order.intent for order in candidate_orders]),
            order_plans=candidate_orders,
            config=BatchRiskConfig(
                partial_allow=partial_allow,
                quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
            ),
            guardrail_state=self._guardrail_state(policy=policy, strategy_id=strategy.strategy_id),
            seen_idempotency_keys=existing_seen_keys,
            now=now,
        )
        if not batch_decision.passed:
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="portfolio_plan",
                entity_id=portfolio_plan.plan_id,
                action="batch_risk_rejected",
                after_state=batch_decision,
                source="batch_risk_gate",
            )
            return []
        if batch_decision.mode == "partial_batch":
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="portfolio_plan",
                entity_id=portfolio_plan.plan_id,
                action="batch_risk_partial_allowed",
                after_state=batch_decision,
                source="batch_risk_gate",
            )

        accepted_order_ids = set(batch_decision.accepted_order_plan_ids)
        seen_keys = set(existing_seen_keys)
        for candidate in candidate_records:
            order_plan = candidate.order_plan
            signal = candidate.signal
            strategy_id = candidate.strategy_id
            strategy_version = candidate.strategy_version
            if order_plan.order_plan_id not in accepted_order_ids:
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="order_plan",
                    entity_id=order_plan.order_plan_id,
                    action="proposal_blocked",
                    after_state={
                        "reason": "batch_risk_rejected",
                        "batch_reasons": batch_decision.rejected_reasons.get(order_plan.order_plan_id, []),
                    },
                    source="batch_risk_gate",
                )
                continue

            intent = order_plan.intent
            state = self._guardrail_state(policy=policy, strategy_id=strategy_id, exclude_order_plan_id=order_plan.order_plan_id)
            risk_check = run_risk_check(
                policy=policy,
                order_plan=order_plan,
                snapshot=portfolio_snapshot,
                seen_idempotency_keys=seen_keys,
                guardrail_state=state,
                quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
                strategy_id=strategy_id,
            )
            if not risk_check.passed:
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="order_plan",
                    entity_id=order_plan.order_plan_id,
                    action="proposal_blocked",
                    after_state={"failed_checks": risk_check.failed_checks, "idempotency_key": order_plan.idempotency_key},
                    source="level3_proposal_service",
                )
                continue

            order_plan.risk_check_id = risk_check.risk_check_id
            order_plan.risk_check_expires_at = risk_check.expires_at
            order_plan.explanation = self._proposal_explanation(
                policy=policy,
                order_plan=order_plan,
                signal=signal,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                snapshot=portfolio_snapshot,
                risk_check=risk_check,
                now=now,
            )
            self.repositories.order_plans.add(order_plan)
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.risk_checked,
                audit=self.audit,
                user_id=policy.user_id,
                source="risk_gatekeeper",
                action="risk_check_passed",
            )
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.proposed,
                audit=self.audit,
                user_id=policy.user_id,
                source="level3_proposal_service",
                action="proposal_created",
            )
            created.append(self.repositories.order_plans.update(order_plan))
            seen_keys.add(order_plan.idempotency_key)
        return created

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
        return self.repositories.order_plans.update(order_plan)

    def modify_order_plan(self, order_plan_id: str, *, quantity: float, limit_price: float | None) -> OrderPlan:
        original = self.repositories.order_plans.require(order_plan_id)
        policy = self.repositories.policies.require(original.policy_id)
        if original.status != OrderStatus.proposed:
            raise RuntimeError("only proposed orders can be modified")
        if quantity <= 0 or quantity > original.intent.quantity:
            raise RuntimeError("quantity can only be reduced")
        if original.intent.limit_price is not None and limit_price is not None:
            lower = original.intent.limit_price * 0.98
            upper = original.intent.limit_price * 1.02
            if not lower <= limit_price <= upper:
                raise RuntimeError("limit_price modification must stay within 2 percent")
            if original.intent.side == "buy" and original.auto_order_reference_price is not None and limit_price > original.auto_order_reference_price:
                raise RuntimeError("buy limit price cannot chase above the reference price")

        modified_intent = OrderIntent(
            symbol=original.intent.symbol,
            side=original.intent.side,
            order_type=original.intent.order_type,
            quantity=quantity,
            limit_price=limit_price,
            notional=round(quantity * (limit_price or original.intent.limit_price or 0), 2),
            target_weight=original.intent.target_weight,
            reason=original.intent.reason,
            quote_time=utc_now(),
        )
        new_order = OrderPlan(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            intent=modified_intent,
            idempotency_key=f"{original.idempotency_key}:mod:{new_id('mod')}",
            auto_order_reference_price=original.auto_order_reference_price,
            replaces_order_plan_id=original.order_plan_id,
            expires_at=utc_now() + timedelta(minutes=policy.order_expiry_minutes),
        )
        strategy_id = original.explanation.strategy_id if original.explanation else "unknown_strategy"
        risk_check = run_risk_check(
            policy=policy,
            order_plan=new_order,
            snapshot=fixture_portfolio_snapshot(),
            seen_idempotency_keys=self._seen_idempotency_keys(),
            guardrail_state=self._guardrail_state(policy=policy, strategy_id=strategy_id, exclude_order_plan_id=original.order_plan_id),
            quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
            strategy_id=strategy_id,
        )
        if not risk_check.passed:
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=original.order_plan_id,
                action="proposal_blocked",
                after_state={"reason": "modified_proposal_failed_risk", "failed_checks": risk_check.failed_checks},
                source="user_modification",
            )
            raise RiskCheckRequired("modified proposal failed risk check")

        new_order.risk_check_id = risk_check.risk_check_id
        new_order.risk_check_expires_at = risk_check.expires_at
        if original.explanation is not None:
            new_order.explanation = original.explanation.model_copy(
                update={
                    "quantity": modified_intent.quantity,
                    "limit_price": modified_intent.limit_price,
                    "estimated_notional": modified_intent.notional,
                    "estimated_cash_impact": modified_intent.notional if modified_intent.side == "buy" else -modified_intent.notional,
                    "risk_checks_passed": risk_check.passed_checks,
                    "risk_checks_failed": risk_check.failed_checks,
                    "risk_check_id": risk_check.risk_check_id,
                    "risk_check_expires_at": risk_check.expires_at,
                    "idempotency_key": new_order.idempotency_key,
                }
            )

        transition_order_plan(
            order_plan=original,
            new_status=OrderStatus.modified,
            audit=self.audit,
            user_id=policy.user_id,
            source="user_modification",
            action="proposal_modified",
        )
        self.repositories.order_plans.update(original)
        self.repositories.order_plans.add(new_order)
        transition_order_plan(
            order_plan=new_order,
            new_status=OrderStatus.risk_checked,
            audit=self.audit,
            user_id=policy.user_id,
            source="risk_gatekeeper",
            action="risk_check_passed",
        )
        transition_order_plan(
            order_plan=new_order,
            new_status=OrderStatus.proposed,
            audit=self.audit,
            user_id=policy.user_id,
            source="user_modification",
            action="proposal_created",
        )
        return self.repositories.order_plans.update(new_order)

    def _broker_for_policy(self, policy: UserPolicy):
        if policy.broker == BrokerMode.paper:
            return PaperBroker()
        if policy.broker == BrokerMode.mock:
            return MockBroker()
        raise RuntimeError("live broker mode is disabled in the pre-harness")

    def _orders_for_submit_batch(self, order_plan: OrderPlan) -> list[OrderPlan]:
        return orders_for_submit_batch(self.repositories.order_plans.list(), order_plan)

    def _submit_batch_context(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        strategy_id: str,
    ) -> SubmitBatchContext:
        return build_submit_batch_context(
            order_plans=self.repositories.order_plans.list(),
            order_plan=order_plan,
            policy=policy,
            strategy_id=strategy_id,
            autopilot_paused=self.autopilot_paused,
            last_blocked_reason=self.last_blocked_reason,
        )

    def _portfolio_plan_for_order_batch(self, *, policy: UserPolicy, order_plans: list[OrderPlan]) -> PortfolioPlan:
        return PortfolioPlan(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            target_weights={},
            cash_target_weight=0.0,
            order_intents=[order.intent for order in order_plans],
        )

    def _strategy_id_for_order(self, order_plan: OrderPlan) -> str:
        return order_plan.explanation.strategy_id if order_plan.explanation else "unknown_strategy"

    def _fresh_submission_risk_check(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        snapshot: PortfolioSnapshot,
        strategy_id: str,
    ):
        return run_risk_check(
            policy=policy,
            order_plan=order_plan,
            snapshot=snapshot,
            seen_idempotency_keys=self._seen_idempotency_keys(exclude_order_plan_id=order_plan.order_plan_id, submitted_only=True),
            guardrail_state=self._guardrail_state(policy=policy, strategy_id=strategy_id, exclude_order_plan_id=order_plan.order_plan_id),
            quote_max_age_seconds=policy.stale_quote_max_age_seconds,
            strategy_id=strategy_id,
        )

    def _submit_batch_risk_decision(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        snapshot: PortfolioSnapshot,
        strategy_id: str,
    ) -> BatchRiskDecision:
        context = self._submit_batch_context(
            policy=policy,
            order_plan=order_plan,
            strategy_id=strategy_id,
        )
        return run_batch_risk_gate(
            policy=policy,
            portfolio_plan=self._portfolio_plan_for_order_batch(policy=policy, order_plans=context.batch_orders),
            snapshot=snapshot,
            quotes=context.quotes,
            order_plans=context.batch_orders,
            config=BatchRiskConfig(quote_max_age_seconds=policy.stale_quote_max_age_seconds),
            guardrail_state=context.guardrail_state,
            seen_idempotency_keys=context.seen_idempotency_keys,
        )

    def _submit_to_broker(self, *, policy: UserPolicy, order_plan: OrderPlan) -> tuple[OrderPlan, BrokerOrder, list[Fill]]:
        broker = self._broker_for_policy(policy)
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.submitted,
            audit=self.audit,
            user_id=policy.user_id,
            source="execution_service",
        )
        broker_order, fills = broker.submit_order(order_plan)
        self.repositories.broker_orders.add(broker_order)
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.accepted,
            audit=self.audit,
            user_id=policy.user_id,
            source="broker_adapter",
        )
        for fill in fills:
            self.repositories.fills.add(fill)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="fill",
                entity_id=fill.fill_id,
                action="fill_recorded",
                after_state=fill,
                source="broker_adapter",
            )
        if len(fills) > 1:
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.partially_filled,
                audit=self.audit,
                user_id=policy.user_id,
                source="broker_adapter",
            )
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.filled,
            audit=self.audit,
            user_id=policy.user_id,
            source="broker_adapter",
        )
        self.repositories.order_plans.update(order_plan)
        return order_plan, broker_order, fills

    def submit_order_plan(self, order_plan_id: str) -> tuple[OrderPlan, BrokerOrder, list[Fill]]:
        order_plan = self.repositories.order_plans.require(order_plan_id)
        policy = self.repositories.policies.require(order_plan.policy_id)

        if order_plan.risk_check_id is None or order_plan.status == OrderStatus.draft:
            raise RiskCheckRequired("risk_checked is required before submission")
        if order_plan.risk_check_expires_at is not None and order_plan.risk_check_expires_at <= utc_now():
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.expired,
                audit=self.audit,
                user_id=policy.user_id,
                source="execution_service",
                action="risk_check_expired",
            )
            self.repositories.order_plans.update(order_plan)
            raise RiskCheckRequired("fresh risk check is required before submission")
        if policy.execution_mode.value == "approval_required" and order_plan.status != OrderStatus.user_approved:
            raise ApprovalRequired("explicit user approval is required before submission")

        strategy_id = self._strategy_id_for_order(order_plan)
        snapshot = fixture_portfolio_snapshot()
        fresh_risk = self._fresh_submission_risk_check(
            policy=policy,
            order_plan=order_plan,
            snapshot=snapshot,
            strategy_id=strategy_id,
        )
        if not fresh_risk.passed:
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=order_plan.order_plan_id,
                action="risk_check_failed",
                before_state=order_plan,
                after_state={"failed_checks": fresh_risk.failed_checks},
                source="execution_service",
            )
            raise RiskCheckRequired(f"fresh risk check failed: {fresh_risk.failed_checks}")
        order_plan.risk_check_id = fresh_risk.risk_check_id
        order_plan.risk_check_expires_at = fresh_risk.expires_at

        batch_decision = self._submit_batch_risk_decision(
            policy=policy,
            order_plan=order_plan,
            snapshot=snapshot,
            strategy_id=strategy_id,
        )
        if not batch_decision.passed or order_plan.order_plan_id not in set(batch_decision.accepted_order_plan_ids):
            before_blocked = order_plan.model_copy(deep=True)
            order_plan.blocked_reason = "batch_risk_rejected"
            self.repositories.order_plans.update(order_plan)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=order_plan.order_plan_id,
                action="batch_risk_rejected",
                before_state=before_blocked,
                after_state=batch_decision,
                source="execution_service",
            )
            raise RiskCheckRequired(f"batch risk check failed: {batch_decision.failed_checks}")

        return self._submit_to_broker(policy=policy, order_plan=order_plan)

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

        signals = self.repositories.signals.list()
        if not signals:
            signals = self.run_signals()
        snapshot = fixture_portfolio_snapshot()
        plan = self.create_portfolio_plan(policy_id=policy.policy_id, signals=signals, snapshot=snapshot)
        proposals = self.generate_order_proposals(portfolio_plan_id=plan.plan_id, snapshot=snapshot)
        strategy = self.load_strategy()
        submitted: list[dict[str, object]] = []
        blocked: list[dict[str, object]] = []

        for proposal in proposals:
            state = self._guardrail_state(policy=policy, strategy_id=strategy.strategy_id, exclude_order_plan_id=proposal.order_plan_id)
            result = authorize_level4(
                order_plan=proposal,
                policy=policy,
                strategy=strategy,
                snapshot=snapshot,
                state=state,
                seen_idempotency_keys=self._seen_idempotency_keys(exclude_order_plan_id=proposal.order_plan_id, submitted_only=True),
            )
            if not result.authorized:
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
                continue

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
            submitted.append(
                {
                    "order_plan_id": order_plan.order_plan_id,
                    "broker_order_id": broker_order.broker_order_id,
                    "fills": len(fills),
                }
            )

        if not proposals and not blocked:
            blocked.append({"reason": self.last_blocked_reason or "no_proposals"})
        return {"submitted": submitted, "blocked": blocked, "live_trading_enabled": False}

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
