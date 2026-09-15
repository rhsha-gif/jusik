"""A supervised REST restart retires only an obsolete stream incident."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from quantpilot.paper.broker import FixtureGateway
from quantpilot.paper.calendar import Session
from quantpilot.paper.diagnostics import open_incident
from quantpilot.paper.runtime import Runtime
from quantpilot.paper.store import Store

NOW = datetime(2026, 9, 15, 1, tzinfo=timezone.utc)


def setup_rest(tmp_path):
    store = Store(tmp_path / "experiment.sqlite3")
    store.configure({"supervisor_enabled": True}, 1)
    store.control("pause", now=NOW)
    store.put("day", "2026-09-15")
    store.put("day_base_valid", True)
    store.put("recovery_required", True)
    store.put("universe", ["005930"])
    store.put("data:005930", {"quality": "completed_bars_validated",
                              "last_bar": (NOW - timedelta(minutes=2)).isoformat()})
    open_incident(store, "feed", "feed_unavailable", NOW - timedelta(minutes=10))
    calendar = SimpleNamespace(session=lambda _: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5)))
    runtime = Runtime(store, SimpleNamespace(), FixtureGateway(store), calendar, lambda: NOW, background_data=True)
    return store, runtime


def test_verified_rest_restart_retires_only_the_obsolete_feed_incident(tmp_path):
    store, runtime = setup_rest(tmp_path)
    try:
        open_incident(store, "operator", "manual_review", NOW)
        result = runtime.cycle()
        assert "feed" not in store.get("incidents")
        assert store.get("incidents")["operator"]["reason_code"] == "manual_review"
        assert store.get("recovery_required") is False
        assert store.get("control") == "paused"
        assert store.get("resume_authorized_day") is None
        assert not result["new_entries"] and not store.orders()
        assert store.db.execute("SELECT COUNT(*) FROM audit WHERE kind='incident_recovered'").fetchone()[0] == 1
    finally:
        store.close()


@pytest.mark.parametrize("blocker", ["empty", "stale", "future", "quality", "reconcile", "baseline", "daily_halt", "drawdown", "other_feed", "hybrid", "no_restart"])
def test_rest_restart_never_retires_incident_without_its_scoped_proof(tmp_path, blocker):
    store, runtime = setup_rest(tmp_path)
    try:
        if blocker == "empty":
            store.put("universe", [])
        elif blocker in {"stale", "future", "quality"}:
            data = store.get("data:005930")
            if blocker == "quality":
                data["quality"] = "unverified"
            else:
                data["last_bar"] = (NOW + timedelta(seconds=1) if blocker == "future" else NOW - timedelta(minutes=3)).isoformat()
            store.put("data:005930", data)
        elif blocker == "reconcile":
            runtime.gateway.reconcile = lambda _: False
        elif blocker == "baseline":
            store.put("day_base_valid", False)
        elif blocker in {"daily_halt", "drawdown"}:
            store.put("intraday_loss_state", {"day": "2026-09-15", "day_base": 5_000_000,
                                               "daily_halted": blocker == "daily_halt", "drawdown_halted": blocker == "drawdown"})
        elif blocker == "other_feed":
            open_incident(store, "feed", "quote_stale_or_crossed", NOW)
        elif blocker == "hybrid":
            store.configure({"hybrid_feed_enabled": True}, 2)
        elif blocker == "no_restart":
            store.put("recovery_required", False)
        runtime.cycle()
        assert store.get("incidents").get("feed")
        assert store.get("control") == "paused" and not store.orders()
    finally:
        store.close()
