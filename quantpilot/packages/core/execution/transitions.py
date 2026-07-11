"""Pure transition definitions for canonical KIS paper execution events.

This module deliberately has no persistence or broker dependencies.  Schema-v10
SQLite code can import these definitions without making the event reducer depend
on the database layer.
"""

from __future__ import annotations

from typing import Literal

from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperOrderDispatch,
    PaperRiskReservation,
)


PAPER_DISPATCH_TRANSITIONS: dict[str, set[str]] = {
    "prepared": {"expired_pre_dispatch", "failed_pre_dispatch"},
    "dispatch_claimed": {
        "outcome_unknown",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
    },
    "outcome_unknown": {
        "outcome_unknown",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
        "cancelled",
    },
    "accepted": {"accepted", "partially_filled", "filled", "rejected", "cancelled"},
    "partially_filled": {"partially_filled", "filled", "cancelled"},
    "filled": {"filled"},
    "rejected": {"rejected"},
    "cancelled": {"cancelled"},
    "expired_pre_dispatch": {"expired_pre_dispatch"},
    "failed_pre_dispatch": {"failed_pre_dispatch"},
}

PAPER_DISPATCH_RECONCILIATION_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"pending", "blocked", "reconciled"},
    "blocked": {"blocked", "reconciled"},
    "reconciled": {"reconciled"},
}

PAPER_CANCEL_TRANSITIONS: dict[str, set[str]] = {
    "prepared": {"reconciled_cancelled", "reconciled_filled"},
    "cancel_claimed": {
        "cancel_accepted",
        "cancel_outcome_unknown",
        "reconciled_cancelled",
        "reconciled_filled",
        "rejected",
    },
    "cancel_accepted": {
        "cancel_accepted",
        "reconciled_cancelled",
        "reconciled_filled",
    },
    "cancel_outcome_unknown": {
        "cancel_outcome_unknown",
        "reconciled_cancelled",
        "reconciled_filled",
    },
    "reconciled_cancelled": {"reconciled_cancelled"},
    "reconciled_filled": {"reconciled_filled"},
    "rejected": {"rejected", "reconciled_cancelled", "reconciled_filled"},
}

PAPER_RESERVATION_TERMINALS = {
    "released_filled",
    "released_cancelled",
    "released_rejected",
    "released_expired",
}

PAPER_RESERVATION_RELEASE_BY_DISPATCH = {
    "filled": ("released_filled", "filled"),
    "cancelled": ("released_cancelled", "cancelled"),
    "rejected": ("released_rejected", "rejected"),
    "expired_pre_dispatch": ("released_expired", "expired_pre_dispatch"),
    "failed_pre_dispatch": ("released_expired", "failed_pre_dispatch"),
}

ORDER_DISPATCH_EVENT_TYPES = frozenset(
    {
        "LegacyOrderDispatchImported",
        "OrderPrepared",
        "DispatchFenceRebound",
        "DispatchClaimed",
        "OutcomeUnknown",
        "OrderAccepted",
        "DispatchEvidenceObserved",
        "OrderPartiallyFilled",
        "OrderFilled",
        "OrderRejected",
        "OrderCancelled",
        "OrderExpiredPreDispatch",
        "OrderFailedPreDispatch",
        "ReconciliationBlocked",
        "DispatchReconciled",
    }
)

RISK_RESERVATION_EVENT_TYPES = frozenset(
    {
        "LegacyRiskReservationImported",
        "RiskReserved",
        "RiskReservationFenceRebound",
        "RiskReservationReleased",
    }
)

CANCEL_REQUEST_EVENT_TYPES = frozenset(
    {
        "LegacyCancelRequestImported",
        "CancelPrepared",
        "CancelClaimed",
        "CancelAccepted",
        "CancelOutcomeUnknown",
        "CancelRejected",
        "CancelReconciledCancelled",
        "CancelReconciledFilled",
    }
)

PAPER_EXECUTION_EVENT_TYPES = frozenset(
    ORDER_DISPATCH_EVENT_TYPES
    | RISK_RESERVATION_EVENT_TYPES
    | CANCEL_REQUEST_EVENT_TYPES
)

IMPORT_EVENT_TYPES = frozenset(
    {
        "LegacyOrderDispatchImported",
        "LegacyRiskReservationImported",
        "LegacyCancelRequestImported",
    }
)

DISPATCH_STATUS_EVENT_TYPES = {
    "outcome_unknown": "OutcomeUnknown",
    "accepted": "OrderAccepted",
    "partially_filled": "OrderPartiallyFilled",
    "filled": "OrderFilled",
    "rejected": "OrderRejected",
    "cancelled": "OrderCancelled",
    "expired_pre_dispatch": "OrderExpiredPreDispatch",
    "failed_pre_dispatch": "OrderFailedPreDispatch",
}

