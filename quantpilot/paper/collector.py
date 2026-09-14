"""Background market reads share the paper request budget, never the order loop.

Each symbol is read once per KST minute, shortly after its last bar became final
(minute boundary + finality grace + a small margin), and the symbols are spread
across the minute so the shared request limiter is never saturated for long. A
symbol quarantined today is not read again (unless it is a held position, where
only bars newer than the disputed one are kept so protection keeps a history), a
transient broker refusal pauses the sweep briefly instead of poisoning the
candidate status, and a genuine revision is audited once with the full diff.
"""

from datetime import datetime, timedelta
import threading

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import BAR_FINALITY_GRACE_SECONDS
from quantpilot.paper.data import LIMITER_INTERVAL
from quantpilot.paper.store import CompletedBarRevised, Store
from quantpilot.packages.core.kis_paper import (
    KisPaperGatewayRejected,
    KisPaperTransportError,
    safe_failure,
)

UNIVERSE_TTL_SECONDS = 300
# A refused read (per-second limit, transport failure) pauses the sweep briefly;
# retrying on the next poll would only keep the limit tripped.
REJECTION_PAUSE_SECONDS = 3.0
MAX_ATTEMPTS_PER_MINUTE = 2
FAILURE_AUDIT_INTERVAL_SECONDS = 60


