from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import quantpilot.packages.core.operator.retirement as retirement_module
from quantpilot.packages.core.operator.retirement import (
    MarketableLimitLiquidationDecision,
    MarketableLimitLiquidationInput,
    build_marketable_limit_liquidation_decision,
)


NOW = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)


def _request(**updates: object) -> MarketableLimitLiquidationInput:
    values: dict[str, object] = {
        "purpose": "protective_exit",
        "policy_id": "fixture-policy",
        "policy_version": 4,
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "symbol": "005930",
        "quantity_held": 8,
        "quantity_requested": 4,
        "current_weight": 0.12,
        "best_bid": 99.0,
        "quote_as_of": NOW,
        "evaluated_at": NOW,
        "max_quote_age_seconds": 30,
        "managed_position_updated_at": NOW - timedelta(minutes=1),
        "reconciled_snapshot_id": "snapshot-001",
        "reconciled_at": NOW,
        "reason_code": "protective_stop_triggered",
    }
    values.update(updates)
    return MarketableLimitLiquidationInput(**values)


def test_retirement_order_factory_is_pure_and_broker_free() -> None:
    source = inspect.getsource(retirement_module)
    forbidden = (
        "datetime.now",
        "utc_now",
        "uuid4",
        "os.environ",
        "packages.brokers",
        "packages.db",
        "core.execution",
        "harness_service",
        "services.api",
        "OrderIntent",
        "OrderPlan",
    )

    assert all(token not in source for token in forbidden)


@pytest.mark.parametrize("purpose", ["protective_exit", "strategy_retirement"])
def test_ready_decision_is_an_exact_best_bid_limit_sell(purpose: str) -> None:
    decision = build_marketable_limit_liquidation_decision(
        _request(
            purpose=purpose,
            reason_code=f"{purpose}_requested",
        )
    )

    assert decision.status == "ready"
    assert decision.side == "sell"
    assert decision.order_type == "limit"
    assert decision.quantity_to_sell == 4
    assert decision.quantity_to_sell <= decision.quantity_held
    assert decision.quantity_to_sell <= decision.quantity_requested
    assert decision.limit_price == 99.0
    assert decision.notional == 396.0
    assert decision.target_weight == 0.06
    assert decision.purpose == purpose
    assert decision.reason_codes == [
        f"{purpose}_requested",
        "marketable_limit_sell_ready",
    ]


def test_requested_quantity_above_holding_is_capped_without_over_sell() -> None:
    decision = build_marketable_limit_liquidation_decision(
        _request(
            purpose="strategy_retirement",
            quantity_requested=99,
            reason_code="strategy_disabled",
        )
    )

    assert decision.status == "ready"
    assert decision.quantity_to_sell == 8
    assert decision.quantity_to_sell <= decision.quantity_held
    assert decision.quantity_to_sell <= decision.quantity_requested
    assert decision.notional == 792.0
    assert decision.target_weight == 0.0
    assert decision.reason_codes == [
        "strategy_disabled",
        "quantity_capped_to_holding",
        "marketable_limit_sell_ready",
    ]


def test_quote_exactly_at_freshness_boundary_is_allowed() -> None:
    decision = build_marketable_limit_liquidation_decision(
        _request(quote_as_of=NOW - timedelta(seconds=30))
    )

    assert decision.status == "ready"
    assert decision.quote_age_seconds == 30.0


def test_quote_beyond_freshness_boundary_fails_closed() -> None:
    decision = build_marketable_limit_liquidation_decision(
        _request(quote_as_of=NOW - timedelta(seconds=30, microseconds=1))
    )

    assert decision.status == "blocked"
    assert decision.quantity_to_sell == 0
    assert decision.limit_price is None
    assert decision.notional == 0.0
    assert decision.target_weight == 0.12
    assert "quote_stale" in decision.reason_codes


