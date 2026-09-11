from datetime import timedelta
from types import SimpleNamespace

import pytest

from quantpilot.paper.store import Store
from quantpilot.paper.risk import entry_size
from quantpilot.tests.unit.test_paper_runtime_controls import NOW, quote, signal


def prepared(tmp_path, monkeypatch):
    s = Store(tmp_path / "experiment.sqlite3")
    s.configure({"strategy_generation": "intraday_v2", "max_positions": 2}, 1)
    s.control("start")
    monkeypatch.setattr(
        "quantpilot.paper.intraday.deployment.admitted", lambda *a: True
    )
    s.put("intraday_feed_at", NOW.isoformat())
    s.put(
        "intraday_universe",
        {
            "at": NOW.isoformat(),
            "symbols": ["005930", "000660"],
            "metadata": {
                "eligibility": {
                    symbol: {
                        "available_at": NOW.isoformat(),
                        "market": "KOSPI",
                        "instrument": "common_stock",
                        "suspended": False,
                        "source_verified": True,
                    }
                    for symbol in ("005930", "000660")
                }
            },
        },
    )
    for symbol in ("005930", "000660"):
        for kind in ("tick", "quote"):
            s.put(
                "intraday_event:" + symbol + ":" + kind,
                {"event_at": NOW.isoformat(), "received_at": NOW.isoformat()},
            )
    return s


def test_target_must_pay_both_legs(tmp_path, monkeypatch):
    s = prepared(tmp_path, monkeypatch)
    value = signal()
    value.target = 10020
    assert entry_size(s, value, quote(), 0.5, NOW) == 0
    s.close()


def test_daily_budget_reserves_pending_and_partial_positions(tmp_path, monkeypatch):
    from quantpilot.paper.intraday.controls import loss_budget

    s = prepared(tmp_path, monkeypatch)
    s.put("cash", 4_970_000.0)
    s.put("realized", -30_000.0)
    s.put("day_base", 5_000_000.0)
    n = entry_size(s, signal(), quote(), 0.5, NOW)
    assert n > 0
    s.reserve(
        order_id="pending",
        signal=signal(),
        quantity=n,
        price=10000,
        side="buy",
        now=NOW,
        policy_version=2,
        reason="fixture",
    )
    before = loss_budget(s, NOW)["reserved"]
    s.update_order("pending", "partially_filled", n // 2, n // 2 * 10000, NOW)
    after = loss_budget(s, NOW)["reserved"]
    assert after >= before - 1
    value = signal()
    value.symbol = "000660"
    q = SimpleNamespace(symbol="000660", last=10000, bid=9990, ask=10000, as_of=NOW)
    n2 = entry_size(s, value, q, 0.5, NOW)
    assert (n + n2) * (100 + 10000 * 32.81054 / 10000) <= 20_001
    s.close()


def test_halts_survive_restart_and_policy_changes(tmp_path, monkeypatch):
    from quantpilot.paper.intraday.controls import loss_budget

    s = prepared(tmp_path, monkeypatch)
    s.put("day_base", 5_000_000.0)
    s.put("cash", 4_949_000.0)
    s.put("realized", -51_000.0)
    assert loss_budget(s, NOW)["daily_halted"]
    s.close()
    s = Store(tmp_path / "experiment.sqlite3")
    s.put("cash", 5_000_000.0)
    s.put("realized", 0.0)
    assert loss_budget(s, NOW)["daily_halted"]
    assert not loss_budget(s, NOW + timedelta(days=1))["daily_halted"]
    s.put("cash", 4_740_000.0)
    s.put("realized", -260_000.0)
    assert loss_budget(s, NOW + timedelta(days=1))["drawdown_halted"]
    s.configure({"ai_enabled": True}, s.policy.version)
    s.put("cash", 5_000_000.0)
    assert loss_budget(s, NOW + timedelta(days=2))["drawdown_halted"]
    with pytest.raises(ValueError, match="generation"):
        s.configure({"strategy_generation": "legacy"}, s.policy.version)
    s.close()


def test_no_admission_or_stale_tick_cannot_enter(tmp_path, monkeypatch):
    s = prepared(tmp_path, monkeypatch)
    s.put("intraday_feed_at", (NOW - timedelta(seconds=60)).isoformat())
    assert entry_size(s, signal(), quote(), 0.5, NOW) == 0
    s.put("intraday_feed_at", NOW.isoformat())
    monkeypatch.setattr(
        "quantpilot.paper.intraday.deployment.admitted", lambda *a: False
    )
    assert entry_size(s, signal(), quote(), 0.5, NOW) == 0
    s.close()


def test_security_type_and_symbol_specific_tape_are_final_gates(tmp_path, monkeypatch):
    s = prepared(tmp_path, monkeypatch)
    assert entry_size(s, signal(), quote(), 0.5, NOW) > 0
    universe = s.get("intraday_universe")
    universe["metadata"]["eligibility"]["005930"]["instrument"] = "etf"
    s.put("intraday_universe", universe)
    assert entry_size(s, signal(), quote(), 0.5, NOW) == 0
    universe["metadata"]["eligibility"]["005930"]["instrument"] = "common_stock"
    s.put("intraday_universe", universe)
    s.put(
        "intraday_event:005930:tick",
        {
            "received_at": NOW.isoformat(),
            "event_at": (NOW - timedelta(seconds=60)).isoformat(),
        },
    )
    assert entry_size(s, signal(), quote(), 0.5, NOW) == 0
    s.close()


@pytest.mark.parametrize("failure", ["version_changed", "invalid_history"])
def test_invalid_strategy_evidence_cannot_disable_protective_exit(
    tmp_path, monkeypatch, failure
):
    from quantpilot.paper.broker import FixtureGateway
    from quantpilot.paper.calendar import Session
    from quantpilot.paper.runtime import Runtime
    from quantpilot.paper.intraday.strategy import SPECS

    s = prepared(tmp_path, monkeypatch)
    entry = signal()
    entry.version = (
        "intraday2:obsolete" if failure == "version_changed" else SPECS[2].version
    )
    s.reserve(
        order_id="buy",
        signal=entry,
        quantity=10,
        price=10000,
        side="buy",
        now=NOW,
        policy_version=s.policy.version,
        reason="fixture",
    )
    s.update_order("buy", "filled", 10, 100000, NOW)
    s.control("pause")
    if failure == "invalid_history":

        def unavailable(*args):
            raise ValueError("invalid_bars")

        monkeypatch.setattr(
            "quantpilot.paper.intraday.strategy.protective_exit", unavailable
        )
    market = SimpleNamespace(quotes=lambda symbols: {"005930": quote()})
    calendar = SimpleNamespace(
        session=lambda now: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5))
    )
    runtime = Runtime(s, market, FixtureGateway(s), calendar, lambda: NOW)
    assert runtime.cycle()["new_entries"] is False
    assert not s.positions()
    assert s.get("control") == "paused"
    assert any(o["side"] == "sell" for o in s.orders())
    s.close()


