from __future__ import annotations

from quantpilot.packages.core.reports.report_types import AttributionReport


def render_attribution_markdown(report: AttributionReport) -> str:
    lines = [
        "# Operation Report",
        "",
        f"Status: {report.status}",
        f"Live trading enabled: {'yes' if report.live_trading_enabled else 'no'}",
        f"Ledger primary source: {report.ledger_primary_source}",
        f"Ledger events: {report.ledger_event_count}",
    ]
    if report.unavailable_reason:
        lines.append(f"Unavailable reason: {report.unavailable_reason}")

    policy = report.policy_intent
    lines.extend(
        [
            "",
            "## Policy Intent",
            policy.summary,
            f"Allowed order types: {', '.join(policy.allowed_order_types) or 'none'}",
        ]
    )

    metrics = report.paper_trial_metrics
    lines.extend(
        [
            "",
            "## Paper Trial Metrics",
            f"Status: {metrics.status}",
            f"Turnover notional: {metrics.turnover_notional:.2f}",
            f"Filled notional: {metrics.execution_quality.filled_notional:.2f}",
            f"Fill ratio: {_format_ratio(metrics.execution_quality.fill_ratio)}",
        ]
    )
    if metrics.unavailable_reason:
        lines.append(f"Metrics unavailable reason: {metrics.unavailable_reason}")

    lines.extend(["", "## Signal Contribution"])
    if report.signal_contributions:
        for item in report.signal_contributions:
            lines.append(
                "- "
                f"{item.symbol}: {item.action or 'unknown'} score={item.contribution_score:.6f}, "
                f"intended={item.intended_notional:.2f}, filled={item.filled_notional:.2f}"
            )
    else:
        lines.append("- unavailable")

    risk = report.risk_budget
    lines.extend(
        [
            "",
            "## Risk Budget",
            f"Status: {risk.status}",
            risk.explanation,
            f"Largest order usage: {_format_ratio(risk.largest_order_usage)}",
        ]
    )
    if risk.failed_check_counts:
        failed = ", ".join(f"{name}={count}" for name, count in sorted(risk.failed_check_counts.items()))
        lines.append(f"Failed checks: {failed}")

    lines.extend(["", "## Sector Attribution"])
    if report.sector_attribution:
        for item in report.sector_attribution:
            lines.append(
                "- "
                f"{item.sector}: symbols={', '.join(item.symbols) or 'none'}, "
                f"intended={item.intended_notional:.2f}, filled={item.filled_notional:.2f}, "
                f"rejected={item.rejected_notional:.2f}"
            )
    else:
        lines.append("- unavailable")

    lines.extend(["", "## Theme Attribution"])
    for item in report.theme_attribution:
        lines.append(
            "- "
            f"{item.theme}: status={item.data_status}, signals={item.signal_count}, "
            f"intended={item.intended_notional:.2f}, filled={item.filled_notional:.2f}"
        )

    lines.extend(["", "## Position Attribution"])
    if report.position_attribution:
        for item in report.position_attribution:
            lines.append(
                "- "
                f"{item.symbol}: {item.status}, intended={item.intended_notional:.2f}, "
                f"filled={item.filled_notional:.2f}, rejected={item.rejected_notional:.2f}"
            )
    else:
        lines.append("- unavailable")

    lines.extend(["", "## Rejected And Trimmed Decisions"])
    if report.rejected_trimmed_explanations:
        for item in report.rejected_trimmed_explanations:
            lines.append(
                "- "
                f"{item.decision_type} {item.symbol or item.order_plan_id or item.intent_id or 'unknown'}: "
                f"{', '.join(item.reason_codes)}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Review Flags"])
    if report.review_flags:
        for flag in report.review_flags:
            lines.append(f"- {flag.severity}: {flag.code} - {flag.detail}")
    else:
        lines.append("- none")

    return "\n".join(lines)


def _format_ratio(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.2%}"
