"""Bounded incident lifecycles and code-owned entry gate accounting."""

from datetime import datetime
import re

from quantpilot.paper.calendar import KST
from quantpilot.packages.core.kis_paper import safe_failure

REASONS = frozenset({
    "manual_pause", "no_signal", "warming", "stale_feed", "reconciliation_required",
    "invalid_baseline", "loss_limit", "entry_cutoff", "recovery_required", "stopped",
    "unsafe_environment", "paper_submission_disabled", "broker_read_backoff",
    "completed_bar_revised", "quote_incomplete", "quote_stale_or_crossed",
    "position_reconciliation_required", "balance_not_reconciled", "policy_version_conflict",
    "new_entries_paused", "broker_sell_quantity_unavailable", "broker_experiment_slice_exhausted",
    "data_unavailable", "session_closed", "execution_rejected", "internal_error",
    "submission_evidence_expired", "paper_kill_engaged", "feed_unavailable", "order_identity_changed",
    "entry_window_closed", "final_entry_risk_failed", "final_sell_attribution_failed", "conflicting_order",
})


def failure(exc, stage):
    detail = safe_failure(exc, stage)
    inner = getattr(exc, "detail", None)
    if isinstance(inner, dict) and inner.get("stage") in {"balance", "daily_orders"}:
        detail = {"stage": inner["stage"], "error": inner.get("error") if inner.get("error") in {
            "KisPaperGatewayRejected", "KisPaperTransportError", "KisPaperProtocolError", "ValueError", "TimeoutError"
        } else type(exc).__name__, "broker_code": inner.get("broker_code")}
    code = detail.get("broker_code")
    detail["broker_code"] = code if code and re.fullmatch(r"(?:EGW|APBK|OPSQ)[0-9]{4,5}", code) else None
    detail["reason_code"] = str(exc) if type(exc) is ValueError and str(exc) in REASONS else "internal_error"
    return detail


def subsystem(code, symbol=None):
    if "protection" in code:
        return "protection" + (":" + symbol if symbol else "")
    if "reconcil" in code or "order" in code or "account" in code:
        return "reconciliation"
    if "feed" in code or "quote" in code:
        return "feed"
    return "runtime"


def open_incident(store, scope, code, now):
    with store.transaction():
        incidents = store.get("incidents", {})
        old = incidents.get(scope, {})
        incidents[scope] = {"reason_code": code, "since": old.get("since", now.isoformat()),
                            "last_seen": now.isoformat(), "count": old.get("count", 0) + 1}
        store.put("incidents", incidents)
        store.put("incident", next(iter(incidents.values()))["reason_code"])
        if not old or old.get("reason_code") != code:
            store.audit("incident_opened", {"subsystem": scope, **incidents[scope]}, now)


def recover_incident(store, scope, now):
    with store.transaction():
        incidents = store.get("incidents", {})
        old = incidents.pop(scope, None)
        if old:
            store.audit("incident_recovered", {"subsystem": scope, **old}, now)
            store.put("incidents", incidents)
            store.put("incident", next(iter(incidents.values()))["reason_code"] if incidents else None)


def publish_gates(store, reasons, now):
    reasons = sorted(set(reasons))
    day = now.astimezone(KST).date().isoformat()
    with store.transaction():
        old = store.get("entry_gates", {})
        totals = store.get("entry_gate_seconds:" + day, {})
        if old.get("day") == day and old.get("observed_at"):
            elapsed = max(0, (now - datetime.fromisoformat(old["observed_at"])).total_seconds())
            for reason in old.get("reasons", []):
                totals[reason] = totals.get(reason, 0) + elapsed
        if old.get("reasons") != reasons or old.get("day") != day:
            store.audit("entry_gates_changed", {"reasons": reasons}, now)
        store.put("entry_gate_seconds:" + day, totals)
        store.put("entry_gates", {"day": day, "reasons": reasons, "observed_at": now.isoformat()})


def gate_seconds(store, now):
    day = now.astimezone(KST).date().isoformat()
    totals = store.get("entry_gate_seconds:" + day, {})
    current = store.get("entry_gates", {})
    if current.get("day") == day and current.get("observed_at"):
        elapsed = max(0, (now - datetime.fromisoformat(current["observed_at"])).total_seconds())
        for reason in current.get("reasons", []):
            totals[reason] = totals.get(reason, 0) + elapsed
    return totals
