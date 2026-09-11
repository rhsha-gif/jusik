from datetime import datetime, timedelta, timezone

from quantpilot.paper.calendar import Session
from quantpilot.paper.intraday.shadow import simulate, finalize
from quantpilot.paper.intraday.evaluation import Experiment
from quantpilot.paper.intraday.strategy import SPECS

OPEN = datetime(2026, 9, 11, tzinfo=timezone.utc)
SESSION = Session(OPEN, OPEN + timedelta(hours=6))


def event(seconds, kind, ident, **values):
    at = (OPEN + timedelta(seconds=seconds)).isoformat()
    return {
        "id": ident,
        "kind": kind,
        "symbol": "005930",
        "event_at": at,
        "received_at": at,
        **values,
    }


def observation():
    return {
        "at": (OPEN + timedelta(seconds=60)).isoformat(),
        "issues": [],
        "rules_weights": {"opening_range_breakout": 0.6},
        "ai_weights": {"opening_range_breakout": 0.48},
        "signals": [
            {
                "strategy_id": "opening_range_breakout",
                "symbol": "005930",
                "price": 100,
                "stop": 95,
                "target": 105,
                "score": 0.5,
                "reason": "fixture",
                "version": SPECS[0].version,
                "entry_atr14": 1,
            }
        ],
    }


def test_touch_never_fills_and_duplicate_partial_tape_is_not_reused():
    book = event(59, "quote", "q1", bid=99, ask=100, bid_size=1000, ask_size=1000)
    tick = event(59, "tick", "t1", price=100, quantity=2000)
    touch = event(62, "tick", "t2", price=100, quantity=2000)
    through = event(63, "tick", "t3", price=99, quantity=2000)
    no_fill = simulate([observation()], [book, tick, touch], SESSION, "rules")
    assert no_fill["unresolved"] == [] and no_fill["round_trips"] == 0
    events = [
        book,
        tick,
        touch,
        through,
        event(125, "quote", "q2", bid=105, ask=106, bid_size=1000, ask_size=1000),
        event(127, "tick", "t4", price=106, quantity=2000),
    ]
    once = simulate([observation()], events, SESSION, "rules")
    twice = simulate([observation()], events + [through], SESSION, "rules")
    assert once == twice
    assert once["round_trips"] == 1 and once["trades"][0]["quantity"] == 20
    assert once["partial_fills"] == 1


def test_absent_realtime_observations_cannot_qualify_a_session(tmp_path):
    e = Experiment(tmp_path / "research.sqlite3")
    result = finalize(
        e,
        {
            "bars": [],
            "universes": [],
            "events": [],
            "data_mode": "fixture",
            "sha256": "test",
        },
        SESSION,
        SESSION.closes,
    )
    assert "incomplete_realtime_session_coverage" in result["quality_issues"]
    assert result["orders_submitted"] == 0
    e.close()


def test_delayed_tick_from_before_order_cannot_fill():
    book = event(59, "quote", "q1", bid=99, ask=100, bid_size=1000, ask_size=1000)
    tick = event(59, "tick", "t1", price=100, quantity=2000)
    late = event(59, "tick", "late", price=99, quantity=2000)
    late["received_at"] = event(63, "tick", "clock")["received_at"]
    result = simulate([observation()], [book, tick, late], SESSION, "rules")
    assert result["unresolved"] == [] and result["partial_fills"] == 0
    assert result["ending_equity"] == 5_000_000


def test_partial_buy_is_cancelled_before_protective_exit():
    events = [
        event(59, "quote", "q1", bid=99, ask=100, bid_size=1000, ask_size=1000),
        event(59, "tick", "t1", price=100, quantity=2000),
        event(63, "tick", "t2", price=99, quantity=2000),
        event(64, "quote", "q2", bid=90, ask=91, bid_size=1000, ask_size=1000),
        event(66, "tick", "t3", price=91, quantity=2000),
    ]
    result = simulate([observation()], events, SESSION, "rules")
    assert result["unresolved"] == [] and result["round_trips"] == 1
    assert result["trades"][0]["quantity"] == 20
    assert result["trades"][0]["reason"] == "stop"
    assert result["trades"][0]["net_pnl"] < -200
    assert result["missed_quantity"] > 0
