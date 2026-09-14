from __future__ import annotations

from datetime import datetime, timedelta
import json
import threading

import pytest

from quantpilot.paper.account_notices import NOTICE_TR
from quantpilot.paper.calendar import KST
from quantpilot.paper.feeds import FeedApproval, HybridFeed, MAX_SUBSCRIPTIONS
from quantpilot.paper.intraday.stream import QUOTE_TR, TICK_TR


NOW = datetime(2026, 9, 14, 10, 0, 1, tzinfo=KST)
APPROVAL = FeedApproval("fixture-approval", "fixture-hts-id")
KEY = "0" * 32  # Synthetic AES fixture, never a broker credential.
IV = "abcdef0123456789"
NOTICE = (
    '1|H0STCNI9|001|S7v3/wg4AdVNmRAH5JJmWSMtDnUqAr7a0AN1LEbGM6Uys5DEqONEo2a/eNSjsSsAXEsmjujEqpwtgKPTu+/ziuTjjfAQun89BVmMCo7ra119ooJr6/CXb7abuzeBiQQx/SNblRSNuZjibsZrbnylCq7kWPVMissNmg4Dh8/4qoTg7G91CTenPtqoHzKjOwAo'
)


def _ack(tr_id: str, tr_key: str, *, crypto: bool = False) -> str:
    return json.dumps(
        {
            "header": {
                "tr_id": tr_id,
                "tr_key": tr_key,
                **({"encrypt": "Y"} if crypto else {}),
            },
            "body": {
                "rt_cd": "0",
                "output": ({"key": KEY, "iv": IV} if crypto else {}),
            },
        }
    )


def _tick(second: str = "100000", price: str = "70000") -> str:
    fields = ["0"] * 46
    for index, value in {
        0: "005930",
        1: second,
        2: price,
        12: "10",
        33: "20260914",
    }.items():
        fields[index] = value
    return "0|H0STCNT0|001|" + "^".join(fields)


def _book(second: str = "100000") -> str:
    fields = ["0"] * 59
    for index, value in {
        0: "005930",
        1: second,
        3: "70100",
        13: "69900",
        23: "100",
        33: "200",
    }.items():
        fields[index] = value
    return "0|H0STASP0|001|" + "^".join(fields)


class FakeSocket:
    def __init__(self, incoming, *, idle: threading.Event | None = None) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict] = []
        self.pongs: list[bytes] = []
        self.idle = idle

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def pong(self, raw: bytes) -> None:
        self.pongs.append(raw)

    def recv(self, timeout: float):
        assert timeout == 1
        if self.incoming:
            item = self.incoming.pop(0)
            if callable(item):
                return item()
            if isinstance(item, Exception):
                raise item
            return item
        if self.idle is not None:
            self.idle.set()
            threading.Event().wait(0.005)
        raise TimeoutError


class NoSleepStop:
    def __init__(self) -> None:
        self.value = False
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.value

    def set(self) -> None:
        self.value = True

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        return self.value


def _connect_for(*sockets: FakeSocket):
    remaining = list(sockets)

    def connect(url: str, **kwargs):
        assert url.endswith(":31000")
        assert kwargs["proxy"] is None and kwargs["max_size"] == 1_048_576
        if not remaining:
            raise RuntimeError("fixture connector secret detail")
        return remaining.pop(0)

    return connect


def _request_keys(socket: FakeSocket) -> list[tuple[str, str, str]]:
    return [
        (
            message["header"]["tr_type"],
            message["body"]["input"]["tr_id"],
            message["body"]["input"]["tr_key"],
        )
        for message in socket.sent
    ]


def test_desired_protects_held_and_open_books_inside_account_inclusive_capacity() -> None:
    candidates = [f"{index:06d}" for index in range(20)]
    held = [f"1{index:05d}" for index in range(10)]
    opened = [held[0], "200000"]

    desired = HybridFeed.desired(candidates, held, opened)
    keys = {(item.tr_id, item.tr_key) for item in desired.market}

    assert len(desired.market) + 1 <= MAX_SUBSCRIPTIONS
    for symbol in set(held + opened):
        assert (TICK_TR, symbol) in keys
        assert (QUOTE_TR, symbol) in keys
        assert all(
            item.protected for item in desired.market if item.tr_key == symbol
        )
    with pytest.raises(ValueError, match="protected_subscription_capacity"):
        HybridFeed.desired([], [f"3{index:05d}" for index in range(20)])


