from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.services.api.dependencies import get_harness_service, require_operator_actor
from quantpilot.services.api.main import app


def test_api_smoke_includes_default_operator_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "BROKER_MODE",
        "DATA_MODE",
        "FULLY_AUTOMATED_OPERATOR_ENABLED",
        "GUARDED_AUTOPILOT_ENABLED",
        "LIVE_TRADING_ENABLED",
        "MARKET_ORDERS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    app.dependency_overrides[get_harness_service] = lambda: HarnessService()
    app.dependency_overrides[require_operator_actor] = lambda: "test-operator"

    try:
        response = TestClient(app).post("/api/harness/run-smoke")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    operator = response.json()["operator"]
    assert operator["status"] == "blocked"
    assert operator["fallback"] == "level5_flag_disabled"
    assert operator["live_trading_enabled"] is False
