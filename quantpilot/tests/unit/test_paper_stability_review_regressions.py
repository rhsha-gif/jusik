"""Reproductions for the independent safety review; all clients are fake."""
from datetime import timedelta, datetime, timezone
from types import SimpleNamespace
import json

import pytest

from quantpilot.tests.unit.test_paper_daily_cancel import trial
from quantpilot.tests.unit.test_intraday_durable_gateway import NOW
from quantpilot.tests.test_paper_api_budget import FakeClock, SCOPE
from quantpilot.paper.api_budget import SharedBudget, BudgetTransport
from quantpilot.packages.core.kis_paper import KisPaperClient, KisPaperConfig, KisHttpResponse, KisPaperTransportError, KisPaperCancelOutcomeUnknown
from quantpilot.paper.store import Store


@pytest.mark.parametrize("after_wire", [False, True])
def test_cancel_refusal_before_wire_releases_claim_but_unknown_keeps_it(trial, tmp_path, after_wire):
    s, client, gateway, _ = trial
    clock = FakeClock(NOW.timestamp())
    at = lambda: datetime.fromtimestamp(clock(), timezone.utc)
    gateway.clock = at
    budget = SharedBudget(tmp_path/"budget.sqlite3", SCOPE, clock=clock, sleep=clock.sleep)
    gateway.budget = budget
    posts = []
    class Raw:
        def request_json(self, method, url, **kwargs):
            assert method == "POST" and url.endswith("/order-rvsecncl")
            posts.append(url)
            if after_wire:
                raise KisPaperTransportError("fixture timeout")
            return KisHttpResponse(200, {"rt_cd":"0","msg_cd":"40630000","output":{"KRX_FWDG_ORD_ORGNO":"00950","ODNO":"0000099999","ORD_TMD":"100020"}})
    real = KisPaperClient(KisPaperConfig(app_key="fixture",app_secret="fixture",account_number="12345678",access_token="fixture"),
                          transport=BudgetTransport(Raw(),budget))
    client.cancel_paper_remaining_order = real.cancel_paper_remaining_order
    if after_wire:
        with pytest.raises(KisPaperCancelOutcomeUnknown):
            gateway.cancel(s.orders()[0],at())
        gateway.cancel(s.orders()[0],at())
        assert len(posts) == 1 and s.get("cancel_claim:trial") is True
        assert s.orders()[0]["state"] == "cancel_unknown"
    else:
        budget.share_cooldown(16)
        gateway.cancel(s.orders()[0],at())
        assert not posts and not s.get("cancel_claim:trial")
        assert s.orders()[0]["state"] == "accepted"
        gateway.cancel(s.orders()[0],at())
        gateway.cancel(s.orders()[0],at())
        assert len(posts) == 1 and s.get("cancel_claim:trial") is True
        assert s.db.execute("SELECT COUNT(*) FROM audit WHERE kind='cancel_not_sent'").fetchone()[0] == 1


def test_invalidated_close_revokes_dependent_baselines_until_new_proof(tmp_path):
    from quantpilot.paper.valuation import record_close, roll_baselines, invalidate_close
    from quantpilot.paper.calendar import Calendar
    s = Store(tmp_path/"s")
    friday = datetime(2026,9,11,6,30,tzinfo=timezone.utc)
    monday = friday+timedelta(days=3)
    record_close(s,friday,valid=True,equity=4_950_000)
    roll_baselines(s,Calendar(),monday)
    assert s.get("day_base_valid") and s.get("month_base_valid")
    invalidate_close(s,"2026-09-11",monday,"ledger_recovery")
    roll_baselines(s,Calendar(),monday)
    assert s.get("day_base_valid") is False and s.get("month_base_valid") is False
    assert s.get("day_base_reason") == "previous_session_close_invalidated"
    record_close(s,friday+timedelta(minutes=1),valid=True,equity=4_900_000)
    roll_baselines(s,Calendar(),monday)
    assert s.get("day_base") == s.get("month_base") == 4_900_000
    assert s.get("day_base_valid") is True and s.get("month_base_valid") is True
    s.close()


