from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from quantpilot.packages.core.analyst.reports import generate_analyst_report
from quantpilot.packages.core.data.providers import MarketDataProvider, SecurityProvider
from quantpilot.packages.core.portfolio.planner import (
    build_rebalance_suggestion_report,
    fixture_portfolio_snapshot,
)
from quantpilot.packages.core.schemas import (
    AnalystReport,
    CandidateUniverseItem,
    OperationReport,
    RebalanceSuggestionReport,
    Signal,
    StrategyRecipe,
    UserPolicy,
)
from quantpilot.packages.core.signals.service import generate_signals
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators
from quantpilot.packages.core.universe.builder import build_candidate_universe
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


@dataclass(frozen=True)
class Level12RunResult:
    policy: UserPolicy
    strategy: StrategyRecipe
    universe: list[CandidateUniverseItem]
    analyst_reports: list[AnalystReport]
    signals: list[Signal]
    rebalance: RebalanceSuggestionReport
    daily_report: OperationReport
    order_submission_enabled: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "strategy": self.strategy,
            "universe": self.universe,
            "analyst_reports": self.analyst_reports,
            "signals": self.signals,
            "rebalance": self.rebalance,
            "daily_report": self.daily_report,
            "order_submission_enabled": self.order_submission_enabled,
        }


class Level12Service:
    def __init__(
        self,
        *,
        repositories: RepositoryRegistry,
        audit: AuditRecorder,
        security_provider: SecurityProvider,
        market_data_provider: MarketDataProvider,
        load_strategy: Callable[[], StrategyRecipe],
    ) -> None:
        self.repositories = repositories
        self.audit = audit
        self.security_provider = security_provider
        self.market_data_provider = market_data_provider
        self.load_strategy = load_strategy

    def run(self, *, policy_id: str) -> Level12RunResult:
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
                "supported_actions": [
                    action.value for action in sorted({signal.action for signal in signals}, key=lambda item: item.value)
                ],
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

        return Level12RunResult(
            policy=policy,
            strategy=recipe,
            universe=universe,
            analyst_reports=analyst_reports,
            signals=signals,
            rebalance=rebalance_report,
            daily_report=operation_report,
        )
