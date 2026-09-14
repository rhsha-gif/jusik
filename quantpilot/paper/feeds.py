"""Hybrid public-stream/REST-fallback feed for the KIS paper operator.

Integration contract
--------------------
Construct ``HybridFeed(connect=..., approval_supplier=..., clock=...)``.  The
approval supplier must return ``FeedApproval(approval_key, notice_key)`` where
``notice_key`` is the paper HTS ID used only as the H0STCNI9 subscription key.
Run ``feed.run(stop_event, desired_supplier, observer)`` in one background
thread.  ``desired_supplier`` returns a ``DesiredSubscriptions`` produced by
``feed.desired(candidates, held_symbols, open_order_symbols)``; ``observer``
receives only fixed diagnostics and redacted ``reconciliation_hint`` mappings.
The caller asks ``quotes(symbols, now, ttl)`` first and REST-fetches every
missing symbol.  After that authoritative REST reconciliation completes, call
``set_reconciled(now)``.  ``healthy(now, required_symbols)`` is true only after
the current connection has ACKed its account and required public subscriptions,
all required stream data is fresh, and that caller-owned reconciliation fence is
fresh.  This module never reads credentials, places orders, mutates fills/cash,
or persists account notices.  Wiring is therefore off until a parent explicitly
constructs and starts it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import re
import threading
from typing import Callable, Iterable

from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.paper.account_notices import NOTICE_TR, NoticeCipher, parse_notice_frame
from quantpilot.paper.config import aware
from quantpilot.paper.data import symbol_code
from quantpilot.paper.intraday.stream import PAPER_WS, QUOTE_TR, TICK_TR, parse_frame


MAX_SUBSCRIPTIONS = 40
MAX_MARKET_SUBSCRIPTIONS = MAX_SUBSCRIPTIONS - 1


@dataclass(frozen=True, repr=False)
class FeedApproval:
    approval_key: str
    notice_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.approval_key, str) or not self.approval_key:
            raise ValueError("feed_approval_invalid")
        if not isinstance(self.notice_key, str) or not self.notice_key:
            raise ValueError("feed_notice_key_invalid")
        if len(self.approval_key) > 2048 or len(self.notice_key) > 128:
            raise ValueError("feed_approval_invalid")


@dataclass(frozen=True, order=True)
class Subscription:
    tr_id: str
    tr_key: str
    protected: bool = False

    def __post_init__(self) -> None:
        if self.tr_id not in {TICK_TR, QUOTE_TR}:
            raise ValueError("market_subscription_invalid")
        symbol_code(self.tr_key)


@dataclass(frozen=True)
class DesiredSubscriptions:
    market: tuple[Subscription, ...]

    def __post_init__(self) -> None:
        keys = {(item.tr_id, item.tr_key) for item in self.market}
        if len(keys) != len(self.market) or len(keys) > MAX_MARKET_SUBSCRIPTIONS:
            raise ValueError("stream_subscription_capacity")


class HybridFeed:
    """Thread-safe, paper-only hybrid feed with explicit REST fallback fences."""

    def __init__(
        self,
        *,
        connect: Callable | None,
        approval_supplier: Callable[[], FeedApproval],
        clock: Callable[[], datetime],
        fresh_seconds: float = 5,
        reconciliation_seconds: float = 30,
        max_reconnects: int = 5,
        max_backoff_seconds: float = 8,
    ) -> None:
        if fresh_seconds <= 0 or reconciliation_seconds <= 0:
            raise ValueError("feed_freshness_invalid")
        if max_reconnects < 0 or max_backoff_seconds <= 0:
            raise ValueError("feed_reconnect_policy_invalid")
        self._connect = connect
        self._approval_supplier = approval_supplier
        self._clock = clock
        self._fresh = timedelta(seconds=fresh_seconds)
        self._reconciliation_ttl = timedelta(seconds=reconciliation_seconds)
        self._max_reconnects = max_reconnects
        self._max_backoff = max_backoff_seconds
        self._lock = threading.RLock()
        self._connected = False
        self._connected_at: datetime | None = None
        self._reconciled_at: datetime | None = None
        self._acked: set[tuple[str, str]] = set()
        self._wanted: set[tuple[str, str]] = set()
        self._pending: dict[tuple[str, str], str] = {}
        self._pending_since = None
        self._notice_cipher: NoticeCipher | None = None
        self._subscriptions_ready = False
        self._ticks: dict[str, tuple[float, datetime]] = {}
        self._books: dict[str, tuple[float, float, datetime]] = {}
        self._times = {}
        self._observed: dict[tuple[str, str], datetime] = {}
        self._seen_order: deque[str] = deque(maxlen=4096)
        self._seen: set[str] = set()

    @staticmethod
    def desired(
        candidates: Iterable[str],
        held_symbols: Iterable[str] = (),
        open_order_symbols: Iterable[str] = (),
    ) -> DesiredSubscriptions:
        """Allocate protected books first, then top-five books and candidate ticks."""

        candidate_codes = list(dict.fromkeys(symbol_code(item) for item in candidates))[
            :20
        ]
        protected_codes = list(
            dict.fromkeys(
                symbol_code(item)
                for item in (*tuple(held_symbols), *tuple(open_order_symbols))
            )
        )
        if len(protected_codes) * 2 > MAX_MARKET_SUBSCRIPTIONS:
            raise ValueError("protected_subscription_capacity")
        selected: list[Subscription] = []
        keys: set[tuple[str, str]] = set()

        def add(tr_id: str, code: str, *, protected: bool) -> bool:
            key = (tr_id, code)
            if key in keys:
                return True
            if len(selected) >= MAX_MARKET_SUBSCRIPTIONS:
                return False
            selected.append(Subscription(tr_id, code, protected))
            keys.add(key)
            return True

        for code in protected_codes:
            add(TICK_TR, code, protected=True)
            add(QUOTE_TR, code, protected=True)
        for code in candidate_codes[:5]:
            missing = sum((tr_id, code) not in keys for tr_id in (TICK_TR, QUOTE_TR))
            if len(selected) + missing <= MAX_MARKET_SUBSCRIPTIONS:
                add(TICK_TR, code, protected=code in protected_codes)
                add(QUOTE_TR, code, protected=code in protected_codes)
        for code in candidate_codes:
            add(TICK_TR, code, protected=code in protected_codes)
        return DesiredSubscriptions(tuple(selected))

    def set_reconciled(self, now: datetime) -> None:
        with self._lock:
            self._reconciled_at = aware(now)

    def quotes(
        self, symbols: Iterable[str], now: datetime, ttl: float | timedelta
    ) -> dict[str, Quote]:
        """Return fresh stream Quotes; omitted symbols are the caller's REST fallback."""

        now = aware(now)
        lifetime = ttl if isinstance(ttl, timedelta) else timedelta(seconds=float(ttl))
        if lifetime.total_seconds() < 0:
            raise ValueError("quote_ttl_invalid")
        result: dict[str, Quote] = {}
        with self._lock:
            if not self._connected:
                return result
            for code in dict.fromkeys(symbol_code(item) for item in symbols):
                tick = self._ticks.get(code)
                if (TICK_TR, code) not in self._acked or tick is None:
                    continue
                last, tick_at = tick
                age = now - tick_at
                if age < timedelta(0) or age >= lifetime:
                    continue
                book = self._books.get(code)
                if ((QUOTE_TR, code) not in self._acked or book is None
                        or not timedelta(0) <= now - book[2] < lifetime):
                    continue
                bid, ask = book[:2]
                if bid > ask:
                    continue
                result[code] = Quote(symbol=code, last=last, bid=bid, ask=ask,
                                     as_of=min(tick_at, book[2]),
                                     occurred_at=self._times.get((QUOTE_TR, code), (None, None))[0],
                                     received_at=self._times.get((QUOTE_TR, code), (None, None))[1])
        return result

    def healthy(self, now: datetime, required_symbols: Iterable[str]) -> bool:
        now = aware(now)
        required = set(symbol_code(item) for item in required_symbols)
        with self._lock:
            if not self._connected or self._connected_at is None:
                return False
            account = next((item for item in self._acked if item[0] == NOTICE_TR), None)
            if account is None or self._notice_cipher is None:
                return False
            required_subscriptions = {
                item for item in self._wanted if item[1] in required and item[0] != NOTICE_TR
            }
            if (
                any((TICK_TR, code) not in required_subscriptions for code in required)
                or not required_subscriptions.issubset(self._acked)
            ):
                return False
            if any(
                item not in self._observed
                or not timedelta(0) <= now - self._observed[item] <= self._fresh
                for item in required_subscriptions
            ):
                return False
            return bool(
                self._reconciled_at is not None
                and self._reconciled_at >= self._connected_at
                and timedelta(0) <= now - self._reconciled_at <= self._reconciliation_ttl
            )

    def status(self):
        """Public redacted state; never return subscription/account keys or key material."""
        with self._lock:
            return {"state": "connected" if self._connected else "disconnected",
                    "acked_subscriptions": len(self._acked), "pending_subscriptions": len(self._pending),
                    "required_subscriptions": len(self._wanted), "capacity": MAX_SUBSCRIPTIONS,
                    "account_notice_ready": self._notice_cipher is not None,
                    "connected_at": self._connected_at.isoformat() if self._connected_at else None}

    def run(self, stop_event, desired_supplier, observer) -> None:
        """Blocking background-worker entry point; reconnects with bounded backoff."""

        attempts = 0
        while not stop_event.is_set():
            try:
                approval = self._approval_supplier()
                if not isinstance(approval, FeedApproval):
                    raise ValueError("feed_approval_invalid")
                connect = self._connect
                if connect is None:
                    from websockets.sync.client import connect
                with connect(
                    PAPER_WS,
                    proxy=None,
                    open_timeout=10,
                    close_timeout=5,
                    max_size=1_048_576,
                ) as socket:
                    with self._lock:
                        self._connected = True
                        self._connected_at = aware(self._clock())
                        self._reconciled_at = None
                    self._observe(observer, "feed_connected")
                    self._consume(socket, approval, stop_event, desired_supplier, observer)
                    if stop_event.is_set():
                        break
                    raise ConnectionError("feed_connection_closed")
            except Exception as exc:
                if self._subscriptions_ready:
                    attempts = 0
                self._disconnect()
                if stop_event.is_set():
                    break
                attempts += 1
                if attempts > self._max_reconnects:
                    self._observe(observer, "feed_stopped", reason="reconnect_exhausted")
                    return
                delay = min(float(2 ** (attempts - 1)), self._max_backoff)
                self._observe(
                    observer,
                    "feed_disconnected",
                    reason=self._safe_reason(exc),
                    retry_in_seconds=delay,
                )
                stop_event.wait(delay)
        self._disconnect()
        self._observe(observer, "feed_stopped", reason="requested")

    def _consume(self, socket, approval, stop_event, desired_supplier, observer) -> None:
        while not stop_event.is_set():
            desired = desired_supplier()
            if not isinstance(desired, DesiredSubscriptions):
                raise ValueError("desired_subscriptions_invalid")
            self._sync(socket, approval, desired)
            try:
                raw = socket.recv(timeout=1)
            except TimeoutError:
                continue
            received = aware(self._clock())
            try:
                if isinstance(raw, str) and raw.startswith("{"):
                    self._ack_or_ping(socket, raw)
                elif isinstance(raw, str) and raw.startswith("0|"):
                    self._market(raw, received, observer)
                else:
                    self._notice(raw, received, observer)
            except ValueError as exc:
                self._observe(observer, "frame_rejected", reason=self._safe_reason(exc))

    def _sync(
        self, socket, approval: FeedApproval, desired: DesiredSubscriptions
    ) -> None:
        account = (NOTICE_TR, approval.notice_key)
        wanted = {account, *((item.tr_id, item.tr_key) for item in desired.market)}
        if len(wanted) > MAX_SUBSCRIPTIONS:
            raise ValueError("stream_subscription_capacity")
        with self._lock:
            self._wanted = wanted
            if self._pending:
                if self._pending_since and (self._clock() - self._pending_since).total_seconds() >= 15:
                    raise ValueError("subscription_ack_timeout")
                return
            self._pending_since = self._clock()
            removals = sorted(self._acked - wanted)
            if removals:
                for key in removals:
                    self._send(socket, approval.approval_key, key, "2")
                    self._pending[key] = "unsubscribe"
                return
            additions = sorted(wanted - self._acked)
            for key in additions:
                self._send(socket, approval.approval_key, key, "1")
                self._pending[key] = "subscribe"

    @staticmethod
    def _send(socket, approval_key: str, key: tuple[str, str], action: str) -> None:
        socket.send(
            json.dumps(
                {
                    "header": {
                        "approval_key": approval_key,
                        "custtype": "P",
                        "tr_type": action,
                        "content-type": "utf-8",
                    },
                    "body": {"input": {"tr_id": key[0], "tr_key": key[1]}},
                },
                separators=(",", ":"),
            )
        )

    def _ack_or_ping(self, socket, raw: str) -> None:
        try:
            message = json.loads(raw)
            header, body = message["header"], message.get("body", {})
            tr_id = header["tr_id"]
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("stream_control_invalid") from None
        if tr_id == "PINGPONG":
            socket.pong(raw.encode("utf-8"))
            return
        tr_key = header.get("tr_key")
        with self._lock:
            matches = [
                key
                for key in self._pending
                if key[0] == tr_id and (tr_key is None or key[1] == tr_key)
            ]
            if len(matches) != 1 or body.get("rt_cd") != "0":
                raise ValueError("stream_subscription_rejected")
            key = matches[0]
            action = self._pending[key]
            output = body.get("output") or {}
            if action == "subscribe":
                if tr_id == NOTICE_TR:
                    self._notice_cipher = NoticeCipher.from_subscription_ack(
                        output.get("key"), output.get("iv")
                    )
                # Public ACK metadata never installs an account cipher. The actual
                # notice frame must still use the encrypted H0STCNI9 envelope.
                self._acked.add(key)
            else:
                self._acked.discard(key)
                self._observed.pop(key, None)
                if tr_id == TICK_TR:
                    self._ticks.pop(key[1], None)
                elif tr_id == QUOTE_TR:
                    self._books.pop(key[1], None)
            self._pending.pop(key)
            if not self._pending:
                self._pending_since = None
                if self._notice_cipher is not None and self._wanted <= self._acked:
                    self._subscriptions_ready = True

    def _market(self, raw: str, received: datetime, observer) -> None:
        with self._lock:
            symbols = {key[1] for key in self._acked if key[0] in {TICK_TR, QUOTE_TR}}
        events = parse_frame(raw, received, symbols, future_tolerance_seconds=1)
        for event in events:
            occurred = datetime.fromisoformat(event["event_at"])
            age = (received - occurred).total_seconds()
            if age < -1 or age >= 30:
                raise ValueError("market_event_delayed")
            event_id = event["id"]
            with self._lock:
                if event_id in self._seen:
                    continue
                if len(self._seen_order) == self._seen_order.maxlen:
                    self._seen.discard(self._seen_order[0])
                self._seen_order.append(event_id)
                self._seen.add(event_id)
                normalized = min(occurred, received)
                key = (TICK_TR if event["kind"] == "tick" else QUOTE_TR, event["symbol"])
                if key not in self._acked:
                    raise ValueError("unsubscribed_symbol")
                previous = self._observed.get(key)
                if previous is not None and normalized < previous:
                    continue
                self._observed[key] = normalized
                self._times[key] = (occurred, received)
                if event["kind"] == "tick":
                    self._ticks[event["symbol"]] = (event["price"], normalized)
                else:
                    self._books[event["symbol"]] = (
                        event["bid"],
                        event["ask"],
                        normalized,
                    )
            self._observe(
                observer,
                "market_observation",
                event_kind=event["kind"],
                symbol=event["symbol"],
                event_id=event_id,
                occurred_at=occurred.isoformat(),
                observed_at=normalized.isoformat(),
                received_at=received.isoformat(),
            )

    def _notice(self, raw: object, received: datetime, observer) -> None:
        with self._lock:
            account_keys = [key for key in self._acked if key[0] == NOTICE_TR]
            cipher = self._notice_cipher
        hint = parse_notice_frame(
            raw,
            cipher=cipher,
            received_at=received,
            registered=len(account_keys) == 1,
        )
        with self._lock:
            if hint["event_id"] in self._seen:
                return
            if len(self._seen_order) == self._seen_order.maxlen:
                self._seen.discard(self._seen_order[0])
            self._seen_order.append(hint["event_id"])
            self._seen.add(hint["event_id"])
        self._observe(observer, **hint)

    def _disconnect(self) -> None:
        with self._lock:
            self._subscriptions_ready = False
            self._connected = False
            self._connected_at = None
            self._reconciled_at = None
            self._acked.clear()
            self._wanted.clear()
            self._pending.clear()
            self._notice_cipher = None
            self._ticks.clear()
            self._books.clear()
            self._observed.clear()
            self._times.clear()

    @staticmethod
    def _safe_reason(exc: Exception) -> str:
        value = str(exc)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
            return value
        return type(exc).__name__

    @staticmethod
    def _observe(observer, kind: str, **fields) -> None:
        observer({"kind": kind, **fields})
