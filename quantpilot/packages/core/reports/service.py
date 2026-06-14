from __future__ import annotations

from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.reports.paper_metrics import PaperTrialMetricsCalculator
from quantpilot.packages.core.risk.types import BatchRiskDecision
from quantpilot.packages.core.schemas import Fill, OperationReport, OrderPlan, UserPolicy
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
    paper_metrics = PaperTrialMetricsCalculator().calculate(
        ledger_entries=ledger_entries,
        target_weights=latest_plan.target_weights if latest_plan is not None else None,
        target_cash_weight=latest_plan.cash_target_weight if latest_plan is not None else None,
        snapshot=fixture_portfolio_snapshot() if latest_plan is not None else None,
        batch_risk_decisions=_batch_risk_decisions_from_audit(repositories),
        signal_timestamps=signal_timestamps,
        max_daily_turnover=policy.max_daily_turnover,
        single_order_cash_limit=policy.single_order_cash_limit,
    )
    summary["paper_trial_metrics"] = paper_metrics.model_dump(mode="json")
    return OperationReport(
        user_id=user_id,
        policy_id=policy.policy_id,
        summary=summary,
        order_plan_ids=[order.order_plan_id for order in orders],
        fill_ids=[fill.fill_id for fill in fills],
        audit_event_count=len(repositories.audit_logs.list()),
        live_trading_enabled=False,
    )
