from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from quantpilot.services.api.main import allowed_cors_origins, app


DEPLOYED_PREVIEW_ORIGIN = "https://web-h90tnddev-shyeon-s-projects.vercel.app"


def test_deployed_private_preview_origin_can_call_local_api() -> None:
    client = TestClient(app)

    response = client.options(
        "/api/health",
        headers={
            "Origin": DEPLOYED_PREVIEW_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == DEPLOYED_PREVIEW_ORIGIN


def test_unknown_origin_is_not_allowed() -> None:
    client = TestClient(app)

    response = client.options(
        "/api/health",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_extra_cors_origins_must_be_exact_env_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "QUANTPILOT_API_ALLOWED_ORIGINS",
        "https://personal-preview.vercel.app/, https://another-private.example",
    )

    origins = allowed_cors_origins()

    assert "https://personal-preview.vercel.app" in origins
    assert "https://another-private.example" in origins
    assert "*" not in origins
