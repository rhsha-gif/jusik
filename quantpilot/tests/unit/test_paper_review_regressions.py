from datetime import timedelta
from types import SimpleNamespace
import pytest
from quantpilot.paper.store import Store
from quantpilot.paper.broker import KisGateway
from quantpilot.tests.unit.test_intraday_durable_gateway import Client, NOW


def test_pause_preserves_pending_flatten_and_resume_stays_blocked(tmp_path):
    store = Store(tmp_path / "s")
    store.control("start")
    store.control("flatten")
    store.control("pause")
    assert store.get("control") == "flattening"
    with pytest.raises(ValueError, match="flatten_in_progress"):
        store.control("resume")
    store.close()


def test_failed_begin_releases_lease(tmp_path, monkeypatch):
    store = Store(tmp_path / "s")
    gateway = KisGateway(store, Client(), None, lambda: NOW)

    def fail(**kwargs):
        raise RuntimeError("fixture")

    monkeypatch.setattr(
        "quantpilot.paper.broker.DurablePaperSubmissionCoordinator", fail
    )
    with pytest.raises(RuntimeError):
        gateway.begin()
    assert gateway.session is None
    assert all(
        s.status == "closed" for s in gateway.kernel.list_paper_execution_sessions()
    )
    gateway.close()
    store.close()


def test_exclusive_restart_reclaims_orphan_before_lease_expiry(tmp_path):
    store = Store(tmp_path / "s")
    gateway = KisGateway(store, Client(), None, lambda: NOW + timedelta(seconds=1))
    gateway.kernel.start_paper_execution_session(
        started_at=NOW, lease_expires_at=NOW + timedelta(minutes=5)
    )
    gateway.recover_exclusive_owner()
    gateway.begin()
    assert gateway.session.status == "active"
    gateway.end()
    gateway.close()
    store.close()


def test_cancel_fill_race_is_query_only(tmp_path):
    store = Store(tmp_path / "s")
    client = Client()
    client.get_daily_orders_and_fills = lambda *args, **kwargs: SimpleNamespace(rows=[])
    gateway = KisGateway(store, client, None, lambda: NOW)
    # Dispatch load is the only seam; zero rows must never reach POST.
    gateway.kernel.load_paper_order_dispatch = lambda _: SimpleNamespace(
        status="accepted"
    )
    gateway.cancel({"id": "gone"}, NOW)
    assert not store.get("cancel_claim:gone")
    assert client.order_calls == 0
    gateway.close()
    store.close()


def test_unrelated_holding_blocks_entries_but_preserves_known_stop(tmp_path):
    from dataclasses import replace
    from quantpilot.paper.runtime import Runtime
    from quantpilot.paper.calendar import Session
    from quantpilot.packages.core.marketdata.types import Quote

    store = Store(tmp_path / "s")
    store.control("start")
    signal = SimpleNamespace(
        symbol="005930",
        strategy_id="trend_pullback",
        version="1",
        stop=69000.0,
        target=72000.0,
    )
    store.reserve(
        order_id="entry",
        signal=signal,
        quantity=1,
        price=70000,
        side="buy",
        now=NOW,
        policy_version=1,
        reason="fixture",
    )
    store.update_order("entry", "filled", 1, 70000, NOW)
    client = Client()
    client.filled = True
    original = client.get_balance()
    client.get_balance = lambda **kwargs: replace(
        original,
        positions=(
            *original.positions,
            replace(original.positions[0], symbol="000660"),
        ),
    )
    calendar = SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)),
        current_open_session_date=lambda at: at.date(),
    )
    gateway = KisGateway(store, client, calendar, lambda: NOW)
    submitted = []
    gateway.submit = lambda order, *args: submitted.append(order)
    quote = Quote(symbol="005930", last=60000, bid=60000, ask=60100, as_of=NOW)
    market = SimpleNamespace(quotes=lambda symbols: {"005930": quote})
    result = Runtime(store, market, gateway, calendar, lambda: NOW).cycle()
    assert result["new_entries"] is False
    assert (
        len(submitted) == 1
        and submitted[0]["side"] == "sell"
        and submitted[0]["reason"] == "stop"
    )
    gateway.close()
    store.close()


def test_report_includes_trial_strategy_realized_pnl(tmp_path):
    from quantpilot.paper.reporting import snapshot

    store = Store(tmp_path / "s")
    store.control("start")
    signal = SimpleNamespace(
        symbol="005930",
        strategy_id="lab_fixture",
        version="hash",
        stop=9900.0,
        target=10400.0,
    )
    for side, price in (("buy", 10000), ("sell", 10200)):
        store.reserve(
            order_id=side,
            signal=signal,
            quantity=1,
            price=price,
            side=side,
            now=NOW,
            policy_version=1,
            reason="fixture",
        )
        store.update_order(side, "filled", 1, price, NOW)
    report = snapshot(store)
    assert "lab_fixture" in {r["strategy"] for r in report["strategies"]}
    assert (
        sum(r["net_pnl"] for r in report["strategies"]) == report["realized_net_pnl"]
        or abs(
            sum(r["net_pnl"] for r in report["strategies"]) - report["realized_net_pnl"]
        )
        < 0.01
    )
    store.close()


def test_one_quantity_divergence_does_not_strand_another_position(tmp_path):
    from dataclasses import replace
    from quantpilot.paper.runtime import Runtime
    from quantpilot.paper.calendar import Session
    from quantpilot.packages.core.marketdata.types import Quote

    store = Store(tmp_path / "s")
    store.control("start")
    for symbol in ("005930", "000660"):
        signal = SimpleNamespace(
            symbol=symbol,
            strategy_id="trend_pullback",
            version="1",
            stop=69000.0,
            target=72000.0,
        )
        store.reserve(
            order_id="entry-" + symbol,
            signal=signal,
            quantity=1,
            price=70000,
            side="buy",
            now=NOW,
            policy_version=1,
            reason="fixture",
        )
        store.update_order("entry-" + symbol, "filled", 1, 70000, NOW)
    client = Client()
    client.filled = True
    original = client.get_balance()
    client.get_balance = lambda **kwargs: replace(
        original,
        positions=(
            *original.positions,
            replace(
                original.positions[0],
                symbol="000660",
                holding_quantity=5,
                orderable_quantity=5,
            ),
        ),
    )
    calendar = SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)),
        current_open_session_date=lambda at: at.date(),
    )
    gateway = KisGateway(store, client, calendar, lambda: NOW)
    submitted = []
    gateway.submit = lambda order, *args: submitted.append(order)
    market = SimpleNamespace(
        quotes=lambda symbols: {
            s: Quote(symbol=s, last=60000, bid=60000, ask=60100, as_of=NOW)
            for s in symbols
        }
    )
    result = Runtime(store, market, gateway, calendar, lambda: NOW).cycle()
    assert result["new_entries"] is False
    assert [o["symbol"] for o in submitted] == ["005930"]
    assert store.get("unverified_symbols") == ["000660"]
    from quantpilot.paper.reporting import render, snapshot

    assert "보호 대기: 000660" in render(snapshot(store))
    assert any(
        "보호 대기: 000660" in str(tuple(row))
        for row in store.db.execute("SELECT * FROM outbox")
    )
    assert gateway.protection_allowed("005930") and not gateway.protection_allowed(
        "000660"
    )
    gateway.close()
    store.close()
