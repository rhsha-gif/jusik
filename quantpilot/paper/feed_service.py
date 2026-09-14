"""Thread-owned ledger adapter for the hybrid feed; notices only wake REST checks."""

from collections import Counter
from datetime import datetime
import threading

from quantpilot.paper.calendar import KST
from quantpilot.paper.store import Store


class FeedService:
    def __init__(self, path, feed, clock):
        self.path, self.feed, self.clock = path, feed, clock
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="paper-hybrid-feed", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=20)

    def run(self):
        store = Store(self.path)
        counts, latest = Counter(), {}
        flushed = None
        try:
            def observe(event):
                kind = event["kind"]
                counts[kind] += 1
                now = self.clock()
                if kind == "market_observation":
                    latest[event["symbol"] + ":" + event["event_kind"]] = event
                    day = now.astimezone(KST).date().isoformat()
                    if not store.get("market_ingress:" + day):
                        store.put("market_ingress:" + day, {"at": now.isoformat()})
                elif kind == "reconciliation_hint":
                    # Only a hint crosses this boundary. No quantities, fills or account fields.
                    with store.transaction():
                        store.put("reconcile_wakeup", event["event_id"])
                        if event.get("notice_kind") == "execution":
                            store.put("paper_ingress_verified:" + now.astimezone(KST).date().isoformat(),
                                      {"execution_notice": True, "market_observed": bool(latest),
                                       "at": now.isoformat(), "event_id": event["event_id"]})
                        store.audit("paper_execution_notice_hint", event, now)
                else:
                    with store.transaction():
                        if kind in {"feed_disconnected", "feed_stopped", "feed_connected"}:
                            store.put("feed_entry_ready", False)
                            store.put("reconcile_wakeup", now.isoformat())
                            store.put("feed_status", {**self.feed.status(), "last_event": kind, "observed_at": now.isoformat()})
                        if kind != "frame_rejected" or counts[kind] <= 3:
                            store.audit("hybrid_feed", event, now)

            def desired():
                nonlocal flushed
                now = self.clock()
                # Persist coalesced market timing, not every tick, to bound SQLite contention.
                if flushed is None or (now - flushed).total_seconds() >= 1:
                    with store.transaction():
                        store.put("feed_status", {**self.feed.status(), "counts": dict(counts),
                                                  "observed_at": now.isoformat(), "market_times": latest})
                    flushed = now
                return self.feed.desired(store.get("universe", [])[:20],
                                         [p["symbol"] for p in store.positions()],
                                         [o["symbol"] for o in store.orders(True)])
            self.feed.run(self.stop, desired, observe)
        except Exception:
            store.put("feed_entry_ready", False)
            store.put("feed_status", {"state": "failed", "reason": "feed_worker_failed", "observed_at": self.clock().isoformat()})
        finally:
            store.close()