def test_prior_slippage_is_not_charged_again_to_next_day_loss(tmp_path, monkeypatch):
    from quantpilot.paper.intraday.controls import loss_budget
    from quantpilot.paper.runtime import Runtime

    s = prepared(tmp_path, monkeypatch)
    for side in ("buy", "sell"):
        s.reserve(
            order_id=side,
            signal=signal(),
            quantity=100,
            price=10000,
            side=side,
            now=NOW,
            policy_version=s.policy.version,
            reason="fixture",
        )
        s.update_order(side, "filled", 100, 1000000, NOW)
    old = loss_budget(s, NOW)
    assert old["equity"] < s.get("cash")
    runtime = Runtime(s, None, None, None, lambda: NOW)
    assert runtime.equity() == old["equity"]
    tomorrow = NOW + timedelta(days=1)
    s.put("day", tomorrow.date().isoformat())
    s.put("day_base", s.get("cash"))
    new = loss_budget(s, tomorrow)
    assert new["day_base"] == new["equity"] == old["equity"]
    assert new["available"] == pytest.approx(new["day_base"] * 0.01)
    s.close()


def test_sell_fill_rejects_wrong_position_version_atomically(tmp_path, monkeypatch):
    s = prepared(tmp_path, monkeypatch)
    s.reserve(
        order_id="buy",
        signal=signal(),
        quantity=10,
        price=10000,
        side="buy",
        now=NOW,
        policy_version=s.policy.version,
        reason="fixture",
    )
    s.update_order("buy", "filled", 10, 100000, NOW)
    wrong = signal()
    wrong.version = "wrong-version"
    s.reserve(
        order_id="sell",
        signal=wrong,
        quantity=10,
        price=10000,
        side="sell",
        now=NOW,
        policy_version=s.policy.version,
        reason="fixture",
    )
    before = (s.get("cash"), s.positions())
    with pytest.raises(ValueError, match="sell_exceeds_attributed_position"):
        s.update_order("sell", "filled", 10, 100000, NOW)
    assert (s.get("cash"), s.positions()) == before
    s.close()
