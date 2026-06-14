from __future__ import annotations

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import BrokerMode, OrderStatus, UserPolicy


def _service_with_policy(policy: UserPolicy | None = None) -> tuple[HarnessService, UserPolicy]:
    service = HarnessService()
    active_policy = policy or service.parse_policy()
    if policy is not None:
        service.repositories.policies.add(policy)
    signals = service.run_signals()
    service.create_portfolio_plan(
        policy_id=active_policy.policy_id,
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
    )
    return service, active_policy


def test_report_includes_optional_paper_trial_metrics_from_ledger() -> None:
    service, policy = _service_with_policy(UserPolicy(broker=BrokerMode.paper))
    plan = service.repositories.portfolio_plans.list()[-1]
    proposal = service.generate_order_proposals(
        portfolio_plan_id=plan.plan_id,
        snapshot=fixture_portfolio_snapshot(),
    )[0]

    service.approve_order_plan(proposal.order_plan_id)
    submitted, broker_order, fills = service.submit_order_plan(proposal.order_plan_id)
    report = service.create_daily_report(policy_id=policy.policy_id)

    paper_metrics = report.summary["paper_trial_metrics"]
    assert submitted.status == OrderStatus.filled
    assert broker_order.broker_mode == BrokerMode.paper
    assert len(fills) == 2
    assert paper_metrics["status"] == "available"
    assert paper_metrics["ledger_sources"] == ["paper"]
    assert paper_metrics["data_modes"] == ["paper_trading"]
    assert paper_metrics["turnover_notional"] == sum(fill.notional for fill in fills)
    assert paper_metrics["execution_quality"]["orders_intended"] >= 1
    assert paper_metrics["execution_quality"]["orders_filled"] == 1
    assert paper_metrics["execution_quality"]["fill_ratio"] > 0
    assert paper_metrics["execution_quality"]["latency_status"] == "available"
    assert paper_metrics["live_trading_enabled"] is False


def test_report_marks_paper_trial_metrics_unavailable_without_ledger() -> None:
    service, policy = _service_with_policy()

    report = service.create_daily_report(policy_id=policy.policy_id)

    paper_metrics = report.summary["paper_trial_metrics"]
    assert paper_metrics["status"] == "unavailable"
    assert paper_metrics["unavailable_reason"] == "missing_ledger"
    assert paper_metrics["live_trading_enabled"] is False
