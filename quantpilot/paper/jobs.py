"""Separate-process AI jobs; a slow provider never blocks execution cycles."""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
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
    if store.policy.independent_reports_enabled:
        for row in store.db.execute("SELECT id,kind FROM jobs WHERE state='retry_wait' ORDER BY at").fetchall():
            retry = store.get("job_retry:" + row["id"])
            if retry and datetime.fromisoformat(retry) <= now:
                job = {"key": row["id"], "kind": row["kind"]}
                break
    if not job:
        return {"status": "idle"}
    # An hourly job must remain claimable when initial collection has not arrived.
    if job["kind"] != "postclose" and store.policy.ai_enabled:
        try:
            observed = datetime.fromisoformat(store.get("evidence", {})["observed_at"])
            valid = observed.tzinfo is not None and observed <= now < observed + timedelta(minutes=75)
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            return {"status": "waiting_for_evidence", "job": job["key"]}
    with store.transaction():
        retry_row = store.db.execute("SELECT state FROM jobs WHERE id=?", (job["key"],)).fetchone()
        retry_at = store.get("job_retry:" + job["key"])
        if retry_row and retry_row[0] == "retry_wait" and retry_at and datetime.fromisoformat(retry_at) <= now:
            store.db.execute("UPDATE jobs SET state='retry_claimable' WHERE id=? AND state='retry_wait'", (job["key"],))
        claimed = store.db.execute(
            "INSERT OR IGNORE INTO jobs(id,kind,state,at) VALUES(?,?,'running',?)",
            (job["key"], job["kind"], now.isoformat()),
        ).rowcount
        if not claimed:
            claimed = store.db.execute("UPDATE jobs SET state='running' WHERE id=? AND state='retry_claimable'", (job["key"],)).rowcount
        attempt = store.db.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM job_attempts WHERE job_id=?", (job["key"],)).fetchone()[0]
        if claimed:
            store.db.execute("INSERT INTO job_attempts(job_id,attempt,at,state) VALUES(?,?,?,'running')", (job["key"], attempt, now.isoformat()))
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
            evidence = dict(evidence, report=store.get("operation_report:" + job["key"].split(":")[0]) or snapshot(store, now=now))
            result = run_review(
                evidence, now, primary=store.policy.primary_ai, runner=runner
            )
        else:
            observed = datetime.fromisoformat(evidence["observed_at"])

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
        error = "ai_disabled" if not store.policy.ai_enabled else type(exc).__name__
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
        report = snapshot(store, now=now)
    retrying = bool(error and job["kind"] == "postclose" and store.policy.independent_reports_enabled
                    and store.policy.ai_enabled and attempt < 3)
    state = "retry_wait" if retrying else "failed" if error else "completed"
    with store.transaction():
        store.db.execute("UPDATE job_attempts SET state=?,result=?,error=? WHERE job_id=? AND attempt=?",
                         ("failed" if error else "completed", encode(payload), error, job["key"], attempt))
        store.db.execute("UPDATE jobs SET state=?,result=?,error=? WHERE id=?", (state, encode(payload), error, job["key"]))
        if retrying:
            first = store.db.execute("SELECT at FROM job_attempts WHERE job_id=? ORDER BY attempt LIMIT 1", (job["key"],)).fetchone()[0]
            store.put("job_retry:" + job["key"], (datetime.fromisoformat(first) + timedelta(seconds=60 if attempt == 1 else 300)).isoformat())
        if store.policy.strategy_generation == "intraday_v2":
            store.audit(
                "intraday_advisory_result",
                {"job": job["key"], "result": payload, "error": error},
                now,
            )
        if report is not None:
            if store.policy.independent_reports_enabled:
                if not retrying:
                    store.enqueue("review:" + job["key"], "QuantPilot AI 복기\n" + review, now)
                    store.put("last_review", {"job": job["key"], "state": state, "attempts": attempt, "error": error})
            else:
                # Include this job's terminal status in the old combined report too.
                report = snapshot(store, now=now)
                store.put("last_report", report)
                store.enqueue("report:" + job["key"], render(report, review), now)
    if sender and store.policy.slack_enabled:
        drain_outbox(store, sender)
    return {
        "status": state,
        "job": job["key"],
        "error": error,
    }


def recover_interrupted_jobs(store, now=None):
    """Called only while owning the worker lock; never steals a running worker's job."""
    for row in store.db.execute(
        "SELECT id,kind,at FROM jobs WHERE state='running'"
    ).fetchall():
        with store.transaction():
            store.db.execute(
                "UPDATE jobs SET state='failed',error='worker_interrupted' WHERE id=?",
                (row["id"],),
            )
            if row["kind"] == "postclose" and store.policy.independent_reports_enabled:
                now = now or datetime.now(timezone.utc)
                attempts = store.db.execute("SELECT COUNT(*) FROM job_attempts WHERE job_id=?", (row["id"],)).fetchone()[0]
                if not attempts:
                    store.db.execute("INSERT INTO job_attempts VALUES(?,1,?,'failed',NULL,'worker_interrupted')", (row["id"], row["at"]))
                    attempts = 1
                store.db.execute("UPDATE job_attempts SET state='failed',error='worker_interrupted' WHERE job_id=? AND state='running'", (row["id"],))
                if attempts < 3:
                    store.db.execute("UPDATE jobs SET state='retry_wait' WHERE id=?", (row["id"],))
                    first = store.db.execute("SELECT at FROM job_attempts WHERE job_id=? ORDER BY attempt LIMIT 1", (row["id"],)).fetchone()[0]
                    store.put("job_retry:" + row["id"], (datetime.fromisoformat(first) + timedelta(seconds=60 if attempts <= 1 else 300)).isoformat())
                else:
                    store.enqueue("review:" + row["id"], "QuantPilot AI 복기 미완료: worker_interrupted", now)
                    store.put("last_review", {"job": row["id"], "state": "failed", "attempts": attempts, "error": "worker_interrupted"})
            elif row["kind"] == "postclose":
                store.enqueue(
                    "report:" + row["id"],
                    render(snapshot(store), "복기 프로세스가 중단되었습니다."),
                    datetime.now(timezone.utc),
                )
