"""Offline evidence across the real client, durable kernel and operational adapters."""
from datetime import timedelta, datetime, timezone
from types import SimpleNamespace
import json
import sqlite3

import pytest

from quantpilot.paper.store import Store
from quantpilot.paper.calendar import Session, Calendar
from quantpilot.tests.unit.test_intraday_durable_gateway import Client, NOW
from quantpilot.packages.core.kis_paper import KisPaperClient, KisPaperConfig, KisHttpResponse, KisPaperTransportError
from quantpilot.packages.core.marketdata.types import Quote


@pytest.mark.parametrize("gate", ["expiry", "balance_stale", "quote_stale", "pause", "baseline", "submission_disabled", "kill", "admission", "unknown"])
def test_queued_real_post_rechecks_authority_and_unknown_never_replays(tmp_path, gate):
    from quantpilot.paper.api_budget import SharedBudget, BudgetTransport
    from quantpilot.paper.auth import RefreshingClient
    from quantpilot.paper.broker import KisGateway
    from quantpilot.packages.core.execution.paper_submission import PaperSubmissionRejected, PaperSubmissionOutcomeUnknown
    from quantpilot.tests.test_paper_api_budget import FakeClock
    clock = FakeClock(NOW.timestamp())
    at = lambda: datetime.fromtimestamp(clock(), timezone.utc)
    store = Store(tmp_path / "experiment.sqlite3")
    store.configure({"data_mode": "paper_trading", "shared_api_budget_enabled": True}, 1)
    store.control("start", now=NOW)
    store.put("day_base_valid", True)
    store.put("weights", {"trend_pullback": 0.6})
    env = {"KIS_PAPER_ORDER_SUBMISSION_ENABLED": "true"}
    changed = [False]
    def sleep(seconds):
        clock.sleep(seconds)
        if not changed[0]:
            changed[0] = True
            if gate == "pause":
                store.control("pause", now=at())
            elif gate == "baseline":
                store.put("day_base_valid", False)
            elif gate == "submission_disabled":
                env["KIS_PAPER_ORDER_SUBMISSION_ENABLED"] = "false"
            elif gate == "kill":
                gateway.kernel.start_paper_kill_operation(session=gateway.session, reason="operator_requested", started_at=at())
    budget = SharedBudget(tmp_path / "budget.sqlite3", "sha256:" + "b"*64, clock=clock, sleep=sleep)
    fixture, posts = Client(), []
    class Raw:
        def request_json(self, method, url, **kwargs):
            if url.endswith("/oauth2/tokenP"):
                return KisHttpResponse(200, {"access_token": "fixture", "token_type": "Bearer", "expires_in": 86400})
            assert method == "POST" and url.endswith("/order-cash")
            posts.append(url)
            fixture.order_calls += 1
            raise KisPaperTransportError("fixture timeout")
    def factory(config):
        client = KisPaperClient(config, transport=BudgetTransport(Raw(), budget))
        client.get_balance = fixture.get_balance
        client.get_buying_power = fixture.get_buying_power
        client.get_daily_orders_and_fills = fixture.get_daily_orders_and_fills
        return client
    proxy = RefreshingClient(KisPaperConfig(app_key="fixture", app_secret="fixture", account_number="12345678"), factory, at)
    calendar = SimpleNamespace(session=lambda _: Session(NOW-timedelta(hours=1), NOW+timedelta(hours=5)), current_open_session_date=lambda _: NOW.date())
    gateway = KisGateway(store, proxy, calendar, at)
    gateway.budget, gateway.environment = budget, env
    try:
        gateway.begin()
        assert gateway.reconcile(at())
        if gate == "balance_stale":
            # The balance snapshot is 10 s old when the order is prepared; the quote and
            # the risk check are fresh, so only the reconciled-balance evidence expires
            # (at +15 s) while the plan waits in the queue for 6 s more.
            clock.sleep(10)
        signal = SimpleNamespace(symbol="005930", strategy_id="trend_pullback", version="1", stop=69000., target=72000., entry_atr14=1000.)
        store.reserve(order_id="queued", signal=signal, quantity=1, price=70000, side="buy", now=at(), policy_version=store.policy.version, reason="fixture")
        budget.share_cooldown({"expiry": 20, "balance_stale": 6, "quote_stale": 6}.get(gate, 1))
        quote_at = at() - timedelta(seconds=10) if gate == "quote_stale" else at()
        if gate == "admission":
            from contextlib import contextmanager
            @contextmanager
            def unavailable(priority):
                raise TimeoutError("fixture queue timeout")
                yield
            budget.send_admission = unavailable
        with pytest.raises(PaperSubmissionOutcomeUnknown if gate == "unknown" else PaperSubmissionRejected):
            gateway.submit(store.orders()[0], Quote(symbol="005930", last=70000, bid=69900, ask=70000, as_of=quote_at), at())
        dispatch = gateway.kernel.load_paper_order_dispatch("queued")
        if gate == "unknown":
            assert len(posts) == 1 and dispatch.status == "outcome_unknown"
            gateway.reconcile(at())
            gateway.reconcile(at())
            assert len(posts) == 1 and gateway.kernel.load_paper_order_dispatch("queued").attempt_count == 1
        else:
            assert posts == []
            assert dispatch.status == "rejected"
            if gate != "admission":
                with sqlite3.connect(budget.path) as db:
                    assert json.loads(db.execute("SELECT body FROM api_requests ORDER BY at DESC LIMIT 1").fetchone()[0])["stage"] == "before_send"
            if gate in {"balance_stale", "quote_stale"}:
                rejected = [json.loads(r[0]) for r in store.db.execute(
                    "SELECT payload FROM audit WHERE kind='queued_order_rejected'")]
                assert [r["reason_code"] for r in rejected] == ["submission_evidence_expired"]
    finally:
        gateway.end()
        gateway.close()
        store.close()


