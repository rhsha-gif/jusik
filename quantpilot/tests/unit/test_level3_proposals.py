from __future__ import annotations

from datetime import timedelta

import pytest

from quantpilot.packages.core.execution.state_machine import ApprovalRequired, RiskCheckRequired
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot, proposal_idempotency_key
from quantpilot.packages.core.schemas import OrderStatus, RiskCheck, UserPolicy, utc_now


def _service_with_plan(policy: UserPolicy | None = None) -> tuple[HarnessService, str]:
    service = HarnessService()
    active_policy = policy or service.parse_policy()
    if policy is not None:
        service.repositories.policies.add(policy)
    signals = service.run_signals()
    snapshot = fixture_portfolio_snapshot()
    plan = service.create_portfolio_plan(policy_id=active_policy.policy_id, signals=signals, snapshot=snapshot)
    return service, plan.plan_id


def test_idempotency_key_is_stable_and_unique() -> None:
    policy = UserPolicy(policy_id="pol-fixed", version=7)

    first = proposal_idempotency_key(
        policy=policy,
        strategy_id="pullback_trend_v1",
        strategy_version="1.0",
        symbol="AAA",
        side="buy",
        trading_date="2026-06-12",
    )
    same = proposal_idempotency_key(
        policy=policy,
        strategy_id="pullback_trend_v1",
        strategy_version="1.0",
        symbol="AAA",
        side="buy",
        trading_date="2026-06-12",
    )
    different = proposal_idempotency_key(
        policy=policy,
        strategy_id="pullback_trend_v1",
        strategy_version="1.0",
        symbol="AAA",
        side="sell",
        trading_date="2026-06-12",
    )

    assert first == same
    assert first != different
    assert len(first) == 32


def test_failed_risk_check_prevents_proposal_creation() -> None:
    policy = UserPolicy(single_order_cash_limit=1)
    service, plan_id = _service_with_plan(policy)

    proposals = service.generate_order_proposals(portfolio_plan_id=plan_id)

    assert proposals == []
    assert service.repositories.order_plans.list() == []
    assert any(event.action == "proposal_blocked" for event in service.repositories.audit_logs.list())


def test_proposal_risk_failure_audit_records_each_candidate_key(monkeypatch: pytest.MonkeyPatch) -> None:
    service, plan_id = _service_with_plan()
    seen_order_keys: list[str] = []

    def fail_risk_check(**kwargs: object) -> RiskCheck:
        order_plan = kwargs["order_plan"]
        assert hasattr(order_plan, "idempotency_key")
        seen_order_keys.append(order_plan.idempotency_key)
        return RiskCheck(
            order_plan_id=order_plan.order_plan_id,
            passed=False,
            failed_checks=["forced_failure"],
            policy_version=order_plan.policy_version,
            idempotency_key=order_plan.idempotency_key,
        )

    monkeypatch.setattr("quantpilot.packages.core.harness_service.run_risk_check", fail_risk_check)

    proposals = service.generate_order_proposals(portfolio_plan_id=plan_id)

    blocked_events = [
        event
        for event in service.repositories.audit_logs.list()
        if event.action == "proposal_blocked"
        and isinstance(event.after_state, dict)
        and event.after_state.get("failed_checks") == ["forced_failure"]
    ]
    assert proposals == []
    assert len(blocked_events) == len(seen_order_keys)
    assert [event.after_state["idempotency_key"] for event in blocked_events] == seen_order_keys


def test_level3_proposal_has_explanation_and_requires_user_approval() -> None:
    service, plan_id = _service_with_plan()

    proposals = service.generate_order_proposals(portfolio_plan_id=plan_id)

    assert proposals
    proposal = proposals[0]
    assert proposal.status == OrderStatus.proposed
    assert proposal.risk_check_id is not None
    assert proposal.risk_check_expires_at is not None
    assert proposal.explanation is not None
    assert proposal.explanation.symbol == proposal.intent.symbol
    assert proposal.explanation.risk_check_id == proposal.risk_check_id
    assert all(item.explanation is not None and item.explanation.idempotency_key == item.idempotency_key for item in proposals)
    with pytest.raises(ApprovalRequired):
        service.submit_order_plan(proposal.order_plan_id)


def test_user_rejection_prevents_later_submission() -> None:
    service, plan_id = _service_with_plan()
    proposal = service.generate_order_proposals(portfolio_plan_id=plan_id)[0]

    rejected = service.reject_order_plan(proposal.order_plan_id, reason="not today")

    assert rejected.status == OrderStatus.rejected
    with pytest.raises(ApprovalRequired):
        service.submit_order_plan(proposal.order_plan_id)


def test_user_modification_creates_re_risked_proposal_with_new_key() -> None:
    service, plan_id = _service_with_plan()
    proposal = service.generate_order_proposals(portfolio_plan_id=plan_id)[0]

    modified = service.modify_order_plan(
        proposal.order_plan_id,
        quantity=proposal.intent.quantity * 0.5,
        limit_price=proposal.intent.limit_price,
    )

    original = service.repositories.order_plans.require(proposal.order_plan_id)
    assert original.status == OrderStatus.modified
    assert modified.status == OrderStatus.proposed
    assert modified.replaces_order_plan_id == proposal.order_plan_id
    assert modified.idempotency_key != proposal.idempotency_key
    assert modified.risk_check_id is not None


def test_expired_risk_check_prevents_submission() -> None:
    service, plan_id = _service_with_plan()
    proposal = service.generate_order_proposals(portfolio_plan_id=plan_id)[0]
    proposal.risk_check_expires_at = utc_now() - timedelta(seconds=1)
    service.repositories.order_plans.update(proposal)
    service.approve_order_plan(proposal.order_plan_id)

    with pytest.raises(RiskCheckRequired):
        service.submit_order_plan(proposal.order_plan_id)


def test_duplicate_idempotency_key_cannot_submit_twice() -> None:
    service, plan_id = _service_with_plan()
    first = service.generate_order_proposals(portfolio_plan_id=plan_id)[0]
    service.approve_order_plan(first.order_plan_id)
    service.submit_order_plan(first.order_plan_id)

    duplicate = first.model_copy(
        update={
            "order_plan_id": "oplan-duplicate",
            "status": OrderStatus.user_approved,
            "risk_check_id": first.risk_check_id,
            "risk_check_expires_at": utc_now() + timedelta(minutes=10),
        },
        deep=True,
    )
    service.repositories.order_plans.add(duplicate)

    with pytest.raises(RiskCheckRequired):
        service.submit_order_plan(duplicate.order_plan_id)
