from __future__ import annotations

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import BrokerMode, UserPolicy


def test_operation_report_markdown_contains_attribution_sections() -> None:
    service = HarnessService()
    policy = UserPolicy(broker=BrokerMode.paper, preferred_themes=["ai"])
    service.repositories.policies.add(policy)
    signals = service.run_signals()
    plan = service.create_portfolio_plan(
        policy_id=policy.policy_id,
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
    )
    proposal = service.generate_order_proposals(
        portfolio_plan_id=plan.plan_id,
        snapshot=fixture_portfolio_snapshot(),
    )[0]
    service.approve_order_plan(proposal.order_plan_id)
    service.submit_order_plan(proposal.order_plan_id)

    report = service.create_daily_report(policy_id=policy.policy_id)
    markdown = report.summary["operation_markdown"]

    assert "# Operation Report" in markdown
    assert "## Policy Intent" in markdown
    assert "## Paper Trial Metrics" in markdown
    assert "## Signal Contribution" in markdown
    assert "## Risk Budget" in markdown
    assert "## Sector Attribution" in markdown
    assert "## Theme Attribution" in markdown
    assert "## Position Attribution" in markdown
    assert "## Rejected And Trimmed Decisions" in markdown
    assert "Live trading enabled: no" in markdown


def test_operation_report_markdown_marks_missing_ledger_unavailable() -> None:
    service = HarnessService()
    policy = UserPolicy()
    service.repositories.policies.add(policy)
    signals = service.run_signals()
    service.create_portfolio_plan(
        policy_id=policy.policy_id,
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
    )

    report = service.create_daily_report(policy_id=policy.policy_id)
    markdown = report.summary["operation_markdown"]
    attribution = report.summary["attribution_report"]

    assert attribution["status"] == "unavailable"
    assert attribution["unavailable_reason"] == "missing_ledger"
    assert "Status: unavailable" in markdown
    assert "Unavailable reason: missing_ledger" in markdown
    assert any(flag["code"] == "missing_ledger" for flag in report.summary["review_flags"])