def test_close_report_uses_frozen_proof_and_explicit_correction_invalidates_it(tmp_path):
    from quantpilot.paper.valuation import record_close, invalidate_close
    from quantpilot.paper.reporting import operation_report_once
    s = Store(tmp_path / "s")
    s.configure({"independent_reports_enabled": True}, 1)
    s.put("day_base_valid", True)
    close_at = NOW.replace(hour=6, minute=30)
    record_close(s, close_at, valid=True, equity=5_000_000)
    s.put("cash", 4_999_000)
    s.put("day_base", 4_000_000)
    calendar = SimpleNamespace(session=lambda _: Session(NOW-timedelta(hours=1), close_at))
    operation_report_once(s, calendar, close_at+timedelta(seconds=5))
    report = s.get("last_report")
    assert report["cash"] == report["equity"] == 5_000_000
    assert report["daily_pnl"] == 0 and not report["daily_pnl_incomplete"]
    invalidate_close(s, "2026-09-10", close_at+timedelta(minutes=1), "ledger_recovery")
    assert not s.get("close:2026-09-10")["valid"]
    assert s.db.execute("SELECT COUNT(*) FROM close_observations WHERE valid=1").fetchone()[0] == 1
    s.close()


def test_additive_migration_backs_up_history_and_profile_apply_never_arms(tmp_path):
    from quantpilot.paper.stabilization import preview, apply, FEATURES
    s = Store(tmp_path / "experiment.sqlite3")
    s.configure({"data_mode": "paper_trading", "max_positions": 1, "symbol_cap": .1, "trade_risk": .001}, 1)
    s.control("pause", now=NOW)
    s.audit("fixture_history", {"sequence": 1}, NOW)
    version = s.policy.version
    s.db.execute("DROP TABLE bar_finalizations")  # A disposable pre-migration fixture.
    s.close()
    before = (tmp_path / "experiment.sqlite3").read_bytes()
    assert preview(tmp_path)["status"] == "ready"
    assert (tmp_path / "experiment.sqlite3").read_bytes() == before
    result = apply(tmp_path, {}, version)
    assert result["orders_armed"] is False and result["control"] == "paused"
    s = Store(tmp_path / "experiment.sqlite3")
    assert all(getattr(s.policy, field) for field in FEATURES)
    assert s.policy.max_positions == 1 and s.policy.initial_capital == 5_000_000
    assert s.db.execute("SELECT COUNT(*) FROM audit WHERE kind='fixture_history'").fetchone()[0] == 1
    s.close()
    backups = list((tmp_path/"backups").rglob("*.sqlite3"))
    assert len(backups) >= 2
    for path in backups:
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("SELECT COUNT(*) FROM audit WHERE kind='fixture_history'").fetchone()[0] == 1


def test_four_session_acceptance_requires_real_evidence_and_counts_incident_actions(tmp_path):
    from quantpilot.paper.stabilization import acceptance
    from quantpilot.paper.diagnostics import open_incident
    s = Store(tmp_path / "s")
    first = datetime(2026,9,14,tzinfo=timezone.utc)
    report = acceptance(s, Calendar(), "2026-09-14", first+timedelta(days=3,hours=7))
    assert report["status"] == "pending" and len(report["days"]) == 4
    assert "paper_execution_ingress_unverified" in report["reasons"]
    assert all("operation_report_missing" in d["reasons"] for d in report["days"])
    s.control("resume", now=first)
    assert not list(s.db.execute("SELECT id FROM audit WHERE kind='operator_incident_intervention'"))
    open_incident(s, "reconciliation", "reconciliation_required", first)
    s.control("pause", now=first)
    s.control("resume", now=first+timedelta(seconds=1))
    report = acceptance(s, Calendar(), "2026-09-14", first+timedelta(days=3,hours=7))
    assert "incident_intervention_limit_exceeded" in report["reasons"]
    s.close()