@pytest.mark.parametrize(
    ("updates", "expected_reason"),
    [
        (
            {"quote_as_of": NOW.replace(tzinfo=None)},
            "quote_timestamp_naive",
        ),
        (
            {"evaluated_at": NOW.replace(tzinfo=None)},
            "quote_timestamp_naive",
        ),
        (
            {"quote_as_of": NOW + timedelta(microseconds=1)},
            "quote_future",
        ),
    ],
)
def test_naive_or_future_quote_fails_closed(
    updates: dict[str, object],
    expected_reason: str,
) -> None:
    decision = build_marketable_limit_liquidation_decision(_request(**updates))

    assert decision.status == "blocked"
    assert decision.quantity_to_sell == 0
    assert decision.limit_price is None
    assert decision.notional == 0.0
    assert expected_reason in decision.reason_codes


@pytest.mark.parametrize(
    ("best_bid", "expected_reason"),
    [
        (None, "best_bid_missing"),
        (0.0, "best_bid_nonpositive"),
        (-0.01, "best_bid_nonpositive"),
        (float("nan"), "best_bid_not_finite"),
        (float("inf"), "best_bid_not_finite"),
    ],
)
def test_missing_or_invalid_best_bid_fails_closed(
    best_bid: float | None,
    expected_reason: str,
) -> None:
    decision = build_marketable_limit_liquidation_decision(
        _request(best_bid=best_bid)
    )

    assert decision.status == "blocked"
    assert decision.quantity_to_sell == 0
    assert decision.limit_price is None
    assert decision.notional == 0.0
    assert expected_reason in decision.reason_codes


@pytest.mark.parametrize(
    ("quantity_held", "quantity_requested", "expected_reason"),
    [
        (0, 4, "quantity_held_invalid"),
        (-1, 4, "quantity_held_invalid"),
        (8, 0, "quantity_requested_invalid"),
        (8, -1, "quantity_requested_invalid"),
    ],
)
def test_nonpositive_quantity_fails_closed(
    quantity_held: int,
    quantity_requested: int,
    expected_reason: str,
) -> None:
    decision = build_marketable_limit_liquidation_decision(
        _request(
            quantity_held=quantity_held,
            quantity_requested=quantity_requested,
        )
    )

    assert decision.status == "blocked"
    assert decision.quantity_to_sell == 0
    assert decision.limit_price is None
    assert decision.notional == 0.0
    assert expected_reason in decision.reason_codes


@pytest.mark.parametrize("invalid_quantity", [True, 1.5, "8"])
def test_non_integer_quantity_is_rejected_at_the_typed_boundary(
    invalid_quantity: object,
) -> None:
    with pytest.raises(ValidationError):
        _request(quantity_held=invalid_quantity)


def test_decision_and_sha256_idempotency_key_are_deterministic() -> None:
    request = _request()

    first = build_marketable_limit_liquidation_decision(request)
    second = build_marketable_limit_liquidation_decision(request)

    assert first == second
    assert first.idempotency_key == second.idempotency_key
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", first.idempotency_key)


@pytest.mark.parametrize(
    "updates",
    [
        {"managed_position_updated_at": NOW - timedelta(seconds=59)},
        {"purpose": "strategy_retirement"},
        {"policy_id": "other-policy"},
        {"policy_version": 5},
        {"strategy_id": "other-strategy"},
        {"strategy_version": "2.1"},
        {"symbol": "000660"},
        {"quantity_held": 9},
        {"quantity_requested": 3},
        {"current_weight": 0.11},
        {"best_bid": 98.0},
        {"quote_as_of": NOW - timedelta(seconds=1)},
        {"evaluated_at": NOW + timedelta(seconds=1)},
        {"max_quote_age_seconds": 31},
        {"reason_code": "technical_exit_triggered"},
    ],
)
def test_idempotency_key_changes_when_position_state_or_input_changes(
    updates: dict[str, object],
) -> None:
    baseline = build_marketable_limit_liquidation_decision(_request())
    changed = build_marketable_limit_liquidation_decision(_request(**updates))

    assert changed.idempotency_key != baseline.idempotency_key


def test_output_contract_rejects_market_order_semantics() -> None:
    decision = build_marketable_limit_liquidation_decision(_request())

    with pytest.raises(ValidationError):
        MarketableLimitLiquidationDecision(
            **{
                **decision.model_dump(),
                "order_type": "market",
            }
        )
