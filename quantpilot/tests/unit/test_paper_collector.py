from datetime import timedelta
from types import SimpleNamespace
import pytest
from quantpilot.paper.broker import FixtureGateway
from quantpilot.paper.calendar import Session
from quantpilot.paper.collector import Collector
from quantpilot.paper.runtime import Runtime
from quantpilot.paper.store import Store
from quantpilot.paper.strategy import Bar
from quantpilot.tests.unit.test_intraday_durable_gateway import NOW
from dataclasses import replace
from quantpilot.paper.calendar import KST


def calendar():
    return SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5))
    )


def test_order_cycle_does_not_fetch_the_candidate_universe_in_background_mode(tmp_path):
    store = Store(tmp_path / "s")
    store.control("start")

    def forbidden(*args):
        pytest.fail("order loop waited for discovery")

    runtime = Runtime(
        store,
        SimpleNamespace(candidates=forbidden, minutes=forbidden),
        FixtureGateway(store),
        calendar(),
        lambda: NOW,
        background_data=True,
    )
    assert runtime.cycle()["status"] == "running"
    store.close()


def test_collector_persists_only_observed_bars_for_order_loop(tmp_path):
    path = tmp_path / "s"
    writer = Store(path)
    reader = Store(path)
    bar = Bar(
        "005930",
        NOW.replace(second=0) - timedelta(minutes=1),
        100.0,
        101.0,
        99.0,
        100.0,
        10.0,
    )
    market = SimpleNamespace(
        candidates=lambda *args: (["005930"], "fixture"), minutes=lambda *args: [bar]
    )
    collector = Collector(path, market, calendar(), lambda: NOW)
    collector.collect_once(writer)
    collector.collect_once(writer)
    assert reader.load_bars("005930") == [bar]
    assert reader.get("data:005930")["last_bar"] == bar.start.isoformat()
    writer.close()
    reader.close()


def test_revised_completed_bar_quarantines_symbol_for_session(tmp_path, monkeypatch):
    store = Store(tmp_path / "s")
    store.control("start")
    bar = Bar("005930", NOW.replace(second=0) - timedelta(minutes=1),
              100.0, 101.0, 99.0, 100.0, 10.0)
    store.save_bars([bar])
    market = SimpleNamespace(candidates=lambda *args: (["005930"], "fixture"),
                             minutes=lambda *args: [replace(bar, volume=11.0)])
    collector = Collector(store.path, market, calendar(), lambda: NOW)
    collector.collect_once(store)
    assert store.load_bars("005930") == [bar]
    assert store.get("data_quarantine:005930")["day"] == NOW.astimezone(KST).date().isoformat()
    # A later successful read cannot erase a known revision from this session.
    market.minutes = lambda *args: [bar]
    collector.collect_once(store)
    def forbidden(*args):
        pytest.fail("quarantined data reached strategy evaluation")
    monkeypatch.setattr("quantpilot.paper.strategy.evaluate_strategies", forbidden)
    runtime = Runtime(store, market, FixtureGateway(store), calendar(), lambda: NOW,
                      background_data=True)
    assert runtime.cycle()["status"] == "running"
    assert store.get("candidate_status:005930") == "completed_bar_revised_quarantined"
    from quantpilot.paper.risk import data_quarantined, entry_size
    assert data_quarantined(store, "005930", NOW)
    assert not data_quarantined(store, "005930", NOW + timedelta(days=1))
    # Submission-time sizing rechecks quarantine even for an already selected signal.
    assert entry_size(store, SimpleNamespace(symbol="005930"), None, 0.1, NOW) == 0
    store.close()


def test_real_collector_thread_runs_beside_order_cycle(tmp_path):
    import threading

    store = Store(tmp_path / "s")
    store.control("start")
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    bar = Bar(
        "005930",
        NOW.replace(second=0) - timedelta(minutes=1),
        100.0,
        101.0,
        99.0,
        100.0,
        10.0,
    )

    def minutes(*args):
        entered.set()
        assert release.wait(2)
        return [bar]

    market = SimpleNamespace(
        candidates=lambda *args: (["005930"], "fixture"), minutes=minutes
    )
    collector = Collector(store.path, market, calendar(), lambda: NOW)
    original = collector.collect_once

    def collect(writer):
        original(writer)
        completed.set()

    collector.collect_once = collect
    collector.start()
    try:
        assert entered.wait(2)
        runtime = Runtime(
            store,
            market,
            FixtureGateway(store),
            calendar(),
            lambda: NOW,
            background_data=True,
        )
        assert runtime.cycle()["status"] == "running"
        release.set()
        assert completed.wait(2)
        assert store.load_bars("005930") == [bar]
        assert store.get("collector_error") is None
    finally:
        release.set()
        collector.close()
        store.close()
