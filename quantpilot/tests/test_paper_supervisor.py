from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quantpilot.paper.config import Policy
from quantpilot.paper.store import Store
from quantpilot.paper import supervisor


NOW = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class StopAt:
    def __init__(self, clock, seconds):
        self.clock = clock
        self.at = clock.now + timedelta(seconds=seconds)

    def is_set(self):
        return self.clock.now >= self.at


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminate_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = -15


class Factory:
    def __init__(self, clock, behavior=None):
        self.clock = clock
        self.behavior = behavior or (lambda role, count: FakeProcess())
        self.calls = []
        self.processes = []

    def __call__(self, command):
        role = {"start": "trader", "worker": "worker", "reporter": "reporter"}[
            command[-1]
        ]
        role_count = sum(call[0] == role for call in self.calls)
        self.calls.append((role, self.clock.now, command))
        process = self.behavior(role, role_count)
        if isinstance(process, BaseException):
            raise process
        self.processes.append((role, process))
        return process


@pytest.fixture(autouse=True)
def supervisor_policy_opt_in(tmp_path):
    with_store = Store(tmp_path / "experiment.sqlite3")
    with_store.configure({"supervisor_enabled": True, "data_mode": "paper_trading"}, with_store.policy.version)
    with_store.close()


def read_setting(runtime_dir: Path, key: str, default=None):
    store = Store(runtime_dir / "experiment.sqlite3")
    try:
        return store.get(key, default)
    finally:
        store.close()


def test_disabled_policy_blocks_without_launch(tmp_path, monkeypatch):
    store = Store(tmp_path / "experiment.sqlite3")
    store.configure({"supervisor_enabled": False}, store.policy.version)
    store.close()
    clock = FakeClock()
    factory = Factory(clock)

    result = supervisor.supervise(tmp_path, "python", clock, factory, StopAt(clock, 1))

    assert result == {"status": "blocked", "reason": "supervisor_disabled"}
    assert factory.calls == []


def test_unsafe_environment_blocks_without_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    clock = FakeClock()
    factory = Factory(clock)

    result = supervisor.supervise(tmp_path, "python", clock, factory, StopAt(clock, 1))

    assert result == {"status": "blocked", "reason": "unsafe_environment"}
    assert factory.calls == []


def test_launches_only_explicit_roles_and_preserves_manual_safety_state(tmp_path):
    store = Store(tmp_path / "experiment.sqlite3")
    try:
        store.put("control", "paused")
        store.put("resume_authorized_day", "2026-09-14")
        store.put(
            "intraday_loss_state",
            {"day": "2026-09-14", "daily_halted": True, "drawdown_halted": True},
        )
    finally:
        store.close()
    clock = FakeClock()
    factory = Factory(clock)

    result = supervisor.supervise(
        tmp_path, "X:/python.exe", clock, factory, StopAt(clock, 1)
    )

    assert result["status"] == "stopped"
    assert {call[0] for call in factory.calls} == set(supervisor.ROLES)
    for role, _, command in factory.calls:
        assert command == [
            "X:/python.exe",
            "-m",
            "quantpilot.paper",
            "--runtime-dir",
            str(tmp_path.resolve()),
            supervisor.ROLE_COMMAND[role],
        ]
    assert read_setting(tmp_path, "control") == "paused"
    assert read_setting(tmp_path, "resume_authorized_day") == "2026-09-14"
    assert read_setting(tmp_path, "intraday_loss_state")["drawdown_halted"] is True
    marker = read_setting(tmp_path, "recovery_required")
    assert marker["previous_control"] == "paused"
    assert marker["previous_running"] is False
    assert marker["resume_authorized_day"] == "2026-09-14"
    assert all(read_setting(tmp_path, f"role_stop:{role}") for role in supervisor.ROLES)
    assert all(
        read_setting(tmp_path, f"supervised:{role}") is False
        for role in supervisor.ROLES
    )


