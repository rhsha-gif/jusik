from __future__ import annotations

import pytest

from quantpilot.jobs.check_kis_paper_connection import connection_config, check_connection
from quantpilot.packages.core.kis_paper import KisPaperConfigurationError


def environment(**overrides):
    return dict(KIS_PAPER_APP_KEY="fixture-key", KIS_PAPER_APP_SECRET="fixture-secret",
                KIS_PAPER_ACCOUNT_NUMBER="12345678", **overrides)


@pytest.mark.parametrize("account", ["12345678", "1234567801", "12345678-01"])
def test_stock_account_formats(account):
    env = environment()
    env["KIS_PAPER_ACCOUNT_NUMBER"] = account
    config = connection_config(env)
    assert config.account_number == "12345678"
    assert config.product_code == "01"
    assert config.access_token == ""


def test_conflicting_product_is_rejected():
    env = environment(KIS_PAPER_PRODUCT_CODE="03")
    env["KIS_PAPER_ACCOUNT_NUMBER"] = "12345678-01"
    with pytest.raises(KisPaperConfigurationError):
        connection_config(env)


def test_invalid_account_stops_before_network():
    env = environment()
    env["KIS_PAPER_ACCOUNT_NUMBER"] = "invalid"
    result = check_connection(env)
    assert result["status"] == "failed"
    assert result["stage"] == "configuration"
    assert "invalid" not in str(result)


def test_auto_auth_and_queries_without_orders(monkeypatch):
    import quantpilot.jobs.check_kis_paper_connection as job
    from types import SimpleNamespace

    calls = []

    class Client:
        def __init__(self, config, **kwargs):
            self.config = config

        def request_access_token(self):
            calls.append("token")
            return SimpleNamespace(access_token="fixture-token")

        def get_current_price(self, symbol):
            assert self.config.access_token == "fixture-token"
            calls.append("price")

        def get_balance(self):
            calls.append("balance")

    monkeypatch.setattr(job, "KisPaperClient", Client)
    result = check_connection(environment())
    assert result["status"] == "connected"
    assert calls == ["token", "price", "balance"]
    assert "fixture" not in str(result)


def test_untrusted_error_is_not_printed(monkeypatch):
    import quantpilot.jobs.check_kis_paper_connection as job

    class Client:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("secret-response")

    monkeypatch.setattr(job, "KisPaperClient", Client)
    result = check_connection(environment())
    assert result["status"] == "failed"
    assert "secret-response" not in str(result)


@pytest.mark.parametrize("method,path", [
    ("POST", "/uapi/domestic-stock/v1/trading/order-cash"),
    ("POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl"),
    ("GET", "/oauth2/tokenP"),
])
def test_transport_rejects_unauthorized_requests(method, path):
    from quantpilot.jobs.check_kis_paper_connection import ConnectionTransport
    from quantpilot.packages.core.kis_paper import KIS_PAPER_BASE_URL
    with pytest.raises(KisPaperConfigurationError):
        ConnectionTransport().request_json(method, KIS_PAPER_BASE_URL + path)


def test_transport_rejects_production():
    from quantpilot.jobs.check_kis_paper_connection import ConnectionTransport
    with pytest.raises(KisPaperConfigurationError):
        ConnectionTransport().request_json("POST", "https://openapi.koreainvestment.com:9443/oauth2/tokenP")


def test_existing_token_and_account_mismatch(monkeypatch):
    import quantpilot.jobs.check_kis_paper_connection as job
    from quantpilot.packages.core.kis_paper import KisPaperBusinessError

    class Client:
        def __init__(self, config, **kwargs):
            assert config.access_token == "fixture-token"

        def get_current_price(self, symbol):
            pass

        def get_balance(self):
            raise KisPaperBusinessError("rejected (code=90070000)")

    monkeypatch.setattr(job, "KisPaperClient", Client)
    result = check_connection(environment(KIS_PAPER_ACCESS_TOKEN="fixture-token"))
    assert result["stage"] == "balance_query"
    assert result["status"] == "failed"
    assert result["reason"] == "paper_account_user_mismatch"
