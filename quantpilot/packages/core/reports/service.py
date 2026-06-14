from __future__ import annotations

from typing import Any

from quantpilot.packages.core.ledger.types import LedgerEntry
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.reports.attribution import AttributionReportBuilder
from quantpilot.packages.core.reports.markdown import render_attribution_markdown
from quantpilot.packages.core.reports.paper_metrics import PaperTrialMetricsCalculator
from quantpilot.packages.core.reports.metrics_types import PaperTrialMetrics
from quantpilot.packages.core.reports.report_types import AttributionOperationReport
from quantpilot.packages.core.risk.types import BatchRiskDecision
from quantpilot.packages.core.schemas import Fill, OperationReport, OrderPlan, PortfolioPlan, UserPolicy
from quantpilot.packages.db.repositories import RepositoryRegistry


def _batch_risk_decisions_from_audit(repositories: RepositoryRegistry) -> list[BatchRiskDecision]:
    decisions: list[BatchRiskDecision] = []
    for event in repositories.audit_logs.list():
        if event.action not in {"batch_risk_partial_allowed", "batch_risk_rejected"}:
            continue
        if event.after_state is None:
            continue
        try:
            decisions.append(BatchRiskDecision.model_validate(event.after_state))
        except ValueError:
            continue
    return decisions


def _build_attribution_operation_report(
    *,
    user_id: str,
    policy: UserPolicy,
    legacy_summary: dict[str, Any],
    ledger_entries: list[LedgerEntry],
    paper_metrics: PaperTrialMetrics,
    orders: list[OrderPlan],
    repositories: RepositoryRegistry,
    portfolio_plans: list[PortfolioPlan],
    latest_plan: PortfolioPlan | None,
    batch_risk_decisions: list[BatchRiskDecision],
) -> AttributionOperationReport:
    builder = AttributionReportBuilder()
    try:
        attribution = builder.build(
            policy=policy,
            ledger_entries=ledger_entries,
            paper_metrics=paper_metrics,
            signals=repositories.signals.list(),
            orders=orders,
            portfolio_plans=portfolio_plans,
            batch_risk_decisions=batch_risk_decisions,
            snapshot=fixture_portfolio_snapshot() if latest_plan is not None else None,
        )
    except Exception as exc:
        attribution = builder.unavailable_report(
            policy=policy,
            paper_metrics=paper_metrics,
            reason=f"attribution_error:{exc.__class__.__name__}",
        )
    markdown = render_attribution_markdown(attribution)
    machine_payload = {
        "schema_version": "quantpilot.attribution_operation_report.v1",
        "user_id": user_id,
        "policy_id": policy.policy_id,
        "policy_version": policy.version,
        "status": attribution.status,
        "legacy_summary": legacy_summary,
        "attribution_report": attribution.model_dump(mode="json"),
        "review_flags": [flag.model_dump(mode="json") for flag in attribution.review_flags],
        "live_trading_enabled": False,
    }
    return AttributionOperationReport(
        user_id=user_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        status=attribution.status,
        attribution_report=attribution,
        markdown=markdown,
        machine_payload=machine_payload,
        review_flags=attribution.review_flags,
        live_trading_enabled=False,
    )


def build_operation_report(
    *,
    user_id: str,
    policy: UserPolicy,
    orders: list[OrderPlan],
    fills: list[Fill],
    repositories: RepositoryRegistry,
) -> OperationReport:
    summary = {
        "orders_total": len(orders),
        "fills_total": len(fills),
        "filled_notional": round(sum(fill.notional for fill in fills), 2),
        "broker": policy.broker.value,
        "execution_mode": policy.execution_mode.value,
    }
    ledger_summary = repositories.reconciliation_ledger.summary(policy_id=policy.policy_id)
    summary.update(ledger_summary)
    ledger_entries = [
        entry for entry in repositories.reconciliation_ledger.list()
        if entry.policy_id == policy.policy_id
    ]
    portfolio_plans = [
        plan for plan in repositories.portfolio_plans.list()
        if plan.policy_id == policy.policy_id
    ]
    latest_plan = portfolio_plans[-1] if portfolio_plans else None
    signal_timestamps = {
        signal.symbol: signal.generated_at
        for signal in repositories.signals.list()
        if signal.policy_version in {None, policy.version}
    }
    batch_risk_decisions = _batch_risk_decisions_from_audit(repositories)
    paper_metrics = PaperTrialMetricsCalculator().calculate(
        ledger_entries=ledger_entries,
        target_weights=latest_plan.target_weights if latest_plan is not None else None,
        target_cash_weight=latest_plan.cash_target_weight if latest_plan is not None else None,
        snapshot=fixture_portfolio_snapshot() if latest_plan is not None else None,
        batch_risk_decisions=batch_risk_decisions,
        signal_timestamps=signal_timestamps,
        max_daily_turnover=policy.max_daily_turnover,
        single_order_cash_limit=policy.single_order_cash_limit,
    )
    summary["paper_trial_metrics"] = paper_metrics.model_dump(mode="json")
    attribution_operation_report = _build_attribution_operation_report(
        user_id=user_id,
        policy=policy,
        legacy_summary=dict(summary),
        ledger_entries=ledger_entries,
        paper_metrics=paper_metrics,
        orders=orders,
        repositories=repositories,
        portfolio_plans=portfolio_plans,
        latest_plan=latest_plan,
        batch_risk_decisions=batch_risk_decisions,
    )
    summary["attribution_report"] = attribution_operation_report.attribution_report.model_dump(mode="json")
    summary["operation_markdown"] = attribution_operation_report.markdown
    summary["machine_payload"] = attribution_operation_report.machine_payload
    summary["review_flags"] = [
        flag.model_dump(mode="json")
        for flag in attribution_operation_report.review_flags
    ]
    summary["attribution_operation_report"] = attribution_operation_report.model_dump(mode="json")
    return OperationReport(
        user_id=user_id,
        policy_id=policy.policy_id,
        summary=summary,
        order_plan_ids=[order.order_plan_id for order in orders],
        fill_ids=[fill.fill_id for fill in fills],
        audit_event_count=len(repositories.audit_logs.list()),
        live_trading_enabled=False,
    )
