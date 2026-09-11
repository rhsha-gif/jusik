"""Admission binds a frozen report and strategy version; it grants no order authority."""

from hashlib import sha256
import json
import ast
from pathlib import Path

VALIDATED_POLICY = {
    "initial_capital": 5_000_000,
    "trade_risk": 0.005,
    "symbol_cap": 0.25,
    "strategy_cap": 0.60,
    "max_positions": 2,
    "daily_loss_limit": 0.01,
    "peak_drawdown_limit": 0.05,
    "fee_bps": 1.40527,
    "sell_tax_bps": 20.0,
    "slippage_bps": 10.0,
    "entry_cutoff_minutes": 30,
    "liquidation_minutes": 20,
    "quote_ttl_seconds": 15,
    "cycle_seconds": 10,
    "max_candidates": 20,
}


def digest(value):
    return sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _implementation_hash():
    """Bind frozen evidence to executable rules, data, risk and execution semantics."""
    paper = Path(__file__).resolve().parents[1]
    paths = list((paper / "intraday").glob("*.py")) + [
        paper / (name + ".py")
        for name in (
            "calendar",
            "config",
            "data",
            "strategy",
            "risk",
            "runtime",
            "store",
            "broker",
            "intelligence",
            "jobs",
        )
    ]
    return digest(
        {
            path.relative_to(paper)
            .as_posix(): sha256(
                ast.dump(
                    ast.parse(path.read_text(encoding="utf-8")),
                    include_attributes=False,
                ).encode()
            )
            .hexdigest()
            for path in sorted(paths)
        }
    )


IMPLEMENTATION_HASH = _implementation_hash()


def admitted(store, strategy_id, version):
    from quantpilot.paper.intraday.strategy import SPECS

    if not any(s.strategy_id == strategy_id and s.version == version for s in SPECS):
        return False
    record = store.get("intraday_admission", {}).get(strategy_id)
    if not record or record.get("version") != version:
        return False
    report = record.get("report", {})
    candidate = record.get("candidate", {})
    return (
        record.get("report_hash") == digest(report)
        and report.get("status") == "paper_eligible"
        and report.get("version") == version
        and report.get("strategy_id") == strategy_id
        and candidate.get("version") == version
        and report.get("candidate_hash") == digest(candidate)
        and report.get("historical_passed") is True
        and report.get("multiple_comparison_corrected") is True
        and report.get("shadow_days", 0) >= 20
        and report.get("shadow_round_trips", 0) > 0
        and report.get("execution_policy") == VALIDATED_POLICY
        and all(
            getattr(store.policy, key) == value
            for key, value in VALIDATED_POLICY.items()
        )
    )


def admit(store, experiment, report, now):
    """Copy a computed qualification into a new, stopped experiment ledger."""
    row = experiment.db.execute(
        "SELECT body FROM looks WHERE strategy=? ORDER BY id DESC LIMIT 1",
        (report["strategy_id"],),
    ).fetchone()
    if (
        not row
        or json.loads(row[0]) != report
        or report.get("status") != "paper_eligible"
        or report.get("execution_policy") != VALIDATED_POLICY
    ):
        raise ValueError("latest_passing_evaluation_required")
    candidate = experiment.read("frozen", report["strategy_id"])
    if not candidate or digest(candidate) != report["candidate_hash"]:
        raise ValueError("frozen_candidate_changed")
    with store.transaction():
        if store.get("control") != "stopped" or store.orders() or store.positions():
            raise ValueError("intraday_admission_requires_new_stopped_ledger")
        capital = VALIDATED_POLICY["initial_capital"]
        if (
            store.policy.initial_capital != capital
            or store.get("initial_capital") != capital
            or store.get("cash") != capital
        ):
            raise ValueError("validated_initial_capital_required")
        changes = {
            key: value
            for key, value in VALIDATED_POLICY.items()
            if key != "initial_capital" and getattr(store.policy, key) != value
        }
        if store.policy.strategy_generation != "intraday_v2":
            changes.update(strategy_generation="intraday_v2", research_enabled=False)
        if changes:
            store.configure(
                changes,
                store.policy.version,
            )
        records = store.get("intraday_admission", {})
        records[report["strategy_id"]] = {
            "version": report["version"],
            "report": report,
            "report_hash": digest(report),
            "candidate": candidate,
        }
        store.put("intraday_admission", records)
        store.audit(
            "intraday_candidate_admitted",
            {
                "version": report["version"],
                "report_hash": digest(report),
                "orders_armed": False,
            },
            now,
        )
