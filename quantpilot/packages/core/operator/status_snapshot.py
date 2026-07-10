"""Secret-free professional-operator status projection.

The projection is deliberately pure: it receives already validated durable
models and never opens a database, calls a broker, or invents market values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    OperatorCycleClaim,
    OperatorSafetyState,
    PaperExecutionSession,
    PaperOrderDispatch,
    PendingLiquidationCheckpoint,
    StateStoreProvenance,
    StrategyOperatorState,
)
from quantpilot.packages.core.schemas import HarnessModel


KST = ZoneInfo("Asia/Seoul")
DisplayStatus = Literal["safe", "attention", "critical", "unavailable"]
_PUBLIC_ERROR_CODES = frozenset(
    {
        "acceptance_persistence_failed",
        "broker_acceptance_mismatch",
        "broker_amount_negative",
        "broker_business_date_unverified",
        "broker_business_rejected",
        "broker_exception_after_claim",
        "broker_fill_amount_inconsistent",
        "broker_fill_evidence_regressed",
        "broker_history_window_manual_resolution_required",
        "broker_match_ambiguous",
        "broker_order_branch_mismatch",
        "broker_quantity_inconsistent",
        "broker_quantity_negative",
        "broker_response_ambiguous",
        "broker_state_inconsistent",
        "broker_submission_outcome_unknown",
        "durable_dispatch_missing_before_claim",
        "durable_dispatch_unclaimed_before_post",
        "local_configuration_error",
        "paper_session_closed_after_claim",
        "paper_session_closed_before_dispatch",
        "prebroker_submission_failed",
        "prepared_without_submission_attempt",
        "process_interrupted",
        "risk_check_expired",
        "submission_evidence_expired",
    }
)


class EvidenceFreshness(HarnessModel):
    status: Literal["fresh", "stale", "unavailable"]
    latest_evidence_at: datetime | None = None
    stale_after_seconds: int = Field(ge=1)
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("latest_evidence_at")
    @classmethod
    def timestamp_must_be_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _optional_aware(value, "latest evidence")


class SafetyPolicyStatus(HarnessModel):
    policy_id: str
    status: DisplayStatus
    autopilot_paused: bool
    broker_healthy: bool
    updated_at: datetime
    stale: bool
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("updated_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value, "safety-state update")


class PaperSessionStatus(HarnessModel):
    status: Literal["active", "closed", "abandoned", "none"]
    started_at: datetime | None = None
    lease_expires_at: datetime | None = None
    updated_at: datetime | None = None
    lease_valid: bool = False

    @field_validator("started_at", "lease_expires_at", "updated_at")
    @classmethod
    def timestamps_must_be_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _optional_aware(value, "paper-session")


class ProfessionalSafetyStatus(HarnessModel):
    status: DisplayStatus
    policies: list[SafetyPolicyStatus] = Field(default_factory=list)
    latest_session: PaperSessionStatus
    reason_codes: list[str] = Field(default_factory=list)


class PositionRiskStatus(HarnessModel):
    policy_id: str
    policy_version: int
    strategy_id: str
    strategy_version: str
    symbol: str
    quantity: float
    average_entry_price: float
    atr14: float
    active_stop: float
    attribution_status: Literal["active", "conflicted"]
    reconciled_at: datetime
    status: DisplayStatus
    stale: bool
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("reconciled_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value, "position reconciliation")


class StrategyHealthStatusView(HarnessModel):
    policy_id: str
    strategy_id: str
    strategy_version: str
    health_status: Literal[
        "active",
        "review_unavailable",
        "paused_reapproval",
        "disabled",
    ]
    retirement_phase: Literal[
        "none",
        "risk_first",
        "remaining",
        "awaiting_reconciliation",
        "complete",
    ]
    pending_order_count: int = Field(ge=0)
    last_risk_evaluated_at: datetime | None = None
    updated_at: datetime
    status: DisplayStatus
    stale: bool
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("last_risk_evaluated_at", "updated_at")
    @classmethod
    def timestamps_must_be_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _optional_aware(value, "strategy health")


class RebalanceStatusView(HarnessModel):
    policy_id: str
    strategy_id: str
    strategy_version: str
    current_week: str
    last_rebalance_session: str | None = None
    claim_status: Literal[
        "completed",
        "in_progress",
        "expired",
        "evidence_mismatch",
        "not_recorded",
    ]
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    status: DisplayStatus
    reason_codes: list[str] = Field(default_factory=list)

    @field_validator("claimed_at", "completed_at")
    @classmethod
    def timestamps_must_be_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _optional_aware(value, "rebalance claim")


class ReconciliationDispatchStatus(HarnessModel):
    order_plan_id: str
    policy_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    side: Literal["buy", "sell"]
    purpose: Literal["rebalance", "protective_exit", "strategy_retirement"]
    status: str
    reconciliation_status: Literal["pending", "reconciled", "blocked"]
    quantity: float
    cumulative_filled_quantity: float
    remaining_quantity: float
    updated_at: datetime
    last_error_code: str | None = None

    @field_validator("updated_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value, "dispatch update")


class PendingLiquidationStatusView(HarnessModel):
    order_plan_id: str
    policy_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    purpose: Literal["protective_exit", "strategy_retirement"]
    status: str
    quantity_requested: float
    cumulative_filled_quantity: float
    remaining_quantity: float
    updated_at: datetime
    last_error_code: str | None = None

    @field_validator("updated_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value, "pending-liquidation update")


class ProfessionalReconciliationStatus(HarnessModel):
    status: DisplayStatus
    unresolved_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    outcome_unknown_count: int = Field(ge=0)
    dispatches: list[ReconciliationDispatchStatus] = Field(default_factory=list)
    pending_liquidations: list[PendingLiquidationStatusView] = Field(
        default_factory=list
    )
    reason_codes: list[str] = Field(default_factory=list)


class ProfessionalOperatorStatusSnapshot(HarnessModel):
    available: bool
    overall_status: DisplayStatus
    reason_code: str | None = None
    source: Literal["paper_state_sqlite"] = "paper_state_sqlite"
    observed_at: datetime
    live_trading_enabled: Literal[False] = False
    schema_version: int | None = Field(default=None, ge=1)
    freshness: EvidenceFreshness
    safety: ProfessionalSafetyStatus
    positions: list[PositionRiskStatus] = Field(default_factory=list)
    strategy_health: list[StrategyHealthStatusView] = Field(default_factory=list)
    rebalance: list[RebalanceStatusView] = Field(default_factory=list)
    reconciliation: ProfessionalReconciliationStatus

    @field_validator("observed_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value, "status observation")


def unavailable_professional_operator_status(
    *,
    observed_at: datetime,
    reason_code: str,
    stale_after_seconds: int = 180,
) -> ProfessionalOperatorStatusSnapshot:
    """Return an explicit unavailable result without leaking diagnostic inputs."""

    _validate_inputs(observed_at, stale_after_seconds)
    normalized_reason = reason_code.strip().lower()
    if not normalized_reason or not all(
        character.isalnum() or character == "_"
        for character in normalized_reason
    ):
        raise ValueError("professional status reason code must be snake-case text")
    return ProfessionalOperatorStatusSnapshot(
        available=False,
        overall_status="unavailable",
        reason_code=normalized_reason,
        observed_at=observed_at,
        freshness=EvidenceFreshness(
            status="unavailable",
            stale_after_seconds=stale_after_seconds,
            reason_codes=[normalized_reason],
        ),
        safety=ProfessionalSafetyStatus(
            status="unavailable",
            latest_session=PaperSessionStatus(status="none"),
            reason_codes=[normalized_reason],
        ),
        reconciliation=ProfessionalReconciliationStatus(
            status="unavailable",
            unresolved_count=0,
            blocked_count=0,
            outcome_unknown_count=0,
            reason_codes=[normalized_reason],
        ),
    )


def build_professional_operator_status(
    *,
    observed_at: datetime,
    provenance: StateStoreProvenance,
    safety_states: list[OperatorSafetyState],
    positions: list[ManagedPositionState],
    strategy_states: list[StrategyOperatorState],
    cycle_claims: list[OperatorCycleClaim],
    sessions: list[PaperExecutionSession],
    dispatches: list[PaperOrderDispatch],
    pending_liquidations: list[PendingLiquidationCheckpoint],
    stale_after_seconds: int = 180,
) -> ProfessionalOperatorStatusSnapshot:
    """Build a deterministic allowlisted status view from durable evidence."""

    _validate_inputs(observed_at, stale_after_seconds)
    if (
        provenance.data_mode != "paper_trading"
        or provenance.broker_environment != "kis_paper"
    ):
        raise ValueError("professional status requires KIS paper provenance")

    safety = _build_safety(
        safety_states,
        sessions,
        observed_at=observed_at,
        stale_after_seconds=stale_after_seconds,
    )
    position_views = [
        _position_view(
            item,
            observed_at=observed_at,
            stale_after_seconds=stale_after_seconds,
        )
        for item in sorted(
            positions,
            key=lambda item: (
                item.policy_id,
                item.strategy_id,
                item.strategy_version,
                item.symbol,
            ),
        )
    ]
    strategy_views = [
        _strategy_view(
            item,
            observed_at=observed_at,
            stale_after_seconds=stale_after_seconds,
        )
        for item in sorted(
            strategy_states,
            key=lambda item: (
                item.policy_id,
                item.strategy_id,
                item.strategy_version,
            ),
        )
    ]
    rebalance = _rebalance_views(
        strategy_states,
        cycle_claims,
        observed_at=observed_at,
    )
    reconciliation = _reconciliation_view(dispatches, pending_liquidations)

    evidence_times = [
        *[item.updated_at for item in safety_states],
        *[item.updated_at for item in positions],
        *[item.updated_at for item in strategy_states],
        *[item.updated_at for item in sessions],
        *[item.updated_at for item in dispatches],
        *[item.updated_at for item in pending_liquidations],
        *[item.claimed_at for item in cycle_claims],
        *[
            item.completed_at
            for item in cycle_claims
            if item.completed_at is not None
        ],
    ]
    freshness = _freshness(
        evidence_times,
        observed_at=observed_at,
        stale_after_seconds=stale_after_seconds,
    )

    section_statuses: list[DisplayStatus] = [
        safety.status,
        reconciliation.status,
        *[item.status for item in position_views],
        *[item.status for item in strategy_views],
        *[item.status for item in rebalance],
    ]
    if not strategy_views:
        section_statuses.append("attention")
    if freshness.status != "fresh":
        section_statuses.append("attention")
    overall = _worst_status(section_statuses)

    return ProfessionalOperatorStatusSnapshot(
        available=True,
        overall_status=overall,
        reason_code=None,
        observed_at=observed_at,
        schema_version=provenance.schema_version,
        freshness=freshness,
        safety=safety,
        positions=position_views,
        strategy_health=strategy_views,
        rebalance=rebalance,
        reconciliation=reconciliation,
    )


def _build_safety(
    safety_states: list[OperatorSafetyState],
    sessions: list[PaperExecutionSession],
    *,
    observed_at: datetime,
    stale_after_seconds: int,
) -> ProfessionalSafetyStatus:
    policies: list[SafetyPolicyStatus] = []
    for item in sorted(safety_states, key=lambda state: state.policy_id):
        stale, future = _stale_or_future(
            item.updated_at,
            observed_at=observed_at,
            stale_after_seconds=stale_after_seconds,
        )
        reasons: list[str] = []
        if item.autopilot_paused:
            reasons.append("autopilot_paused")
        if not item.broker_healthy:
            reasons.append("broker_unhealthy")
        if item.last_blocked_reason is not None:
            reasons.append("operator_blocked_reason_present")
        if stale:
            reasons.append("safety_state_stale")
        if future:
            reasons.append("safety_state_from_future")
        status: DisplayStatus = "safe"
        if not item.broker_healthy or future:
            status = "critical"
        elif item.autopilot_paused or stale or item.last_blocked_reason:
            status = "attention"
        policies.append(
            SafetyPolicyStatus(
                policy_id=item.policy_id,
                status=status,
                autopilot_paused=item.autopilot_paused,
                broker_healthy=item.broker_healthy,
                updated_at=item.updated_at,
                stale=stale,
                reason_codes=sorted(set(reasons)),
            )
        )

    latest = max(sessions, key=lambda item: item.updated_at) if sessions else None
    latest_session = (
        PaperSessionStatus(status="none")
        if latest is None
        else PaperSessionStatus(
            status=latest.status,
            started_at=latest.started_at,
            lease_expires_at=latest.lease_expires_at,
            updated_at=latest.updated_at,
            lease_valid=(
                latest.status == "active"
                and latest.started_at <= observed_at < latest.lease_expires_at
            ),
        )
    )
    reasons = [] if policies else ["operator_safety_state_missing"]
    statuses: list[DisplayStatus] = [item.status for item in policies]
    if not policies:
        statuses.append("attention")
    if latest is None:
        statuses.append("attention")
        reasons.append("paper_session_evidence_missing")
    if latest is not None and latest.status == "active" and not latest_session.lease_valid:
        statuses.append("critical")
        reasons.append("paper_session_lease_expired")
    return ProfessionalSafetyStatus(
        status=_worst_status(statuses),
        policies=policies,
        latest_session=latest_session,
        reason_codes=sorted(set(reasons)),
    )


def _position_view(
    item: ManagedPositionState,
    *,
    observed_at: datetime,
    stale_after_seconds: int,
) -> PositionRiskStatus:
    stale, future = _stale_or_future(
        item.reconciled_at,
        observed_at=observed_at,
        stale_after_seconds=stale_after_seconds,
    )
    reasons: list[str] = []
    if stale:
        reasons.append("position_reconciliation_stale")
    if future:
        reasons.append("position_reconciliation_from_future")
    if item.attribution_status == "conflicted":
        reasons.append("position_attribution_conflicted")
    status: DisplayStatus = "safe"
    if item.attribution_status == "conflicted" or future:
        status = "critical"
    elif stale:
        status = "attention"
    return PositionRiskStatus(
        policy_id=item.policy_id,
        policy_version=item.policy_version,
        strategy_id=item.strategy_id,
        strategy_version=item.strategy_version,
        symbol=item.symbol,
        quantity=item.quantity,
        average_entry_price=item.average_entry_price,
        atr14=item.atr14,
        active_stop=item.active_stop,
        attribution_status=item.attribution_status,
        reconciled_at=item.reconciled_at,
        status=status,
        stale=stale,
        reason_codes=sorted(set(reasons)),
    )


def _strategy_view(
    item: StrategyOperatorState,
    *,
    observed_at: datetime,
    stale_after_seconds: int,
) -> StrategyHealthStatusView:
    stale, future = _stale_or_future(
        item.updated_at,
        observed_at=observed_at,
        stale_after_seconds=stale_after_seconds,
    )
    reasons: list[str] = []
    if item.reason_codes:
        reasons.append("durable_strategy_reason_present")
    if item.health_status != "active":
        reasons.append(f"strategy_{item.health_status}")
    if item.retirement_phase == "awaiting_reconciliation":
        reasons.append("strategy_retirement_awaiting_reconciliation")
    if stale:
        reasons.append("strategy_state_stale")
    if future:
        reasons.append("strategy_state_from_future")
    status: DisplayStatus = "safe"
    if future or item.retirement_phase == "awaiting_reconciliation":
        status = "critical"
    elif item.health_status != "active" or stale:
        status = "attention"
    return StrategyHealthStatusView(
        policy_id=item.policy_id,
        strategy_id=item.strategy_id,
        strategy_version=item.strategy_version,
        health_status=item.health_status,
        retirement_phase=item.retirement_phase,
        pending_order_count=len(item.pending_order_plan_ids),
        last_risk_evaluated_at=item.last_risk_evaluated_at,
        updated_at=item.updated_at,
        status=status,
        stale=stale,
        reason_codes=sorted(set(reasons)),
    )


def _rebalance_views(
    strategy_states: list[StrategyOperatorState],
    cycle_claims: list[OperatorCycleClaim],
    *,
    observed_at: datetime,
) -> list[RebalanceStatusView]:
    iso = observed_at.astimezone(KST).isocalendar()
    current_week = f"{iso.year:04d}-W{iso.week:02d}"
    state_by_key = {
        (item.policy_id, item.strategy_id, item.strategy_version): item
        for item in strategy_states
    }
    current_claims = [
        item
        for item in cycle_claims
        if item.cycle_kind == "weekly_rebalance" and item.bucket == current_week
    ]
    claim_by_key = {
        (item.policy_id, item.strategy_id, item.strategy_version): item
        for item in current_claims
    }
    result: list[RebalanceStatusView] = []
    for policy_id, strategy_id, strategy_version in sorted(
        set(state_by_key) | set(claim_by_key)
    ):
        key = (policy_id, strategy_id, strategy_version)
        state = state_by_key.get(key)
        claim = claim_by_key.get(key)
        state_completed = (
            state is not None and state.last_rebalance_session == current_week
        )
        reasons: list[str] = []
        if state is None:
            claim_status = "evidence_mismatch"
            status: DisplayStatus = "critical"
            reasons.append("strategy_state_missing_for_rebalance_claim")
        elif claim is not None and claim.completed_at is not None and state_completed:
            claim_status = "completed"
            status = "safe"
        elif claim is not None and claim.completed_at is None:
            if claim.lease_expires_at is not None and claim.lease_expires_at <= observed_at:
                claim_status = "expired"
                status = "critical"
                reasons.append("weekly_rebalance_claim_expired")
            else:
                claim_status = "in_progress"
                status = "attention"
                reasons.append("weekly_rebalance_in_progress")
        elif claim is not None or state_completed:
            claim_status = "evidence_mismatch"
            status = "critical"
            reasons.append("weekly_rebalance_evidence_mismatch")
        else:
            claim_status = "not_recorded"
            status = "attention"
            reasons.append("weekly_rebalance_not_recorded")
        result.append(
            RebalanceStatusView(
                policy_id=policy_id,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                current_week=current_week,
                last_rebalance_session=(
                    None if state is None else state.last_rebalance_session
                ),
                claim_status=claim_status,
                claimed_at=None if claim is None else claim.claimed_at,
                completed_at=None if claim is None else claim.completed_at,
                status=status,
                reason_codes=reasons,
            )
        )
    return result


def _reconciliation_view(
    dispatches: list[PaperOrderDispatch],
    pending_liquidations: list[PendingLiquidationCheckpoint],
) -> ProfessionalReconciliationStatus:
    active_dispatch_statuses = {
        "prepared",
        "dispatch_claimed",
        "outcome_unknown",
        "accepted",
        "partially_filled",
    }
    unresolved_dispatches = [
        item
        for item in dispatches
        if item.status in active_dispatch_statuses
        or item.reconciliation_status != "reconciled"
    ]
    unresolved_pending = [
        item for item in pending_liquidations if item.status != "reconciled"
    ]
    dispatch_views = [
        ReconciliationDispatchStatus(
            order_plan_id=item.order_plan_id,
            policy_id=item.policy_id,
            strategy_id=item.strategy_id,
            strategy_version=item.strategy_version,
            symbol=item.symbol,
            side=item.side,
            purpose=item.purpose,
            status=item.status,
            reconciliation_status=item.reconciliation_status,
            quantity=item.quantity,
            cumulative_filled_quantity=item.cumulative_filled_quantity,
            remaining_quantity=max(
                0.0,
                item.quantity - item.cumulative_filled_quantity,
            ),
            updated_at=item.updated_at,
            last_error_code=_public_error_code(
                item.last_error_code,
                fallback="paper_dispatch_error_redacted",
            ),
        )
        for item in sorted(unresolved_dispatches, key=lambda value: value.order_plan_id)
    ]
    pending_views = [
        PendingLiquidationStatusView(
            order_plan_id=item.order_plan_id,
            policy_id=item.policy_id,
            strategy_id=item.strategy_id,
            strategy_version=item.strategy_version,
            symbol=item.symbol,
            purpose=item.purpose,
            status=item.status,
            quantity_requested=item.quantity_requested,
            cumulative_filled_quantity=item.cumulative_filled_quantity,
            remaining_quantity=max(
                0.0,
                item.quantity_requested - item.cumulative_filled_quantity,
            ),
            updated_at=item.updated_at,
            last_error_code=_public_error_code(
                item.last_error_code,
                fallback="pending_liquidation_error_redacted",
            ),
        )
        for item in sorted(unresolved_pending, key=lambda value: value.order_plan_id)
    ]
    blocked_count = sum(
        item.reconciliation_status == "blocked" for item in unresolved_dispatches
    )
    outcome_unknown_count = sum(
        item.status == "outcome_unknown" for item in unresolved_dispatches
    ) + sum(item.status == "outcome_unknown" for item in unresolved_pending)
    terminal_pending_count = sum(
        item.status not in active_dispatch_statuses
        and item.reconciliation_status != "reconciled"
        for item in unresolved_dispatches
    )
    unresolved_count = len(dispatch_views) + len(pending_views)
    reasons: list[str] = []
    status: DisplayStatus = "safe"
    if blocked_count:
        status = "critical"
        reasons.append("paper_reconciliation_blocked")
    if outcome_unknown_count:
        status = "critical"
        reasons.append("paper_submission_outcome_unknown")
    if terminal_pending_count:
        status = "critical"
        reasons.append("paper_terminal_reconciliation_pending")
    if unresolved_count and status == "safe":
        status = "attention"
        reasons.append("paper_reconciliation_pending")
    return ProfessionalReconciliationStatus(
        status=status,
        unresolved_count=unresolved_count,
        blocked_count=blocked_count,
        outcome_unknown_count=outcome_unknown_count,
        dispatches=dispatch_views,
        pending_liquidations=pending_views,
        reason_codes=sorted(set(reasons)),
    )


def _freshness(
    evidence_times: list[datetime],
    *,
    observed_at: datetime,
    stale_after_seconds: int,
) -> EvidenceFreshness:
    if not evidence_times:
        return EvidenceFreshness(
            status="unavailable",
            stale_after_seconds=stale_after_seconds,
            reason_codes=["durable_operator_evidence_missing"],
        )
    latest = max(evidence_times)
    stale, future = _stale_or_future(
        latest,
        observed_at=observed_at,
        stale_after_seconds=stale_after_seconds,
    )
    reasons: list[str] = []
    status: Literal["fresh", "stale", "unavailable"] = "fresh"
    if future:
        status = "stale"
        reasons.append("durable_evidence_from_future")
    elif stale:
        status = "stale"
        reasons.append("durable_evidence_stale")
    return EvidenceFreshness(
        status=status,
        latest_evidence_at=latest,
        stale_after_seconds=stale_after_seconds,
        reason_codes=reasons,
    )


def _stale_or_future(
    value: datetime,
    *,
    observed_at: datetime,
    stale_after_seconds: int,
) -> tuple[bool, bool]:
    _aware(value, "durable evidence")
    age = (observed_at - value).total_seconds()
    return age > stale_after_seconds, age < 0


def _worst_status(statuses: list[DisplayStatus]) -> DisplayStatus:
    if not statuses:
        return "safe"
    rank = {"safe": 0, "attention": 1, "critical": 2, "unavailable": 3}
    return max(statuses, key=lambda item: rank[item])


def _validate_inputs(observed_at: datetime, stale_after_seconds: int) -> None:
    _aware(observed_at, "status observation")
    if isinstance(stale_after_seconds, bool) or stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be a positive integer")


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} timestamp must include a UTC offset")
    return value


def _optional_aware(value: datetime | None, field_name: str) -> datetime | None:
    return None if value is None else _aware(value, field_name)


def _public_error_code(value: str | None, *, fallback: str) -> str | None:
    if value is None:
        return None
    return value if value in _PUBLIC_ERROR_CODES else fallback
