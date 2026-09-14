from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from quantpilot.paper.store import Store, CompletedBarRevised
from quantpilot.paper.strategy import Bar
from quantpilot.paper.valuation import record_close, roll_baselines

NOW = datetime(2026, 9, 14, 6, 31, tzinfo=timezone.utc)


def test_proven_close_survives_later_transport_failure(tmp_path):
    s = Store(tmp_path / "experiment.sqlite3")
    record_close(s, NOW, valid=True, equity=4_999_000)
    record_close(s, NOW + timedelta(minutes=10), valid=False)
    assert s.get("last_close_equity_valid") is True
    assert s.get("last_close_equity") == 4_999_000
    assert s.get("close:2026-09-14")["valid"] is True
    assert s.db.execute("SELECT COUNT(*) FROM close_observations").fetchone()[0] == 2
    s.close()


def test_previous_session_close_not_previous_calendar_day(tmp_path):
    from quantpilot.paper.calendar import Calendar
    s = Store(tmp_path / "experiment.sqlite3")
    friday = NOW - timedelta(days=3)
    record_close(s, friday, valid=True, equity=4_998_000)
    # A prior order establishes this is no longer the first experiment session.
    s.orders = lambda: [{"at": friday.isoformat()}]
    roll_baselines(s, Calendar(), NOW)
    assert s.get("day_base") == 4_998_000
    assert s.get("day_base_valid") is True
    assert s.get("day_base_source") == "2026-09-11"
    s.close()


def test_provisional_revision_finality_and_immutable_used_bar(tmp_path):
    s = Store(tmp_path / "experiment.sqlite3")
    start = NOW.replace(hour=1, minute=0, second=0)
    bar = Bar("005930", start, 100, 102, 99, 101, 10)
    assert s.observe_bars([bar], start + timedelta(seconds=50)) == []
    revised = replace(bar, close=102, volume=20)
    assert s.observe_bars([revised], start + timedelta(seconds=65)) == []
    assert not s.load_bars(bar.symbol)
    assert s.observe_bars([revised], start + timedelta(seconds=75)) == [revised]
    with pytest.raises(CompletedBarRevised):
        s.observe_bars([bar], start + timedelta(seconds=90))
    assert s.load_bars(bar.symbol) == [revised]
    s.close()


def test_incident_recovery_is_scoped(tmp_path):
    from quantpilot.paper.diagnostics import open_incident, recover_incident
    s = Store(tmp_path / "experiment.sqlite3")
    open_incident(s, "reconciliation", "reconciliation_required", NOW)
    open_incident(s, "protection:005930", "position_protection_unavailable", NOW)
    recover_incident(s, "reconciliation", NOW)
    assert s.get("incident") == "position_protection_unavailable"
    assert "protection:005930" in s.get("incidents")
    s.close()


def test_reconciliation_failure_still_evaluates_verified_protection(tmp_path):
    from types import SimpleNamespace
    from quantpilot.paper.broker import FixtureGateway
    from quantpilot.paper.runtime import Runtime
    from quantpilot.paper.calendar import Session
    from quantpilot.packages.core.marketdata.types import Quote
    s = Store(tmp_path / "s")
    s.control("start", now=NOW)
    signal = SimpleNamespace(symbol="005930", strategy_id="trend_pullback", version="1", stop=99, target=105)
    s.reserve(order_id="prior", signal=signal, quantity=1, price=100, side="buy", now=NOW, policy_version=1, reason="fixture")
    s.update_order("prior", "filled", 1, 100, NOW)
    s.control("pause", now=NOW)
    class Gateway(FixtureGateway):
        def reconcile(self, at):
            raise TimeoutError()
    market = SimpleNamespace(quotes=lambda symbols: {"005930": Quote(symbol="005930", last=98, bid=98, ask=99, as_of=NOW)})
    calendar = SimpleNamespace(session=lambda at: Session(NOW-timedelta(hours=6), NOW+timedelta(hours=1)))
    result = Runtime(s, market, Gateway(s), calendar, lambda: NOW).cycle()
    assert result["new_entries"] is False
    assert not s.positions()
    assert len([o for o in s.orders() if o["side"] == "sell"]) == 1
    assert s.get("incidents")["reconciliation"]
    s.close()


def test_reporter_reports_without_trader_or_ai_and_corrects_once(tmp_path):
    from types import SimpleNamespace
    from quantpilot.paper.calendar import Session
    from quantpilot.paper.reporting import operation_report_once, drain_outbox
    s = Store(tmp_path / "s")
    s.configure({"independent_reports_enabled": True}, 1)
    calendar = SimpleNamespace(session=lambda at: Session(NOW-timedelta(hours=7), NOW-timedelta(seconds=10)))
    assert operation_report_once(s, calendar, NOW)["id"] == "operation:2026-09-14"
    assert s.get("last_report")["close_confirmed"] is False
    assert s.get("last_report")["close_report_delay_seconds"] == 10
    calls = []
    def fail(key, text):
        calls.append(key)
        raise TimeoutError()
    drain_outbox(s, SimpleNamespace(send=fail))
    s.close()
    s = Store(tmp_path / "s")
    assert operation_report_once(s, calendar, NOW)["status"] == "already_reported"
    record_close(s, NOW, valid=True, equity=5_000_000)
    assert operation_report_once(s, calendar, NOW)["id"] == "correction:2026-09-14"
    operation_report_once(s, calendar, NOW)
    drain_outbox(s, SimpleNamespace(send=fail))
    assert calls == ["operation:2026-09-14", "correction:2026-09-14"]
    s.close()


def test_ai_postclose_retries_at_one_and_five_minutes_with_both_failures(tmp_path):
    from quantpilot.paper.jobs import work_once
    import json
    s = Store(tmp_path / "s")
    s.configure({"independent_reports_enabled": True, "ai_enabled": True}, 1)
    s.put("ai_due", {"key": "2026-09-14:postclose", "kind": "postclose"})
    calls = []
    def runner(provider, *args):
        calls.append(provider)
        if provider == "claude":
            raise TimeoutError()
        raise FileNotFoundError()
    assert work_once(s, NOW, runner=runner)["status"] == "retry_wait"
    assert work_once(s, NOW+timedelta(seconds=59), runner=runner)["status"] == "already_processed"
    assert work_once(s, NOW+timedelta(seconds=60), runner=runner)["status"] == "retry_wait"
    s.close()
    s = Store(tmp_path / "s")
    assert work_once(s, NOW+timedelta(seconds=299), runner=runner)["status"] == "already_processed"
    assert work_once(s, NOW+timedelta(seconds=300), runner=runner)["status"] == "failed"
    assert calls == ["claude", "codex"] * 3
    results = s.db.execute("SELECT result FROM job_attempts").fetchall()
    assert len(results) == 3
    assert json.loads(results[0][0])["provider_failures"] == {"claude": "runner_timeout", "codex": "runner_unavailable"}
    assert [r[0] for r in s.db.execute("SELECT id FROM outbox")] == ["review:2026-09-14:postclose"]
    s.close()
