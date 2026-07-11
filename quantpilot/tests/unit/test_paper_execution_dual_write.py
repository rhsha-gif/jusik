"""Public-path coverage for schema-v11 paper execution dual-write."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from quantpilot.packages.core.execution.events import PaperExecutionEvent
from quantpilot.packages.core.execution.reducer import replay_paper_execution_events
from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperOrderDispatch,
)
from quantpilot.packages.db.sqlite_repositories import (
    PaperStateConflictError,
    PaperStateCorruptionError,
    PaperStateStore,
)
from quantpilot.tests.paper_execution_event_store_fixtures import (
    NOW,
    insert_reserved_dispatch,
    make_cancel_request,
    make_dispatch,
    make_reservation,
    paper_store,
    start_session,
)


def _events(
    store: PaperStateStore,
    aggregate_type: str,
    aggregate_id: str,
) -> list[PaperExecutionEvent]:
    return store.list_paper_execution_events(  # type: ignore[arg-type]
        aggregate_type,
        aggregate_id,
    )


def _signature(events: list[PaperExecutionEvent]) -> list[tuple[int, str, str, int]]:
    return [
        (
            event.aggregate_version,
            event.event_type,
            event.source,
            event.source_revision,
        )
        for event in events
    ]


def _assert_replay(events: list[PaperExecutionEvent], expected_after: object) -> None:
    assert events
    projection = replay_paper_execution_events(events)
    assert projection.after == expected_after


def _claim(
    store: PaperStateStore,
    session,
    prepared: PaperOrderDispatch,
) -> PaperOrderDispatch:
    return store.claim_dispatch_attempt(
        prepared.order_plan_id,
        session=session,
        claimed_at=NOW + timedelta(seconds=2),
    )


def _accepted_from_claim(
    store: PaperStateStore,
    claimed: PaperOrderDispatch,
) -> PaperOrderDispatch:
    accepted_at = NOW + timedelta(seconds=3)
    accepted = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "accepted",
                "broker_business_date": NOW.date(),
                "broker_order_reference": "0000001234",
                "broker_forwarding_order_org_number": "06010",
                "broker_order_time": "100001",
                "last_error_code": None,
                "updated_at": accepted_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    return store.update_paper_order_dispatch(
        accepted,
        mutation_origin="broker_post_result",
    )


def _broker_rejection(claimed: PaperOrderDispatch) -> PaperOrderDispatch:
    rejected_at = NOW + timedelta(seconds=3)
    return PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "rejected",
                "reconciliation_status": "reconciled",
                "last_error_code": "broker_business_rejected",
                "updated_at": rejected_at,
                "reconciled_at": rejected_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )


def test_prepare_claim_streams_are_exact_and_replay_matches_rows(tmp_path) -> None:
    with paper_store(tmp_path / "prepare-claim.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="prepare-claim")
        reservation = make_reservation(prepared)

        persisted_dispatch, persisted_reservation = (
            store.reserve_and_insert_paper_order_dispatch(prepared, reservation)
        )
        first_event_ids = [
            event.event_id for event in store.list_paper_execution_events()
        ]
        assert store.reserve_and_insert_paper_order_dispatch(
            prepared,
            reservation,
        ) == (persisted_dispatch, persisted_reservation)
        assert [
            event.event_id for event in store.list_paper_execution_events()
        ] == first_event_ids

        claimed = _claim(store, session, prepared)
        dispatch_events = _events(
            store,
            "order_dispatch",
            prepared.order_plan_id,
        )
        reservation_events = _events(
            store,
            "risk_reservation",
            reservation.reservation_id,
        )

        assert _signature(reservation_events) == [
            (1, "RiskReserved", "local_prepare", 0),
        ]
        assert _signature(dispatch_events) == [
            (1, "OrderPrepared", "local_prepare", 0),
            (2, "DispatchClaimed", "local_dispatch_claim", 1),
        ]
        assert reservation_events[0].causation_id is None
        assert dispatch_events[0].causation_id == reservation_events[0].event_id
        assert dispatch_events[1].causation_id == dispatch_events[0].event_id
        assert dispatch_events[0].received_at == prepared.updated_at
        assert dispatch_events[1].received_at == claimed.updated_at
        assert dispatch_events[1].occurred_at == claimed.updated_at
        _assert_replay(dispatch_events, claimed)
        _assert_replay(reservation_events, persisted_reservation)


def test_takeover_emits_one_paired_rebound_and_same_session_is_noop(tmp_path) -> None:
    with paper_store(tmp_path / "takeover.sqlite3") as store:
        predecessor = start_session(store)
        prepared = make_dispatch(store, predecessor, suffix="takeover")
        _, reservation = insert_reserved_dispatch(store, prepared)
        successor = start_session(
            store,
            started_at=NOW + timedelta(hours=2),
        )

        rebound = store.takeover_prepared_paper_order_dispatch(
            prepared.order_plan_id,
            session=successor,
            taken_over_at=NOW + timedelta(hours=2, seconds=1),
        )
        rebound_reservation = store.load_paper_risk_reservation(
            prepared.order_plan_id
        )
        assert rebound_reservation is not None
        event_ids = [event.event_id for event in store.list_paper_execution_events()]

        same = store.takeover_prepared_paper_order_dispatch(
            prepared.order_plan_id,
            session=successor,
            taken_over_at=NOW + timedelta(hours=2, seconds=2),
        )
        assert same == rebound
        assert [event.event_id for event in store.list_paper_execution_events()] == event_ids

        dispatch_events = _events(
            store,
            "order_dispatch",
            prepared.order_plan_id,
        )
        reservation_events = _events(
            store,
            "risk_reservation",
            reservation.reservation_id,
        )
        assert _signature(dispatch_events) == [
            (1, "OrderPrepared", "local_prepare", 0),
            (2, "DispatchFenceRebound", "local_session_takeover", 1),
        ]
        assert _signature(reservation_events) == [
            (1, "RiskReserved", "local_prepare", 0),
            (2, "RiskReservationFenceRebound", "local_session_takeover", 1),
        ]
        assert dispatch_events[1].causation_id == dispatch_events[0].event_id
        assert reservation_events[1].causation_id == dispatch_events[1].event_id
        _assert_replay(dispatch_events, rebound)
        _assert_replay(reservation_events, rebound_reservation)


def test_terminal_dispatch_and_reservation_release_are_one_paired_fact(
    tmp_path,
) -> None:
    with paper_store(tmp_path / "terminal-release.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="terminal")
        _, reservation = insert_reserved_dispatch(store, prepared)
        claimed = _claim(store, session, prepared)
        rejected = store.update_paper_order_dispatch(
            _broker_rejection(claimed),
            mutation_origin="broker_post_result",
        )
        released = store.load_paper_risk_reservation(prepared.order_plan_id)
        assert released is not None
        assert released.status == "released_rejected"

        event_ids = [event.event_id for event in store.list_paper_execution_events()]
        assert store.update_paper_order_dispatch(
            rejected,
            mutation_origin="broker_post_result",
        ) == rejected
        assert [event.event_id for event in store.list_paper_execution_events()] == event_ids

        dispatch_events = _events(
            store,
            "order_dispatch",
            prepared.order_plan_id,
        )
        reservation_events = _events(
            store,
            "risk_reservation",
            reservation.reservation_id,
        )
        assert _signature(dispatch_events) == [
            (1, "OrderPrepared", "local_prepare", 0),
            (2, "DispatchClaimed", "local_dispatch_claim", 1),
            (3, "OrderRejected", "broker_acceptance", 2),
        ]
        assert _signature(reservation_events) == [
            (1, "RiskReserved", "local_prepare", 0),
            (2, "RiskReservationReleased", "broker_acceptance", 1),
        ]
        assert dispatch_events[2].causation_id == dispatch_events[1].event_id
        assert reservation_events[1].causation_id == dispatch_events[2].event_id
        _assert_replay(dispatch_events, rejected)
        _assert_replay(reservation_events, released)


def test_cancel_create_claim_and_accept_emit_one_closed_stream(tmp_path) -> None:
    with paper_store(tmp_path / "cancel.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="cancel")
        _, reservation = insert_reserved_dispatch(store, prepared)
        accepted_dispatch = _accepted_from_claim(
            store,
            _claim(store, session, prepared),
        )
        kill = store.start_paper_kill_operation(
            session=session,
            reason="dual-write cancel test",
            started_at=NOW + timedelta(seconds=4),
        )
        requested = make_cancel_request(
            store,
            kill_id=kill.kill_id,
            dispatch=accepted_dispatch,
            created_at=NOW + timedelta(seconds=5),
        )
        persisted = store.create_paper_cancel_request(requested, session=session)
        claimed = store.claim_paper_cancel_attempt(
            persisted.cancel_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=6),
        )
        accepted = PaperCancelRequest.model_validate(
            claimed.model_copy(
                update={
                    "status": "cancel_accepted",
                    "response_order_reference": "0000005678",
                    "updated_at": NOW + timedelta(seconds=7),
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        accepted = store.update_paper_cancel_request(
            accepted,
            session=session,
            mutation_origin="kill_cancel_journal",
        )

        events = _events(store, "cancel_request", requested.cancel_id)
        assert _signature(events) == [
            (1, "CancelPrepared", "kill_cancel", 0),
            (2, "CancelClaimed", "kill_cancel", 1),
            (3, "CancelAccepted", "kill_cancel", 2),
        ]
        assert events[0].causation_id is None
        assert events[1].causation_id == events[0].event_id
        assert events[2].causation_id == events[1].event_id
        _assert_replay(events, accepted)

        existing = store.create_paper_cancel_request(requested, session=session)
        assert existing == accepted
        assert len(_events(store, "cancel_request", requested.cancel_id)) == 3
        held = store.load_paper_risk_reservation(prepared.order_plan_id)
        assert held is not None
        assert held.reservation_id == reservation.reservation_id
        assert held.status == "held"


def test_multi_dispatch_recovery_emits_one_event_per_changed_stream(
    tmp_path,
) -> None:
    with paper_store(tmp_path / "multi-recovery.sqlite3") as store:
        predecessor = start_session(store)
        buy = make_dispatch(
            store,
            predecessor,
            suffix="recovery-buy",
            broker_orderable_cash=2_000_000,
            broker_orderable_buy_quantity=30,
        )
        protective_sell = make_dispatch(
            store,
            predecessor,
            suffix="recovery-sell",
            side="sell",
            purpose="protective_exit",
            quantity=3,
            snapshot_symbol_quantity=10,
            snapshot_symbol_orderable_quantity=10,
            broker_orderable_cash=None,
            broker_orderable_buy_quantity=None,
        )
        insert_reserved_dispatch(store, buy)
        insert_reserved_dispatch(store, protective_sell)
        _claim(store, predecessor, buy)
        store.claim_dispatch_attempt(
            protective_sell.order_plan_id,
            session=predecessor,
            claimed_at=NOW + timedelta(seconds=3),
        )
        successor = start_session(
            store,
            started_at=NOW + timedelta(hours=2),
        )

        recovered = store.recover_interrupted_dispatches(
            session=successor,
            recovered_at=NOW + timedelta(hours=2, seconds=1),
        )
        assert {dispatch.order_plan_id for dispatch in recovered} == {
            buy.order_plan_id,
            protective_sell.order_plan_id,
        }
        event_ids = [event.event_id for event in store.list_paper_execution_events()]
        assert store.recover_interrupted_dispatches(
            session=successor,
            recovered_at=NOW + timedelta(hours=2, seconds=2),
        ) == []
        assert [event.event_id for event in store.list_paper_execution_events()] == event_ids

        for recovered_dispatch in recovered:
            events = _events(
                store,
                "order_dispatch",
                recovered_dispatch.order_plan_id,
            )
            assert _signature(events) == [
                (1, "OrderPrepared", "local_prepare", 0),
                (2, "DispatchClaimed", "local_dispatch_claim", 1),
                (3, "OutcomeUnknown", "process_recovery", 2),
            ]
            assert events[2].causation_id == events[1].event_id
            _assert_replay(events, recovered_dispatch)


def test_wrong_origin_rolls_back_authoritative_row_and_event(tmp_path) -> None:
    with paper_store(tmp_path / "wrong-origin.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="wrong-origin")
        _, reservation = insert_reserved_dispatch(store, prepared)
        claimed = _claim(store, session, prepared)
        accepted = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "accepted",
                    "broker_business_date": NOW.date(),
                    "broker_order_reference": "0000001234",
                    "broker_forwarding_order_org_number": "06010",
                    "broker_order_time": "100001",
                    "updated_at": NOW + timedelta(seconds=3),
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        before_ids = [event.event_id for event in store.list_paper_execution_events()]

        with pytest.raises(PaperStateCorruptionError):
            store.update_paper_order_dispatch(
                accepted,
                mutation_origin="local_submission_guard",
            )

        assert store.load_paper_order_dispatch(prepared.order_plan_id) == claimed
        held = store.load_paper_risk_reservation(prepared.order_plan_id)
        assert held is not None
        assert held.reservation_id == reservation.reservation_id
        assert held.status == "held"
        assert [event.event_id for event in store.list_paper_execution_events()] == before_ids


def test_event_failure_rolls_back_terminal_dispatch_release_and_both_events(
    tmp_path,
    monkeypatch,
) -> None:
    with paper_store(tmp_path / "terminal-event-failure.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="terminal-failure")
        _, reservation = insert_reserved_dispatch(store, prepared)
        claimed = _claim(store, session, prepared)
        rejected = _broker_rejection(claimed)
        before_ids = [event.event_id for event in store.list_paper_execution_events()]
        original_insert = store._insert_paper_execution_event  # noqa: SLF001

        def insert_then_fail(candidate: PaperExecutionEvent) -> None:
            original_insert(candidate)
            if candidate.event_type == "RiskReservationReleased":
                raise sqlite3.IntegrityError("injected release-event failure")

        monkeypatch.setattr(
            store,
            "_insert_paper_execution_event",
            insert_then_fail,
        )
        with pytest.raises(PaperStateConflictError):
            store.update_paper_order_dispatch(
                rejected,
                mutation_origin="broker_post_result",
            )

        assert store.load_paper_order_dispatch(prepared.order_plan_id) == claimed
        held = store.load_paper_risk_reservation(prepared.order_plan_id)
        assert held is not None
        assert held.reservation_id == reservation.reservation_id
        assert held.status == "held"
        assert [event.event_id for event in store.list_paper_execution_events()] == before_ids
