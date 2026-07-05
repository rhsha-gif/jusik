from __future__ import annotations

from datetime import timedelta

import pytest

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import ApprovalTicketStatus, BrokerMode, UserPolicy, utc_now


def test_fixture_approval_ticket_approves_and_submits_mock_order() -> None:
    service = HarnessService()
    policy = service.parse_policy("fixture")
    ticket = service.generate_approval_tickets(policy_id=policy.policy_id, data_mode="fixture")[0]

    result = service.approve_and_submit_approval_ticket(ticket.ticket_id, approved_by="tester")

    submitted_ticket = result["ticket"]
    assert submitted_ticket.status == ApprovalTicketStatus.submitted
    assert result["broker_order"].broker_mode == BrokerMode.mock  # type: ignore[union-attr]
    assert result["fills"]
    assert result["live_trading_enabled"] is False


def test_live_candidate_approval_blocks_before_broker_submission() -> None:
    service = HarnessService()
    policy = service.parse_policy("fixture")
    ticket = service.generate_approval_tickets(
        policy_id=policy.policy_id,
        data_mode="live_trading_candidate",
    )[0]

    result = service.approve_and_submit_approval_ticket(ticket.ticket_id, approved_by="tester")

    blocked_ticket = result["ticket"]
    assert blocked_ticket.status == ApprovalTicketStatus.blocked
    assert blocked_ticket.blocked_reason == "live_broker_unavailable"
    assert result["broker_order"] is None
    assert service.repositories.broker_orders.list() == []


def test_rejected_approval_ticket_blocks_order_submission() -> None:
    service = HarnessService()
    policy = service.parse_policy("fixture")
    ticket = service.generate_approval_tickets(policy_id=policy.policy_id, data_mode="fixture")[0]

    rejected = service.reject_approval_ticket(ticket.ticket_id, reason="not approved")

    assert rejected.status == ApprovalTicketStatus.rejected
    order_plan = service.repositories.order_plans.require(ticket.order_plan_id)
    assert order_plan.status.value == "rejected"
    with pytest.raises(RuntimeError):
        service.approve_and_submit_approval_ticket(ticket.ticket_id)


def test_expired_approval_ticket_fails_closed() -> None:
    service = HarnessService()
    policy = service.parse_policy("fixture")
    ticket = service.generate_approval_tickets(policy_id=policy.policy_id, data_mode="fixture")[0]
    ticket.expires_at = utc_now() - timedelta(seconds=1)
    service.repositories.approval_tickets.update(ticket)

    with pytest.raises(RuntimeError):
        service.approve_and_submit_approval_ticket(ticket.ticket_id)

    expired = service.repositories.approval_tickets.require(ticket.ticket_id)
    assert expired.status == ApprovalTicketStatus.expired
    assert service.repositories.broker_orders.list() == []


def test_duplicate_approval_ticket_submission_fails_closed() -> None:
    service = HarnessService()
    policy = service.parse_policy("fixture")
    ticket = service.generate_approval_tickets(policy_id=policy.policy_id, data_mode="fixture")[0]
    service.approve_and_submit_approval_ticket(ticket.ticket_id)

    with pytest.raises(RuntimeError):
        service.approve_and_submit_approval_ticket(ticket.ticket_id)


def test_paper_trading_ticket_requires_paper_broker() -> None:
    service = HarnessService()
    policy = UserPolicy(broker=BrokerMode.mock)
    service.repositories.policies.add(policy)
    ticket = service.generate_approval_tickets(policy_id=policy.policy_id, data_mode="paper_trading")[0]

    result = service.approve_and_submit_approval_ticket(ticket.ticket_id)

    blocked_ticket = result["ticket"]
    assert blocked_ticket.status == ApprovalTicketStatus.blocked
    assert blocked_ticket.blocked_reason == "paper_broker_required"
    assert result["broker_order"] is None
