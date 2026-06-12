from __future__ import annotations

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.brokers.paper_broker import PaperBroker
from quantpilot.packages.core.execution.state_machine import ApprovalRequired, RiskCheckRequired, transition_order_plan
from quantpilot.packages.core.policy.parser import DEFAULT_POLICY_TEXT, parse_policy_text
from quantpilot.packages.core.analyst.reports import generate_analyst_report
from quantpilot.packages.core.portfolio.planner import build_portfolio_plan, build_rebalance_suggestion_report, fixture_portfolio_snapshot
from quantpilot.packages.core.reports.service import build_operation_report
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    Fill,
    OperationReport,
    OrderPlan,
    OrderStatus,
    PortfolioPlan,
    PortfolioSnapshot,
    Signal,
    UserPolicy,
)
from quantpilot.packages.core.signals.service import generate_signals, load_fixture_ohlcv
from quantpilot.packages.core.strategies.loader import load_default_strategy
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators, fixture_price_history
from quantpilot.packages.core.universe.builder import build_candidate_universe
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


class HarnessService:
    def __init__(self, repositories: RepositoryRegistry | None = None) -> None:
        self.repositories = repositories or RepositoryRegistry()
        self.audit = AuditRecorder(self.repositories.audit_logs)

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
        bars = load_fixture_ohlcv()
        signals = generate_signals(recipe, bars)
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
        universe = build_candidate_universe(policy)
        bars = load_fixture_ohlcv()
        signals = generate_signals(recipe, bars, policy=policy)
        price_history = fixture_price_history()
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
        quotes = {bar["symbol"]: float(bar["close"]) for bar in load_fixture_ohlcv()}
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
        )
        return self.repositories.order_plans.update(order_plan)

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
        if policy.execution_mode.value == "approval_required" and order_plan.status != OrderStatus.user_approved:
            raise ApprovalRequired("explicit user approval is required before submission")

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