@pytest.mark.parametrize("marker", ["missing", "completed", "legacy", "supervised"])
@pytest.mark.parametrize("control", ["paused", "running"])
def test_actual_cli_start_preserves_recovery_and_operator_state(
    tmp_path, monkeypatch, marker, control
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from quantpilot.paper import cli, collector

    store = Store(tmp_path / "experiment.sqlite3")
    store.put("control", control)
    store.put("resume_authorized_day", "2026-09-11")
    loss = {"daily_halted": True, "drawdown_halted": True}
    store.put("intraday_loss_state", loss)
    if marker in {"completed", "legacy"}:
        store.put("recovery_required", marker == "legacy")
    expected = (
        {
            "required": True,
            "detected_at": NOW.isoformat(),
            "previous_control": control,
            "previous_running": control == "running",
            "resume_authorized_day": "2026-09-11",
        }
        if marker == "supervised"
        else True
    )
    events = []

    class FakeCollector:
        def __init__(self, *args):
            pass

        def start(self):
            events.append("collector_started")

        def close(self):
            events.append("collector_closed")

    def build_runtime(child_store, env):
        def cycle():
            # Observe the persisted state after the real child startup path.
            assert child_store.get("recovery_required") == expected
            assert child_store.get("control") == control
            assert child_store.get("resume_authorized_day") == "2026-09-11"
            assert child_store.get("intraday_loss_state") == loss
            events.append("cycle")
            return {"status": "protecting", "new_entries": False}

        return SimpleNamespace(
            market=None,
            calendar=SimpleNamespace(session=lambda _: None),
            clock=lambda: NOW,
            feed=None,
            budget=None,
            gateway=SimpleNamespace(close=lambda: events.append("gateway_closed")),
            cycle=cycle,
        )

    monkeypatch.setattr(cli, "build_runtime", build_runtime)
    monkeypatch.setattr(cli, "account_owner", lambda _: nullcontext())
    monkeypatch.setattr(collector, "Collector", FakeCollector)

    def launch_child(command):
        assert cli.main(["--runtime-dir", str(tmp_path), "start", "--once"]) == 0
        return FakeProcess()

    try:
        if marker == "supervised":
            supervisor._launch(store, tmp_path, "python", launch_child, "trader", NOW)
        else:
            launch_child(None)
        assert events == ["collector_started", "cycle", "collector_closed", "gateway_closed"]
        assert store.get("recovery_required") == expected
        assert store.get("control") == control
        assert store.get("resume_authorized_day") == "2026-09-11"
    finally:
        store.close()


def test_crash_restarts_after_30_seconds_not_immediately(tmp_path):
    clock = FakeClock()

    def behavior(role, count):
        if role == "trader" and count == 0:
            return FakeProcess(returncode=7)
        return FakeProcess()

    factory = Factory(clock, behavior)
    supervisor.supervise(tmp_path, "python", clock, factory, StopAt(clock, 35))

    trader_calls = [call for call in factory.calls if call[0] == "trader"]
    assert len(trader_calls) == 2
    assert (trader_calls[1][1] - trader_calls[0][1]).total_seconds() >= 30
    assert read_setting(tmp_path, "supervisor:restart_attempts")["trader"] == {
        "day": "2026-09-14",
        "count": 1,
    }


def test_stale_child_gets_normal_stop_then_only_owned_process_is_terminated(tmp_path):
    clock = FakeClock()
    factory = Factory(clock)

    supervisor.supervise(tmp_path, "python", clock, factory, StopAt(clock, 220))

    first_processes = {role: process for role, process in factory.processes[:3]}
    assert all(process.terminate_calls == 1 for process in first_processes.values())
    for role in supervisor.ROLES:
        reason = read_setting(tmp_path, f"role_stop_reason:{role}")
        # The final supervisor stop may supersede the stale reason, but the child
        # state and restart count prove stale handling completed first.
        assert reason["reason"] in {"heartbeat_stale", "supervisor_stop"}
        assert read_setting(tmp_path, "supervisor:restart_attempts")[role]["count"] == 1


def test_immediate_lock_like_exits_are_bounded_to_three_restarts_per_role(tmp_path):
    clock = FakeClock()
    factory = Factory(clock, lambda role, count: FakeProcess(returncode=2))

    supervisor.supervise(tmp_path, "python", clock, factory, StopAt(clock, 220))

    for role in supervisor.ROLES:
        assert sum(call[0] == role for call in factory.calls) == 4
        assert read_setting(tmp_path, "supervisor:restart_attempts")[role]["count"] == 3
        assert read_setting(tmp_path, f"supervisor:role:{role}")["status"] == (
            "restart_exhausted"
        )
    status = supervisor.supervisor_status(tmp_path)
    assert status["status"] == "blocked"
    assert status["reason"] == "restart_exhausted"


def test_restart_counter_persists_across_supervisor_recovery(tmp_path):
    store = Store(tmp_path / "experiment.sqlite3")
    try:
        store.put(
            "supervisor:identity",
            {"pid": 999999, "birthmarker": "old", "status": "running"},
        )
        store.put(
            "supervisor:restart_attempts",
            {"trader": {"day": "2026-09-14", "count": 2}},
        )
    finally:
        store.close()
    clock = FakeClock()
    factory = Factory(clock)

    supervisor.supervise(tmp_path, "python", clock, factory, StopAt(clock, 1))

    assert factory.calls == []
    assert read_setting(tmp_path, "supervisor:restart_attempts")["trader"]["count"] == 3
    identity = read_setting(tmp_path, "supervisor:identity")
    assert identity["pid"] != 999999
    assert identity["birthmarker"] != "old"


def test_restart_counter_resets_on_kst_day_rollover(tmp_path):
    store = Store(tmp_path / "experiment.sqlite3")
    try:
        store.put(
            "supervisor:identity",
            {"pid": 999999, "birthmarker": "old", "status": "running"},
        )
        store.put(
            "supervisor:restart_attempts",
            {"trader": {"day": "2026-09-13", "count": 3}},
        )
    finally:
        store.close()
    clock = FakeClock()

    supervisor.supervise(tmp_path, "python", clock, Factory(clock), StopAt(clock, 1))

    assert read_setting(tmp_path, "supervisor:restart_attempts")["trader"] == {
        "day": "2026-09-14",
        "count": 1,
    }


def test_duplicate_supervisor_lock_is_rejected(tmp_path):
    lock_path = tmp_path / "supervisor.lock"
    with supervisor._singleton_lock(lock_path):
        with pytest.raises(
            supervisor.SupervisorAlreadyRunning, match="supervisor_running"
        ):
            with supervisor._singleton_lock(lock_path):
                pass


def test_worker_launch_failure_is_explicitly_blocked_in_status(tmp_path):
    clock = FakeClock()

    def behavior(role, count):
        if role == "worker":
            return OSError("fixture unavailable")
        return FakeProcess()

    supervisor.supervise(
        tmp_path, "python", clock, Factory(clock, behavior), StopAt(clock, 1)
    )

    status = supervisor.supervisor_status(tmp_path)
    assert status["status"] == "blocked"
    assert status["reason"] == "worker_unavailable"
    assert status["roles"]["worker"]["status"] == "worker_unavailable"


def test_status_does_not_call_a_dead_supervisor_running(tmp_path):
    store = Store(tmp_path/"experiment.sqlite3")
    store.put("supervisor:identity", {"status": "running", "pid": 999999, "birthmarker": "fixture"})
    store.put("supervisor_heartbeat", NOW.isoformat())
    result = supervisor._status_from_store(store, NOW+timedelta(seconds=181))
    assert result["status"] == "blocked" and result["reason"] == "supervisor_heartbeat_stale"
    store.close()


def test_native_child_factory_uses_hidden_exact_owned_command(tmp_path, monkeypatch):
    captured = []
    def popen(command, **kwargs):
        captured.append((command, kwargs))
        return FakeProcess()
    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    command = ["fixture-python", "-m", "quantpilot.paper", "--runtime-dir", str(tmp_path), "start"]
    result = supervisor._spawn_hidden(command)
    assert isinstance(result, FakeProcess) and captured[0][0] == command
    assert captured[0][1]["stdin"] == supervisor.subprocess.DEVNULL
    if supervisor.os.name == "nt":
        assert captured[0][1]["creationflags"] == supervisor.subprocess.CREATE_NO_WINDOW
    assert len(list((tmp_path/"logs").glob("*.log"))) == 2
