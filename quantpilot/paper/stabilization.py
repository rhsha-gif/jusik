"""Explicit profile migration and read-only four-session acceptance evidence."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import environment_safe

FEATURES = {"shared_api_budget_enabled": True, "hybrid_feed_enabled": True,
            "supervisor_enabled": True, "independent_reports_enabled": True}
TRIAL = {"initial_capital": 5_000_000, "max_positions": 1, "symbol_cap": 0.10,
         "trade_risk": 0.001, "daily_loss_limit": 0.01, "peak_drawdown_limit": 0.05}


def preview(directory):
    from quantpilot.paper.dashboard import ledger
    directory = Path(directory).resolve()
    with ledger(directory / "experiment.sqlite3") as view:
        policy = view.policy
        reasons = []
        if policy.data_mode != "paper_trading" or policy.strategy_generation != "legacy":
            reasons.append("legacy_paper_profile_required")
        if view.get("control") not in {"paused", "stopped"}:
            reasons.append("paused_profile_required")
        if view.orders(True):
            reasons.append("open_orders_require_reconciliation")
        if any(getattr(policy, key) != value for key, value in TRIAL.items()):
            reasons.append("approved_trial_profile_mismatch")
        return {"status": "blocked" if reasons else "ready", "reasons": reasons,
                "policy_version": policy.version, "feature_changes": FEATURES,
                "risk_settings": TRIAL, "orders_armed": False, "live_trading_enabled": False,
                "backup_required": True}


def apply(directory, environment, expected_version):
    """Operator-invoked only. Pause and stop services before applying this migration."""
    from quantpilot.paper.cli import process_lock
    from quantpilot.paper.store import Store
    from quantpilot.paper.dashboard import open_ledger
    if not environment_safe(environment):
        raise ValueError("unsafe_environment")
    directory = Path(directory).resolve()
    if any((p / ".git").exists() for p in (directory, *directory.parents)):
        raise ValueError("runtime_directory_inside_repository")
    with ExitStack() as stack:
        for role in ("supervisor", "trader", "worker", "reporter"):
            stack.enter_context(process_lock(directory / (role + ".lock")))
        proposed = preview(directory)
        if proposed["status"] != "ready":
            raise ValueError(proposed["reasons"][0])
        if proposed["policy_version"] != expected_version:
            raise ValueError("policy_version_conflict")
        now = datetime.now(timezone.utc)
        backups = directory / "backups" / now.strftime("stabilization-%Y%m%dT%H%M%S%fZ")
        backups.mkdir(parents=True)
        for name in ("experiment.sqlite3", "broker.sqlite3"):
            path = directory / name
            if path.exists():
                source = open_ledger(path)
                target = sqlite3.connect(backups / name)
                try:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("backup_integrity_failed")
                finally:
                    target.close()
                    source.close()
        store = Store(directory / "experiment.sqlite3")
        try:
            policy = store.configure(FEATURES, expected_version)
            store.put("recovery_required", True)
            store.put("stabilization_run_start", now.isoformat())
            store.put("stabilization_backup", str(backups))
            store.audit("stabilization_applied", {"version": policy.version, "features": FEATURES}, now)
            return {"status": "applied", "policy_version": policy.version, "backup": str(backups),
                    "control": store.get("control"), "orders_armed": False}
        finally:
            store.close()


def acceptance(view, calendar, start_day, now):
    """Four complete trading sessions; missing evidence never implies success."""
    import math
    import re
    from quantpilot.paper.valuation import close_for_day
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_day):
        raise ValueError("acceptance_start_date_invalid")
    first = datetime.fromisoformat(start_day).replace(tzinfo=KST)
    if first > now or (now - first).days > 60:
        raise ValueError("acceptance_window_invalid")
    sessions = []
    for offset in range((now.astimezone(KST).date() - first.date()).days + 1):
        stamp = first + timedelta(days=offset)
        session = calendar.session(stamp)
        if session and session.closes <= now:
            sessions.append((stamp.date().isoformat(), session))
        if len(sessions) == 4:
            break
    days = []
    for day, session in sessions:
        report = view.get("operation_report:" + day)
        reasons = []
        if not report:
            reasons.append("operation_report_missing")
        else:
            if not report.get("close_confirmed"):
                reasons.append("close_unconfirmed")
            if report.get("positions") or report.get("open_orders"):
                reasons.append("not_flat_at_close")
            if report.get("daily_pnl_incomplete"):
                reasons.append("baseline_or_valuation_missing")
            proof = close_for_day(view, day) or {}
            prior_day = calendar.previous_session_date(session.opens)
            prior_close = close_for_day(view, prior_day) or {}
            initial_session = all(datetime.fromisoformat(o["at"]).astimezone(KST).date().isoformat() >= day for o in view.orders())
            expected = prior_close.get("equity") if prior_close.get("valid") else view.get("initial_capital") if initial_session else None
            expected_source = prior_day if prior_close.get("valid") else "initial_capital" if initial_session else None
            if (expected is None or not isinstance(proof.get("day_base"), (int, float))
                    or not math.isclose(proof["day_base"], expected, abs_tol=.01, rel_tol=0)
                    or proof.get("day_base_source") != expected_source):
                reasons.append("daily_baseline_proof_missing_or_changed")
            original = view.get("operation_initial:" + day, report)
            if original.get("close_report_delay_seconds", 61) > 60:
                reasons.append("operation_report_late")
            if report.get("api_budget", {}).get("violations") != 0:
                reasons.append("api_budget_evidence_missing_or_violated")
            if not view.get("session_coverage:" + day, {}).get("started_before_open"):
                reasons.append("full_session_coverage_missing")
            if not view.get("market_ingress:" + day):
                reasons.append("paper_stream_ingress_unverified")
        review = view.db.execute("SELECT state,error FROM jobs WHERE id=?", (day + ":postclose",)).fetchone()
        if not review or review[0] not in {"completed", "failed"} or (review[0] == "failed" and not review[1]):
            reasons.append("ai_terminal_result_missing")
        days.append({"day": day, "passed": not reasons, "reasons": reasons})
    interventions = []
    for row in view.db.execute("SELECT at FROM audit WHERE kind='operator_incident_intervention'"):
        at = datetime.fromisoformat(row[0])
        if first <= at <= now:
            interventions.append(at)
    overall = []
    if view.policy.data_mode != "paper_trading" or not all(getattr(view.policy, flag) for flag in FEATURES):
        overall.append("stabilized_paper_profile_required")
    activated = view.get("stabilization_run_start")
    activation_deadline = sessions[0][1].opens if sessions else first
    if not activated or datetime.fromisoformat(activated) > activation_deadline:
        overall.append("full_session_activation_missing")
    if len(days) != 4:
        overall.append("four_trading_sessions_pending")
    if len(interventions) > 1:
        overall.append("incident_intervention_limit_exceeded")
    if not any(view.get("paper_ingress_verified:" + day, {}).get("execution_notice") for day, _ in sessions):
        overall.append("paper_execution_ingress_unverified")
    if any(first <= datetime.fromisoformat(r[0]) <= now for r in view.db.execute("SELECT at FROM audit WHERE kind='baseline_override'")):
        overall.append("manual_baseline_override")
    proofs = ledger_proofs(view, first, now)
    for proof, passed in proofs.items():
        if not passed:
            overall.append(proof + "_unverified")
    return {"status": "passed" if len(days) == 4 and all(d["passed"] for d in days) and not overall else "pending",
            "days": days, "reasons": overall, "proofs": proofs, "incident_interventions": len(interventions),
            "live_trading_enabled": False}


def ledger_proofs(view, start, end):
    """Compute conservation from durable cumulative fills, not success flags."""
    import math
    orders = view.orders()
    cash = float(view.get("initial_capital"))
    quantities = {}
    identities = []
    for order in orders:
        sign = 1 if order["side"] == "buy" else -1
        cash += -sign * order["amount"] - order["cost"]
        quantities[order["symbol"]] = quantities.get(order["symbol"], 0) + sign * order["filled"]
        if order.get("broker_reference"):
            day = datetime.fromisoformat(order["at"]).astimezone(KST).date().isoformat()
            identities.append((day, order["broker_reference"]))
    trades = [dict(r) for r in view.db.execute("SELECT * FROM trades")]
    fills_match = all(sum(t["quantity"] for t in trades if t["id"].rsplit(":", 1)[0] == o["id"]) == o["filled"]
                      for o in orders if o["side"] == "sell")
    finality = []
    if view.db.execute("SELECT 1 FROM sqlite_master WHERE name='bar_finalizations'").fetchone():
        finality = [(datetime.fromisoformat(r[0]), datetime.fromisoformat(r[1])) for r in view.db.execute("SELECT start,observed_at FROM bar_finalizations")
                    if start <= datetime.fromisoformat(r[1]) <= end]
    return {"ledger_conservation": math.isclose(cash, view.get("cash"), abs_tol=0.01, rel_tol=0)
            and {s: q for s, q in quantities.items() if q} == {p["symbol"]: p["quantity"] for p in view.positions()},
            "no_duplicate_dispatch": len(identities) == len(set(identities)),
            "no_duplicate_fill": fills_match and len({t["id"] for t in trades}) == len(trades),
            "finality_enforced": bool(finality) and all((observed - bar).total_seconds() >= 75 for bar, observed in finality)}
