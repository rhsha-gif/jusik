from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.operator.position_ledger import PaperRiskReservation


NOW = datetime(2026, 7, 11, 2, 0, tzinfo=timezone.utc)
ACCOUNT_FINGERPRINT = f"sha256:{'a' * 64}"


def _buy(**updates: object) -> PaperRiskReservation:
    payload: dict[str, object] = {
        "reservation_id": "presv_buy_001",
        "order_plan_id": "plan_buy_001",
        "idempotency_key": "buy-key-001",
        "kind": "cash_buy",
        "symbol": "005930",
        "side": "buy",
        "reserved_cash_krw": 70_000,
        "reserved_sell_quantity": None,
        "reserved_gross_exposure_krw": 70_000,
        "broker_orderable_cash_basis_krw": 300_000,
        "broker_orderable_buy_quantity_basis": 4,
        "snapshot_orderable_quantity_basis": None,
        "snapshot_gross_exposure_basis_krw": 200_000,
        "gross_exposure_limit_krw": 900_000,
        "store_id": "store-paper-001",
        "session_id": "session-paper-001",
        "fencing_token": 1,
        "account_scope_fingerprint": ACCOUNT_FINGERPRINT,
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(updates)
    return PaperRiskReservation(**payload)


def _sell(**updates: object) -> PaperRiskReservation:
    payload: dict[str, object] = {
        "reservation_id": "presv_sell_001",
        "order_plan_id": "plan_sell_001",
        "idempotency_key": "sell-key-001",
        "kind": "sell_quantity",
        "symbol": "000660",
        "side": "sell",
        "reserved_cash_krw": None,
        "reserved_sell_quantity": 3,
        "reserved_gross_exposure_krw": 0,
        "broker_orderable_cash_basis_krw": None,
        "broker_orderable_buy_quantity_basis": None,
        "snapshot_orderable_quantity_basis": 10,
        "snapshot_gross_exposure_basis_krw": 900_000,
        "gross_exposure_limit_krw": 500_000,
        "store_id": "store-paper-001",
        "session_id": "session-paper-001",
        "fencing_token": 1,
        "account_scope_fingerprint": ACCOUNT_FINGERPRINT,
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(updates)
    return PaperRiskReservation(**payload)


def test_buy_reservation_normalizes_exact_whole_numeric_inputs() -> None:
    reservation = _buy(
        symbol=" 005930 ",
        reserved_cash_krw=Decimal("70000"),
        reserved_gross_exposure_krw=70_000.0,
        broker_orderable_cash_basis_krw=Decimal("300000"),
        broker_orderable_buy_quantity_basis=4.0,
        snapshot_gross_exposure_basis_krw=Decimal("200000"),
        gross_exposure_limit_krw=900_000.0,
    )

    assert reservation.symbol == "005930"
    assert reservation.status == "held"
    assert reservation.release_reason is None
    assert reservation.released_at is None
    for field in (
        "reserved_cash_krw",
        "reserved_gross_exposure_krw",
        "broker_orderable_cash_basis_krw",
        "broker_orderable_buy_quantity_basis",
        "snapshot_gross_exposure_basis_krw",
        "gross_exposure_limit_krw",
    ):
        assert type(getattr(reservation, field)) is int


def test_sell_reservation_keeps_zero_incremental_gross_even_above_buy_limit() -> None:
    reservation = _sell(reserved_sell_quantity=Decimal("3"))

    assert type(reservation.reserved_sell_quantity) is int
    assert reservation.reserved_sell_quantity == 3
    assert reservation.reserved_gross_exposure_krw == 0
    assert (
        reservation.snapshot_gross_exposure_basis_krw
        > reservation.gross_exposure_limit_krw
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reserved_cash_krw", 70_000.5),
        ("reserved_gross_exposure_krw", Decimal("70000.1")),
        ("broker_orderable_cash_basis_krw", True),
        ("broker_orderable_buy_quantity_basis", 4.5),
        ("snapshot_gross_exposure_basis_krw", float("inf")),
        ("gross_exposure_limit_krw", Decimal("NaN")),
    ],
)
def test_capacity_fields_reject_non_integral_or_boolean_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match="exact integers|integers"):
        _buy(**{field: value})


