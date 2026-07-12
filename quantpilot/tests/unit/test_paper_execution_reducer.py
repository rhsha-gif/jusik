from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.execution.events import (
    PaperEventStreamConflict,
    PaperEventStreamCorruption,
    PaperExecutionEvent,
    PaperExecutionEventProvenance,
    build_paper_execution_event,
    identity_keys_for_dispatch,
)
from quantpilot.packages.core.execution.reducer import (
    join_correlated_execution_projections,
    projection_canonical_bytes,
    reduce_paper_execution_event,
    replay_paper_execution_events,
)
from quantpilot.packages.core.execution.transitions import (
    PAPER_CANCEL_TRANSITIONS,
    PAPER_DISPATCH_RECONCILIATION_TRANSITIONS,
    PAPER_DISPATCH_TRANSITIONS,
    PAPER_RESERVATION_RELEASE_BY_DISPATCH,
    classify_dispatch_event_type,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperDispatchFillEvidence,
    PaperOrderDispatch,
    PaperRiskReservation,
)


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
ACCOUNT = "sha256:" + "a" * 64
OTHER_ACCOUNT = "sha256:" + "b" * 64
STORE = "store-reducer-tests"


def _dispatch(**updates: object) -> PaperOrderDispatch:
    prepared_at = NOW + timedelta(seconds=1)
    values: dict[str, object] = {
        "order_plan_id": "oplan-reducer-001",
        "broker_order_id": "bord-local-001",
        "run_id": "run-reducer-001",
        "idempotency_key": "paper-reducer-001",
        "request_fingerprint": "sha256:" + "c" * 64,
        "policy_id": "policy-paper",
        "policy_version": 3,
        "user_id": "local-user",
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "purpose": "rebalance",
        "symbol": "005930",
        "side": "buy",
        "quantity": 10.0,
        "limit_price": 70_000.0,
        "quote_as_of": NOW,
        "quote_last": 69_900.0,
        "quote_bid": 69_800.0,
        "quote_ask": 70_000.0,
        "quote_reference_basis": "l2_midpoint",
        "risk_check_id": "risk-final-001",
        "risk_check_expires_at": NOW + timedelta(minutes=10),
        "submission_evidence_expires_at": NOW + timedelta(minutes=9),
        "reconciled_snapshot_id": "snapshot-paper-001",
        "reconciled_snapshot_at": NOW,
        "snapshot_cash": 2_000_000.0,
        "snapshot_equity": 10_000_000.0,
        "snapshot_symbol_quantity": 5.0,
        "snapshot_symbol_orderable_quantity": 4.0,
        "snapshot_daily_loss_ratio": -0.01,
        "snapshot_monthly_loss_ratio": -0.02,
        "broker_orderable_cash": 1_000_000.0,
        "broker_orderable_buy_quantity": 14.0,
        "minimum_cash_reserve_krw": 0,
        "entry_atr14": 1_200.0,
        "store_id": STORE,
        "session_id": "psess-reducer-001",
        "fencing_token": 1,
        "account_scope_fingerprint": ACCOUNT,
        "prepared_at": prepared_at,
        "updated_at": prepared_at,
    }
    values.update(updates)
    return PaperOrderDispatch(**values)


def _claimed(dispatch: PaperOrderDispatch) -> PaperOrderDispatch:
    claimed_at = dispatch.updated_at + timedelta(seconds=1)
    return PaperOrderDispatch.model_validate(
        dispatch.model_copy(
            update={
                "status": "dispatch_claimed",
                "attempt_count": 1,
                "dispatch_claimed_at": claimed_at,
                "updated_at": claimed_at,
                "revision": dispatch.revision + 1,
            }
        ).model_dump()
    )


def _accepted(dispatch: PaperOrderDispatch) -> PaperOrderDispatch:
    updated_at = dispatch.updated_at + timedelta(seconds=1)
    return PaperOrderDispatch.model_validate(
        dispatch.model_copy(
            update={
                "status": "accepted",
                "broker_business_date": date(2026, 7, 10),
                "broker_order_reference": "0000012345",
                "broker_forwarding_order_org_number": "70001",
                "broker_order_time": "101530",
                "last_error_code": None,
                "updated_at": updated_at,
                "revision": dispatch.revision + 1,
            }
        ).model_dump()
    )


def _fill(
    dispatch: PaperOrderDispatch,
    *,
    reference: str,
    quantity: float,
    at: datetime,
    time_basis: str = "broker_execution",
) -> PaperDispatchFillEvidence:
    return PaperDispatchFillEvidence(
        broker_fill_reference=reference,
        broker_order_id=dispatch.broker_order_id,
        broker_order_reference="0000012345",
        symbol=dispatch.symbol,
        side=dispatch.side,
        quantity=quantity,
        price=dispatch.limit_price,
        notional=quantity * dispatch.limit_price,
        evidence_at=at,
        time_basis=time_basis,
    )


