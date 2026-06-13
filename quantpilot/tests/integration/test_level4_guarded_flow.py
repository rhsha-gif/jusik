from __future__ import annotations

from fastapi.testclient import TestClient

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import BrokerMode, ExecutionMode, UserPolicy
from quantpilot.services.api.dependencies import get_harness_service
from quantpilot.services.api.main import app


def _client_for_service(service: HarnessService) -> TestClient:
    app.dependency_overrides[get_harness_service] = lambda: service
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def test_guarded_run_once_uses_level3_proposals_and_is_disabled_by_default() -> None:
    service = HarnessService()
    policy = service.parse_policy()

    result = service.run_guarded_autopilot_once(policy_id=policy.policy_id)

    assert result["submitted"] == []
    assert result["blocked"]
    assert result["blocked"][0]["reason"] == "guarded_autopilot_enabled"
    assert service.repositories.order_plans.list()


def test_autopilot_status_and_kill_switch_api_blocks_subsequent_guarded_run() -> None:
    service = HarnessService()
    policy = UserPolicy(
        execution_mode=ExecutionMode.guarded_autopilot,
        broker=BrokerMode.mock,
        authority_level=4,
        guarded_autopilot_enabled=True,
    )
    service.repositories.policies.add(policy)
    client = _client_for_service(service)
    try:
        status_before = client.get("/api/autopilot/status")
        killed = client.post("/api/autopilot/kill-switch", json={"policy_id": policy.policy_id, "reason": "test"})
        run_after = client.post("/api/autopilot/guarded/run-once", json={"policy_id": policy.policy_id})
        status_after = client.get("/api/autopilot/status")
    finally:
        _clear_overrides()

    assert status_before.status_code == 200
    assert status_before.json()["live_trading_enabled"] is False
    assert killed.status_code == 200
    assert killed.json()["kill_switch_engaged"] is True
    assert run_after.status_code == 200
    assert run_after.json()["submitted"] == []
    assert status_after.json()["kill_switch_engaged"] is True
    assert status_after.json()["last_blocked_reason"] == "kill_switch_not_engaged"
