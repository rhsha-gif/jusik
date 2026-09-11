"""Anonymized broker-format regression and query-only recovery acceptance."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quantpilot.packages.db.sqlite_repositories import PaperStateStore
from quantpilot.tests.unit.test_paper_reconciliation import (
    NOW, FINGERPRINT, FakeClient, _accepted_from_post, _reconciler, _row, _balance, _unknown,
)
from quantpilot.paper.store import Store
from quantpilot.paper.calendar import Session
from quantpilot.paper.recovery import preview, apply_recovery, RecoveryTransport
from quantpilot.packages.core.kis_paper import KisBalancePosition, KIS_PAPER_BASE_URL, KisPaperConfigurationError


class FilledClient(FakeClient):
    def get_balance(self, **kwargs):
        return replace(_balance(), positions=(KisBalancePosition(
            "005930", "fixture", 2, 2, Decimal("70000"), Decimal("71000"),
            Decimal("140000"), Decimal("142000")),))

    def place_limit_cash_order(self, **kwargs):
        pytest.fail("recovery attempted an order")

    def cancel_paper_remaining_order(self, **kwargs):
        pytest.fail("recovery attempted cancellation")


def seed(tmp_path):
    with PaperStateStore(tmp_path / "broker.sqlite3", data_mode="paper_trading",
                         account_scope_fingerprint=FINGERPRINT) as kernel:
        dispatch = _accepted_from_post(kernel)
    store = Store(tmp_path / "experiment.sqlite3")
    store.configure({"data_mode": "paper_trading"}, 1)
    store.put("account_binding", FINGERPRINT)
    store.control("start")
    store.reserve(order_id=dispatch.order_plan_id, signal=SimpleNamespace(
        symbol=dispatch.symbol, strategy_id=dispatch.strategy_id, version=dispatch.strategy_version,
        stop=69000, target=74000), quantity=2, price=70000, side="buy", now=NOW,
        policy_version=store.policy.version, reason="fixture")
    store.update_order(dispatch.order_plan_id, "accepted", 0, 0, NOW)
    store.control("pause")
    store.close()
    client = FilledClient(replace(_row(filled=2, remaining=0, amount="140000"),
                                  original_order_number="0000000000"))
    calendar = SimpleNamespace(session=lambda at: Session(NOW-timedelta(hours=1), NOW+timedelta(hours=5)))
    return client, calendar


def test_zero_padded_original_sentinel_reconciles_full_fill_once(tmp_path):
    with PaperStateStore(tmp_path / "broker.sqlite3", data_mode="paper_trading",
                         account_scope_fingerprint=FINGERPRINT) as store:
        accepted = _accepted_from_post(store)
        row = replace(_row(filled=2, remaining=0, amount="140000"),
                      original_order_number="0000000000")
        reconciler = _reconciler(store, FakeClient(row))
        filled = reconciler.reconcile_dispatch(accepted, (row,))
        assert filled.status == "filled"
        assert filled.cumulative_filled_quantity == 2
        assert len(filled.fill_evidence) == 1
        assert filled.reconciliation_status == "reconciled"
        assert reconciler.reconcile_dispatch(filled, (row,)) == filled


@pytest.mark.parametrize("original", ["0000000123", "０", "0.0", "-0", "0" * 17])
def test_non_original_or_invalid_sentinel_cannot_match(tmp_path, original):
    with PaperStateStore(tmp_path / "broker.sqlite3", data_mode="paper_trading",
                         account_scope_fingerprint=FINGERPRINT) as store:
        accepted = _accepted_from_post(store)
        row = replace(_row(filled=2, remaining=0, amount="140000"),
                      original_order_number=original)
        assert _reconciler(store, FakeClient(row)).reconcile_dispatch(accepted, (row,)) == accepted


def test_preview_is_read_only_and_needs_no_order_authority(tmp_path):
    client, calendar = seed(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.sqlite3")}
    result = preview(tmp_path, client, calendar, NOW + timedelta(seconds=30))
    assert result["status"] == "reconciled" and result["changed_orders"] == 1
    assert result["position_quantity"] == 2 and not result["order_authority"]
    assert not result["applied"]
    assert {p.name: p.read_bytes() for p in tmp_path.glob("*.sqlite3")} == before


def test_apply_preserves_pause_costs_and_idempotency_and_quarantines_after_close(tmp_path):
    client, calendar = seed(tmp_path)
    now = NOW + timedelta(hours=6)
    result = apply_recovery(tmp_path, client, calendar, now)
    assert result["status"] == "reconciled" and result["applied"]
    store = Store(tmp_path / "experiment.sqlite3")
    cash = store.get("cash")
    assert store.get("control") == "paused"
    assert store.positions()[0]["quantity"] == 2
    assert store.positions()[0]["quarantined"] == 1
    assert cash == pytest.approx(5_000_000-140000*(1+store.policy.fee_bps/10000))
    assert store.get("last_close_equity") == pytest.approx(cash + 142000, abs=0.01)
    store.close()
    assert apply_recovery(tmp_path, client, calendar, now + timedelta(seconds=1))["changed_orders"] == 0
    store = Store(tmp_path / "experiment.sqlite3")
    assert store.get("cash") == cash and store.positions()[0]["quantity"] == 2
    store.close()


def test_interruption_between_kernel_and_experiment_can_replay_once(tmp_path, monkeypatch):
    client, calendar = seed(tmp_path)
    original = Store.update_order
    # Dry preview succeeds; only the actual persisted experiment write fails.
    def crash(self, *args, **kwargs):
        dbpath = self.db.execute("PRAGMA database_list").fetchone()[2]
        if dbpath:
            raise RuntimeError("injected_after_kernel_commit")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Store, "update_order", crash)
    with pytest.raises(RuntimeError, match="injected_after_kernel_commit"):
        apply_recovery(tmp_path, client, calendar, NOW + timedelta(seconds=30))
    monkeypatch.setattr(Store, "update_order", original)
    result = apply_recovery(tmp_path, client, calendar, NOW + timedelta(seconds=31))
    assert result["status"] == "reconciled" and result["position_quantity"] == 2
    assert apply_recovery(tmp_path, client, calendar, NOW + timedelta(seconds=32))["changed_orders"] == 0


def test_active_owner_and_wrong_account_block_apply(tmp_path):
    from quantpilot.paper.cli import process_lock
    client, calendar = seed(tmp_path)
    with process_lock(tmp_path / "trader.lock"):
        with pytest.raises(OSError):
            apply_recovery(tmp_path, client, calendar, NOW)
    client.account_scope_fingerprint = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="recovery_account_mismatch"):
        preview(tmp_path, client, calendar, NOW)
    assert not (tmp_path / "recovery-backups").exists()


@pytest.mark.parametrize("method,path", [("POST", "/uapi/domestic-stock/v1/trading/order-cash"),
    ("POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl"),
    ("GET", "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl")])
def test_recovery_transport_rejects_orders_cancels_and_unsupported_reads(method, path):
    with pytest.raises(KisPaperConfigurationError, match="recovery_endpoint_blocked"):
        RecoveryTransport().request_json(method, KIS_PAPER_BASE_URL+path)


def test_paper_report_masks_unreconciled_zero_holdings_and_stale_data(tmp_path):
    from quantpilot.paper.reporting import snapshot, render
    client, calendar = seed(tmp_path)
    store = Store(tmp_path / "experiment.sqlite3")
    report = snapshot(store, now=NOW)
    assert report["valuation_incomplete"] and not report["reconciliation_complete"]
    assert "확인 불가" in render(report) and "+0.00%" not in render(report)
    store.put("reconciliation_complete", True)
    store.put("last_reconciled_at", (NOW-timedelta(seconds=181)).isoformat())
    assert snapshot(store, now=NOW)["valuation_incomplete"]
    store.close()


def test_recovery_report_supersedes_only_pending_and_is_idempotent(tmp_path):
    from quantpilot.paper.reporting import enqueue_recovery_report, drain_outbox
    store = Store(tmp_path / "experiment.sqlite3")
    store.enqueue("pending", "old report", NOW)
    store.enqueue("uncertain", "old uncertain", NOW)
    store.db.execute("UPDATE outbox SET state='delivery_unknown' WHERE id='uncertain'")
    store.put("valuation_invalidated_before", NOW.isoformat())
    key = enqueue_recovery_report(store, NOW)
    assert enqueue_recovery_report(store, NOW+timedelta(seconds=5)) == key
    rows = {r[0]: r[1] for r in store.db.execute("SELECT id,state FROM outbox")}
    assert rows == {"pending": "superseded", "uncertain": "delivery_unknown", key: "pending"}
    calls = []
    drain_outbox(store, SimpleNamespace(send=lambda *args: calls.append(args)))
    drain_outbox(store, SimpleNamespace(send=lambda *args: calls.append(args)))
    assert len(calls) == 1 and calls[0][0] == key
    store.close()


def test_missing_ai_input_does_not_consume_hourly_job(tmp_path):
    from quantpilot.paper.jobs import work_once
    store = Store(tmp_path / "experiment.sqlite3")
    store.configure({"ai_enabled": True}, 1)
    store.put("ai_due", {"kind": "hourly", "key": "fixture:hourly:1"})
    assert work_once(store, NOW)["status"] == "waiting_for_evidence"
    assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    store.close()


def test_bad_date_row_does_not_hide_a_valid_unknown_dispatch_fill(tmp_path):
    with PaperStateStore(tmp_path / "broker.sqlite3", data_mode="paper_trading",
                         account_scope_fingerprint=FINGERPRINT) as store:
        unknown = _unknown(store)
        good = _row(filled=2, remaining=0, amount="140000")
        bad = replace(good, order_date="00000000")
        filled = _reconciler(store, FakeClient(bad, good)).reconcile_dispatch(unknown, (bad, good))
        assert filled.status == "filled" and filled.cumulative_filled_quantity == 2


@pytest.mark.parametrize("next_day", [False, True])
def test_recovery_quarantines_preopen_and_carried_intraday_positions(tmp_path, next_day):
    from quantpilot.packages.core.kis_paper import KisDailyOrdersResult
    client, calendar = seed(tmp_path)
    now = NOW + (timedelta(days=3) if next_day else timedelta(seconds=30))
    calendar = SimpleNamespace(session=lambda at: Session(
        now - timedelta(hours=1) if next_day else now + timedelta(hours=1), now + timedelta(hours=5)))
    client.get_daily_orders_and_fills = lambda *a, **kw: KisDailyOrdersResult(client.rows, 1)
    assert apply_recovery(tmp_path, client, calendar, now)["status"] == "reconciled"
    store = Store(tmp_path / "experiment.sqlite3")
    assert store.positions()[0]["quarantined"] == 1 and store.get("control") == "paused"
    store.close()


def test_apply_itself_supersedes_pending_report_and_preserves_unknown_delivery(tmp_path):
    client, calendar = seed(tmp_path)
    store = Store(tmp_path / "experiment.sqlite3")
    store.enqueue("old_report", "old data", NOW)
    store.enqueue("unknown", "uncertain", NOW)
    store.db.execute("UPDATE outbox SET state='delivery_unknown' WHERE id='unknown'")
    store.close()
    assert apply_recovery(tmp_path, client, calendar, NOW + timedelta(seconds=30))["applied"]
    store = Store(tmp_path / "experiment.sqlite3")
    states = {r[0]: r[1] for r in store.db.execute("SELECT id,state FROM outbox")}
    assert states["old_report"] == "superseded" and states["unknown"] == "delivery_unknown"
    assert len([k for k,v in states.items() if k.startswith("recovery:") and v == "pending"]) == 1
    store.close()


def test_stale_previous_close_masks_daily_pnl_until_a_valid_session_close(tmp_path):
    from quantpilot.paper.valuation import roll_baselines, record_close
    from quantpilot.paper.reporting import snapshot, render
    from quantpilot.paper.calendar import KST
    client, _ = seed(tmp_path)
    store = Store(tmp_path / "experiment.sqlite3")
    store.put("day", NOW.astimezone(KST).date().isoformat())
    store.put("last_close_day", "2026-07-09")
    store.put("last_close_equity_valid", True)
    store.put("last_close_equity", 4_500_000)
    monday = NOW + timedelta(days=3)
    calendar = SimpleNamespace(previous_session_date=lambda at: "2026-07-10")
    roll_baselines(store, calendar, monday)
    store.put("reconciliation_complete", True)
    store.put("last_reconciled_at", monday.isoformat())
    report = snapshot(store, now=monday)
    assert not report["valuation_incomplete"] and report["daily_pnl_incomplete"]
    assert "당일 손익: 확인 불가" in render(report)
    record_close(store, monday, valid=True, equity=5_000_000)
    tuesday = monday+timedelta(days=1)
    roll_baselines(store, SimpleNamespace(previous_session_date=lambda at: "2026-07-13"), tuesday)
    store.put("last_reconciled_at", tuesday.isoformat())
    assert not snapshot(store, now=tuesday)["daily_pnl_incomplete"]
    assert store.get("day_base") == 5_000_000
    store.close()


def test_dashboard_excludes_invalidated_samples_without_deleting_them(tmp_path):
    from quantpilot.paper.dashboard import SeriesStore, summary, sample_once
    seed(tmp_path)
    series = SeriesStore(tmp_path / "dashboard.sqlite3")
    for offset in (-1, 1):
        series.append({"at": (NOW+timedelta(seconds=offset)).isoformat(), "equity": 5_000_000,
                       "cash": 5_000_000, "realized": 0, "daily_pnl": 0})
    store = Store(tmp_path / "experiment.sqlite3")
    store.put("valuation_invalidated_before", NOW.isoformat())
    store.close()
    assert not sample_once(tmp_path / "experiment.sqlite3", series, NOW)
    data = summary(tmp_path / "experiment.sqlite3", tmp_path / "dashboard.sqlite3")
    assert len(data["series"]) == 1 and len(series.recent()) == 2


def test_recovery_factory_rejects_unsafe_flags_before_credentials(monkeypatch):
    from quantpilot.paper.recovery import build_client
    monkeypatch.setattr("quantpilot.jobs.check_kis_paper_connection.connection_config",
                        lambda env: pytest.fail("credentials were accessed"))
    with pytest.raises(ValueError, match="unsafe_environment"):
        build_client({"LIVE_TRADING_ENABLED": "true"}, lambda: NOW)


def test_restart_after_projection_commit_still_finishes_correction(tmp_path, monkeypatch):
    from quantpilot.paper.broker import KisGateway
    client, calendar = seed(tmp_path)
    store = Store(tmp_path / "experiment.sqlite3")
    store.enqueue("old_report", "unreconciled report", NOW)
    store.close()
    original = KisGateway.reconcile
    def crash_after_projection(self, now):
        result = original(self, now)
        if self.store.db.execute("PRAGMA database_list").fetchone()[2]:
            raise RuntimeError("injected_after_projection_commit")
        return result
    monkeypatch.setattr(KisGateway, "reconcile", crash_after_projection)
    with pytest.raises(RuntimeError, match="injected_after_projection_commit"):
        apply_recovery(tmp_path, client, calendar, NOW + timedelta(seconds=30))
    from quantpilot.paper.cli import build_runtime
    store = Store(tmp_path / "experiment.sqlite3")
    assert store.get("recovery_pending_since") is not None
    with pytest.raises(ValueError, match="recovery_incomplete"):
        build_runtime(store, {})
    store.close()
    monkeypatch.setattr(KisGateway, "reconcile", original)
    result = apply_recovery(tmp_path, client, calendar, NOW + timedelta(seconds=31))
    assert result["changed_orders"] == 0
    store = Store(tmp_path / "experiment.sqlite3")
    assert store.db.execute("SELECT state FROM outbox WHERE id='old_report'").fetchone()[0] == "superseded"
    assert store.db.execute("SELECT COUNT(*) FROM outbox WHERE id LIKE 'recovery:%'").fetchone()[0] == 1
    assert store.get("recovery_pending_since") is None
    store.close()
