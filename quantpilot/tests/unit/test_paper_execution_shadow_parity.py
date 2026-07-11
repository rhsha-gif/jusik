"""Gate-2 shadow parity, restart, ordering, and legacy-corpus evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import sqlite3
import pytest

from quantpilot.packages.core.execution.events import (
    PaperExecutionEventProvenance,
    canonical_json_bytes,
    event_canonical_bytes,
    identity_keys_for_dispatch,
)
from quantpilot.packages.core.execution.reducer import (
    join_correlated_execution_projections,
    replay_paper_execution_events,
)
from quantpilot.packages.core.kis_paper import KisPaperClient
from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperDispatchFillEvidence,
    PaperOrderDispatch,
)
from quantpilot.packages.db.sqlite_repositories import (
    PaperStateConflictError,
    PaperStateStore,
)
from quantpilot.tests.paper_execution_event_store_fixtures import (
    ACCOUNT_B,
    NOW,
    downgrade_to_schema,
    insert_reserved_dispatch,
    make_cancel_request,
    make_dispatch,
    paper_store,
    start_session,
)


def _expected_provenance(store: PaperStateStore) -> PaperExecutionEventProvenance:
    provenance = store.provenance
    return PaperExecutionEventProvenance(
        store_id=provenance.store_id,
        account_scope_fingerprint=provenance.account_scope_fingerprint,
        data_mode="paper_trading",
        broker_environment="kis_paper",
    )


def _assert_store_parity(store: PaperStateStore) -> None:
    """Replay every stream, join by order, and compare all authoritative models."""

    changes_before = store._connection.total_changes  # noqa: SLF001
    events_by_stream = defaultdict(list)
    for event in store.list_paper_execution_events():
        events_by_stream[(event.aggregate_type, event.aggregate_id)].append(event)
    projections = [
        replay_paper_execution_events(
            events,
            expected_provenance=_expected_provenance(store),
        )
        for _, events in sorted(events_by_stream.items())
    ]

    dispatches = {
        item.order_plan_id: item for item in store.list_paper_order_dispatches()
    }
    reservations = {
        item.reservation_id: item for item in store.list_paper_risk_reservations()
    }
    cancels = {
        item.cancel_id: item for item in store.list_paper_cancel_requests()
    }
    authoritative = {
        **{("order_dispatch", key): value for key, value in dispatches.items()},
        **{("risk_reservation", key): value for key, value in reservations.items()},
        **{("cancel_request", key): value for key, value in cancels.items()},
    }
    replayed = {
        (projection.aggregate_type, projection.aggregate_id): projection.after
        for projection in projections
    }
    assert replayed == authoritative

    joined = {
        item.order_plan_id: item
        for item in join_correlated_execution_projections(projections)
    }
    expected_order_ids = {
        *dispatches,
        *(item.order_plan_id for item in reservations.values()),
        *(item.order_plan_id for item in cancels.values()),
    }
    assert set(joined) == expected_order_ids
    for order_plan_id, correlated in joined.items():
        expected_dispatch = dispatches.get(order_plan_id)
        expected_reservation = next(
            (
                item
                for item in reservations.values()
                if item.order_plan_id == order_plan_id
            ),
            None,
        )
        expected_cancels = sorted(
            (
                item
                for item in cancels.values()
                if item.order_plan_id == order_plan_id
            ),
            key=lambda item: item.cancel_id,
        )
        assert (
            None
            if correlated.order_dispatch is None
            else correlated.order_dispatch.after
        ) == expected_dispatch
        assert (
            None
            if correlated.risk_reservation is None
            else correlated.risk_reservation.after
        ) == expected_reservation
        assert [item.after for item in correlated.cancel_requests] == expected_cancels
    assert store._connection.total_changes == changes_before  # noqa: SLF001


def _accepted(
    store: PaperStateStore,
    claimed: PaperOrderDispatch,
    *,
    at=NOW + timedelta(seconds=3),
) -> PaperOrderDispatch:
    accepted = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "accepted",
                "broker_business_date": NOW.date(),
                "broker_order_reference": "0000001234",
                "broker_forwarding_order_org_number": "06010",
                "broker_order_time": "100001",
                "last_error_code": None,
                "updated_at": at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    return store.update_paper_order_dispatch(
        accepted,
        mutation_origin="broker_post_result",
    )


def _fill(
    dispatch: PaperOrderDispatch,
    *,
    reference: str,
    quantity: float,
    at,
) -> PaperDispatchFillEvidence:
    return PaperDispatchFillEvidence(
        broker_fill_reference=reference,
        broker_order_id=dispatch.broker_order_id,
        broker_order_reference=(
            dispatch.broker_order_reference or "0000001234"
        ),
        symbol=dispatch.symbol,
        side=dispatch.side,
        quantity=quantity,
        price=dispatch.limit_price,
        notional=quantity * dispatch.limit_price,
        evidence_at=at,
        time_basis="broker_daily_aggregate_first_observed",
    )


def _with_fill_state(
    store: PaperStateStore,
    dispatch: PaperOrderDispatch,
    *,
    status: str,
    quantities: tuple[float, ...],
    at,
) -> PaperOrderDispatch:
    fills = list(dispatch.fill_evidence)
    for index, quantity in enumerate(quantities, start=1):
        if index <= len(fills):
            assert fills[index - 1].quantity == quantity
            continue
        fills.append(
            _fill(
                dispatch,
                reference=f"fill-{dispatch.order_plan_id}-{index}",
                quantity=quantity,
                at=at,
            )
        )
    terminal = status == "filled"
    updated = PaperOrderDispatch.model_validate(
        dispatch.model_copy(
            update={
                "status": status,
                "reconciliation_status": "reconciled" if terminal else "pending",
                "broker_business_date": NOW.date(),
                "broker_order_reference": "0000001234",
                "broker_order_branch_number": "00123",
                "broker_order_time": "100001",
                "cumulative_filled_quantity": sum(quantities),
                "fill_evidence": fills,
                "last_error_code": None,
                "updated_at": at,
                "reconciled_at": at if terminal else None,
                "revision": dispatch.revision + 1,
            }
        ).model_dump()
    )
    return store.update_paper_order_dispatch(
        updated,
        mutation_origin="broker_reconciliation",
    )


def _terminal_without_fill(
    store: PaperStateStore,
    dispatch: PaperOrderDispatch,
    *,
    status: str,
    at,
    origin: str,
) -> PaperOrderDispatch:
    updates: dict[str, object] = {
        "status": status,
        "reconciliation_status": "reconciled",
        "updated_at": at,
        "reconciled_at": at,
        "revision": dispatch.revision + 1,
    }
    if status == "rejected":
        updates["last_error_code"] = (
            "broker_business_rejected"
            if origin == "broker_post_result"
            else "local_configuration_error"
        )
    elif status == "cancelled":
        updates.update(
            {
                "broker_order_branch_number": "00123",
                "last_error_code": None,
            }
        )
    elif status == "expired_pre_dispatch":
        updates["last_error_code"] = "risk_check_expired"
    elif status == "failed_pre_dispatch":
        updates["last_error_code"] = "paper_session_closed_before_dispatch"
    terminal = PaperOrderDispatch.model_validate(
        dispatch.model_copy(update=updates).model_dump()
    )
    return store.update_paper_order_dispatch(
        terminal,
        mutation_origin=origin,  # type: ignore[arg-type]
    )


def _cancel_update(
    store: PaperStateStore,
    session,
    request: PaperCancelRequest,
    *,
    status: str,
    at,
) -> PaperCancelRequest:
    terminal = status in {"reconciled_cancelled", "reconciled_filled"}
    error_codes = {
        "cancel_outcome_unknown": "broker_response_ambiguous",
        "rejected": "broker_business_rejected",
    }
    updated = PaperCancelRequest.model_validate(
        request.model_copy(
            update={
                "status": status,
                "response_order_reference": (
                    "0000005678"
                    if status == "cancel_accepted"
                    else request.response_order_reference
                ),
                "last_error_code": error_codes.get(status),
                "updated_at": at,
                "reconciled_at": at if terminal else None,
                "revision": request.revision + 1,
            }
        ).model_dump()
    )
    return store.update_paper_cancel_request(
        updated,
        session=session,
        mutation_origin="kill_cancel_journal",
    )


def test_every_runtime_public_mutation_path_replays_after_each_write(tmp_path) -> None:
    with paper_store(tmp_path / "normal.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="normal")
        insert_reserved_dispatch(store, prepared)
        _assert_store_parity(store)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        _assert_store_parity(store)
        accepted = _accepted(store, claimed)
        _assert_store_parity(store)
        partial = _with_fill_state(
            store,
            accepted,
            status="partially_filled",
            quantities=(3,),
            at=NOW + timedelta(seconds=4),
        )
        _assert_store_parity(store)
        _with_fill_state(
            store,
            partial,
            status="filled",
            quantities=(3, 7),
            at=NOW + timedelta(seconds=5),
        )
        _assert_store_parity(store)

    with paper_store(tmp_path / "takeover.sqlite3") as store:
        predecessor = start_session(store)
        prepared = make_dispatch(store, predecessor, suffix="takeover")
        insert_reserved_dispatch(store, prepared)
        successor = start_session(store, started_at=NOW + timedelta(hours=2))
        store.takeover_prepared_paper_order_dispatch(
            prepared.order_plan_id,
            session=successor,
            taken_over_at=NOW + timedelta(hours=2, seconds=1),
        )
        _assert_store_parity(store)

    with paper_store(tmp_path / "recovery.sqlite3") as store:
        predecessor = start_session(store)
        prepared = make_dispatch(store, predecessor, suffix="recovery")
        insert_reserved_dispatch(store, prepared)
        store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=predecessor,
            claimed_at=NOW + timedelta(seconds=2),
        )
        successor = start_session(store, started_at=NOW + timedelta(hours=2))
        store.recover_interrupted_dispatches(
            session=successor,
            recovered_at=NOW + timedelta(hours=2, seconds=1),
        )
        _assert_store_parity(store)

    with paper_store(tmp_path / "cancel.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="cancel")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        kill = store.start_paper_kill_operation(
            session=session,
            reason="parity cancel corpus",
            started_at=NOW + timedelta(seconds=4),
        )
        request = store.create_paper_cancel_request(
            make_cancel_request(
                store,
                kill_id=kill.kill_id,
                dispatch=accepted,
                created_at=NOW + timedelta(seconds=5),
            ),
            session=session,
        )
        _assert_store_parity(store)
        request = store.claim_paper_cancel_attempt(
            request.cancel_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=6),
        )
        _assert_store_parity(store)
        request = _cancel_update(
            store,
            session,
            request,
            status="cancel_accepted",
            at=NOW + timedelta(seconds=7),
        )
        _assert_store_parity(store)
        _terminal_without_fill(
            store,
            accepted,
            status="cancelled",
            at=NOW + timedelta(seconds=8),
            origin="broker_reconciliation",
        )
        _assert_store_parity(store)
        _cancel_update(
            store,
            session,
            request,
            status="reconciled_cancelled",
            at=NOW + timedelta(seconds=9),
        )
        _assert_store_parity(store)


def test_fill_before_ack_and_cancel_fill_races_remain_atomic_and_in_parity(
    tmp_path,
) -> None:
    with paper_store(tmp_path / "fill-before-ack.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="fill-before-ack")
        insert_reserved_dispatch(store, prepared)
        forged_at = NOW + timedelta(seconds=2)
        forged_fill = _fill(
            prepared,
            reference="fill-before-claim",
            quantity=1,
            at=forged_at,
        )
        fill_before_claim = PaperOrderDispatch.model_validate(
            prepared.model_copy(
                update={
                    "status": "partially_filled",
                    "attempt_count": 1,
                    "dispatch_claimed_at": forged_at,
                    "broker_business_date": NOW.date(),
                    "broker_order_reference": "0000001234",
                    "broker_order_branch_number": "00123",
                    "broker_order_time": "100001",
                    "cumulative_filled_quantity": 1,
                    "fill_evidence": [forged_fill],
                    "updated_at": forged_at,
                    "revision": prepared.revision + 1,
                }
            ).model_dump()
        )
        before_claim_events = tuple(
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        )
        with pytest.raises(PaperStateConflictError):
            store.update_paper_order_dispatch(
                fill_before_claim,
                mutation_origin="broker_reconciliation",
            )
        assert store.load_paper_order_dispatch(prepared.order_plan_id) == prepared
        assert tuple(
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        ) == before_claim_events
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        partial = _with_fill_state(
            store,
            claimed,
            status="partially_filled",
            quantities=(1,),
            at=NOW + timedelta(seconds=3),
        )
        before_events = [
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        ]
        late_acceptance = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "accepted",
                    "broker_business_date": NOW.date(),
                    "broker_order_reference": "0000001234",
                    "broker_forwarding_order_org_number": "06010",
                    "broker_order_time": "100001",
                    "updated_at": NOW + timedelta(seconds=4),
                    "revision": partial.revision + 1,
                }
            ).model_dump()
        )
        with pytest.raises(PaperStateConflictError):
            store.update_paper_order_dispatch(
                late_acceptance,
                mutation_origin="broker_post_result",
            )
        assert store.load_paper_order_dispatch(prepared.order_plan_id) == partial
        assert [
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        ] == before_events
        _with_fill_state(
            store,
            partial,
            status="filled",
            quantities=(1, 9),
            at=NOW + timedelta(seconds=5),
        )
        _assert_store_parity(store)

    with paper_store(tmp_path / "full-fill-before-ack.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="full-fill-before-ack")
        insert_reserved_dispatch(store, prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        filled = _with_fill_state(
            store,
            claimed,
            status="filled",
            quantities=(10,),
            at=NOW + timedelta(seconds=3),
        )
        assert filled.status == "filled"
        reservation = store.load_paper_risk_reservation(prepared.order_plan_id)
        assert reservation is not None
        assert reservation.status == "released_filled"
        _assert_store_parity(store)

    with paper_store(tmp_path / "fill-wins-cancel.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="fill-wins")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        kill = store.start_paper_kill_operation(
            session=session,
            reason="fill wins cancel race",
            started_at=NOW + timedelta(seconds=4),
        )
        request = store.create_paper_cancel_request(
            make_cancel_request(
                store,
                kill_id=kill.kill_id,
                dispatch=accepted,
                created_at=NOW + timedelta(seconds=5),
            ),
            session=session,
        )
        request = store.claim_paper_cancel_attempt(
            request.cancel_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=6),
        )
        request = _cancel_update(
            store,
            session,
            request,
            status="cancel_accepted",
            at=NOW + timedelta(seconds=7),
        )
        _with_fill_state(
            store,
            accepted,
            status="filled",
            quantities=(10,),
            at=NOW + timedelta(seconds=8),
        )
        _cancel_update(
            store,
            session,
            request,
            status="reconciled_filled",
            at=NOW + timedelta(seconds=9),
        )
        _assert_store_parity(store)


def test_cumulative_fill_replay_growth_and_regression_have_closed_results(
    tmp_path,
) -> None:
    with paper_store(tmp_path / "cumulative-fill.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="cumulative-fill")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        one = _with_fill_state(
            store,
            accepted,
            status="partially_filled",
            quantities=(1,),
            at=NOW + timedelta(seconds=4),
        )
        one_events = tuple(
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        )
        assert store.update_paper_order_dispatch(
            one,
            mutation_origin="broker_reconciliation",
        ) == one
        assert tuple(
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        ) == one_events

        two = _with_fill_state(
            store,
            one,
            status="partially_filled",
            quantities=(1, 1),
            at=NOW + timedelta(seconds=5),
        )
        assert two.cumulative_filled_quantity == 2
        two_events = tuple(
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        )
        partial_events = store.list_paper_execution_events(
            "order_dispatch",
            prepared.order_plan_id,
        )
        assert len(partial_events) == 5
        assert len(partial_events[-1].identity_keys) == 1
        regressed = PaperOrderDispatch.model_validate(
            two.model_copy(
                update={
                    "cumulative_filled_quantity": 1,
                    "fill_evidence": [two.fill_evidence[0]],
                    "updated_at": NOW + timedelta(seconds=6),
                    "revision": two.revision + 1,
                }
            ).model_dump()
        )
        with pytest.raises(PaperStateConflictError):
            store.update_paper_order_dispatch(
                regressed,
                mutation_origin="broker_reconciliation",
            )
        assert store.load_paper_order_dispatch(prepared.order_plan_id) == two
        assert tuple(
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        ) == two_events
        _assert_store_parity(store)


def test_runtime_multi_fill_event_persists_one_identity_row_per_new_fill(
    tmp_path,
) -> None:
    with paper_store(tmp_path / "multi-fill.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="multi-fill")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        partial = _with_fill_state(
            store,
            accepted,
            status="partially_filled",
            quantities=(1, 2),
            at=NOW + timedelta(seconds=4),
        )
        events = store.list_paper_execution_events(
            "order_dispatch",
            prepared.order_plan_id,
        )
        multi_fill_event = events[-1]
        assert multi_fill_event.event_type == "OrderPartiallyFilled"
        assert len(multi_fill_event.identity_keys) == 2
        persisted_identity_count = store._connection.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM paper_execution_event_identity_keys "
            "WHERE event_id = ?",
            (multi_fill_event.event_id,),
        ).fetchone()[0]
        assert persisted_identity_count == 2
        assert partial.cumulative_filled_quantity == 3
        _assert_store_parity(store)


def test_execution_identity_scope_is_account_sensitive(tmp_path) -> None:
    with paper_store(tmp_path / "identity-account-scope.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="identity-account")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        partial = _with_fill_state(
            store,
            accepted,
            status="partially_filled",
            quantities=(1,),
            at=NOW + timedelta(seconds=4),
        )
        venue_scoped = PaperOrderDispatch.model_validate(
            partial.model_copy(
                update={
                    "fill_evidence": [
                        partial.fill_evidence[0].model_copy(
                            update={"time_basis": "broker_execution"}
                        )
                    ]
                }
            ).model_dump()
        )
        local_key = identity_keys_for_dispatch(None, venue_scoped)[0]
        foreign = PaperOrderDispatch.model_validate(
            venue_scoped.model_copy(
                update={"account_scope_fingerprint": ACCOUNT_B}
            ).model_dump()
        )
        foreign_key = identity_keys_for_dispatch(None, foreign)[0]

        assert local_key.kind == "venue_execution"
        assert local_key.kind == foreign_key.kind
        assert local_key.external_id == foreign_key.external_id
        assert local_key.evidence_payload_hash == foreign_key.evidence_payload_hash
        assert local_key.scope_hash != foreign_key.scope_hash


def test_partial_cancel_preserves_fills_and_cancel_rejection_holds_capacity(
    tmp_path,
) -> None:
    with paper_store(tmp_path / "partial-cancel.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="partial-cancel")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        partial = _with_fill_state(
            store,
            accepted,
            status="partially_filled",
            quantities=(3,),
            at=NOW + timedelta(seconds=4),
        )
        kill = store.start_paper_kill_operation(
            session=session,
            reason="partial fill then cancel",
            started_at=NOW + timedelta(seconds=5),
        )
        request = store.create_paper_cancel_request(
            make_cancel_request(
                store,
                kill_id=kill.kill_id,
                dispatch=partial,
                created_at=NOW + timedelta(seconds=6),
            ),
            session=session,
        )
        request = store.claim_paper_cancel_attempt(
            request.cancel_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=7),
        )
        request = _cancel_update(
            store,
            session,
            request,
            status="cancel_accepted",
            at=NOW + timedelta(seconds=8),
        )
        cancelled = _terminal_without_fill(
            store,
            partial,
            status="cancelled",
            at=NOW + timedelta(seconds=9),
            origin="broker_reconciliation",
        )
        _cancel_update(
            store,
            session,
            request,
            status="reconciled_cancelled",
            at=NOW + timedelta(seconds=10),
        )
        assert cancelled.fill_evidence == partial.fill_evidence
        assert cancelled.cumulative_filled_quantity == 3
        reservation = store.load_paper_risk_reservation(prepared.order_plan_id)
        assert reservation is not None
        assert reservation.status == "released_cancelled"
        _assert_store_parity(store)

    with paper_store(tmp_path / "cancel-rejected.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="cancel-rejected")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        kill = store.start_paper_kill_operation(
            session=session,
            reason="cancel rejection capacity",
            started_at=NOW + timedelta(seconds=4),
        )
        request = store.create_paper_cancel_request(
            make_cancel_request(
                store,
                kill_id=kill.kill_id,
                dispatch=accepted,
                created_at=NOW + timedelta(seconds=5),
            ),
            session=session,
        )
        request = store.claim_paper_cancel_attempt(
            request.cancel_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=6),
        )
        rejected = _cancel_update(
            store,
            session,
            request,
            status="rejected",
            at=NOW + timedelta(seconds=7),
        )
        assert rejected.last_error_code == "broker_business_rejected"
        assert store.load_paper_order_dispatch(prepared.order_plan_id) == accepted
        reservation = store.load_paper_risk_reservation(prepared.order_plan_id)
        assert reservation is not None
        assert reservation.status == "held"
        _assert_store_parity(store)


def test_contradictory_post_terminal_evidence_changes_nothing(tmp_path) -> None:
    with paper_store(tmp_path / "terminal-contradiction.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="terminal-contradiction")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        filled = _with_fill_state(
            store,
            accepted,
            status="filled",
            quantities=(10,),
            at=NOW + timedelta(seconds=4),
        )
        released = store.load_paper_risk_reservation(prepared.order_plan_id)
        assert released is not None
        before_events = tuple(
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        )
        contradicted_at = NOW + timedelta(seconds=5)
        contradictory = PaperOrderDispatch.model_validate(
            filled.model_copy(
                update={
                    "status": "cancelled",
                    "updated_at": contradicted_at,
                    "reconciled_at": contradicted_at,
                    "revision": filled.revision + 1,
                }
            ).model_dump()
        )

        with pytest.raises(PaperStateConflictError):
            store.update_paper_order_dispatch(
                contradictory,
                mutation_origin="broker_reconciliation",
            )

        assert store.load_paper_order_dispatch(prepared.order_plan_id) == filled
        assert store.load_paper_risk_reservation(prepared.order_plan_id) == released
        assert tuple(
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        ) == before_events
        _assert_store_parity(store)


def test_reconciliation_block_and_legacy_terminal_reconcile_emit_exact_events(
    tmp_path,
) -> None:
    with paper_store(tmp_path / "reconciliation-block.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="reconciliation-block")
        insert_reserved_dispatch(store, prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        blocked_at = NOW + timedelta(seconds=3)
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
        blocked = store.update_paper_order_dispatch(
            blocked,
            mutation_origin="broker_reconciliation",
        )
        events = store.list_paper_execution_events(
            "order_dispatch",
            prepared.order_plan_id,
        )
        assert events[-1].event_type == "ReconciliationBlocked"
        assert events[-1].source_revision == blocked.revision
        _assert_store_parity(store)

    path = tmp_path / "legacy-terminal-reconciled.sqlite3"
    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="legacy-reconciled")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        filled = _with_fill_state(
            store,
            accepted,
            status="filled",
            quantities=(10,),
            at=NOW + timedelta(seconds=4),
        )
    downgrade_to_schema(path, target_version=10)
    legacy_blocked = PaperOrderDispatch.model_validate(
        filled.model_copy(
            update={
                "reconciliation_status": "blocked",
                "last_error_code": "broker_match_ambiguous",
                "reconciled_at": None,
            }
        ).model_dump()
    )
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE paper_order_dispatches SET status = ?, revision = ?, "
            "state_json = ?, updated_at = ? WHERE order_plan_id = ?",
            (
                legacy_blocked.status,
                legacy_blocked.revision,
                canonical_json_bytes(legacy_blocked).decode("utf-8"),
                legacy_blocked.updated_at.isoformat(),
                legacy_blocked.order_plan_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with paper_store(path) as migrated:
        imported_events = migrated.list_paper_execution_events(
            "order_dispatch",
            legacy_blocked.order_plan_id,
        )
        assert [event.event_type for event in imported_events] == [
            "LegacyOrderDispatchImported"
        ]
        reconciled_at = legacy_blocked.updated_at + timedelta(seconds=1)
        reconciled = PaperOrderDispatch.model_validate(
            legacy_blocked.model_copy(
                update={
                    "reconciliation_status": "reconciled",
                    "last_error_code": None,
                    "updated_at": reconciled_at,
                    "reconciled_at": reconciled_at,
                    "revision": legacy_blocked.revision + 1,
                }
            ).model_dump()
        )
        reconciled = migrated.update_paper_order_dispatch(
            reconciled,
            mutation_origin="broker_reconciliation",
        )
        events = migrated.list_paper_execution_events(
            "order_dispatch",
            legacy_blocked.order_plan_id,
        )
        assert [event.event_type for event in events] == [
            "LegacyOrderDispatchImported",
            "DispatchReconciled",
        ]
        reservation_events = migrated.list_paper_execution_events(
            "risk_reservation",
            f"presv-{legacy_blocked.order_plan_id}",
        )
        assert len(reservation_events) == 1
        assert reconciled.reconciliation_status == "reconciled"
        _assert_store_parity(migrated)


def _advance_dispatch_for_import(
    store: PaperStateStore,
    session,
    prepared: PaperOrderDispatch,
    status: str,
) -> PaperOrderDispatch:
    if status == "prepared":
        return prepared
    if status in {"expired_pre_dispatch", "failed_pre_dispatch"}:
        return _terminal_without_fill(
            store,
            prepared,
            status=status,
            at=NOW + timedelta(seconds=2),
            origin="local_submission_guard",
        )
    claimed = store.claim_dispatch_attempt(
        prepared.order_plan_id,
        session=session,
        claimed_at=NOW + timedelta(seconds=2),
    )
    if status == "dispatch_claimed":
        return claimed
    if status == "outcome_unknown":
        unknown = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "outcome_unknown",
                    "last_error_code": "broker_response_ambiguous",
                    "updated_at": NOW + timedelta(seconds=3),
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        return store.update_paper_order_dispatch(
            unknown,
            mutation_origin="broker_post_result",
        )
    if status == "rejected":
        return _terminal_without_fill(
            store,
            claimed,
            status="rejected",
            at=NOW + timedelta(seconds=3),
            origin="broker_post_result",
        )
    accepted = _accepted(store, claimed)
    if status == "accepted":
        return accepted
    if status == "partially_filled":
        return _with_fill_state(
            store,
            accepted,
            status="partially_filled",
            quantities=(3,),
            at=NOW + timedelta(seconds=4),
        )
    if status == "filled":
        return _with_fill_state(
            store,
            accepted,
            status="filled",
            quantities=(10,),
            at=NOW + timedelta(seconds=4),
        )
    if status == "cancelled":
        return _terminal_without_fill(
            store,
            accepted,
            status="cancelled",
            at=NOW + timedelta(seconds=4),
            origin="broker_reconciliation",
        )
    raise AssertionError(f"unsupported fixture status: {status}")


@pytest.mark.parametrize(
    "status",
    [
        "prepared",
        "dispatch_claimed",
        "outcome_unknown",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
        "cancelled",
        "expired_pre_dispatch",
        "failed_pre_dispatch",
    ],
)
def test_v10_import_replays_every_dispatch_and_reservation_state(
    tmp_path,
    status: str,
) -> None:
    path = tmp_path / f"v10-dispatch-{status}.sqlite3"
    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix=f"import-{status}")
        insert_reserved_dispatch(store, prepared)
        expected_dispatch = _advance_dispatch_for_import(
            store,
            session,
            prepared,
            status,
        )
        expected_reservation = store.load_paper_risk_reservation(
            prepared.order_plan_id
        )
        assert expected_reservation is not None
        _assert_store_parity(store)
    downgrade_to_schema(path, target_version=10)

    with paper_store(path) as migrated:
        assert migrated.load_paper_order_dispatch(
            prepared.order_plan_id
        ) == expected_dispatch
        assert migrated.load_paper_risk_reservation(
            prepared.order_plan_id
        ) == expected_reservation
        assert {
            event.event_type
            for event in migrated.list_paper_execution_events()
        } == {
            "LegacyOrderDispatchImported",
            "LegacyRiskReservationImported",
        }
        _assert_store_parity(migrated)


@pytest.mark.parametrize(
    "status",
    [
        "prepared",
        "cancel_claimed",
        "cancel_accepted",
        "cancel_outcome_unknown",
        "reconciled_cancelled",
        "reconciled_filled",
        "rejected",
    ],
)
def test_v10_import_replays_every_cancel_state(tmp_path, status: str) -> None:
    path = tmp_path / f"v10-cancel-{status}.sqlite3"
    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix=f"cancel-import-{status}")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        kill = store.start_paper_kill_operation(
            session=session,
            reason="v10 cancel-state corpus",
            started_at=NOW + timedelta(seconds=4),
        )
        request = store.create_paper_cancel_request(
            make_cancel_request(
                store,
                kill_id=kill.kill_id,
                dispatch=accepted,
                created_at=NOW + timedelta(seconds=5),
            ),
            session=session,
        )
        if status != "prepared":
            request = store.claim_paper_cancel_attempt(
                request.cancel_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=6),
            )
        if status not in {"prepared", "cancel_claimed"}:
            request = _cancel_update(
                store,
                session,
                request,
                status=status,
                at=NOW + timedelta(seconds=7),
            )
        expected = request
        _assert_store_parity(store)
    downgrade_to_schema(path, target_version=10)

    with paper_store(path) as migrated:
        assert migrated.load_paper_cancel_request(expected.cancel_id) == expected
        _assert_store_parity(migrated)


def test_restart_shadow_rebuild_is_read_only_and_has_zero_broker_authority(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "restart-read-only.sqlite3"
    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="restart")
        insert_reserved_dispatch(store, prepared)
        accepted = _accepted(
            store,
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=2),
            ),
        )
        _with_fill_state(
            store,
            accepted,
            status="partially_filled",
            quantities=(2,),
            at=NOW + timedelta(seconds=4),
        )
        before_events = tuple(
            event_canonical_bytes(event)
            for event in store.list_paper_execution_events()
        )
        before_rows = tuple(
            canonical_json_bytes(item)
            for item in [
                *store.list_paper_order_dispatches(),
                *store.list_paper_risk_reservations(),
                *store.list_paper_cancel_requests(),
            ]
        )

    def broker_call_forbidden(*_args, **_kwargs):
        raise AssertionError("shadow replay attempted broker authority")

    broker_methods: tuple[str, ...] = (
        "request_access_token",
        "get_current_price",
        "get_l2",
        "get_balance",
        "get_daily_orders_and_fills",
        "get_cancelable_orders",
        "get_buying_power",
        "place_limit_cash_order",
        "cancel_full_remaining_order",
    )
    for name in broker_methods:
        monkeypatch.setattr(KisPaperClient, name, broker_call_forbidden)

    with paper_store(path) as reopened:
        changes_before = reopened._connection.total_changes  # noqa: SLF001
        assert changes_before == 0
        _assert_store_parity(reopened)
        after_events = tuple(
            event_canonical_bytes(event)
            for event in reopened.list_paper_execution_events()
        )
        after_rows = tuple(
            canonical_json_bytes(item)
            for item in [
                *reopened.list_paper_order_dispatches(),
                *reopened.list_paper_risk_reservations(),
                *reopened.list_paper_cancel_requests(),
            ]
        )
        assert reopened._connection.total_changes == changes_before  # noqa: SLF001

    assert after_events == before_events
    assert after_rows == before_rows
