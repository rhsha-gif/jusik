from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.execution.events import (
    PaperEventSchemaUnsupported,
    PaperEventStreamCorruption,
    PaperExecutionEvent,
    PaperOrderDispatchEventPayload,
    build_paper_execution_event,
    canonical_import_event_id,
    canonical_json_bytes,
    canonical_sha256,
    decode_paper_execution_event,
    identity_keys_for_dispatch,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperDispatchFillEvidence,
    PaperOrderDispatch,
    PaperRiskReservation,
)


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
ACCOUNT = "sha256:" + "a" * 64
STORE = "store-event-tests"


def _dispatch(**updates: object) -> PaperOrderDispatch:
    prepared_at = NOW + timedelta(seconds=1)
    values: dict[str, object] = {
        "order_plan_id": "oplan-event-001",
        "broker_order_id": "bord-local-001",
        "run_id": "run-event-001",
        "idempotency_key": "paper-event-001",
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
        "session_id": "psess-event-001",
        "fencing_token": 1,
        "account_scope_fingerprint": ACCOUNT,
        "prepared_at": prepared_at,
        "updated_at": prepared_at,
    }
    values.update(updates)
    return PaperOrderDispatch(**values)


def _reservation(dispatch: PaperOrderDispatch, **updates: object) -> PaperRiskReservation:
    values: dict[str, object] = {
        "reservation_id": "presv-event-001",
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


def _observed(
    dispatch: PaperOrderDispatch,
    *,
    time_basis: str,
    reference: str,
) -> PaperOrderDispatch:
    observed_at = dispatch.updated_at + timedelta(seconds=1)
    fill = PaperDispatchFillEvidence(
        broker_fill_reference=reference,
        broker_order_id=dispatch.broker_order_id,
        broker_order_reference="0000012345",
        symbol=dispatch.symbol,
        side=dispatch.side,
        quantity=dispatch.quantity,
        price=dispatch.limit_price,
        notional=dispatch.quantity * dispatch.limit_price,
        evidence_at=observed_at,
        time_basis=time_basis,
    )
    return PaperOrderDispatch.model_validate(
        dispatch.model_copy(
            update={
                "status": "filled",
                "broker_business_date": date(2026, 7, 10),
                "broker_order_reference": "0000012345",
                "broker_order_branch_number": "00123",
                "broker_order_time": "101530",
                "cumulative_filled_quantity": dispatch.quantity,
                "fill_evidence": [fill],
                "updated_at": observed_at,
                "revision": dispatch.revision + 1,
            }
        ).model_dump()
    )


def test_canonical_preimages_have_pinned_bytes_and_digests() -> None:
    preimage = {
        "aggregate_id": "oplan-event-001",
        "aggregate_type": "order_dispatch",
        "event_schema_version": 1,
        "payload_hash": "sha256:" + "b" * 64,
        "source_revision": 7,
        "store_id": STORE,
    }
    assert canonical_json_bytes(preimage) == (
        b'{"aggregate_id":"oplan-event-001","aggregate_type":"order_dispatch",'
        b'"event_schema_version":1,"payload_hash":"sha256:bbbbbbbbbbbbbbbbbbbbbbbb'
        b'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","source_revision":7,'
        b'"store_id":"store-event-tests"}'
    )
    assert canonical_sha256(preimage) == (
        "sha256:52735de915c82dd5109129fd331f978aa1f8d662835a2cb4185ac35b679b831f"
    )
    assert canonical_import_event_id(
        store_id=STORE,
        aggregate_type="order_dispatch",
        aggregate_id="oplan-event-001",
        source_revision=7,
        payload_hash="sha256:" + "b" * 64,
    ) == "pevt_import_52735de915c82dd5109129fd331f978aa1f8d662835a2cb4185ac35b679b831f"


@pytest.mark.parametrize(
    ("time_basis", "reference", "expected_kind", "expected_scope"),
    [
        (
            "broker_execution",
            "exec-001",
            "venue_execution",
            "sha256:a03dbf596d4b563c3f15a10c34ee09d06d8a98a3f9c1fe30b223df131136d3f5",
        ),
        (
            "broker_daily_aggregate_first_observed",
            "kisagg-001",
            "broker_cumulative_delta",
            "sha256:4a900fcb7f04fda97f2e6970739a641dc2d05ae996f33f05365003b9536db954",
        ),
    ],
)
def test_existing_fill_evidence_maps_to_typed_identity_scope(
    time_basis: str,
    reference: str,
    expected_kind: str,
    expected_scope: str,
) -> None:
    claimed = _claimed(_dispatch())
    observed = _observed(claimed, time_basis=time_basis, reference=reference)
    keys = identity_keys_for_dispatch(claimed, observed)
    assert len(keys) == 1
    assert keys[0].kind == expected_kind
    assert keys[0].external_id == reference
    assert keys[0].scope_hash == expected_scope


def test_event_payload_has_no_aggregate_time_basis_and_binds_envelope() -> None:
    prepared = _dispatch()
    event = build_paper_execution_event(
        event_id="pevt_order_prepared",
        aggregate_version=1,
        event_type="OrderPrepared",
        source="local_prepare",
        after=prepared,
        causation_id="pevt_risk_reserved",
    )
    payload = event.payload.model_dump(mode="json", exclude_none=True)
    assert set(payload) == {"after"}
    assert "time_basis" not in payload
    assert event.aggregate_id == prepared.order_plan_id
    assert event.local_broker_order_id == prepared.broker_order_id
    assert event.broker_order_id is None
    assert event.received_at == prepared.updated_at
    assert PaperExecutionEvent.model_validate(event.model_dump()) == event


def test_order_accepted_uses_verified_kis_business_time() -> None:
    claimed = _claimed(_dispatch())
    updated_at = claimed.updated_at + timedelta(seconds=1)
    accepted = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "accepted",
                "broker_business_date": date(2026, 7, 10),
                "broker_order_reference": "0000012345",
                "broker_forwarding_order_org_number": "70001",
                "broker_order_time": "101530",
                "updated_at": updated_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    event = build_paper_execution_event(
        event_id="pevt_accepted",
        aggregate_version=3,
        event_type="OrderAccepted",
        source="broker_acceptance",
        after=accepted,
        before=claimed,
        causation_id="pevt_claimed",
    )
    assert event.occurred_at.isoformat() == "2026-07-10T10:15:30+09:00"
    assert event.received_at == accepted.updated_at


def test_import_event_requires_all_fill_keys_and_one_migration_clock() -> None:
    filled = _observed(
        _claimed(_dispatch()),
        time_basis="broker_execution",
        reference="exec-import-001",
    )
    received_at = NOW + timedelta(days=1)
    payload = PaperOrderDispatchEventPayload(after=filled, legacy_snapshot=True)
    event_id = canonical_import_event_id(
        store_id=filled.store_id,
        aggregate_type="order_dispatch",
        aggregate_id=filled.order_plan_id,
        source_revision=filled.revision,
        payload_hash=canonical_sha256(payload),
    )
    event = build_paper_execution_event(
        event_id=event_id,
        aggregate_version=1,
        event_type="LegacyOrderDispatchImported",
        source="schema_migration",
        after=filled,
        causation_id=None,
        legacy_snapshot=True,
        migration_received_at=received_at,
    )
    assert event.payload.legacy_snapshot is True
    assert len(event.identity_keys) == len(filled.fill_evidence) == 1
    assert event.occurred_at == filled.updated_at
    assert event.received_at == received_at

    raw = event.model_dump()
    raw["event_id"] = "pevt_import_forged"
    with pytest.raises(ValidationError, match="event_id is not deterministic"):
        PaperExecutionEvent.model_validate(raw)


def test_multi_fill_import_preserves_each_time_basis_without_aggregate_value() -> None:
    claimed = _claimed(_dispatch())
    observed_at = claimed.updated_at + timedelta(seconds=1)
    fills = [
        PaperDispatchFillEvidence(
            broker_fill_reference="exec-mixed-001",
            broker_order_id=claimed.broker_order_id,
            broker_order_reference="0000012345",
            symbol=claimed.symbol,
            side=claimed.side,
            quantity=2,
            price=claimed.limit_price,
            notional=2 * claimed.limit_price,
            evidence_at=observed_at,
            time_basis="broker_execution",
        ),
        PaperDispatchFillEvidence(
            broker_fill_reference="kisagg-mixed-002",
            broker_order_id=claimed.broker_order_id,
            broker_order_reference="0000012345",
            symbol=claimed.symbol,
            side=claimed.side,
            quantity=3,
            price=claimed.limit_price,
            notional=3 * claimed.limit_price,
            evidence_at=observed_at,
            time_basis="broker_daily_aggregate_first_observed",
        ),
    ]
    partial = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "partially_filled",
                "broker_business_date": date(2026, 7, 10),
                "broker_order_reference": "0000012345",
                "broker_order_branch_number": "00123",
                "broker_order_time": "101530",
                "cumulative_filled_quantity": 5,
                "fill_evidence": fills,
                "updated_at": observed_at,
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    payload = PaperOrderDispatchEventPayload(after=partial, legacy_snapshot=True)
    event_id = canonical_import_event_id(
        store_id=partial.store_id,
        aggregate_type="order_dispatch",
        aggregate_id=partial.order_plan_id,
        source_revision=partial.revision,
        payload_hash=canonical_sha256(payload),
    )
    event = build_paper_execution_event(
        event_id=event_id,
        aggregate_version=1,
        event_type="LegacyOrderDispatchImported",
        source="schema_migration",
        after=partial,
        causation_id=None,
        legacy_snapshot=True,
        migration_received_at=NOW + timedelta(days=1),
    )
    assert [item.kind for item in event.identity_keys] == [
        "broker_cumulative_delta",
        "venue_execution",
    ]
    assert not hasattr(event.payload, "time_basis")


def test_recursive_secret_validator_rejects_instead_of_redacting() -> None:
    event = build_paper_execution_event(
        event_id="pevt_order_prepared",
        aggregate_version=1,
        event_type="OrderPrepared",
        source="local_prepare",
        after=_dispatch(),
        causation_id="pevt_risk_reserved",
    )
    raw = event.model_dump()
    raw["payload"]["api_key"] = "must-not-survive"
    with pytest.raises(ValidationError, match="forbidden field: api_key"):
        PaperExecutionEvent.model_validate(raw)

    raw = event.model_dump()
    raw["payload"]["after"]["order_plan_payload"] = {
        "authorization": "Bearer must-not-survive"
    }
    with pytest.raises(ValidationError, match="forbidden field: authorization"):
        PaperExecutionEvent.model_validate(raw)

    raw = event.model_dump()
    raw["payload"]["after"]["account_number"] = "12345678"
    with pytest.raises(ValidationError, match="forbidden field: account_number"):
        PaperExecutionEvent.model_validate(raw)


def test_fencing_token_exception_is_path_and_type_scoped() -> None:
    event = build_paper_execution_event(
        event_id="pevt_risk",
        aggregate_version=1,
        event_type="RiskReserved",
        source="local_prepare",
        after=_reservation(_dispatch()),
        causation_id=None,
    )
    assert PaperExecutionEvent.model_validate(event.model_dump()) == event
    raw = event.model_dump()
    raw["fencing_token"] = 1
    with pytest.raises(ValidationError, match="fencing_token is allowed only"):
        PaperExecutionEvent.model_validate(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [("event_schema_version", 2), ("event_type", "TradeBusted")],
)
def test_raw_unknown_schema_or_type_is_classified_before_pydantic(
    field: str,
    value: object,
) -> None:
    event = build_paper_execution_event(
        event_id="pevt_order_prepared",
        aggregate_version=1,
        event_type="OrderPrepared",
        source="local_prepare",
        after=_dispatch(),
        causation_id="pevt_risk_reserved",
    )
    raw = event.model_dump()
    raw[field] = value
    with pytest.raises(PaperEventSchemaUnsupported):
        decode_paper_execution_event(raw)


def test_known_schema_malformed_event_is_corruption() -> None:
    event = build_paper_execution_event(
        event_id="pevt_order_prepared",
        aggregate_version=1,
        event_type="OrderPrepared",
        source="local_prepare",
        after=_dispatch(),
        causation_id="pevt_risk_reserved",
    )
    raw = event.model_dump()
    raw["payload_hash"] = "sha256:" + "0" * 64
    with pytest.raises(PaperEventStreamCorruption):
        decode_paper_execution_event(raw)


def test_cancel_envelope_uses_local_and_actual_broker_ids_without_idempotency() -> None:
    request = PaperCancelRequest(
        cancel_id="pcancel-event-001",
        kill_id="pkill-event-001",
        order_plan_id="oplan-event-001",
        broker_order_id="bord-local-001",
        broker_order_reference="0000012345",
        broker_forwarding_order_org_number="70001",
        symbol="005930",
        side="buy",
        cancelable_quantity=10,
        original_limit_price=70_000,
        store_id=STORE,
        account_scope_fingerprint=ACCOUNT,
        created_at=NOW,
        updated_at=NOW,
    )
    event = build_paper_execution_event(
        event_id="pevt_cancel_prepared",
        aggregate_version=1,
        event_type="CancelPrepared",
        source="kill_cancel",
        after=request,
        causation_id=None,
    )
    assert event.aggregate_id == request.cancel_id
    assert event.correlation_id == request.order_plan_id
    assert event.idempotency_key is None
    assert event.local_broker_order_id == request.broker_order_id
    assert event.broker_order_id == request.broker_order_reference
