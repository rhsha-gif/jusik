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


def _event_ids(store: PaperStateStore) -> list[str]:
    return [event.event_id for event in store.list_paper_execution_events()]


def _fail_after_event(
    store: PaperStateStore,
    monkeypatch,
    *,
    event_type: str,
    occurrence: int = 1,
) -> None:
    original_insert = store._insert_paper_execution_event  # noqa: SLF001
    seen = 0

    def insert_then_fail(candidate: PaperExecutionEvent) -> None:
        nonlocal seen
        original_insert(candidate)
        if candidate.event_type == event_type:
            seen += 1
            if seen == occurrence:
                raise sqlite3.IntegrityError(
                    f"injected {event_type} event failure"
                )

    monkeypatch.setattr(
        store,
        "_insert_paper_execution_event",
        insert_then_fail,
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


def test_terminal_enrichment_advances_dispatch_without_second_release(
    tmp_path,
) -> None:
    with paper_store(tmp_path / "terminal-enrichment.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="terminal-enrichment")
        _, reservation = insert_reserved_dispatch(store, prepared)
        claimed = _claim(store, session, prepared)
        rejected = store.update_paper_order_dispatch(
            _broker_rejection(claimed),
            mutation_origin="broker_post_result",
        )
        enriched_at = rejected.updated_at + timedelta(seconds=1)
        enriched = PaperOrderDispatch.model_validate(
            rejected.model_copy(
                update={
                    "broker_order_branch_number": "00123",
                    "last_error_code": None,
                    "updated_at": enriched_at,
                    "reconciled_at": enriched_at,
                    "revision": rejected.revision + 1,
                }
            ).model_dump()
        )
        enriched = store.update_paper_order_dispatch(
            enriched,
            mutation_origin="broker_reconciliation",
        )

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
        assert _signature(dispatch_events)[-1] == (
            4,
            "DispatchEvidenceObserved",
            "broker_reconciliation",
            3,
        )
        assert _signature(reservation_events) == [
            (1, "RiskReserved", "local_prepare", 0),
            (2, "RiskReservationReleased", "broker_acceptance", 1),
        ]
        assert dispatch_events[-1].causation_id == dispatch_events[-2].event_id
        _assert_replay(dispatch_events, enriched)


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
        assert store.create_paper_cancel_request(
            requested,
            session=session,
        ) == persisted
        assert len(_events(store, "cancel_request", requested.cancel_id)) == 1
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

        with pytest.raises(PaperStateConflictError):
            store.create_paper_cancel_request(requested, session=session)
        assert len(_events(store, "cancel_request", requested.cancel_id)) == 3
        divergent_id = PaperCancelRequest.model_validate(
            requested.model_copy(update={"cancel_id": "pcancel-divergent"}).model_dump()
        )
        with pytest.raises(PaperStateConflictError):
            store.create_paper_cancel_request(divergent_id, session=session)
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


def test_reconciliation_acceptance_source_is_pinned(tmp_path) -> None:
    with paper_store(tmp_path / "reconciliation-source.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="reconciliation-source")
        insert_reserved_dispatch(store, prepared)
        claimed = _claim(store, session, prepared)
        reconciled_at = NOW + timedelta(seconds=3)
        accepted = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "accepted",
                    "broker_business_date": NOW.date(),
                    "broker_order_reference": "0000001234",
                    "broker_order_branch_number": "00123",
                    "broker_order_time": "100001",
                    "last_error_code": None,
                    "updated_at": reconciled_at,
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        accepted = store.update_paper_order_dispatch(
            accepted,
            mutation_origin="broker_reconciliation",
        )

        events = _events(store, "order_dispatch", prepared.order_plan_id)
        assert _signature(events)[-1] == (
            3,
            "OrderAccepted",
            "broker_reconciliation",
            2,
        )
        _assert_replay(events, accepted)


def test_cancel_public_no_write_invariants_do_not_advance_stream(tmp_path) -> None:
    with paper_store(tmp_path / "cancel-no-write.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix="cancel-no-write")
        insert_reserved_dispatch(store, prepared)
        accepted_dispatch = _accepted_from_claim(
            store,
            _claim(store, session, prepared),
        )
        kill = store.start_paper_kill_operation(
            session=session,
            reason="cancel no-write test",
            started_at=NOW + timedelta(seconds=4),
        )
        requested = make_cancel_request(
            store,
            kill_id=kill.kill_id,
            dispatch=accepted_dispatch,
            created_at=NOW + timedelta(seconds=5),
        )
        nonzero_create = PaperCancelRequest.model_validate(
            requested.model_copy(update={"revision": 1}).model_dump()
        )
        before_ids = _event_ids(store)
        with pytest.raises(PaperStateConflictError):
            store.create_paper_cancel_request(nonzero_create, session=session)
        assert store.load_paper_cancel_request(requested.cancel_id) is None
        assert _event_ids(store) == before_ids

        persisted = store.create_paper_cancel_request(requested, session=session)
        claimed = store.claim_paper_cancel_attempt(
            persisted.cancel_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=6),
        )
        claimed_event_ids = _event_ids(store)
        assert store.update_paper_cancel_request(
            claimed,
            session=session,
            mutation_origin="kill_cancel_journal",
        ) == claimed
        assert _event_ids(store) == claimed_event_ids

        same_status_change = PaperCancelRequest.model_validate(
            claimed.model_copy(
                update={
                    "last_error_code": "cancel_still_working",
                    "updated_at": NOW + timedelta(seconds=7),
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        with pytest.raises(PaperStateConflictError):
            store.update_paper_cancel_request(
                same_status_change,
                session=session,
                mutation_origin="kill_cancel_journal",
            )
        assert store.load_paper_cancel_request(claimed.cancel_id) == claimed
        assert _event_ids(store) == claimed_event_ids


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


@pytest.mark.parametrize("phase", ["prepare", "claim"])
def test_event_failure_rolls_back_prepare_or_claim(
    tmp_path,
    monkeypatch,
    phase: str,
) -> None:
    with paper_store(tmp_path / f"{phase}-event-failure.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix=f"{phase}-failure")
        reservation = make_reservation(prepared)
        if phase == "prepare":
            _fail_after_event(store, monkeypatch, event_type="OrderPrepared")
            with pytest.raises(PaperStateConflictError):
                store.reserve_and_insert_paper_order_dispatch(
                    prepared,
                    reservation,
                )
            assert store.load_paper_order_dispatch(prepared.order_plan_id) is None
            assert store.load_paper_risk_reservation(prepared.order_plan_id) is None
            assert store.list_paper_execution_events() == []
            return

        insert_reserved_dispatch(store, prepared)
        before_ids = _event_ids(store)
        _fail_after_event(store, monkeypatch, event_type="DispatchClaimed")
        with pytest.raises(PaperStateConflictError):
            _claim(store, session, prepared)
        assert store.load_paper_order_dispatch(prepared.order_plan_id) == prepared
        assert store.load_paper_risk_reservation(prepared.order_plan_id) is not None
        assert _event_ids(store) == before_ids


def test_event_failure_rolls_back_takeover_pair(
    tmp_path,
    monkeypatch,
) -> None:
    with paper_store(tmp_path / "takeover-event-failure.sqlite3") as store:
        predecessor = start_session(store)
        prepared = make_dispatch(store, predecessor, suffix="takeover-failure")
        _, reservation = insert_reserved_dispatch(store, prepared)
        successor = start_session(
            store,
            started_at=NOW + timedelta(hours=2),
        )
        before_ids = _event_ids(store)
        _fail_after_event(
            store,
            monkeypatch,
            event_type="RiskReservationFenceRebound",
        )

        with pytest.raises(PaperStateConflictError):
            store.takeover_prepared_paper_order_dispatch(
                prepared.order_plan_id,
                session=successor,
                taken_over_at=NOW + timedelta(hours=2, seconds=1),
            )

        assert store.load_paper_order_dispatch(prepared.order_plan_id) == prepared
        assert store.load_paper_risk_reservation(prepared.order_plan_id) == reservation
        assert _event_ids(store) == before_ids


def test_event_failure_rolls_back_multi_dispatch_recovery(
    tmp_path,
    monkeypatch,
) -> None:
    with paper_store(tmp_path / "recovery-event-failure.sqlite3") as store:
        predecessor = start_session(store)
        buy = make_dispatch(
            store,
            predecessor,
            suffix="recovery-failure-buy",
            broker_orderable_cash=2_000_000,
            broker_orderable_buy_quantity=30,
        )
        protective_sell = make_dispatch(
            store,
            predecessor,
            suffix="recovery-failure-sell",
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
        claimed_buy = _claim(store, predecessor, buy)
        claimed_sell = store.claim_dispatch_attempt(
            protective_sell.order_plan_id,
            session=predecessor,
            claimed_at=NOW + timedelta(seconds=3),
        )
        successor = start_session(
            store,
            started_at=NOW + timedelta(hours=2),
        )
        before_ids = _event_ids(store)
        _fail_after_event(
            store,
            monkeypatch,
            event_type="OutcomeUnknown",
            occurrence=2,
        )

        with pytest.raises(PaperStateConflictError):
            store.recover_interrupted_dispatches(
                session=successor,
                recovered_at=NOW + timedelta(hours=2, seconds=1),
            )

        assert store.load_paper_order_dispatch(buy.order_plan_id) == claimed_buy
        assert (
            store.load_paper_order_dispatch(protective_sell.order_plan_id)
            == claimed_sell
        )
        assert _event_ids(store) == before_ids


@pytest.mark.parametrize(
    ("phase", "failed_event_type"),
    [
        ("create", "CancelPrepared"),
        ("claim", "CancelClaimed"),
        ("update", "CancelAccepted"),
    ],
)
def test_event_failure_rolls_back_cancel_mutations(
    tmp_path,
    monkeypatch,
    phase: str,
    failed_event_type: str,
) -> None:
    with paper_store(tmp_path / f"cancel-{phase}-failure.sqlite3") as store:
        session = start_session(store)
        prepared = make_dispatch(store, session, suffix=f"cancel-{phase}-failure")
        insert_reserved_dispatch(store, prepared)
        accepted_dispatch = _accepted_from_claim(
            store,
            _claim(store, session, prepared),
        )
        kill = store.start_paper_kill_operation(
            session=session,
            reason="cancel fault test",
            started_at=NOW + timedelta(seconds=4),
        )
        requested = make_cancel_request(
            store,
            kill_id=kill.kill_id,
            dispatch=accepted_dispatch,
            created_at=NOW + timedelta(seconds=5),
        )

        if phase == "create":
            _fail_after_event(store, monkeypatch, event_type=failed_event_type)
            with pytest.raises(PaperStateConflictError):
                store.create_paper_cancel_request(requested, session=session)
            assert store.load_paper_cancel_request(requested.cancel_id) is None
            assert _events(store, "cancel_request", requested.cancel_id) == []
            return

        persisted = store.create_paper_cancel_request(requested, session=session)
        if phase == "claim":
            before_ids = _event_ids(store)
            _fail_after_event(store, monkeypatch, event_type=failed_event_type)
            with pytest.raises(PaperStateConflictError):
                store.claim_paper_cancel_attempt(
                    persisted.cancel_id,
                    session=session,
                    claimed_at=NOW + timedelta(seconds=6),
                )
            assert store.load_paper_cancel_request(persisted.cancel_id) == persisted
            assert _event_ids(store) == before_ids
            return

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
        before_ids = _event_ids(store)
        _fail_after_event(store, monkeypatch, event_type=failed_event_type)
        with pytest.raises(PaperStateConflictError):
            store.update_paper_cancel_request(
                accepted,
                session=session,
                mutation_origin="kill_cancel_journal",
            )
        assert store.load_paper_cancel_request(claimed.cancel_id) == claimed
        assert _event_ids(store) == before_ids
