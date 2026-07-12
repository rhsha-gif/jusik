from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from math import isclose, isfinite
from typing import Callable
from zoneinfo import ZoneInfo

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.brokers.paper_broker import PaperBroker
from quantpilot.packages.brokers.kis_paper import KisPaperBrokerAdapter
from quantpilot.packages.core.execution.paper_submission import (
    DurablePaperSubmissionCoordinator,
)
from quantpilot.packages.core.execution.state_machine import (
    ApprovalRequired,
    RiskCheckRequired,
    authorize_level4,
    live_trading_flag_enabled,
    operator_kill_switch_engaged,
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
from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionBinding,
    OperatorSafetyState,
    PendingLiquidationCheckpoint,
)
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.reports.service import build_operation_report
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.risk.types import BatchRiskConfig
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    DataMode,
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
from quantpilot.packages.core.backtest.costs import (
    KIS_BANKIS_ONLINE_FEE_BPS,
    KIS_RETAIL_COST_BASIS,
    KRX_SELL_TAX_BPS_FROM_2026,
    kis_retail_assumptions,
)
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
from quantpilot.packages.core.data.mode import resolve_data_mode
from quantpilot.packages.core.data.quality import ExchangeCalendar, SimpleKrxCalendar
from quantpilot.packages.core.signals.service import generate_signals
from quantpilot.packages.core.strategies.loader import load_default_strategy
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators
from quantpilot.packages.core.universe.builder import build_candidate_universe
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


_SAFETY_FIELD_UNSET = object()
_PERFORMANCE_RECORD_UNSET = object()
_KST = ZoneInfo("Asia/Seoul")
_KRX_REGULAR_CLOSE = time(15, 30)
_KRX_CLOSE_FINALITY_DELAY = timedelta(minutes=30)


def _latest_completed_krx_session(
    observed_at: datetime,
    calendar: ExchangeCalendar,
) -> date:
    local = observed_at.astimezone(_KST)
    session = local.date()
    finalized_at = datetime.combine(
        session,
        _KRX_REGULAR_CLOSE,
        tzinfo=_KST,
    ) + _KRX_CLOSE_FINALITY_DELAY
    if local < finalized_at:
        session -= timedelta(days=1)
    completed = calendar.previous_trading_session(session)
    if completed is None:
        raise RuntimeError("performance calendar has no completed KRX session")
    return completed


