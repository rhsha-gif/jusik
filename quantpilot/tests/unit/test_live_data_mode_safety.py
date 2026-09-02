from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from quantpilot.packages.core.data.mode import is_data_mode_safe
from quantpilot.packages.core.schemas import DataMode
from quantpilot.services.api.main import app


NEW_LIVE_DATA_MODES = (
    DataMode.live_trading_candidate,
    DataMode.live_canary,
    DataMode.live_scaled,
)


@pytest.mark.parametrize("mode", NEW_LIVE_DATA_MODES)
def test_new_live_data_modes_are_unsafe(mode: DataMode) -> None:
    assert not is_data_mode_safe(mode)


@pytest.mark.parametrize("mode", NEW_LIVE_DATA_MODES)
def test_health_blocks_new_live_data_modes(
    monkeypatch: pytest.MonkeyPatch,
    mode: DataMode,
) -> None:
    monkeypatch.setenv("DATA_MODE", mode.value)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("MARKET_ORDERS_ENABLED", "false")
    monkeypatch.setenv("GUARDED_AUTOPILOT_ENABLED", "false")
    monkeypatch.setenv("FULLY_AUTOMATED_OPERATOR_ENABLED", "false")
    monkeypatch.setenv("BROKER_MODE", "mock")

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "blocked",
        "live_trading_enabled": False,
        "market_orders_enabled": False,
        "guarded_autopilot_enabled": False,
        "fully_automated_operator_enabled": False,
        "default_broker": "mock",
        "data_mode": mode.value,
        "data_mode_safe": False,
        "data_mode_error": f"DATA_MODE {mode.value!r} is not safe for this harness",
    }
