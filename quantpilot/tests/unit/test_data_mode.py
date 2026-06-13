from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from quantpilot.packages.core.data.providers import CsvMarketDataProvider, CsvSecurityProvider
from quantpilot.packages.core.data.mode import DataModeConfigError, is_data_mode_safe, resolve_data_mode
from quantpilot.packages.core.schemas import DataMode
from quantpilot.services.api.dependencies import get_harness_service
from quantpilot.services.api.main import app

LOCAL_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "local_data")


def test_default_data_mode_is_fixture() -> None:
    os.environ.pop("DATA_MODE", None)
    assert resolve_data_mode() == DataMode.fixture


def test_live_trading_mode_is_not_safe() -> None:
    assert not is_data_mode_safe(DataMode.live_trading)


def test_pre_live_modes_are_safe() -> None:
    safe_modes = [
        DataMode.fixture,
        DataMode.local_historical,
        DataMode.external_historical,
        DataMode.realtime_market_data,
        DataMode.paper_trading,
    ]
    for mode in safe_modes:
        assert is_data_mode_safe(mode), f"{mode.value} should be safe in the pre-harness"


def test_unknown_env_var_fails_closed() -> None:
    os.environ["DATA_MODE"] = "not_a_valid_mode"
    try:
        with pytest.raises(DataModeConfigError, match="Unsupported DATA_MODE"):
            resolve_data_mode()
    finally:
        del os.environ["DATA_MODE"]


def test_health_endpoint_includes_data_mode() -> None:
    os.environ.pop("DATA_MODE", None)
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["data_mode"] == "fixture"
    assert data["data_mode_safe"] is True


def test_health_endpoint_backward_compat() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "live_trading_enabled" in data
    assert "default_broker" in data
    assert data["live_trading_enabled"] is False
    assert data["status"] == "ok"
    assert data["default_broker"] == "mock"


def test_live_trading_env_is_surfaced_as_blocked() -> None:
    os.environ["DATA_MODE"] = "live_trading"
    try:
        client = TestClient(app)
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["data_mode"] == "live_trading"
        assert data["data_mode_safe"] is False
    finally:
        del os.environ["DATA_MODE"]


def test_invalid_data_mode_is_surfaced_as_blocked() -> None:
    os.environ["DATA_MODE"] = "not_a_valid_mode"
    try:
        client = TestClient(app)
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "blocked"
        assert data["data_mode"] == "not_a_valid_mode"
        assert data["data_mode_safe"] is False
        assert "Unsupported DATA_MODE" in data["data_mode_error"]
    finally:
        del os.environ["DATA_MODE"]


def test_api_dependency_uses_local_historical_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_MODE", "local_historical")
    monkeypatch.setenv("LOCAL_DATA_DIR", LOCAL_DATA_DIR)

    service = get_harness_service()

    assert isinstance(service.security_provider, CsvSecurityProvider)
    assert isinstance(service.market_data_provider, CsvMarketDataProvider)


def test_api_dependency_blocks_invalid_data_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_MODE", "not_a_valid_mode")

    with pytest.raises(HTTPException) as exc_info:
        get_harness_service()
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "harness data mode configuration invalid"
