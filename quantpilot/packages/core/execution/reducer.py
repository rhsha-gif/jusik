"""Deterministic reducer for canonical KIS paper execution-event streams."""

from __future__ import annotations

import re
from typing import Any, Iterable, Literal, Mapping, cast

from pydantic import Field, field_validator, model_validator

from quantpilot.packages.core.execution.events import (
    PAPER_MUTATION_ORIGIN_SOURCES,
    PaperEventSchemaUnsupported,
    PaperEventStreamConflict,
    PaperEventStreamCorruption,
    PaperExecutionAfter,
    PaperExecutionAggregateType,
    PaperExecutionEvent,
    PaperExecutionEventProvenance,
    canonical_json_bytes,
    decode_paper_execution_event,
    event_fingerprint,
    identity_keys_for_dispatch,
    payload_after,
)
from quantpilot.packages.core.execution.transitions import (
    IMPORT_EVENT_TYPES,
    PaperDispatchSpecialEventType,
    classify_dispatch_event_type,
    validate_cancel_event_transition,
    validate_reservation_event_transition,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperOrderDispatch,
    PaperRiskReservation,
)
from quantpilot.packages.core.schemas import HarnessModel


class PaperExecutionProjection(HarnessModel):
    """One independent aggregate projection plus replay conflict evidence."""

    aggregate_type: PaperExecutionAggregateType
    aggregate_id: str
    store_id: str
    account_scope_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    data_mode: Literal["paper_trading"] = "paper_trading"
    broker_environment: Literal["kis_paper"] = "kis_paper"
    after: PaperExecutionAfter
    aggregate_version: int = Field(ge=1)
    source_revision: int = Field(ge=0)
    last_event_id: str
    event_fingerprints: dict[str, str]
    version_event_ids: dict[int, str]
    identity_scope_hashes: tuple[str, ...] = ()

    @field_validator("identity_scope_hashes")
    @classmethod
    def scopes_must_be_sorted_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        expected = tuple(sorted(set(value)))
        if value != expected:
            raise ValueError("projection identity scopes must be sorted and unique")
        if any(re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None for item in value):
            raise ValueError("projection identity scope is not a canonical hash")
        return value

    @field_validator("aggregate_id", "store_id", "last_event_id")
    @classmethod
    def projection_ids_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("projection identifiers must not be blank")
        return normalized

    @model_validator(mode="after")
    def projection_must_bind_after_state(self) -> "PaperExecutionProjection":
        after = self.after
        if isinstance(after, PaperOrderDispatch):
            expected_type = "order_dispatch"
            expected_id = after.order_plan_id
        elif isinstance(after, PaperRiskReservation):
            expected_type = "risk_reservation"
            expected_id = after.reservation_id
        else:
            expected_type = "cancel_request"
            expected_id = after.cancel_id
        if self.aggregate_type != expected_type or self.aggregate_id != expected_id:
            raise ValueError("projection aggregate does not match after-state")
        if (
            self.store_id != after.store_id
            or self.account_scope_fingerprint != after.account_scope_fingerprint
            or self.data_mode != after.data_mode
            or self.broker_environment != after.broker_environment
            or self.source_revision != after.revision
        ):
            raise ValueError("projection provenance or revision does not match after-state")
        expected_versions = set(range(1, self.aggregate_version + 1))
        if set(self.version_event_ids) != expected_versions:
            raise ValueError("projection aggregate versions are not contiguous")
        if self.version_event_ids[self.aggregate_version] != self.last_event_id:
            raise ValueError("projection last event does not match aggregate version")
        if set(self.version_event_ids.values()) != set(self.event_fingerprints):
            raise ValueError("projection event evidence is incomplete")
        if any(
            not event_id.strip()
            or re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is None
            for event_id, fingerprint in self.event_fingerprints.items()
        ):
            raise ValueError("projection event fingerprint is invalid")
        return self


class PaperCorrelatedExecutionProjection(HarnessModel):
    order_plan_id: str
    order_dispatch: PaperExecutionProjection | None = None
    risk_reservation: PaperExecutionProjection | None = None
    cancel_requests: list[PaperExecutionProjection] = Field(default_factory=list)


def projection_canonical_bytes(projection: PaperExecutionProjection) -> bytes:
    return canonical_json_bytes(projection)