def test_closed_position_and_repaired_environment_do_not_leave_orphan_incidents(tmp_path):
    from quantpilot.paper.runtime import Runtime
    from quantpilot.paper.broker import FixtureGateway
    from quantpilot.paper.calendar import Session
    from quantpilot.paper.diagnostics import open_incident
    s = Store(tmp_path/"s")
    s.control("pause",now=NOW)
    open_incident(s,"protection:005930","position_protection_unavailable",NOW)
    open_incident(s,"runtime","unsafe_environment",NOW)
    calendar = SimpleNamespace(session=lambda _:Session(NOW-timedelta(hours=1),NOW+timedelta(hours=5)))
    runtime = Runtime(s,SimpleNamespace(),FixtureGateway(s),calendar,lambda:NOW)
    runtime.cycle()
    assert not s.get("incidents") and s.get("incident") is None
    s.put("incident","execution_reconciliation_required")
    runtime.cycle()
    assert not s.get("incidents") and s.get("incident") is None
    s.close()


def test_repeated_established_connections_reset_retry_budget_and_public_ack_metadata_is_ignored():
    from quantpilot.tests.test_paper_hybrid_feed import FakeSocket, NoSleepStop, _connect_for, _ack, APPROVAL
    from quantpilot.paper.feeds import HybridFeed, TICK_TR, QUOTE_TR, NOTICE_TR
    stop = NoSleepStop()
    def finish():
        stop.set()
        raise TimeoutError()
    sockets=[]
    for index in range(8):
        notice=json.loads(_ack(NOTICE_TR,APPROVAL.notice_key,crypto=True))
        notice["header"].pop("encrypt")  # Cipher lengths and encrypted frames define the notice boundary.
        sockets.append(FakeSocket([json.dumps(notice),_ack(TICK_TR,"005930",crypto=True),_ack(QUOTE_TR,"005930"),
                                   finish if index == 7 else ConnectionError("fixture disconnect")]))
    feed=HybridFeed(connect=_connect_for(*sockets),approval_supplier=lambda:APPROVAL,clock=lambda:NOW,max_reconnects=2)
    observed=[]
    feed.run(stop,lambda:HybridFeed.desired(["005930"]),observed.append)
    assert len([x for x in observed if x["kind"]=="feed_connected"]) == 8
    assert stop.waits == [1.0]*7
    assert observed[-1] == {"kind":"feed_stopped","reason":"requested"}


def test_core_daily_failure_preserves_unaffected_holding_but_not_unknown_order_symbol(tmp_path):
    from dataclasses import replace
    from quantpilot.paper.broker import KisGateway
    from quantpilot.paper.calendar import Session
    from quantpilot.tests.unit.test_intraday_durable_gateway import Client
    from quantpilot.packages.core.execution.paper_reconciliation import PaperReconciliationUnavailable
    from quantpilot.packages.core.execution.paper_submission import PaperSubmissionOutcomeUnknown
    from quantpilot.packages.core.marketdata.types import Quote
    s = Store(tmp_path/"s")
    s.control("start",now=NOW)
    held = SimpleNamespace(symbol="000660",strategy_id="trend_pullback",version="1",stop=69000.,target=72000.)
    s.reserve(order_id="held",signal=held,quantity=1,price=70000,side="buy",now=NOW,policy_version=1,reason="fixture")
    s.update_order("held","filled",1,70000,NOW)
    s.put("weights",{"trend_pullback":.6})
    client=Client()
    client.filled=True
    raw_balance=client.get_balance()
    client.get_balance=lambda **kwargs:replace(raw_balance,positions=(replace(raw_balance.positions[0],symbol="000660"),))
    calendar=SimpleNamespace(session=lambda _:Session(NOW-timedelta(hours=1),NOW+timedelta(hours=5)),current_open_session_date=lambda _:NOW.date())
    gateway=KisGateway(s,client,calendar,lambda:NOW)
    try:
        gateway.begin()
        assert gateway.reconcile(NOW)
        intent=SimpleNamespace(symbol="005930",strategy_id="trend_pullback",version="1",stop=69000.,target=72000.,entry_atr14=1000.)
        s.reserve(order_id="unknown",signal=intent,quantity=1,price=70000,side="buy",now=NOW,policy_version=1,reason="fixture")
        client.order_outcome=KisPaperTransportError("fixture")
        with pytest.raises(PaperSubmissionOutcomeUnknown):
            gateway.submit(next(o for o in s.orders() if o["id"]=="unknown"),Quote(symbol="005930",last=70000,bid=69900,ask=70000,as_of=NOW),NOW)
        def unavailable(*args,**kwargs):
            raise TimeoutError("fixture")
        client.get_daily_orders_and_fills=unavailable
        with pytest.raises(PaperReconciliationUnavailable) as error:
            gateway.reconcile(NOW)
        assert error.value.detail["stage"]=="daily_orders"
        assert gateway.protection_allowed("000660") and not gateway.protection_allowed("005930")
        assert gateway.entry_reconciled is False and s.get("reconciliation_complete") is False
    finally:
        gateway.end()
        gateway.close()
        s.close()


