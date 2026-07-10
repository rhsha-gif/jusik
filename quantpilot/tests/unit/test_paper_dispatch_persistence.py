from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.operator.position_ledger import (
    PaperDispatchFillEvidence,
    PaperExecutionSession,
    PaperOrderDispatch,
    PaperRunCheckpoint,
)
from quantpilot.packages.db.sqlite_repositories import (
    PAPER_STATE_SCHEMA_VERSION,
    PaperStateConflictError,
    PaperStateMigrationRequired,
    PaperStateProvenanceError,
    PaperStateStore,
)


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
ACCOUNT_A = "sha256:" + "a" * 64
ACCOUNT_B = "sha256:" + "b" * 64


def _paper_store(path, *, account: str = ACCOUNT_A) -> PaperStateStore:
    return PaperStateStore(
        path,
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=account,
    )


def _session(
    store: PaperStateStore,
    *,
    started_at: datetime = NOW,
    lease_expires_at: datetime | None = None,
) -> PaperExecutionSession:
    return store.start_paper_execution_session(
        started_at=started_at,
        lease_expires_at=lease_expires_at or started_at + timedelta(minutes=5),
    )


def _dispatch(
    store: PaperStateStore,
    session: PaperExecutionSession,
    **updates: object,
) -> PaperOrderDispatch:
    prepared_at = NOW + timedelta(seconds=1)
    values: dict[str, object] = {
        "order_plan_id": "oplan-paper-001",
        "run_id": "run-paper-001",
        "idempotency_key": "paper-order-001",
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
        "entry_atr14": 1_200.0,
        "store_id": store.provenance.store_id,
        "session_id": session.session_id,
        "fencing_token": session.fencing_token,
        "account_scope_fingerprint": ACCOUNT_A,
        "prepared_at": prepared_at,
        "updated_at": prepared_at,
    }
    values.update(updates)
    return PaperOrderDispatch(**values)


def _sell_dispatch(
    store: PaperStateStore,
    session: PaperExecutionSession,
    *,
    order_plan_id: str,
    idempotency_key: str,
    purpose: str,
    prepared_at: datetime | None = None,
) -> PaperOrderDispatch:
    prepared = prepared_at or NOW + timedelta(seconds=1)
    return _dispatch(
        store,
        session,
        order_plan_id=order_plan_id,
        idempotency_key=idempotency_key,
        request_fingerprint="sha256:" + "d" * 64,
        side="sell",
        purpose=purpose,
        quantity=3.0,
        broker_orderable_cash=None,
        broker_orderable_buy_quantity=None,
        entry_atr14=None,
        prepared_at=prepared,
        updated_at=prepared,
    )


def test_store_provenance_is_immutable_across_reopen(tmp_path) -> None:
    path = tmp_path / "fixture.sqlite3"
    with PaperStateStore(path) as store:
        provenance = store.provenance
        assert provenance.data_mode == "fixture"
        assert provenance.broker_environment == "fixture_mock"
        assert provenance.account_scope_fingerprint is None
        assert provenance.schema_version == PAPER_STATE_SCHEMA_VERSION

    with PaperStateStore(path) as reopened:
        assert reopened.provenance == provenance

    with pytest.raises(PaperStateProvenanceError, match="does not match"):
        _paper_store(path)


