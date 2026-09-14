"""Date-specific close proofs survive later transient observation failures."""

from datetime import datetime
from hashlib import sha256
import math

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import aware
from quantpilot.paper.store import encode


def close_for_day(store, day):
    evidence = store.get("close:" + str(day))
    if evidence:
        return evidence
    if store.get("last_close_day") == day and store.get("last_close_equity_valid") is True:
        return {"day": day, "valid": True, "equity": store.get("last_close_equity"),
                "observed_at": store.get("last_close_observed_at"), "source": "legacy_close_proof"}
    return None


def roll_baselines(store, calendar, now):
    day = aware(now).astimezone(KST).date().isoformat()
    initial = store.get("initial_capital")
    first_session = all(datetime.fromisoformat(o["at"]).astimezone(KST).date().isoformat() == day
                        for o in store.orders())
    try:
        expected_close = calendar.previous_session_date(now)
    except (AttributeError, ValueError):
        expected_close = None
    close = close_for_day(store, expected_close)
    close_valid = bool(close and close.get("valid") and isinstance(close.get("equity"), (int, float))
                       and math.isfinite(close["equity"]) and close["equity"] > 0)
    with store.transaction():
        if store.get("day") != day:
            store.put("day", day)
            store.put("day_base", close["equity"] if close_valid else initial)
            store.put("day_base_valid", close_valid or first_session)
            store.put("day_base_source", expected_close if close_valid else "initial_capital" if first_session else expected_close)
            store.put("day_base_reason", None if close_valid or first_session else "previous_session_close_missing")
            store.audit("daily_baseline", {"day": day, "source": store.get("day_base_source"),
                                           "valid": store.get("day_base_valid")}, now)
        elif store.get("day_base_valid") is None:
            store.put("day_base_valid", first_session and store.get("day_base", initial) == initial)
            store.put("day_base_source", "initial_capital" if first_session else None)
        elif store.get("day_base_reason") in {"previous_session_close_missing", "previous_session_close_invalidated"} and close_valid:
            store.put("day_base", close["equity"])
            store.put("day_base_valid", True)
            store.put("day_base_source", expected_close)
            store.put("day_base_reason", None)
            store.audit("daily_baseline_rebuilt", {"source": expected_close}, now)
        # Preserve existing explicit intraday overrides.
        if store.get("month") != day[:7]:
            store.put("month", day[:7])
            store.put("month_base", close["equity"] if close_valid else initial)
            store.put("month_base_source", expected_close if close_valid or not first_session else "initial_capital")
            store.put("month_base_valid", close_valid or first_session)
        elif store.get("month_base_valid") is False:
            monthly_close = close_for_day(store, store.get("month_base_source"))
            if monthly_close and monthly_close.get("valid"):
                store.put("month_base", monthly_close["equity"])
                store.put("month_base_valid", True)


def record_close(store, now, *, valid, equity=None):
    day = aware(now).astimezone(KST).date().isoformat()
    valid = valid is True and isinstance(equity, (int, float)) and not isinstance(equity, bool) and math.isfinite(equity) and equity > 0
    evidence = {"day": day, "valid": valid, "equity": equity if valid else None,
                "observed_at": now.isoformat(), "reconciled_at": store.get("last_reconciled_at"),
                "positions": len(store.positions()), "open_orders": len(store.orders(True)),
                "cash": store.get("cash"), "position_snapshot": store.positions(),
                "day_base": store.get("day_base", store.get("initial_capital")),
                "day_base_valid": store.get("day_base_valid", False),
                "day_base_source": store.get("day_base_source"),
                "source": "broker_reconciled_close"}
    identity = sha256(encode(evidence).encode()).hexdigest()
    with store.transaction():
        store.db.execute("INSERT OR IGNORE INTO close_observations VALUES(?,?,?,?,?)",
                         (identity, day, now.isoformat(), int(valid), encode(evidence)))
        existing = close_for_day(store, day)
        store.put("close_attempt:" + day, evidence)
        if valid or not (existing and existing.get("valid")):
            store.put("close:" + day, dict(evidence, evidence_id=identity))
            if day >= store.get("last_close_day", ""):
                store.put("last_close_day", day)
                store.put("last_close_observed_at", now.isoformat())
                store.put("last_close_equity_valid", valid)
                if valid:
                    store.put("last_close_equity", equity)
        if not existing or existing.get("valid") != valid:
            store.audit("close_observed", dict(evidence, evidence_id=identity,
                                              previous_proof_preserved=bool(existing and existing.get("valid") and not valid)), now)
    return close_for_day(store, day)