def test_missing_month_close_has_an_explicit_audited_remedy_without_clearing_halts(tmp_path):
    from quantpilot.paper.valuation import roll_baselines, record_close, month_baseline_preview, apply_month_baseline
    from quantpilot.paper.calendar import Calendar
    from quantpilot.paper.stabilization import FEATURES, acceptance
    s=Store(tmp_path/"experiment.sqlite3")
    s.configure(dict(FEATURES,data_mode="paper_trading"),1)
    version=s.policy.version
    prior=datetime(2026,9,29,1,tzinfo=timezone.utc)
    now=datetime(2026,10,1,6,31,tzinfo=timezone.utc)
    s.control("start",now=prior)
    signal=SimpleNamespace(symbol="005930",strategy_id="trend_pullback",version="1",stop=69000.,target=72000.)
    for side in ("buy","sell"):
        s.reserve(order_id=side,signal=signal,quantity=1,price=70000,side=side,now=prior,policy_version=version,reason="fixture")
        s.update_order(side,"filled",1,70000,prior)
    s.control("pause",now=now)
    roll_baselines(s,Calendar(),now)
    assert s.get("month_base_valid") is False
    assert s.get("month_base_source") == "2026-09-30"
    assert month_baseline_preview(s,now)["status"]=="blocked"
    record_close(s,now,valid=True,equity=s.get("cash"))
    loss={"day":"2026-10-01","daily_halted":True,"drawdown_halted":True,"peak":5_000_000}
    s.put("intraday_loss_state",loss)
    previous_daily=s.get("day_base")
    assert month_baseline_preview(s,now)["status"]=="ready"
    s.close()
    with pytest.raises(ValueError,match="policy_version_conflict"):
        apply_month_baseline(tmp_path,now,version-1,"verified month epoch reset")
    result=apply_month_baseline(tmp_path,now,version,"verified month epoch reset")
    assert result["status"]=="applied" and result["orders_armed"] is False
    s=Store(tmp_path/"experiment.sqlite3")
    assert s.get("month_base_valid") is True and s.get("month_base")==s.get("cash")
    assert s.get("day_base")==previous_daily and s.get("day_base_valid") is False
    assert s.get("control")=="paused" and s.get("intraday_loss_state")==loss
    assert s.db.execute("SELECT COUNT(*) FROM audit WHERE kind='baseline_override'").fetchone()[0]==1
    assert "manual_baseline_override" in acceptance(s,Calendar(),"2026-10-01",now)["reasons"]
    with pytest.raises(ValueError,match="invalid_current_month_baseline_required"):
        apply_month_baseline(tmp_path,now,version,"duplicate month epoch reset")
    s.close()
