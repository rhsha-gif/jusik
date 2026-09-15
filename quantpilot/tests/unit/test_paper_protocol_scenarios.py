"""Loss diagnosis 2026-09-15, priority 1: fill recognition, protection and close chained
through the real runtime cycle, the durable gateway and a fake broker client.

The chain is: entry accepted -> stale-limit cancel whose POST times out after the wire
-> the broker fills the original order anyway -> the fill is recognised once and the
position is protected with exactly one sell POST -> the unfilled sell survives the
session close as a quarantined holding -> a restarted process re-sends nothing.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import json
import sys
import types

from quantpilot.paper.broker import KisGateway
from quantpilot.paper.calendar import Session
from quantpilot.paper.runtime import Runtime
from quantpilot.paper.store import Store
from quantpilot.packages.core.kis_paper import (
    KisDailyOrderFill,
    KisDailyOrdersResult,
    KisPaperCancelOutcomeUnknown,
)
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.tests.unit.test_intraday_durable_gateway import Client, NOW

CLOSES = NOW + timedelta(hours=5)


def audits(store, kind):
    return [json.loads(r[1]) for r in store.db.execute("SELECT kind, payload FROM audit ORDER BY id") if r[0] == kind]


def test_cancel_unknown_then_late_fill_is_protected_once_and_survives_close_and_restart(tmp_path, monkeypatch):
    stub = types.ModuleType("quantpilot.paper.strategy")
    stub.evaluate_strategies = lambda *a: []
    stub.allocate_weights = lambda *a, **k: {}
    stub.select_signals = lambda *a: []
    monkeypatch.setitem(sys.modules, "quantpilot.paper.strategy", stub)

    store = Store(tmp_path / "experiment.sqlite3")
    store.control("start")
    store.put("weights", {"trend_pullback": 0.6})
    now = [NOW]
    client = Client()
    client.order_outcome = replace(client.order_outcome, krx_forwarding_order_org_number="00950")
    posts = []

    def place(**kwargs):
        posts.append(kwargs)
        client.order_calls += 1
        return replace(
            client.order_outcome,
            side=kwargs["side"], quantity=kwargs["quantity"], limit_price=kwargs["limit_price"],
            order_number="0000012345" if kwargs["side"] == "buy" else "0000012346",
        )

    client.place_limit_cash_order = place
    original_daily = client.get_daily_orders_and_fills
    sell_row = [None]

    def daily(*args, **kwargs):
        rows = list(original_daily(*args, **kwargs).rows)
        if sell_row[0] is not None:
            rows.append(sell_row[0])
        return KisDailyOrdersResult(tuple(rows), 1)

    client.get_daily_orders_and_fills = daily

    def unsupported():
        raise AssertionError("paper native cancelable inquiry must not be called")

    client.get_cancelable_orders = unsupported
    cancels = []

    def cancel_after_wire(**kwargs):
        cancels.append(kwargs)
        raise KisPaperCancelOutcomeUnknown("fixture timeout after the wire")

    client.cancel_paper_remaining_order = cancel_after_wire
    calendar = SimpleNamespace(
        session=lambda at: (Session(NOW - timedelta(hours=1), CLOSES) if at < CLOSES + timedelta(hours=1)
                            else Session(CLOSES + timedelta(hours=1), CLOSES + timedelta(hours=7))),
        current_open_session_date=lambda at: at.date(),
    )
    price = [70000]
    market = SimpleNamespace(
        quotes=lambda symbols: {s: Quote(symbol=s, last=price[0], bid=price[0], ask=price[0] + 100, as_of=now[0])
                                for s in symbols},
    )
    gateway = KisGateway(store, client, calendar, lambda: now[0])
    runtime = Runtime(store, market, gateway, calendar, lambda: now[0], background_data=True)

    # 1. The entry is accepted by the broker but not filled.
    gateway.begin()
    assert gateway.reconcile(now[0])
    store.reserve(
        order_id="entry",
        signal=SimpleNamespace(symbol="005930", strategy_id="trend_pullback", version="1",
                               stop=69000.0, target=72000.0, entry_atr14=1000.0),
        quantity=1, price=70000, side="buy", now=now[0], policy_version=1, reason="fixture",
    )
    gateway.submit(store.orders()[0], market.quotes(["005930"])["005930"], now[0])
    gateway.end()
    assert len(posts) == 1 and posts[0]["side"] == "buy"

    # 2. Sixty seconds later the entry limit is stale; the cancel POST times out after
    #    entering the wire. The claim is kept and never re-POSTed by later cycles.
    now[0] = NOW + timedelta(seconds=61)
    assert runtime.cycle()["status"] == "protecting"
    assert store.orders()[0]["state"] == "cancel_unknown" and store.get("cancel_claim:entry") is True
    assert len(cancels) == 1
    assert [a["order_id"] for a in audits(store, "cancel_failed")] == ["entry"]
    now[0] += timedelta(seconds=10)
    runtime.cycle()
    assert len(cancels) == 1 and len(posts) == 1 and not store.positions()

    # 3. The broker reports the original order filled after all: recognised exactly once.
    client.filled = True
    now[0] += timedelta(seconds=10)
    recognised_at = now[0]
    runtime.cycle()
    assert store.orders()[0]["state"] == "filled"
    [position] = store.positions()
    assert position["quantity"] == 1 and position["stop"] == 69000.0
    assert store.get("timeline:entry")["fill_recognized_at"] == recognised_at.isoformat()
    cash_after_fill = store.get("cash")
    now[0] += timedelta(seconds=10)
    runtime.cycle()
    assert store.get("cash") == cash_after_fill and store.positions()[0]["quantity"] == 1

    # 4. The bid breaks the stop: exactly one protective sell POST, linked to the first
    #    observation of the condition, and later cycles never duplicate it.
    price[0] = 68000
    now[0] += timedelta(seconds=10)
    stop_seen_at = now[0]
    runtime.cycle()
    sells = [o for o in store.orders() if o["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["reason"] == "stop" and sells[0]["state"] == "submitted"
    assert len(posts) == 2 and posts[1]["side"] == "sell" and posts[1]["quantity"] == 1
    exit_timeline = store.get("timeline:" + sells[0]["id"])
    assert exit_timeline["condition_first_observed_at"] == exit_timeline["decision_at"] == stop_seen_at.isoformat()
    assert exit_timeline["send_start_at"] == exit_timeline["response_at"] == stop_seen_at.isoformat()
    assert exit_timeline["broker_order_reference"] == "0000012346"
    sell_row[0] = KisDailyOrderFill(
        "0000012346", "", "91234", "20260910", "100001", "005930", "fixture", "sell", 1,
        Decimal(str(int(posts[1]["limit_price"]))), 0, Decimal(0), 1, 0, False, 0, Decimal(0),
    )
    for _ in range(3):
        now[0] += timedelta(seconds=10)
        runtime.cycle()
    assert len(posts) == 2 and len([o for o in store.orders() if o["side"] == "sell"]) == 1
    assert store.positions()[0]["quantity"] == 1
    assert audits(store, "protective_sell_reissued") == []

    # 5. The sell is still unfilled at the close: the holding is quarantined, the open
    #    order and the exposure stay visible, nothing is sent.
    now[0] = CLOSES + timedelta(seconds=1)
    result = runtime.cycle()
    assert result["status"] == "postclose" and result["quarantined"] == 1
    assert store.positions()[0]["quarantined"] == 1
    assert len(store.orders(True)) == 1 and len(posts) == 2
    assert store.get("control") == "paused"

    # 6. A restarted process (new gateway, same durable journal) re-sends nothing: the
    #    stale sell is cancelled once more (its POST is again unknown), the quarantined
    #    holding is still attributed and no second protective sell is created.
    gateway.close()
    restarted = KisGateway(store, client, calendar, lambda: now[0])
    runtime = Runtime(store, market, restarted, calendar, lambda: now[0], background_data=True)
    try:
        now[0] = CLOSES + timedelta(hours=2)
        result = runtime.cycle()
        assert result["status"] == "protecting"
        assert len(posts) == 2 and len(cancels) == 2
        assert cancels[1]["original_order_number"] == "0000012346"
        [sell] = [o for o in store.orders() if o["side"] == "sell"]
        assert sell["state"] == "cancel_unknown"
        assert store.positions()[0]["quantity"] == 1 and store.positions()[0]["quarantined"] == 1
        assert store.get("cash") == cash_after_fill
        now[0] += timedelta(seconds=10)
        runtime.cycle()
        assert len(posts) == 2 and len(cancels) == 2
    finally:
        restarted.close()
        store.close()
