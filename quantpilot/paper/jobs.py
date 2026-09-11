"""Separate-process AI jobs; a slow provider never blocks execution cycles."""

from __future__ import annotations
from datetime import datetime, timezone
from quantpilot.paper.store import encode
from quantpilot.paper.reporting import snapshot, render, drain_outbox


def work_once(store, now=None, runner=None, sender=None):
    now = now or datetime.now(timezone.utc)
    if sender and store.policy.slack_enabled:
        drain_outbox(store, sender)
    if store.policy.research_enabled:
        from quantpilot.paper.research import forward_once

        forward_once(store, now)
    job = store.get("ai_due")
    if not job:
        return {"status": "idle"}
    # An hourly job must remain claimable when initial collection has not arrived.
    if job["kind"] != "postclose" and store.policy.ai_enabled:
        from datetime import timedelta
        try:
            observed = datetime.fromisoformat(store.get("evidence", {})["observed_at"])
            valid = observed.tzinfo is not None and observed <= now < observed + timedelta(minutes=75)
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            return {"status": "waiting_for_evidence", "job": job["key"]}
    with store.transaction():
        claimed = store.db.execute(
            "INSERT OR IGNORE INTO jobs(id,kind,state,at) VALUES(?,?,'running',?)",
            (job["key"], job["kind"], now.isoformat()),
        ).rowcount
    if not claimed:
        return {"status": "already_processed"}
    result = None
    error = None
    try:
        if not store.policy.ai_enabled:
            raise ValueError("ai_disabled")
        from quantpilot.paper.intelligence import (
            run_assessment,
            run_review,
            IntelligenceError,
        )

        evidence = store.get("evidence", {})
        evidence = dict(
            evidence,
            symbols=evidence.get("symbols", []),
            strategies=list(store.policy.active_strategies),
        )
        if store.policy.strategy_generation == "intraday_v2":
            store.audit(
                "intraday_advisory_input",
                {"job": job["key"], "evidence": evidence},
                now,
            )
        if job["kind"] == "postclose":
            evidence = dict(evidence, report=snapshot(store))
            result = run_review(
                evidence, now, primary=store.policy.primary_ai, runner=runner
            )
        else:
            observed = datetime.fromisoformat(evidence["observed_at"])
            from datetime import timedelta

            if observed.tzinfo is None or not observed <= now < observed + timedelta(
                minutes=75
            ):
                raise ValueError("stale_evidence")
            result = run_assessment(
                evidence, observed, primary=store.policy.primary_ai, runner=runner
            )
            if not isinstance(result, IntelligenceError):
                if store.policy.strategy_generation == "intraday_v2":
                    result = result.model_copy(
                        update={
                            "expires_at": min(
                                result.expires_at, observed + timedelta(minutes=30)
                            )
                        }
                    )
                store.put("assessment", result.model_dump(mode="json"))
        if isinstance(result, IntelligenceError):
            error = result.code.value
    except Exception as exc:
        error = type(exc).__name__
    payload = (
        result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    )
    report = None
    if job["kind"] == "postclose":
        from quantpilot.paper.research import postclose

        research = postclose(store, now, payload, runner=runner)
        store.put("last_research", research)
        review = (
            f"복기 미완료: {error}. 기존 검증 규칙을 유지합니다."
            if error
            else (
                str(payload)
                if not isinstance(payload, dict)
                else str(payload.get("summary", payload))
            )
        )
        report = snapshot(store)
    with store.transaction():
        store.db.execute(
            "UPDATE jobs SET state=?,result=?,error=? WHERE id=?",
            ("failed" if error else "completed", encode(payload), error, job["key"]),
        )
        if store.policy.strategy_generation == "intraday_v2":
            store.audit(
                "intraday_advisory_result",
                {"job": job["key"], "result": payload, "error": error},
                now,
            )
        if report is not None:
            store.put("last_report", report)
            store.enqueue("report:" + job["key"], render(report, review), now)
    if sender and store.policy.slack_enabled:
        drain_outbox(store, sender)
    return {
        "status": "failed" if error else "completed",
        "job": job["key"],
        "error": error,
    }


def recover_interrupted_jobs(store):
    """Called only while owning the worker lock; never steals a running worker's job."""
    for row in store.db.execute(
        "SELECT id,kind FROM jobs WHERE state='running'"
    ).fetchall():
        with store.transaction():
            store.db.execute(
                "UPDATE jobs SET state='failed',error='worker_interrupted' WHERE id=?",
                (row["id"],),
            )
            if row["kind"] == "postclose":
                store.enqueue(
                    "report:" + row["id"],
                    render(snapshot(store), "복기 프로세스가 중단되었습니다."),
                    datetime.now(timezone.utc),
                )
