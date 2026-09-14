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
from quantpilot.packages.core.kis_paper import KisPaperGatewayRejected

# NOW is 10:00:02 KST. With the default 15 s grace and 2 s margin the first symbol's
# slot opens at 10:00:17, so the fixed-clock tests run at 10:00:22.
AT = NOW + timedelta(seconds=20)


def calendar():
    return SimpleNamespace(
        session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5))
    )


def bar_at(minute_offset=-1, **changes):
    bar = Bar(
        "005930",
        NOW.replace(second=0) + timedelta(minutes=minute_offset),
        100.0,
        101.0,
        99.0,
        100.0,
        10.0,
    )
    return replace(bar, **changes)


def audits(store, kind):
    return [
        r for r in store.db.execute("SELECT kind, payload FROM audit ORDER BY id")
        if r[0] == kind
    ]


def hold(store, symbol):
    store.db.execute(
        "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,0)",
        (symbol, "trend_pullback", 1, 100.0, 95.0, 110.0, "v1", NOW.isoformat()),
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
    bar = bar_at()
    calls = []

    def minutes(*args):
        calls.append(args)
        return [bar]

    market = SimpleNamespace(
        candidates=lambda *args: (["005930"], "fixture"), minutes=minutes
    )
    collector = Collector(path, market, calendar(), lambda: AT)
    assert collector.collect_once(writer)["fetched"] == ["005930"]
    assert collector.collect_once(writer)["fetched"] == []  # same minute: no second read
    assert reader.load_bars("005930") == [bar]
    assert reader.get("data:005930")["last_bar"] == bar.start.isoformat()
    assert calls == [("005930", AT, 15)]
    writer.close()
    reader.close()


def test_collector_reads_each_symbol_once_per_minute_after_the_grace(tmp_path):
    store = Store(tmp_path / "s")
    now = [NOW]
    calls = []

    def minutes(symbol, at, grace):
        calls.append((symbol, at, grace))
        return [bar_at()]

    market = SimpleNamespace(candidates=lambda *args: (["005930"], "fixture"), minutes=minutes)
    collector = Collector(store.path, market, calendar(), lambda: now[0])
    collector.collect_once(store)  # 10:00:02, inside the grace window
    assert calls == []
    assert store.get("collector_heartbeat") == NOW.isoformat()
    now[0] = NOW + timedelta(seconds=15)  # 10:00:17: slot open
    collector.collect_once(store)
    now[0] = NOW + timedelta(seconds=40)
    collector.collect_once(store)
    assert calls == [("005930", NOW + timedelta(seconds=15), 15)]
    now[0] = NOW + timedelta(seconds=75)  # 10:01:17: next minute
    collector.collect_once(store)
    assert [c[1] for c in calls] == [NOW + timedelta(seconds=15), NOW + timedelta(seconds=75)]
    store.close()


def test_positions_come_first_and_slots_stay_inside_the_minute_above_the_limiter_floor(tmp_path):
    store = Store(tmp_path / "s")
    universe = [f"{i:06d}" for i in range(1, 31)]
    store.put("universe", universe)
    hold(store, "999999")
    collector = Collector(store.path, SimpleNamespace(), calendar(), lambda: NOW)
    symbols = collector.symbols(store)
    assert symbols[0] == "999999" and symbols[1:] == universe
    bucket = collector.bucket(NOW)
    slots = []
    for offset in range(0, 60):
        at = bucket + timedelta(seconds=offset)
        for symbol in collector.due(symbols, at):
            slots.append((symbol, offset))
            collector.fetched[symbol] = bucket.isoformat()
    assert [s for s, _ in slots] == symbols  # every symbol gets a slot within the minute
    assert slots[0][1] == 17 and slots[-1][1] < 60
    # Too many symbols for the minute: spacing is clamped to the limiter floor, never
    # below, and the tail simply carries into the next minute.
    crowded = [f"{i:06d}" for i in range(1, 61)]
    fresh = Collector(store.path, SimpleNamespace(), calendar(), lambda: NOW)
    assert fresh.due(crowded, bucket + timedelta(seconds=18)) == crowded[:1]
    assert fresh.due(crowded, bucket + timedelta(seconds=18.1)) == crowded[:2]
    assert len(fresh.due(crowded, bucket + timedelta(seconds=59.9))) < 60
    store.close()


def test_collector_pauses_while_the_trader_backs_off_from_the_broker(tmp_path):
    store = Store(tmp_path / "s")
    calls = []
    market = SimpleNamespace(
        candidates=lambda *args: (["005930"], "fixture"),
        minutes=lambda *args: calls.append(args) or [bar_at()],
    )
    now = [AT]
    store.put("broker_retry_after", (AT + timedelta(seconds=5)).isoformat())
    collector = Collector(store.path, market, calendar(), lambda: now[0])
    collector.collect_once(store)
    assert calls == []
    assert store.get("collector_heartbeat") == AT.isoformat()
    now[0] = AT + timedelta(seconds=6)
    collector.collect_once(store)
    assert len(calls) == 1
    store.close()


def test_gateway_rejection_keeps_candidate_status_and_is_counted_once(tmp_path):
    store = Store(tmp_path / "s")
    store.put("candidate_status:005930", "ready")
    store.put("candidate_status:000660", "ready")
    now = [AT]
    failures = [
        KisPaperGatewayRejected("refused (code=EGW00201)", code="EGW00201")
    ]
    calls = []

    def minutes(symbol, at, grace):
        calls.append((symbol, at))
        if failures and symbol == "005930":
            raise failures.pop()
        return [replace(bar_at(), symbol=symbol)]

    market = SimpleNamespace(
        candidates=lambda *args: (["005930", "000660"], "fixture"), minutes=minutes
    )
    collector = Collector(store.path, market, calendar(), lambda: now[0])
    result = collector.collect_once(store)  # both slots are open at 10:00:22
    assert result["fetched"] == [] and calls == [("005930", AT)]  # sweep stopped
    assert store.get("candidate_status:005930") == "ready"
    key = "collector_minutes:KisPaperGatewayRejected:EGW00201"
    assert store.get("collector_error_counts") == {key: 1}
    assert len(audits(store, "collector_request_failed")) == 1
    now[0] = AT + timedelta(seconds=1)
    collector.collect_once(store)  # still paused
    assert len(calls) == 1
    now[0] = AT + timedelta(seconds=4)
    result = collector.collect_once(store)  # retried once, then the next symbol
    assert result["fetched"] == ["005930", "000660"]
    assert store.load_bars("000660")[0].symbol == "000660"
    # A second failure inside the audit interval (next minute's slot, 56 s later) is
    # counted but not audited again.
    failures.append(KisPaperGatewayRejected("refused (code=EGW00201)", code="EGW00201"))
    now[0] = AT + timedelta(seconds=56)
    collector.collect_once(store)
    assert store.get("collector_error_counts") == {key: 2}
    assert len(audits(store, "collector_request_failed")) == 1
    assert store.get("candidate_status:005930") == "ready"
    from quantpilot.paper.reporting import snapshot

    report = snapshot(store, now=now[0])
    assert report["collector_error_counts"] == {key: 2}
    assert report["collector_last_error"]["key"] == key
    store.close()


def test_snapshot_exposes_empty_collector_error_counts_by_default(tmp_path):
    from quantpilot.paper.reporting import snapshot

    store = Store(tmp_path / "s")
    report = snapshot(store, now=NOW)
    assert report["collector_error_counts"] == {} and report["collector_last_error"] is None
    store.close()


def test_revised_completed_bar_quarantines_symbol_for_session(tmp_path, monkeypatch):
    store = Store(tmp_path / "s")
    store.control("start")
    bar = bar_at()
    store.save_bars([bar])
    calls = []

    def minutes(*args):
        calls.append(args)
        return [replace(bar, volume=11.0)]

    market = SimpleNamespace(candidates=lambda *args: (["005930"], "fixture"), minutes=minutes)
    now = [AT]
    collector = Collector(store.path, market, calendar(), lambda: now[0])
    collector.collect_once(store)
    assert store.load_bars("005930") == [bar]
    day = AT.astimezone(KST).date().isoformat()
    quarantine = store.get("data_quarantine:005930")
    assert quarantine["day"] == day and quarantine["start"] == bar.start.isoformat()
    # Labelled immediately, before any order cycle looks at it.
    assert store.get("candidate_status:005930") == "completed_bar_revised_quarantined"
    [(_, payload)] = audits(store, "market_data_quarantined")
    import json

    payload = json.loads(payload)
    assert payload["stored"]["volume"] == 10.0 and payload["revised"]["volume"] == 11.0
    assert payload["changed"] == ["volume"]
    assert payload["seconds_after_close"] == 22.0
    # Quarantined for the session: no further reads, no repeated audit, and a later
    # clean read could not erase the known revision anyway.
    market.minutes = lambda *args: calls.append(args) or [bar]
    now[0] = AT + timedelta(minutes=2)
    collector.collect_once(store)
    assert len(calls) == 1
    assert len(audits(store, "market_data_quarantined")) == 1

    def forbidden(*args):
        pytest.fail("quarantined data reached strategy evaluation")
    monkeypatch.setattr("quantpilot.paper.strategy.evaluate_strategies", forbidden)
    runtime = Runtime(store, market, FixtureGateway(store), calendar(), lambda: AT,
                      background_data=True)
    assert runtime.cycle()["status"] == "running"
    assert store.get("candidate_status:005930") == "completed_bar_revised_quarantined"
    from quantpilot.paper.risk import data_quarantined, entry_size
    assert data_quarantined(store, "005930", AT)
    assert not data_quarantined(store, "005930", AT + timedelta(days=1))
    # Submission-time sizing rechecks quarantine even for an already selected signal.
    assert entry_size(store, SimpleNamespace(symbol="005930"), None, 0.1, AT) == 0
    store.close()


def test_held_quarantined_symbol_still_receives_newer_bars_only(tmp_path):
    store = Store(tmp_path / "s")
    bar = bar_at()
    store.save_bars([bar])
    hold(store, "005930")
    store.put("universe", [])
    newer = bar_at(0)
    market = SimpleNamespace(
        candidates=lambda *args: ([], "fixture"),
        minutes=lambda *args: [replace(bar, volume=11.0), newer],
    )
    now = [AT]
    collector = Collector(store.path, market, calendar(), lambda: now[0])
    collector.collect_once(store)  # first read: the revision is detected and audited once
    assert store.load_bars("005930") == [bar]
    now[0] = AT + timedelta(minutes=1)
    assert collector.collect_once(store)["fetched"] == ["005930"]
    assert store.load_bars("005930") == [bar, newer]  # disputed bar kept as stored
    assert len(audits(store, "market_data_quarantined")) == 1
    assert store.get("data:005930")["floor"] == bar.start.isoformat()
    store.close()


def test_evidence_accumulates_every_symbol_across_passes(tmp_path):
    store = Store(tmp_path / "s")
    market = SimpleNamespace(
        candidates=lambda *args: (["005930", "000660"], "fixture"),
        minutes=lambda symbol, at, grace: [replace(bar_at(), symbol=symbol)],
    )
    now = [NOW + timedelta(seconds=15)]  # only the first slot is open
    collector = Collector(store.path, market, calendar(), lambda: now[0])
    assert collector.collect_once(store)["fetched"] == ["005930"]
    assert list(store.get("evidence")["market_data"]) == ["005930"]
    now[0] = NOW + timedelta(seconds=20)
    assert collector.collect_once(store)["fetched"] == ["000660"]
    evidence = store.get("evidence")
    assert sorted(evidence["market_data"]) == ["000660", "005930"]
    assert evidence["symbols"] == ["005930", "000660"]
    assert evidence["observed_at"] == (NOW + timedelta(seconds=15)).isoformat()
    store.close()


def test_real_collector_thread_runs_beside_order_cycle(tmp_path):
    import threading

    store = Store(tmp_path / "s")
    store.control("start")
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    bar = bar_at()

    def minutes(*args):
        entered.set()
        assert release.wait(2)
        return [bar]

    market = SimpleNamespace(
        candidates=lambda *args: (["005930"], "fixture"), minutes=minutes
    )
    collector = Collector(store.path, market, calendar(), lambda: AT)
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
            lambda: AT,
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
