from __future__ import annotations

from typing import Any

import pytest

from quantpilot.packages.core.data.kis_historical import (
    KIS_TOKEN_ENDPOINT,
    request_access_token,
    request_access_token_from_env,
)
from quantpilot.packages.core.data.providers import ProviderError


class FakePostTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout_seconds})
        return self.response


def test_token_request_parses_response_and_targets_tokenp() -> None:
    transport = FakePostTransport(
        {"access_token": "tok_abc", "token_type": "Bearer", "expires_in": 86400}
    )

    token = request_access_token(
        app_key="key", app_secret="secret", base_url="https://example.test:9443", transport=transport
    )

    assert token.access_token == "tok_abc"
    assert token.token_type == "Bearer"
    assert token.expires_in_seconds == 86400
    call = transport.calls[0]
    assert call["url"] == f"https://example.test:9443{KIS_TOKEN_ENDPOINT}"
    assert call["body"] == {"grant_type": "client_credentials", "appkey": "key", "appsecret": "secret"}


def test_token_request_fails_closed_without_access_token() -> None:
    transport = FakePostTransport({"msg1": "invalid appkey"})

    with pytest.raises(ProviderError, match="invalid appkey"):
        request_access_token(app_key="key", app_secret="secret", transport=transport)


def test_token_request_requires_credentials() -> None:
    transport = FakePostTransport({})

    with pytest.raises(ProviderError, match="app key"):
        request_access_token(app_key="  ", app_secret="secret", transport=transport)
    with pytest.raises(ProviderError, match="app secret"):
        request_access_token(app_key="key", app_secret="  ", transport=transport)
    assert transport.calls == []


def test_token_request_from_env_uses_env_names_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "env-key")
    monkeypatch.setenv("KIS_APP_SECRET", "env-secret")
    monkeypatch.setenv("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443")
    transport = FakePostTransport({"access_token": "tok_env", "expires_in": "86400"})

    token = request_access_token_from_env(transport=transport)

    assert token.access_token == "tok_env"
    assert token.expires_in_seconds == 86400
    assert transport.calls[0]["url"].startswith("https://openapivts.koreainvestment.com:29443")
