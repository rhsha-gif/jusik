from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from quantpilot.packages.core.schemas import (
    AuditLogEvent,
    BrokerMode,
    DataMode,
    HarnessModel,
    OrderPlan,
    OrderStatus,
    PortfolioSnapshot,
    UserPolicy,
    new_id,
    utc_now,
)


StatusSeverity = Literal["ok", "warn", "blocked", "critical"]
MonitoringStatus = Literal["ready", "degraded", "blocked"]
ReconciliationStatus = Literal["matched", "mismatch"]


class ReconciliationIssue(HarnessModel):
    issue_type: Literal["cash_mismatch", "position_mismatch", "open_order_mismatch"]
    detail: str
    severity: StatusSeverity = "blocked"
    symbol: str | None = None
    order_id: str | None = None
    internal_value: str | float | None = None
    broker_value: str | float | None = None


class ReconciliationReport(HarnessModel):
    reconciliation_id: str = Field(default_factory=lambda: new_id("recon"))
    policy_id: str
    user_id: str
    run_id: str | None = None
    broker_mode: str
    status: ReconciliationStatus
    severity: StatusSeverity
    order_submission_allowed: bool
    internal_snapshot_id: str
    broker_snapshot_id: str
    internal_cash: float
    broker_cash: float
    cash_delta: float
    internal_positions: dict[str, float] = Field(default_factory=dict)
    broker_positions: dict[str, float] = Field(default_factory=dict)
    internal_open_order_ids: list[str] = Field(default_factory=list)
    broker_open_order_ids: list[str] = Field(default_factory=list)
    issues: list[ReconciliationIssue] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class MonitoringSnapshot(HarnessModel):
    snapshot_id: str = Field(default_factory=lambda: new_id("monsnap"))
    mode: DataMode
    status: MonitoringStatus
    severity: StatusSeverity
    run_id: str | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    broker_mode: str = BrokerMode.mock.value
    live_trading_enabled: bool = False
    market_orders_enabled: bool = False
    guarded_autopilot_enabled: bool = False
    fully_automated_operator_enabled: bool = False
    operator_kill_switch_engaged: bool = False
    fallback_reasons: list[str] = Field(default_factory=list)
    recent_audit_event_ids: list[str] = Field(default_factory=list)
    reconciliation_report: ReconciliationReport | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AuditReference(HarnessModel):
    event_id: str
    action: str
    entity_type: str
    entity_id: str
    source: str
    created_at: datetime


class IncidentBundle(HarnessModel):
    bundle_id: str = Field(default_factory=lambda: new_id("incident"))
    mode: DataMode
    run_id: str | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    severity: StatusSeverity
    fallback_reasons: list[str] = Field(default_factory=list)
    safety_flags: dict[str, Any] = Field(default_factory=dict)
    reconciliation_report: ReconciliationReport | None = None
    recent_audit_references: list[AuditReference] = Field(default_factory=list)
    recent_audit_events: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=utc_now)


class KillSwitchDrillResult(HarnessModel):
    drill_id: str = Field(default_factory=lambda: new_id("ksdrill"))
    policy_id: str | None = None
    operator_run_id: str | None = None
    status: Literal["passed", "failed", "blocked"]
    severity: StatusSeverity
    blocked_order_submission: bool
    order_submission_attempted: bool = False
    submitted_order_plan_ids: list[str] = Field(default_factory=list)
    reason: str
    fallback_reason: str | None = None
    broker_mode: str = BrokerMode.mock.value
    live_trading_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)


def operational_mode_for_policy(policy: UserPolicy | None) -> DataMode:
    if policy is not None and policy.broker == BrokerMode.paper:
        return DataMode.paper_trading
    return DataMode.fixture


def open_order_ids(order_plans: list[OrderPlan], *, policy_id: str | None = None) -> list[str]:
    open_statuses = {OrderStatus.submitted, OrderStatus.accepted, OrderStatus.partially_filled}
    return [
        plan.order_plan_id
        for plan in order_plans
        if plan.status in open_statuses and (policy_id is None or plan.policy_id == policy_id)
    ]


def _position_quantities(snapshot: PortfolioSnapshot) -> dict[str, float]:
    return {position.symbol: round(float(position.quantity), 6) for position in snapshot.positions if position.quantity > 0}