def invalidate_close(store, day, now, reason):
    """Explicit ledger corrections supersede proofs without erasing observations."""
    evidence = close_for_day(store, day)
    if store.get("day_base_source") == day:
        store.put("day_base_valid", False)
        store.put("day_base_reason", "previous_session_close_invalidated")
    if store.get("month_base_source") == day:
        store.put("month_base_valid", False)
    if evidence and evidence.get("valid"):
        store.put("close:" + day, dict(evidence, valid=False, invalidated_at=now.isoformat(),
                                       invalidation_reason=reason))
        if store.get("last_close_day") == day:
            store.put("last_close_equity_valid", False)
        store.audit("close_proof_invalidated", {"day": day, "reason": reason}, now)


def month_baseline_preview(store, now):
    """Review a new monthly risk epoch; never claim to reconstruct a lost close."""
    day = aware(now).astimezone(KST).date().isoformat()
    close = close_for_day(store, day) or {}
    reasons = []
    if store.policy.data_mode != "paper_trading":
        reasons.append("paper_profile_required")
    if store.get("month_base_valid") is not False or store.get("month") != day[:7]:
        reasons.append("invalid_current_month_baseline_required")
    if store.get("control") not in {"paused", "stopped"} or store.positions() or store.orders(True):
        reasons.append("flat_paused_profile_required")
    if (not close.get("valid") or close.get("positions") != 0 or close.get("open_orders") != 0
            or close.get("cash") is None or not math.isclose(close.get("equity", 0), store.get("cash"), abs_tol=.01, rel_tol=0)
            or not math.isclose(close["cash"], store.get("cash"), abs_tol=.01, rel_tol=0)):
        reasons.append("current_flat_close_proof_required")
    return {"status": "blocked" if reasons else "ready", "reasons": reasons,
            "policy_version": store.policy.version, "previous_month_base": store.get("month_base"),
            "proposed_month_base": close.get("equity"), "source_day": day,
            "evidence_id": close.get("evidence_id"), "orders_armed": False}


def apply_month_baseline(directory, now, expected_version, reason):
    """Explicit operator remedy. Preserve daily baseline, pause and loss halts."""
    from pathlib import Path
    from contextlib import ExitStack
    from quantpilot.paper.cli import process_lock
    from quantpilot.paper.store import Store
    directory = Path(directory).resolve()
    if any((p / ".git").exists() for p in (directory, *directory.parents)):
        raise ValueError("runtime_directory_inside_repository")
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        raise ValueError("baseline_review_reason_required")
    with ExitStack() as stack:
        for role in ("supervisor", "trader", "worker", "reporter"):
            stack.enter_context(process_lock(directory / (role + ".lock")))
        store = Store(directory / "experiment.sqlite3")
        stack.callback(store.close)
        with store.transaction():
            proposal = month_baseline_preview(store, now)
            if proposal["status"] != "ready":
                raise ValueError(proposal["reasons"][0])
            if proposal["policy_version"] != expected_version:
                raise ValueError("policy_version_conflict")
            store.verify_cash()
            store.put("month_base", proposal["proposed_month_base"])
            store.put("month_base_source", proposal["source_day"])
            store.put("month_base_valid", True)
            store.put("month_base_epoch", {"kind": "operator_close_reset", "at": now.isoformat()})
            store.audit("baseline_override", dict(proposal, kind="monthly_risk_epoch", reason=reason), now)
            return dict(proposal, status="applied", control=store.get("control"), orders_armed=False)
