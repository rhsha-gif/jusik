from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantpilot.jobs import run_smoke
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.db.repositories import RepositoryRegistry
from quantpilot.services.api import dependencies as api_dependencies
from quantpilot.services.api.dependencies import get_harness_service, require_operator_actor
from quantpilot.services.api.main import app


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        (
            "QUANTPILOT_RUNTIME_ROLE",
            "paper-session",
            "generic_runtime_rejects_paper_session_role",
        ),
        ("KIS_PAPER_APP_KEY", "fake-paper-key", "generic_runtime_rejects_paper_arming_environment"),
        ("BROKER_MODE", "paper", "generic_runtime_rejects_paper_arming_environment"),
        (
            "FULLY_AUTOMATED_OPERATOR_ENABLED",
            "true",
            "generic_runtime_rejects_paper_arming_environment",
        ),
    ],
)
def test_generic_api_startup_rejects_paper_arming_without_runtime_role(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    error: str,
) -> None:
    monkeypatch.delenv("QUANTPILOT_RUNTIME_ROLE", raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(
        RuntimeError,
        match=f"^{error}$",
    ):
        with TestClient(app):
            pass


def test_generic_smoke_rejects_paper_arming_without_runtime_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QUANTPILOT_RUNTIME_ROLE", raising=False)
    monkeypatch.setenv("BROKER_MODE", "paper")

    with pytest.raises(
        RuntimeError,
        match="^generic_runtime_rejects_paper_arming_environment$",
    ):
        run_smoke.main()


def _client_for_service(service: HarnessService) -> TestClient:
    app.dependency_overrides[get_harness_service] = lambda: service
    app.dependency_overrides[require_operator_actor] = lambda: "test-operator"
    return TestClient(app)


def _authenticated_client() -> TestClient:
    app.dependency_overrides[require_operator_actor] = lambda: "test-operator"
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


def test_run_smoke_preserves_attached_registry() -> None:
    shared_repositories = RepositoryRegistry()
    shared_service = HarnessService(shared_repositories)
    existing_policy = shared_service.parse_policy(user_id="existing-user")
    policies_before = shared_repositories.policies.list()
    audit_logs_before = shared_repositories.audit_logs.list()

    summary = shared_service.run_smoke()

    assert summary["live_trading_enabled"] is False
    assert shared_repositories.policies.list() == policies_before == [existing_policy]
    assert shared_repositories.audit_logs.list() == audit_logs_before


def test_api_smoke_route_passes() -> None:
    try:
        response = _authenticated_client().post("/api/harness/run-smoke")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["live_trading_enabled"] is False
    assert body["broker"] == "mock"
    assert all(order["status"] == "filled" for order in body["orders"])


def test_api_smoke_route_preserves_shared_orders_and_audit_logs(monkeypatch) -> None:
    monkeypatch.setenv("DATA_MODE", "fixture")
    shared_repositories = RepositoryRegistry()
    monkeypatch.setattr(api_dependencies, "repositories", shared_repositories)
    monkeypatch.setattr(api_dependencies, "_harness_service", None)
    monkeypatch.setattr(api_dependencies, "_operator_service", None)
    monkeypatch.setattr(api_dependencies, "_service_config_key", None)
    shared_service = HarnessService(shared_repositories)
    shared_service.run_smoke()
    existing_orders = shared_repositories.order_plans.list()
    existing_audit_logs = shared_repositories.audit_logs.list()

    try:
        response = _authenticated_client().post("/api/harness/run-smoke")
        body = response.json()
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert set(body) == {
        "policy_id",
        "broker",
        "execution_mode",
        "signals",
        "portfolio_plan_id",
        "orders",
        "fills",
        "audit_events",
        "report_id",
        "live_trading_enabled",
    }
    assert shared_repositories.order_plans.list() == existing_orders
    assert shared_repositories.audit_logs.list() == existing_audit_logs


def test_smoke_job_uses_local_historical_env(monkeypatch, capsys) -> None:
    data_dir = Path(__file__).resolve().parents[1] / "fixtures" / "local_data"
    monkeypatch.setenv("DATA_MODE", "local_historical")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(data_dir))

    exit_code = run_smoke.main()

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["signals"] == 2
    assert output["live_trading_enabled"] is False


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
