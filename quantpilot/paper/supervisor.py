"""Conservative, opt-in lifecycle supervision for paper-only processes.

The supervisor owns only children it created.  Durable settings are coordination
requests, not trading instructions: this module never changes ``control`` or any
loss/recovery authorization gate.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from quantpilot.paper.config import environment_safe
from quantpilot.paper.store import Store


ROLES = ("trader", "worker", "reporter")
ROLE_COMMAND = {"trader": "start", "worker": "worker", "reporter": "reporter"}
HEARTBEAT_KEY = {
    "trader": "heartbeat",
    "worker": "worker_heartbeat",
    "reporter": "reporter_heartbeat",
}
RESTART_DELAYS = (30, 60, 120)
HEARTBEAT_STALE_SECONDS = 180
STOP_GRACE_SECONDS = 30
POLL_SECONDS = 1
KST = ZoneInfo("Asia/Seoul")


class SupervisorAlreadyRunning(ValueError):
    pass


@contextmanager
def _singleton_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SupervisorAlreadyRunning("supervisor_running") from exc
        yield
    finally:
        handle.close()


def _now(clock) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_aware_clock_required")
    return value


def _stop_requested(stop) -> bool:
    if stop is None:
        return False
    if hasattr(stop, "is_set"):
        return bool(stop.is_set())
    if callable(stop):
        return bool(stop())
    return bool(stop)


def _wait(clock, stop, seconds: float) -> None:
    if hasattr(clock, "sleep"):
        clock.sleep(seconds)
    elif stop is not None and hasattr(stop, "wait"):
        stop.wait(seconds)
    else:
        time.sleep(seconds)


def _day(now: datetime) -> str:
    return now.astimezone(KST).date().isoformat()


def _parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _role_state(store: Store, role: str) -> dict:
    value = store.get(f"supervisor:role:{role}", {})
    return value if isinstance(value, dict) else {}


def _write_role_state(store: Store, role: str, **changes) -> dict:
    state = _role_state(store, role) | changes
    store.put(f"supervisor:role:{role}", state)
    return state


def _attempts(store: Store) -> dict:
    value = store.get("supervisor:restart_attempts", {})
    return value if isinstance(value, dict) else {}


def _attempt_record(store: Store, role: str, now: datetime) -> dict:
    attempts = _attempts(store)
    record = attempts.get(role, {})
    today = _day(now)
    if not isinstance(record, dict) or record.get("day") != today:
        record = {"day": today, "count": 0}
        attempts[role] = record
        store.put("supervisor:restart_attempts", attempts)
    return record


def _schedule_restart(store: Store, role: str, now: datetime, reason: str) -> bool:
    record = _attempt_record(store, role, now)
    attempts = _attempts(store)
    count = int(record.get("count", 0))
    if count >= len(RESTART_DELAYS):
        _write_role_state(
            store,
            role,
            status="restart_exhausted",
            reason=reason,
            next_start_at=None,
        )
        return False
    delay = RESTART_DELAYS[count]
    attempts[role] = {"day": _day(now), "count": count + 1}
    store.put("supervisor:restart_attempts", attempts)
    _write_role_state(
        store,
        role,
        status="restart_scheduled",
        reason=reason,
        next_start_at=(now + timedelta(seconds=delay)).isoformat(),
    )
    return True


def _request_role_stop(store: Store, role: str, now: datetime, reason: str) -> None:
    store.put(f"role_stop:{role}", True)
    store.put(
        f"role_stop_reason:{role}",
        {"reason": reason, "requested_at": now.isoformat()},
    )


def _clear_role_stop(store: Store, role: str) -> None:
    store.put(f"role_stop:{role}", False)
    store.put(f"role_stop_reason:{role}", None)


def _mark_recovery_required(store: Store, now: datetime) -> None:
    control = store.get("control")
    store.put(
        "recovery_required",
        {
            "required": True,
            "detected_at": now.isoformat(),
            "previous_control": control,
            "previous_running": control == "running",
            "resume_authorized_day": store.get("resume_authorized_day"),
        },
    )


def _launch(
    store: Store,
    runtime_dir: Path,
    python: str | Path,
    process_factory,
    role: str,
    now: datetime,
):
    _clear_role_stop(store, role)
    if role == "trader":
        _mark_recovery_required(store, now)
    command = [
        str(python),
        "-m",
        "quantpilot.paper",
        "--runtime-dir",
        str(runtime_dir),
        ROLE_COMMAND[role],
    ]
    process = process_factory(command)
    _write_role_state(
        store,
        role,
        status="running",
        launched_at=now.isoformat(),
        next_start_at=None,
        reason=None,
    )
    store.audit("supervisor_child_started", {"role": role}, now)
    return process


def _status_from_store(store: Store, now=None) -> dict:
    roles = {role: _role_state(store, role) for role in ROLES}
    identity = store.get("supervisor:identity")
    exhausted = [
        role
        for role, state in roles.items()
        if state.get("status") == "restart_exhausted"
    ]
    worker_unavailable = roles["worker"].get("status") == "worker_unavailable"
    if worker_unavailable:
        status, reason = "blocked", "worker_unavailable"
    elif exhausted:
        status, reason = "blocked", "restart_exhausted"
    elif isinstance(identity, dict) and identity.get("status") == "running":
        heartbeat = _parse_timestamp(store.get("supervisor_heartbeat"))
        checked_at = now or datetime.now(timezone.utc)
        if heartbeat is not None and 0 <= (checked_at - heartbeat).total_seconds() <= HEARTBEAT_STALE_SECONDS:
            status, reason = "running", None
        else:
            status, reason = "blocked", "supervisor_heartbeat_stale"
    else:
        status, reason = "stopped", None
    return {
        "status": status,
        "reason": reason,
        "identity": identity,
        "roles": roles,
        "restart_attempts": _attempts(store),
        "exhausted_roles": exhausted,
        "heartbeat": store.get("supervisor_heartbeat"),
    }


def supervisor_status(runtime_dir: str | Path) -> dict:
    directory = Path(runtime_dir).expanduser().resolve()
    from quantpilot.paper.dashboard import ledger
    with ledger(directory / "experiment.sqlite3") as store:
        return _status_from_store(store)


def request_supervisor_stop(runtime_dir: str | Path, clock=None) -> dict:
    directory = Path(runtime_dir).expanduser().resolve()
    now = _now(clock or (lambda: datetime.now(timezone.utc)))
    store = Store(directory / "experiment.sqlite3")
    try:
        store.put("supervisor:stop_requested", True)
        for role in ROLES:
            _request_role_stop(store, role, now, "supervisor_stop")
        store.audit("supervisor_stop_requested", {"roles": list(ROLES)}, now)
        return {"status": "stop_requested", "roles": list(ROLES)}
    finally:
        store.close()


def supervise(runtime_dir, python, clock, process_factory, stop):
    """Run the blocking supervisor loop with injectable process and time adapters."""

    directory = Path(runtime_dir).expanduser().resolve()
    with _singleton_lock(directory / "supervisor.lock"):
        store = Store(directory / "experiment.sqlite3")
        children = {role: None for role in ROLES}
        stop_deadlines: dict[str, datetime] = {}
        birthmarker = uuid4().hex
        try:
            if not getattr(store.policy, "supervisor_enabled", False):
                return {"status": "blocked", "reason": "supervisor_disabled"}
            if store.policy.data_mode != "paper_trading":
                return {"status": "blocked", "reason": "paper_profile_required"}
            if not environment_safe(os.environ):
                return {"status": "blocked", "reason": "unsafe_environment"}

            now = _now(clock)
            previous_identity = store.get("supervisor:identity")
            recovering = (
                isinstance(previous_identity, dict)
                and previous_identity.get("status") == "running"
            )
            identity = {
                "pid": os.getpid(),
                "birthmarker": birthmarker,
                "started_at": now.isoformat(),
                "status": "running",
            }
            store.put("supervisor:identity", identity)
            store.put("supervisor:stop_requested", False)
            for role in ROLES:
                store.put(f"supervised:{role}", True)
                _clear_role_stop(store, role)
                _attempt_record(store, role, now)
                state = _role_state(store, role)
                if state.get("status") == "restart_exhausted" and int(_attempt_record(store, role, now).get("count", 0)) >= 3:
                    continue
                scheduled = _parse_timestamp(state.get("next_start_at"))
                if scheduled is not None and scheduled > now:
                    continue
                if recovering and scheduled is None:
                    _schedule_restart(store, role, now, "supervisor_recovered")
                    continue
                try:
                    children[role] = _launch(
                        store, directory, python, process_factory, role, now
                    )
                except Exception:
                    status = (
                        "worker_unavailable" if role == "worker" else "launch_failed"
                    )
                    _schedule_restart(store, role, now, "launch_failed")
                    if status == "worker_unavailable" and _role_state(store, role).get(
                        "status"
                    ) != "restart_exhausted":
                        _write_role_state(
                            store, role, status=status, reason="launch_failed"
                        )

            shutting_down = False
            shutdown_deadline = None
            while True:
                now = _now(clock)
                store.put("supervisor_heartbeat", now.isoformat())
                store.put("supervisor_status", _status_from_store(store, now))
                externally_stopped = _stop_requested(stop)
                ledger_stopped = bool(store.get("supervisor:stop_requested", False))
                if (externally_stopped or ledger_stopped) and not shutting_down:
                    shutting_down = True
                    shutdown_deadline = now + timedelta(seconds=STOP_GRACE_SECONDS)
                    for role in ROLES:
                        _request_role_stop(store, role, now, "supervisor_stop")

                for role in ROLES:
                    process = children[role]
                    if process is not None:
                        returncode = process.poll()
                        if returncode is not None:
                            previous_status = _role_state(store, role).get("status")
                            children[role] = None
                            stop_deadlines.pop(role, None)
                            _write_role_state(
                                store,
                                role,
                                status="stopped" if shutting_down else "exited",
                                returncode=returncode,
                                stopped_at=now.isoformat(),
                            )
                            stale_exit = previous_status in {
                                "stale_stop_requested",
                                "terminated_stale",
                            }
                            if not shutting_down and (
                                stale_exit
                                or not store.get(f"role_stop:{role}", False)
                            ):
                                _schedule_restart(store, role, now, "child_exited")
                            continue

                        launched_at = _parse_timestamp(
                            _role_state(store, role).get("launched_at")
                        ) or now
                        heartbeat = _parse_timestamp(store.get(HEARTBEAT_KEY[role]))
                        freshness = max(
                            value for value in (launched_at, heartbeat) if value is not None
                        )
                        if (
                            not shutting_down
                            and role not in stop_deadlines
                            and _role_state(store, role).get("status")
                            not in {"stale_stop_requested", "terminated_stale"}
                            and (now - freshness).total_seconds() > HEARTBEAT_STALE_SECONDS
                        ):
                            _request_role_stop(store, role, now, "heartbeat_stale")
                            stop_deadlines[role] = now + timedelta(
                                seconds=STOP_GRACE_SECONDS
                            )
                            _write_role_state(
                                store,
                                role,
                                status="stale_stop_requested",
                                reason="heartbeat_stale",
                            )
                        if role in stop_deadlines and now >= stop_deadlines[role]:
                            process.terminate()
                            stop_deadlines.pop(role, None)
                            _write_role_state(
                                store,
                                role,
                                status="terminated_stale",
                                reason="heartbeat_stale",
                            )

                    if children[role] is None and not shutting_down:
                        state = _role_state(store, role)
                        if state.get("status") == "restart_exhausted":
                            record = _attempt_record(store, role, now)
                            if int(record.get("count", 0)) == 0:
                                _schedule_restart(store, role, now, "day_rollover")
                            continue
                        due = _parse_timestamp(state.get("next_start_at"))
                        if due is not None and due <= now:
                            try:
                                children[role] = _launch(
                                    store, directory, python, process_factory, role, now
                                )
                            except Exception:
                                status = (
                                    "worker_unavailable"
                                    if role == "worker"
                                    else "launch_failed"
                                )
                                _schedule_restart(store, role, now, "launch_failed")
                                if status == "worker_unavailable" and _role_state(
                                    store, role
                                ).get("status") != "restart_exhausted":
                                    _write_role_state(
                                        store,
                                        role,
                                        status=status,
                                        reason="launch_failed",
                                    )

                if shutting_down:
                    live = [
                        process
                        for process in children.values()
                        if process is not None and process.poll() is None
                    ]
                    if not live:
                        break
                    if shutdown_deadline is not None and now >= shutdown_deadline:
                        for role, process in children.items():
                            if process is not None and process.poll() is None:
                                process.terminate()
                                _write_role_state(
                                    store,
                                    role,
                                    status="terminated_on_supervisor_stop",
                                    reason="supervisor_stop",
                                )
                        break
                _wait(clock, stop, POLL_SECONDS)

            return _status_from_store(store) | {"status": "stopped", "reason": None}
        except KeyboardInterrupt:
            return {"status": "stopped", "reason": "interrupted"}
        finally:
            # Exceptions use the same normal-stop grace as an explicit shutdown.
            live = {role: child for role, child in children.items() if child is not None and child.poll() is None}
            if live:
                deadline = _now(clock) + timedelta(seconds=STOP_GRACE_SECONDS)
                for role in live:
                    _request_role_stop(store, role, _now(clock), "supervisor_exiting")
                while any(p.poll() is None for p in live.values()) and _now(clock) < deadline:
                    _wait(clock, None, POLL_SECONDS)
                for child in live.values():
                    if child.poll() is None:
                        child.terminate()
            now = _now(clock)
            identity = store.get("supervisor:identity")
            if isinstance(identity, dict) and identity.get("birthmarker") == birthmarker:
                store.put(
                    "supervisor:identity",
                    identity | {"status": "stopped", "stopped_at": now.isoformat()},
                )
            for role in ROLES:
                store.put(f"supervised:{role}", False)
            store.put("supervisor_status", _status_from_store(store))
            store.close()


def _spawn_hidden(command):
    """Only invoked by an explicit supervisor start; never attach to arbitrary PIDs."""
    directory = Path(command[command.index("--runtime-dir") + 1])
    logs = directory / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stem = command[-1] + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    with (logs / (stem + ".out.log")).open("ab") as out, (logs / (stem + ".err.log")).open("ab") as err:
        return subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def _runtime_directory(value: Path) -> Path:
    directory = value.expanduser().resolve()
    if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
        raise ValueError("runtime_directory_inside_repository")
    return directory


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="QuantPilot paper process supervisor")
    result.add_argument(
        "--runtime-dir", type=Path, default=Path.home() / ".quantpilot" / "intraday"
    )
    result.add_argument("command", choices=("status", "stop", "start"))
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        directory = _runtime_directory(args.runtime_dir)
        if args.command == "status":
            result = supervisor_status(directory)
        elif args.command == "stop":
            result = request_supervisor_stop(directory)
        else:
            result = supervise(
                directory,
                sys.executable,
                lambda: datetime.now(timezone.utc),
                _spawn_hidden,
                threading.Event(),
            )
    except SupervisorAlreadyRunning:
        result = {"status": "blocked", "reason": "supervisor_running"}
    except Exception as exc:
        reason = str(exc)
        if not isinstance(exc, ValueError) or not reason.replace("_", "").isalnum():
            reason = type(exc).__name__
        result = {"status": "blocked", "reason": reason}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("status") not in {"blocked"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