@pytest.mark.parametrize(
    "updates",
    [
        {"side": "sell"},
        {"reserved_cash_krw": 0, "reserved_gross_exposure_krw": 0},
        {"reserved_sell_quantity": 1},
        {"broker_orderable_cash_basis_krw": None},
        {"broker_orderable_buy_quantity_basis": None},
        {"snapshot_orderable_quantity_basis": 1},
        {"reserved_gross_exposure_krw": 60_000},
    ],
)
def test_cash_buy_requires_exact_buy_capacity_shape(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="cash-buy reservation"):
        _buy(**updates)


def test_cash_buy_rejects_projected_gross_above_limit() -> None:
    with pytest.raises(ValidationError, match="gross-exposure limit"):
        _buy(
            snapshot_gross_exposure_basis_krw=850_001,
            gross_exposure_limit_krw=900_000,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"side": "buy"},
        {"reserved_sell_quantity": None},
        {"reserved_cash_krw": 1},
        {"snapshot_orderable_quantity_basis": None},
        {"broker_orderable_cash_basis_krw": 1},
        {"broker_orderable_buy_quantity_basis": 1},
        {"reserved_gross_exposure_krw": 1},
    ],
)
def test_sell_quantity_requires_exact_sell_capacity_shape(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="sell-quantity reservation"):
        _sell(**updates)


@pytest.mark.parametrize(
    "status",
    [
        "released_filled",
        "released_cancelled",
        "released_rejected",
        "released_expired",
    ],
)
def test_each_terminal_status_requires_and_accepts_release_evidence(
    status: str,
) -> None:
    released_at = NOW + timedelta(seconds=1)
    reservation = _buy(
        status=status,
        release_reason=status,
        released_at=released_at,
        updated_at=released_at,
        revision=1,
    )

    assert reservation.status == status
    assert reservation.release_reason == status
    assert reservation.released_at == released_at


@pytest.mark.parametrize(
    "updates",
    [
        {"release_reason": "filled", "released_at": NOW},
        {
            "status": "released_filled",
            "released_at": NOW + timedelta(seconds=1),
            "updated_at": NOW + timedelta(seconds=1),
        },
        {
            "status": "released_filled",
            "release_reason": "filled",
            "updated_at": NOW + timedelta(seconds=1),
        },
        {
            "status": "released_filled",
            "release_reason": "filled",
            "released_at": NOW + timedelta(seconds=2),
            "updated_at": NOW + timedelta(seconds=1),
        },
    ],
)
def test_release_evidence_cannot_disagree_with_lifecycle(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="release"):
        _buy(**updates)


@pytest.mark.parametrize("field", ["created_at", "updated_at", "released_at"])
def test_reservation_timestamps_must_be_timezone_aware(field: str) -> None:
    updates: dict[str, object] = {field: datetime(2026, 7, 11, 2, 0)}
    if field == "released_at":
        updates.update(status="released_filled", release_reason="filled")
    with pytest.raises(ValidationError, match="UTC offset"):
        _buy(**updates)


def test_reservation_update_cannot_precede_creation() -> None:
    with pytest.raises(ValidationError, match="precede creation"):
        _buy(updated_at=NOW - timedelta(microseconds=1))


@pytest.mark.parametrize(
    "updates",
    [
        {"reservation_id": " "},
        {"order_plan_id": " "},
        {"idempotency_key": " "},
        {"store_id": " "},
        {"session_id": " "},
        {"symbol": "INVALID"},
        {"account_scope_fingerprint": "raw-account-number"},
        {"data_mode": "live_trading"},
        {"broker_environment": "kis_live"},
    ],
)
def test_reservation_identity_and_provenance_fail_closed(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _buy(**updates)
