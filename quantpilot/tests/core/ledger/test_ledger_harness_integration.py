from __future__ import annotations

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.ledger.types import LedgerEventType
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import BrokerMode, OrderStatus, UserPolicy


def _service_with_policy(policy: UserPolicy | None = None) -> tuple[HarnessService, UserPolicy]:
    service = HarnessService()
    active_policy = policy or service.parse_policy()
    if policy is not None:
        service.repositories.policies.add(policy)
    signals = service.run_signals()
    service.create_portfolio_plan(policy_id=active_policy.policy_id, signals=signals, snapshot=fixture_portfolio_snapshot())
    return service, active_policy


def _generate_proposals(service: HarnessService, policy: UserPolicy):
    plan = service.repositories.portfolio_plans.list()[-1]
    return service.generate_order_proposals(portfolio_plan_id=plan.plan_id, snapshot=fixture_portfolio_snapshot())


def test_mock_submission_records_lifecycle_events_and_report_summary() -> None:
    service, policy = _service_with_policy()
    proposal = _generate_proposals(service, policy)[0]

    service.approve_order_plan(proposal.order_plan_id)
    submitted, broker_order, fills = service.submit_order_plan(proposal.order_plan_id)
    report = service.create_daily_report(policy_id=policy.policy_id)

    entries = service.repositories.reconciliation_ledger.by_order_plan_id(proposal.order_plan_id)
    event_types = [entry.event_type for entry in entries]
    assert submitted.status == OrderStatus.filled
    assert broker_order.broker_mode == BrokerMode.mock
    assert fills
    assert event_types == [
        LedgerEventType.order_intent,
        LedgerEventType.submitted,
        LedgerEventType.fill,
        LedgerEventType.position_update,
    ]
    assert {entry.idempotency_key for entry in entries} == {proposal.idempotency_key}
    assert {entry.source for entry in entries} == {"mock"}
    assert report.summary["ledger_event_count"] == len(service.repositories.reconciliation_ledger.list())
    assert report.summary["ledger_event_counts"]["position_update"] == 1
    assert report.summary["ledger_sources"] == ["mock"]
    assert report.live_trading_enabled is False


def test_paper_partial_fill_records_partial_and_final_fill_sources() -> None:
    policy = UserPolicy(broker=BrokerMode.paper)
    service, active_policy = _service_with_policy(policy)
    proposal = _generate_proposals(service, active_policy)[0]

    service.approve_order_plan(proposal.order_plan_id)
    submitted, broker_order, fills = service.submit_order_plan(proposal.order_plan_id)

    entries = service.repositories.reconciliation_ledger.by_order_plan_id(proposal.order_plan_id)
    event_types = [entry.event_type for entry in entries]
    assert submitted.status == OrderStatus.filled
    assert broker_order.broker_mode == BrokerMode.paper
    assert len(fills) == 2
    assert LedgerEventType.partial_fill in event_types
    assert LedgerEventType.fill in event_types
    assert {entry.source for entry in entries} == {"paper"}
    assert {entry.data_mode for entry in entries} == {"paper_trading"}


def test_reject_and_cancel_record_terminal_events_with_reason() -> None:
    service, policy = _service_with_policy()
    first, second = _generate_proposals(service, policy)[:2]

    service.reject_order_plan(first.order_plan_id, reason="skip_today")
    service.cancel_order_plan(second.order_plan_id, reason="user_cancelled")

    rejected = service.repositories.reconciliation_ledger.by_order_plan_id(first.order_plan_id)
    cancelled = service.repositories.reconciliation_ledger.by_order_plan_id(second.order_plan_id)

    assert rejected[-1].event_type == LedgerEventType.reject
    assert rejected[-1].metadata["reason"] == "skip_today"
    assert cancelled[-1].event_type == LedgerEventType.cancel
    assert cancelled[-1].metadata["reason"] == "user_cancelled"
