"""Durable, process-shared request pacing for the KIS paper boundary.

The database contains only a SHA-256 account-scope fingerprint and operational
timings.  Constructing a budget is explicit; importing this module never enables
or starts paper trading.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import json
from quantpilot.paper.calendar import KST
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from quantpilot.packages.core.kis_paper import (
    KisHttpResponse,
    KisPaperGatewayRejected,
    KisPaperTransportError,
    KisPaperConfigurationError,
)


DEFAULT_INTERVAL_SECONDS = 1.05


class BudgetNotSent(KisPaperConfigurationError):
    """The decorated transport was never invoked for this request."""

_SCOPE = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_DIAGNOSTIC = re.compile(r"[A-Za-z0-9_.-]{1,32}")
_HTTP_STATUS = re.compile(r"\bHTTP status ([1-5][0-9]{2})\b")
_PRIORITIES = {
    "minute": 10,
    "discovery": 10,
    "entry": 20,
    "reconcile": 30,
    "unknown": 30,
    "sell": 40,
    "cancel": 40,
    "query": 40,
    "queries": 40,
}


@dataclass(frozen=True)
class _RequestContext:
    priority: str
    before_send: Callable[[], Any] | None


@dataclass(frozen=True)
class BudgetAdmission:
    request_id: str
    priority: str
    queued_seconds: float
    admitted_at: float


class SharedBudget:
    """SQLite-backed, priority-aware request scheduler shared by processes."""

    def __init__(
        self,
        path: str | Path,
        scope: str,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        observer: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        if not isinstance(scope, str) or _SCOPE.fullmatch(scope) is None:
            raise ValueError("budget scope must be a safe SHA-256 account fingerprint")
        if isinstance(interval, bool) or not isinstance(interval, (int, float)):
            raise TypeError("budget interval must be numeric")
        if not math.isfinite(float(interval)) or interval <= 0:
            raise ValueError("budget interval must be finite and positive")
        database = Path(path).expanduser().resolve(strict=False)
        if database.exists() and database.is_dir():
            raise ValueError("budget database path must name a file")
        if not database.parent.exists():
            raise ValueError("budget database parent must already exist")
        if any((parent / ".git").exists() for parent in (database.parent, *database.parents)):
            raise ValueError("budget database must be outside a Git worktree")

        self.path = database
        self.scope = scope
        self.interval = float(interval)
        self.clock = clock
        self.sleep = sleep
        self.observer = observer
        self._local = threading.local()
        self._waiter_ttl = max(30.0, self.interval * 10.0)
        # WAL setup itself needs an exclusive transition on a brand-new file.
        # Use the same cross-process mutex that later protects send starts.
        deadline = time.monotonic() + 30
        while True:
            with self._sending() as acquired:
                if acquired:
                    self._initialize()
                    break
            if time.monotonic() >= deadline:
                raise TimeoutError("budget_initialization_busy")
            time.sleep(0.02)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            str(self.path),
            timeout=max(5.0, self.interval * 5.0),
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_budget_state (
                    scope TEXT PRIMARY KEY,
                    next_at REAL NOT NULL,
                    cooldown_until REAL NOT NULL,
                    send_after REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS api_budget_waiters (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE,
                    priority INTEGER NOT NULL,
                    priority_name TEXT NOT NULL,
                    enqueued_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS api_budget_waiters_order
                    ON api_budget_waiters(scope, priority DESC, sequence ASC);
                CREATE TABLE IF NOT EXISTS api_requests(
                    request_id TEXT PRIMARY KEY, scope TEXT NOT NULL, at TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS api_budget_daily (
                    scope TEXT NOT NULL,
                    day TEXT NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0,
                    queue_seconds REAL NOT NULL DEFAULT 0,
                    processing_seconds REAL NOT NULL DEFAULT 0,
                    retries INTEGER NOT NULL DEFAULT 0,
                    rate_violations INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (scope, day)
                );
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(api_budget_state)")
            }
            if "send_after" not in columns:
                connection.execute(
                    "ALTER TABLE api_budget_state ADD COLUMN send_after REAL NOT NULL DEFAULT 0"
                )
            connection.execute(
                "INSERT OR IGNORE INTO api_budget_state(scope, next_at, cooldown_until) "
                "VALUES (?, 0, 0)",
                (self.scope,),
            )

    @contextmanager
    def context(
        self,
        priority: str = "unknown",
        before_send: Callable[[], Any] | None = None,
    ) -> Iterator[None]:
        """Attach priority and a last-moment safety callback to this thread."""

        normalized = self._normalize_priority(priority)
        if before_send is not None and not callable(before_send):
            raise TypeError("before_send must be callable")
        previous = getattr(self._local, "request_context", None)
        self._local.request_context = _RequestContext(normalized, before_send)
        try:
            yield
        finally:
            if previous is None:
                try:
                    del self._local.request_context
                except AttributeError:
                    pass
            else:
                self._local.request_context = previous

    def _current_context(self) -> _RequestContext:
        return getattr(self._local, "request_context", _RequestContext("unknown", None))

    @staticmethod
    def _normalize_priority(priority: str) -> str:
        if not isinstance(priority, str):
            raise TypeError("budget priority must be a string")
        normalized = priority.strip().lower()
        if normalized not in _PRIORITIES:
            raise ValueError("unsupported budget priority")
        return normalized

    def acquire(self, priority: str = "unknown") -> BudgetAdmission:
        """Wait for and durably reserve one slot; a crash cannot release it early."""

        normalized = self._normalize_priority(priority)
        request_id = uuid4().hex
        enqueued_at = float(self.clock())
        if not math.isfinite(enqueued_at):
            raise ValueError("budget clock must return a finite timestamp")
        self._enqueue(request_id, normalized, enqueued_at)
        try:
            while True:
                now = float(self.clock())
                if not math.isfinite(now):
                    raise ValueError("budget clock must return a finite timestamp")
                admitted, wait_for = self._try_admit(request_id, normalized, enqueued_at, now)
                if admitted:
                    return BudgetAdmission(
                        request_id=request_id,
                        priority=normalized,
                        queued_seconds=max(0.0, now - enqueued_at),
                        admitted_at=now,
                    )
                self.sleep(max(0.001, min(wait_for, self._waiter_ttl / 2.0)))
        except BaseException:
            self._remove_waiter(request_id)
            raise

    def _enqueue(self, request_id: str, priority: str, now: float) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO api_budget_waiters"
                "(scope, request_id, priority, priority_name, enqueued_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self.scope,
                    request_id,
                    _PRIORITIES[priority],
                    priority,
                    now,
                    now + self._waiter_ttl,
                ),
            )
            connection.commit()

    def _try_admit(
        self,
        request_id: str,
        priority: str,
        enqueued_at: float,
        now: float,
    ) -> tuple[bool, float]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM api_budget_waiters WHERE scope = ? AND request_id != ? "
                "AND expires_at <= ?",
                (self.scope, request_id, now),
            )
            refreshed = connection.execute(
                "UPDATE api_budget_waiters SET expires_at = ? "
                "WHERE scope = ? AND request_id = ?",
                (now + self._waiter_ttl, self.scope, request_id),
            )
            if refreshed.rowcount != 1:
                connection.rollback()
                raise RuntimeError("budget waiter expired before admission")
            first = connection.execute(
                "SELECT request_id FROM api_budget_waiters WHERE scope = ? "
                "ORDER BY priority DESC, sequence ASC LIMIT 1",
                (self.scope,),
            ).fetchone()
            state = connection.execute(
                "SELECT next_at, cooldown_until, send_after FROM api_budget_state WHERE scope = ?",
                (self.scope,),
            ).fetchone()
            ready_at = max(float(state["next_at"]), float(state["cooldown_until"]), float(state["send_after"]))
            if first["request_id"] != request_id or now < ready_at:
                connection.commit()
                return False, max(0.001, ready_at - now if now < ready_at else 0.01)

            connection.execute(
                "DELETE FROM api_budget_waiters WHERE scope = ? AND request_id = ?",
                (self.scope, request_id),
            )
            connection.execute(
                "UPDATE api_budget_state SET next_at = ? WHERE scope = ?",
                (now + self.interval, self.scope),
            )
            day = self._day(now)
            connection.execute(
                "INSERT INTO api_budget_daily(scope, day, requests, queue_seconds) "
                "VALUES (?, ?, 1, ?) "
                "ON CONFLICT(scope, day) DO UPDATE SET "
                "requests = requests + 1, queue_seconds = queue_seconds + excluded.queue_seconds",
                (self.scope, day, max(0.0, now - enqueued_at)),
            )
            connection.commit()
            return True, 0.0

    def _remove_waiter(self, request_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM api_budget_waiters WHERE scope = ? AND request_id = ?",
                    (self.scope, request_id),
                )
        except sqlite3.Error:
            # The lease expiry remains a bounded cleanup fallback if SQLite itself failed.
            pass

    def share_cooldown(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("cooldown must be finite and positive")
        now = float(self.clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE api_budget_state SET cooldown_until = MAX(cooldown_until, ?) "
                "WHERE scope = ?",
                (now + seconds, self.scope),
            )
            connection.commit()

    @contextmanager
    def _sending(self) -> Iterator[bool]:
        """Serialize actual starts across processes, including transport failures.

        The SQLite reservation survives a crash; this small adjacent lock ensures a
        process descheduled after reservation cannot reorder actual transport starts.
        """

        lock_path = self.path.with_name(self.path.name + ".send.lock")
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0, 2)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                import msvcrt

                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    yield False
                    return
                unlock = lambda: msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:  # pragma: no cover - Windows is the production host.
                import fcntl

                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    yield False
                    return
                unlock = lambda: fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            try:
                yield True
            finally:
                lock_file.seek(0)
                unlock()

    @contextmanager
    def send_admission(self, priority):
        # Keep unsent callers in the priority queue even during a slow transport.
        priority = self._normalize_priority(priority)
        request_id = uuid4().hex
        enqueued = float(self.clock())
        if not math.isfinite(enqueued):
            raise ValueError("budget clock must return a finite timestamp")
        deadline = time.monotonic() + 120
        refresh_at = enqueued + self._waiter_ttl / 3
        self._enqueue(request_id, priority, enqueued)
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("budget_queue_timeout")
                wait_for = 0.02
                with self._sending() as acquired:
                    if acquired:
                        now = float(self.clock())
                        if not math.isfinite(now):
                            raise ValueError("budget clock must return a finite timestamp")
                        admitted, wait_for = self._try_admit(request_id, priority, enqueued, now)
                        if admitted:
                            yield BudgetAdmission(request_id, priority, max(0, now-enqueued), now)
                            return
                self.sleep(max(0.001, min(wait_for, self._waiter_ttl / 2)))
                # Refresh our lease while another process owns the wire.
                if self.clock() >= refresh_at:
                    with self._connect() as db:
                        db.execute("UPDATE api_budget_waiters SET expires_at=? WHERE request_id=?",
                                   (self.clock() + self._waiter_ttl, request_id))
                    refresh_at = self.clock() + self._waiter_ttl / 3
        finally:
            self._remove_waiter(request_id)

    def record_request(self, event):
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO api_requests VALUES(?,?,?,?)",
                       (event["request_id"], self.scope, event["received_at"], json.dumps(event, separators=(",", ":"))))

    def _wait_for_send_turn(self) -> None:
        while True:
            now = float(self.clock())
            with self._connect() as connection:
                send_after = float(
                    connection.execute(
                        "SELECT send_after FROM api_budget_state WHERE scope = ?",
                        (self.scope,),
                    ).fetchone()[0]
                )
            remaining = send_after - now
            if remaining <= 0:
                return
            self.sleep(remaining)

    def _mark_send(self, timestamp: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE api_budget_state SET send_after = MAX(send_after, ?) WHERE scope = ?",
                (timestamp + self.interval, self.scope),
            )

    def _finish_send(self, timestamp: float) -> None:
        # Spacing from completion is deliberately conservative and prevents a
        # zero-duration follower from bursting after a slow request.
        self._mark_send(timestamp)

    def record_processing(
        self,
        admitted_at: float,
        processing_seconds: float,
        *,
        retry: bool = False,
        rate_violation: bool = False,
    ) -> None:
        day = self._day(admitted_at)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO api_budget_daily"
                "(scope, day, processing_seconds, retries, rate_violations) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(scope, day) DO UPDATE SET "
                "processing_seconds = processing_seconds + excluded.processing_seconds, "
                "retries = retries + excluded.retries, "
                "rate_violations = rate_violations + excluded.rate_violations",
                (
                    self.scope,
                    day,
                    max(0.0, processing_seconds),
                    int(retry),
                    int(rate_violation),
                ),
            )

    def snapshot(self) -> dict[str, Any]:
        """Return secret-free queue state and aggregate daily diagnostics."""

        now = float(self.clock())
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM api_budget_waiters WHERE scope = ? AND expires_at <= ?",
                (self.scope, now),
            )
            queue_depth = int(
                connection.execute(
                    "SELECT COUNT(*) FROM api_budget_waiters WHERE scope = ?",
                    (self.scope,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT day, requests, queue_seconds, processing_seconds, retries, "
                "rate_violations FROM api_budget_daily WHERE scope = ? ORDER BY day",
                (self.scope,),
            ).fetchall()
        return {
            "scope": self.scope,
            "interval_seconds": self.interval,
            "queue_depth": queue_depth,
            "days": {
                row["day"]: {
                    "requests": int(row["requests"]),
                    "queue_seconds": float(row["queue_seconds"]),
                    "processing_seconds": float(row["processing_seconds"]),
                    "retries": int(row["retries"]),
                    "rate_violations": int(row["rate_violations"]),
                }
                for row in rows
            },
        }

    @staticmethod
    def _day(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, KST).date().isoformat()


class BudgetTransport:
    """KIS JSON transport decorator with durable pacing and safe GET retries."""

    def __init__(
        self,
        transport: Any,
        budget: SharedBudget,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not hasattr(transport, "request_json"):
            raise TypeError("transport must provide request_json")
        if not isinstance(budget, SharedBudget):
            raise TypeError("budget must be a SharedBudget")
        self.transport = transport
        self.budget = budget
        self.clock = clock or budget.clock

    def request_json(self, method: str, url: str, **kwargs: Any) -> KisHttpResponse:
        wire_started = [False]
        try:
            return self._request_json(method, url, wire_started, **kwargs)
        except Exception:
            if method.upper() == "POST" and not wire_started[0]:
                raise BudgetNotSent("budget_pre_transport_refused") from None
            raise

    def _request_json(self, method, url, wire_started, **kwargs):
        context = self.budget._current_context()
        if context.priority == "unknown" and "/quotations/" in url:
            context = _RequestContext("minute", context.before_send)
        retries = 0
        while True:
            callback_failure: BaseException | None = None
            with self.budget.send_admission(context.priority) as admission:
                self.budget._wait_for_send_turn()
                if context.before_send is not None:
                    # The slot remains reserved if this last-moment check expires.
                    try:
                        context.before_send()
                    except BaseException as exc:
                        callback_failure = exc
                if callback_failure is None:
                    started = float(self.clock())
                    self.budget._mark_send(started)
                    response: KisHttpResponse | None = None
                    failure: BaseException | None = None
                    try:
                        wire_started[0] = True
                        response = self.transport.request_json(method, url, **kwargs)
                    except BaseException as exc:
                        failure = exc
                    finished = float(self.clock())
                    self.budget._finish_send(finished)
            if callback_failure is not None:
                self.budget.record_processing(admission.admitted_at, 0.0)
                self._observe(
                    method=method,
                    headers=kwargs.get("headers"),
                    admission=admission,
                    processing_seconds=0.0,
                    response=None,
                    failure=callback_failure,
                    stage="before_send",
                )
                raise callback_failure
            elapsed = max(0.0, finished - started)
            rate_violation = self._is_rate_violation(response, failure)
            will_retry = method.upper() == "GET" and rate_violation and retries < 2
            if rate_violation:
                # A rejected POST is never replayed, but every peer must cool down.
                self.budget.share_cooldown(self.budget.interval * (2 ** retries))
            self.budget.record_processing(
                admission.admitted_at,
                elapsed,
                retry=will_retry,
                rate_violation=rate_violation,
            )
            self._observe(
                method=method,
                headers=kwargs.get("headers"),
                admission=admission,
                processing_seconds=elapsed,
                response=response,
                failure=failure,
                stage="retry" if will_retry else ("error" if failure else "response"),
            )
            if will_retry:
                retries += 1
                continue
            if failure is not None:
                raise failure
            if response is None:
                raise RuntimeError("transport returned no response")
            return response

    @staticmethod
    def _is_rate_violation(
        response: KisHttpResponse | None,
        failure: BaseException | None,
    ) -> bool:
        if response is not None:
            if response.status_code == 429:
                return True
            return str(response.payload.get("msg_cd", "")).strip() == "EGW00201"
        if isinstance(failure, KisPaperGatewayRejected):
            return getattr(failure, "code", None) == "EGW00201"
        return isinstance(failure, KisPaperTransportError) and (
            BudgetTransport._failure_http_status(failure) == 429
        )

    def _observe(
        self,
        *,
        method: str,
        headers: object,
        admission: BudgetAdmission,
        processing_seconds: float,
        response: KisHttpResponse | None,
        failure: BaseException | None,
        stage: str,
    ) -> None:
        header_map = headers if isinstance(headers, Mapping) else {}
        raw_tr = header_map.get("tr_id")
        tr_id = raw_tr if isinstance(raw_tr, str) and re.fullmatch(r"[A-Z][A-Z0-9]{5,19}", raw_tr) else None
        status = response.status_code if response is not None else self._failure_http_status(failure)
        code: object = None
        if response is not None:
            code = response.payload.get("msg_cd")
        elif failure is not None:
            code = getattr(failure, "code", None)
        event = {
            "http_status": status,
            "broker_code": self._safe_value(code),
            "method": method.upper() if method.upper() in {"GET", "POST"} else "UNKNOWN",
            "tr_id": tr_id,
            "stage": stage,
            "request_id": admission.request_id,
            "queue_seconds": admission.queued_seconds,
            "processing_seconds": processing_seconds,
            "occurred_at": datetime.fromtimestamp(admission.admitted_at, timezone.utc).isoformat(),
            "received_at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
        }
        self.budget.record_request(event)
        if self.budget.observer is not None:
            self.budget.observer(event)

    @staticmethod
    def _safe_value(value: object) -> str | None:
        text = str(value or "").strip()
        return text if text in {"0", "90070000"} or re.fullmatch(r"(?:EGW|APBK|OPSQ)[0-9]{4,5}", text) else None

    @staticmethod
    def _failure_http_status(failure: BaseException | None) -> int | None:
        if failure is None:
            return None
        status = getattr(failure, "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            return status
        match = _HTTP_STATUS.search(str(failure))
        return int(match.group(1)) if match else None
