"""Latency timeline and measurement-only audits: record, never gate."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

from quantpilot.paper import latency
from quantpilot.paper.store import Store
from quantpilot.packages.core.marketdata.types import Quote

NOW = datetime(2026, 9, 15, 1, 50, 25, tzinfo=timezone.utc)


def signal(**changes):
    base = dict(symbol="005930", strategy_id="trend_pullback", price=10000.0, stop=9900.0,
                target=10300.0, score=0.8, version="1", reason="fixture", entry_atr14=None,
                bar_end=NOW - timedelta(seconds=25), observed_at=NOW - timedelta(seconds=5),
                computed_at=NOW)
    base.update(changes)
    return SimpleNamespace(**base)


def test_mark_is_first_write_wins_and_report_measures_intervals(tmp_path):
    s = Store(tmp_path / "s")
    latency.link_signal(s, "buy-1", signal(), NOW)
    latency.mark(s, "buy-1", prepared_at=NOW + timedelta(seconds=1),
                 send_start_at=NOW + timedelta(seconds=3), response_at=NOW + timedelta(seconds=5))
    latency.mark(s, "buy-1", send_start_at=NOW + timedelta(seconds=30))  # ignored: already set
    latency.mark(s, "buy-1", fill_recognized_at=NOW + timedelta(seconds=15), noise=None)
    timeline = s.get("timeline:buy-1")
    assert timeline["send_start_at"] == (NOW + timedelta(seconds=3)).isoformat()
    assert "noise" not in timeline
    report = latency.report(s, now=NOW)
    entry = report["entry"]
    assert report["orders"] == 1 and entry["orders"] == 1
    assert entry["bar_close_to_send"] == {"count": 1, "missing": 0, "p50": 28.0, "p95": 28.0, "p99": 28.0, "max": 28.0}
    assert entry["decision_to_fill_recognized"]["p50"] == 15.0
    assert entry["bar_close_to_observed"]["p50"] == 20.0
    # An exit with no first-observation instant counts as missing, never as zero.
    latency.mark(s, "sell-1", side="sell", decision_at=NOW, send_start_at=NOW + timedelta(seconds=2))
    report = latency.report(s, now=NOW)
    assert report["exit"]["condition_to_send"] == {"count": 0, "missing": 1, "p50": None, "p95": None, "p99": None, "max": None}
    assert report["exit"]["decision_to_send"]["p50"] == 2.0
    # Another day's orders are excluded from today's report.
    latency.mark(s, "buy-old", side="buy", decision_at=NOW - timedelta(days=1))
    assert latency.report(s, now=NOW)["orders"] == 2
    assert latency.report(s, day=(NOW - timedelta(days=1)).astimezone(latency.KST).date().isoformat())["orders"] == 1
    head = latency.headline(s, NOW)
    assert head["entry_bar_close_to_send"] == {"count": 1, "p50": 28.0, "p95": 28.0}
    s.close()


def test_percentiles_use_nearest_rank():
    values = list(range(1, 101))
    assert latency._percentile(values, 0.50) == 50
    assert latency._percentile(values, 0.95) == 95
    assert latency._percentile(values, 0.99) == 99
    assert latency._percentile([], 0.5) is None


def test_stale_signal_boundary_matches_the_bar_freshness_gate():
    fresh = signal(bar_end=NOW - timedelta(seconds=90))
    stale = signal(bar_end=NOW - timedelta(seconds=91))
    assert not latency.stale_signal(fresh, NOW)
    assert latency.stale_signal(stale, NOW)
    assert not latency.stale_signal(signal(bar_end=None), NOW)


def test_exit_condition_first_observation_persists_until_cleared(tmp_path):
    s = Store(tmp_path / "s")
    first = latency.observe_exit_condition(s, "005930", "stop", NOW)
    again = latency.observe_exit_condition(s, "005930", "stop", NOW + timedelta(seconds=10))
    assert first == again == NOW.isoformat()
    # A different reason restarts the clock; no reason clears it.
    assert latency.observe_exit_condition(s, "005930", "target", NOW + timedelta(seconds=20)) == (NOW + timedelta(seconds=20)).isoformat()
    assert latency.observe_exit_condition(s, "005930", None, NOW + timedelta(seconds=30)) is None
    assert s.get("exit_condition:005930") is None
    latency.observe_exit_condition(s, "005930", "stop", NOW + timedelta(seconds=40))
    quote = Quote(symbol="005930", last=9800, bid=9800, ask=9810, as_of=NOW + timedelta(seconds=41))
    latency.link_exit(s, "sell-1", "005930", "trend_pullback", "stop", quote, NOW + timedelta(seconds=42))
    timeline = s.get("timeline:sell-1")
    assert timeline["condition_first_observed_at"] == (NOW + timedelta(seconds=40)).isoformat()
    assert timeline["quote_observed_at"] == quote.as_of.isoformat()
    assert timeline["side"] == "sell" and timeline["reason"] == "stop"
    s.close()


def test_measurement_audits_record_net_target_reentry_and_reissue(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    price = 10000.0
    latency.measure_entry(s, signal(), price, 3, NOW)
    [payload] = [json.loads(r[1]) for r in s.db.execute("SELECT kind, payload FROM audit") if r[0] == "entry_net_target"]
    policy = s.policy
    expected = 10300 * (1 - (policy.fee_bps + policy.sell_tax_bps + policy.slippage_bps) / 10000) \
        - price * (1 + (policy.fee_bps + policy.slippage_bps) / 10000)
    assert payload["net_target_per_share"] == round(expected, 4)
    assert payload["would_pass_net_target_gate"] is True and payload["net_reward_ratio"] > 0
    # A target one tick above the price fails the (measurement-only) net-target gate.
    latency.measure_entry(s, signal(target=10010.0), price, 3, NOW)
    rows = [json.loads(r[1]) for r in s.db.execute("SELECT kind, payload FROM audit") if r[0] == "entry_net_target"]
    assert rows[-1]["would_pass_net_target_gate"] is False
    # Same-day re-entry after a filled stop is audited, a stop on another day is not.
    sig = signal()
    s.reserve(order_id="buy-0", signal=sig, quantity=1, price=10000, side="buy", now=NOW - timedelta(minutes=30), policy_version=1, reason="entry")
    s.update_order("buy-0", "filled", 1, 10000, NOW - timedelta(minutes=30))
    s.reserve(order_id="sell-0", signal=sig, quantity=1, price=9900, side="sell", now=NOW - timedelta(minutes=20), policy_version=1, reason="stop")
    s.update_order("sell-0", "filled", 1, 9900, NOW - timedelta(minutes=20))
    latency.measure_entry(s, sig, price, 1, NOW)
    [reentry] = [json.loads(r[1]) for r in s.db.execute("SELECT kind, payload FROM audit") if r[0] == "reentry_after_stop"]
    assert reentry["prior_stop_orders"] == ["sell-0"]
    latency.measure_entry(s, sig, price, 1, NOW + timedelta(days=1))
    assert sum(1 for r in s.db.execute("SELECT kind FROM audit") if r[0] == "reentry_after_stop") == 1
    # A second protective sell for the same symbol today is counted as a re-issue.
    latency.measure_exit(s, "sell-1", "005930", "stop", NOW)
    [reissue] = [json.loads(r[1]) for r in s.db.execute("SELECT kind, payload FROM audit") if r[0] == "protective_sell_reissued"]
    assert reissue["prior_sells_today"] == 1
    s.close()


def test_durable_gateway_stamps_prepare_send_response_and_fill_recognition(tmp_path):
    from quantpilot.paper.broker import KisGateway
    from quantpilot.paper.calendar import Session
    from quantpilot.tests.unit.test_intraday_durable_gateway import Client, NOW as T0

    s = Store(tmp_path / "s.sqlite")
    s.control("start")
    s.put("weights", {"trend_pullback": 0.6})
    client = Client()
    now = [T0]
    calendar = SimpleNamespace(
        session=lambda at: Session(T0 - timedelta(hours=1), T0 + timedelta(hours=5)),
        current_open_session_date=lambda at: at.date(),
    )
    g = KisGateway(s, client, calendar, lambda: now[0])
    g.begin()
    g.reconcile(T0)
    sig = SimpleNamespace(symbol="005930", strategy_id="trend_pullback", stop=69000.0, target=72000.0, version="1", entry_atr14=1000.0)
    s.reserve(order_id="paper-test", signal=sig, quantity=1, price=70000, side="buy", now=T0, policy_version=1, reason="fixture")
    q = Quote(symbol="005930", last=70000, bid=69900, ask=70000, as_of=T0)
    now[0] = T0 + timedelta(seconds=2)
    g.submit(s.orders()[0], q, now[0])
    timeline = s.get("timeline:paper-test")
    assert timeline["prepared_at"] == timeline["queue_enter_at"] == timeline["send_start_at"] == timeline["response_at"] == now[0].isoformat()
    assert timeline["broker_order_reference"] == "0000012345" and timeline["broker_order_time"] == "100001"
    assert "fill_recognized_at" not in timeline
    client.filled = True
    now[0] = T0 + timedelta(seconds=9)
    g.reconcile(now[0])
    assert s.get("timeline:paper-test")["fill_recognized_at"] == now[0].isoformat()
    now[0] = T0 + timedelta(seconds=19)
    g.reconcile(now[0])  # a repeated reconcile never moves the first recognition
    assert s.get("timeline:paper-test")["fill_recognized_at"] == (T0 + timedelta(seconds=9)).isoformat()
    g.end()
    g.close()
    s.close()


def test_cli_latency_command_is_read_only(tmp_path, capsys):
    from quantpilot.paper.cli import main

    s = Store(tmp_path / "experiment.sqlite3")
    latency.mark(s, "buy-1", side="buy", bar_end=NOW - timedelta(seconds=25), decision_at=NOW,
                 send_start_at=NOW + timedelta(seconds=3))
    s.close()
    day = NOW.astimezone(latency.KST).date().isoformat()
    assert main(["--runtime-dir", str(tmp_path), "--json", "latency", "--day", day]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["orders"] == 1 and out["entry"]["bar_close_to_send"]["p50"] == 28.0
    assert out["targets_seconds"] == {"exit_condition_to_send": 5, "entry_bar_close_to_send": 30}


def test_reentry_after_a_filled_stop_gets_a_fresh_first_observation(tmp_path):
    """Review finding: the protection loop never revisits a closed symbol, so the exit
    record must be retired when the sell is linked, not inherited by the next episode."""
    s = Store(tmp_path / "s")
    quote = Quote(symbol="005930", last=9800, bid=9800, ask=9810, as_of=NOW)
    latency.observe_exit_condition(s, "005930", "stop", NOW)
    latency.link_exit(s, "sell-1", "005930", "trend_pullback", "stop", quote, NOW + timedelta(seconds=1))
    assert s.get("exit_condition:005930") is None
    later = NOW + timedelta(hours=3)
    assert latency.observe_exit_condition(s, "005930", "stop", later) == later.isoformat()
    latency.link_exit(s, "sell-2", "005930", "trend_pullback", "stop", quote, later + timedelta(seconds=1))
    assert s.get("timeline:sell-2")["condition_first_observed_at"] == later.isoformat()
    s.close()


def test_recording_faults_never_cost_the_order(tmp_path, monkeypatch):
    from quantpilot.paper.broker import FixtureGateway
    from quantpilot.paper.calendar import Session
    from quantpilot.paper.runtime import Runtime

    s = Store(tmp_path / "s")
    s.control("start")
    s.put("weights", {"trend_pullback": 0.6})
    sig = SimpleNamespace(**{**signal().__dict__, "entry_atr14": None})
    calendar = SimpleNamespace(session=lambda at: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)))
    runtime = Runtime(s, SimpleNamespace(), FixtureGateway(s), calendar, lambda: NOW)

    def broken(*args, **kwargs):
        raise RuntimeError("sqlite busy")

    monkeypatch.setattr(latency, "measure_entry", broken)
    quote = Quote(symbol="005930", last=10000, bid=9990, ask=10000, as_of=NOW)
    runtime.place(sig, 1, quote, "buy", "fixture", NOW)
    [order] = s.orders()
    assert order["state"] == "filled"
    [payload] = [json.loads(r[1]) for r in s.db.execute("SELECT kind, payload FROM audit") if r[0] == "latency_record_failed"]
    assert payload["order_id"] == order["id"] and payload["error"] == "RuntimeError"
    s.close()