CANCEL_STATUS_EVENT_TYPES = {
    "prepared": "CancelPrepared",
    "cancel_claimed": "CancelClaimed",
    "cancel_accepted": "CancelAccepted",
    "cancel_outcome_unknown": "CancelOutcomeUnknown",
    "rejected": "CancelRejected",
    "reconciled_cancelled": "CancelReconciledCancelled",
    "reconciled_filled": "CancelReconciledFilled",
}

PaperDispatchSpecialEventType = Literal[
    "LegacyOrderDispatchImported",
    "OrderPrepared",
    "DispatchFenceRebound",
    "DispatchClaimed",
]


def dispatch_immutable_identity(dispatch: PaperOrderDispatch) -> tuple[object, ...]:
    """Return the schema-v10 immutable identity checked by generic updates."""

    return (
        dispatch.order_plan_id,
        dispatch.broker_order_id,
        dispatch.run_id,
        dispatch.idempotency_key,
        dispatch.request_fingerprint,
        dispatch.policy_id,
        dispatch.policy_version,
        dispatch.user_id,
        dispatch.strategy_id,
        dispatch.strategy_version,
        dispatch.purpose,
        dispatch.symbol,
        dispatch.side,
        dispatch.order_type,
        dispatch.quantity,
        dispatch.limit_price,
        dispatch.quote_as_of,
        dispatch.quote_last,
        dispatch.quote_bid,
        dispatch.quote_ask,
        dispatch.quote_reference_basis,
        dispatch.risk_check_id,
        dispatch.risk_check_expires_at,
        dispatch.submission_evidence_expires_at,
        dispatch.reconciled_snapshot_id,
        dispatch.reconciled_snapshot_at,
        dispatch.snapshot_cash,
        dispatch.snapshot_equity,
        dispatch.snapshot_symbol_quantity,
        dispatch.snapshot_symbol_orderable_quantity,
        dispatch.snapshot_daily_loss_ratio,
        dispatch.snapshot_monthly_loss_ratio,
        dispatch.broker_orderable_cash,
        dispatch.broker_orderable_buy_quantity,
        dispatch.minimum_cash_reserve_krw,
        dispatch.entry_atr14,
        dispatch.store_id,
        dispatch.session_id,
        dispatch.fencing_token,
        dispatch.data_mode,
        dispatch.broker_environment,
        dispatch.account_scope_fingerprint,
        dispatch.prepared_at,
    )


def cancel_immutable_identity(request: PaperCancelRequest) -> tuple[object, ...]:
    """Return the schema-v10 immutable identity checked by cancel updates."""

    return (
        request.kill_id,
        request.order_plan_id,
        request.broker_order_id,
        request.broker_order_reference,
        request.broker_forwarding_order_org_number,
        request.symbol,
        request.side,
        request.cancelable_quantity,
        request.original_limit_price,
        request.exchange_id,
        request.store_id,
        request.data_mode,
        request.broker_environment,
        request.account_scope_fingerprint,
    )


def new_dispatch_fills(
    before: PaperOrderDispatch | None,
    after: PaperOrderDispatch,
) -> tuple[object, ...]:
    """Return newly introduced fill models after proving old evidence is intact."""

    before_by_reference = (
        {}
        if before is None
        else {item.broker_fill_reference: item for item in before.fill_evidence}
    )
    after_by_reference = {
        item.broker_fill_reference: item for item in after.fill_evidence
    }
    if not set(before_by_reference).issubset(after_by_reference) or any(
        after_by_reference[reference] != evidence
        for reference, evidence in before_by_reference.items()
    ):
        raise ValueError("paper dispatch fill evidence cannot be removed or changed")
    return tuple(
        after_by_reference[reference]
        for reference in sorted(set(after_by_reference) - set(before_by_reference))
    )


def _validate_dispatch_create(after: PaperOrderDispatch) -> None:
    if (
        after.status != "prepared"
        or after.revision != 0
        or after.attempt_count != 0
        or after.dispatch_claimed_at is not None
    ):
        raise ValueError("new paper dispatch must be prepared at revision zero")


def _validate_dispatch_claim(
    before: PaperOrderDispatch,
    after: PaperOrderDispatch,
) -> None:
    if before.status != "prepared" or before.attempt_count != 0:
        raise ValueError("dispatch claim requires an unattempted prepared order")
    if after.updated_at <= before.updated_at:
        raise ValueError("dispatch claim timestamp must advance")
    if after.dispatch_claimed_at is None or after.dispatch_claimed_at != after.updated_at:
        raise ValueError("dispatch claim requires exact durable claim time")
    expected = PaperOrderDispatch.model_validate(
        before.model_copy(
            update={
                "status": "dispatch_claimed",
                "attempt_count": 1,
                "dispatch_claimed_at": after.dispatch_claimed_at,
                "updated_at": after.updated_at,
                "revision": before.revision + 1,
            }
        ).model_dump()
    )
    if after != expected:
        raise ValueError("dispatch claim changed fields outside the closed claim shape")


