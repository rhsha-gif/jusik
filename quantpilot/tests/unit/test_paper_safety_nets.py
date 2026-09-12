"""Safety nets from the 2026-09-12 audit that apply to the legacy generation too.

Daily-loss / drawdown halts gate every generation, the trader pauses itself after
the close, a persistent unknown order calls the operator without blocking, a dead
trader raises an hourly liveness DM, and a refused start leaves ledger evidence.
"""

import json
import sys
import types
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quantpilot.paper.broker import FixtureGateway
from quantpilot.paper.calendar import Session
from quantpilot.paper.risk import entry_size
from quantpilot.paper.runtime import Runtime
from quantpilot.paper.store import Store
from quantpilot.tests.unit.test_paper_runtime_controls import NOW, quote, signal


def _stub_strategy(monkeypatch):
    stub = types.ModuleType("quantpilot.paper.strategy")
    stub.evaluate_strategies = lambda *a: []
    stub.allocate_weights = lambda *a, **k: {}
    stub.select_signals = lambda *a: []
    monkeypatch.setitem(sys.modules, "quantpilot.paper.strategy", stub)


def _open_session():
    return SimpleNamespace(
        session=lambda now: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5))
    )


def _closed_session():
    return SimpleNamespace(
        session=lambda now: Session(NOW - timedelta(hours=7), NOW - timedelta(minutes=1))
    )


