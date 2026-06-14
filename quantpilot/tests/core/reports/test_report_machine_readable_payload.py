from __future__ import annotations

import json

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import BrokerMode, UserPolicy


def test_report_machine_payload_is_json_serializable() -> None:
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
    payload = report.summary["machine_payload"]
    operation_report = report.summary["attribution_operation_report"]

    assert payload["schema_version"] == "quantpilot.attribution_operation_report.v1"
    assert payload["status"] == "available"
    assert payload["attribution_report"]["live_trading_enabled"] is False
    assert payload["offline_learning_report"]["live_auto_update"] is False
    assert payload["offline_learning_report"]["promotion_candidate"]["status"] == "pending_review"
    assert operation_report["machine_payload"]["schema_version"] == payload["schema_version"]
    assert operation_report["markdown"].startswith("# Operation Report")
    json.dumps(payload)
    json.dumps(operation_report)


def test_legacy_simple_report_fields_remain_present_with_rich_payload() -> None:
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

    assert report.summary["orders_total"] == 0
    assert report.summary["fills_total"] == 0
    assert report.summary["filled_notional"] == 0
    assert "paper_trial_metrics" in report.summary
    assert "machine_payload" in report.summary
    assert report.summary["offline_learning_report"]["status"] == "unavailable"
    assert report.summary["offline_learning_report"]["promotion_candidate"] is None
    assert report.live_trading_enabled is False