def _deep_projection(
    projection: PaperExecutionProjection,
) -> PaperExecutionProjection:
    return PaperExecutionProjection.model_validate(projection.model_dump())


def _event_provenance(event: PaperExecutionEvent) -> PaperExecutionEventProvenance:
    return PaperExecutionEventProvenance(
        store_id=event.store_id,
        account_scope_fingerprint=event.account_scope_fingerprint,
        data_mode=event.data_mode,
        broker_environment=event.broker_environment,
    )


def _projection_provenance(
    projection: PaperExecutionProjection,
) -> PaperExecutionEventProvenance:
    return PaperExecutionEventProvenance(
        store_id=projection.store_id,
        account_scope_fingerprint=projection.account_scope_fingerprint,
        data_mode=projection.data_mode,
        broker_environment=projection.broker_environment,
    )


def _validate_expected_provenance(
    event: PaperExecutionEvent,
    expected: PaperExecutionEventProvenance | None,
) -> None:
    if expected is not None and _event_provenance(event) != expected:
        raise PaperEventStreamCorruption(
            "canonical event does not match expected store provenance"
        )


def _validate_causation(
    projection: PaperExecutionProjection | None,
    event: PaperExecutionEvent,
) -> None:
    if event.event_type in IMPORT_EVENT_TYPES:
        if event.causation_id is not None:
            raise PaperEventStreamCorruption("import event causation must be null")
        return
    if projection is None:
        if event.event_type in {"RiskReserved", "CancelPrepared"}:
            if event.causation_id is not None:
                raise PaperEventStreamCorruption("root create causation must be null")
            return
        if event.event_type == "OrderPrepared":
            if event.causation_id is None:
                raise PaperEventStreamCorruption(
                    "OrderPrepared must cite its paired RiskReserved event"
                )
            return
        raise PaperEventStreamConflict("update event cannot seed an empty stream")
    if event.event_type in {
        "RiskReservationFenceRebound",
        "RiskReservationReleased",
    }:
        if event.causation_id is None or event.causation_id == projection.last_event_id:
            raise PaperEventStreamCorruption(
                "paired reservation event must cite a different aggregate event"
            )
        return
    if event.causation_id != projection.last_event_id:
        raise PaperEventStreamCorruption(
            "event causation must cite the immediately preceding stream event"
        )


_BROKER_UNKNOWN_CODES = {
    "broker_response_ambiguous",
    "broker_exception_after_claim",
    "broker_acceptance_mismatch",
    "broker_business_date_unverified",
    "acceptance_persistence_failed",
}
_LOCAL_GUARD_REJECTION_CODES = {
    "paper_kill_engaged_after_claim",
    "paper_session_closed_after_claim",
    "local_configuration_error",
}
_LOCAL_PRE_DISPATCH_STATUS_BY_CODE = {
    "paper_kill_engaged": "expired_pre_dispatch",
    "risk_check_expired": "expired_pre_dispatch",
    "submission_evidence_expired": "expired_pre_dispatch",
    "paper_session_closed_before_dispatch": "failed_pre_dispatch",
}
_BROKER_RECONCILIATION_BLOCK_CODES = {
    "broker_amount_negative",
    "broker_fill_amount_inconsistent",
    "broker_fill_evidence_regressed",
    "broker_history_window_manual_resolution_required",
    "broker_match_ambiguous",
    "broker_order_branch_mismatch",
    "broker_quantity_inconsistent",
    "broker_quantity_negative",
    "broker_state_inconsistent",
}


def _require_exact_dispatch_copy(
    before: PaperOrderDispatch,
    after: PaperOrderDispatch,
    *,
    updates: Mapping[str, Any],
    error: str,
) -> None:
    expected = PaperOrderDispatch.model_validate(
        before.model_copy(update=dict(updates)).model_dump()
    )
    if after != expected:
        raise PaperEventStreamCorruption(error)


