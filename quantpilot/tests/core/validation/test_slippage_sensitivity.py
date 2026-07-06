from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from quantpilot.packages.core.backtest import BacktestAssumptions, BacktestRequest, BacktestSignal, run_backtest
from quantpilot.packages.core.schemas import SignalAction
from quantpilot.packages.core.validation import build_benchmark_relative_attribution, run_slippage_sensitivity


def _rows(days: int = 8) -> list[dict[str, Any]]:
    start = date(2026, 2, 1)
    rows: list[dict[str, Any]] = []
    for index in range(days):
        session = start + timedelta(days=index)
        close = 100.0 + index * 2
        rows.append(
            {
                "symbol": "AAA",
                "date": session.isoformat(),
                "open": close,
                "high": close + 3,
                "low": close - 3,
                "close": close,
                "volume": 100_000,
            }
        )
    return rows


def _benchmark(days: int = 8) -> list[dict[str, Any]]:
    start = date(2026, 2, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "value": 100.0 + index,
        }
        for index in range(days)
    ]


def _request() -> BacktestRequest:
    return BacktestRequest(
        strategy_id="slippage_strategy",
        recipe_version="1.0",
        initial_cash=10_000,
        assumptions=BacktestAssumptions(
            fee_bps=1.0,
            slippage_bps=5.0,
            sell_tax_bps=1.0,
            min_trading_days=1,
            min_filled_trades=1,
        ),
        signals=[
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 2, 1),
                action=SignalAction.buy_ready,
                target_weight_hint=0.5,
            )
        ],
    )


def test_slippage_sensitivity_sweeps_scenarios_without_granting_promotion() -> None:
    result = run_slippage_sensitivity(
        _request(),
        _rows(),
        slippage_bps_values=[0.0, 5.0, 50.0],
        data_mode="fixture",
    )

    returns = [scenario.total_return for scenario in result.scenarios]

    assert result.status == "completed"
    assert [scenario.slippage_bps for scenario in result.scenarios] == [0.0, 5.0, 50.0]
    assert returns[0] is not None and returns[-1] is not None
    assert returns[0] > returns[-1]
    assert result.worst_total_return == returns[-1]
    assert result.total_return_range is not None
    assert result.conservative_pass is False


def test_benchmark_relative_attribution_uses_aligned_dates() -> None:
    backtest = run_backtest(_request(), _rows())

    attribution = build_benchmark_relative_attribution(
        backtest,
        _benchmark(),
        benchmark_label="fixture_benchmark",
    )

    assert attribution.status == "completed"
    assert attribution.benchmark_label == "fixture_benchmark"
    assert attribution.matched_days == 8
    assert attribution.start_date == date(2026, 2, 1)
    assert attribution.end_date == date(2026, 2, 8)
    assert attribution.strategy_total_return is not None
    assert attribution.benchmark_total_return is not None
    assert attribution.excess_return == round(
        attribution.strategy_total_return - attribution.benchmark_total_return,
        6,
    )
