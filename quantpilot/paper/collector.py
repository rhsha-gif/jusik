"""Background market reads share the paper request budget, never the order loop."""

from datetime import datetime
import threading

from quantpilot.paper.store import Store
from quantpilot.paper.calendar import KST


class Collector:
    def __init__(self, path, market, calendar, clock):
        self.path = path
        self.market = market
        self.calendar = calendar
        self.clock = clock
        self.stopping = threading.Event()
        self.thread = threading.Thread(
            target=self.run, name="paper-market-data", daemon=True
        )

    def start(self):
        self.thread.start()

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=15)

    def collect_once(self, store):
        now = self.clock()
        session = self.calendar.session(now)
        if not session or not session.trading(now):
            return
        at = store.get("universe_at")
        if at is None or (now - datetime.fromisoformat(at)).total_seconds() >= 300:
            symbols, source = self.market.candidates(now, store.policy.max_candidates)
            with store.transaction():
                store.put("universe", symbols)
                store.put("universe_at", now.isoformat())
                store.put("universe_source", source)
        symbols = list(
            dict.fromkeys(
                [p["symbol"] for p in store.positions()] + store.get("universe", [])
            )
        )
        observations = {}
        for symbol in symbols:
            if self.stopping.is_set() or not session.trading(self.clock()):
                break
            try:
                observed = self.clock()
                bars = self.market.minutes(symbol, observed)
                store.save_bars(bars)
                if bars:
                    observations[symbol] = {
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
                    },
                )
            except Exception as exc:
                if isinstance(exc, ValueError) and str(exc) == "completed_bar_revised":
                    store.put("data_quarantine:" + symbol, {
                        "day": observed.astimezone(KST).date().isoformat(),
                        "reason": "completed_bar_revised",
                    })
                    store.audit("market_data_quarantined", {
                        "symbol": symbol, "reason": "completed_bar_revised",
                    }, observed)
                store.put("candidate_status:" + symbol, type(exc).__name__)
        store.put("collector_heartbeat", self.clock().isoformat())
        if observations:
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

    def run(self):
        store = Store(self.path)
        try:
            while not self.stopping.is_set():
                try:
                    self.collect_once(store)
                    store.put("collector_error", None)
                except Exception as exc:
                    store.put("collector_error", type(exc).__name__)
                self.stopping.wait(10)
        finally:
            store.close()
