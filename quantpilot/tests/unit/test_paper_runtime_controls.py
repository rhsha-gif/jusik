from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys
import types
import pytest
from quantpilot.paper.store import Store
from quantpilot.paper.broker import FixtureGateway
from quantpilot.paper.runtime import Runtime
from quantpilot.paper.risk import entry_size
from quantpilot.paper.calendar import Session
from quantpilot.packages.core.marketdata.types import Quote

NOW = datetime(2026, 9, 10, 1, tzinfo=timezone.utc)


def signal():
    return SimpleNamespace(
        symbol="005930",
        strategy_id="trend_pullback",
        price=10000.0,
        stop=9900.0,
        target=10300.0,
        score=0.8,
        version="1",
        reason="test",
    )


def quote(at=NOW):
    return Quote(symbol="005930", last=10000, bid=9990, ask=10000, as_of=at)


def test_risk_budget_and_no_account_topup(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    n = entry_size(s, signal(), quote(), 0.6, NOW)
    assert 0 < n <= 125
    assert n * (100 + 10000 * (2 * 1.40527 + 20 + 10) / 10000) <= 25000
    s.put("cash", 1000)
    assert entry_size(s, signal(), quote(), 0.6, NOW) == 0
    s.close()


def test_pause_does_not_disable_protective_sell(tmp_path, monkeypatch):
    stub = types.ModuleType("quantpilot.paper.strategy")
    stub.evaluate_strategies = lambda *a: []
    stub.allocate_weights = lambda *a, **k: {}
    stub.select_signals = lambda *a: []
    monkeypatch.setitem(sys.modules, "quantpilot.paper.strategy", stub)
    s = Store(tmp_path / "s")
    s.control("start")
    s.reserve(
        order_id="buy",
        signal=signal(),
        quantity=10,
        price=10000,
        side="buy",
        now=NOW,
        policy_version=1,
        reason="entry",
    )
    s.update_order("buy", "filled", 10, 100000, NOW)
    s.control("pause")
    market = SimpleNamespace(
        quotes=lambda symbols: {
            "005930": Quote(symbol="005930", last=9850, bid=9850, ask=9860, as_of=NOW)
        }
    )
    calendar = SimpleNamespace(
        session=lambda now: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5))
    )
    r = Runtime(s, market, FixtureGateway(s), calendar, lambda: NOW)
    assert r.cycle()["new_entries"] is False
    assert not s.positions() and s.get("control") == "paused"
    s.close()


def test_stale_quotes_and_future_values_rejected(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    for at in (NOW - timedelta(seconds=60), NOW + timedelta(seconds=1)):
        with pytest.raises(ValueError):
            entry_size(s, signal(), quote(at), 0.6, NOW)
    s.close()


def test_policy_and_pause_rechecked_before_reservation(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    s.control("pause")
    with pytest.raises(ValueError, match="paused"):
        s.reserve(
            order_id="buy",
            signal=signal(),
            quantity=1,
            price=10000,
            side="buy",
            now=NOW,
            policy_version=1,
            reason="entry",
        )
    s.close()


def test_cli_factory_blocks_before_credentials_or_network(tmp_path, monkeypatch):
    from quantpilot.paper.cli import build_runtime
    from quantpilot.packages.core import kis_paper

    monkeypatch.setattr(
        kis_paper,
        "KisPaperClient",
        lambda *a, **kw: pytest.fail("client constructed before gate"),
    )
    s = Store(tmp_path / "s")
    with pytest.raises(ValueError, match="fixture_requires"):
        build_runtime(s, {})
    s.configure({"data_mode": "paper_trading"}, 1)
    with pytest.raises(ValueError, match="paper_submission_disabled"):
        build_runtime(s, {})
    with pytest.raises(ValueError, match="unsafe_environment"):
        build_runtime(s, {"LIVE_TRADING_ENABLED": "true"})
    s.close()


class RefusingGateway(FixtureGateway):
    """Fixture gateway whose reconcile raises the queued refusals first."""

    def __init__(self, store, refusals):
        super().__init__(store)
        self.refusals = list(refusals)
        self.reconcile_calls = 0

    def reconcile(self, now):
        self.reconcile_calls += 1
        if self.refusals:
            raise self.refusals.pop(0)
        return True


def refusal():
    from quantpilot.packages.core.kis_paper import KisPaperGatewayRejected

    return KisPaperGatewayRejected("refused (code=EGW00201)", code="EGW00201")


def audits(store, kind):
    return [r for r in store.db.execute("SELECT kind FROM audit") if r[0] == kind]


def backoff_runtime(store, refusals, clock):
    calendar = SimpleNamespace(
        session=lambda now: Session(NOW - timedelta(hours=1), NOW + timedelta(hours=5))
    )
    gateway = RefusingGateway(store, refusals)
    return Runtime(store, SimpleNamespace(), gateway, calendar, clock, background_data=True), gateway


def test_gateway_refusal_backs_off_one_cycle_then_recovers(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    now = [NOW]
    runtime, gateway = backoff_runtime(s, [refusal()], lambda: now[0])
    assert runtime.cycle() == {"status": "blocked", "reason": "execution_reconciliation_required"}
    assert s.get("broker_read_failures") == 1
    assert s.get("broker_retry_after") == (NOW + timedelta(seconds=2)).isoformat()
    assert s.get("reconciliation_complete") is False
    assert s.get("cycle_error_counts") == {"execution_cycle:KisPaperGatewayRejected:EGW00201": 1}
    assert len(audits(s, "cycle_failed")) == 1
    now[0] = NOW + timedelta(seconds=1)
    assert runtime.cycle() == {"status": "blocked", "reason": "broker_read_backoff"}
    assert gateway.reconcile_calls == 1  # no broker call inside the backoff window
    now[0] = NOW + timedelta(seconds=3)
    assert runtime.cycle()["status"] == "running"
    assert gateway.reconcile_calls >= 2
    assert s.get("broker_read_failures") == 0 and s.get("broker_retry_after") is None
    s.close()


def test_gateway_refusal_backoff_doubles_and_caps_at_sixty_seconds(tmp_path):
    s = Store(tmp_path / "s")
    s.control("start")
    now = [NOW]
    runtime, _ = backoff_runtime(s, [refusal() for _ in range(7)], lambda: now[0])
    delays = []
    for _ in range(7):
        assert runtime.cycle()["reason"] == "execution_reconciliation_required"
        retry_after = datetime.fromisoformat(s.get("broker_retry_after"))
        delays.append((retry_after - now[0]).total_seconds())
        now[0] = retry_after
    assert delays == [2, 4, 8, 16, 32, 60, 60]
    assert s.get("broker_read_failures") == 6
    s.close()
