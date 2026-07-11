"""Deterministic, fake-only fixtures for schema-v11 execution-event tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperDispatchFillEvidence,
    PaperExecutionSession,
    PaperOrderDispatch,
    PaperRiskReservation,
)
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
ACCOUNT_A = "sha256:" + "a" * 64
ACCOUNT_B = "sha256:" + "b" * 64


@dataclass(frozen=True)
class LegacyExecutionCorpus:
    dispatch: PaperOrderDispatch
    reservation: PaperRiskReservation
    cancel: PaperCancelRequest


def paper_store(
    path: str | Path,
    *,
    account: str = ACCOUNT_A,
) -> PaperStateStore:
    return PaperStateStore(
        path,
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=account,
    )


def start_session(
    store: PaperStateStore,
    *,
    started_at: datetime = NOW,
) -> PaperExecutionSession:
    return store.start_paper_execution_session(
        started_at=started_at,
        lease_expires_at=started_at + timedelta(hours=1),
    )


def make_dispatch(
    store: PaperStateStore,
    session: PaperExecutionSession,
    *,
    suffix: str = "001",
    **updates: object,
) -> PaperOrderDispatch:
    prepared_at = NOW + timedelta(seconds=1)
    values: dict[str, object] = {
        "order_plan_id": f"oplan-event-store-{suffix}",
        "broker_order_id": f"bord-event-store-{suffix}",
        "run_id": f"run-event-store-{suffix}",
        "idempotency_key": f"paper-event-store-{suffix}",
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
        "risk_check_id": f"risk-event-store-{suffix}",
        "risk_check_expires_at": NOW + timedelta(minutes=10),
        "submission_evidence_expires_at": NOW + timedelta(minutes=9),
        "reconciled_snapshot_id": f"snapshot-event-store-{suffix}",
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
        "store_id": store.provenance.store_id,
        "session_id": session.session_id,
        "fencing_token": session.fencing_token,
        "account_scope_fingerprint": store.provenance.account_scope_fingerprint,
        "prepared_at": prepared_at,
        "updated_at": prepared_at,
    }
    values.update(updates)
    return PaperOrderDispatch(**values)


def make_reservation(
    dispatch: PaperOrderDispatch,
    **updates: object,
) -> PaperRiskReservation:
    quantity = int(dispatch.quantity)
    notional = quantity * int(dispatch.limit_price)
    current_gross = max(0, int(dispatch.snapshot_equity - dispatch.snapshot_cash))
    values: dict[str, object] = {
        "reservation_id": f"presv-{dispatch.order_plan_id}",
        "order_plan_id": dispatch.order_plan_id,
        "idempotency_key": dispatch.idempotency_key,
        "kind": "cash_buy" if dispatch.side == "buy" else "sell_quantity",
        "symbol": dispatch.symbol,
        "side": dispatch.side,
        "reserved_cash_krw": notional if dispatch.side == "buy" else None,
        "reserved_sell_quantity": quantity if dispatch.side == "sell" else None,
        "reserved_gross_exposure_krw": notional if dispatch.side == "buy" else 0,
        "broker_orderable_cash_basis_krw": (
            int(dispatch.broker_orderable_cash or 0)
            if dispatch.side == "buy"
            else None
        ),
        "broker_orderable_buy_quantity_basis": (
            int(dispatch.broker_orderable_buy_quantity or 0)
            if dispatch.side == "buy"
            else None
        ),
        "snapshot_orderable_quantity_basis": (
            int(dispatch.snapshot_symbol_orderable_quantity)
            if dispatch.side == "sell"
            else None
        ),
        "snapshot_gross_exposure_basis_krw": current_gross,
        "minimum_cash_reserve_krw": dispatch.minimum_cash_reserve_krw or 0,
        "gross_exposure_limit_krw": int(dispatch.snapshot_equity),
        "store_id": dispatch.store_id,
        "session_id": dispatch.session_id,
        "fencing_token": dispatch.fencing_token,
        "account_scope_fingerprint": dispatch.account_scope_fingerprint,
        "created_at": dispatch.prepared_at,
        "updated_at": dispatch.prepared_at,
    }
    values.update(updates)
    return PaperRiskReservation(**values)


def insert_reserved_dispatch(
    store: PaperStateStore,
    dispatch: PaperOrderDispatch,
) -> tuple[PaperOrderDispatch, PaperRiskReservation]:
    return store.reserve_and_insert_paper_order_dispatch(
        dispatch,
        make_reservation(dispatch),
    )


def make_cancel_request(
    store: PaperStateStore,
    *,
    kill_id: str,
    dispatch: PaperOrderDispatch,
    created_at: datetime,
) -> PaperCancelRequest:
    return PaperCancelRequest(
        cancel_id=f"pcancel-{dispatch.order_plan_id}",
        kill_id=kill_id,
        order_plan_id=dispatch.order_plan_id,
        broker_order_id=dispatch.broker_order_id,
        broker_order_reference=dispatch.broker_order_reference,
        broker_forwarding_order_org_number=(
            dispatch.broker_forwarding_order_org_number
        ),
        symbol=dispatch.symbol,
        side=dispatch.side,
        cancelable_quantity=(
            dispatch.quantity - dispatch.cumulative_filled_quantity
        ),
        original_limit_price=dispatch.limit_price,
        store_id=store.provenance.store_id,
        account_scope_fingerprint=store.provenance.account_scope_fingerprint,
        created_at=created_at,
        updated_at=created_at,
    )


def create_v10_legacy_corpus(path: str | Path) -> LegacyExecutionCorpus:
    """Create current rows through public APIs, then strip only schema-v11 facts."""

    with paper_store(path) as store:
        session = start_session(store)
        prepared = make_dispatch(store, session)
        insert_reserved_dispatch(store, prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        accepted_at = NOW + timedelta(seconds=3)
        accepted = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "accepted",
                    "broker_business_date": NOW.date(),
                    "broker_order_reference": "0000001234",
                    "broker_forwarding_order_org_number": "06010",
                    "broker_order_time": "100001",
                    "updated_at": accepted_at,
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        accepted = store.update_paper_order_dispatch(
            accepted,
            mutation_origin="broker_post_result",
        )

        observed_at = NOW + timedelta(seconds=4)
        fills = [
            PaperDispatchFillEvidence(
                broker_fill_reference="fill-event-store-001",
                broker_order_id=accepted.broker_order_id,
                broker_order_reference=accepted.broker_order_reference,
                symbol=accepted.symbol,
                side=accepted.side,
                quantity=1,
                price=accepted.limit_price,
                notional=accepted.limit_price,
                evidence_at=observed_at,
                time_basis="broker_execution",
            ),
            PaperDispatchFillEvidence(
                broker_fill_reference="aggregate-event-store-002",
                broker_order_id=accepted.broker_order_id,
                broker_order_reference=accepted.broker_order_reference,
                symbol=accepted.symbol,
                side=accepted.side,
                quantity=2,
                price=accepted.limit_price,
                notional=2 * accepted.limit_price,
                evidence_at=observed_at,
                time_basis="broker_daily_aggregate_first_observed",
            ),
        ]
        partial = PaperOrderDispatch.model_validate(
            accepted.model_copy(
                update={
                    "status": "partially_filled",
                    "broker_order_branch_number": "00123",
                    "cumulative_filled_quantity": 3,
                    "fill_evidence": fills,
                    "updated_at": observed_at,
                    "revision": accepted.revision + 1,
                }
            ).model_dump()
        )
        partial = store.update_paper_order_dispatch(
            partial,
            mutation_origin="broker_reconciliation",
        )
        reservation = store.load_paper_risk_reservation(partial.order_plan_id)
        assert reservation is not None

        kill = store.start_paper_kill_operation(
            session=session,
            reason="schema-v10 migration fixture",
            started_at=NOW + timedelta(seconds=5),
        )
        cancel = make_cancel_request(
            store,
            kill_id=kill.kill_id,
            dispatch=partial,
            created_at=NOW + timedelta(seconds=6),
        )
        cancel = store.create_paper_cancel_request(cancel, session=session)
        cancel = store.claim_paper_cancel_attempt(
            cancel.cancel_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=7),
        )

    downgrade_to_schema(path, target_version=10)
    return LegacyExecutionCorpus(
        dispatch=partial,
        reservation=reservation,
        cancel=cancel,
    )


def downgrade_to_schema(
    path: str | Path,
    *,
    target_version: int,
) -> None:
    if target_version not in {6, 7, 8, 9, 10}:
        raise ValueError("test downgrade supports only schema v6 through v10")
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TABLE IF EXISTS paper_execution_event_identity_keys")
        connection.execute("DROP TABLE IF EXISTS paper_execution_events")
        if target_version < 9:
            connection.execute("DROP TABLE IF EXISTS paper_cancel_requests")
            connection.execute("DROP TABLE IF EXISTS paper_kill_operations")
        if target_version < 10:
            connection.execute("DROP TABLE IF EXISTS paper_risk_reservations")
            rows = connection.execute(
                "SELECT order_plan_id, state_json FROM paper_order_dispatches"
            ).fetchall()
            for order_plan_id, payload in rows:
                state = json.loads(payload)
                state.pop("minimum_cash_reserve_krw", None)
                connection.execute(
                    "UPDATE paper_order_dispatches SET state_json = ? "
                    "WHERE order_plan_id = ?",
                    (
                        json.dumps(
                            state,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        order_plan_id,
                    ),
                )
        if target_version < 8:
            connection.execute("DROP TABLE IF EXISTS paper_order_dispatches")
            connection.execute("DROP TABLE IF EXISTS paper_execution_sessions")
        if target_version < 7:
            connection.execute("DROP TABLE IF EXISTS paper_portfolio_loss_baselines")
        row = connection.execute(
            "SELECT state_json FROM state_store_metadata WHERE singleton_id = 1"
        ).fetchone()
        if row is None:
            raise AssertionError("fixture database lost state-store metadata")
        provenance = json.loads(row[0])
        provenance["schema_version"] = target_version
        connection.execute(
            "UPDATE state_store_metadata SET schema_version = ?, state_json = ? "
            "WHERE singleton_id = 1",
            (
                target_version,
                json.dumps(
                    provenance,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
        connection.execute(f"PRAGMA user_version = {target_version}")
        connection.commit()
    finally:
        connection.close()
