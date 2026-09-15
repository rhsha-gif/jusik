"""Offline acceptance tests for the cross-process KIS paper request budget."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
import threading
import time

import pytest

from quantpilot.packages.core.kis_paper import (
    KisHttpResponse,
    KisPaperGatewayRejected,
    KisPaperTransportError,
)
from quantpilot.paper.api_budget import BudgetNotSent, BudgetTransport, SharedBudget


SCOPE = "sha256:" + "a" * 64


class FakeClock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class SequenceTransport:
    def __init__(self, outcomes, clock=None):
        self.outcomes = list(outcomes)
        self.clock = clock
        self.starts = []

    def request_json(self, method, url, **kwargs):
        self.starts.append(self.clock() if self.clock else time.time())
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def response(status=200, code="0"):
    return KisHttpResponse(status, {"rt_cd": "0", "msg_cd": code})


def test_scope_and_database_must_be_secret_free_and_outside_git(tmp_path):
    with pytest.raises(ValueError, match="SHA-256"):
        SharedBudget(tmp_path / "unsafe.sqlite3", "account-123")
    repository_database = Path(__file__).resolve().parents[2] / "unsafe.sqlite3"
    with pytest.raises(ValueError, match="outside a Git"):
        SharedBudget(repository_database, SCOPE)
    assert not repository_database.exists()


def test_deterministic_spacing_callback_and_daily_snapshot(tmp_path):
    clock = FakeClock()
    events = []
    checks = []
    budget = SharedBudget(
        tmp_path / "budget.sqlite3",
        SCOPE,
        interval=1.05,
        clock=clock,
        sleep=clock.sleep,
        observer=events.append,
    )
    raw = SequenceTransport([response(), response()], clock)
    transport = BudgetTransport(raw, budget)

    with budget.context(priority="query", before_send=lambda: checks.append(clock())):
        transport.request_json("GET", "https://fixture.invalid/a", headers={"tr_id": "FHKST01010100"})
        transport.request_json("GET", "https://fixture.invalid/b", headers={"tr_id": "FHKST01010200"})

    assert raw.starts == checks
    assert raw.starts[1] - raw.starts[0] == pytest.approx(1.05)
    day = budget.snapshot()["days"]["2023-11-15"]
    assert day == {
        "requests": 2,
        "queue_seconds": pytest.approx(1.05),
        "processing_seconds": 0.0,
        "retries": 0,
        "rate_violations": 0,
    }
    assert events[0] == {
        "http_status": 200,
        "broker_code": "0",
        "method": "GET",
        "tr_id": "FHKST01010100",
        "stage": "response",
        "request_id": events[0]["request_id"],
        "queue_seconds": 0.0,
        "processing_seconds": 0.0,
        "occurred_at": events[0]["occurred_at"],
        "received_at": events[0]["received_at"],
    }


def test_expired_before_send_emits_no_transport_and_cleans_queue(tmp_path):
    clock = FakeClock()
    budget = SharedBudget(
        tmp_path / "budget.sqlite3", SCOPE, interval=1, clock=clock, sleep=clock.sleep
    )
    raw = SequenceTransport([response()], clock)
    transport = BudgetTransport(raw, budget)

    def expired():
        raise RuntimeError("risk_snapshot_expired")

    with pytest.raises(BudgetNotSent, match="budget_pre_transport_refused"):
        with budget.context(priority="entry", before_send=expired):
            transport.request_json("POST", "https://fixture.invalid/order", headers={})

    assert raw.starts == []
    assert budget.snapshot()["queue_depth"] == 0
    assert budget.snapshot()["days"]["2023-11-15"]["requests"] == 1


def test_crash_reservation_survives_and_abandoned_waiter_expires(tmp_path):
    clock = FakeClock()
    path = tmp_path / "budget.sqlite3"
    crashed = SharedBudget(path, SCOPE, interval=1, clock=clock, sleep=clock.sleep)
    crashed.acquire("query")  # Simulate a process dying after its durable reservation.
    crashed._enqueue("abandoned", "cancel", clock() - crashed._waiter_ttl - 1)

    replacement = SharedBudget(path, SCOPE, interval=1, clock=clock, sleep=clock.sleep)
    raw = SequenceTransport([response()], clock)
    BudgetTransport(raw, replacement).request_json(
        "GET", "https://fixture.invalid/read", headers={}
    )

    assert raw.starts == [1_700_000_001.0]
    assert replacement.snapshot()["queue_depth"] == 0


@pytest.mark.parametrize("limited", [response(429), response(500, "EGW00201")])
def test_get_retries_only_proven_limits_with_shared_backoff(tmp_path, limited):
    clock = FakeClock()
    checks = []
    budget = SharedBudget(
        tmp_path / "budget.sqlite3", SCOPE, interval=1, clock=clock, sleep=clock.sleep
    )
    raw = SequenceTransport([limited, response()], clock)
    wrapped = BudgetTransport(raw, budget)
    with budget.context(priority="query", before_send=lambda: checks.append(clock())):
        assert wrapped.request_json("GET", "https://fixture.invalid/read", headers={}).status_code == 200
    assert raw.starts == checks == [1_700_000_000.0, 1_700_000_001.0]
    day = budget.snapshot()["days"]["2023-11-15"]
    assert (day["requests"], day["retries"], day["rate_violations"]) == (2, 1, 1)


def test_gateway_limit_retries_but_post_and_unproven_failure_never_retry(tmp_path):
    clock = FakeClock()
    budget = SharedBudget(
        tmp_path / "budget.sqlite3", SCOPE, interval=0.1, clock=clock, sleep=clock.sleep
    )
    gateway = KisPaperGatewayRejected("limited", code="EGW00201")
    get_raw = SequenceTransport([gateway, response()], clock)
    assert BudgetTransport(get_raw, budget).request_json(
        "GET", "https://fixture.invalid/read", headers={}
    ).status_code == 200
    post_raw = SequenceTransport([response(429), response()], clock)
    assert BudgetTransport(post_raw, budget).request_json(
        "POST", "https://fixture.invalid/token", headers={}
    ).status_code == 429
    unknown = SequenceTransport([RuntimeError("429 in unrelated text"), response()], clock)
    with pytest.raises(RuntimeError, match="unrelated"):
        BudgetTransport(unknown, budget).request_json(
            "GET", "https://fixture.invalid/read", headers={}
        )
    assert len(get_raw.starts) == 2
    assert len(post_raw.starts) == len(unknown.starts) == 1

    proven_http = SequenceTransport(
        [KisPaperTransportError("KIS paper HTTP status 429"), response()], clock
    )
    assert BudgetTransport(proven_http, budget).request_json(
        "GET", "https://fixture.invalid/read", headers={}
    ).status_code == 200
    impostor = SequenceTransport([RuntimeError("HTTP status 429"), response()], clock)
    with pytest.raises(RuntimeError, match="HTTP status"):
        BudgetTransport(impostor, budget).request_json(
            "GET", "https://fixture.invalid/read", headers={}
        )
    assert len(proven_http.starts) == 2
    assert len(impostor.starts) == 1


class GateClock(FakeClock):
    def __init__(self):
        super().__init__()
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def sleep(self, seconds):
        self.waiting.set()
        assert self.release.wait(2)
        with self.lock:
            self.now += seconds


def test_high_priority_preempts_waiting_entry_but_not_admitted_slot(tmp_path):
    clock = GateClock()
    budget = SharedBudget(
        tmp_path / "budget.sqlite3", SCOPE, interval=1, clock=clock, sleep=clock.sleep
    )
    sent = []

    class RecordingTransport:
        def request_json(self, method, url, **kwargs):
            sent.append(url)
            return response()

    wrapped = BudgetTransport(RecordingTransport(), budget)
    with budget.context(priority="query"):
        wrapped.request_json("GET", "already-sent", headers={})

    def issue(priority, name):
        with budget.context(priority=priority):
            wrapped.request_json("GET", name, headers={})

    low = threading.Thread(target=issue, args=("entry", "entry"), daemon=True)
    low.start()
    assert clock.waiting.wait(2)
    high = threading.Thread(target=issue, args=("cancel", "cancel"), daemon=True)
    high.start()
    deadline = time.time() + 2
    while budget.snapshot()["queue_depth"] < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert budget.snapshot()["queue_depth"] == 2
    clock.release.set()
    low.join(2)
    high.join(2)
    assert not low.is_alive() and not high.is_alive()
    assert sent[0] == "already-sent"
    assert sent[1:] == ["cancel", "entry"]


def _multiprocess_request(path, scope, interval, gate, starts):
    budget = SharedBudget(path, scope, interval=interval)

    class Transport:
        def request_json(self, method, url, **kwargs):
            starts.put(time.time())
            return response()

    gate.wait()
    with budget.context(priority="query"):
        BudgetTransport(Transport(), budget).request_json("GET", "fixture", headers={})


def test_real_multiprocess_clients_are_admitted_without_bursts(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    gate = ctx.Event()
    starts = ctx.Queue()
    path = str(tmp_path / "shared.sqlite3")
    interval = 0.08
    processes = [
        ctx.Process(target=_multiprocess_request, args=(path, SCOPE, interval, gate, starts))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    gate.set()
    observed = sorted(starts.get(timeout=10) for _ in processes)
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert all(
        later - earlier >= interval - 0.015
        for earlier, later in zip(observed, observed[1:])
    )


def test_slow_wire_keeps_unsent_requests_available_for_priority_preemption(tmp_path):
    budget = SharedBudget(tmp_path / "budget.sqlite3", SCOPE, interval=.02)
    entered, release = threading.Event(), threading.Event()
    sent, errors = [], []
    class Slow:
        def request_json(self, method, url, **kwargs):
            sent.append(url)
            if url == "slow":
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("fixture")
            return response()
    wrapped = BudgetTransport(Slow(), budget)
    def issue(priority, name):
        try:
            with budget.context(priority):
                wrapped.request_json("GET", name)
        except Exception as exc:
            errors.append(type(exc).__name__)
    threads = [threading.Thread(target=issue, args=args, daemon=True) for args in
               [("minute", "slow"), ("minute", "discovery"), ("sell", "protect")]]
    try:
        threads[0].start()
        assert entered.wait(2)
        threads[1].start()
        threads[2].start()
        deadline = time.monotonic()+2
        while budget.snapshot()["queue_depth"] < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        assert budget.snapshot()["queue_depth"] == 2
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(3)
    assert not errors and all(not t.is_alive() for t in threads)
    assert sent == ["slow", "protect", "discovery"]


def test_post_rate_rejection_shares_cooldown_without_retry(tmp_path):
    clock = FakeClock()
    budget = SharedBudget(tmp_path / "budget.sqlite3", SCOPE, clock=clock, sleep=clock.sleep)
    raw = SequenceTransport([response(429), response()], clock)
    wrapped = BudgetTransport(raw, budget)
    assert wrapped.request_json("POST", "fixture").status_code == 429
    assert len(raw.starts) == 1
    wrapped.request_json("GET", "fixture")
    assert raw.starts[1]-raw.starts[0] == pytest.approx(1.05, abs=1e-6)
    assert budget.snapshot()["days"]["2023-11-15"]["rate_violations"] == 1


@pytest.mark.parametrize("contents", [b"", b"\0", b"legacy"])
def test_send_lock_preserves_contents_and_releases_after_error(tmp_path, contents):
    budget = object.__new__(SharedBudget)
    budget.path = tmp_path / "budget.sqlite3"
    lock = Path(str(budget.path) + ".send.lock")
    lock.write_bytes(contents)
    with pytest.raises(RuntimeError, match="fixture"):
        with budget._sending() as acquired:
            assert acquired
            with budget._sending() as contender:
                assert not contender
            raise RuntimeError("fixture")
    with budget._sending() as acquired:
        assert acquired
    assert lock.read_bytes() == contents


def test_empty_send_lock_contention_never_writes_locked_byte(tmp_path):
    budget = object.__new__(SharedBudget)
    budget.path = tmp_path / "budget.sqlite3"
    lock = Path(str(budget.path) + ".send.lock")
    with lock.open("a+b") as owner:
        try:
            import msvcrt
        except ImportError:
            import fcntl

            fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(owner.fileno(), fcntl.LOCK_UN)
        else:
            msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(owner.fileno(), msvcrt.LK_UNLCK, 1)
        try:
            with budget._sending() as acquired:
                assert not acquired
        finally:
            owner.seek(0)
            unlock()
    with budget._sending() as acquired:
        assert acquired
    assert lock.read_bytes() == b""