def _validate_dispatch_source(
    before: PaperOrderDispatch | None,
    after: PaperOrderDispatch,
    event: PaperExecutionEvent,
) -> None:
    if event.event_type == "LegacyOrderDispatchImported":
        if event.source != "schema_migration":
            raise PaperEventStreamCorruption("dispatch import source is invalid")
        return
    if event.event_type == "OrderPrepared":
        if event.source != "local_prepare":
            raise PaperEventStreamCorruption("OrderPrepared source is invalid")
        return
    if event.event_type == "DispatchClaimed":
        if event.source != "local_dispatch_claim":
            raise PaperEventStreamCorruption("DispatchClaimed source is invalid")
        return
    if event.event_type == "DispatchFenceRebound":
        if event.source != "local_session_takeover":
            raise PaperEventStreamCorruption("DispatchFenceRebound source is invalid")
        return
    if before is None:
        raise PaperEventStreamConflict("dispatch update lacks prior state")
    if event.source == "process_recovery":
        if event.event_type != "OutcomeUnknown" or before.status != "dispatch_claimed":
            raise PaperEventStreamCorruption("process-recovery delta is invalid")
        _require_exact_dispatch_copy(
            before,
            after,
            updates={
                "status": "outcome_unknown",
                "last_error_code": "process_interrupted",
                "updated_at": after.updated_at,
                "revision": before.revision + 1,
            },
            error="process-recovery delta is invalid",
        )
        return
    if event.source == PAPER_MUTATION_ORIGIN_SOURCES["broker_post_result"]:
        if before.status != "dispatch_claimed" or after.status not in {
            "accepted",
            "rejected",
            "outcome_unknown",
        }:
            raise PaperEventStreamCorruption("broker POST result delta is invalid")
        if after.status == "accepted":
            _require_exact_dispatch_copy(
                before,
                after,
                updates={
                    "status": "accepted",
                    "broker_business_date": after.broker_business_date,
                    "broker_order_reference": after.broker_order_reference,
                    "broker_forwarding_order_org_number": (
                        after.broker_forwarding_order_org_number
                    ),
                    "broker_order_time": after.broker_order_time,
                    "last_error_code": None,
                    "updated_at": after.updated_at,
                    "revision": before.revision + 1,
                },
                error="broker acceptance delta is invalid",
            )
        elif after.status == "rejected":
            if after.last_error_code != "broker_business_rejected":
                raise PaperEventStreamCorruption("broker rejection code is invalid")
            _require_exact_dispatch_copy(
                before,
                after,
                updates={
                    "status": "rejected",
                    "reconciliation_status": "reconciled",
                    "last_error_code": "broker_business_rejected",
                    "updated_at": after.updated_at,
                    "reconciled_at": after.updated_at,
                    "revision": before.revision + 1,
                },
                error="broker rejection delta is invalid",
            )
        else:
            if after.last_error_code not in _BROKER_UNKNOWN_CODES:
                raise PaperEventStreamCorruption("ambiguous broker result code is invalid")
            _require_exact_dispatch_copy(
                before,
                after,
                updates={
                    "status": "outcome_unknown",
                    "last_error_code": after.last_error_code,
                    "updated_at": after.updated_at,
                    "revision": before.revision + 1,
                },
                error="ambiguous broker result delta is invalid",
            )
        return
    if event.source == PAPER_MUTATION_ORIGIN_SOURCES["local_submission_guard"]:
        if before.status == "prepared":
            expected_status = _LOCAL_PRE_DISPATCH_STATUS_BY_CODE.get(
                after.last_error_code or ""
            )
            if expected_status is None or after.status != expected_status:
                raise PaperEventStreamCorruption("local submission guard delta is invalid")
            _require_exact_dispatch_copy(
                before,
                after,
                updates={
                    "status": expected_status,
                    "reconciliation_status": "reconciled",
                    "last_error_code": after.last_error_code,
                    "updated_at": after.updated_at,
                    "reconciled_at": after.updated_at,
                    "revision": before.revision + 1,
                },
                error="local submission guard delta is invalid",
            )
            return
        if (
            before.status != "dispatch_claimed"
            or after.status != "rejected"
            or after.last_error_code not in _LOCAL_GUARD_REJECTION_CODES
        ):
            raise PaperEventStreamCorruption("local submission guard delta is invalid")
        _require_exact_dispatch_copy(
            before,
            after,
            updates={
                "status": "rejected",
                "reconciliation_status": "reconciled",
                "last_error_code": after.last_error_code,
                "updated_at": after.updated_at,
                "reconciled_at": after.updated_at,
                "revision": before.revision + 1,
            },
            error="local submission guard delta is invalid",
        )
        return
    if event.source == PAPER_MUTATION_ORIGIN_SOURCES["broker_reconciliation"]:
        if (
            before.attempt_count != 1
            or after.attempt_count != 1
            or before.status
            not in {"dispatch_claimed", "outcome_unknown", "accepted", "partially_filled"}
        ):
            raise PaperEventStreamCorruption(
                "broker reconciliation requires a claimed external attempt"
            )
        if event.event_type == "ReconciliationBlocked":
            if after.last_error_code not in _BROKER_RECONCILIATION_BLOCK_CODES:
                raise PaperEventStreamCorruption(
                    "broker reconciliation block code is invalid"
                )
            expected_status = (
                "outcome_unknown" if before.status == "dispatch_claimed" else before.status
            )
            _require_exact_dispatch_copy(
                before,
                after,
                updates={
                    "status": expected_status,
                    "reconciliation_status": "blocked",
                    "last_error_code": after.last_error_code,
                    "updated_at": after.updated_at,
                    "revision": before.revision + 1,
                },
                error="broker reconciliation blocked delta is invalid",
            )
            return
        if after.status not in {
            "accepted",
            "partially_filled",
            "filled",
            "rejected",
            "cancelled",
        }:
            raise PaperEventStreamCorruption(
                "broker reconciliation produced an unreachable lifecycle delta"
            )
        terminal = after.status in {"filled", "rejected", "cancelled"}
        _require_exact_dispatch_copy(
            before,
            after,
            updates={
                "status": after.status,
                "reconciliation_status": "reconciled" if terminal else "pending",
                "broker_business_date": after.broker_business_date,
                "broker_order_reference": after.broker_order_reference,
                "broker_order_branch_number": after.broker_order_branch_number,
                "broker_order_time": after.broker_order_time,
                "cumulative_filled_quantity": after.cumulative_filled_quantity,
                "fill_evidence": after.fill_evidence,
                "last_error_code": None,
                "updated_at": after.updated_at,
                "reconciled_at": after.updated_at if terminal else None,
                "revision": before.revision + 1,
            },
            error="broker reconciliation delta is invalid",
        )
        return
    raise PaperEventStreamCorruption("dispatch source is not allowed for this mutation")


