from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quantpilot.packages.core.operator.status_snapshot import (
    unavailable_professional_operator_status,
)
from quantpilot.services.api.dependencies import (
    get_professional_operator_status_snapshot,
)
from quantpilot.services.api.routers.operator import router


NOW = datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_professional_operator_status_snapshot] = (
        lambda: unavailable_professional_operator_status(
            observed_at=NOW,
            reason_code="paper_state_db_not_configured",
        )
    )
    return TestClient(app)


def test_professional_status_route_is_typed_read_only_and_secret_free() -> None:
    response = _client().get("/api/operator/professional-status")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["overall_status"] == "unavailable"
    assert body["reason_code"] == "paper_state_db_not_configured"
    assert body["live_trading_enabled"] is False
    assert set(body) == {
        "available",
        "overall_status",
        "reason_code",
        "source",
        "observed_at",
        "live_trading_enabled",
        "schema_version",
        "freshness",
        "safety",
        "positions",
        "strategy_health",
        "rebalance",
        "reconciliation",
    }

    rendered = json.dumps(body, sort_keys=True)
    for forbidden in (
        "account_scope_fingerprint",
        "idempotency_key",
        "request_fingerprint",
        "order_plan_payload",
        "app_secret",
        "access_token",
    ):
        assert forbidden not in rendered


def test_main_app_registers_default_unavailable_professional_status(
    monkeypatch,
) -> None:
    monkeypatch.delenv("KIS_PAPER_STATE_DB", raising=False)
    from quantpilot.services.api.main import app

    response = TestClient(app).get("/api/operator/professional-status")

    assert response.status_code == 200
    body = response.json()
    assert {
        key: body[key]
        for key in (
            "available",
            "overall_status",
            "reason_code",
            "live_trading_enabled",
        )
    } == {
        "available": False,
        "overall_status": "unavailable",
        "reason_code": "paper_state_path_unset",
        "live_trading_enabled": False,
    }
