from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from quantpilot.packages.brokers.mock_broker import MockBroker
from quantpilot.packages.brokers.paper_broker import PaperBroker
from quantpilot.packages.core.backtest import (
    AcceptanceThresholds,
    BacktestAssumptions,
    BacktestRequest,
    BacktestSignal,
    build_walk_forward_windows,
    calculate_max_drawdown,
    evaluate_acceptance,
    run_backtest,
)
from quantpilot.packages.core.schemas import Signal, SignalAction


class PriceHistoryOnlyProvider:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.price_history_calls = 0

    def get_price_history(self) -> list[dict[str, Any]]:
        self.price_history_calls += 1
        return [dict(row) for row in self.rows]

    def get_bars(self) -> list[dict[str, Any]]:
        raise AssertionError("backtest engine must not consume snapshot bars")


def _rows(days: int = 6) -> list[dict[str, Any]]:
    start = date(2026, 1, 1)
    rows: list[dict[str, Any]] = []
    for index in range(days):
        session = start + timedelta(days=index)
        close = 100.0 + index * 2
        rows.append(
            {
                "symbol": "AAA",
                "ticker": "AAA",
                "date": session.isoformat(),
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 100_000,
            }
        )
    return rows


def _buy_request(*, assumptions: BacktestAssumptions | None = None) -> BacktestRequest:
    return BacktestRequest(
        strategy_id="test_strategy",
        recipe_version="1.0",
        initial_cash=10_000,
        assumptions=assumptions or BacktestAssumptions(sell_tax_bps=10.0),
        signals=[
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 1, 1),
                action=SignalAction.buy_ready,
                target_weight_hint=0.50,
                reason="deterministic buy",
            )
        ],
    )


def test_backtest_consumes_price_history_provider_boundary_only() -> None:
    provider = PriceHistoryOnlyProvider(_rows())

    result = run_backtest(_buy_request(), provider)

    assert provider.price_history_calls == 1
    assert result.research_only is True
    assert result.live_trading_approval is False
    assert result.metrics.filled_trades == 1
    assert result.metrics.final_gross_exposure > 0


def test_backtest_repeated_runs_are_identical() -> None:
    provider = PriceHistoryOnlyProvider(_rows())
    request = _buy_request()

    first = run_backtest(request, provider)
    second = run_backtest(request, PriceHistoryOnlyProvider(_rows()))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.result_id.startswith("bt_")


def test_backtest_accepts_existing_signal_shape_and_injected_rows() -> None:
    signal = Signal(
        strategy_id="test_strategy",
        recipe_version="1.0",
        symbol="AAA",
        signal_date=date(2026, 1, 1),
        action=SignalAction.buy_ready,
        strength=0.8,
        target_weight_hint=0.50,
        reason="existing signal model",
    )
    request = BacktestRequest(
        strategy_id="test_strategy",
        recipe_version="1.0",
        initial_cash=10_000,
        assumptions=BacktestAssumptions(sell_tax_bps=10.0),
        signals=[signal],
    )

    result = run_backtest(request, _rows())

    assert result.metrics.filled_trades == 1
    assert result.input_summary["data_boundary"] == "injected_price_history"


def test_fees_and_slippage_reduce_return() -> None:
    zero_cost = BacktestAssumptions(fee_bps=0.0, slippage_bps=0.0, sell_tax_bps=0.0)
    realistic_cost = BacktestAssumptions(fee_bps=15.0, slippage_bps=5.0, sell_tax_bps=10.0)

    zero = run_backtest(_buy_request(assumptions=zero_cost), PriceHistoryOnlyProvider(_rows()))
    realistic = run_backtest(_buy_request(assumptions=realistic_cost), PriceHistoryOnlyProvider(_rows()))

    assert realistic.metrics.total_return < zero.metrics.total_return
    assert any("unrealistic_cost_assumption" in warning for warning in zero.warnings)


def test_max_drawdown_calculation_on_hand_written_equity_curve() -> None:
    assert calculate_max_drawdown([100.0, 120.0, 90.0, 110.0]) == pytest.approx(0.25)


def test_blocked_and_unfilled_trades_do_not_change_positions_or_cash() -> None:
    rows = _rows()
    rows[1]["low"] = 101.5  # Buy limit at prior close 100 is not touched.
    request = BacktestRequest(
        strategy_id="test_strategy",
        recipe_version="1.0",
        initial_cash=10_000,
        assumptions=BacktestAssumptions(sell_tax_bps=10.0),
        signals=[
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 1, 1),
                action=SignalAction.buy_ready,
                target_weight_hint=0.50,
                reason="limit should not fill",
            ),
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 1, 2),
                action=SignalAction.blocked,
                target_weight_hint=0.50,
                reason="blocked by research policy",
            ),
        ],
    )

    result = run_backtest(request, PriceHistoryOnlyProvider(rows))

    assert result.metrics.number_of_blocked_trades == 2
    assert {trade.blocked_reason for trade in result.trades} == {"limit_not_touched", "blocked_signal"}
    assert result.metrics.final_cash == 10_000
    assert result.metrics.final_gross_exposure == 0


def test_backtest_never_calls_broker_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_submit(*args: object, **kwargs: object) -> None:
        raise AssertionError("backtest must not submit broker orders")

    monkeypatch.setattr(MockBroker, "submit_order", fail_submit)
    monkeypatch.setattr(PaperBroker, "submit_order", fail_submit)

    result = run_backtest(_buy_request(), PriceHistoryOnlyProvider(_rows()))

    assert result.metrics.filled_trades == 1


def test_too_short_data_produces_warning() -> None:
    result = run_backtest(_buy_request(), PriceHistoryOnlyProvider(_rows(days=2)))

    assert any("insufficient_data" in warning for warning in result.warnings)


def test_walk_forward_windows_and_acceptance_are_research_only() -> None:
    trading_dates = [date(2026, 1, 1) + timedelta(days=index) for index in range(10)]

    windows = build_walk_forward_windows(trading_dates, train_size=4, test_size=2, step_size=2)
    result = run_backtest(_buy_request(), PriceHistoryOnlyProvider(_rows(days=10)))
    evaluation = evaluate_acceptance(
        result,
        AcceptanceThresholds(
            min_total_return=-1.0,
            max_drawdown=1.0,
            min_filled_trades=1,
            max_turnover=1.0,
        ),
    )

    assert [(window.train_start, window.train_end, window.test_start, window.test_end) for window in windows] == [
        (date(2026, 1, 1), date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6)),
        (date(2026, 1, 3), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)),
        (date(2026, 1, 5), date(2026, 1, 8), date(2026, 1, 9), date(2026, 1, 10)),
    ]
    assert evaluation.passed is True
    assert evaluation.research_only is True
    assert evaluation.live_trading_approval is False
