import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quantpilot.paper import dashboard
from quantpilot.paper.dashboard import (
    SeriesStore,
    create_app,
    episodes,
    ledger,
    open_ledger,
    sample_once,
    serve,
    strategy_stats,
    summary,
    timeline,
)
from quantpilot.paper.store import Store

NOW = datetime(2026, 9, 11, 1, tzinfo=timezone.utc)
R_BUDGET = 5_000_000 * 0.005


def intent(symbol="005930", strategy="trend_pullback"):
    return SimpleNamespace(
        symbol=symbol, strategy_id=strategy, stop=9900.0, target=10400.0, version="1"
    )


def reserve(s, id, side, symbol="005930", strategy="trend_pullback", qty=10, at=NOW):
    return s.reserve(
        order_id=id,
        signal=intent(symbol, strategy),
        quantity=qty,
        price=10000,
        side=side,
        now=at,
        policy_version=1,
        reason="test_" + side,
    )


def seed(path):
    """One winning trip, one losing trip, one open position, one partial exit."""

    s = Store(path)
    s.control("start")
    t = NOW
    # winner: partial then full buy, full sell at 10200
    reserve(s, "b1", "buy", at=t)
    s.update_order("b1", "partially_filled", 4, 40000, t)
    s.update_order("b1", "filled", 10, 100000, t)
    reserve(s, "s1", "sell", at=t + timedelta(minutes=1))
    s.update_order("s1", "filled", 10, 102000, t + timedelta(minutes=2))
    # loser on another symbol and strategy, sold at 9800
    reserve(s, "b2", "buy", "000660", "range_reversion", at=t + timedelta(minutes=3))
    s.update_order("b2", "filled", 10, 100000, t + timedelta(minutes=3))
    reserve(s, "s2", "sell", "000660", "range_reversion", at=t + timedelta(minutes=4))
    s.update_order("s2", "filled", 10, 98000, t + timedelta(minutes=5))
    # open position with a partially filled, then cancelled, exit
    reserve(s, "b3", "buy", "035420", "trend_pullback", at=t + timedelta(minutes=6))
    s.update_order("b3", "filled", 10, 100000, t + timedelta(minutes=6))
    reserve(s, "s3", "sell", "035420", "trend_pullback", at=t + timedelta(minutes=7))
    s.update_order("s3", "partially_filled", 4, 41200, t + timedelta(minutes=8))
    s.update_order("s3", "cancelled", 4, 41200, t + timedelta(minutes=9))
    s.put("marks", {"035420": 10300.0})
    s.put("marks_at", {"035420": (t + timedelta(minutes=9)).isoformat()})
    s.put("weights", {"trend_pullback": 0.6, "range_reversion": 0.4})
    s.verify_cash()
    return s


def trade_sums(s, order_id):
    return s.db.execute(
        "SELECT SUM(pnl),SUM(adjusted_pnl),SUM(gross_pnl) FROM trades WHERE id LIKE ?",
        (order_id + ":%",),
    ).fetchone()


def test_episodes_rebuild_round_trips_from_orders(tmp_path):
    s = seed(tmp_path / "experiment.sqlite3")
    with ledger(tmp_path / "experiment.sqlite3") as view:
        eps = {e["symbol"]: e for e in episodes(view)}
    win, loss, open_ep = eps["005930"], eps["000660"], eps["035420"]
    assert win["outcome"] == "win" and win["entry_ids"] == ["b1"] and win["exit_ids"] == ["s1"]
    assert win["entry_price"] == 10000 and win["exit_price"] == 10200
    net, adjusted, gross = trade_sums(s, "s1")
    assert win["net"] == pytest.approx(net)
    assert win["adjusted"] == pytest.approx(adjusted)
    assert win["gross"] == pytest.approx(gross) == pytest.approx(2000)
    assert win["r"] == pytest.approx(adjusted / R_BUDGET)
    assert win["reason"] == "test_sell" and win["exit_at"] >= win["entry_at"]
    assert loss["outcome"] == "loss" and loss["strategy"] == "range_reversion"
    assert loss["net"] == pytest.approx(trade_sums(s, "s2")[0]) and loss["net"] < 0
    assert open_ep["outcome"] == "open" and open_ep["held"] == 6 and open_ep["r"] is None
    assert open_ep["exit_price"] == pytest.approx(10300)
    position = s.positions()[0]
    assert open_ep["unrealized"] == pytest.approx(6 * 10300 - position["basis"])
    s.close()