def _validate_reservation_source(event: PaperExecutionEvent) -> None:
    expected_sources: dict[str, set[str]] = {
        "LegacyRiskReservationImported": {"schema_migration"},
        "RiskReserved": {"local_prepare"},
        "RiskReservationFenceRebound": {"local_session_takeover"},
        "RiskReservationReleased": {
            "broker_acceptance",
            "broker_reconciliation",
            "local_submission_result",
        },
    }
    if event.source not in expected_sources[event.event_type]:
        raise PaperEventStreamCorruption("reservation source is invalid")


def _validate_cancel_source(event: PaperExecutionEvent) -> None:
    expected = "schema_migration" if event.event_type == "LegacyCancelRequestImported" else "kill_cancel"
    if event.source != expected:
        raise PaperEventStreamCorruption("cancel source is invalid")


def _expected_dispatch_event_type(
    before: PaperOrderDispatch | None,
    after: PaperOrderDispatch,
    *,
    legacy_snapshot: bool,
) -> str:
    if before is None:
        special: PaperDispatchSpecialEventType = (
            "LegacyOrderDispatchImported" if legacy_snapshot else "OrderPrepared"
        )
        return classify_dispatch_event_type(
            None,
            after,
            special_event_type=special,
        )
    legal: list[str] = []
    for special in ("DispatchFenceRebound", "DispatchClaimed", None):
        try:
            legal.append(
                classify_dispatch_event_type(
                    before,
                    after,
                    special_event_type=cast(
                        PaperDispatchSpecialEventType | None,
                        special,
                    ),
                )
            )
        except ValueError:
            continue
    if len(legal) != 1:
        raise ValueError("dispatch transition is illegal or ambiguous")
    return legal[0]


def _expected_reservation_event_type(
    before: PaperRiskReservation | None,
    after: PaperRiskReservation,
    *,
    legacy_snapshot: bool,
) -> str:
    candidates = (
        ["LegacyRiskReservationImported" if legacy_snapshot else "RiskReserved"]
        if before is None
        else ["RiskReservationFenceRebound", "RiskReservationReleased"]
    )
    legal: list[str] = []
    for candidate in candidates:
        try:
            validate_reservation_event_transition(before, after, candidate)
            legal.append(candidate)
        except ValueError:
            continue
    if len(legal) != 1:
        raise ValueError("reservation transition is illegal or ambiguous")
    return legal[0]