def test_paper_store_rejects_account_environment_and_fixture_seed_mismatch(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        assert store.provenance.account_scope_fingerprint == ACCOUNT_A

    with pytest.raises(PaperStateProvenanceError, match="does not match"):
        _paper_store(path, account=ACCOUNT_B)
    with pytest.raises(PaperStateProvenanceError, match="invalid"):
        PaperStateStore(
            path,
            data_mode="paper_trading",
            broker_environment="fixture_mock",
            account_scope_fingerprint=ACCOUNT_A,
        )
    with pytest.raises(PaperStateProvenanceError, match="seeding"):
        PaperStateStore(
            path,
            allow_fixture_seed=True,
            data_mode="paper_trading",
            account_scope_fingerprint=ACCOUNT_A,
        )


def test_populated_legacy_database_cannot_be_promoted_to_paper(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA user_version = 5")
        connection.execute(
            "CREATE TABLE legacy_operator_state (state_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO legacy_operator_state VALUES (?)",
            ('{"source":"fixture"}',),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperStateMigrationRequired, match="cannot be promoted"):
        _paper_store(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute(
            "SELECT COUNT(*) FROM legacy_operator_state"
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'state_store_metadata'
            """
        ).fetchone() is None
    finally:
        connection.close()


def test_run_checkpoint_data_mode_must_match_store(tmp_path) -> None:
    checkpoint = PaperRunCheckpoint(
        run_id="run-mode-001",
        idempotency_key="run-mode-key-001",
        policy_id="policy-paper",
        user_id="local-user",
        policy_version=3,
        run_mode="paper_submit",
        requested_at=NOW,
        request_fingerprint="sha256:" + "d" * 64,
        status="started",
        data_mode="paper_trading",
        started_at=NOW,
        updated_at=NOW,
    )
    with PaperStateStore(tmp_path / "fixture.sqlite3") as fixture:
        with pytest.raises(PaperStateProvenanceError, match="data mode"):
            fixture.insert_run_checkpoint(checkpoint)
    with _paper_store(tmp_path / "paper.sqlite3") as paper:
        assert paper.insert_run_checkpoint(checkpoint) == checkpoint


def test_session_fence_is_exact_and_tokens_advance(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        first = _session(store)
        prepared = _dispatch(store, first)
        store.insert_paper_order_dispatch(prepared)
        with pytest.raises(PaperStateConflictError, match="unexpired"):
            _session(store, started_at=NOW + timedelta(minutes=1))

        second = _session(
            store,
            started_at=NOW + timedelta(minutes=6),
            lease_expires_at=NOW + timedelta(minutes=11),
        )
        assert second.fencing_token == first.fencing_token + 1
        assert store.load_paper_execution_session(first.session_id).status == "abandoned"  # type: ignore[union-attr]
        with pytest.raises(PaperStateConflictError, match="ownership changed"):
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=first,
                claimed_at=NOW + timedelta(minutes=6, seconds=1),
            )


def test_session_renewal_rejects_stale_owner_and_expired_lease(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as first, _paper_store(path) as second:
        session = _session(first)
        stale_copy = second.load_paper_execution_session(session.session_id)
        assert stale_copy == session

        renewed = first.renew_paper_execution_session(
            session,
            renewed_at=NOW + timedelta(minutes=1),
            lease_expires_at=NOW + timedelta(minutes=10),
        )
        assert renewed.fencing_token == session.fencing_token
        assert renewed.revision == session.revision + 1
        with pytest.raises(PaperStateConflictError, match="ownership changed"):
            second.renew_paper_execution_session(
                stale_copy,  # type: ignore[arg-type]
                renewed_at=NOW + timedelta(minutes=2),
                lease_expires_at=NOW + timedelta(minutes=11),
            )
        with pytest.raises(PaperStateConflictError, match="not active"):
            first.renew_paper_execution_session(
                renewed,
                renewed_at=NOW + timedelta(minutes=10),
                lease_expires_at=NOW + timedelta(minutes=15),
            )


def test_successor_can_take_over_only_unattempted_prepared_dispatch(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        predecessor = _session(store)
        prepared = _dispatch(store, predecessor)
        store.insert_paper_order_dispatch(prepared)

        successor = _session(
            store,
            started_at=NOW + timedelta(minutes=6),
            lease_expires_at=NOW + timedelta(minutes=11),
        )
        rebound = store.takeover_prepared_paper_order_dispatch(
            prepared.order_plan_id,
            session=successor,
            taken_over_at=NOW + timedelta(minutes=6, seconds=1),
        )
        assert rebound.session_id == successor.session_id
        assert rebound.fencing_token == successor.fencing_token
        assert rebound.attempt_count == 0
        assert rebound.status == "prepared"
        assert rebound.revision == prepared.revision + 1
        excluded = {"session_id", "fencing_token", "updated_at", "revision"}
        assert rebound.model_dump(exclude=excluded) == prepared.model_dump(
            exclude=excluded
        )

        claimed = store.claim_dispatch_attempt(
            rebound.order_plan_id,
            session=successor,
            claimed_at=NOW + timedelta(minutes=6, seconds=2),
        )
        next_successor = _session(
            store,
            started_at=NOW + timedelta(minutes=12),
            lease_expires_at=NOW + timedelta(minutes=17),
        )
        with pytest.raises(PaperStateConflictError, match="only an unattempted"):
            store.takeover_prepared_paper_order_dispatch(
                claimed.order_plan_id,
                session=next_successor,
                taken_over_at=NOW + timedelta(minutes=12, seconds=1),
            )


def test_prepare_is_exactly_idempotent_and_divergent_key_conflicts(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        assert store.insert_paper_order_dispatch(prepared) == prepared
        assert store.insert_paper_order_dispatch(prepared) == prepared

        divergent = prepared.model_copy(
            update={
                "order_plan_id": "oplan-paper-002",
                "quantity": 11.0,
            }
        )
        with pytest.raises(PaperStateConflictError, match="different evidence"):
            store.insert_paper_order_dispatch(
                PaperOrderDispatch.model_validate(divergent.model_dump())
            )

        duplicate_internal_broker_id = prepared.model_copy(
            update={
                "order_plan_id": "oplan-paper-003",
                "idempotency_key": "paper-order-003",
                "request_fingerprint": "sha256:" + "e" * 64,
            }
        )
        with pytest.raises(PaperStateConflictError, match="already exists"):
            store.insert_paper_order_dispatch(
                PaperOrderDispatch.model_validate(
                    duplicate_internal_broker_id.model_dump()
                )
            )


def test_buy_and_sell_orderability_evidence_fails_closed(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        buy = _dispatch(store, session)
        for updates in (
            {"broker_orderable_cash": 699_999.0},
            {"broker_orderable_buy_quantity": 9.0},
            {"broker_orderable_cash": None},
        ):
            with pytest.raises(ValidationError, match="broker-orderable|orderability"):
                PaperOrderDispatch.model_validate(
                    buy.model_copy(update=updates).model_dump()
                )

        sell = PaperOrderDispatch.model_validate(
            buy.model_copy(
                update={
                    "side": "sell",
                    "purpose": "protective_exit",
                    "quantity": 3.0,
                    "broker_orderable_cash": None,
                    "broker_orderable_buy_quantity": None,
                    "entry_atr14": None,
                }
            ).model_dump()
        )
        assert sell.quantity <= sell.snapshot_symbol_orderable_quantity
        with pytest.raises(ValidationError, match="orderable quantity"):
            PaperOrderDispatch.model_validate(
                sell.model_copy(update={"quantity": 5.0}).model_dump()
            )

def test_two_connections_allow_only_one_dispatch_claim(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as first, _paper_store(path) as second:
        session = _session(first)
        second_session = second.load_paper_execution_session(session.session_id)
        assert second_session == session
        prepared = _dispatch(first, session)
        first.insert_paper_order_dispatch(prepared)

        claimed = first.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        assert claimed.status == "dispatch_claimed"
        assert claimed.attempt_count == 1
        with pytest.raises(PaperStateConflictError, match="only external attempt"):
            second.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=second_session,  # type: ignore[arg-type]
                claimed_at=NOW + timedelta(seconds=3),
            )


@pytest.mark.parametrize("purpose", ["protective_exit", "strategy_retirement"])
def test_restart_allows_verified_risk_reducing_sell_past_unknown_buy(
    tmp_path,
    purpose: str,
) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        predecessor = _session(store)
        buy = _dispatch(store, predecessor)
        store.insert_paper_order_dispatch(buy)
        store.claim_dispatch_attempt(
            buy.order_plan_id,
            session=predecessor,
            claimed_at=NOW + timedelta(seconds=2),
        )

    with _paper_store(path) as reopened:
        successor = _session(
            reopened,
            started_at=NOW + timedelta(minutes=6),
            lease_expires_at=NOW + timedelta(minutes=11),
        )
        recovered = reopened.recover_interrupted_dispatches(
            session=successor,
            recovered_at=NOW + timedelta(minutes=6, seconds=1),
        )
        assert [item.status for item in recovered] == ["outcome_unknown"]

        prepared_at = NOW + timedelta(minutes=6, seconds=2)
        protective_sell = _sell_dispatch(
            reopened,
            successor,
            order_plan_id=f"oplan-{purpose}",
            idempotency_key=f"paper-order-{purpose}",
            purpose=purpose,
            prepared_at=prepared_at,
        )
        reopened.insert_paper_order_dispatch(protective_sell)
        claimed = reopened.claim_dispatch_attempt(
            protective_sell.order_plan_id,
            session=successor,
            claimed_at=prepared_at + timedelta(seconds=1),
        )

        assert claimed.status == "dispatch_claimed"
        assert claimed.attempt_count == 1
        assert claimed.quantity <= claimed.snapshot_symbol_orderable_quantity
        unknown_buy = reopened.load_paper_order_dispatch(buy.order_plan_id)
        assert unknown_buy is not None
        assert unknown_buy.status == "outcome_unknown"
        assert unknown_buy.attempt_count == 1


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_unknown_buy_still_blocks_ordinary_rebalance_claims(tmp_path, side: str) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        buy = _dispatch(store, session)
        store.insert_paper_order_dispatch(buy)
        store.claim_dispatch_attempt(
            buy.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        if side == "buy":
            ordinary = _dispatch(
                store,
                session,
                order_plan_id="oplan-ordinary-buy",
                idempotency_key="paper-order-ordinary-buy",
                request_fingerprint="sha256:" + "e" * 64,
            )
        else:
            ordinary = _sell_dispatch(
                store,
                session,
                order_plan_id="oplan-ordinary-sell",
                idempotency_key="paper-order-ordinary-sell",
                purpose="rebalance",
            )
        store.insert_paper_order_dispatch(ordinary)

        with pytest.raises(PaperStateConflictError, match="unresolved"):
            store.claim_dispatch_attempt(
                ordinary.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=3),
            )
        assert store.load_paper_order_dispatch(ordinary.order_plan_id) == ordinary


@pytest.mark.parametrize(
    ("side", "purpose"),
    [
        ("buy", "rebalance"),
        ("sell", "rebalance"),
        ("sell", "protective_exit"),
        ("sell", "strategy_retirement"),
    ],
)
def test_unresolved_sell_blocks_every_new_dispatch_claim(
    tmp_path,
    side: str,
    purpose: str,
) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        unresolved_sell = _sell_dispatch(
            store,
            session,
            order_plan_id="oplan-unresolved-sell",
            idempotency_key="paper-order-unresolved-sell",
            purpose="protective_exit",
        )
        store.insert_paper_order_dispatch(unresolved_sell)
        store.claim_dispatch_attempt(
            unresolved_sell.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )

        if side == "buy":
            candidate = _dispatch(
                store,
                session,
                order_plan_id="oplan-candidate-buy",
                idempotency_key="paper-order-candidate-buy",
                request_fingerprint="sha256:" + "f" * 64,
            )
        else:
            candidate = _sell_dispatch(
                store,
                session,
                order_plan_id=f"oplan-candidate-{purpose}",
                idempotency_key=f"paper-order-candidate-{purpose}",
                purpose=purpose,
            )
        store.insert_paper_order_dispatch(candidate)

        with pytest.raises(PaperStateConflictError, match="unresolved"):
            store.claim_dispatch_attempt(
                candidate.order_plan_id,
                session=session,
                claimed_at=NOW + timedelta(seconds=3),
            )
        assert store.load_paper_order_dispatch(candidate.order_plan_id) == candidate


def test_dispatch_claim_rejects_at_submission_evidence_expiry(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        prepared = _dispatch(
            store,
            session,
            submission_evidence_expires_at=NOW + timedelta(minutes=2),
        )
        store.insert_paper_order_dispatch(prepared)

        with pytest.raises(PaperStateConflictError, match="submission evidence expired"):
            store.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=session,
                claimed_at=prepared.submission_evidence_expires_at,
            )
        unchanged = store.load_paper_order_dispatch(prepared.order_plan_id)
        assert unchanged == prepared


def test_restart_converts_claimed_to_unknown_without_redispatch_authority(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        store.insert_paper_order_dispatch(prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        assert claimed.attempt_count == 1

    with _paper_store(path) as reopened:
        live_owner_recovery = reopened.recover_interrupted_dispatches(
            session=session,
            recovered_at=NOW + timedelta(seconds=3),
        )
        assert live_owner_recovery == []
        recovery_session = _session(
            reopened,
            started_at=NOW + timedelta(minutes=6),
            lease_expires_at=NOW + timedelta(minutes=11),
        )
        recovered = reopened.recover_interrupted_dispatches(
            session=recovery_session,
            recovered_at=NOW + timedelta(minutes=6, seconds=1),
        )
        assert [item.status for item in recovered] == ["outcome_unknown"]
        unknown = reopened.load_paper_order_dispatch(prepared.order_plan_id)
        assert unknown is not None
        assert unknown.attempt_count == 1
        assert unknown.last_error_code == "process_interrupted"
        with pytest.raises(PaperStateConflictError, match="different session fence"):
            reopened.claim_dispatch_attempt(
                prepared.order_plan_id,
                session=recovery_session,
                claimed_at=NOW + timedelta(minutes=6, seconds=2),
            )


def test_legacy_broker_org_decodes_only_as_forwarding_and_conflicts_fail() -> None:
    with _paper_store(":memory:") as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        store.insert_paper_order_dispatch(prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )

    legacy_payload = claimed.model_dump()
    legacy_payload.pop("broker_forwarding_order_org_number")
    legacy_payload.pop("broker_order_branch_number")
    legacy_payload.update(
        {
            "status": "accepted",
            "broker_business_date": date(2026, 7, 10),
            "broker_order_reference": "kis-order-legacy",
            "broker_order_org_number": "70001",
            "broker_order_time": "090001",
            "updated_at": NOW + timedelta(seconds=3),
            "revision": claimed.revision + 1,
        }
    )

    migrated = PaperOrderDispatch.model_validate(legacy_payload)
    assert migrated.broker_forwarding_order_org_number == "70001"
    assert migrated.broker_order_branch_number is None
    assert "broker_order_org_number" not in migrated.model_dump()

    with pytest.raises(ValidationError, match="conflicts with forwarding"):
        PaperOrderDispatch.model_validate(
            {
                **legacy_payload,
                "broker_forwarding_order_org_number": "70002",
            }
        )


def test_broker_and_risk_evidence_is_monotonic_and_immutable(tmp_path) -> None:
    with _paper_store(tmp_path / "paper.sqlite3") as store:
        session = _session(store)
        prepared = _dispatch(store, session)
        store.insert_paper_order_dispatch(prepared)
        claimed = store.claim_dispatch_attempt(
            prepared.order_plan_id,
            session=session,
            claimed_at=NOW + timedelta(seconds=2),
        )
        accepted = PaperOrderDispatch.model_validate(
            claimed.model_copy(
                update={
                    "status": "accepted",
                    "broker_business_date": date(2026, 7, 10),
                    "broker_order_reference": "kis-order-001",
                    "broker_forwarding_order_org_number": "00001",
                    "broker_order_time": "090001",
                    "updated_at": NOW + timedelta(seconds=3),
                    "revision": claimed.revision + 1,
                }
            ).model_dump()
        )
        assert store.update_paper_order_dispatch(accepted) == accepted

        fill = PaperDispatchFillEvidence(
            broker_fill_reference="kis-fill-001",
            broker_order_id=accepted.broker_order_id,
            broker_order_reference="kis-order-001",
            symbol="005930",
            side="buy",
            quantity=4.0,
            price=70_000.0,
            notional=280_000.0,
            evidence_at=NOW + timedelta(seconds=4),
            time_basis="broker_execution",
        )
        partial = PaperOrderDispatch.model_validate(
            accepted.model_copy(
                update={
                    "status": "partially_filled",
                    "broker_order_branch_number": "91234",
                    "cumulative_filled_quantity": 4.0,
                    "fill_evidence": [fill],
                    "updated_at": NOW + timedelta(seconds=4),
                    "revision": accepted.revision + 1,
                }
            ).model_dump()
        )
        assert store.update_paper_order_dispatch(partial) == partial

        changed_quote = partial.model_copy(
            update={
                "quote_bid": 69_700.0,
                "quote_last": 69_850.0,
                "updated_at": NOW + timedelta(seconds=5),
                "revision": partial.revision + 1,
            }
        )
        with pytest.raises(PaperStateConflictError, match="immutable"):
            store.update_paper_order_dispatch(
                PaperOrderDispatch.model_validate(changed_quote.model_dump())
            )

        for field, value in (
            ("snapshot_daily_loss_ratio", -0.015),
            ("snapshot_monthly_loss_ratio", -0.025),
            ("snapshot_symbol_orderable_quantity", 3.0),
            ("broker_orderable_cash", 900_000.0),
            ("broker_orderable_buy_quantity", 13.0),
            ("submission_evidence_expires_at", NOW + timedelta(minutes=8)),
        ):
            changed_risk_evidence = partial.model_copy(
                update={
                    field: value,
                    "updated_at": NOW + timedelta(seconds=5),
                    "revision": partial.revision + 1,
                }
            )
            with pytest.raises(PaperStateConflictError, match="immutable"):
                store.update_paper_order_dispatch(
                    PaperOrderDispatch.model_validate(
                        changed_risk_evidence.model_dump()
                    )
                )

        changed_forwarding_identity = partial.model_copy(
            update={
                "broker_forwarding_order_org_number": "00002",
                "updated_at": NOW + timedelta(seconds=5),
                "revision": partial.revision + 1,
            }
        )
        with pytest.raises(PaperStateConflictError, match="forwarding organization"):
            store.update_paper_order_dispatch(
                PaperOrderDispatch.model_validate(
                    changed_forwarding_identity.model_dump()
                )
            )

        changed_branch_identity = partial.model_copy(
            update={
                "broker_order_branch_number": "91235",
                "updated_at": NOW + timedelta(seconds=5),
                "revision": partial.revision + 1,
            }
        )
        with pytest.raises(PaperStateConflictError, match="order branch"):
            store.update_paper_order_dispatch(
                PaperOrderDispatch.model_validate(
                    changed_branch_identity.model_dump()
                )
            )

        removed_fill = partial.model_copy(
            update={
                "status": "accepted",
                "cumulative_filled_quantity": 0.0,
                "fill_evidence": [],
                "updated_at": NOW + timedelta(seconds=5),
                "revision": partial.revision + 1,
            }
        )
        with pytest.raises(PaperStateConflictError):
            store.update_paper_order_dispatch(
                PaperOrderDispatch.model_validate(removed_fill.model_dump())
            )


def test_dispatch_model_and_schema_store_no_raw_secrets_or_account_id(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    with _paper_store(path) as store:
        session = _session(store)
        dispatch = _dispatch(store, session)
        store.insert_paper_order_dispatch(dispatch)
        with pytest.raises(ValidationError):
            PaperOrderDispatch.model_validate(
                {**dispatch.model_dump(), "api_key": "must-not-persist"}
            )

    connection = sqlite3.connect(path)
    try:
        metadata_payload = json.loads(
            connection.execute(
                "SELECT state_json FROM state_store_metadata"
            ).fetchone()[0]
        )
        dispatch_payload = json.loads(
            connection.execute(
                "SELECT state_json FROM paper_order_dispatches"
            ).fetchone()[0]
        )
        columns = {
            row[1]
            for table in (
                "state_store_metadata",
                "paper_execution_sessions",
                "paper_order_dispatches",
            )
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
    finally:
        connection.close()

    forbidden = {
        "account_id",
        "account_number",
        "api_key",
        "api_secret",
        "access_token",
        "authorization",
        "credential",
    }
    assert not forbidden.intersection(metadata_payload)
    assert not forbidden.intersection(dispatch_payload)
    assert not forbidden.intersection(columns)
    assert dispatch_payload["account_scope_fingerprint"] == ACCOUNT_A
    assert datetime.fromisoformat(
        dispatch_payload["submission_evidence_expires_at"].replace("Z", "+00:00")
    ) == NOW + timedelta(minutes=9)