def test_actual_gateway_retains_only_fresh_attributed_protection_after_daily_query_failure(tmp_path):
    from quantpilot.paper.broker import KisGateway
    s = Store(tmp_path / "s")
    s.control("start", now=NOW)
    signal = SimpleNamespace(symbol="005930", strategy_id="trend_pullback", version="1", stop=69000., target=72000.)
    s.reserve(order_id="held", signal=signal, quantity=1, price=70000, side="buy", now=NOW, policy_version=1, reason="fixture")
    s.update_order("held", "filled", 1, 70000, NOW)
    client = Client()
    client.filled = True
    def fail(*args, **kwargs):
        raise TimeoutError("fixture")
    client.get_daily_orders_and_fills = fail
    current = [NOW]
    calendar = SimpleNamespace(session=lambda _: Session(NOW-timedelta(hours=1), NOW+timedelta(hours=5)), current_open_session_date=lambda _: NOW.date())
    gateway = KisGateway(s, client, calendar, lambda: current[0])
    try:
        with pytest.raises(TimeoutError):
            gateway.reconcile(NOW)
        assert not gateway.entry_reconciled
        assert gateway.protection_allowed("005930")
        assert not gateway.protection_allowed("000660")
        current[0] += timedelta(seconds=15)
        assert not gateway.protection_allowed("005930")
    finally:
        gateway.close()
        s.close()


def test_successful_ai_fallback_preserves_the_failed_provider_code():
    from quantpilot.paper.intelligence import run_review
    def runner(provider, *args):
        if provider == "claude":
            raise FileNotFoundError()
        return {"model": "fixture", "summary": "규칙을 유지한다", "observations": [], "risks": []}
    review = run_review({"symbols": [], "strategies": []}, NOW, runner=runner)
    assert review.provider == "codex"
    assert review.model_dump(mode="json")["provider_failures"] == {"claude": "runner_unavailable"}


def test_four_day_evidence_can_pass_but_a_manual_baseline_change_cannot(tmp_path):
    from quantpilot.paper.stabilization import acceptance, FEATURES
    from quantpilot.paper.valuation import roll_baselines, record_close
    from quantpilot.paper.reporting import operation_report_once
    from quantpilot.paper.strategy import Bar
    s = Store(tmp_path / "s")
    s.configure(dict(FEATURES, data_mode="paper_trading"), 1)
    s.put("stabilization_run_start", "2026-09-13T23:00:00+00:00")
    calendar = Calendar()
    first = datetime(2026,9,14,tzinfo=timezone.utc)
    for offset in range(4):
        stamp = first+timedelta(days=offset)
        day = stamp.date().isoformat()
        session = calendar.session(stamp)
        roll_baselines(s, calendar, session.opens)
        s.put("session_coverage:"+day, {"started_before_open": True})
        s.put("market_ingress:"+day, {"at": session.opens.isoformat()})
        s.put("api_budget_status", {"state": "enabled", "violations": 0})
        s.observe_bars([Bar("005930",session.opens,100,101,99,100,10)],session.opens+timedelta(seconds=75))
        record_close(s, session.closes, valid=True, equity=s.get("cash"))
        operation_report_once(s, calendar, session.closes+timedelta(seconds=1))
        s.db.execute("INSERT INTO jobs VALUES(?, 'postclose','failed',?,NULL,'ai_disabled')",
                     (day+":postclose", session.closes.isoformat()))
    s.put("paper_ingress_verified:2026-09-14", {"execution_notice": True})
    now = first+timedelta(days=3,hours=7)
    result = acceptance(s, calendar, "2026-09-14", now)
    assert result["status"] == "passed", result
    s.put("stabilization_run_start", "2026-09-14T00:00:01+00:00")
    assert "full_session_activation_missing" in acceptance(s, calendar, "2026-09-14", now)["reasons"]
    s.put("stabilization_run_start", "2026-09-13T23:00:00+00:00")
    proof = s.get("close:2026-09-15")
    s.put("close:2026-09-15", dict(proof, day_base=4_900_000))
    result = acceptance(s, calendar, "2026-09-14", now)
    assert result["status"] == "pending"
    assert "daily_baseline_proof_missing_or_changed" in result["days"][1]["reasons"]
    s.close()


def test_normal_daily_resume_is_not_an_incident_intervention(tmp_path):
    from quantpilot.paper.diagnostics import open_incident
    s = Store(tmp_path / "s")
    s.control("resume", now=NOW-timedelta(days=1))
    open_incident(s, "feed", "feed_unavailable", NOW-timedelta(days=1))
    s.control("resume", now=NOW)
    assert not list(s.db.execute("SELECT id FROM audit WHERE kind='operator_incident_intervention'"))
    assert s.get("incidents")["feed"]  # Resuming never clears a fault.
    assert s.get("resume_authorized_day") == "2026-09-10"
    s.close()
