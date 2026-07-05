from __future__ import annotations

import hashlib
from datetime import timedelta

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.brokers.paper_broker import PaperBroker
from quantpilot.packages.core.execution.state_machine import (
    ApprovalRequired,
    RiskCheckRequired,
    authorize_level4,
    live_trading_flag_enabled,
    transition_order_plan,
)
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
    OperatorNotification,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    PortfolioPlan,
    PortfolioSnapshot,
    ProposalExplanation,
    Signal,
    StrategyApprovalTicket,
    StrategyApprovalTicketStatus,
    StrategyDraft,
    StrategyDraftStatus,
    StrategyPerformanceRecord,
    StrategyRecipe,
    TradeApprovalTicket,
    UserPolicy,
    ApprovalTicketStatus,
    new_id,
    utc_now,
)
from quantpilot.packages.core.backtest.costs import kis_retail_assumptions
from quantpilot.packages.core.backtest.engine import run_backtest
from quantpilot.packages.core.backtest.replay import replay_signals
from quantpilot.packages.core.backtest.schemas import BacktestRequest, BacktestResult
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

    def _authorize_simulated_order_plan(self, order_plan: OrderPlan, *, source: str) -> OrderPlan:
        policy = self.repositories.policies.require(order_plan.policy_id)
        before = order_plan.model_copy(deep=True)
        order_plan.approved_by = f"{source}:mock_policy_v{policy.version}"
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="order_plan",
            entity_id=order_plan.order_plan_id,
            action="level_1_2_mock_order_authorized",
            before_state=before,
            after_state=order_plan,
            source=source,
        )
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.user_approved,
            audit=self.audit,
            user_id=policy.user_id,
            source=source,
            action="level_1_2_mock_order_authorized",
        )
        return self.repositories.order_plans.update(order_plan)

    def run_level_1_2_mock_execution(self, *, policy_id: str, partial_allow: bool = False) -> dict[str, object]:
        policy = self.repositories.policies.require(policy_id)
        if live_trading_flag_enabled():
            raise RuntimeError("mock execution requires LIVE_TRADING_ENABLED=false")
        if policy.broker != BrokerMode.mock:
            raise RuntimeError("Level 1-2 mock execution requires BrokerMode.mock")

        self.audit.emit(
            user_id=policy.user_id,
            entity_type="policy",
            entity_id=policy.policy_id,
            action="level_1_2_mock_execution_started",
            after_state={"data_mode": "fixture", "broker": policy.broker.value},
            source="level_1_2_mock_execution",
        )
        level_1_2 = self.run_level_1_2(policy_id=policy.policy_id)
        signals = list(level_1_2["signals"])  # type: ignore[arg-type]
        snapshot = fixture_portfolio_snapshot()
        executable_plan = self.create_portfolio_plan(
            policy_id=policy.policy_id,
            signals=signals,
            snapshot=snapshot,
        )
        proposals = self.generate_order_proposals(
            portfolio_plan_id=executable_plan.plan_id,
            snapshot=snapshot,
            partial_allow=partial_allow,
        )

        submitted_order_plans: list[OrderPlan] = []
        broker_orders: list[BrokerOrder] = []
        fills: list[Fill] = []
        blocked_proposals: list[dict[str, object]] = []

        for proposal in proposals:
            try:
                authorized = self._authorize_simulated_order_plan(
                    proposal,
                    source="level_1_2_mock_execution",
                )
                order_plan, broker_order, order_fills = self.submit_order_plan(authorized.order_plan_id)
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="order_plan",
                    entity_id=order_plan.order_plan_id,
                    action="level_1_2_mock_order_submitted",
                    after_state=order_plan,
                    source="level_1_2_mock_execution",
                )
                submitted_order_plans.append(order_plan)
                broker_orders.append(broker_order)
                fills.extend(order_fills)
            except (ApprovalRequired, RiskCheckRequired, RuntimeError) as exc:
                current = self.repositories.order_plans.require(proposal.order_plan_id)
                current.blocked_reason = str(exc)
                self.repositories.order_plans.update(current)
                blocked_proposals.append({"order_plan_id": current.order_plan_id, "reason": str(exc)})

        daily_report = self.create_daily_report(policy_id=policy.policy_id)
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="operation_report",
            entity_id=daily_report.report_id,
            action="level_1_2_mock_execution_completed",
            after_state={
                "submitted_order_plan_ids": [order.order_plan_id for order in submitted_order_plans],
                "broker_order_ids": [order.broker_order_id for order in broker_orders],
                "fill_ids": [fill.fill_id for fill in fills],
                "blocked": blocked_proposals,
                "live_trading_enabled": False,
            },
            source="level_1_2_mock_execution",
        )

        auto_execution = self._build_auto_execution_summary(
            signals=signals,
            proposals=proposals,
            submitted_order_plans=submitted_order_plans,
            broker_orders=broker_orders,
            fills=fills,
            blocked_proposals=blocked_proposals,
        )

        return {
            **level_1_2,
            "portfolio_plan": executable_plan,
            "proposals": proposals,
            "submitted_order_plans": submitted_order_plans,
            "broker_orders": broker_orders,
            "fills": fills,
            "blocked_proposals": blocked_proposals,
            "auto_execution": auto_execution,
            "daily_report": daily_report,
            "data_mode": "fixture",
            "broker": BrokerMode.mock.value,
            "order_submission_enabled": True,
            "live_trading_enabled": False,
        }

    def _build_auto_execution_summary(
        self,
        *,
        signals: list[Signal],
        proposals: list[OrderPlan],
        submitted_order_plans: list[OrderPlan],
        broker_orders: list[BrokerOrder],
        fills: list[Fill],
        blocked_proposals: list[dict[str, object]],
    ) -> dict[str, object]:
        """Summarise the Level 1-2 program-trading run for the operator console.

        Level 1-2 has no human in the loop: the program judges trade timing from
        the signals and submits to the MockBroker automatically. This block makes
        that narrative first-class (per-symbol timing decisions + outcomes) without
        creating any new side effects — every value is derived from objects already
        produced by the run above.
        """
        signals_by_symbol = {signal.symbol: signal for signal in signals}
        broker_by_plan = {order.order_plan_id: order.broker_order_id for order in broker_orders}
        blocked_by_plan = {str(item["order_plan_id"]): str(item["reason"]) for item in blocked_proposals}
        submitted_ids = {order.order_plan_id for order in submitted_order_plans}

        decisions: list[dict[str, object]] = []
        for proposal in proposals:
            signal = signals_by_symbol.get(proposal.intent.symbol)
            executed = proposal.order_plan_id in submitted_ids
            explanation = proposal.explanation
            decisions.append(
                {
                    "order_plan_id": proposal.order_plan_id,
                    "symbol": proposal.intent.symbol,
                    "side": proposal.intent.side,
                    "action": signal.action.value if signal else None,
                    "strength": signal.strength if signal else None,
                    "quantity": proposal.intent.quantity,
                    "notional": proposal.intent.notional,
                    "decision": "executed" if executed else "blocked",
                    "reason": (explanation.signal_reason if explanation else None)
                    or (signal.reason if signal else proposal.intent.reason),
                    "broker_order_id": broker_by_plan.get(proposal.order_plan_id),
                    "blocked_reason": blocked_by_plan.get(proposal.order_plan_id),
                }
            )

        return {
            "mode": "program_auto_trade",
            "executed": bool(submitted_order_plans),
            "signals_evaluated": len(signals),
            "proposals": len(proposals),
            "auto_submitted": len(submitted_order_plans),
            "filled": len(fills),
            "blocked": len(blocked_proposals),
            "filled_notional": round(sum(fill.notional for fill in fills), 2),
            "decisions": decisions,
            "live_trading_enabled": False,
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
        excluded = set(exclude_order_plan_ids or set())
        if exclude_order_plan_id is not None:
            excluded.add(exclude_order_plan_id)
        submitted_states = {
            OrderStatus.submitted,
            OrderStatus.accepted,
            OrderStatus.partially_filled,
            OrderStatus.filled,
        }
        keys: set[str] = set()
        for order in self.repositories.order_plans.list():
            if order.order_plan_id in excluded:
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
        exclude_order_plan_ids: set[str] | None = None,
    ) -> GuardrailState:
        excluded = set(exclude_order_plan_ids or set())
        if exclude_order_plan_id is not None:
            excluded.add(exclude_order_plan_id)
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
            if order.policy_id == policy.policy_id and order.status in submitted_states and order.order_plan_id not in excluded
        ]
        unfilled_order_keys = [
            f"{order.explanation.strategy_id if order.explanation else strategy_id}:{order.intent.symbol}:{order.intent.side}"
            for order in self.repositories.order_plans.list()
            if order.policy_id == policy.policy_id and order.status in unfilled_states and order.order_plan_id not in excluded
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

    def _quotes_for_intents(self, intents: list[OrderIntent]) -> dict[str, float]:
        return {
            intent.symbol: float(intent.limit_price)
            for intent in intents
            if intent.limit_price is not None
        }

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

        candidate_records: list[tuple[OrderPlan, Signal | None, str, str]] = []
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
            candidate_records.append((order_plan, signal, strategy_id, strategy_version))

        if not candidate_records:
            return []

        batch_decision = run_batch_risk_gate(
            policy=policy,
            portfolio_plan=portfolio_plan,
            snapshot=portfolio_snapshot,
            quotes=self._quotes_for_intents([record[0].intent for record in candidate_records]),
            order_plans=[record[0] for record in candidate_records],
            config=BatchRiskConfig(
                partial_allow=partial_allow,
                quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
            ),
            guardrail_state=self._guardrail_state(policy=policy, strategy_id=strategy.strategy_id),
            seen_idempotency_keys=self._seen_idempotency_keys(),
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
        for order_plan, signal, strategy_id, strategy_version in candidate_records:
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

    def _validated_approval_data_mode(self, data_mode: str) -> str:
        allowed = {"fixture", "paper_trading", "live_trading_candidate"}
        if data_mode not in allowed:
            raise RuntimeError(f"unsupported approval data mode: {data_mode}")
        return data_mode

    def _expire_approval_ticket_if_needed(self, ticket: TradeApprovalTicket) -> TradeApprovalTicket:
        if ticket.status == ApprovalTicketStatus.pending and ticket.expires_at <= utc_now():
            before = ticket.model_copy(deep=True)
            ticket.status = ApprovalTicketStatus.expired
            self.repositories.approval_tickets.update(ticket)
            self.audit.emit(
                user_id=ticket.user_id,
                entity_type="approval_ticket",
                entity_id=ticket.ticket_id,
                action="approval_ticket_expired",
                before_state=before,
                after_state=ticket,
                source="approval_ticket_service",
            )
        return ticket

    def _pending_ticket_for_order(self, order_plan_id: str) -> TradeApprovalTicket | None:
        for ticket in self.repositories.approval_tickets.list():
            refreshed = self._expire_approval_ticket_if_needed(ticket)
            if refreshed.order_plan_id == order_plan_id and refreshed.status == ApprovalTicketStatus.pending:
                return refreshed
        return None

    def generate_approval_tickets(
        self,
        *,
        policy_id: str,
        portfolio_plan_id: str | None = None,
        data_mode: str = "live_trading_candidate",
        partial_allow: bool = False,
    ) -> list[TradeApprovalTicket]:
        selected_mode = self._validated_approval_data_mode(data_mode)
        policy = self.repositories.policies.require(policy_id)
        snapshot = fixture_portfolio_snapshot()
        if portfolio_plan_id is None:
            level_1_2 = self.run_level_1_2(policy_id=policy.policy_id)
            signals = list(level_1_2["signals"])  # type: ignore[arg-type]
            portfolio_plan = self.create_portfolio_plan(
                policy_id=policy.policy_id,
                signals=signals,
                snapshot=snapshot,
            )
        else:
            portfolio_plan = self.repositories.portfolio_plans.require(portfolio_plan_id)

        proposals = self.generate_order_proposals(
            portfolio_plan_id=portfolio_plan.plan_id,
            snapshot=snapshot,
            partial_allow=partial_allow,
        )
        tickets: list[TradeApprovalTicket] = []
        for proposal in proposals:
            existing = self._pending_ticket_for_order(proposal.order_plan_id)
            if existing is not None:
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="approval_ticket",
                    entity_id=existing.ticket_id,
                    action="approval_ticket_duplicate_blocked",
                    after_state={"order_plan_id": proposal.order_plan_id},
                    source="approval_ticket_service",
                )
                continue

            ticket = TradeApprovalTicket(
                user_id=policy.user_id,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                order_plan_id=proposal.order_plan_id,
                data_mode=selected_mode,  # type: ignore[arg-type]
                symbol=proposal.intent.symbol,
                side=proposal.intent.side,
                quantity=proposal.intent.quantity,
                limit_price=proposal.intent.limit_price,
                notional=proposal.intent.notional,
                reason=proposal.explanation.signal_reason if proposal.explanation else proposal.intent.reason,
                expires_at=proposal.expires_at or (utc_now() + timedelta(minutes=policy.order_expiry_minutes)),
                live_trading_enabled=False,
            )
            self.repositories.approval_tickets.add(ticket)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="approval_ticket",
                entity_id=ticket.ticket_id,
                action="approval_ticket_created",
                after_state=ticket,
                source="approval_ticket_service",
            )
            tickets.append(ticket)
        return tickets

    def pending_approval_tickets(self) -> list[TradeApprovalTicket]:
        return [
            refreshed
            for ticket in self.repositories.approval_tickets.list()
            if (refreshed := self._expire_approval_ticket_if_needed(ticket)).status == ApprovalTicketStatus.pending
        ]

    def _block_approval_ticket(self, ticket: TradeApprovalTicket, *, reason: str) -> TradeApprovalTicket:
        before = ticket.model_copy(deep=True)
        ticket.status = ApprovalTicketStatus.blocked
        ticket.blocked_reason = reason
        self.repositories.approval_tickets.update(ticket)
        self.audit.emit(
            user_id=ticket.user_id,
            entity_type="approval_ticket",
            entity_id=ticket.ticket_id,
            action="approval_ticket_submission_blocked",
            before_state=before,
            after_state=ticket,
            source="approval_ticket_service",
        )
        return ticket

    def approve_and_submit_approval_ticket(
        self,
        ticket_id: str,
        *,
        approved_by: str = "user",
    ) -> dict[str, object]:
        ticket = self._expire_approval_ticket_if_needed(self.repositories.approval_tickets.require(ticket_id))
        if ticket.status != ApprovalTicketStatus.pending:
            raise RuntimeError(f"approval ticket is not pending: {ticket.status.value}")

        order_plan = self.repositories.order_plans.require(ticket.order_plan_id)
        policy = self.repositories.policies.require(ticket.policy_id)
        before = ticket.model_copy(deep=True)
        ticket.status = ApprovalTicketStatus.approved
        ticket.approved_at = utc_now()
        ticket.approved_by = approved_by
        self.repositories.approval_tickets.update(ticket)
        self.audit.emit(
            user_id=ticket.user_id,
            entity_type="approval_ticket",
            entity_id=ticket.ticket_id,
            action="approval_ticket_approved",
            before_state=before,
            after_state=ticket,
            source="approval_ticket_service",
        )

        if ticket.data_mode == "live_trading_candidate":
            blocked = self._block_approval_ticket(ticket, reason="live_broker_unavailable")
            return {"ticket": blocked, "order_plan": order_plan, "broker_order": None, "fills": [], "live_trading_enabled": False}
        if live_trading_flag_enabled():
            blocked = self._block_approval_ticket(ticket, reason="live_trading_flag_engaged")
            return {"ticket": blocked, "order_plan": order_plan, "broker_order": None, "fills": [], "live_trading_enabled": False}
        if ticket.data_mode == "fixture" and policy.broker != BrokerMode.mock:
            blocked = self._block_approval_ticket(ticket, reason="mock_broker_required")
            return {"ticket": blocked, "order_plan": order_plan, "broker_order": None, "fills": [], "live_trading_enabled": False}
        if ticket.data_mode == "paper_trading" and policy.broker != BrokerMode.paper:
            blocked = self._block_approval_ticket(ticket, reason="paper_broker_required")
            return {"ticket": blocked, "order_plan": order_plan, "broker_order": None, "fills": [], "live_trading_enabled": False}
        if order_plan.intent.side == "buy" and order_plan.explanation is not None:
            budget_ok, budget_detail = self.strategy_capital_budget_check(
                order_plan.explanation.strategy_id,
                proposed_notional=order_plan.intent.notional,
                equity=fixture_portfolio_snapshot().equity,
            )
            if not budget_ok:
                blocked = self._block_approval_ticket(ticket, reason=budget_detail)
                return {"ticket": blocked, "order_plan": order_plan, "broker_order": None, "fills": [], "live_trading_enabled": False}

        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.user_approved,
            audit=self.audit,
            user_id=policy.user_id,
            source="approval_ticket_service",
            action="approval_ticket_approved",
        )
        self.repositories.order_plans.update(order_plan)
        try:
            submitted, broker_order, fills = self.submit_order_plan(order_plan.order_plan_id)
        except (ApprovalRequired, RiskCheckRequired, RuntimeError) as exc:
            blocked = self._block_approval_ticket(ticket, reason=str(exc))
            return {"ticket": blocked, "order_plan": order_plan, "broker_order": None, "fills": [], "live_trading_enabled": False}

        before_submit = ticket.model_copy(deep=True)
        ticket.status = ApprovalTicketStatus.submitted
        ticket.submitted_at = utc_now()
        ticket.submitted_order_plan_id = submitted.order_plan_id
        ticket.broker_order_id = broker_order.broker_order_id
        self.repositories.approval_tickets.update(ticket)
        self.audit.emit(
            user_id=ticket.user_id,
            entity_type="approval_ticket",
            entity_id=ticket.ticket_id,
            action="approval_ticket_submitted",
            before_state=before_submit,
            after_state=ticket,
            source="approval_ticket_service",
        )
        return {
            "ticket": ticket,
            "order_plan": submitted,
            "broker_order": broker_order,
            "fills": fills,
            "live_trading_enabled": False,
        }

    def reject_approval_ticket(self, ticket_id: str, *, reason: str = "user_rejected") -> TradeApprovalTicket:
        ticket = self._expire_approval_ticket_if_needed(self.repositories.approval_tickets.require(ticket_id))
        if ticket.status != ApprovalTicketStatus.pending:
            raise RuntimeError(f"approval ticket is not pending: {ticket.status.value}")
        before = ticket.model_copy(deep=True)
        ticket.status = ApprovalTicketStatus.rejected
        ticket.rejected_at = utc_now()
        ticket.rejection_reason = reason
        self.repositories.approval_tickets.update(ticket)
        order_plan = self.repositories.order_plans.get(ticket.order_plan_id)
        if order_plan is not None and order_plan.status == OrderStatus.proposed:
            self.reject_order_plan(order_plan.order_plan_id, reason=reason)
        self.audit.emit(
            user_id=ticket.user_id,
            entity_type="approval_ticket",
            entity_id=ticket.ticket_id,
            action="approval_ticket_rejected",
            before_state=before,
            after_state=ticket,
            source="approval_ticket_service",
        )
        return ticket

    # --- strategy activation tickets (product vision design doc §4.1) ---

    def record_backtest_result(self, result: BacktestResult) -> BacktestResult:
        self.repositories.backtest_results.add(result)
        self.audit.emit(
            user_id="fixture-user",
            entity_type="backtest_result",
            entity_id=result.result_id,
            action="backtest_result_recorded",
            after_state={
                "strategy_id": result.strategy_id,
                "recipe_version": result.recipe_version,
                "research_only": result.research_only,
            },
            source="strategy_ticket_service",
        )
        return result

    def _strategy_evidence_error(
        self, *, backtest_report_id: str, strategy_id: str, strategy_version: str
    ) -> str | None:
        result = self.repositories.backtest_results.get(backtest_report_id)
        if result is None:
            return f"missing backtest evidence: {backtest_report_id}"
        if result.strategy_id != strategy_id or result.recipe_version != strategy_version:
            return "backtest evidence does not match strategy/version"
        if not result.research_only or result.live_trading_approval:
            return "backtest evidence must be research_only without live approval"
        return None

    def create_strategy_approval_ticket(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        spec_hash: str,
        backtest_report_id: str,
        requested_execution_level: str = "level_3",
        capital_budget_pct: float = 0.2,
        valid_days: int = 30,
        reapproval_triggers: list[str] | None = None,
        user_id: str = "fixture-user",
    ) -> StrategyApprovalTicket:
        error = self._strategy_evidence_error(
            backtest_report_id=backtest_report_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        )
        if error is not None:
            raise RuntimeError(error)

        ticket = StrategyApprovalTicket(
            user_id=user_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            spec_hash=spec_hash,
            backtest_report_id=backtest_report_id,
            requested_execution_level=requested_execution_level,  # type: ignore[arg-type]
            capital_budget_pct=capital_budget_pct,
            valid_until=utc_now() + timedelta(days=valid_days),
            reapproval_triggers=list(reapproval_triggers or []),
        )
        for existing in self.repositories.strategy_approval_tickets.list():
            if existing.strategy_id != strategy_id:
                continue
            if existing.status not in {
                StrategyApprovalTicketStatus.pending,
                StrategyApprovalTicketStatus.approved,
            }:
                continue
            before = existing.model_copy(deep=True)
            existing.status = StrategyApprovalTicketStatus.superseded
            existing.superseded_by = ticket.ticket_id
            self.repositories.strategy_approval_tickets.update(existing)
            self.audit.emit(
                user_id=existing.user_id,
                entity_type="strategy_approval_ticket",
                entity_id=existing.ticket_id,
                action="strategy_ticket_superseded",
                before_state=before,
                after_state=existing,
                source="strategy_ticket_service",
            )
        self.repositories.strategy_approval_tickets.add(ticket)
        self.audit.emit(
            user_id=ticket.user_id,
            entity_type="strategy_approval_ticket",
            entity_id=ticket.ticket_id,
            action="strategy_ticket_created",
            after_state=ticket,
            source="strategy_ticket_service",
        )
        return ticket

    # Pending human confirmation (design doc §3.2): realized MDD beyond this
    # multiple of the backtest evidence MDD forces re-approval.
    DRIFT_MDD_MULTIPLIER = 1.5

    def _notify(
        self,
        *,
        event_type: str,
        message: str,
        severity: str = "warning",
        strategy_id: str | None = None,
        ticket_id: str | None = None,
        user_id: str = "fixture-user",
    ) -> OperatorNotification:
        notification = OperatorNotification(
            user_id=user_id,
            severity=severity,  # type: ignore[arg-type]
            event_type=event_type,  # type: ignore[arg-type]
            strategy_id=strategy_id,
            ticket_id=ticket_id,
            message=message,
        )
        self.repositories.notifications.add(notification)
        self.audit.emit(
            user_id=user_id,
            entity_type="notification",
            entity_id=notification.notification_id,
            action="notification_emitted",
            after_state=notification,
            source="notification_service",
        )
        return notification

    def list_notifications(self, *, unacknowledged_only: bool = False) -> list[OperatorNotification]:
        notifications = sorted(
            self.repositories.notifications.list(), key=lambda item: item.created_at, reverse=True
        )
        if unacknowledged_only:
            notifications = [item for item in notifications if item.acknowledged_at is None]
        return notifications

    def acknowledge_notification(self, notification_id: str) -> OperatorNotification:
        notification = self.repositories.notifications.require(notification_id)
        if notification.acknowledged_at is None:
            before = notification.model_copy(deep=True)
            notification.acknowledged_at = utc_now()
            self.repositories.notifications.update(notification)
            self.audit.emit(
                user_id=notification.user_id,
                entity_type="notification",
                entity_id=notification.notification_id,
                action="notification_acknowledged",
                before_state=before,
                after_state=notification,
                source="notification_service",
            )
        return notification

    def record_strategy_performance(self, record: StrategyPerformanceRecord) -> StrategyPerformanceRecord:
        self.repositories.strategy_performance.add(record)
        self.audit.emit(
            user_id="fixture-user",
            entity_type="strategy_performance",
            entity_id=record.record_id,
            action="strategy_performance_recorded",
            after_state=record,
            source="strategy_ticket_service",
        )
        return record

    def compute_strategy_performance(
        self, strategy_id: str, strategy_version: str
    ) -> StrategyPerformanceRecord | None:
        """Accumulate a fill-sequence PnL curve for one strategy.

        Fills attribute to a strategy through OrderPlan.explanation. The curve
        marks open positions at each fill's trade price (research-only
        approximation: fees excluded, no daily close revaluation), and MDD /
        return are expressed against the cumulative buy notional. Returns
        ``None`` when the strategy has no attributed buy fills yet.
        """
        plans_by_id = {plan.order_plan_id: plan for plan in self.repositories.order_plans.list()}
        attributed: list[tuple[Fill, str]] = []
        for fill in self.repositories.fills.list():
            plan = plans_by_id.get(fill.order_plan_id)
            if plan is None or plan.explanation is None:
                continue
            if (
                plan.explanation.strategy_id == strategy_id
                and plan.explanation.strategy_version == strategy_version
            ):
                attributed.append((fill, plan.intent.side))
        attributed.sort(key=lambda item: item[0].filled_at)

        cash = 0.0
        invested = 0.0
        positions: dict[str, float] = {}
        last_price: dict[str, float] = {}
        curve: list[float] = []
        for fill, side in attributed:
            last_price[fill.symbol] = fill.price
            if side == "buy":
                cash -= fill.notional
                invested += fill.notional
                positions[fill.symbol] = positions.get(fill.symbol, 0.0) + fill.quantity
            else:
                cash += fill.notional
                positions[fill.symbol] = positions.get(fill.symbol, 0.0) - fill.quantity
            equity = cash + sum(
                quantity * last_price[symbol]
                for symbol, quantity in positions.items()
                if quantity > 0
            )
            curve.append(equity)
        if not curve or invested <= 0:
            return None

        peak = 0.0
        drawdown_abs = 0.0
        for value in curve:
            peak = max(peak, value)
            drawdown_abs = max(drawdown_abs, peak - value)
        observation_days = len({fill.filled_at.date() for fill, _ in attributed})
        return StrategyPerformanceRecord(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            realized_max_drawdown=drawdown_abs / invested,
            realized_total_return=curve[-1] / invested,
            observation_days=observation_days,
            source="auto_feed",
        )

    def run_strategy_performance_feed(self) -> list[StrategyPerformanceRecord]:
        """Compute and record performance for every strategy with fills."""
        pairs: set[tuple[str, str]] = set()
        plans_with_fills = {fill.order_plan_id for fill in self.repositories.fills.list()}
        for plan in self.repositories.order_plans.list():
            if plan.order_plan_id in plans_with_fills and plan.explanation is not None:
                pairs.add((plan.explanation.strategy_id, plan.explanation.strategy_version))
        records: list[StrategyPerformanceRecord] = []
        for strategy_id, strategy_version in sorted(pairs):
            record = self.compute_strategy_performance(strategy_id, strategy_version)
            if record is not None:
                records.append(self.record_strategy_performance(record))
        return records

    def _latest_strategy_performance(
        self, strategy_id: str, strategy_version: str
    ) -> StrategyPerformanceRecord | None:
        records = [
            record
            for record in self.repositories.strategy_performance.list()
            if record.strategy_id == strategy_id and record.strategy_version == strategy_version
        ]
        return max(records, key=lambda record: record.as_of) if records else None

    def _drift_trigger_fired(self, ticket: StrategyApprovalTicket) -> str | None:
        record = self._latest_strategy_performance(ticket.strategy_id, ticket.strategy_version)
        if record is None:
            return None
        evidence = self.repositories.backtest_results.get(ticket.backtest_report_id)
        if evidence is None:
            return "backtest_evidence_missing"
        # Zero-MDD evidence means the backtest never drew down, so ANY realized
        # drawdown exceeds the tolerated multiple — fail closed by design.
        limit = evidence.metrics.max_drawdown * self.DRIFT_MDD_MULTIPLIER
        if record.realized_max_drawdown > limit:
            return "mdd_exceeds_backtest_1_5x"
        return None

    def _drift_expire_ticket_if_needed(self, ticket: StrategyApprovalTicket) -> StrategyApprovalTicket:
        if ticket.status != StrategyApprovalTicketStatus.approved:
            return ticket
        fired = self._drift_trigger_fired(ticket)
        if fired is None:
            return ticket
        before = ticket.model_copy(deep=True)
        ticket.status = StrategyApprovalTicketStatus.expired
        if fired not in ticket.reapproval_triggers:
            ticket.reapproval_triggers = [*ticket.reapproval_triggers, fired]
        self.repositories.strategy_approval_tickets.update(ticket)
        self.audit.emit(
            user_id=ticket.user_id,
            entity_type="strategy_approval_ticket",
            entity_id=ticket.ticket_id,
            action="strategy_ticket_drift_expired",
            before_state=before,
            after_state=ticket,
            source="strategy_ticket_service",
        )
        self._notify(
            event_type="strategy_drift_expired",
            severity="critical",
            strategy_id=ticket.strategy_id,
            ticket_id=ticket.ticket_id,
            message=(
                f"strategy {ticket.strategy_id} v{ticket.strategy_version} exceeded its "
                f"drawdown limit ({fired}); approval expired and re-approval is required"
            ),
            user_id=ticket.user_id,
        )
        return ticket

    def _expire_strategy_ticket_if_needed(self, ticket: StrategyApprovalTicket) -> StrategyApprovalTicket:
        ticket = self._drift_expire_ticket_if_needed(ticket)
        active = {StrategyApprovalTicketStatus.pending, StrategyApprovalTicketStatus.approved}
        if ticket.status in active and ticket.valid_until <= utc_now():
            before = ticket.model_copy(deep=True)
            ticket.status = StrategyApprovalTicketStatus.expired
            self.repositories.strategy_approval_tickets.update(ticket)
            self.audit.emit(
                user_id=ticket.user_id,
                entity_type="strategy_approval_ticket",
                entity_id=ticket.ticket_id,
                action="strategy_ticket_expired",
                before_state=before,
                after_state=ticket,
                source="strategy_ticket_service",
            )
            self._notify(
                event_type="strategy_ticket_expired",
                severity="warning",
                strategy_id=ticket.strategy_id,
                ticket_id=ticket.ticket_id,
                message=(
                    f"approval for strategy {ticket.strategy_id} v{ticket.strategy_version} "
                    f"passed valid_until and expired; re-approval is required"
                ),
                user_id=ticket.user_id,
            )
        return ticket

    def pending_strategy_tickets(self) -> list[StrategyApprovalTicket]:
        return [
            refreshed
            for ticket in self.repositories.strategy_approval_tickets.list()
            if (refreshed := self._expire_strategy_ticket_if_needed(ticket)).status
            == StrategyApprovalTicketStatus.pending
        ]

    def approve_strategy_ticket(self, ticket_id: str, *, approved_by: str = "user") -> StrategyApprovalTicket:
        ticket = self._expire_strategy_ticket_if_needed(
            self.repositories.strategy_approval_tickets.require(ticket_id)
        )
        if ticket.status != StrategyApprovalTicketStatus.pending:
            raise RuntimeError(f"strategy ticket is not pending: {ticket.status.value}")
        error = self._strategy_evidence_error(
            backtest_report_id=ticket.backtest_report_id,
            strategy_id=ticket.strategy_id,
            strategy_version=ticket.strategy_version,
        )
        if error is not None:
            raise RuntimeError(error)
        before = ticket.model_copy(deep=True)
        ticket.status = StrategyApprovalTicketStatus.approved
        ticket.approved_at = utc_now()
        ticket.approved_by = approved_by
        self.repositories.strategy_approval_tickets.update(ticket)
        self.audit.emit(
            user_id=ticket.user_id,
            entity_type="strategy_approval_ticket",
            entity_id=ticket.ticket_id,
            action="strategy_ticket_approved",
            before_state=before,
            after_state=ticket,
            source="strategy_ticket_service",
        )
        return ticket

    def reject_strategy_ticket(self, ticket_id: str, *, reason: str = "user_rejected") -> StrategyApprovalTicket:
        ticket = self._expire_strategy_ticket_if_needed(
            self.repositories.strategy_approval_tickets.require(ticket_id)
        )
        if ticket.status != StrategyApprovalTicketStatus.pending:
            raise RuntimeError(f"strategy ticket is not pending: {ticket.status.value}")
        before = ticket.model_copy(deep=True)
        ticket.status = StrategyApprovalTicketStatus.rejected
        ticket.rejected_at = utc_now()
        ticket.rejection_reason = reason
        self.repositories.strategy_approval_tickets.update(ticket)
        self.audit.emit(
            user_id=ticket.user_id,
            entity_type="strategy_approval_ticket",
            entity_id=ticket.ticket_id,
            action="strategy_ticket_rejected",
            before_state=before,
            after_state=ticket,
            source="strategy_ticket_service",
        )
        return ticket

    def revoke_strategy_ticket(self, ticket_id: str, *, reason: str) -> StrategyApprovalTicket:
        ticket = self.repositories.strategy_approval_tickets.require(ticket_id)
        if ticket.status not in {
            StrategyApprovalTicketStatus.pending,
            StrategyApprovalTicketStatus.approved,
        }:
            raise RuntimeError(f"strategy ticket cannot be revoked: {ticket.status.value}")
        before = ticket.model_copy(deep=True)
        ticket.status = StrategyApprovalTicketStatus.revoked
        ticket.revoked_at = utc_now()
        ticket.revoked_reason = reason
        self.repositories.strategy_approval_tickets.update(ticket)
        self.audit.emit(
            user_id=ticket.user_id,
            entity_type="strategy_approval_ticket",
            entity_id=ticket.ticket_id,
            action="strategy_ticket_revoked",
            before_state=before,
            after_state=ticket,
            source="strategy_ticket_service",
        )
        self._notify(
            event_type="strategy_ticket_revoked",
            severity="critical" if reason == "kill_switch_engaged" else "warning",
            strategy_id=ticket.strategy_id,
            ticket_id=ticket.ticket_id,
            message=f"approval for strategy {ticket.strategy_id} was revoked ({reason})",
            user_id=ticket.user_id,
        )
        return ticket

    def _active_strategy_ticket(self, strategy_id: str) -> StrategyApprovalTicket | None:
        for ticket in self.repositories.strategy_approval_tickets.list():
            refreshed = self._expire_strategy_ticket_if_needed(ticket)
            if (
                refreshed.strategy_id == strategy_id
                and refreshed.status == StrategyApprovalTicketStatus.approved
            ):
                return refreshed
        return None

    def _strategy_deployed_notional(self, strategy_id: str) -> float:
        """Net capital currently deployed by a strategy (buys minus sells)."""
        plans_by_id = {plan.order_plan_id: plan for plan in self.repositories.order_plans.list()}
        deployed = 0.0
        for fill in self.repositories.fills.list():
            plan = plans_by_id.get(fill.order_plan_id)
            if plan is None or plan.explanation is None:
                continue
            if plan.explanation.strategy_id != strategy_id:
                continue
            deployed += fill.notional if plan.intent.side == "buy" else -fill.notional
        return max(0.0, deployed)

    def strategy_capital_budget_check(
        self, strategy_id: str, *, proposed_notional: float, equity: float
    ) -> tuple[bool, str]:
        """Enforce the approved ticket's capital budget (design doc §4.4).

        Strategies without an active strategy-level approval are not governed
        by a budget (the per-trade approval rail is the control there), so the
        check passes with an explanatory detail.
        """
        ticket = self._active_strategy_ticket(strategy_id)
        if ticket is None:
            return True, "no_strategy_budget"
        if equity <= 0:
            return False, "no_equity"
        budget = ticket.capital_budget_pct * equity
        deployed = self._strategy_deployed_notional(strategy_id)
        if deployed + proposed_notional > budget:
            return False, (
                f"strategy_capital_budget_exceeded: deployed {deployed:.0f} + "
                f"proposed {proposed_notional:.0f} > budget {budget:.0f} "
                f"({ticket.capital_budget_pct:.0%} of equity)"
            )
        return True, f"within_budget: {deployed + proposed_notional:.0f} <= {budget:.0f}"

    def strategy_activation_allowed(
        self, strategy_id: str, *, execution_level: str
    ) -> tuple[bool, str]:
        """Fail-closed gate: is there an active approval covering this level?"""
        if any(policy.kill_switch_engaged for policy in self.repositories.policies.list()):
            return False, "kill_switch_engaged"
        covered_by_level = {
            "level_3": {"level_3"},
            "level_4": {"level_3", "level_4"},
        }
        for ticket in self.repositories.strategy_approval_tickets.list():
            if ticket.strategy_id != strategy_id:
                continue
            refreshed = self._expire_strategy_ticket_if_needed(ticket)
            if refreshed.status != StrategyApprovalTicketStatus.approved:
                continue
            if execution_level in covered_by_level.get(refreshed.requested_execution_level, set()):
                return True, refreshed.ticket_id
        return False, "no_active_strategy_approval"

    # --- strategy studio (product vision design doc §4.3) ---

    def create_strategy_draft(
        self,
        *,
        symbols: list[str] | None = None,
        sectors: list[str] | None = None,
        note: str = "",
        user_id: str = "fixture-user",
    ) -> StrategyDraft:
        requested_symbols = {str(item).strip().upper() for item in (symbols or []) if str(item).strip()}
        requested_sectors = {str(item).strip().lower() for item in (sectors or []) if str(item).strip()}
        if not requested_symbols and not requested_sectors:
            raise RuntimeError("at least one symbol or sector is required")

        universe: list[str] = []
        for row in self.security_provider.get_securities():
            symbol = str(row.get("ticker") or row.get("symbol") or "").upper()
            sector = str(row.get("sector") or "").lower()
            if not symbol:
                continue
            if symbol in requested_symbols or sector in requested_sectors:
                universe.append(symbol)
        if not universe:
            raise RuntimeError("no universe symbols match the requested symbols/sectors")

        recipe = load_default_strategy()
        spec_hash = hashlib.sha256(
            "|".join([recipe.strategy_id, recipe.version, *sorted(universe)]).encode("utf-8")
        ).hexdigest()[:16]
        rationale = (
            f"rule-based recipe {recipe.strategy_id} v{recipe.version} armed over "
            f"{len(universe)} symbol(s); entries wait for the classifier's setup "
            f"(arming principle - approval is not an immediate buy). {note}"
        ).strip()
        draft = StrategyDraft(
            user_id=user_id,
            strategy_id=recipe.strategy_id,
            strategy_version=recipe.version,
            spec_hash=spec_hash,
            universe_symbols=sorted(set(universe)),
            requested_sectors=sorted(requested_sectors),
            rationale=rationale,
        )
        self.repositories.strategy_drafts.add(draft)
        self.audit.emit(
            user_id=draft.user_id,
            entity_type="strategy_draft",
            entity_id=draft.draft_id,
            action="strategy_draft_created",
            after_state=draft,
            source="strategy_studio_service",
        )
        return draft

    def validate_strategy_draft(self, draft_id: str) -> dict[str, object]:
        draft = self.repositories.strategy_drafts.require(draft_id)
        wanted = set(draft.universe_symbols)
        history = [
            dict(row, symbol=str(row.get("symbol") or row.get("ticker") or "").upper())
            for row in self.market_data_provider.get_price_history()
        ]
        history = [row for row in history if row["symbol"] in wanted]
        if not history:
            raise RuntimeError("no price history covers the draft universe; check DATA_MODE/LOCAL_DATA_DIR")

        signals = replay_signals(history, warmup_bars=20)
        result = run_backtest(
            BacktestRequest(
                strategy_id=draft.strategy_id,
                recipe_version=draft.strategy_version,
                signals=signals,
                assumptions=kis_retail_assumptions(),
            ),
            history,
        )
        self.record_backtest_result(result)
        before = draft.model_copy(deep=True)
        draft.status = StrategyDraftStatus.validated
        draft.backtest_report_id = result.result_id
        draft.validated_at = utc_now()
        self.repositories.strategy_drafts.update(draft)
        self.audit.emit(
            user_id=draft.user_id,
            entity_type="strategy_draft",
            entity_id=draft.draft_id,
            action="strategy_draft_validated",
            before_state=before,
            after_state=draft,
            source="strategy_studio_service",
        )
        return {
            "draft": draft,
            "backtest_report_id": result.result_id,
            "replayed_signals": len(signals),
            "metrics": result.metrics,
            "warnings": list(result.warnings),
            "ticket_ready": True,
        }

    def _broker_for_policy(self, policy: UserPolicy):
        if policy.broker == BrokerMode.paper:
            return PaperBroker()
        if policy.broker == BrokerMode.mock:
            return MockBroker()
        raise RuntimeError("live broker mode is disabled in the pre-harness")

    def _orders_for_submit_batch(self, order_plan: OrderPlan) -> list[OrderPlan]:
        batch_statuses = {
            OrderStatus.proposed,
            OrderStatus.user_approved,
            OrderStatus.submitted,
            OrderStatus.accepted,
            OrderStatus.partially_filled,
            OrderStatus.filled,
        }
        batch: list[OrderPlan] = []
        current_seen = False
        for existing in self.repositories.order_plans.list():
            if existing.policy_id != order_plan.policy_id or existing.status not in batch_statuses:
                continue
            if existing.order_plan_id == order_plan.order_plan_id:
                batch.append(order_plan)
                current_seen = True
            else:
                batch.append(existing)
        if not current_seen:
            batch.append(order_plan)
        return batch

    def _portfolio_plan_for_order_batch(self, *, policy: UserPolicy, order_plans: list[OrderPlan]) -> PortfolioPlan:
        return PortfolioPlan(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            target_weights={},
            cash_target_weight=0.0,
            order_intents=[order.intent for order in order_plans],
        )

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

        batch_orders = self._orders_for_submit_batch(order_plan)
        batch_order_ids = {order.order_plan_id for order in batch_orders}
        batch_decision = run_batch_risk_gate(
            policy=policy,
            portfolio_plan=self._portfolio_plan_for_order_batch(policy=policy, order_plans=batch_orders),
            snapshot=fixture_portfolio_snapshot(),
            quotes=self._quotes_for_intents([order.intent for order in batch_orders]),
            order_plans=batch_orders,
            config=BatchRiskConfig(quote_max_age_seconds=policy.stale_quote_max_age_seconds),
            guardrail_state=self._guardrail_state(
                policy=policy,
                strategy_id=strategy_id,
                exclude_order_plan_ids=batch_order_ids,
            ),
            seen_idempotency_keys=self._seen_idempotency_keys(
                exclude_order_plan_ids=batch_order_ids,
                submitted_only=True,
            ),
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
        self._notify(
            event_type="kill_switch_engaged",
            severity="critical",
            message=f"kill switch engaged ({reason}); all armed strategies are being revoked",
            user_id=policy.user_id,
        )
        # Strategy-level approvals are armed authority; the kill switch must
        # revoke them too (design doc §4.5). They stay revoked after release —
        # re-arming requires a fresh approval ticket.
        for ticket in self.repositories.strategy_approval_tickets.list():
            if ticket.status in {
                StrategyApprovalTicketStatus.pending,
                StrategyApprovalTicketStatus.approved,
            }:
                self.revoke_strategy_ticket(ticket.ticket_id, reason="kill_switch_engaged")
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
