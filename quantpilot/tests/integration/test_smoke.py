from __future__ import annotations

from fastapi.testclient import TestClient

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.services.api.dependencies import get_harness_service
from quantpilot.services.api.main import app


def _client_for_service(service: HarnessService) -> TestClient:
    app.dependency_overrides[get_harness_service] = lambda: service
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def test_end_to_end_policy_to_report_smoke_test_passes() -> None:
    summary = HarnessService().run_smoke()

    assert summary["live_trading_enabled"] is False
    assert summary["broker"] == "mock"
    assert summary["signals"] == 7
    assert summary["fills"] == 3
    assert all(order["status"] == "filled" for order in summary["orders"])
    assert summary["audit_events"] >= 14


def test_api_smoke_route_passes() -> None:
    response = TestClient(app).post("/api/harness/run-smoke")

    assert response.status_code == 200
    body = response.json()
    assert body["live_trading_enabled"] is False
    assert body["broker"] == "mock"
    assert all(order["status"] == "filled" for order in body["orders"])


def test_portfolio_plan_api_guides_user_when_policy_is_missing() -> None:
    client = _client_for_service(HarnessService())
    try:
        response = client.post("/api/portfolio/plan", json={})
    finally:
        _clear_overrides()

    assert response.status_code == 409
    assert response.json()["detail"]["next_step"] == "POST /api/policies/parse"


def test_portfolio_plan_api_guides_user_when_signals_are_missing() -> None:
    service = HarnessService()
    policy = service.parse_policy()
    client = _client_for_service(service)
    try:
        response = client.post("/api/portfolio/plan", json={"policy_id": policy.policy_id})
    finally:
        _clear_overrides()

    assert response.status_code == 409
    assert response.json()["detail"]["next_step"] == "POST /api/signals/run"


def test_order_plan_api_guides_user_when_portfolio_plan_is_missing() -> None:
    client = _client_for_service(HarnessService())
    try:
        response = client.post("/api/orders/plan", json={})
    finally:
        _clear_overrides()

    assert response.status_code == 409
    assert response.json()["detail"]["next_step"] == "POST /api/portfolio/plan"


def test_missing_repository_item_returns_404_instead_of_500() -> None:
    client = _client_for_service(HarnessService())
    try:
        response = client.get("/api/orders/missing-order/status")
    finally:
        _clear_overrides()

    assert response.status_code == 404
    assert "missing item" in response.json()["detail"]["error"]
