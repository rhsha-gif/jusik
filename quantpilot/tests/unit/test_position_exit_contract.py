from __future__ import annotations

import json
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quantpilot.packages.core.risk.position_exit import PositionRiskInput, evaluate_position_risk
import quantpilot.packages.core.risk.position_exit as position_exit_module
from quantpilot.packages.core.schemas import SignalAction


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "professional_operator_cases.json"


def _request(**updates: object) -> PositionRiskInput:
    now = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "symbol": "005930",
        "quantity": 10.0,
        "average_entry_price": 100.0,
        "current_price": 100.0,
        "atr14": 2.0,
        "sma20": 100.0,
        "rsi14": 50.0,
        "quote_as_of": now,
        "evaluated_at": now,
    }
    values.update(updates)
    return PositionRiskInput(**values)


def test_position_exit_module_has_no_execution_or_broker_dependencies() -> None:
    source = inspect.getsource(position_exit_module)
    forbidden = (
        "packages.brokers",
        "packages.db",
        "core.execution",
        "harness_service",
        "services.api",
        "OrderPlan",
        "OrderIntent",
    )
    assert all(token not in source for token in forbidden)


@pytest.mark.xfail(reason="QP-120 Claude implementation target", strict=False)
def test_protective_stop_uses_tighter_fixed_or_atr_boundary() -> None:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["position_stop_cases"]

    for case in cases:
        decision = evaluate_position_risk(
            _request(
                average_entry_price=case["average_entry_price"],
                atr14=case["atr14"],
            )
        )
        assert decision.protective_stop == case["expected_protective_stop"]


@pytest.mark.xfail(reason="QP-120 Claude implementation target", strict=False)
def test_hard_stop_exit_precedes_overheat_trim() -> None:
    decision = evaluate_position_risk(_request(current_price=95.0, atr14=2.0, rsi14=80.0))

    assert decision.action == SignalAction.exit
    assert decision.exit_fraction == 1.0
    assert decision.quantity_to_exit == 10.0
    assert "protective_stop_triggered" in decision.reason_codes


@pytest.mark.xfail(reason="QP-120 Claude implementation target", strict=False)
def test_price_equal_to_protective_stop_exits_full_position() -> None:
    decision = evaluate_position_risk(_request(current_price=96.0, atr14=2.0))

    assert decision.protective_stop == 96.0
    assert decision.action == SignalAction.exit
    assert decision.exit_fraction == 1.0


@pytest.mark.xfail(reason="QP-120 Claude implementation target", strict=False)
def test_overheat_trims_half_when_no_exit_trigger_fires() -> None:
    decision = evaluate_position_risk(_request(current_price=121.0, sma20=100.0, rsi14=75.0, atr14=2.0))

    assert decision.action == SignalAction.trim
    assert decision.exit_fraction == 0.5
    assert decision.quantity_to_exit == 5.0


@pytest.mark.xfail(reason="QP-120 Claude implementation target", strict=False)
def test_stale_quote_blocks_without_exit_quantity() -> None:
    now = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
    decision = evaluate_position_risk(
        _request(quote_as_of=now - timedelta(seconds=31), evaluated_at=now, current_price=90.0)
    )

    assert decision.action == SignalAction.blocked
    assert decision.quantity_to_exit == 0.0
    assert "quote_stale" in decision.reason_codes


@pytest.mark.xfail(reason="QP-120 Claude implementation target", strict=False)
def test_future_or_naive_quote_timestamp_fails_closed() -> None:
    now = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
    future = evaluate_position_risk(_request(quote_as_of=now + timedelta(seconds=1), evaluated_at=now))
    naive = evaluate_position_risk(
        _request(quote_as_of=now.replace(tzinfo=None), evaluated_at=now.replace(tzinfo=None))
    )

    assert future.action == SignalAction.blocked
    assert naive.action == SignalAction.blocked


@pytest.mark.xfail(reason="QP-120 Claude implementation target", strict=False)
def test_position_risk_decision_is_deterministic_and_preserves_identity() -> None:
    request = _request()

    first = evaluate_position_risk(request)
    second = evaluate_position_risk(request)

    assert first == second
    assert first.strategy_id == request.strategy_id
    assert first.strategy_version == request.strategy_version
