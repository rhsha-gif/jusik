from datetime import datetime, timezone
from types import SimpleNamespace
import sys, types
from quantpilot.paper.store import Store
from quantpilot.paper.reporting import snapshot, drain_outbox
from quantpilot.paper.jobs import work_once, recover_interrupted_jobs

NOW = datetime(2026, 9, 10, 7, tzinfo=timezone.utc)


def test_report_queued_only_after_review_terminates(tmp_path, monkeypatch):
    s = Store(tmp_path / "s")
    s.configure({"ai_enabled": True}, 1)
    s.put("ai_due", {"kind": "postclose", "key": "2026-09-10:postclose"})
    stub = types.ModuleType("quantpilot.paper.intelligence")
    stub.IntelligenceError = type("IntelligenceError", (), {})

    def review(*a, **kw):
        assert s.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
        return {"summary": "검토 완료"}

    stub.run_review = review
    stub.run_assessment = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "quantpilot.paper.intelligence", stub)
    assert work_once(s, NOW)["status"] == "completed"
    assert work_once(s, NOW)["status"] == "already_processed"
    assert s.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    s.close()


def test_slack_uncertain_result_not_resent(tmp_path):
    s = Store(tmp_path / "s")
    s.enqueue("one", "report", NOW)
    calls = []

    def send(*a):
        calls.append(a)
        raise TimeoutError()

    sender = SimpleNamespace(send=send)
    drain_outbox(s, sender)
    drain_outbox(s, sender)
    assert len(calls) == 1
    assert s.db.execute("SELECT state FROM outbox").fetchone()[0] == "delivery_unknown"
    s.close()


def test_interrupted_review_still_reports_numbers(tmp_path):
    s = Store(tmp_path / "s")
    s.db.execute(
        "INSERT INTO jobs VALUES('d:postclose','postclose','running',?,NULL,NULL)",
        (NOW.isoformat(),),
    )
    recover_interrupted_jobs(s)
    assert s.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
    assert snapshot(s)["equity"] == 5_000_000
    s.close()