def reconcile_paper_state(
    *,
    policy_id: str,
    user_id: str,
    run_id: str | None,
    broker_mode: str,
    internal_snapshot: PortfolioSnapshot,
    broker_snapshot: PortfolioSnapshot,
    internal_open_order_ids: list[str],
    broker_open_order_ids: list[str],
) -> ReconciliationReport:
    issues: list[ReconciliationIssue] = []
    cash_delta = round(float(broker_snapshot.cash - internal_snapshot.cash), 6)
    if abs(cash_delta) > 0.01:
        issues.append(
            ReconciliationIssue(
                issue_type="cash_mismatch",
                detail="internal and broker cash differ",
                internal_value=round(float(internal_snapshot.cash), 6),
                broker_value=round(float(broker_snapshot.cash), 6),
            )
        )

    internal_positions = _position_quantities(internal_snapshot)
    broker_positions = _position_quantities(broker_snapshot)
    for symbol in sorted(set(internal_positions) | set(broker_positions)):
        internal_quantity = internal_positions.get(symbol, 0.0)
        broker_quantity = broker_positions.get(symbol, 0.0)
        if abs(internal_quantity - broker_quantity) > 1e-6:
            issues.append(
                ReconciliationIssue(
                    issue_type="position_mismatch",
                    detail=f"position quantity mismatch for {symbol}",
                    symbol=symbol,
                    internal_value=internal_quantity,
                    broker_value=broker_quantity,
                )
            )

    internal_open = sorted(set(internal_open_order_ids))
    broker_open = sorted(set(broker_open_order_ids))
    if internal_open != broker_open:
        issues.append(
            ReconciliationIssue(
                issue_type="open_order_mismatch",
                detail="internal and broker open-order ids differ",
                internal_value=",".join(internal_open),
                broker_value=",".join(broker_open),
            )
        )

    status: ReconciliationStatus = "mismatch" if issues else "matched"
    severity: StatusSeverity = "blocked" if issues else "ok"
    return ReconciliationReport(
        policy_id=policy_id,
        user_id=user_id,
        run_id=run_id,
        broker_mode=broker_mode,
        status=status,
        severity=severity,
        order_submission_allowed=not issues,
        internal_snapshot_id=internal_snapshot.snapshot_id,
        broker_snapshot_id=broker_snapshot.snapshot_id,
        internal_cash=round(float(internal_snapshot.cash), 6),
        broker_cash=round(float(broker_snapshot.cash), 6),
        cash_delta=cash_delta,
        internal_positions=internal_positions,
        broker_positions=broker_positions,
        internal_open_order_ids=internal_open,
        broker_open_order_ids=broker_open,
        issues=issues,
    )


def status_severity(
    *,
    status: str,
    safety_flags: dict[str, bool | str],
    reconciliation_report: ReconciliationReport | None = None,
) -> StatusSeverity:
    if bool(safety_flags.get("LIVE_TRADING_ENABLED")):
        return "critical"
    if reconciliation_report is not None and reconciliation_report.severity in {"critical", "blocked"}:
        return reconciliation_report.severity
    if status == "failed":
        return "critical"
    if status == "blocked":
        return "blocked"
    if status == "fallback":
        return "warn"
    return "ok"


def build_incident_bundle(
    *,
    mode: DataMode,
    run_id: str | None,
    policy_id: str | None,
    policy_version: int | None,
    severity: StatusSeverity,
    fallback_reasons: list[str],
    safety_flags: dict[str, bool | str],
    reconciliation_report: ReconciliationReport | None,
    audit_events: list[AuditLogEvent],
    limit: int = 10,
) -> IncidentBundle:
    recent = audit_events[-limit:]
    references = [
        AuditReference(
            event_id=event.event_id,
            action=event.action,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            source=event.source,
            created_at=event.created_at,
        )
        for event in recent
    ]
    return IncidentBundle(
        mode=mode,
        run_id=run_id,
        policy_id=policy_id,
        policy_version=policy_version,
        severity=severity,
        fallback_reasons=fallback_reasons,
        safety_flags=dict(safety_flags),
        reconciliation_report=reconciliation_report,
        recent_audit_references=references,
        recent_audit_events=[event.model_dump(mode="json") for event in recent],
        context={"live_trading_enabled": False, "order_submission_enabled": False},
    )
