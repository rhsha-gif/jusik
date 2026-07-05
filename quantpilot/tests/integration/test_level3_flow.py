from __future__ import annotations

from fastapi.testclient import TestClient

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import OrderStatus
from quantpilot.services.api.dependencies import get_harness_service
from quantpilot.services.api.main import app


def _client_for_service(service: HarnessService) -> TestClient:
    app.dependency_overrides[get_harness_service] = lambda: service
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def test_level3_flow_end_to_end() -> None:
    service = HarnessService()
    policy = service.parse_policy()
    signals = service.run_signals()
    plan = service.create_portfolio_plan(policy_id=policy.policy_id, signals=signals, snapshot=fixture_portfolio_snapshot())

    proposals = service.generate_order_proposals(portfolio_plan_id=plan.plan_id)
    proposal = proposals[0]
    assert proposal.status == OrderStatus.proposed
    approved = service.approve_order_plan(proposal.order_plan_id)
    assert approved.status == OrderStatus.user_approved
    submitted, broker_order, fills = service.submit_order_plan(approved.order_plan_id)
    report = service.create_daily_report(policy_id=policy.policy_id)

    assert submitted.status == OrderStatus.filled
    assert broker_order.broker_mode.value == "mock"
    assert fills
    assert report.live_trading_enabled is False


def test_order_proposal_api_routes_return_explicit_states() -> None:
    service = HarnessService()
    policy = service.parse_policy()
    service.run_signals()
    plan = service.create_portfolio_plan(policy_id=policy.policy_id)
    client = _client_for_service(service)
    try:
        generated = client.post("/api/orders/generate-proposals", json={"portfolio_plan_id": plan.plan_id})
        proposed = client.get("/api/orders/proposed")
        rejected = client.post(f"/api/orders/{generated.json()[0]['order_plan_id']}/reject", json={"reason": "skip"})
    finally:
        _clear_overrides()

    assert generated.status_code == 200
    assert proposed.status_code == 200
    assert proposed.json()[0]["status"] == "proposed"
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_level12_mock_execute_api_submits_mock_orders() -> None:
    service = HarnessService()
    policy = service.parse_policy()
    client = _client_for_service(service)
    try:
        response = client.post("/api/level-1-2/mock-execute", json={"policy_id": policy.policy_id})
    finally:
        _clear_overrides()

    body = response.json()
    assert response.status_code == 200
    assert body["order_submission_enabled"] is True
    assert body["broker"] == "mock"
    assert body["live_trading_enabled"] is False
    assert body["broker_orders"]
    assert body["fills"]


def test_approval_ticket_api_blocks_live_candidate_after_user_approval() -> None:
    service = HarnessService()
    policy = service.parse_policy()
    client = _client_for_service(service)
    try:
        generated = client.post(
            "/api/execution/approval-tickets/generate",
            json={"policy_id": policy.policy_id, "data_mode": "live_trading_candidate"},
        )
        ticket_id = generated.json()[0]["ticket_id"]
        pending = client.get("/api/execution/approval-tickets/pending")
        approved = client.post(
            f"/api/execution/approval-tickets/{ticket_id}/approve-and-submit",
            json={"approved_by": "tester"},
        )
    finally:
        _clear_overrides()

    assert generated.status_code == 200
    assert pending.status_code == 200
    assert pending.json()[0]["ticket_id"] == ticket_id
    assert approved.status_code == 200
    body = approved.json()
    assert body["ticket"]["status"] == "blocked"
    assert body["ticket"]["blocked_reason"] == "live_broker_unavailable"
    assert body["broker_order"] is None
    assert service.repositories.broker_orders.list() == []