def _partial(
    dispatch: PaperOrderDispatch,
    *,
    quantities: tuple[float, ...] = (2.0,),
) -> PaperOrderDispatch:
    observed_at = dispatch.updated_at + timedelta(seconds=1)
    existing = list(dispatch.fill_evidence)
    new = [
        _fill(
            dispatch,
            reference=f"exec-{len(existing) + index + 1}",
            quantity=quantity,
            at=observed_at,
        )
        for index, quantity in enumerate(quantities)
    ]
    fills = [*existing, *new]
    return PaperOrderDispatch.model_validate(
        dispatch.model_copy(
            update={
                "status": "partially_filled",
                "broker_business_date": date(2026, 7, 10),
                "broker_order_reference": "0000012345",
                "broker_forwarding_order_org_number": (
                    dispatch.broker_forwarding_order_org_number
                ),
                "broker_order_branch_number": "00123",
                "broker_order_time": "101530",
                "cumulative_filled_quantity": sum(item.quantity for item in fills),
                "fill_evidence": fills,
                "updated_at": observed_at,
                "revision": dispatch.revision + 1,
            }
        ).model_dump()
    )


def _filled(dispatch: PaperOrderDispatch) -> PaperOrderDispatch:
    remaining = dispatch.quantity - dispatch.cumulative_filled_quantity
    observed_at = dispatch.updated_at + timedelta(seconds=1)
    fills = list(dispatch.fill_evidence)
    if remaining > 0:
        fills.append(
            _fill(
                dispatch,
                reference=f"exec-{len(fills) + 1}",
                quantity=remaining,
                at=observed_at,
            )
        )
    return PaperOrderDispatch.model_validate(
        dispatch.model_copy(
            update={
                "status": "filled",
                "reconciliation_status": "reconciled",
                "broker_business_date": date(2026, 7, 10),
                "broker_order_reference": "0000012345",
                "broker_order_branch_number": "00123",
                "broker_order_time": "101530",
                "cumulative_filled_quantity": dispatch.quantity,
                "fill_evidence": fills,
                "last_error_code": None,
                "updated_at": observed_at,
                "reconciled_at": observed_at,
                "revision": dispatch.revision + 1,
            }
        ).model_dump()
    )


def _reservation(dispatch: PaperOrderDispatch, **updates: object) -> PaperRiskReservation:
    values: dict[str, object] = {
        "reservation_id": "presv-reducer-001",
        "order_plan_id": dispatch.order_plan_id,
        "idempotency_key": dispatch.idempotency_key,
        "kind": "cash_buy",
        "symbol": dispatch.symbol,
        "side": "buy",
        "reserved_cash_krw": 700_000,
        "reserved_sell_quantity": None,
        "reserved_gross_exposure_krw": 700_000,
        "broker_orderable_cash_basis_krw": 1_000_000,
        "broker_orderable_buy_quantity_basis": 14,
        "snapshot_orderable_quantity_basis": None,
        "snapshot_gross_exposure_basis_krw": 8_000_000,
        "minimum_cash_reserve_krw": 0,
        "gross_exposure_limit_krw": 10_000_000,
        "store_id": STORE,
        "session_id": dispatch.session_id,
        "fencing_token": dispatch.fencing_token,
        "account_scope_fingerprint": ACCOUNT,
        "created_at": dispatch.prepared_at,
        "updated_at": dispatch.prepared_at,
    }
    values.update(updates)
    return PaperRiskReservation(**values)