class HarnessService:
    def __init__(
        self,
        repositories: RepositoryRegistry | None = None,
        *,
        security_provider: SecurityProvider | None = None,
        market_data_provider: MarketDataProvider | None = None,
        data_mode: DataMode = DataMode.fixture,
        performance_clock: Callable[[], datetime] = utc_now,
        performance_calendar: ExchangeCalendar | None = None,
        pending_liquidation_provider: object | None = None,
        external_paper_broker: KisPaperBrokerAdapter | None = None,
        paper_submission_coordinator: DurablePaperSubmissionCoordinator | None = None,
    ) -> None:
        if (external_paper_broker is None) != (
            paper_submission_coordinator is None
        ):
            raise ValueError(
                "external paper broker and durable coordinator must be configured together"
            )
        if (
            external_paper_broker is not None
            and external_paper_broker.submission_gateway
            is not paper_submission_coordinator
        ):
            raise ValueError(
                "external paper broker must use the configured durable coordinator"
            )
        self.repositories = repositories or RepositoryRegistry()
        self.audit = AuditRecorder(self.repositories.audit_logs)
        self.autopilot_paused = False
        self.broker_healthy = True
        self.last_blocked_reason: str | None = None
        # Fixtures stay the default; local/historical providers are injected explicitly.
        self.security_provider: SecurityProvider = security_provider or FixtureSecurityProvider()
        self.market_data_provider: MarketDataProvider = market_data_provider or FixtureMarketDataProvider()
        self.data_mode = data_mode
        self.performance_clock = performance_clock
        provider_calendar = getattr(self.market_data_provider, "exchange_calendar", None)
        self.performance_calendar = (
            performance_calendar
            or provider_calendar
            or SimpleKrxCalendar()
        )
        self.pending_liquidation_provider = pending_liquidation_provider
        self.external_paper_broker = external_paper_broker
        self.paper_submission_coordinator = paper_submission_coordinator
        self.paper_dispatch_provider = (
            paper_submission_coordinator.store
            if paper_submission_coordinator is not None
            else None
        )
        self.operator_safety_state_provider: object | None = None

    @property
    def external_paper_enabled(self) -> bool:
        return (
            self.external_paper_broker is not None
            and self.paper_submission_coordinator is not None
        )

    @classmethod
    def from_environment(cls, repositories: RepositoryRegistry | None = None) -> "HarnessService":
        data_mode = resolve_data_mode()
        security_provider, market_data_provider = build_providers_from_env()
        return cls(
            repositories,
            security_provider=security_provider,
            market_data_provider=market_data_provider,
            data_mode=data_mode,
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
        quotes: dict[str, float] | None = None,
        quote_times: dict[str, datetime] | None = None,
        require_explicit_quotes: bool = False,
        require_whole_shares: bool = False,
        rebalance_band: float | None = None,
    ) -> PortfolioPlan:
        policy = self.repositories.policies.require(policy_id)
        selected_signals = signals if signals is not None else self.repositories.signals.list()
        portfolio_snapshot = snapshot or fixture_portfolio_snapshot()
        selected_quotes = quotes
        if selected_quotes is None:
            selected_quotes = {
                bar["symbol"]: float(bar["close"])
                for bar in self.market_data_provider.get_bars()
            }
        planner_options = {}
        if rebalance_band is not None:
            planner_options["rebalance_band"] = rebalance_band
        plan = build_portfolio_plan(
            policy=policy,
            signals=selected_signals,
            snapshot=portfolio_snapshot,
            quotes=selected_quotes,
            quote_times=quote_times,
            require_explicit_quotes=require_explicit_quotes,
            require_whole_shares=require_whole_shares,
            **planner_options,
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
            if (
                order.status == OrderStatus.cancelled
                and order.blocked_reason == "dry_run_no_submission"
            ):
                # A dry run is evidence that no broker side effect was attempted;
                # it must not poison the later executable proposal for that signal.
                continue
            if submitted_only and order.status not in submitted_states:
                continue
            keys.add(order.idempotency_key)
        return keys

    def _hydrate_operator_safety_state(
        self,
        *,
        policy_id: str,
    ) -> OperatorSafetyState | None:
        provider = self.operator_safety_state_provider
        if provider is None or not hasattr(
            provider,
            "load_operator_safety_state",
        ):
            return None
        durable = provider.load_operator_safety_state(policy_id)
        if durable is not None:
            self.autopilot_paused = durable.autopilot_paused
            self.broker_healthy = durable.broker_healthy
            self.last_blocked_reason = durable.last_blocked_reason
        return durable

    def _guardrail_state(
        self,
        *,
        policy: UserPolicy,
        strategy_id: str,
        exclude_order_plan_id: str | None = None,
        exclude_order_plan_ids: set[str] | None = None,
        now: datetime | None = None,
    ) -> GuardrailState:
        self._hydrate_operator_safety_state(policy_id=policy.policy_id)
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
        pre_submission_states = {
            OrderStatus.proposed,
            OrderStatus.user_approved,
        }
        post_submission_unfilled_states = {
            OrderStatus.submitted,
            OrderStatus.accepted,
            OrderStatus.partially_filled,
        }
        current_time = now or utc_now()
        current_trading_date = current_time.astimezone(
            ZoneInfo("Asia/Seoul")
        ).date()
        repository_orders = self.repositories.order_plans.list()
        orders_by_id = {order.order_plan_id: order for order in repository_orders}
        filled_quantities_by_order: dict[str, float] = {}
        for fill in self.repositories.fills.list():
            filled_quantities_by_order[fill.order_plan_id] = (
                filled_quantities_by_order.get(fill.order_plan_id, 0.0)
                + fill.quantity
            )

        def active_unfilled(order: OrderPlan) -> bool:
            if order.status not in unfilled_states:
                return False
            if order.status not in pre_submission_states:
                return True
            for deadline in (order.expires_at, order.risk_check_expires_at):
                if deadline is not None:
                    if deadline.tzinfo is None or deadline.utcoffset() is None:
                        return False
                    if deadline <= current_time:
                        return False
            return True

        def remaining_sell_quantity(order: OrderPlan) -> float:
            if order.status not in post_submission_unfilled_states:
                return order.intent.quantity
            return max(
                0.0,
                order.intent.quantity
                - filled_quantities_by_order.get(order.order_plan_id, 0.0),
            )

        submitted_orders = [
            order
            for order in repository_orders
            if order.policy_id == policy.policy_id
            and order.status in submitted_states
            and order.order_plan_id not in excluded
            and order.created_at.astimezone(ZoneInfo("Asia/Seoul")).date()
            == current_trading_date
        ]
        unfilled_order_keys = [
            f"{order.explanation.strategy_id if order.explanation else strategy_id}:{order.intent.symbol}:{order.intent.side}"
            for order in repository_orders
            if order.policy_id == policy.policy_id
            and active_unfilled(order)
            and order.order_plan_id not in excluded
        ]
        reserved_sell_quantities: dict[str, float] = {}
        repository_reserved_quantities: dict[str, float] = {}
        for order in repository_orders:
            if (
                order.policy_id != policy.policy_id
                or not active_unfilled(order)
                or order.order_plan_id in excluded
                or order.intent.side != "sell"
            ):
                continue
            remaining_quantity = remaining_sell_quantity(order)
            if remaining_quantity <= 0.000001:
                continue
            symbol = order.intent.symbol.strip().upper()
            reserved_sell_quantities[symbol] = (
                reserved_sell_quantities.get(symbol, 0.0) + remaining_quantity
            )
            repository_reserved_quantities[order.order_plan_id] = (
                remaining_quantity
            )
        durable_daily_count = 0
        durable_daily_turnover = 0.0
        durable_submitted_keys: list[str] = []
        unresolved_paper_buy_order = False
        accounted_reserved_quantities_by_order = dict(
            repository_reserved_quantities
        )
        counted_order_plan_ids = {
            order.order_plan_id for order in submitted_orders
        }
        provider = self.pending_liquidation_provider
        if provider is not None and hasattr(provider, "list_pending_liquidations"):
            checkpoints: list[PendingLiquidationCheckpoint] = (
                provider.list_pending_liquidations(include_reconciled=True)
            )
            for checkpoint in checkpoints:
                if (
                    checkpoint.policy_id != policy.policy_id
                    or checkpoint.order_plan_id in excluded
                ):
                    continue
                unresolved_submission = checkpoint.status in {
                    "prepared",
                    "submitted",
                    "accepted",
                    "partially_filled",
                    "outcome_unknown",
                    "filled",
                }
                durable_reservation = (
                    checkpoint.quantity_requested
                    if unresolved_submission
                    else (
                        checkpoint.cumulative_filled_quantity
                        if checkpoint.status
                        in {"cancelled", "rejected", "failed"}
                        else 0.0
                    )
                )
                repository_order = orders_by_id.get(checkpoint.order_plan_id)
                if (
                    checkpoint.status == "prepared"
                    and repository_order is not None
                    and not active_unfilled(repository_order)
                ):
                    durable_reservation = 0.0
                additional_reservation = max(
                    0.0,
                    durable_reservation
                    - accounted_reserved_quantities_by_order.get(
                        checkpoint.order_plan_id,
                        0.0,
                    ),
                )
                if additional_reservation > 0.000001:
                    reserved_sell_quantities[checkpoint.symbol] = (
                        reserved_sell_quantities.get(checkpoint.symbol, 0.0)
                        + additional_reservation
                    )
                    unfilled_order_keys.append(
                        f"{checkpoint.strategy_id}:{checkpoint.symbol}:sell"
                    )
                accounted_reserved_quantities_by_order[
                    checkpoint.order_plan_id
                ] = max(
                    accounted_reserved_quantities_by_order.get(
                        checkpoint.order_plan_id,
                        0.0,
                    ),
                    durable_reservation,
                )
                if (
                    checkpoint.order_plan_id not in counted_order_plan_ids
                    and checkpoint.broker_submission_attempted
                    and checkpoint.created_at.astimezone(
                        ZoneInfo("Asia/Seoul")
                    ).date()
                    == current_trading_date
                ):
                    durable_daily_count += 1
                    durable_daily_turnover += (
                        checkpoint.quantity_requested * checkpoint.limit_price
                    )
                    durable_submitted_keys.append(checkpoint.idempotency_key)
                    counted_order_plan_ids.add(checkpoint.order_plan_id)

        dispatch_provider = self.paper_dispatch_provider
        if dispatch_provider is not None and hasattr(
            dispatch_provider,
            "list_paper_order_dispatches",
        ):
            if hasattr(dispatch_provider, "list_paper_risk_reservations"):
                for reservation in dispatch_provider.list_paper_risk_reservations(
                    held_only=True
                ):
                    if (
                        reservation.order_plan_id in excluded
                        or reservation.kind != "sell_quantity"
                    ):
                        continue
                    reserved_quantity = float(
                        reservation.reserved_sell_quantity or 0
                    )
                    additional_reservation = max(
                        0.0,
                        reserved_quantity
                        - accounted_reserved_quantities_by_order.get(
                            reservation.order_plan_id,
                            0.0,
                        ),
                    )
                    if additional_reservation > 0.000001:
                        reserved_sell_quantities[reservation.symbol] = (
                            reserved_sell_quantities.get(reservation.symbol, 0.0)
                            + additional_reservation
                        )
                    accounted_reserved_quantities_by_order[
                        reservation.order_plan_id
                    ] = max(
                        accounted_reserved_quantities_by_order.get(
                            reservation.order_plan_id,
                            0.0,
                        ),
                        reserved_quantity,
                    )
            active_dispatch_states = {
                "prepared",
                "dispatch_claimed",
                "outcome_unknown",
                "accepted",
                "partially_filled",
            }
            for dispatch in dispatch_provider.list_paper_order_dispatches():
                if (
                    dispatch.policy_id != policy.policy_id
                    or dispatch.order_plan_id in excluded
                ):
                    continue
                if dispatch.status in active_dispatch_states:
                    unfilled_order_keys.append(
                        f"{dispatch.strategy_id}:{dispatch.symbol}:{dispatch.side}"
                    )
                    if dispatch.side == "buy":
                        unresolved_paper_buy_order = True
                    if dispatch.side == "sell":
                        outstanding = max(
                            0.0,
                            dispatch.quantity
                            - dispatch.cumulative_filled_quantity,
                        )
                        additional_reservation = max(
                            0.0,
                            outstanding
                            - accounted_reserved_quantities_by_order.get(
                                dispatch.order_plan_id,
                                0.0,
                            ),
                        )
                        if additional_reservation > 0.000001:
                            reserved_sell_quantities[dispatch.symbol] = (
                                reserved_sell_quantities.get(dispatch.symbol, 0.0)
                                + additional_reservation
                            )
                        accounted_reserved_quantities_by_order[
                            dispatch.order_plan_id
                        ] = max(
                            accounted_reserved_quantities_by_order.get(
                                dispatch.order_plan_id,
                                0.0,
                            ),
                            outstanding,
                        )
                attempted_at = dispatch.dispatch_claimed_at
                if (
                    dispatch.attempt_count == 1
                    and attempted_at is not None
                    and dispatch.order_plan_id not in counted_order_plan_ids
                    and attempted_at.astimezone(ZoneInfo("Asia/Seoul")).date()
                    == current_trading_date
                ):
                    durable_daily_count += 1
                    durable_daily_turnover += (
                        dispatch.quantity * dispatch.limit_price
                    )
                    durable_submitted_keys.append(dispatch.idempotency_key)
                    counted_order_plan_ids.add(dispatch.order_plan_id)
        return GuardrailState(
            daily_order_count=len(submitted_orders) + durable_daily_count,
            daily_turnover_used=round(
                sum(order.intent.notional for order in submitted_orders)
                + durable_daily_turnover,
                2,
            ),
            kill_switch_engaged=policy.kill_switch_engaged,
            broker_healthy=self.broker_healthy,
            autopilot_paused=self.autopilot_paused,
            last_blocked_reason=self.last_blocked_reason,
            unresolved_paper_buy_order=unresolved_paper_buy_order,
            unfilled_order_keys=sorted(set(unfilled_order_keys)),
            submitted_idempotency_keys=sorted(set([
                *[order.idempotency_key for order in submitted_orders],
                *durable_submitted_keys,
            ])),
            reserved_sell_quantities=reserved_sell_quantities,
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
                account_equity_at_proposal=portfolio_snapshot.equity,
                portfolio_snapshot_id=portfolio_snapshot.snapshot_id,
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

    def apply_risk_check(
        self,
        order_plan_id: str,
        *,
        snapshot: PortfolioSnapshot | None = None,
        position_binding: ManagedPositionBinding | None = None,
        market_quote: Quote | None = None,
        now: datetime | None = None,
    ):
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
            position_binding=position_binding,
            market_quote=market_quote,
            now=now,
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

    @staticmethod
    def _performance_fill_time_utc(fill: Fill) -> datetime:
        if fill.filled_at.tzinfo is None:
            return fill.filled_at.replace(tzinfo=timezone.utc)
        return fill.filled_at.astimezone(timezone.utc)

    def _strategy_fill_evidence(
        self,
        strategy_id: str,
        strategy_version: str,
    ) -> list[tuple[Fill, str, ProposalExplanation]]:
        plans_by_id = {
            plan.order_plan_id: plan
            for plan in self.repositories.order_plans.list()
        }
        evidence: list[tuple[Fill, str, ProposalExplanation]] = []
        for fill in self.repositories.fills.list():
            plan = plans_by_id.get(fill.order_plan_id)
            if plan is None or plan.explanation is None:
                continue
            explanation = plan.explanation
            if (
                explanation.strategy_id == strategy_id
                and explanation.strategy_version == strategy_version
            ):
                evidence.append((fill, plan.intent.side, explanation))
        evidence.sort(key=lambda item: self._performance_fill_time_utc(item[0]))
        return evidence

    @staticmethod
    def _strategy_fill_fingerprint(
        evidence: list[tuple[Fill, str, ProposalExplanation]],
    ) -> str:
        payload = [
            {
                "fill": fill.model_dump(mode="json"),
                "side": side,
                "strategy_id": explanation.strategy_id,
                "strategy_version": explanation.strategy_version,
                "account_equity_at_proposal": explanation.account_equity_at_proposal,
                "portfolio_snapshot_id": explanation.portfolio_snapshot_id,
            }
            for fill, side, explanation in evidence
        ]
        payload.sort(key=lambda item: str(item["fill"]["fill_id"]))
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _performance_calendar_fingerprint(
        self,
        start_session: date,
        end_session: date,
    ) -> str:
        payload = {
            "calendar_name": self.performance_calendar.name,
            "start_session": start_session.isoformat(),
            "end_session": end_session.isoformat(),
            "trading_sessions": [
                session.isoformat()
                for session in self.performance_calendar.trading_sessions(
                    start_session,
                    end_session,
                )
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _performance_close_snapshot(
        self,
        *,
        symbols: set[str],
        start_session: date,
        end_session: date,
    ) -> tuple[dict[tuple[str, date], float], bool]:
        try:
            price_history = list(self.market_data_provider.get_price_history())
        except Exception:
            return {}, True

        closes: dict[tuple[str, date], float] = {}
        for row in price_history:
            try:
                symbol = str(
                    row.get("symbol") or row.get("ticker") or ""
                ).strip().upper()
                if symbol not in symbols:
                    continue
                session = date.fromisoformat(str(row.get("date")))
                close = float(row["close"])
            except (
                AttributeError,
                KeyError,
                OverflowError,
                TypeError,
                ValueError,
            ):
                continue
            if (
                start_session <= session <= end_session
                and self.performance_calendar.is_trading_session(session)
                and close > 0
                and isfinite(close)
            ):
                closes[(symbol, session)] = close
        return closes, False

    @staticmethod
    def _performance_close_fingerprint(
        closes: dict[tuple[str, date], float],
    ) -> str:
        payload = [
            {
                "symbol": symbol,
                "session": session.isoformat(),
                "close": close,
            }
            for (symbol, session), close in sorted(closes.items())
        ]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def compute_strategy_performance(
        self, strategy_id: str, strategy_version: str
    ) -> StrategyPerformanceRecord | None:
        """Build fail-closed strategy PnL evidence from fills and daily closes."""
        evaluated_at = self.performance_clock()
        if evaluated_at.tzinfo is None:
            evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
        else:
            evaluated_at = evaluated_at.astimezone(timezone.utc)

        all_evidence = self._strategy_fill_evidence(strategy_id, strategy_version)
        evidence = [
            item
            for item in all_evidence
            if self._performance_fill_time_utc(item[0])
            <= evaluated_at + timedelta(seconds=5)
        ]
        future_fill_detected = len(evidence) != len(all_evidence)
        fill_fingerprint = self._strategy_fill_fingerprint(evidence)
        if not evidence:
            if future_fill_detected:
                return StrategyPerformanceRecord(
                    strategy_id=strategy_id,
                    strategy_version=strategy_version,
                    as_of=evaluated_at,
                    realized_max_drawdown=0.0,
                    realized_total_return=0.0,
                    observation_days=1,
                    source="auto_feed",
                    cost_basis=KIS_RETAIL_COST_BASIS,
                    valuation="last_fill_price",
                    normalization_basis="reconciliation_required",
                    valuation_status="reconciliation_required",
                    data_mode=self.data_mode,
                    included_fill_count=0,
                    included_fill_fingerprint=fill_fingerprint,
                    calendar_name=self.performance_calendar.name,
                )
            return None

        latest_completed_session = _latest_completed_krx_session(
            evaluated_at,
            self.performance_calendar,
        )

        def fill_session_date(fill: Fill) -> date:
            return self._performance_fill_time_utc(fill).astimezone(_KST).date()

        def fill_local_time(fill: Fill) -> time:
            return self._performance_fill_time_utc(fill).astimezone(_KST).time()

        def fill_symbol(fill: Fill) -> str:
            return fill.symbol.strip().upper()

        fee_rate = KIS_BANKIS_ONLINE_FEE_BPS / 10_000.0
        sell_tax_rate = KRX_SELL_TAX_BPS_FROM_2026 / 10_000.0
        first_day = fill_session_date(evidence[0][0])
        symbols = {fill_symbol(fill) for fill, _, _ in evidence}
        closes, provider_failed = self._performance_close_snapshot(
            symbols=symbols,
            start_session=first_day,
            end_session=latest_completed_session,
        )

        fills_by_day: dict[
            date,
            list[tuple[Fill, str, ProposalExplanation]],
        ] = {}
        for item in evidence:
            fills_by_day.setdefault(fill_session_date(item[0]), []).append(item)

        completed_session_list = self.performance_calendar.trading_sessions(
            first_day,
            latest_completed_session,
        )
        completed_sessions = set(completed_session_list)
        evaluation_days = set(fills_by_day) | completed_sessions
        cash = 0.0
        positions: dict[str, float] = {}
        position_cost_basis: dict[str, float] = {}
        fallback_capital_base = 0.0
        fallback_capital_session: date | None = None
        normalization_equity: float | None = None
        normalization_snapshot_id: str | None = None
        normalization_evidence_complete = True
        inventory_consistent = not future_fill_detected
        last_price: dict[str, float] = {}
        curve: list[float] = []
        observed_days: set[date] = set()
        any_daily_close_applied = False
        missing_close_sessions: set[date] = set()
        awaiting_close_symbols: set[str] = set()
        market_data_as_of_session: date | None = None

        def mark_equity() -> float:
            return cash + sum(
                quantity * last_price[symbol]
                for symbol, quantity in positions.items()
                if quantity > 0
            )

        def apply_fill(
            fill: Fill,
            side: str,
            explanation: ProposalExplanation,
            day: date,
        ) -> None:
            nonlocal cash
            nonlocal fallback_capital_base
            nonlocal fallback_capital_session
            nonlocal inventory_consistent
            nonlocal normalization_evidence_complete
            nonlocal normalization_equity
            nonlocal normalization_snapshot_id

            symbol = fill_symbol(fill)
            last_price[symbol] = fill.price
            if side == "buy":
                account_equity = explanation.account_equity_at_proposal
                snapshot_id = (explanation.portfolio_snapshot_id or "").strip()
                if (
                    account_equity is None
                    or not isfinite(account_equity)
                    or not snapshot_id
                ):
                    normalization_evidence_complete = False
                elif normalization_equity is None:
                    normalization_equity = account_equity
                    normalization_snapshot_id = snapshot_id
                cash -= fill.notional * (1.0 + fee_rate)
                positions[symbol] = positions.get(symbol, 0.0) + fill.quantity
                position_cost_basis[symbol] = (
                    position_cost_basis.get(symbol, 0.0) + fill.notional
                )
            else:
                available_quantity = max(0.0, positions.get(symbol, 0.0))
                matched_quantity = min(available_quantity, fill.quantity)
                if fill.quantity > available_quantity + 1e-9:
                    inventory_consistent = False
                matched_ratio = matched_quantity / fill.quantity
                matched_notional = fill.notional * matched_ratio
                cash += matched_notional * (1.0 - fee_rate - sell_tax_rate)
                remaining_quantity = available_quantity - matched_quantity
                if available_quantity > 0:
                    remaining_fraction = remaining_quantity / available_quantity
                    remaining_basis = (
                        position_cost_basis.get(symbol, 0.0)
                        * remaining_fraction
                    )
                else:
                    remaining_basis = 0.0
                positions[symbol] = remaining_quantity
                if remaining_quantity > 0:
                    position_cost_basis[symbol] = remaining_basis
                else:
                    position_cost_basis.pop(symbol, None)
                    awaiting_close_symbols.discard(symbol)

            current_cost_basis = sum(position_cost_basis.values())
            if fallback_capital_session is None and current_cost_basis > 0:
                fallback_capital_session = day
            if day == fallback_capital_session:
                fallback_capital_base = max(
                    fallback_capital_base,
                    current_cost_basis,
                )
            curve.append(mark_equity())
            observed_days.add(day)

        for day in sorted(evaluation_days):
            day_fills = fills_by_day.get(day, [])
            if day in completed_sessions:
                pre_close = [
                    item
                    for item in day_fills
                    if fill_local_time(item[0]) < _KRX_REGULAR_CLOSE
                ]
                post_close = [
                    item
                    for item in day_fills
                    if fill_local_time(item[0]) >= _KRX_REGULAR_CLOSE
                ]
            else:
                pre_close = []
                post_close = day_fills

            for fill, side, explanation in pre_close:
                apply_fill(fill, side, explanation, day)

            if day in completed_sessions:
                open_symbols = [
                    symbol
                    for symbol, quantity in positions.items()
                    if quantity > 0
                ]
                if open_symbols:
                    day_complete = True
                    for symbol in open_symbols:
                        close = closes.get((symbol, day))
                        if close is None:
                            day_complete = False
                            continue
                        last_price[symbol] = close
                        awaiting_close_symbols.discard(symbol)
                        any_daily_close_applied = True
                    if day_complete:
                        market_data_as_of_session = day
                    else:
                        missing_close_sessions.add(day)
                    curve.append(mark_equity())
                    observed_days.add(day)

            for fill, side, explanation in post_close:
                symbol = fill_symbol(fill)
                apply_fill(fill, side, explanation, day)
                if positions.get(symbol, 0.0) > 0:
                    awaiting_close_symbols.add(symbol)

        if not curve:
            return None

        if not inventory_consistent:
            normalization_basis = "reconciliation_required"
        elif normalization_equity is None or not normalization_evidence_complete:
            normalization_basis = "degraded_unbound_equity"
        else:
            normalization_basis = "first_order_account_equity"

        capital_base = normalization_equity or fallback_capital_base or 1.0
        peak = capital_base
        max_drawdown = 0.0
        for value in (capital_base + pnl for pnl in curve):
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - value) / peak)

        has_open_positions = any(quantity > 0 for quantity in positions.values())
        if not inventory_consistent:
            valuation_status = "reconciliation_required"
        elif provider_failed:
            valuation_status = "provider_error"
        elif missing_close_sessions:
            if not any_daily_close_applied:
                valuation_status = "fill_only"
            elif latest_completed_session in missing_close_sessions:
                valuation_status = "stale"
            else:
                valuation_status = "partial"
        elif awaiting_close_symbols:
            valuation_status = (
                "stale" if any_daily_close_applied else "fill_only"
            )
        elif not any_daily_close_applied:
            valuation_status = "fill_only"
        elif (
            has_open_positions
            and market_data_as_of_session != latest_completed_session
        ):
            valuation_status = "stale"
        else:
            valuation_status = "complete"

        return StrategyPerformanceRecord(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            as_of=evaluated_at,
            realized_max_drawdown=max_drawdown,
            realized_total_return=curve[-1] / capital_base,
            observation_days=len(observed_days),
            source="auto_feed",
            cost_basis=KIS_RETAIL_COST_BASIS,
            valuation=(
                "daily_close"
                if valuation_status == "complete"
                else "last_fill_price"
            ),
            normalization_basis=normalization_basis,
            normalization_equity=normalization_equity,
            normalization_snapshot_id=normalization_snapshot_id,
            valuation_status=valuation_status,
            market_data_as_of_session=market_data_as_of_session,
            market_data_fingerprint=self._performance_close_fingerprint(closes),
            market_data_close_count=len(closes),
            data_mode=self.data_mode,
            has_open_positions=has_open_positions,
            included_fill_count=len(evidence),
            included_fill_fingerprint=fill_fingerprint,
            calendar_name=self.performance_calendar.name,
            valuation_start_session=first_day,
            calendar_as_of_session=latest_completed_session,
            calendar_fingerprint=self._performance_calendar_fingerprint(
                first_day,
                latest_completed_session,
            ),
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

    def _latest_auto_strategy_performance(
        self, strategy_id: str, strategy_version: str
    ) -> StrategyPerformanceRecord | None:
        records = [
            record
            for record in self.repositories.strategy_performance.list()
            if record.strategy_id == strategy_id
            and record.strategy_version == strategy_version
            and record.source == "auto_feed"
        ]
        return max(records, key=lambda record: record.as_of) if records else None

    def _auto_performance_readiness_reason(
        self, record: StrategyPerformanceRecord
    ) -> str | None:
        if record.valuation_status == "reconciliation_required":
            return "strategy_performance_reconciliation_required"
        if record.valuation_status != "complete":
            return "strategy_performance_valuation_degraded"
        if (
            record.normalization_basis != "first_order_account_equity"
            or record.normalization_equity is None
            or not (record.normalization_snapshot_id or "").strip()
        ):
            return "strategy_performance_normalization_degraded"
        if record.data_mode != self.data_mode:
            return "strategy_performance_data_mode_mismatch"
        if record.calendar_name != self.performance_calendar.name:
            return "strategy_performance_calendar_mismatch"
        if (
            record.valuation_start_session is None
            or record.calendar_as_of_session is None
            or record.calendar_fingerprint
            != self._performance_calendar_fingerprint(
                record.valuation_start_session,
                record.calendar_as_of_session,
            )
        ):
            return "strategy_performance_calendar_mismatch"
        observed_at = self.performance_clock()
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if (
            record.has_open_positions
            and record.market_data_as_of_session
            != _latest_completed_krx_session(
                observed_at,
                self.performance_calendar,
            )
        ):
            return "strategy_performance_stale"
        return None

    def _auto_performance_evidence_reason(
        self,
        record: StrategyPerformanceRecord,
        *,
        strategy_id: str,
        strategy_version: str,
    ) -> str | None:
        readiness_reason = self._auto_performance_readiness_reason(record)
        if readiness_reason is not None:
            return readiness_reason
        evidence = self._strategy_fill_evidence(strategy_id, strategy_version)
        if (
            record.included_fill_count != len(evidence)
            or record.included_fill_fingerprint
            != self._strategy_fill_fingerprint(evidence)
        ):
            return "strategy_performance_fill_watermark_stale"
        if (
            record.valuation_start_session is None
            or record.calendar_as_of_session is None
        ):
            return "strategy_performance_market_data_mismatch"
        symbols = {
            fill.symbol.strip().upper()
            for fill, _, _ in evidence
        }
        closes, provider_failed = self._performance_close_snapshot(
            symbols=symbols,
            start_session=record.valuation_start_session,
            end_session=record.calendar_as_of_session,
        )
        if provider_failed:
            return "strategy_performance_market_data_unavailable"
        if (
            record.market_data_close_count != len(closes)
            or record.market_data_fingerprint
            != self._performance_close_fingerprint(closes)
        ):
            return "strategy_performance_market_data_mismatch"
        return None

    def _drift_trigger_fired(
        self,
        ticket: StrategyApprovalTicket,
        *,
        validated_record: StrategyPerformanceRecord | None | object = (
            _PERFORMANCE_RECORD_UNSET
        ),
    ) -> str | None:
        if validated_record is _PERFORMANCE_RECORD_UNSET:
            auto_record = self._latest_auto_strategy_performance(
                ticket.strategy_id, ticket.strategy_version
            )
            if auto_record is not None:
                if (
                    self._auto_performance_evidence_reason(
                        auto_record,
                        strategy_id=ticket.strategy_id,
                        strategy_version=ticket.strategy_version,
                    )
                    is not None
                ):
                    return None
                record = auto_record
            else:
                record = self._latest_strategy_performance(
                    ticket.strategy_id,
                    ticket.strategy_version,
                )
        else:
            record = validated_record
        if record is None:
            return None
        if not isinstance(record, StrategyPerformanceRecord):
            raise TypeError("validated performance record has an invalid type")
        evidence = self.repositories.backtest_results.get(ticket.backtest_report_id)
        if evidence is None:
            return "backtest_evidence_missing"
        # Zero-MDD evidence means the backtest never drew down, so ANY realized
        # drawdown exceeds the tolerated multiple — fail closed by design.
        # Since the auto feed prices in transaction costs, the first buy fill
        # already realizes a fee-sized drawdown: a zero-MDD-evidence ticket
        # therefore expires on its first fill. Intended — such evidence gives
        # the monitor no tolerable drawdown budget to operate within.
        limit = evidence.metrics.max_drawdown * self.DRIFT_MDD_MULTIPLIER
        if record.realized_max_drawdown > limit:
            return "mdd_exceeds_backtest_1_5x"
        return None

    def _drift_expire_ticket_if_needed(
        self,
        ticket: StrategyApprovalTicket,
        *,
        validated_record: StrategyPerformanceRecord | None | object = (
            _PERFORMANCE_RECORD_UNSET
        ),
    ) -> StrategyApprovalTicket:
        if ticket.status != StrategyApprovalTicketStatus.approved:
            return ticket
        fired = self._drift_trigger_fired(
            ticket,
            validated_record=validated_record,
        )
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

    def _expire_strategy_ticket_if_needed(
        self,
        ticket: StrategyApprovalTicket,
        *,
        validated_record: StrategyPerformanceRecord | None | object = (
            _PERFORMANCE_RECORD_UNSET
        ),
    ) -> StrategyApprovalTicket:
        ticket = self._drift_expire_ticket_if_needed(
            ticket,
            validated_record=validated_record,
        )
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
            if ticket.status != StrategyApprovalTicketStatus.approved:
                self._expire_strategy_ticket_if_needed(ticket)
                continue
            if ticket.valid_until <= utc_now():
                self._expire_strategy_ticket_if_needed(
                    ticket,
                    validated_record=None,
                )
                continue
            auto_record = self._latest_auto_strategy_performance(
                ticket.strategy_id, ticket.strategy_version
            )
            fill_evidence = self._strategy_fill_evidence(
                ticket.strategy_id,
                ticket.strategy_version,
            )
            if auto_record is None:
                if fill_evidence:
                    return False, "strategy_performance_missing"
                validated_record = self._latest_strategy_performance(
                    ticket.strategy_id,
                    ticket.strategy_version,
                )
            else:
                readiness_reason = self._auto_performance_evidence_reason(
                    auto_record,
                    strategy_id=ticket.strategy_id,
                    strategy_version=ticket.strategy_version,
                )
                if readiness_reason is not None:
                    return False, readiness_reason
                validated_record = auto_record
            refreshed = self._expire_strategy_ticket_if_needed(
                ticket,
                validated_record=validated_record,
            )
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
            if self.external_paper_broker is not None:
                return self.external_paper_broker
            return PaperBroker()
        if policy.broker == BrokerMode.mock:
            return MockBroker()
        raise RuntimeError("live broker mode is disabled in the pre-harness")

    def _orders_for_submit_batch(
        self,
        order_plan: OrderPlan,
        *,
        now: datetime | None = None,
    ) -> list[OrderPlan]:
        if order_plan.status != OrderStatus.user_approved:
            return []
        if order_plan.purpose in {"protective_exit", "strategy_retirement"}:
            return [order_plan]
        batch_statuses = {
            OrderStatus.proposed,
            OrderStatus.user_approved,
        }
        batch: list[OrderPlan] = []
        current_seen = False
        current_time = now or utc_now()
        for existing in self.repositories.order_plans.list():
            if (
                existing.policy_id != order_plan.policy_id
                or existing.status not in batch_statuses
                or existing.purpose != "rebalance"
                or (
                    existing.expires_at is not None
                    and existing.expires_at <= current_time
                )
                or (
                    existing.risk_check_expires_at is not None
                    and existing.risk_check_expires_at <= current_time
                )
            ):
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

    def submit_order_plan(
        self,
        order_plan_id: str,
        *,
        snapshot: PortfolioSnapshot | None = None,
        position_binding: ManagedPositionBinding | None = None,
        market_quote: Quote | None = None,
        paper_run_id: str | None = None,
        entry_atr14: float | None = None,
        now: datetime | None = None,
        before_broker_submit: Callable[[OrderPlan], None] | None = None,
    ) -> tuple[OrderPlan, BrokerOrder, list[Fill]]:
        order_plan = self.repositories.order_plans.require(order_plan_id)
        policy = self.repositories.policies.require(order_plan.policy_id)
        if self.external_paper_enabled and snapshot is None:
            raise RiskCheckRequired(
                "external paper submission requires an explicit reconciled snapshot"
            )
        if self.external_paper_enabled and market_quote is None:
            raise RiskCheckRequired(
                "external paper submission requires an explicit L2 quote"
            )
        if self.external_paper_enabled and (
            paper_run_id is None or not paper_run_id.strip()
        ):
            raise RiskCheckRequired(
                "external paper submission requires a durable operator run ID"
            )
        if order_plan.purpose in {"protective_exit", "strategy_retirement"} and snapshot is None:
            raise RiskCheckRequired(
                "risk-reducing orders require an explicit reconciled portfolio snapshot"
            )
        portfolio_snapshot = snapshot or fixture_portfolio_snapshot()
        submission_time = now or utc_now()
        snapshot_max_age_seconds = (
            policy.stale_quote_max_age_seconds
            if order_plan.purpose in {"protective_exit", "strategy_retirement"}
            else 900
        )

        if order_plan.risk_check_id is None or order_plan.status == OrderStatus.draft:
            raise RiskCheckRequired("risk_checked is required before submission")
        if order_plan.risk_check_expires_at is not None and order_plan.risk_check_expires_at <= submission_time:
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
        if order_plan.status != OrderStatus.user_approved:
            raise ApprovalRequired("an executable order must be in user_approved state")

        strategy_id = order_plan.explanation.strategy_id if order_plan.explanation else "unknown_strategy"
        fresh_risk = run_risk_check(
            policy=policy,
            order_plan=order_plan,
            snapshot=portfolio_snapshot,
            seen_idempotency_keys=self._seen_idempotency_keys(exclude_order_plan_id=order_plan.order_plan_id, submitted_only=True),
            guardrail_state=self._guardrail_state(
                policy=policy,
                strategy_id=strategy_id,
                exclude_order_plan_id=order_plan.order_plan_id,
                now=submission_time,
            ),
            quote_max_age_seconds=policy.stale_quote_max_age_seconds,
            strategy_id=strategy_id,
            position_binding=position_binding,
            market_quote=market_quote,
            now=submission_time,
            snapshot_max_age_seconds=snapshot_max_age_seconds,
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
            order_plan.blocked_reason = "fresh_risk_check_failed"
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.failed,
                audit=self.audit,
                user_id=policy.user_id,
                source="execution_service",
            )
            self.repositories.order_plans.update(order_plan)
            raise RiskCheckRequired(f"fresh risk check failed: {fresh_risk.failed_checks}")
        order_plan.risk_check_id = fresh_risk.risk_check_id
        order_plan.risk_check_expires_at = fresh_risk.expires_at

        batch_orders = self._orders_for_submit_batch(
            order_plan,
            now=submission_time,
        )
        batch_order_ids = {order.order_plan_id for order in batch_orders}
        batch_decision = run_batch_risk_gate(
            policy=policy,
            portfolio_plan=self._portfolio_plan_for_order_batch(policy=policy, order_plans=batch_orders),
            snapshot=portfolio_snapshot,
            quotes=self._quotes_for_intents([order.intent for order in batch_orders]),
            order_plans=batch_orders,
            config=BatchRiskConfig(
                quote_max_age_seconds=policy.stale_quote_max_age_seconds,
                snapshot_max_age_seconds=snapshot_max_age_seconds,
            ),
            guardrail_state=self._guardrail_state(
                policy=policy,
                strategy_id=strategy_id,
                exclude_order_plan_ids=batch_order_ids,
                now=submission_time,
            ),
            seen_idempotency_keys=self._seen_idempotency_keys(
                exclude_order_plan_ids=batch_order_ids,
                submitted_only=True,
            ),
            position_bindings=(
                {order_plan.order_plan_id: position_binding}
                if position_binding is not None
                else None
            ),
            market_quotes=(
                {order_plan.order_plan_id: market_quote}
                if market_quote is not None
                else None
            ),
            now=submission_time,
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
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.failed,
                audit=self.audit,
                user_id=policy.user_id,
                source="execution_service",
            )
            self.repositories.order_plans.update(order_plan)
            raise RiskCheckRequired(f"batch risk check failed: {batch_decision.failed_checks}")

        def current_submission_safety_failures() -> list[str]:
            failures: list[str] = []
            current_policy = self.repositories.policies.require(
                order_plan.policy_id
            )
            self._hydrate_operator_safety_state(policy_id=order_plan.policy_id)
            if (
                current_policy != policy
                or order_plan.policy_version != current_policy.version
            ):
                failures.append("policy_version_match")
            if current_policy.kill_switch_engaged:
                failures.append("kill_switch_not_engaged")
            if live_trading_flag_enabled():
                failures.append("live_trading_disabled")
            if operator_kill_switch_engaged():
                failures.append("operator_kill_switch_not_engaged")
            if self.autopilot_paused:
                failures.append("operator_not_paused")
            if not self.broker_healthy:
                failures.append("broker_health")
            return failures

        def fail_final_submission(failures: list[str]) -> None:
            before = order_plan.model_copy(deep=True)
            order_plan.blocked_reason = "final_submission_safety_gate_failed"
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=order_plan.order_plan_id,
                action="risk_check_failed",
                before_state=before,
                after_state={"failed_checks": failures},
                source="execution_service",
            )
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.failed,
                audit=self.audit,
                user_id=policy.user_id,
                source="execution_service",
            )
            self.repositories.order_plans.update(order_plan)
            raise RiskCheckRequired(
                f"final submission safety gate failed: {failures}"
            )

        final_safety_failures = current_submission_safety_failures()
        if final_safety_failures:
            fail_final_submission(final_safety_failures)

        broker = self._broker_for_policy(policy)
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.submitted,
            audit=self.audit,
            user_id=policy.user_id,
            source="execution_service",
        )
        self.repositories.order_plans.update(order_plan)
        if before_broker_submit is not None:
            try:
                before_broker_submit(order_plan.model_copy(deep=True))
            except Exception as exc:
                before = order_plan.model_copy(deep=True)
                order_plan.blocked_reason = "prebroker_submission_guard_failed"
                transition_order_plan(
                    order_plan=order_plan,
                    new_status=OrderStatus.failed,
                    audit=self.audit,
                    user_id=policy.user_id,
                    source="execution_service",
                )
                self.repositories.order_plans.update(order_plan)
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="order_plan",
                    entity_id=order_plan.order_plan_id,
                    action="prebroker_submission_guard_failed",
                    before_state=before,
                    after_state={"error_type": type(exc).__name__},
                    source="execution_service",
                )
                raise
        final_safety_failures = current_submission_safety_failures()
        if final_safety_failures:
            fail_final_submission(final_safety_failures)
        if self.paper_submission_coordinator is not None:
            try:
                self.paper_submission_coordinator.prepare_order(
                    order_plan.model_copy(deep=True),
                    run_id=paper_run_id or "",
                    user_id=policy.user_id,
                    snapshot=portfolio_snapshot,
                    quote=market_quote,
                    entry_atr14=entry_atr14,
                    quote_max_age_seconds=policy.stale_quote_max_age_seconds,
                    snapshot_max_age_seconds=snapshot_max_age_seconds,
                    minimum_cash_reserve=(
                        policy.min_cash_weight * portfolio_snapshot.equity
                    ),
                )
            except Exception:
                before = order_plan.model_copy(deep=True)
                order_plan.blocked_reason = "paper_dispatch_prepare_failed"
                transition_order_plan(
                    order_plan=order_plan,
                    new_status=OrderStatus.failed,
                    audit=self.audit,
                    user_id=policy.user_id,
                    source="execution_service",
                    action="paper_dispatch_prepare_failed",
                )
                self.repositories.order_plans.update(order_plan)
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="order_plan",
                    entity_id=order_plan.order_plan_id,
                    action="paper_dispatch_prepare_failed",
                    before_state=before,
                    after_state={"reason": "durable_paper_evidence_unavailable"},
                    source="execution_service",
                )
                raise RiskCheckRequired(
                    "durable paper dispatch preparation failed"
                ) from None
        broker_order, fills = broker.submit_order(order_plan)
        evidence_failures: list[str] = []
        if broker_order.order_plan_id != order_plan.order_plan_id:
            evidence_failures.append("broker_order_plan_mismatch")
        if broker_order.broker_mode != policy.broker:
            evidence_failures.append("broker_mode_mismatch")
        fill_ids = [fill.fill_id for fill in fills]
        if len(fill_ids) != len(set(fill_ids)):
            evidence_failures.append("duplicate_fill_id")
        for fill in fills:
            if fill.order_plan_id != order_plan.order_plan_id:
                evidence_failures.append("fill_order_plan_mismatch")
            if fill.broker_order_id != broker_order.broker_order_id:
                evidence_failures.append("fill_broker_order_mismatch")
            if fill.symbol.strip().upper() != order_plan.intent.symbol.strip().upper():
                evidence_failures.append("fill_symbol_mismatch")
            if fill.filled_at.tzinfo is None or fill.filled_at.utcoffset() is None:
                evidence_failures.append("fill_timestamp_naive")
            expected_notional = fill.quantity * fill.price
            if not isclose(
                fill.notional,
                expected_notional,
                rel_tol=0.000001,
                abs_tol=0.01,
            ):
                evidence_failures.append("fill_notional_mismatch")
        filled_quantity = sum(fill.quantity for fill in fills)
        if filled_quantity > order_plan.intent.quantity + 0.000001:
            evidence_failures.append("aggregate_fill_quantity_exceeded")
        if evidence_failures:
            order_plan.blocked_reason = "broker_submission_evidence_invalid"
            self.repositories.order_plans.update(order_plan)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=order_plan.order_plan_id,
                action="risk_check_failed",
                before_state=order_plan,
                after_state={"failed_checks": sorted(set(evidence_failures))},
                source="broker_adapter",
            )
            raise RuntimeError(
                "broker submission evidence invalid: "
                f"{sorted(set(evidence_failures))}"
            )
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
        if 0 < filled_quantity < order_plan.intent.quantity - 0.000001:
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.partially_filled,
                audit=self.audit,
                user_id=policy.user_id,
                source="broker_adapter",
                action="order_partially_filled",
            )
        elif isclose(
            filled_quantity,
            order_plan.intent.quantity,
            rel_tol=0,
            abs_tol=0.000001,
        ):
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.filled,
                audit=self.audit,
                user_id=policy.user_id,
                source="broker_adapter",
            )
        self.repositories.order_plans.update(order_plan)
        return order_plan, broker_order, fills

    def _persist_operator_safety_state(
        self,
        *,
        policy_id: str,
        autopilot_paused: bool | object = _SAFETY_FIELD_UNSET,
        broker_healthy: bool | object = _SAFETY_FIELD_UNSET,
        last_blocked_reason: str | None | object = _SAFETY_FIELD_UNSET,
        require_healthy_broker: bool = False,
    ) -> None:
        provider = self.operator_safety_state_provider
        if provider is None or not (
            hasattr(provider, "load_operator_safety_state")
            and hasattr(provider, "save_operator_safety_state")
        ):
            next_healthy = (
                self.broker_healthy
                if broker_healthy is _SAFETY_FIELD_UNSET
                else bool(broker_healthy)
            )
            if require_healthy_broker and not next_healthy:
                raise RiskCheckRequired(
                    "broker health must recover before autopilot can resume"
                )
            if autopilot_paused is not _SAFETY_FIELD_UNSET:
                self.autopilot_paused = bool(autopilot_paused)
            if broker_healthy is not _SAFETY_FIELD_UNSET:
                self.broker_healthy = bool(broker_healthy)
            if last_blocked_reason is not _SAFETY_FIELD_UNSET:
                self.last_blocked_reason = last_blocked_reason
            return
        if hasattr(provider, "patch_operator_safety_state"):
            try:
                persisted = provider.patch_operator_safety_state(
                    policy_id=policy_id,
                    autopilot_paused=(
                        None
                        if autopilot_paused is _SAFETY_FIELD_UNSET
                        else bool(autopilot_paused)
                    ),
                    broker_healthy=(
                        None
                        if broker_healthy is _SAFETY_FIELD_UNSET
                        else bool(broker_healthy)
                    ),
                    last_blocked_reason=(
                        None
                        if last_blocked_reason is _SAFETY_FIELD_UNSET
                        else last_blocked_reason
                    ),
                    set_last_blocked_reason=(
                        last_blocked_reason is not _SAFETY_FIELD_UNSET
                    ),
                    require_healthy_broker=require_healthy_broker,
                    updated_at=utc_now(),
                )
            except Exception as exc:
                if require_healthy_broker:
                    self._hydrate_operator_safety_state(policy_id=policy_id)
                    raise RiskCheckRequired(
                        "broker health must recover before autopilot can resume"
                    ) from exc
                raise
            self.autopilot_paused = persisted.autopilot_paused
            self.broker_healthy = persisted.broker_healthy
            self.last_blocked_reason = persisted.last_blocked_reason
            return
        existing = provider.load_operator_safety_state(policy_id)
        now = utc_now()
        paused_value = (
            existing.autopilot_paused
            if existing is not None
            else self.autopilot_paused
        )
        healthy_value = (
            existing.broker_healthy
            if existing is not None
            else self.broker_healthy
        )
        reason_value = (
            existing.last_blocked_reason
            if existing is not None
            else self.last_blocked_reason
        )
        if autopilot_paused is not _SAFETY_FIELD_UNSET:
            paused_value = bool(autopilot_paused)
        if broker_healthy is not _SAFETY_FIELD_UNSET:
            healthy_value = bool(broker_healthy)
        if last_blocked_reason is not _SAFETY_FIELD_UNSET:
            reason_value = last_blocked_reason
        if require_healthy_broker and not healthy_value:
            self.autopilot_paused = paused_value
            self.broker_healthy = healthy_value
            self.last_blocked_reason = reason_value
            raise RiskCheckRequired(
                "broker health must recover before autopilot can resume"
            )
        if existing is None:
            state = OperatorSafetyState(
                policy_id=policy_id,
                autopilot_paused=paused_value,
                broker_healthy=healthy_value,
                last_blocked_reason=reason_value,
                updated_at=now,
            )
        else:
            state = existing.model_copy(
                update={
                    "autopilot_paused": paused_value,
                    "broker_healthy": healthy_value,
                    "last_blocked_reason": reason_value,
                    "updated_at": max(
                        now,
                        existing.updated_at + timedelta(microseconds=1),
                    ),
                    "revision": existing.revision + 1,
                }
            )
            state = OperatorSafetyState.model_validate(state.model_dump())
        persisted = provider.save_operator_safety_state(state)
        self.autopilot_paused = persisted.autopilot_paused
        self.broker_healthy = persisted.broker_healthy
        self.last_blocked_reason = persisted.last_blocked_reason

    def record_broker_health(
        self,
        *,
        policy_id: str,
        healthy: bool,
        reason: str | None = None,
    ) -> None:
        self._hydrate_operator_safety_state(policy_id=policy_id)
        self.broker_healthy = healthy
        if not healthy:
            self.autopilot_paused = True
            self.last_blocked_reason = reason or "broker_failure"
        self._persist_operator_safety_state(
            policy_id=policy_id,
            broker_healthy=healthy,
            autopilot_paused=(True if not healthy else _SAFETY_FIELD_UNSET),
            last_blocked_reason=(
                self.last_blocked_reason
                if not healthy
                else _SAFETY_FIELD_UNSET
            ),
        )

    def pause_guarded_autopilot(self, *, policy_id: str, reason: str = "user_paused") -> dict[str, object]:
        policy = self.repositories.policies.require(policy_id)
        self._hydrate_operator_safety_state(policy_id=policy_id)
        self.autopilot_paused = True
        self.last_blocked_reason = "autopilot_paused"
        self._persist_operator_safety_state(
            policy_id=policy_id,
            autopilot_paused=True,
            last_blocked_reason="autopilot_paused",
        )
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
        self._hydrate_operator_safety_state(policy_id=policy_id)
        self._persist_operator_safety_state(
            policy_id=policy_id,
            autopilot_paused=False,
            last_blocked_reason=None,
            require_healthy_broker=True,
        )
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