def _expected_cancel_event_type(
    before: PaperCancelRequest | None,
    after: PaperCancelRequest,
    *,
    legacy_snapshot: bool,
) -> str:
    candidates = (
        ["LegacyCancelRequestImported" if legacy_snapshot else "CancelPrepared"]
        if before is None
        else [
            "CancelClaimed",
            "CancelAccepted",
            "CancelOutcomeUnknown",
            "CancelRejected",
            "CancelReconciledCancelled",
            "CancelReconciledFilled",
        ]
    )
    legal: list[str] = []
    for candidate in candidates:
        try:
            validate_cancel_event_transition(before, after, candidate)
            legal.append(candidate)
        except ValueError:
            continue
    if len(legal) != 1:
        raise ValueError("cancel transition is illegal or ambiguous")
    return legal[0]


def _validate_transition_and_identity_keys(
    projection: PaperExecutionProjection | None,
    event: PaperExecutionEvent,
) -> None:
    before = None if projection is None else projection.after
    after = payload_after(event.payload)
    try:
        if isinstance(after, PaperOrderDispatch):
            if before is not None and not isinstance(before, PaperOrderDispatch):
                raise ValueError("dispatch stream changed aggregate model")
            dispatch_before = before if isinstance(before, PaperOrderDispatch) else None
            expected_type = _expected_dispatch_event_type(
                dispatch_before,
                after,
                legacy_snapshot=event.payload.legacy_snapshot is True,
            )
            if event.event_type != expected_type:
                raise PaperEventStreamCorruption(
                    "dispatch event type does not match transition"
                )
            expected_keys = identity_keys_for_dispatch(dispatch_before, after)
            if tuple(event.identity_keys) != expected_keys:
                raise PaperEventStreamCorruption(
                    "dispatch identity keys do not equal the fill delta"
                )
            _validate_dispatch_source(dispatch_before, after, event)
        elif isinstance(after, PaperRiskReservation):
            if before is not None and not isinstance(before, PaperRiskReservation):
                raise ValueError("reservation stream changed aggregate model")
            reservation_before = before if isinstance(before, PaperRiskReservation) else None
            expected_type = _expected_reservation_event_type(
                reservation_before,
                after,
                legacy_snapshot=event.payload.legacy_snapshot is True,
            )
            if event.event_type != expected_type:
                raise PaperEventStreamCorruption(
                    "reservation event type does not match transition"
                )
            _validate_reservation_source(event)
        else:
            if before is not None and not isinstance(before, PaperCancelRequest):
                raise ValueError("cancel stream changed aggregate model")
            cancel_before = before if isinstance(before, PaperCancelRequest) else None
            expected_type = _expected_cancel_event_type(
                cancel_before,
                after,
                legacy_snapshot=event.payload.legacy_snapshot is True,
            )
            if event.event_type != expected_type:
                raise PaperEventStreamCorruption(
                    "cancel event type does not match transition"
                )
            _validate_cancel_source(event)
    except PaperEventStreamCorruption:
        raise
    except ValueError as exc:
        raise PaperEventStreamConflict("canonical event transition is illegal") from exc