def test_legacy_generation_daily_loss_halt_blocks_new_entries(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    assert s.policy.strategy_generation == "legacy"
    s.put("day_base", 5_000_000.0)
    s.put("cash", 4_999_000.0)
    s.put("realized", -1_000.0)
    assert entry_size(s, signal(), quote(), 0.6, NOW) > 0
    s.put("cash", 4_940_000.0)
    s.put("realized", -60_000.0)
    assert entry_size(s, signal(), quote(), 0.6, NOW) == 0
    state = s.get("intraday_loss_state")
    assert state["daily_halted"] and state["available"] == 0
    # The halt is durable: a recovered balance later the same day does not re-arm entries.
    s.put("cash", 5_000_000.0)
    s.put("realized", 0.0)
    later = NOW + timedelta(hours=2)
    assert entry_size(s, signal(), quote(later), 0.6, later) == 0
    s.close()


def test_legacy_runtime_cycle_respects_loss_halt(tmp_path, monkeypatch):
    _stub_strategy(monkeypatch)
    s = Store(tmp_path / "s")
    s.control("start")
    s.put("day_base", 5_000_000.0)
    s.put("day_base_valid", True)
    s.put("cash", 4_940_000.0)
    s.put("realized", -60_000.0)
    r = Runtime(
        s, SimpleNamespace(quotes=lambda symbols: {}), FixtureGateway(s), _open_session(), lambda: NOW
    )
    assert r.cycle() == {"status": "protecting", "new_entries": False}
    assert s.get("intraday_loss_state")["daily_halted"]
    s.close()


def test_trader_pauses_itself_after_the_session_closes(tmp_path, monkeypatch):
    _stub_strategy(monkeypatch)
    s = Store(tmp_path / "s")
    s.control("start")
    r = Runtime(
        s, SimpleNamespace(quotes=lambda symbols: {}), FixtureGateway(s), _closed_session(), lambda: NOW
    )
    assert r.cycle()["status"] == "postclose"
    assert s.get("control") == "paused"
    kinds = [row["kind"] for row in s.db.execute("SELECT kind FROM audit")]
    assert "auto_paused_after_close" in kinds
    # Idempotent, and never resumes on its own the next morning.
    assert r.cycle()["status"] == "postclose" and s.get("control") == "paused"
    s.close()


def test_auto_pause_can_be_disabled_explicitly(tmp_path, monkeypatch):
    _stub_strategy(monkeypatch)
    s = Store(tmp_path / "s")
    s.configure({"auto_pause_after_close": False}, 1)
    s.control("start")
    r = Runtime(
        s, SimpleNamespace(quotes=lambda symbols: {}), FixtureGateway(s), _closed_session(), lambda: NOW
    )
    assert r.cycle()["status"] == "postclose" and s.get("control") == "running"
    s.close()


def test_persistent_unknown_order_calls_the_operator_without_blocking(tmp_path, monkeypatch):
    _stub_strategy(monkeypatch)
    s = Store(tmp_path / "s")
    s.control("start")
    s.reserve(
        order_id="lost",
        signal=signal(),
        quantity=1,
        price=10000,
        side="buy",
        now=NOW - timedelta(minutes=10),
        policy_version=1,
        reason="entry",
    )
    s.update_order("lost", "outcome_unknown", 0, 0, NOW - timedelta(minutes=10))
    s.control("pause")

    class Gateway(FixtureGateway):
        def cancel(self, order, now):
            pass  # a real gateway defers; the unknown row must not silently close

    r = Runtime(
        s, SimpleNamespace(quotes=lambda symbols: {}), Gateway(s), _open_session(), lambda: NOW
    )
    assert r.cycle()["new_entries"] is False
    assert s.get("incident") is None
    queued = s.db.execute(
        "SELECT COUNT(*) FROM outbox WHERE id LIKE '%manual_resolution_required'"
    ).fetchone()[0]
    assert queued == 1
    r.cycle()
    assert s.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    s.close()


def test_trader_liveness_alert_is_hourly_and_only_while_armed_in_session(tmp_path):
    from quantpilot.paper.reporting import check_trader_liveness

    s = Store(tmp_path / "s")
    s.configure({"slack_enabled": True}, 1)
    s.control("start")
    assert NOW.astimezone(timezone(timedelta(hours=9))).weekday() == 3  # Thursday 10:00 KST
    s.put("heartbeat", (NOW - timedelta(minutes=10)).isoformat())
    assert check_trader_liveness(s, NOW) is True
    assert check_trader_liveness(s, NOW + timedelta(minutes=20)) is False
    assert check_trader_liveness(s, NOW + timedelta(hours=1)) is True
    assert s.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2
    s.put("heartbeat", (NOW + timedelta(hours=2) - timedelta(seconds=60)).isoformat())
    assert check_trader_liveness(s, NOW + timedelta(hours=2)) is False
    s.put("heartbeat", (NOW - timedelta(hours=3)).isoformat())
    assert check_trader_liveness(s, NOW + timedelta(days=2)) is False  # Saturday
    s.put("heartbeat", (NOW - timedelta(hours=3)).isoformat())
    assert check_trader_liveness(s, NOW + timedelta(hours=8)) is False  # after 15:40 KST
    s.close()


def test_process_failure_is_recorded_in_the_ledger(tmp_path):
    from quantpilot.paper.reporting import record_process_failure

    s = Store(tmp_path / "s")
    s.configure({"slack_enabled": True}, 1)
    result = {"status": "blocked", "reason": "PermissionError"}
    record_process_failure(s, "start", result, NOW)
    record_process_failure(s, "start", result, NOW)
    kinds = [row["kind"] for row in s.db.execute("SELECT kind FROM audit")]
    assert kinds.count("process_failed") == 2
    assert s.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    s.close()


def test_cli_start_failure_leaves_process_failed_evidence(tmp_path):
    from quantpilot.paper.cli import main

    directory = Path(tmp_path).resolve()
    if any((p / ".git").exists() for p in (directory, *directory.parents)):
        pytest.skip("runtime directories inside a repository are refused before the ledger opens")
    assert main(["--runtime-dir", str(directory / "rt"), "start"]) == 2
    s = Store(directory / "rt" / "experiment.sqlite3")
    rows = [
        json.loads(r["payload"])
        for r in s.db.execute("SELECT payload FROM audit WHERE kind='process_failed'")
    ]
    assert rows == [{"role": "start", "reason": "fixture_requires_injected_test_clients"}]
    s.close()
