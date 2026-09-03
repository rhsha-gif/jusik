from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.services.api.dependencies import (
    OPERATOR_ACTOR_ID_ENV,
    OPERATOR_SHARED_SECRET_ENV,
    get_harness_service,
    require_operator_actor,
)
from quantpilot.services.api.main import app


MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# This POST only parses the supplied policy into a response model. It does not
# access the service or repositories, so it is the sole read-only preview route.
READ_ONLY_MUTATING_ROUTE_ALLOWLIST = {
    ("POST", "/api/policies/preview"),
}


def _app_mutating_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.methods & MUTATING_METHODS
    ]


def _service_with_proposal() -> tuple[HarnessService, str]:
    service = HarnessService()
    policy = service.parse_policy()
    signals = service.run_signals()
    portfolio_plan = service.create_portfolio_plan(
        policy_id=policy.policy_id,
        signals=signals,
    )
    proposal = service.generate_order_proposals(
        portfolio_plan_id=portfolio_plan.plan_id,
    )[0]
    return service, proposal.order_plan_id


def _operator_headers(secret: str, *, actor_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {secret}",
    }
    if actor_id is not None:
        headers["X-QuantPilot-Operator-Actor"] = actor_id
    return headers


def test_all_app_mutating_routes_require_operator_actor_or_are_read_only() -> None:
    routes = _app_mutating_routes()
    allowlisted_routes: set[tuple[str, str]] = set()

    for route in routes:
        guarded = any(
            dependency.call is require_operator_actor
            for dependency in route.dependant.dependencies
        )
        for method in route.methods & MUTATING_METHODS:
            route_key = (method, route.path)
            if route_key in READ_ONLY_MUTATING_ROUTE_ALLOWLIST:
                allowlisted_routes.add(route_key)
                continue
            assert guarded, f"{method} {route.path}"

    assert allowlisted_routes == READ_ONLY_MUTATING_ROUTE_ALLOWLIST


def test_read_only_get_route_remains_open() -> None:
    service = HarnessService()
    app.dependency_overrides[get_harness_service] = lambda: service
    try:
        response = TestClient(app).get("/api/orders/proposed")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_approve_and_submit_reject_unauthenticated_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_secret = uuid4().hex
    monkeypatch.setenv(OPERATOR_SHARED_SECRET_ENV, configured_secret)
    monkeypatch.setenv(OPERATOR_ACTOR_ID_ENV, "operator-test")
    service, order_plan_id = _service_with_proposal()
    app.dependency_overrides[get_harness_service] = lambda: service
    try:
        client = TestClient(app)
        approve_response = client.post(f"/api/orders/{order_plan_id}/approve")
        submit_response = client.post(f"/api/orders/{order_plan_id}/submit")
        wrong_secret_response = client.post(
            f"/api/orders/{order_plan_id}/approve",
            headers=_operator_headers(uuid4().hex, actor_id="operator-test"),
        )
    finally:
        app.dependency_overrides.clear()

    assert approve_response.status_code == 401
    assert submit_response.status_code == 401
    assert wrong_secret_response.status_code == 403


def test_authenticated_approve_records_actor_and_submit_path_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_secret = uuid4().hex
    actor_id = "configured-operator"
    monkeypatch.setenv(OPERATOR_SHARED_SECRET_ENV, configured_secret)
    monkeypatch.setenv(OPERATOR_ACTOR_ID_ENV, actor_id)
    service, order_plan_id = _service_with_proposal()
    app.dependency_overrides[get_harness_service] = lambda: service
    headers = _operator_headers(configured_secret, actor_id="spoofed-operator")
    try:
        client = TestClient(app)
        approve_response = client.post(
            f"/api/orders/{order_plan_id}/approve",
            headers=headers,
        )
        submit_response = client.post(
            f"/api/orders/{order_plan_id}/submit",
            headers=headers,
        )
    finally:
        app.dependency_overrides.clear()

    assert approve_response.status_code == 200
    assert approve_response.json()["approved_by"] == actor_id
    approval_event = next(
        event
        for event in service.repositories.audit_logs.list()
        if event.action == "proposal_approved"
    )
    assert approval_event.user_id == actor_id
    assert submit_response.status_code == 200
    assert submit_response.json()["order_plan"]["status"] == "filled"


def test_missing_operator_actor_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_secret = uuid4().hex
    monkeypatch.setenv(OPERATOR_SHARED_SECRET_ENV, configured_secret)
    monkeypatch.delenv(OPERATOR_ACTOR_ID_ENV, raising=False)
    service, order_plan_id = _service_with_proposal()
    app.dependency_overrides[get_harness_service] = lambda: service
    try:
        response = TestClient(app).post(
            f"/api/orders/{order_plan_id}/approve",
            headers=_operator_headers(configured_secret, actor_id="spoofed-operator"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert service.repositories.order_plans.require(order_plan_id).approved_by is None


def test_short_operator_shared_secret_is_treated_as_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short_secret = "x" * 31
    monkeypatch.setenv(OPERATOR_SHARED_SECRET_ENV, short_secret)
    monkeypatch.setenv(OPERATOR_ACTOR_ID_ENV, "operator-test")
    service, order_plan_id = _service_with_proposal()
    app.dependency_overrides[get_harness_service] = lambda: service
    try:
        response = TestClient(app).post(
            f"/api/orders/{order_plan_id}/approve",
            headers=_operator_headers(short_secret),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert service.repositories.order_plans.require(order_plan_id).approved_by is None