def reduce_paper_execution_event(
    projection: PaperExecutionProjection | None,
    raw_event: PaperExecutionEvent | Mapping[str, Any],
    *,
    expected_provenance: PaperExecutionEventProvenance | None = None,
) -> PaperExecutionProjection:
    """Apply one caller-ordered event without sorting or mutating inputs."""

    event = decode_paper_execution_event(raw_event)
    _validate_expected_provenance(event, expected_provenance)
    fingerprint = event_fingerprint(event)
    current = None if projection is None else _deep_projection(projection)

    if current is not None:
        if (
            current.aggregate_type != event.aggregate_type
            or current.aggregate_id != event.aggregate_id
            or _projection_provenance(current) != _event_provenance(event)
        ):
            raise PaperEventStreamCorruption(
                "event aggregate or provenance does not match its stream"
            )
        seen_fingerprint = current.event_fingerprints.get(event.event_id)
        if seen_fingerprint is not None:
            if seen_fingerprint == fingerprint:
                return current
            raise PaperEventStreamCorruption("event_id was reused with divergent bytes")
        expected_version = current.aggregate_version + 1
        if event.aggregate_version < expected_version:
            raise PaperEventStreamCorruption(
                "aggregate version was reused by a different event"
            )
        if event.aggregate_version > expected_version:
            raise PaperEventStreamCorruption("aggregate event sequence contains a gap")
        if event.source_revision != current.source_revision + 1:
            raise PaperEventStreamConflict(
                "authoritative source revision must advance by exactly one"
            )
    else:
        if event.aggregate_version != 1:
            raise PaperEventStreamCorruption("first aggregate event version must be one")
        if event.event_type not in IMPORT_EVENT_TYPES and event.source_revision != 0:
            raise PaperEventStreamConflict("create event source revision must be zero")

    _validate_transition_and_identity_keys(current, event)
    _validate_causation(current, event)

    prior_scopes = set(() if current is None else current.identity_scope_hashes)
    new_scopes = {item.scope_hash for item in event.identity_keys}
    if prior_scopes & new_scopes:
        raise PaperEventStreamConflict("execution identity scope was reused")

    fingerprints = {} if current is None else dict(current.event_fingerprints)
    versions = {} if current is None else dict(current.version_event_ids)
    fingerprints[event.event_id] = fingerprint
    versions[event.aggregate_version] = event.event_id
    after = payload_after(event.payload)
    return PaperExecutionProjection(
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        store_id=event.store_id,
        account_scope_fingerprint=event.account_scope_fingerprint,
        data_mode=event.data_mode,
        broker_environment=event.broker_environment,
        after=after.model_copy(deep=True),
        aggregate_version=event.aggregate_version,
        source_revision=event.source_revision,
        last_event_id=event.event_id,
        event_fingerprints=fingerprints,
        version_event_ids=versions,
        identity_scope_hashes=tuple(sorted(prior_scopes | new_scopes)),
    )


def replay_paper_execution_events(
    events: Iterable[PaperExecutionEvent | Mapping[str, Any]],
    *,
    expected_provenance: PaperExecutionEventProvenance | None = None,
) -> PaperExecutionProjection:
    projection: PaperExecutionProjection | None = None
    for event in events:
        projection = reduce_paper_execution_event(
            projection,
            event,
            expected_provenance=expected_provenance,
        )
    if projection is None:
        raise PaperEventStreamConflict("cannot replay an empty event stream")
    return projection


def join_correlated_execution_projections(
    projections: Iterable[PaperExecutionProjection],
) -> list[PaperCorrelatedExecutionProjection]:
    """Join independent streams only for read-only parity diagnostics."""

    grouped: dict[str, PaperCorrelatedExecutionProjection] = {}
    provenance_by_order_plan: dict[str, PaperExecutionEventProvenance] = {}
    for source in projections:
        projection = _deep_projection(source)
        after = projection.after
        order_plan_id = after.order_plan_id
        provenance = _projection_provenance(projection)
        known_provenance = provenance_by_order_plan.get(order_plan_id)
        if known_provenance is not None and known_provenance != provenance:
            raise PaperEventStreamCorruption(
                "correlated execution projections have mismatched provenance"
            )
        provenance_by_order_plan.setdefault(order_plan_id, provenance)
        target = grouped.setdefault(
            order_plan_id,
            PaperCorrelatedExecutionProjection(order_plan_id=order_plan_id),
        )
        if isinstance(after, PaperOrderDispatch):
            if target.order_dispatch is not None:
                raise PaperEventStreamConflict("duplicate order projection in parity join")
            target.order_dispatch = projection
        elif isinstance(after, PaperRiskReservation):
            if target.risk_reservation is not None:
                raise PaperEventStreamConflict("duplicate reservation projection in parity join")
            target.risk_reservation = projection
        else:
            target.cancel_requests.append(projection)
            target.cancel_requests.sort(key=lambda item: item.aggregate_id)
    return [grouped[key] for key in sorted(grouped)]


__all__ = [
    "PaperEventSchemaUnsupported",
    "PaperEventStreamConflict",
    "PaperEventStreamCorruption",
    "PaperExecutionProjection",
    "PaperCorrelatedExecutionProjection",
    "join_correlated_execution_projections",
    "projection_canonical_bytes",
    "reduce_paper_execution_event",
    "replay_paper_execution_events",
]
