from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from quantpilot.paper.auth import RefreshingClient


def test_token_renewal_does_not_retry_failed_order():
    now = [datetime(2026, 9, 10, tzinfo=timezone.utc)]
    calls = {"auth": 0, "order": 0}

    def token():
        calls["auth"] += 1
        return SimpleNamespace(access_token="fixture", expires_in_seconds=100)

    def order():
        calls["order"] += 1
        raise TimeoutError()

    config = SimpleNamespace(with_access_token=lambda value: None)
    factory = lambda cfg: SimpleNamespace(request_access_token=token, submit=order)
    client = RefreshingClient(config, factory, lambda: now[0])
    with pytest.raises(TimeoutError):
        client.submit()
    assert calls == {"auth": 1, "order": 1}
    now[0] += timedelta(seconds=91)
    with pytest.raises(TimeoutError):
        client.submit()
    assert calls == {"auth": 2, "order": 2}