def _cancel(**updates: object) -> PaperCancelRequest:
    values: dict[str, object] = {
        "cancel_id": "pcancel-reducer-001",
        "kill_id": "pkill-reducer-001",
        "order_plan_id": "oplan-reducer-001",
        "broker_order_id": "bord-local-001",
        "broker_order_reference": "0000012345",
        "broker_forwarding_order_org_number": "70001",
        "symbol": "005930",
        "side": "buy",
        "cancelable_quantity": 10,
        "original_limit_price": 70_000,
        "store_id": STORE,
        "account_scope_fingerprint": ACCOUNT,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return PaperCancelRequest(**values)


def _order_events() -> tuple[list[PaperExecutionEvent], list[PaperOrderDispatch]]:
    prepared = _dispatch()
    claimed = _claimed(prepared)
    partial = _partial(claimed)
    filled = _filled(partial)
    events = [
        build_paper_execution_event(
            event_id="pevt-prepared",
            aggregate_version=1,
            event_type="OrderPrepared",
            source="local_prepare",
            after=prepared,
            causation_id="pevt-risk-reserved",
        ),
        build_paper_execution_event(
            event_id="pevt-claimed",
            aggregate_version=2,
            event_type="DispatchClaimed",
            source="local_dispatch_claim",
            after=claimed,
            before=prepared,
            causation_id="pevt-prepared",
        ),
        build_paper_execution_event(
            event_id="pevt-partial",
            aggregate_version=3,
            event_type="OrderPartiallyFilled",
            source="broker_reconciliation",
            after=partial,
            before=claimed,
            causation_id="pevt-claimed",
        ),
        build_paper_execution_event(
            event_id="pevt-filled",
            aggregate_version=4,
            event_type="OrderFilled",
            source="broker_reconciliation",
            after=filled,
            before=partial,
            causation_id="pevt-partial",
        ),
    ]
    return events, [prepared, claimed, partial, filled]


def test_transition_definitions_match_schema_v10_surface() -> None:
    assert PAPER_DISPATCH_TRANSITIONS["dispatch_claimed"] == {
        "outcome_unknown",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
    }
    assert PAPER_DISPATCH_RECONCILIATION_TRANSITIONS == {
        "pending": {"pending", "blocked", "reconciled"},
        "blocked": {"blocked", "reconciled"},
        "reconciled": {"reconciled"},
    }
    assert PAPER_CANCEL_TRANSITIONS["prepared"] == {
        "reconciled_cancelled",
        "reconciled_filled",
    }
    assert PAPER_RESERVATION_RELEASE_BY_DISPATCH["failed_pre_dispatch"] == (
        "released_expired",
        "failed_pre_dispatch",
    )


def test_special_classifier_runs_before_generic_enrichment() -> None:
    prepared = _dispatch()
    claimed = _claimed(prepared)
    rebound_at = prepared.updated_at + timedelta(seconds=1)
    rebound = PaperOrderDispatch.model_validate(
        prepared.model_copy(
            update={
                "session_id": "psess-reducer-002",
                "fencing_token": 2,
                "updated_at": rebound_at,
                "revision": 1,
            }
        ).model_dump()
    )
    assert classify_dispatch_event_type(
        None,
        prepared,
        special_event_type="OrderPrepared",
    ) == "OrderPrepared"
    assert classify_dispatch_event_type(
        prepared,
        claimed,
        special_event_type="DispatchClaimed",
    ) == "DispatchClaimed"
    assert classify_dispatch_event_type(
        prepared,
        rebound,
        special_event_type="DispatchFenceRebound",
    ) == "DispatchFenceRebound"
    same_session = PaperOrderDispatch.model_validate(
        rebound.model_copy(update={"session_id": prepared.session_id}).model_dump()
    )
    with pytest.raises(ValueError, match="successor session and fence"):
        classify_dispatch_event_type(
            prepared,
            same_session,
            special_event_type="DispatchFenceRebound",
        )


def test_generic_dispatch_classifier_covers_all_five_precedence_branches() -> None:
    claimed = _claimed(_dispatch())
    blocked_at = claimed.updated_at + timedelta(seconds=1)
    blocked = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "outcome_unknown",
                "reconciliation_status": "blocked",
                "last_error_code": "broker_match_ambiguous",
                "updated_at": blocked_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    assert classify_dispatch_event_type(claimed, blocked) == "ReconciliationBlocked"

    accepted = _accepted(claimed)
    assert classify_dispatch_event_type(claimed, accepted) == "OrderAccepted"

    rejected_at = claimed.updated_at + timedelta(seconds=1)
    rejected = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "rejected",
                "updated_at": rejected_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    reconciled_at = rejected.updated_at + timedelta(seconds=1)
    reconciled = PaperOrderDispatch.model_validate(
        rejected.model_copy(
            update={
                "reconciliation_status": "reconciled",
                "reconciled_at": reconciled_at,
                "updated_at": reconciled_at,
                "revision": rejected.revision + 1,
            }
        ).model_dump()
    )
    assert classify_dispatch_event_type(rejected, reconciled) == "DispatchReconciled"

    partial = _partial(claimed)
    grown = _partial(partial, quantities=(3.0,))
    assert classify_dispatch_event_type(partial, grown) == "OrderPartiallyFilled"

    enriched_at = accepted.updated_at + timedelta(seconds=1)
    enriched = PaperOrderDispatch.model_validate(
        accepted.model_copy(
            update={
                "broker_order_branch_number": "00123",
                "updated_at": enriched_at,
                "revision": accepted.revision + 1,
            }
        ).model_dump()
    )
    assert classify_dispatch_event_type(accepted, enriched) == "DispatchEvidenceObserved"


def test_replay_is_deterministic_no_sort_and_exact_duplicate_is_noop() -> None:
    events, states = _order_events()
    original_events = [event.model_dump() for event in events]
    projection = replay_paper_execution_events(events)
    first_bytes = projection_canonical_bytes(projection)
    assert projection.after == states[-1]
    assert projection.aggregate_version == 4

    duplicate = reduce_paper_execution_event(projection, events[-1])
    assert projection_canonical_bytes(duplicate) == first_bytes
    assert [event.model_dump() for event in events] == original_events
    assert projection_canonical_bytes(replay_paper_execution_events(events)) == first_bytes

    with pytest.raises(PaperEventStreamCorruption, match="first aggregate event version"):
        replay_paper_execution_events([events[1], events[0]])


def test_fill_before_acceptance_is_legal_but_late_acceptance_regression_blocks() -> None:
    events, states = _order_events()
    partial_projection = replay_paper_execution_events(events[:3])
    partial = states[2]
    late_at = partial.updated_at + timedelta(seconds=1)
    late_acceptance = PaperOrderDispatch.model_validate(
        partial.model_copy(
            update={
                "status": "accepted",
                "cumulative_filled_quantity": 0,
                "fill_evidence": [],
                "updated_at": late_at,
                "revision": partial.revision + 1,
            }
        ).model_dump()
    )
    event = build_paper_execution_event(
        event_id="pevt-late-acceptance",
        aggregate_version=4,
        event_type="OrderAccepted",
        source="broker_reconciliation",
        after=late_acceptance,
        before=None,
        causation_id="pevt-partial",
    )
    with pytest.raises(PaperEventStreamConflict):
        reduce_paper_execution_event(partial_projection, event)
    assert partial_projection.after == partial


def test_gap_divergent_retry_hash_and_source_revision_fail_closed() -> None:
    events, states = _order_events()
    prepared_projection = replay_paper_execution_events(events[:1])
    gap = PaperExecutionEvent.model_validate(
        events[1].model_copy(update={"aggregate_version": 3}).model_dump()
    )
    with pytest.raises(PaperEventStreamCorruption, match="sequence contains a gap"):
        reduce_paper_execution_event(prepared_projection, gap)

    claimed_projection = replay_paper_execution_events(events[:2])
    divergent = PaperExecutionEvent.model_validate(
        events[1].model_copy(update={"causation_id": "pevt-other"}).model_dump()
    )
    with pytest.raises(PaperEventStreamCorruption, match="event_id was reused"):
        reduce_paper_execution_event(claimed_projection, divergent)

    corrupt = events[1].model_dump()
    corrupt["payload_hash"] = "sha256:" + "0" * 64
    with pytest.raises(PaperEventStreamCorruption, match="malformed canonical event"):
        reduce_paper_execution_event(prepared_projection, corrupt)

    skipped_revision = PaperOrderDispatch.model_validate(
        states[1].model_copy(update={"revision": 2}).model_dump()
    )
    skipped = build_paper_execution_event(
        event_id="pevt-skipped-revision",
        aggregate_version=2,
        event_type="DispatchClaimed",
        source="local_dispatch_claim",
        after=skipped_revision,
        before=states[0],
        causation_id="pevt-prepared",
    )
    with pytest.raises(PaperEventStreamConflict, match="source revision"):
        reduce_paper_execution_event(prepared_projection, skipped)


def test_identity_keys_and_event_type_are_corruption_bound_to_the_payload() -> None:
    events, states = _order_events()
    claimed_projection = replay_paper_execution_events(events[:2])

    missing_keys = PaperExecutionEvent.model_validate(
        events[2].model_copy(update={"identity_keys": []}).model_dump()
    )
    with pytest.raises(PaperEventStreamCorruption, match="identity keys"):
        reduce_paper_execution_event(claimed_projection, missing_keys)

    partial_projection = replay_paper_execution_events(events[:3])
    all_fill_keys = identity_keys_for_dispatch(None, states[3])
    forged_old_key = next(
        key for key in all_fill_keys if key.external_id == "exec-1"
    )
    forged_keys = PaperExecutionEvent.model_validate(
        events[3].model_copy(update={"identity_keys": [forged_old_key]}).model_dump()
    )
    with pytest.raises(PaperEventStreamCorruption, match="identity keys"):
        reduce_paper_execution_event(partial_projection, forged_keys)

    wrong_type = PaperExecutionEvent.model_validate(
        events[2].model_copy(update={"event_type": "DispatchEvidenceObserved"}).model_dump()
    )
    with pytest.raises(PaperEventStreamCorruption, match="event type"):
        reduce_paper_execution_event(claimed_projection, wrong_type)


def test_expected_provenance_and_closed_source_and_causation_are_enforced() -> None:
    prepared = _dispatch()
    event = build_paper_execution_event(
        event_id="pevt-prepared",
        aggregate_version=1,
        event_type="OrderPrepared",
        source="local_prepare",
        after=prepared,
        causation_id="pevt-risk-reserved",
    )
    foreign = PaperExecutionEventProvenance(
        store_id=STORE,
        account_scope_fingerprint=OTHER_ACCOUNT,
    )
    with pytest.raises(PaperEventStreamCorruption, match="expected store provenance"):
        reduce_paper_execution_event(None, event, expected_provenance=foreign)

    wrong_source = PaperExecutionEvent.model_validate(
        event.model_copy(update={"source": "broker_acceptance"}).model_dump()
    )
    with pytest.raises(PaperEventStreamCorruption, match="OrderPrepared source"):
        reduce_paper_execution_event(None, wrong_source)

    missing_cause = PaperExecutionEvent.model_validate(
        event.model_copy(update={"causation_id": None}).model_dump()
    )
    with pytest.raises(PaperEventStreamCorruption, match="paired RiskReserved"):
        reduce_paper_execution_event(None, missing_cause)


def test_process_recovery_and_local_submission_origins_have_closed_deltas() -> None:
    events, states = _order_events()
    claimed_projection = replay_paper_execution_events(events[:2])
    claimed = states[1]

    recovered_at = claimed.updated_at + timedelta(seconds=1)
    recovered = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "outcome_unknown",
                "last_error_code": "process_interrupted",
                "updated_at": recovered_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    recovery_event = build_paper_execution_event(
        event_id="pevt-recovered",
        aggregate_version=3,
        event_type="OutcomeUnknown",
        source="process_recovery",
        after=recovered,
        before=claimed,
        causation_id="pevt-claimed",
    )
    assert reduce_paper_execution_event(claimed_projection, recovery_event).after == recovered

    rejected_at = claimed.updated_at + timedelta(seconds=1)
    rejected = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "rejected",
                "reconciliation_status": "reconciled",
                "last_error_code": "paper_kill_engaged_after_claim",
                "reconciled_at": rejected_at,
                "updated_at": rejected_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    local_event = build_paper_execution_event(
        event_id="pevt-local-rejected",
        aggregate_version=3,
        event_type="OrderRejected",
        source="local_submission_result",
        after=rejected,
        before=claimed,
        causation_id="pevt-claimed",
    )
    assert reduce_paper_execution_event(claimed_projection, local_event).after == rejected

    invalid_broker = build_paper_execution_event(
        event_id="pevt-invalid-broker-rejected",
        aggregate_version=3,
        event_type="OrderRejected",
        source="broker_acceptance",
        after=rejected,
        before=claimed,
        causation_id="pevt-claimed",
    )
    with pytest.raises(PaperEventStreamCorruption, match="broker rejection code"):
        reduce_paper_execution_event(claimed_projection, invalid_broker)


def test_dispatch_sources_reject_cross_origin_fields_and_error_codes() -> None:
    events, states = _order_events()
    claimed_projection = replay_paper_execution_events(events[:2])
    claimed = states[1]

    recovered_at = claimed.updated_at + timedelta(seconds=1)
    forged_recovery = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "outcome_unknown",
                "reconciliation_status": "blocked",
                "last_error_code": "process_interrupted",
                "updated_at": recovered_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    recovery_event = build_paper_execution_event(
        event_id="pevt-forged-recovery",
        aggregate_version=3,
        event_type="ReconciliationBlocked",
        source="process_recovery",
        after=forged_recovery,
        before=claimed,
        causation_id="pevt-claimed",
    )
    with pytest.raises(PaperEventStreamCorruption, match="process-recovery"):
        reduce_paper_execution_event(claimed_projection, recovery_event)

    rejected_at = claimed.updated_at + timedelta(seconds=1)
    local_with_broker_identity = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "rejected",
                "reconciliation_status": "reconciled",
                "broker_order_reference": "0000012345",
                "last_error_code": "local_configuration_error",
                "updated_at": rejected_at,
                "reconciled_at": rejected_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    local_event = build_paper_execution_event(
        event_id="pevt-local-with-broker-identity",
        aggregate_version=3,
        event_type="OrderRejected",
        source="local_submission_result",
        after=local_with_broker_identity,
        before=claimed,
        causation_id="pevt-claimed",
    )
    with pytest.raises(PaperEventStreamCorruption, match="local submission guard"):
        reduce_paper_execution_event(claimed_projection, local_event)

    broker_acceptance = _accepted(claimed)
    broker_acceptance_with_branch = PaperOrderDispatch.model_validate(
        broker_acceptance.model_copy(
            update={"broker_order_branch_number": "00123"}
        ).model_dump()
    )
    broker_event = build_paper_execution_event(
        event_id="pevt-broker-with-branch",
        aggregate_version=3,
        event_type="OrderAccepted",
        source="broker_acceptance",
        after=broker_acceptance_with_branch,
        before=claimed,
        causation_id="pevt-claimed",
    )
    with pytest.raises(PaperEventStreamCorruption, match="broker acceptance delta"):
        reduce_paper_execution_event(claimed_projection, broker_event)

    partial = states[2]
    partial_with_local_error = PaperOrderDispatch.model_validate(
        partial.model_copy(update={"last_error_code": "local_configuration_error"}).model_dump()
    )
    local_error_event = build_paper_execution_event(
        event_id="pevt-reconciliation-local-error",
        aggregate_version=3,
        event_type="OrderPartiallyFilled",
        source="broker_reconciliation",
        after=partial_with_local_error,
        before=claimed,
        causation_id="pevt-claimed",
    )
    with pytest.raises(PaperEventStreamCorruption, match="reconciliation delta"):
        reduce_paper_execution_event(claimed_projection, local_error_event)

    accepted_event = build_paper_execution_event(
        event_id="pevt-accepted-for-block",
        aggregate_version=3,
        event_type="OrderAccepted",
        source="broker_acceptance",
        after=broker_acceptance,
        before=claimed,
        causation_id="pevt-claimed",
    )
    accepted_projection = reduce_paper_execution_event(
        claimed_projection,
        accepted_event,
    )
    blocked_at = broker_acceptance.updated_at + timedelta(seconds=1)
    blocked_with_new_broker_evidence = PaperOrderDispatch.model_validate(
        broker_acceptance.model_copy(
            update={
                "reconciliation_status": "blocked",
                "broker_order_branch_number": "00123",
                "last_error_code": "broker_match_ambiguous",
                "updated_at": blocked_at,
                "revision": broker_acceptance.revision + 1,
            }
        ).model_dump()
    )
    blocked_event = build_paper_execution_event(
        event_id="pevt-blocked-with-evidence",
        aggregate_version=4,
        event_type="ReconciliationBlocked",
        source="broker_reconciliation",
        after=blocked_with_new_broker_evidence,
        before=broker_acceptance,
        causation_id="pevt-accepted-for-block",
    )
    with pytest.raises(PaperEventStreamCorruption, match="blocked delta"):
        reduce_paper_execution_event(accepted_projection, blocked_event)

    partial_projection = replay_paper_execution_events(events[:3])
    partial_with_new_fill = _partial(states[2], quantities=(1.0,))
    blocked_with_new_fill = PaperOrderDispatch.model_validate(
        partial_with_new_fill.model_copy(
            update={
                "reconciliation_status": "blocked",
                "last_error_code": "broker_match_ambiguous",
            }
        ).model_dump()
    )
    blocked_fill_event = build_paper_execution_event(
        event_id="pevt-blocked-with-fill",
        aggregate_version=4,
        event_type="ReconciliationBlocked",
        source="broker_reconciliation",
        after=blocked_with_new_fill,
        before=states[2],
        causation_id="pevt-partial",
    )
    with pytest.raises(PaperEventStreamCorruption, match="blocked delta"):
        reduce_paper_execution_event(partial_projection, blocked_fill_event)

    blocked_with_local_error = PaperOrderDispatch.model_validate(
        broker_acceptance.model_copy(
            update={
                "reconciliation_status": "blocked",
                "last_error_code": "local_configuration_error",
                "updated_at": blocked_at,
                "revision": broker_acceptance.revision + 1,
            }
        ).model_dump()
    )
    blocked_local_error_event = build_paper_execution_event(
        event_id="pevt-blocked-local-error",
        aggregate_version=4,
        event_type="ReconciliationBlocked",
        source="broker_reconciliation",
        after=blocked_with_local_error,
        before=broker_acceptance,
        causation_id="pevt-accepted-for-block",
    )
    with pytest.raises(PaperEventStreamCorruption, match="block code"):
        reduce_paper_execution_event(accepted_projection, blocked_local_error_event)


def test_broker_reconciliation_cannot_rewrite_local_guard_rejection() -> None:
    prepared = _dispatch()
    claimed = _claimed(prepared)
    claimed_projection = replay_paper_execution_events(
        [
            build_paper_execution_event(
                event_id="pevt-local-guard-prepared",
                aggregate_version=1,
                event_type="OrderPrepared",
                source="local_prepare",
                after=prepared,
                causation_id="pevt-local-guard-risk",
            ),
            build_paper_execution_event(
                event_id="pevt-local-guard-claimed",
                aggregate_version=2,
                event_type="DispatchClaimed",
                source="local_dispatch_claim",
                after=claimed,
                before=prepared,
                causation_id="pevt-local-guard-prepared",
            ),
        ]
    )
    rejected_at = claimed.updated_at + timedelta(seconds=1)
    rejected = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "rejected",
                "reconciliation_status": "reconciled",
                "last_error_code": "local_configuration_error",
                "updated_at": rejected_at,
                "reconciled_at": rejected_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    rejected_event = build_paper_execution_event(
        event_id="pevt-local-guard-rejected",
        aggregate_version=3,
        event_type="OrderRejected",
        source="local_submission_result",
        after=rejected,
        before=claimed,
        causation_id="pevt-local-guard-claimed",
    )
    rejected_projection = reduce_paper_execution_event(
        claimed_projection,
        rejected_event,
    )

    observed_at = rejected.updated_at + timedelta(seconds=1)
    forged = PaperOrderDispatch.model_validate(
        rejected.model_copy(
            update={
                "broker_business_date": date(2026, 7, 10),
                "broker_order_reference": "0000012345",
                "broker_order_branch_number": "00123",
                "broker_order_time": "101530",
                "last_error_code": None,
                "updated_at": observed_at,
                "reconciled_at": observed_at,
                "revision": rejected.revision + 1,
            }
        ).model_dump()
    )
    forged_event = build_paper_execution_event(
        event_id="pevt-forged-broker-enrichment",
        aggregate_version=4,
        event_type="DispatchEvidenceObserved",
        source="broker_reconciliation",
        after=forged,
        before=rejected,
        causation_id="pevt-local-guard-rejected",
    )
    with pytest.raises(
        PaperEventStreamCorruption,
        match="claimed external attempt",
    ):
        reduce_paper_execution_event(rejected_projection, forged_event)


def test_reservation_create_fence_and_release_are_closed() -> None:
    prepared = _dispatch()
    held = _reservation(prepared)
    created = build_paper_execution_event(
        event_id="pevt-risk",
        aggregate_version=1,
        event_type="RiskReserved",
        source="local_prepare",
        after=held,
        causation_id=None,
    )
    projection = reduce_paper_execution_event(None, created)

    rebound_at = held.updated_at + timedelta(seconds=1)
    rebound = PaperRiskReservation.model_validate(
        held.model_copy(
            update={
                "session_id": "psess-reducer-002",
                "fencing_token": 2,
                "updated_at": rebound_at,
                "revision": 1,
            }
        ).model_dump()
    )
    rebound_event = build_paper_execution_event(
        event_id="pevt-risk-rebound",
        aggregate_version=2,
        event_type="RiskReservationFenceRebound",
        source="local_session_takeover",
        after=rebound,
        causation_id="pevt-dispatch-rebound",
    )
    rebound_projection = reduce_paper_execution_event(projection, rebound_event)

    same_session = PaperRiskReservation.model_validate(
        rebound.model_copy(update={"session_id": held.session_id}).model_dump()
    )
    same_session_event = build_paper_execution_event(
        event_id="pevt-risk-same-session",
        aggregate_version=2,
        event_type="RiskReservationFenceRebound",
        source="local_session_takeover",
        after=same_session,
        causation_id="pevt-dispatch-rebound",
    )
    with pytest.raises(PaperEventStreamConflict):
        reduce_paper_execution_event(projection, same_session_event)

    released_at = rebound.updated_at + timedelta(seconds=1)
    released = PaperRiskReservation.model_validate(
        rebound.model_copy(
            update={
                "status": "released_filled",
                "release_reason": "filled",
                "released_at": released_at,
                "updated_at": released_at,
                "revision": 2,
            }
        ).model_dump()
    )
    release_event = build_paper_execution_event(
        event_id="pevt-risk-release",
        aggregate_version=3,
        event_type="RiskReservationReleased",
        source="broker_reconciliation",
        after=released,
        causation_id="pevt-order-filled",
    )
    projection = reduce_paper_execution_event(rebound_projection, release_event)
    assert projection.after == released

    contradictory = PaperRiskReservation.model_validate(
        rebound.model_copy(
            update={
                "status": "released_cancelled",
                "release_reason": "filled",
                "released_at": released_at,
                "updated_at": released_at,
                "revision": 2,
            }
        ).model_dump()
    )
    bad_event = build_paper_execution_event(
        event_id="pevt-risk-bad-release",
        aggregate_version=3,
        event_type="RiskReservationReleased",
        source="broker_reconciliation",
        after=contradictory,
        causation_id="pevt-order-cancelled",
    )
    with pytest.raises(PaperEventStreamConflict):
        reduce_paper_execution_event(
            rebound_projection,
            bad_event,
        )


@pytest.mark.parametrize(
    ("source", "status", "reason", "is_valid"),
    [
        ("broker_acceptance", "released_rejected", "rejected", True),
        ("broker_acceptance", "released_filled", "filled", False),
        ("broker_reconciliation", "released_filled", "filled", True),
        ("broker_reconciliation", "released_cancelled", "cancelled", True),
        ("broker_reconciliation", "released_rejected", "rejected", True),
        (
            "broker_reconciliation",
            "released_expired",
            "expired_pre_dispatch",
            False,
        ),
        (
            "local_submission_result",
            "released_expired",
            "expired_pre_dispatch",
            True,
        ),
        (
            "local_submission_result",
            "released_expired",
            "failed_pre_dispatch",
            True,
        ),
        ("local_submission_result", "released_rejected", "rejected", True),
        ("local_submission_result", "released_filled", "filled", False),
    ],
)
def test_reservation_release_source_matches_terminal_reason(
    source: str,
    status: str,
    reason: str,
    is_valid: bool,
) -> None:
    held = _reservation(_dispatch())
    projection = reduce_paper_execution_event(
        None,
        build_paper_execution_event(
            event_id="pevt-risk-source-matrix",
            aggregate_version=1,
            event_type="RiskReserved",
            source="local_prepare",
            after=held,
            causation_id=None,
        ),
    )
    released_at = held.updated_at + timedelta(seconds=1)
    released = PaperRiskReservation.model_validate(
        held.model_copy(
            update={
                "status": status,
                "release_reason": reason,
                "released_at": released_at,
                "updated_at": released_at,
                "revision": held.revision + 1,
            }
        ).model_dump()
    )
    event = build_paper_execution_event(
        event_id=f"pevt-release-{source}-{status}-{reason}",
        aggregate_version=2,
        event_type="RiskReservationReleased",
        source=source,
        after=released,
        causation_id="pevt-terminal-dispatch",
    )
    if is_valid:
        assert reduce_paper_execution_event(projection, event).after == released
    else:
        with pytest.raises(PaperEventStreamCorruption, match="reservation source"):
            reduce_paper_execution_event(projection, event)


def test_cancel_create_claim_terminal_and_same_status_tightening() -> None:
    prepared = _cancel()
    prepared_event = build_paper_execution_event(
        event_id="pevt-cancel-prepared",
        aggregate_version=1,
        event_type="CancelPrepared",
        source="kill_cancel",
        after=prepared,
        causation_id=None,
    )
    projection = reduce_paper_execution_event(None, prepared_event)

    claimed_at = prepared.updated_at + timedelta(seconds=1)
    claimed = PaperCancelRequest.model_validate(
        prepared.model_copy(
            update={
                "status": "cancel_claimed",
                "attempt_count": 1,
                "claimed_at": claimed_at,
                "updated_at": claimed_at,
                "revision": 1,
            }
        ).model_dump()
    )
    claimed_event = build_paper_execution_event(
        event_id="pevt-cancel-claimed",
        aggregate_version=2,
        event_type="CancelClaimed",
        source="kill_cancel",
        after=claimed,
        causation_id="pevt-cancel-prepared",
    )
    projection = reduce_paper_execution_event(projection, claimed_event)

    accepted_at = claimed.updated_at + timedelta(seconds=1)
    accepted = PaperCancelRequest.model_validate(
        claimed.model_copy(
            update={
                "status": "cancel_accepted",
                "response_order_reference": "0000099999",
                "updated_at": accepted_at,
                "revision": 2,
            }
        ).model_dump()
    )
    accepted_event = build_paper_execution_event(
        event_id="pevt-cancel-accepted",
        aggregate_version=3,
        event_type="CancelAccepted",
        source="kill_cancel",
        after=accepted,
        causation_id="pevt-cancel-claimed",
    )
    projection = reduce_paper_execution_event(projection, accepted_event)
    assert projection.after == accepted

    enriched_at = accepted.updated_at + timedelta(seconds=1)
    enriched = PaperCancelRequest.model_validate(
        accepted.model_copy(
            update={
                "last_error_code": "unexpected_enrichment",
                "updated_at": enriched_at,
                "revision": 3,
            }
        ).model_dump()
    )
    enriched_event = build_paper_execution_event(
        event_id="pevt-cancel-enriched",
        aggregate_version=4,
        event_type="CancelAccepted",
        source="kill_cancel",
        after=enriched,
        causation_id="pevt-cancel-accepted",
    )
    with pytest.raises(PaperEventStreamConflict):
        reduce_paper_execution_event(projection, enriched_event)

    nonzero = _cancel(revision=1)
    nonzero_event = build_paper_execution_event(
        event_id="pevt-cancel-nonzero",
        aggregate_version=1,
        event_type="CancelPrepared",
        source="kill_cancel",
        after=nonzero,
        causation_id=None,
    )
    with pytest.raises(PaperEventStreamConflict):
        reduce_paper_execution_event(None, nonzero_event)


def test_read_only_join_preserves_independent_aggregate_streams() -> None:
    prepared = _dispatch()
    order_event = build_paper_execution_event(
        event_id="pevt-prepared",
        aggregate_version=1,
        event_type="OrderPrepared",
        source="local_prepare",
        after=prepared,
        causation_id="pevt-risk",
    )
    reservation = _reservation(prepared)
    risk_event = build_paper_execution_event(
        event_id="pevt-risk",
        aggregate_version=1,
        event_type="RiskReserved",
        source="local_prepare",
        after=reservation,
        causation_id=None,
    )
    joined = join_correlated_execution_projections(
        [
            reduce_paper_execution_event(None, order_event),
            reduce_paper_execution_event(None, risk_event),
        ]
    )
    assert len(joined) == 1
    assert joined[0].order_plan_id == prepared.order_plan_id
    assert joined[0].order_dispatch is not None
    assert joined[0].risk_reservation is not None

    foreign_reservation = _reservation(
        prepared,
        store_id="foreign-store",
        account_scope_fingerprint=OTHER_ACCOUNT,
    )
    foreign_event = build_paper_execution_event(
        event_id="pevt-foreign-risk",
        aggregate_version=1,
        event_type="RiskReserved",
        source="local_prepare",
        after=foreign_reservation,
        causation_id=None,
    )
    with pytest.raises(PaperEventStreamCorruption, match="mismatched provenance"):
        join_correlated_execution_projections(
            [
                reduce_paper_execution_event(None, order_event),
                reduce_paper_execution_event(None, foreign_event),
            ]
        )

    cancel_event = build_paper_execution_event(
        event_id="pevt-cancel-for-join",
        aggregate_version=1,
        event_type="CancelPrepared",
        source="kill_cancel",
        after=_cancel(),
        causation_id=None,
    )
    cancel_projection = reduce_paper_execution_event(None, cancel_event)
    with pytest.raises(PaperEventStreamConflict, match="duplicate cancel"):
        join_correlated_execution_projections(
            [cancel_projection, cancel_projection]
        )
