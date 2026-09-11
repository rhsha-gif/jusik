from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from quantpilot.paper.calendar import Session
from quantpilot.paper.intraday.runtime import allocate, schedule
from quantpilot.paper.strategy import Signal
from quantpilot.paper.store import Store


def test_ai_budget_is_bounded_and_never_creates_strategy():
    signal = Signal("trend_pullback", "005930", 100, 95, 110, 0.5, "fixture")
    rules = allocate([signal], None, cap=0.6)
    ai = SimpleNamespace(strategy_scores={"trend_pullback": 0, "range_reversion": 1})
    assert allocate([signal], ai)["trend_pullback"] == rules["trend_pullback"] * 0.8
    assert "range_reversion" not in allocate([signal], ai)
    assert allocate([], ai) == {}


def test_regular_cadence_persists_and_coalesces(tmp_path):
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    session = Session(now, now + timedelta(hours=6))
    s = Store(tmp_path / "paper.sqlite3")
    schedule(s, {}, now, session)
    first = s.get("ai_due")
    schedule(s, {}, now + timedelta(minutes=29), session)
    assert s.get("ai_due") == first
    s.close()
    s = Store(tmp_path / "paper.sqlite3")
    schedule(s, {}, now + timedelta(minutes=30), session)
    assert s.get("ai_due") != first
    s.close()