class Collector:
    def __init__(
        self,
        path,
        market,
        calendar,
        clock,
        *,
        grace_seconds=BAR_FINALITY_GRACE_SECONDS,
        spacing_seconds=1.5,
        boundary_margin_seconds=2.0,
        poll_seconds=1.0,
    ):
        self.path = path
        self.market = market
        self.calendar = calendar
        self.clock = clock
        self.grace_seconds = grace_seconds
        self.spacing_seconds = spacing_seconds
        self.boundary_margin_seconds = boundary_margin_seconds
        self.poll_seconds = poll_seconds
        self.fetched = {}  # symbol -> minute bucket already read (or deliberately skipped)
        self.attempts = {}  # (symbol, bucket) -> reads attempted this minute
        self.retry_after = None
        self.observations = {}  # symbol -> latest observation, accumulated across passes
        self.stopping = threading.Event()
        self.thread = threading.Thread(
            target=self.run, name="paper-market-data", daemon=True
        )

    def start(self):
        self.thread.start()

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=15)

    @staticmethod
    def bucket(now):
        return now.astimezone(KST).replace(second=0, microsecond=0)

    def symbols(self, store):
        return list(
            dict.fromkeys(
                [p["symbol"] for p in store.positions()] + store.get("universe", [])
            )
        )

    def due(self, symbols, now):
        """Symbols whose slot in the current minute has arrived and are not read yet."""

        bucket = self.bucket(now)
        key = bucket.isoformat()
        usable = 60 - self.grace_seconds - self.boundary_margin_seconds - 3
        spacing = max(
            LIMITER_INTERVAL, min(self.spacing_seconds, usable / max(1, len(symbols)))
        )
        first = bucket + timedelta(seconds=self.grace_seconds + self.boundary_margin_seconds)
        out = []
        for index, symbol in enumerate(symbols):
            if self.fetched.get(symbol) == key:
                continue
            if self.attempts.get((symbol, key), 0) >= MAX_ATTEMPTS_PER_MINUTE:
                continue
            if first + timedelta(seconds=index * spacing) <= now:
                out.append(symbol)
        return out

    def paused(self, store, now):
        if self.retry_after is not None and now < self.retry_after:
            return True
        retry_after = store.get("broker_retry_after")
        return bool(retry_after and now < datetime.fromisoformat(retry_after))

    def collect_once(self, store):
        now = self.clock()
        result = {"fetched": [], "skipped": []}
        session = self.calendar.session(now)
        if not session or not session.trading(now):
            return result
        if self.paused(store, now):
            store.put("collector_heartbeat", now.isoformat())
            return result
        at = store.get("universe_at")
        if at is None or (now - datetime.fromisoformat(at)).total_seconds() >= UNIVERSE_TTL_SECONDS:
            symbols, source = self.market.candidates(now, store.policy.max_candidates)
            with store.transaction():
                store.put("universe", symbols)
                store.put("universe_at", now.isoformat())
                store.put("universe_source", source)
        symbols = self.symbols(store)
        held = {p["symbol"] for p in store.positions()}
        key = self.bucket(now).isoformat()
        self.attempts = {k: v for k, v in self.attempts.items() if k[1] == key}
        from quantpilot.paper.risk import data_quarantined

        for symbol in self.due(symbols, now):
            if self.stopping.is_set() or not session.trading(self.clock()):
                break
            observed = self.clock()
            floor = None
            if data_quarantined(store, symbol, observed):
                start = (store.get("data_quarantine:" + symbol) or {}).get("start")
                if symbol not in held or not start:
                    # Nothing new can be learned about a quarantined candidate today.
                    self.fetched[symbol] = key
                    result["skipped"].append(symbol)
                    continue
                floor = datetime.fromisoformat(start)
            try:
                self.fetch(store, symbol, observed, key, floor)
                result["fetched"].append(symbol)
            except CompletedBarRevised as exc:
                self.quarantine(store, symbol, exc, observed)
            except Exception as exc:
                self.record_failure(store, symbol, exc, observed)
                if isinstance(exc, (KisPaperGatewayRejected, KisPaperTransportError)):
                    self.retry_after = observed + timedelta(seconds=REJECTION_PAUSE_SECONDS)
                    break
        store.put("collector_heartbeat", self.clock().isoformat())
        observations = {s: self.observations[s] for s in symbols if s in self.observations}
        if result["fetched"] and observations:
            store.put(
                "evidence",
                {
                    "observed_at": min(v["observed_at"] for v in observations.values()),
                    "symbols": symbols,
                    "strategies": list(store.policy.active_strategies),
                    "market_data": observations,
                    "positions": store.positions(),
                    "weights": store.get("weights", {}),
                },
            )
        return result

    def fetch(self, store, symbol, observed, key, floor=None):
        self.attempts[(symbol, key)] = self.attempts.get((symbol, key), 0) + 1
        bars = self.market.minutes(symbol, observed, self.grace_seconds)
        if floor is not None:
            # The disputed bar and everything before it stay as stored; never re-judged.
            bars = [b for b in bars if b.start > floor]
        store.save_bars(bars)
        if bars:
            self.observations[symbol] = {
                "observed_at": observed.isoformat(),
                "source": "kis_paper",
                "quality": "completed_bars",
                "recent_bars": [
                    {
                        "start": b.start.isoformat(),
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.volume,
                    }
                    for b in bars[-5:]
                ],
            }
        store.put(
            "data:" + symbol,
            {
                "provider": "kis_paper",
                "observed_at": observed.isoformat(),
                "quality": "completed_bars_validated",
                "last_bar": bars[-1].start.isoformat() if bars else None,
                "floor": floor.isoformat() if floor is not None else None,
            },
        )
        self.fetched[symbol] = key

    def quarantine(self, store, symbol, exc, observed):
        day = observed.astimezone(KST).date().isoformat()
        self.fetched[symbol] = self.bucket(observed).isoformat()
        existing = store.get("data_quarantine:" + symbol) or {}
        previous = store.get("data:" + symbol) or {}
        closed = datetime.fromisoformat(exc.start) + timedelta(minutes=1)
        with store.transaction():
            store.put("data_quarantine:" + symbol, {
                "day": day,
                "reason": "completed_bar_revised",
                "start": exc.start,
                "detected_at": observed.isoformat(),
            })
            store.put("candidate_status:" + symbol, "completed_bar_revised_quarantined")
            if existing.get("day") != day or existing.get("start") != exc.start:
                store.audit("market_data_quarantined", {
                    "symbol": symbol,
                    "reason": "completed_bar_revised",
                    "start": exc.start,
                    "stored": exc.stored,
                    "revised": exc.revised,
                    "changed": exc.changed,
                    "observed_at": observed.isoformat(),
                    "previous_observed_at": previous.get("observed_at"),
                    "seconds_after_close": (observed - closed).total_seconds(),
                }, observed)

    def record_failure(self, store, symbol, exc, observed):
        """Count transient read failures without touching a symbol's candidate status."""

        detail = safe_failure(exc, "collector_minutes")
        key = ":".join(str(detail.get(k) or "") for k in ("stage", "error", "broker_code"))
        counts = store.get("collector_error_counts", {})
        counts[key] = counts.get(key, 0) + 1
        store.put("collector_error_counts", counts)
        previous = store.get("collector_last_error", {})
        if (previous.get("key") != key or not previous.get("at") or
                (observed - datetime.fromisoformat(previous["at"])).total_seconds()
                >= FAILURE_AUDIT_INTERVAL_SECONDS):
            store.audit(
                "collector_request_failed",
                detail | {"symbol": symbol, "count": counts[key]},
                observed,
            )
            store.put(
                "collector_last_error",
                {"key": key, "symbol": symbol, "at": observed.isoformat()},
            )

    def run(self):
        store = Store(self.path)
        try:
            while not self.stopping.is_set():
                try:
                    self.collect_once(store)
                    store.put("collector_error", None)
                except Exception as exc:
                    store.put("collector_error", type(exc).__name__)
                self.stopping.wait(self.poll_seconds)
        finally:
            store.close()
