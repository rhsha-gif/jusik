from __future__ import annotations

from datetime import timedelta

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.brokers.paper_broker import PaperBroker
from quantpilot.packages.core.execution.state_machine import ApprovalRequired, RiskCheckRequired, authorize_level4, transition_order_plan
from quantpilot.packages.core.policy.parser import DEFAULT_POLICY_TEXT, parse_policy_text
from quantpilot.packages.core.analyst.reports import generate_analyst_report
from quantpilot.packages.core.portfolio.planner import (
    build_portfolio_plan,
    build_rebalance_suggestion_report,
    current_weight,
    fixture_portfolio_snapshot,
    proposal_idempotency_key,
)
from quantpilot.packages.core.reports.service import build_operation_report
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
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
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators
from quantpilot.packages.core.universe.builder import build_candidate_universe
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

    def run_level_1_2(self, *, policy_id: str) -> dict[str, object]:
        policy = self.repositories.policies.require(policy_id)
        recipe = self.load_strategy()
        securities = self.security_provider.get_securities()
        universe = build_candidate_universe(policy, securities)
        bars = self.market_data_provider.get_bars()
        signals = generate_signals(recipe, bars, policy=policy, securities=securities)
        price_history = self.market_data_provider.get_price_history()
        indicators = {
            candidate.ticker: calculate_technical_indicators(
                price_history,
                ticker=candidate.ticker,
                signal_date=signals[0].signal_date,
            )
            for candidate in universe
        }
        signals_by_ticker = {signal.symbol: signal for signal in signals}
        analyst_reports = [
            generate_analyst_report(
                candidate=candidate,
                indicator=indicators[candidate.ticker],
                signal=signals_by_ticker.get(candidate.ticker),
            )
            for candidate in universe
        ]

        for signal in signals:
            self.repositories.signals.add(signal)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="signal",
                entity_id=signal.signal_id,
                action="level_2_signal_generated",
                after_state=signal,
                source="level_1_2_signal_engine",
            )

        rebalance_report = build_rebalance_suggestion_report(
            policy=policy,
            signals=signals,
            snapshot=fixture_portfolio_snapshot(),
            quotes={bar["symbol"]: float(bar["close"]) for bar in bars},
        )
        self.repositories.portfolio_plans.add(rebalance_report.portfolio_plan)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="portfolio_plan",
            entity_id=rebalance_report.portfolio_plan.plan_id,
            action="level_2_rebalance_suggestion_created",
            after_state=rebalance_report.portfolio_plan,
            source="level_1_2_rebalance_engine",
        )

        operation_report = OperationReport(
            user_id=policy.user_id,
            policy_id=policy.policy_id,
            summary={
                "level": "1-2",
                "candidate_count": len(universe),
                "analyst_report_count": len(analyst_reports),
                "signal_count": len(signals),
                "rebalance_suggestion_count": len(rebalance_report.suggestions),
                "supported_actions": [action.value for action in sorted({signal.action for signal in signals}, key=lambda item: item.value)],
                "order_submission_enabled": False,
                "broker": policy.broker.value,
                "execution_mode": policy.execution_mode.value,
            },
            order_plan_ids=[],
            fill_ids=[],
            audit_event_count=len(self.repositories.audit_logs.list()),
            live_trading_enabled=False,
        )
        self.repositories.operation_reports.add(operation_report)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="operation_report",
            entity_id=operation_report.report_id,
            action="level_1_2_daily_report_generated",
            after_state=operation_report,
            source="level_1_2_report_service",
        )

        return {
            "policy": policy,
            "strategy": recipe,
            "universe": universe,
            "analyst_reports": analyst_reports,
            "signals": signals,
            "rebalance": rebalance_report,
            "daily_report": operation_report,
            "order_submission_enabled": False,
        }

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
    ) -> list[OrderPlan]:
        portfolio_plan = self.repositories.portfolio_plans.require(portfolio_plan_id)
        policy = self.repositories.policies.require(portfolio_plan.policy_id)
        created: list[OrderPlan] = []
        for intent in portfolio_plan.order_intents:
            order_plan = OrderPlan(
                policy_id=policy.policy_id,
                policy_version=policy.version,
                intent=intent,
                idempotency_key=f"{policy.policy_id}:{portfolio_plan.plan_id}:{intent.intent_id}",
            )
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
                self.apply_risk_check(order_plan.order_plan_id, snapshot=snapshot)
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

    def _seen_idempotency_keys(self, *, exclude_order_plan_id: str | None = None, submitted_only: bool = False) -> set[str]:
        submitted_states = {
            OrderStatus.submitted,
            OrderStatus.accepted,
            OrderStatus.partially_filled,
            OrderStatus.filled,
        }
        keys: set[str] = set()
        for order in self.repositories.order_plans.list():
            if order.order_plan_id == exclude_order_plan_id:
                continue
            if submitted_only and order.status not in submitted_states:
                continue
            keys.add(order.idempotency_key)
        return keys

    def _guardrail_state(
        self,
        *,
        policy: UserPolicy,
        strategy_id: str,
        exclude_order_plan_id: str | None = None,
    ) -> GuardrailState:
        submitted_states = {
            OrderStatus.submitted,
            OrderStatus.accepted,
            OrderStatus.partially_filled,
            OrderStatus.filled,
        }
        unfilled_states = {
            OrderStatus.proposed,
            OrderStatus.user_approved,
            OrderStatus.submitted,
            OrderStatus.accepted,
            OrderStatus.partially_filled,
        }
        submitted_orders = [
            order
            for order in self.repositories.order_plans.list()
            if order.policy_id == policy.policy_id and order.status in submitted_states and order.order_plan_id != exclude_order_plan_id
        ]
        unfilled_order_keys = [
            f"{order.explanation.strategy_id if order.explanation else strategy_id}:{order.intent.symbol}:{order.intent.side}"
            for order in self.repositories.order_plans.list()
            if order.policy_id == policy.policy_id and order.status in unfilled_states and order.order_plan_id != exclude_order_plan_id
        ]
        return GuardrailState(
            daily_order_count=len(submitted_orders),
            daily_turnover_used=round(sum(order.intent.notional for order in submitted_orders), 2),
            kill_switch_engaged=policy.kill_switch_engaged,
            autopilot_paused=self.autopilot_paused,
            last_blocked_reason=self.last_blocked_reason,
            unfilled_order_keys=unfilled_order_keys,
            submitted_idempotency_keys=[order.idempotency_key for order in submitted_orders],
        )

    def _signal_by_symbol(self) -> dict[str, Signal]:
        return {signal.symbol: signal for signal in self.repositories.signals.list()}

    def _latest_strategy_for_signals(self) -> StrategyRecipe:
        strategy = self.load_strategy()
        return strategy

    def generate_order_proposals(
        self,
        *,
        portfolio_plan_id: str,
        snapshot: PortfolioSnapshot | None = None,
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

        for intent in ordered_intents:
            if len(created) >= policy.max_daily_orders:
                break
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
            if key in self._seen_idempotency_keys():
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
            state = self._guardrail_state(policy=policy, strategy_id=strategy_id, exclude_order_plan_id=order_plan.order_plan_id)
            risk_check = run_risk_check(
                policy=policy,
                order_plan=order_plan,
                snapshot=portfolio_snapshot,
                seen_idempotency_keys=self._seen_idempotency_keys(),
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
                    after_state={"failed_checks": risk_check.failed_checks, "idempotency_key": key},
                    source="level3_proposal_service",
                )
                continue

            current = current_weight(portfolio_snapshot, intent.symbol)
            quote_age = (now - intent.quote_time).total_seconds()
            warnings = []
            if quote_age > policy.stale_quote_max_age_seconds:
                warnings.append("stale_quote_warning")
            order_plan.risk_check_id = risk_check.risk_check_id
            order_plan.risk_check_expires_at = risk_check.expires_at
            order_plan.explanation = ProposalExplanation(
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
                idempotency_key=key,
                policy_version=policy.version,
                warnings=warnings,
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

        strategy_id = order_plan.explanation.strategy_id if order_plan.explanation else "unknown_strategy"
        fresh_risk = run_risk_check(
            policy=policy,
            order_plan=order_plan,
            snapshot=fixture_portfolio_snapshot(),
            seen_idempotency_keys=self._seen_idempotency_keys(exclude_order_plan_id=order_plan.order_plan_id, submitted_only=True),
            guardrail_state=self._guardrail_state(policy=policy, strategy_id=strategy_id, exclude_order_plan_id=order_plan.order_plan_id),
            quote_max_age_seconds=policy.stale_quote_max_age_seconds,
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

        if not self.repositories.signals.list():
            self.run_signals()
        snapshot = fixture_portfolio_snapshot()
        plan = self.create_portfolio_plan(policy_id=policy.policy_id, signals=self.repositories.signals.list(), snapshot=snapshot)
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
