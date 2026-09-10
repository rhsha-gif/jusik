"""Exercise the real KIS client authority branch with an entirely fake transport."""

from datetime import timedelta
from types import SimpleNamespace

from quantpilot.paper.auth import RefreshingClient
from quantpilot.paper.broker import KisGateway
from quantpilot.paper.calendar import Session
from quantpilot.paper.store import Store
from quantpilot.packages.core.kis_paper import (
    KisPaperClient,
    KisPaperConfig,
    KisHttpResponse,
)
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.tests.unit.test_intraday_durable_gateway import Client, NOW


def test_refreshed_real_client_reaches_the_closed_order_transport(tmp_path):
    calls = []
    fixture = Client()

    class Transport:
        def request_json(self, method, url, **kwargs):
            calls.append((method, url))
            if url.endswith("/oauth2/tokenP"):
                return KisHttpResponse(
                    200,
                    {
                        "access_token": "fixture",
                        "token_type": "Bearer",
                        "expires_in": 86400,
                    },
                )
            assert method == "POST" and url.endswith(
                "/uapi/domestic-stock/v1/trading/order-cash"
            )
            return KisHttpResponse(
                200,
                {
                    "rt_cd": "0",
                    "msg_cd": "APBK0013",
                    "output": {
                        "KRX_FWDG_ORD_ORGNO": "91234",
                        "ODNO": "0000012345",
                        "ORD_TMD": "100002",
                    },
                },
            )

    def factory(config):
        client = KisPaperClient(config, transport=Transport())
        client.get_balance = fixture.get_balance
        client.get_buying_power = fixture.get_buying_power
        client.get_daily_orders_and_fills = fixture.get_daily_orders_and_fills
        client.get_cancelable_orders = fixture.get_cancelable_orders
        return client

    config = KisPaperConfig(
        app_key="fixture", app_secret="fixture", account_number="12345678"
    )
    proxy = RefreshingClient(config, factory, lambda: NOW)
    store = Store(tmp_path / "s")
    store.control("start")
    store.put("weights", {"trend_pullback": 0.6})
    calendar = SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)),
        current_open_session_date=lambda at: at.date(),
    )
    gateway = KisGateway(store, proxy, calendar, lambda: NOW)
    gateway.begin()
    gateway.reconcile(NOW)
    signal = SimpleNamespace(
        symbol="005930",
        strategy_id="trend_pullback",
        version="1",
        stop=69000.0,
        target=72000.0,
        entry_atr14=1000.0,
    )
    store.reserve(
        order_id="real-client-fake-transport",
        signal=signal,
        quantity=1,
        price=70000,
        side="buy",
        now=NOW,
        policy_version=1,
        reason="fixture",
    )
    gateway.submit(
        store.orders()[0],
        Quote(symbol="005930", last=70000, bid=69900, ask=70000, as_of=NOW),
        NOW,
    )
    assert len([url for method, url in calls if url.endswith("order-cash")]) == 1
    dispatch = gateway.kernel.load_paper_order_dispatch("real-client-fake-transport")
    assert dispatch.status == "accepted" and dispatch.attempt_count == 1
    gateway.end()
    gateway.close()
    store.close()
