"""Separate-process-style worker boundary for shadow advisory calls; no orders."""

from datetime import datetime, timezone
import threading

from quantpilot.paper.store import Store
from quantpilot.paper.intraday.runtime import schedule
from quantpilot.paper.intraday.shadow import histories_at


class ShadowAdvisor:
    def __init__(self, path, clock=None):
        self.path = path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.stop = threading.Event()
        with_store = Store(path)
        try:
            if with_store.policy.strategy_generation != "intraday_v2":
                with_store.configure(
                    {
                        "strategy_generation": "intraday_v2",
                        "max_positions": 2,
                        "ai_enabled": True,
                    },
                    with_store.policy.version,
                )
        finally:
            with_store.close()
        self.thread = threading.Thread(
            target=self.run, name="shadow-advisory", daemon=True
        )

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)

    def offer(self, dataset, now, session):
        histories = histories_at(dataset, now, session)
        store = Store(self.path)
        try:
            store.put(
                "evidence",
                {
                    "observed_at": now.isoformat(),
                    "symbols": list(histories),
                    "strategies": list(store.policy.active_strategies),
                    "market_data": {
                        s: [
                            {
                                "start": b.start.isoformat(),
                                "close": b.close,
                                "volume": b.volume,
                            }
                            for b in bars[-30:]
                        ]
                        for s, bars in histories.items()
                    },
                },
            )
            schedule(store, histories, now, session)
        finally:
            store.close()

    def assessment(self, now):
        from quantpilot.paper.intelligence import Assessment
        from quantpilot.paper.store import encode

        store = Store(self.path)
        try:
            raw = store.get("assessment")
            value = Assessment.model_validate_json(encode(raw)) if raw else None
            return value if value and value.usable(now) else None
        except ValueError:
            return None
        finally:
            store.close()

    def run(self):
        from quantpilot.paper.jobs import work_once, recover_interrupted_jobs
        from quantpilot.paper.cli import process_lock

        try:
            with process_lock(str(self.path) + ".worker.lock"):
                store = Store(self.path)
                try:
                    recover_interrupted_jobs(store)
                    while not self.stop.is_set():
                        work_once(store, self.clock())
                        self.stop.wait(1)
                finally:
                    store.close()
        except Exception:
            # Worker failure cannot block market observation. Missing/expired assessment is rules-only.
            return