def _validate_dispatch_fence_rebound(
    before: PaperOrderDispatch,
    after: PaperOrderDispatch,
) -> None:
    if before.status != "prepared" or before.attempt_count != 0:
        raise ValueError("dispatch fence rebound requires an unattempted prepared order")
    if after.updated_at <= before.updated_at:
        raise ValueError("dispatch fence rebound timestamp must advance")
    if after.fencing_token <= before.fencing_token:
        raise ValueError("dispatch fence rebound requires a successor fence")
    expected = PaperOrderDispatch.model_validate(
        before.model_copy(
            update={
                "session_id": after.session_id,
                "fencing_token": after.fencing_token,
                "updated_at": after.updated_at,
                "revision": before.revision + 1,
            }
        ).model_dump()
    )
    if after != expected:
        raise ValueError("dispatch fence rebound changed fields outside the closed shape")


def validate_generic_dispatch_transition(
    before: PaperOrderDispatch,
    after: PaperOrderDispatch,
) -> None:
    """Apply the exact generic schema-v10 dispatch mutation surface."""

    if dispatch_immutable_identity(after) != dispatch_immutable_identity(before):
        raise ValueError("paper dispatch immutable identity changed")
    if after.revision != before.revision + 1:
        raise ValueError("paper dispatch revision must advance by exactly one")
    if after.updated_at <= before.updated_at:
        raise ValueError("paper dispatch update timestamp must advance")
    if after.attempt_count != before.attempt_count:
        raise ValueError("generic dispatch update cannot change attempt count")
    if after.dispatch_claimed_at != before.dispatch_claimed_at:
        raise ValueError("paper dispatch claim evidence is immutable")
    if after.status not in PAPER_DISPATCH_TRANSITIONS[before.status]:
        raise ValueError(f"invalid paper dispatch transition: {before.status} -> {after.status}")
    for field_name in (
        "broker_order_reference",
        "broker_business_date",
        "broker_forwarding_order_org_number",
        "broker_order_branch_number",
        "broker_order_time",
    ):
        previous = getattr(before, field_name)
        if previous is not None and getattr(after, field_name) != previous:
            raise ValueError(f"paper dispatch {field_name} is immutable once assigned")
    new_dispatch_fills(before, after)
    if after.cumulative_filled_quantity < before.cumulative_filled_quantity - 0.000001:
        raise ValueError("paper dispatch cumulative fills cannot decrease")
    if after.reconciliation_status not in PAPER_DISPATCH_RECONCILIATION_TRANSITIONS[
        before.reconciliation_status
    ]:
        raise ValueError("paper dispatch reconciliation cannot move backward")


def classify_dispatch_event_type(
    before: PaperOrderDispatch | None,
    after: PaperOrderDispatch,
    *,
    special_event_type: PaperDispatchSpecialEventType | None = None,
) -> str:
    """Classify special shapes first, then the five generic precedence branches."""

    if special_event_type == "LegacyOrderDispatchImported":
        if before is not None:
            raise ValueError("legacy import must seed an empty stream")
        return special_event_type
    if special_event_type == "OrderPrepared":
        if before is not None:
            raise ValueError("order preparation must seed an empty stream")
        _validate_dispatch_create(after)
        return special_event_type
    if before is None:
        raise ValueError("dispatch updates require a prior projection")
    if special_event_type == "DispatchClaimed":
        _validate_dispatch_claim(before, after)
        return special_event_type
    if special_event_type == "DispatchFenceRebound":
        _validate_dispatch_fence_rebound(before, after)
        return special_event_type
    if special_event_type is not None:
        raise ValueError("unknown dispatch special event type")

    validate_generic_dispatch_transition(before, after)
    new_fills = new_dispatch_fills(before, after)
    if after.reconciliation_status == "blocked":
        return "ReconciliationBlocked"
    if after.status != before.status:
        try:
            return DISPATCH_STATUS_EVENT_TYPES[after.status]
        except KeyError as exc:
            raise ValueError("dispatch lifecycle has no canonical event") from exc
    if (
        after.reconciliation_status == "reconciled"
        and before.reconciliation_status in {"pending", "blocked"}
    ):
        return "DispatchReconciled"
    if new_fills:
        if after.status != "partially_filled":
            raise ValueError("same-status fill growth requires partially_filled")
        return "OrderPartiallyFilled"
    return "DispatchEvidenceObserved"