def test_strategy_stats_match_performance_style_loop(tmp_path):
    s = seed(tmp_path / "experiment.sqlite3")
    with ledger(tmp_path / "experiment.sqlite3") as view:
        stats = {x["strategy"]: x for x in strategy_stats(view, episodes(view))}
    assert set(stats) == {"opening_range_breakout", "trend_pullback", "range_reversion"}
    tp, rr, orb = (
        stats["trend_pullback"],
        stats["range_reversion"],
        stats["opening_range_breakout"],
    )
    assert tp["round_trips"] == 1 and tp["wins"] == 1 and tp["win_rate"] == 1.0
    assert tp["open_episodes"] == 1 and tp["weight"] == 0.6 and tp["active"]
    assert tp["avg_r"] == pytest.approx(trade_sums(s, "s1")[1] / R_BUDGET)
    assert tp["max_drawdown"] == 0
    assert rr["round_trips"] == 1 and rr["losses"] == 1 and rr["win_rate"] == 0
    assert rr["max_drawdown"] == pytest.approx(-trade_sums(s, "s2")[1] / 5_000_000)
    assert orb["round_trips"] == 0 and orb["win_rate"] is None and orb["active"]
    s.close()


def test_timeline_newest_first_with_fill_rows(tmp_path):
    s = seed(tmp_path / "experiment.sqlite3")
    with ledger(tmp_path / "experiment.sqlite3") as view:
        rows = timeline(view, limit=3)
        everything = timeline(view, limit=5000)
    assert [r["id"] for r in rows] == ["s3", "b3", "s2"]
    assert len(everything) == 6
    assert [f["quantity"] for f in rows[0]["fills"]] == [4]
    assert rows[1]["fills"] == []
    s.close()


def test_audit_tail_folds_consecutive_repeats(tmp_path):
    from quantpilot.paper.dashboard import audit_tail

    s = seed(tmp_path / "experiment.sqlite3")
    for i in range(5):
        s.audit("order_reconciled", {"id": "x", "state": "accepted", "filled": 0},
                NOW + timedelta(minutes=10 + i))
    s.audit("control", {"action": "pause"}, NOW + timedelta(minutes=20))
    with ledger(tmp_path / "experiment.sqlite3") as view:
        rows = audit_tail(view, limit=2)
        everything = audit_tail(view, limit=100)
    assert [r["kind"] for r in rows] == ["control", "order_reconciled"]
    assert rows[1]["count"] == 5 and rows[1]["first_at"] < rows[1]["at"]
    assert rows[0]["count"] == 1
    assert all("_raw" not in r for r in everything)
    # the seeded reconciliations differ in state/filled, so they stay separate rows
    assert sum(r["count"] for r in everything) == s.db.execute(
        "SELECT COUNT(*) FROM audit"
    ).fetchone()[0]
    s.close()


def test_view_is_read_only_and_never_creates_a_ledger(tmp_path):
    s = seed(tmp_path / "experiment.sqlite3")
    before = s.db.execute("PRAGMA data_version").fetchone()[0]
    db = open_ledger(tmp_path / "experiment.sqlite3")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("INSERT INTO audit(at,kind,payload) VALUES('x','y','{}')")
    db.close()
    series = SeriesStore(tmp_path / "dashboard.sqlite3")
    payload = summary(tmp_path / "experiment.sqlite3", series.path)
    sample_once(tmp_path / "experiment.sqlite3", series, NOW)
    assert s.db.execute("PRAGMA data_version").fetchone()[0] == before
    assert payload["read_only"] and payload["report"]["live_trading_enabled"] is False
    assert "research" not in payload["report"] and "ai_failures" not in payload["report"]
    s.close()
    with pytest.raises(ValueError, match="ledger_missing"):
        open_ledger(tmp_path / "missing" / "experiment.sqlite3")
    with pytest.raises(ValueError, match="ledger_missing"):
        serve(tmp_path / "missing")
    assert not (tmp_path / "missing" / "experiment.sqlite3").exists()


