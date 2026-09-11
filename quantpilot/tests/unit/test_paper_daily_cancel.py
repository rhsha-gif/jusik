"""Full remaining cancellation through durable paper authority and daily evidence."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quantpilot.paper.broker import KisGateway
from quantpilot.paper.calendar import Session
from quantpilot.paper.store import Store
from quantpilot.packages.core.kis_paper import KisDailyOrdersResult
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.tests.unit.test_intraday_durable_gateway import Client, NOW


@pytest.fixture
def trial(tmp_path, request):
    quantity = getattr(request, "param", 1)
    store = Store(tmp_path / "experiment.sqlite3")
    store.control("start")
    store.put("weights", {"trend_pullback": 0.6})
    client = Client()
    # Forwarding ID is intentionally different from the branch returned by daily inquiry.
    client.order_outcome = replace(client.order_outcome, quantity=quantity,
                                   krx_forwarding_order_org_number="00950")
    def place(**kwargs):
        assert kwargs["quantity"] == quantity
        client.order_calls += 1
        return client.order_outcome
    client.place_limit_cash_order = place
    original_daily = client.get_daily_orders_and_fills
    def daily(*args, **kwargs):
        result = original_daily(*args, **kwargs)
        return replace(result, rows=tuple(replace(r, order_quantity=quantity,
            remaining_quantity=quantity - r.total_filled_quantity) for r in result.rows))
    client.get_daily_orders_and_fills = daily
    def unsupported():
        raise AssertionError("paper native cancelable inquiry must not be called")
    client.get_cancelable_orders = unsupported
    calls = []
    def cancel(**kwargs):
        assert store.get("cancel_claim:trial") is True
        assert store.orders()[0]["state"] == "cancel_unknown"
        calls.append(kwargs)
        return SimpleNamespace(message_code="40630000")
    client.cancel_paper_remaining_order = cancel
    calendar = SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)),
        current_open_session_date=lambda at: at.date(),
    )
    gateway = KisGateway(store, client, calendar, lambda: NOW)
    gateway.begin()
    assert gateway.reconcile(NOW)
    store.reserve(order_id="trial", signal=SimpleNamespace(
        symbol="005930", strategy_id="trend_pullback", version="1",
        stop=69000.0, target=72000.0, entry_atr14=1000.0,
    ), quantity=quantity, price=70000, side="buy", now=NOW, policy_version=1, reason="fixture")
    gateway.submit(store.orders()[0], Quote(symbol="005930", last=70000,
        bid=69900, ask=70000, as_of=NOW), NOW)
    assert gateway.reconcile(NOW)
    yield store, client, gateway, calls
    gateway.end()
    gateway.close()
    store.close()


def test_cancel_ack_is_not_final_and_forwarding_id_survives_daily_branch(trial):
    store, client, gateway, calls = trial
    gateway.cancel(store.orders()[0], NOW)
    assert calls == [{"forwarding_org_number": "00950", "original_order_number": "0000012345"}]
    gateway.reconcile(NOW)
    assert store.orders()[0]["state"] == "cancel_unknown"
    gateway.cancel(store.orders()[0], NOW)
    assert len(calls) == 1
    row = client.get_daily_orders_and_fills().rows[0]
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult((
        replace(row, remaining_quantity=0, cancelled=True, confirmed_cancel_quantity=1),
    ), 1)
    assert gateway.reconcile(NOW)
    assert store.orders()[0]["state"] == "cancelled"
    assert not store.positions()


def test_cancel_timeout_restart_is_query_only_and_late_fill_is_applied_once(trial):
    store, client, gateway, calls = trial
    def timeout(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("fixture")
    client.cancel_paper_remaining_order = timeout
    with pytest.raises(TimeoutError):
        gateway.cancel(store.orders()[0], NOW)
    gateway.end()
    gateway.close()
    restarted = KisGateway(store, client, gateway.calendar, lambda: NOW)
    try:
        restarted.begin()
        restarted.cancel(store.orders()[0], NOW)
        assert len(calls) == 1
        client.filled = True
        assert restarted.reconcile(NOW)
        cash = store.get("cash")
        assert store.orders()[0]["state"] == "filled"
        assert store.positions()[0]["quantity"] == 1
        restarted.reconcile(NOW)
        assert store.get("cash") == cash
        restarted.end()
    finally:
        restarted.close()


@pytest.mark.parametrize("change", [
    {"order_number": "9999999999"}, {"order_time": "100009"},
    {"order_branch_number": "99999"}, {"symbol": "000660"},
    {"side": "sell"}, {"order_price": Decimal("70100")},
    {"original_order_number": "123"}, {"order_date": "20260909"},
    {"remaining_quantity": 2},
])
def test_changed_or_invalid_daily_identity_never_cancels(trial, change):
    store, client, gateway, calls = trial
    row = replace(client.get_daily_orders_and_fills().rows[0], **change)
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult((row,), 1)
    gateway.cancel(store.orders()[0], NOW)
    assert not calls and not store.get("cancel_claim:trial")
    assert not gateway.reconcile(NOW)


def test_missing_forwarding_id_never_uses_daily_branch(trial, monkeypatch):
    store, client, gateway, calls = trial
    dispatch = gateway.kernel.load_paper_order_dispatch("trial")
    monkeypatch.setattr(gateway.kernel, "load_paper_order_dispatch", lambda _: dispatch.model_copy(
        update={"broker_forwarding_order_org_number": None}))
    gateway.cancel(store.orders()[0], NOW)
    assert not calls and not store.get("cancel_claim:trial")


@pytest.mark.parametrize("duplicate", [True, False])
def test_external_working_order_and_duplicate_rows_block_entries(trial, duplicate):
    store, client, gateway, calls = trial
    row = client.get_daily_orders_and_fills().rows[0]
    extra = row if duplicate else replace(row, order_number="9999999999")
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult((row, extra), 1)
    assert not gateway.reconcile(NOW)
    assert not calls


def test_daily_query_failure_cannot_be_treated_as_empty_account(trial):
    store, client, gateway, calls = trial
    def fail(*a, **k):
        raise TimeoutError("fixture")
    client.get_daily_orders_and_fills = fail
    with pytest.raises(Exception):
        gateway.reconcile(NOW)
    assert not gateway.entry_reconciled and not calls


@pytest.mark.parametrize("trial", [2], indirect=True)
def test_partial_fill_then_confirmed_remaining_cancel_preserves_fill(trial):
    store, client, gateway, calls = trial
    client.filled = True
    assert gateway.reconcile(NOW)
    assert store.orders()[0]["state"] == "partially_filled"
    gateway.cancel(store.orders()[0], NOW)
    row = client.get_daily_orders_and_fills().rows[0]
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult((
        replace(row, cancelled=True, remaining_quantity=0, confirmed_cancel_quantity=1),
    ), 1)
    assert gateway.reconcile(NOW)
    assert store.orders()[0]["state"] == "cancelled"
    assert store.orders()[0]["filled"] == store.positions()[0]["quantity"] == 1
    assert len(calls) == 1


def test_cancel_flag_without_confirmed_quantity_cannot_close_order(trial):
    store, client, gateway, calls = trial
    gateway.cancel(store.orders()[0], NOW)
    row = client.get_daily_orders_and_fills().rows[0]
    client.get_daily_orders_and_fills = lambda *a, **k: KisDailyOrdersResult((
        replace(row, cancelled=True, remaining_quantity=0, confirmed_cancel_quantity=0),
    ), 1)
    assert not gateway.reconcile(NOW)
    assert store.orders()[0]["state"] == "cancel_unknown"