def test_run_builds_quotes_deduplicates_normalizes_future_and_emits_safe_hint() -> None:
    idle = threading.Event()
    ping = json.dumps({"header": {"tr_id": "PINGPONG"}})
    socket = FakeSocket(
        [
            _ack(NOTICE_TR, APPROVAL.notice_key, crypto=True),
            _ack(QUOTE_TR, "005930"),
            _ack(TICK_TR, "005930"),
            _tick(),
            _book(),
            _tick(),
            _tick("100002", "70100"),
            _tick("100003", "99999"),
            _tick("095930", "99999"),
            "0|broken",
            NOTICE,
            NOTICE,
            ping,
        ],
        idle=idle,
    )
    observations: list[dict] = []
    stop = threading.Event()
    feed = HybridFeed(
        connect=_connect_for(socket),
        approval_supplier=lambda: APPROVAL,
        clock=lambda: NOW,
    )
    thread = threading.Thread(
        daemon=True,
        target=feed.run,
        args=(stop, lambda: HybridFeed.desired(["005930"]), observations.append),
    )
    thread.start()
    assert idle.wait(1)

    quotes = feed.quotes(["005930", "000660"], NOW, 5)
    feed.set_reconciled(NOW)

    assert quotes["005930"].last == 70100
    assert quotes["005930"].bid == 69900
    assert quotes["005930"].ask == 70100
    # Quote age is the oldest of tick and book; a fresh tick cannot refresh an old book.
    assert quotes["005930"].as_of == NOW - timedelta(seconds=1)
    assert "000660" not in quotes
    assert feed.healthy(NOW, ["005930"])
    assert not feed.healthy(NOW + timedelta(seconds=6), ["005930"])
    hints = [item for item in observations if item["kind"] == "reconciliation_hint"]
    assert len(hints) == 1 and hints[0]["symbol"] == "005930"
    market = [item for item in observations if item["kind"] == "market_observation"]
    assert len(market) == 3  # tick, book, and the distinct <=1s-future tick
    future = market[-1]
    assert future["occurred_at"] != future["received_at"]
    assert future["observed_at"] == future["received_at"]
    assert "fixture-customer" not in repr(observations)
    assert "fixture-approval" not in repr(observations)
    rejected = [item["reason"] for item in observations if item["kind"] == "frame_rejected"]
    assert "market_event_delayed" in rejected
    assert socket.pongs

    stop.set()
    thread.join(1)
    assert not thread.is_alive()
    assert feed.quotes(["005930"], NOW, 5) == {}


def test_unsubscribe_ack_precedes_slot_reuse() -> None:
    phase = {"new": False}
    stop = NoSleepStop()

    def switch():
        phase["new"] = True
        raise TimeoutError

    def finish():
        stop.set()
        raise TimeoutError

    socket = FakeSocket(
        [
            _ack(NOTICE_TR, APPROVAL.notice_key, crypto=True),
            _ack(QUOTE_TR, "005930"),
            _ack(TICK_TR, "005930"),
            switch,
            _ack(QUOTE_TR, "005930"),
            _ack(TICK_TR, "005930"),
            _ack(QUOTE_TR, "000660"),
            _ack(TICK_TR, "000660"),
            finish,
        ]
    )
    feed = HybridFeed(
        connect=_connect_for(socket),
        approval_supplier=lambda: APPROVAL,
        clock=lambda: NOW,
    )
    feed.run(
        stop,
        lambda: HybridFeed.desired(["000660" if phase["new"] else "005930"]),
        lambda _event: None,
    )

    requests = _request_keys(socket)
    last_unsubscribe = max(
        index
        for index, item in enumerate(requests)
        if item[0] == "2" and item[2] == "005930"
    )
    first_reuse = min(
        index
        for index, item in enumerate(requests)
        if item[0] == "1" and item[2] == "000660"
    )
    assert last_unsubscribe < first_reuse


def test_disconnect_after_partial_ack_reconnects_and_resubscribes_without_leaks() -> None:
    first = FakeSocket(
        [
            _ack(NOTICE_TR, APPROVAL.notice_key, crypto=True),
            _ack(TICK_TR, "005930"),
            RuntimeError("fake-secret-must-not-leak"),
        ]
    )
    stop = NoSleepStop()

    def finish():
        stop.set()
        raise TimeoutError

    second = FakeSocket(
        [
            _ack(NOTICE_TR, APPROVAL.notice_key, crypto=True),
            _ack(QUOTE_TR, "005930"),
            _ack(TICK_TR, "005930"),
            finish,
        ]
    )
    observations: list[dict] = []
    feed = HybridFeed(
        connect=_connect_for(first, second),
        approval_supplier=lambda: APPROVAL,
        clock=lambda: NOW,
        max_reconnects=2,
    )

    feed.run(
        stop,
        lambda: HybridFeed.desired(["005930"]),
        observations.append,
    )

    for socket in (first, second):
        keys = _request_keys(socket)
        assert ("1", NOTICE_TR, APPROVAL.notice_key) in keys
        assert ("1", TICK_TR, "005930") in keys
        assert ("1", QUOTE_TR, "005930") in keys
    assert stop.waits == [1.0]
    rendered = repr(observations)
    assert "fake-secret" not in rendered
    assert "fixture-approval" not in rendered
