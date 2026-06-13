from __future__ import annotations

from quantpilot.packages.core.operator.schemas import OperatorReport


def render_operator_report_text(report: OperatorReport) -> str:
    """Deterministic plain-language rendering of an operator report.

    This renderer never calls an LLM, so operator reporting keeps working when no
    language model is available.
    """
    lines = [
        f"Operator run {report.run_id} for user {report.user_id}",
        f"Policy {report.policy_id} version {report.policy_version}",
        f"Status: {report.status}",
        f"Started: {report.started_at.isoformat()}",
        f"Completed: {report.completed_at.isoformat()}",
        f"Live trading enabled: {'YES' if report.live_trading_enabled else 'NO'}",
    ]
    selection = report.strategy_selection
    if selection.selected_strategy_id:
        lines.append(
            f"Selected strategy: {selection.selected_strategy_id} v{selection.selected_version} ({selection.reason})"
        )
    else:
        lines.append(f"No strategy selected: {selection.reason}")
    for strategy_id, reason in sorted(selection.rejected.items()):
        lines.append(f"  rejected {strategy_id}: {reason}")
    if report.fallback is not None:
        lines.append(
            f"Fallback: level 5 -> level {report.fallback.to_level} because {report.fallback.reason_code} ({report.fallback.detail})"
        )
    lines.append(f"Decisions ({len(report.decisions)}):")
    for decision in report.decisions:
        target = decision.order_plan_id or decision.strategy_id or "run"
        lines.append(f"  {decision.action} {target}: {decision.reason}")
    lines.append(f"Submitted order plans: {len([d for d in report.decisions if d.action == 'submit'])}")
    lines.append(f"Broker orders: {', '.join(report.broker_order_ids) or 'none'}")
    lines.append(f"Risk checks: {', '.join(report.risk_check_ids) or 'none'}")
    lines.append("Safety flags:")
    for key in sorted(report.safety_flags):
        lines.append(f"  {key}={report.safety_flags[key]}")
    lines.append(f"Audit events recorded: {report.audit_event_count}")
    return "\n".join(lines)