def test_sample_once_appends_on_change_or_after_a_minute(tmp_path):
    s = seed(tmp_path / "experiment.sqlite3")
    series = SeriesStore(tmp_path / "dashboard.sqlite3")
    path = tmp_path / "experiment.sqlite3"
    assert sample_once(path, series, NOW)
    assert not sample_once(path, series, NOW + timedelta(seconds=10))
    assert sample_once(path, series, NOW + timedelta(seconds=61))
    s.put("marks", {"035420": 10500.0})
    assert sample_once(path, series, NOW + timedelta(seconds=70))
    rows = series.recent()
    assert len(rows) == 3 and rows[-1]["equity"] > rows[0]["equity"]
    # snapshot() rounds money to two decimals.
    assert rows[-1]["realized"] == pytest.approx(s.get("realized"), abs=0.01)
    s.close()


def test_app_serves_page_and_summary_without_sampler(tmp_path):
    from fastapi.testclient import TestClient

    s = seed(tmp_path / "experiment.sqlite3")
    with TestClient(create_app(tmp_path, sample_seconds=0)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "<svg" in page.text and "/api/summary" in page.text
        assert page.headers["cache-control"] == "no-store"
        res = client.get("/api/summary?timeline_limit=2")
        body = res.json()
        assert res.status_code == 200
        assert set(body) >= {
            "report",
            "strategies",
            "episodes",
            "timeline",
            "audit",
            "series",
            "sampler",
            "active_strategies",
            "open_orders",
        }
        assert len(body["timeline"]) == 2 and body["series"] == []
        assert body["open_orders"] == [] and "<details" in page.text
        assert body["sampler"]["running"] is False
        assert body["report"]["positions"][0]["unrealized"] is not None
        assert client.get("/api/health").json()["ledger_present"] is True
        assert client.get("/docs").status_code == 404
    s.close()
    assert not (tmp_path / "dashboard.sqlite3").exists()


def test_summary_reports_missing_ledger_as_unavailable(tmp_path):
    from fastapi.testclient import TestClient

    with TestClient(create_app(tmp_path / "empty", sample_seconds=0)) as client:
        res = client.get("/api/summary")
    assert res.status_code == 503 and res.json()["reason"] == "ledger_missing"
    assert not (tmp_path / "empty" / "experiment.sqlite3").exists()


def test_serve_binds_loopback_and_holds_its_own_lock(tmp_path, monkeypatch):
    import uvicorn

    from quantpilot.paper.cli import process_lock

    s = seed(tmp_path / "experiment.sqlite3")
    s.close()
    seen = {}

    def fake_run(app, **kwargs):
        seen.update(kwargs)
        with pytest.raises(OSError):
            with process_lock(tmp_path / "dashboard.lock"):
                pass
        for name in ("trader", "worker", "reporter"):
            with process_lock(tmp_path / f"{name}.lock"):
                pass

    monkeypatch.setattr(uvicorn, "run", fake_run)
    serve(tmp_path, port=8123, sample_seconds=0)
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8123
    assert dashboard.HOST == "127.0.0.1" and dashboard.DEFAULT_PORT == 8770


def test_cli_dashboard_branch_runs_before_store(monkeypatch, capsys):
    import shutil
    import tempfile

    from quantpilot.paper import cli

    # The CLI refuses runtime directories inside a repository, so use the system temp.
    outside = Path(tempfile.mkdtemp(prefix="qp-dashboard-"))
    try:
        calls = []
        monkeypatch.setattr(
            dashboard, "serve", lambda directory, port, seconds: calls.append((port, seconds))
        )
        assert cli.main(["--runtime-dir", str(outside), "dashboard", "--port", "9000"]) == 0
        assert calls == [(9000, 10)]
        assert not (outside / "experiment.sqlite3").exists()

        def refuse(*a):
            raise ValueError("ledger_missing")

        monkeypatch.setattr(dashboard, "serve", refuse)
        assert cli.main(["--runtime-dir", str(outside), "dashboard"]) == 2
        assert "ledger_missing" in capsys.readouterr().out
    finally:
        shutil.rmtree(outside, ignore_errors=True)
