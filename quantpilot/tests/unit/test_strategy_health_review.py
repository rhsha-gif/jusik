from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

import quantpilot.packages.core.strategies.performance_review as performance_review_module
from quantpilot.packages.core.strategies.performance_review import (
    StrategyHealthInput,
    evaluate_strategy_health,
)


def _request(**updates: object) -> StrategyHealthInput:
    values: dict[str, object] = {
        "strategy_id": "pullback_trend_v2",
        "strategy_version": "2.0",
        "backtest_max_drawdown": 0.08,
        "realized_max_drawdown": 0.05,
        "realized_return": 0.04,
        "benchmark_return": 0.02,
    }
    values.update(updates)
    return StrategyHealthInput(**values)


def test_performance_review_module_is_pure_and_has_no_runtime_dependencies() -> None:
    source = inspect.getsource(performance_review_module)
    forbidden = (
        "datetime.now",
        "os.environ",
        "packages.brokers",
        "packages.db",
        "core.execution",
        "harness_service",
        "services.api",
        "Path(",
        "open(",
    )

    assert all(token not in source for token in forbidden)


@pytest.mark.parametrize(
    ("updates", "field_name"),
    [
        ({"backtest_max_drawdown": -0.01}, "backtest_max_drawdown"),
        ({"realized_max_drawdown": -0.01}, "realized_max_drawdown"),
    ],
)
def test_drawdown_inputs_enforce_positive_ratio_contract(
    updates: dict[str, float],
    field_name: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _request(**updates)

    assert field_name in str(exc_info.value)


def test_zero_backtest_drawdown_requires_reapproval_after_any_realized_drawdown() -> None:
    decision = evaluate_strategy_health(
        _request(
            backtest_max_drawdown=0.0,
            realized_max_drawdown=0.000001,
            benchmark_return=0.0,
        )
    )

    assert decision.status == "paused_reapproval"
    assert decision.reapproval_drawdown_threshold == 0.0


def test_exact_twenty_percent_drawdown_disables_and_starts_retirement() -> None:
    decision = evaluate_strategy_health(_request(realized_max_drawdown=0.20))

    assert decision.status == "disabled"
    assert decision.block_new_buys is True
    assert decision.start_retirement is True
    assert decision.reason_codes == ["max_drawdown_disable_threshold_reached"]


def test_exact_negative_ten_percent_excess_return_disables() -> None:
    decision = evaluate_strategy_health(
        _request(realized_return=0.20, benchmark_return=0.30)
    )

    assert decision.excess_return == pytest.approx(-0.10)
    assert decision.status == "disabled"
    assert decision.block_new_buys is True
    assert decision.start_retirement is True
    assert decision.reason_codes == ["excess_return_disable_threshold_reached"]


def test_all_disable_reasons_are_reported_in_stable_precedence_order() -> None:
    decision = evaluate_strategy_health(
        _request(
            realized_max_drawdown=0.25,
            realized_return=-0.10,
            benchmark_return=0.05,
        )
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == [
        "max_drawdown_disable_threshold_reached",
        "excess_return_disable_threshold_reached",
    ]


def test_drawdown_strictly_above_backtest_multiplier_pauses_for_reapproval() -> None:
    decision = evaluate_strategy_health(
        _request(backtest_max_drawdown=0.08, realized_max_drawdown=0.120001)
    )

    assert decision.reapproval_drawdown_threshold == pytest.approx(0.12)
    assert decision.status == "paused_reapproval"
    assert decision.block_new_buys is True
    assert decision.start_retirement is False
    assert decision.reason_codes == ["max_drawdown_reapproval_threshold_breached"]


def test_exact_backtest_multiplier_remains_active_when_review_is_available() -> None:
    request = _request(
        backtest_max_drawdown=0.08,
        realized_max_drawdown=0.08 * 1.5,
    )

    decision = evaluate_strategy_health(request)

    assert decision.status == "active"
    assert decision.block_new_buys is False
    assert decision.start_retirement is False
    assert decision.reason_codes == ["strategy_health_within_thresholds"]


def test_missing_benchmark_blocks_buys_without_starting_retirement() -> None:
    decision = evaluate_strategy_health(_request(benchmark_return=None))

    assert decision.excess_return is None
    assert decision.status == "review_unavailable"
    assert decision.block_new_buys is True
    assert decision.start_retirement is False
    assert decision.reason_codes == ["benchmark_return_missing"]


def test_drawdown_precedence_applies_when_benchmark_is_missing() -> None:
    paused = evaluate_strategy_health(
        _request(realized_max_drawdown=0.13, benchmark_return=None)
    )
    disabled = evaluate_strategy_health(
        _request(realized_max_drawdown=0.20, benchmark_return=None)
    )

    assert paused.status == "paused_reapproval"
    assert paused.start_retirement is False
    assert disabled.status == "disabled"
    assert disabled.start_retirement is True


def test_active_decision_exposes_metrics_and_preserves_strategy_identity() -> None:
    request = _request()

    decision = evaluate_strategy_health(request)

    assert decision.strategy_id == request.strategy_id
    assert decision.strategy_version == request.strategy_version
    assert decision.backtest_max_drawdown == request.backtest_max_drawdown
    assert decision.realized_max_drawdown == request.realized_max_drawdown
    assert decision.realized_return == request.realized_return
    assert decision.benchmark_return == request.benchmark_return
    assert decision.reapproval_drawdown_threshold == pytest.approx(0.12)
    assert decision.excess_return == pytest.approx(0.02)


def test_strategy_health_review_is_deterministic() -> None:
    request = _request(
        realized_max_drawdown=0.20,
        realized_return=-0.10,
        benchmark_return=0.05,
    )

    assert evaluate_strategy_health(request) == evaluate_strategy_health(request)