def _reservation_static_identity(
    reservation: PaperRiskReservation,
) -> tuple[object, ...]:
    values = reservation.model_dump()
    for key in (
        "session_id",
        "fencing_token",
        "status",
        "release_reason",
        "updated_at",
        "released_at",
        "revision",
    ):
        values.pop(key)
    return tuple(sorted(values.items()))


def validate_reservation_event_transition(
    before: PaperRiskReservation | None,
    after: PaperRiskReservation,
    event_type: str,
) -> None:
    if event_type == "LegacyRiskReservationImported":
        if before is not None:
            raise ValueError("legacy reservation import must seed an empty stream")
        return
    if event_type == "RiskReserved":
        if before is not None or after.status != "held" or after.revision != 0:
            raise ValueError("risk reservation must start held at revision zero")
        return
    if before is None:
        raise ValueError("reservation update requires a prior projection")
    if _reservation_static_identity(after) != _reservation_static_identity(before):
        raise ValueError("paper reservation immutable capacity evidence changed")
    if after.revision != before.revision + 1 or after.updated_at <= before.updated_at:
        raise ValueError("paper reservation revision and time must advance")
    if event_type == "RiskReservationFenceRebound":
        if before.status != "held" or after.status != "held":
            raise ValueError("reservation fence rebound requires a held reservation")
        if after.fencing_token <= before.fencing_token:
            raise ValueError("reservation fence rebound requires a successor fence")
        expected = PaperRiskReservation.model_validate(
            before.model_copy(
                update={
                    "session_id": after.session_id,
                    "fencing_token": after.fencing_token,
                    "updated_at": after.updated_at,
                    "revision": before.revision + 1,
                }
            ).model_dump()
        )
        if after != expected:
            raise ValueError("reservation fence rebound changed fields outside the closed shape")
        return
    if event_type != "RiskReservationReleased":
        raise ValueError("unknown reservation transition event")
    if before.status != "held" or after.status not in PAPER_RESERVATION_TERMINALS:
        raise ValueError("reservation release requires held-to-terminal transition")
    valid_pairs = set(PAPER_RESERVATION_RELEASE_BY_DISPATCH.values())
    if (after.status, after.release_reason) not in valid_pairs:
        raise ValueError("reservation release status and reason disagree")
    expected = PaperRiskReservation.model_validate(
        before.model_copy(
            update={
                "status": after.status,
                "release_reason": after.release_reason,
                "released_at": after.released_at,
                "updated_at": after.updated_at,
                "revision": before.revision + 1,
            }
        ).model_dump()
    )
    if after != expected or after.released_at != after.updated_at:
        raise ValueError("reservation release changed fields outside the closed shape")


def validate_cancel_event_transition(
    before: PaperCancelRequest | None,
    after: PaperCancelRequest,
    event_type: str,
) -> None:
    if event_type == "LegacyCancelRequestImported":
        if before is not None:
            raise ValueError("legacy cancel import must seed an empty stream")
        return
    if event_type == "CancelPrepared":
        if (
            before is not None
            or after.status != "prepared"
            or after.revision != 0
            or after.attempt_count != 0
        ):
            raise ValueError("cancel request must start prepared at revision zero")
        return
    if before is None:
        raise ValueError("cancel update requires a prior projection")
    if event_type == "CancelClaimed":
        if before.status != "prepared" or before.attempt_count != 0:
            raise ValueError("cancel claim requires an unattempted prepared request")
        if after.updated_at <= before.updated_at:
            raise ValueError("cancel claim timestamp must advance")
        if after.claimed_at is None or after.claimed_at != after.updated_at:
            raise ValueError("cancel claim requires exact durable claim time")
        expected = PaperCancelRequest.model_validate(
            before.model_copy(
                update={
                    "status": "cancel_claimed",
                    "attempt_count": 1,
                    "claimed_at": after.claimed_at,
                    "updated_at": after.updated_at,
                    "revision": before.revision + 1,
                }
            ).model_dump()
        )
        if after != expected:
            raise ValueError("cancel claim changed fields outside the closed shape")
        return
    if cancel_immutable_identity(after) != cancel_immutable_identity(before):
        raise ValueError("paper cancel immutable identity changed")
    if after.revision != before.revision + 1 or after.updated_at <= before.updated_at:
        raise ValueError("paper cancel revision and time must advance")
    if after.attempt_count != before.attempt_count or after.claimed_at != before.claimed_at:
        raise ValueError("generic cancel update cannot change claim evidence")
    if after.status == before.status:
        raise ValueError("same-status changed-field cancel writes are excluded from v1")
    if after.status not in PAPER_CANCEL_TRANSITIONS[before.status]:
        raise ValueError(f"invalid paper cancel transition: {before.status} -> {after.status}")
    expected_event = CANCEL_STATUS_EVENT_TYPES[after.status]
    if event_type != expected_event:
        raise ValueError("cancel event type does not match after status")
