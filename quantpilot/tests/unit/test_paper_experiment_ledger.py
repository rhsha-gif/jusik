from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from quantpilot.paper.store import Store

NOW = datetime(2026, 9, 10, 1, tzinfo=timezone.utc)


def intent():
    return SimpleNamespace(
        symbol="005930",
        strategy_id="trend_pullback",
        stop=9900.0,
        target=10400.0,
        version="1",
    )


def reserve(s, id="buy", side="buy", qty=10):
    return s.reserve(
        order_id=id,
        signal=intent(),
        quantity=qty,
        price=10000,
        side=side,
        now=NOW,
        policy_version=1,
        reason="test",
    )


def test_pause_and_config_survive_restart(tmp_path):
    s = Store(tmp_path / "state.sqlite")
    s.control("start")
    s.control("pause")
    s.configure({"max_positions": 3}, 1)
    s.close()
    s = Store(tmp_path / "state.sqlite")
    assert s.get("control") == "paused" and s.policy.max_positions == 3
    with pytest.raises(ValueError, match="version_conflict"):
        s.configure({}, 1)
    with pytest.raises(ValueError):
        s.configure({"trade_risk": 0.01}, 2)
    s.close()


def test_cumulative_partial_fills_only_applied_once(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    assert reserve(s)
    s.update_order("buy", "partially_filled", 4, 40000, NOW)
    cash = s.get("cash")
    s.update_order("buy", "partially_filled", 4, 40000, NOW)
    assert s.get("cash") == cash and s.positions()[0]["quantity"] == 4
    s.update_order("buy", "filled", 10, 100000, NOW)
    assert s.positions()[0]["quantity"] == 10
    s.control("pause")
    reserve(s, "sell", "sell")
    s.update_order("sell", "filled", 10, 102000, NOW + timedelta(minutes=1))
    assert not s.positions() and 5_000_000 < s.get("cash") < 5_002_000
    assert s.db.execute("SELECT gross_pnl FROM trades").fetchone()[0] == pytest.approx(
        2000
    )
    s.verify_cash()
    with pytest.raises(ValueError):
        s.update_order("buy", "filled", 9, 90000, NOW)
    s.close()


def test_pending_symbol_prevents_second_reservation(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    reserve(s)
    assert not reserve(s)
    with pytest.raises(ValueError, match="pending"):
        reserve(s, "another")
    s.close()


def test_unknown_outcome_reserves_and_cannot_be_refilled_backwards(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    reserve(s)
    s.update_order("buy", "outcome_unknown", 0, 0, NOW)
    assert len(s.orders(True)) == 1
    with pytest.raises(ValueError):
        s.update_order("buy", "filled", 10, float("nan"), NOW)
    assert not s.positions()
    s.close()
