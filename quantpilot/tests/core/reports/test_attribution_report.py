from __future__ import annotations

from datetime import datetime, timezone

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.reports.attribution import AttributionReportBuilder
from quantpilot.packages.core.reports.paper_metrics import PaperTrialMetricsCalculator
from quantpilot.packages.core.risk.types import BatchPortfolioExposure, BatchRiskDecision
from quantpilot.packages.core.schemas import (
    BrokerMode,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    OrderType,
    PortfolioPlan,
    Signal,
    SignalAction,
    UserPolicy,
)


def _service_with_policy(policy: UserPolicy) -> tuple[HarnessService, UserPolicy]:
    service = HarnessService()
    service.repositories.policies.add(policy)
    signals = service.run_signals()
    service.create_portfolio_plan(
        policy_id=policy.policy_id,
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
    )
    return service, policy


def test_daily_report_generates_attribution_report_sections() -> None:
    service, policy = _service_with_policy(
        UserPolicy(
            broker=BrokerMode.paper,
            preferred_themes=["ai"],
            preferred_sectors=["tech"],
        )
    )
    plan = service.repositories.portfolio_plans.list()[-1]
    proposal = service.generate_order_proposals(
        portfolio_plan_id=plan.plan_id,
        snapshot=fixture_portfolio_snapshot(),
    )[0]

    service.approve_order_plan(proposal.order_plan_id)
    submitted, _, _ = service.submit_order_plan(proposal.order_plan_id)
    report = service.create_daily_report(policy_id=policy.policy_id)

    attribution = report.summary["attribution_report"]
    assert submitted.status == OrderStatus.filled
    assert attribution["status"] == "available"
    assert attribution["ledger_primary_source"] == "reconciliation_ledger"
    assert attribution["paper_trial_metrics"]["status"] == "available"
    assert attribution["signal_contributions"]
    assert attribution["risk_budget"]["status"] == "available"
    assert attribution["sector_attribution"]
    assert attribution["theme_attribution"][0]["theme"] == "ai"
    assert attribution["position_attribution"]
    assert attribution["live_trading_enabled"] is False


def _order(policy: UserPolicy, *, order_plan_id: str, symbol: str, notional: float) -> OrderPlan:
    intent = OrderIntent(
        intent_id=f"intent_{order_plan_id}",
        symbol=symbol,
        side="buy",
        order_type=OrderType.limit,
        quantity=notional / 100,
        limit_price=100,
        notional=notional,
        target_weight=0.05,
        reason=f"{symbol} fixture signal reason",
    )
    return OrderPlan(
        order_plan_id=order_plan_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=intent,
        idempotency_key=f"idem_{order_plan_id}",
    )


def _entry(
    policy: UserPolicy,
    order: OrderPlan,
    *,
    event_type: LedgerEventType,
    notional: float,
    metadata: dict[str, object] | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        event_type=event_type,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        order_plan_id=order.order_plan_id,
        intent_id=order.intent.intent_id,
        broker_order_id=f"broker_{order.order_plan_id}" if event_type != LedgerEventType.order_intent else None,
        fill_id=f"fill_{order.order_plan_id}" if event_type == LedgerEventType.fill else None,
        idempotency_key=order.idempotency_key,
        dedupe_key=f"{event_type.value}:{order.order_plan_id}",
        source="mock",
        data_mode="fixture",
        symbol=order.intent.symbol,
        side=order.intent.side,
        quantity=notional / 100,
        price=100,
        notional=notional,
        metadata=metadata or {},
        occurred_at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
    )


def test_attribution_builder_explains_rejected_and_trimmed_decisions() -> None:
    policy = UserPolicy(preferred_themes=["semiconductors"])
    kept = _order(policy, order_plan_id="oplan_keep", symbol="AAA", notional=1_000)
    rejected = _order(policy, order_plan_id="oplan_reject", symbol="BBB", notional=500)
    trimmed = _order(policy, order_plan_id="oplan_trim", symbol="CCC", notional=700)
    entries = [
        _entry(policy, kept, event_type=LedgerEventType.order_intent, notional=1_000),
        _entry(policy, kept, event_type=LedgerEventType.fill, notional=1_000),
        _entry(policy, rejected, event_type=LedgerEventType.order_intent, notional=500),
        _entry(
            policy,
            rejected,
            event_type=LedgerEventType.reject,
            notional=500,
            metadata={"reason": "max_daily_turnover_after_batch"},
        ),
    ]
    batch_decision = BatchRiskDecision(
        passed=True,
        mode="partial_batch",
        policy_version=policy.version,
        accepted_order_plan_ids=[kept.order_plan_id],
        rejected_order_plan_ids=[trimmed.order_plan_id],
        rejected_reasons={trimmed.order_plan_id: ["max_sector_weight_after_batch"]},
        portfolio_after_batch=BatchPortfolioExposure(
            cash=9_000,
            equity=10_000,
            cash_weight=0.9,
            position_values={},
            position_weights={},
            sector_values={},
            sector_weights={},
        ),
    )
    paper_metrics = PaperTrialMetricsCalculator().calculate(
        ledger_entries=entries,
        batch_risk_decisions=[batch_decision],
        max_daily_turnover=2_000,
        single_order_cash_limit=1_000,
    )
    signal = Signal(
        strategy_id="fixture_strategy",
        recipe_version="1.0",
        symbol="AAA",
        action=SignalAction.buy_ready,
        strength=0.8,
        target_weight_hint=0.05,
        reason="strong fixture trend",
    )
    plan = PortfolioPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        target_weights={"AAA": 0.05, "BBB": 0.0, "CCC": 0.0},
        cash_target_weight=0.95,
        order_intents=[kept.intent, rejected.intent, trimmed.intent],
    )

    attribution = AttributionReportBuilder().build(
        policy=policy,
        ledger_entries=entries,
        paper_metrics=paper_metrics,
        signals=[signal],
        orders=[kept, rejected, trimmed],
        portfolio_plans=[plan],
        batch_risk_decisions=[batch_decision],
        snapshot=fixture_portfolio_snapshot(),
    )

    decisions = {
        (item.decision_type, item.order_plan_id): item.reason_codes
        for item in attribution.rejected_trimmed_explanations
    }
    positions = {
        item.order_plan_id: item.status
        for item in attribution.position_attribution
    }
    assert attribution.status == "available"
    assert decisions[("rejected", rejected.order_plan_id)] == ["max_daily_turnover_after_batch"]
    assert decisions[("trimmed", trimmed.order_plan_id)] == ["max_sector_weight_after_batch"]
    assert positions[rejected.order_plan_id] == "rejected"
    assert positions[trimmed.order_plan_id] == "trimmed"
    assert attribution.risk_budget.failed_check_counts["max_sector_weight_after_batch"] == 1
    assert attribution.live_trading_enabled is False
