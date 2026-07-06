from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from quantpilot.packages.core.backtest import BacktestAssumptions, BacktestRequest, BacktestSignal
from quantpilot.packages.core.schemas import SignalAction
from quantpilot.packages.core.validation import build_walk_forward_splits, run_walk_forward_validation


def _rows(days: int = 12) -> list[dict[str, Any]]:
    start = date(2026, 1, 1)
    rows: list[dict[str, Any]] = []
    for index in range(days):
        session = start + timedelta(days=index)
        close = 100.0 + index
        rows.append(
            {
                "symbol": "AAA",
                "date": session.isoformat(),
                "open": close,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 100_000,
            }
        )
    return rows


def _request() -> BacktestRequest:
    return BacktestRequest(
        strategy_id="walk_forward_strategy",
        recipe_version="1.0",
        initial_cash=10_000,
        assumptions=BacktestAssumptions(
            fee_bps=1.0,
            slippage_bps=1.0,
            sell_tax_bps=1.0,
            min_trading_days=1,
            min_filled_trades=1,
        ),
        signals=[
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 1, 6),
                action=SignalAction.buy_ready,
                target_weight_hint=0.5,
            ),
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 1, 8),
                action=SignalAction.buy_ready,
                target_weight_hint=0.5,
            ),
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 1, 10),
                action=SignalAction.buy_ready,
                target_weight_hint=0.5,
            ),
        ],
    )


def test_walk_forward_splits_include_purge_and_embargo_metadata() -> None:
    trading_dates = [date(2026, 1, 1) + timedelta(days=index) for index in range(12)]

    splits = build_walk_forward_splits(
        trading_dates,
        train_size=4,
        test_size=2,
        step_size=2,
        purge_days=1,
        embargo_days=1,
    )

    assert [(split.train_start, split.train_end, split.test_start, split.test_end) for split in splits] == [
        (date(2026, 1, 1), date(2026, 1, 4), date(2026, 1, 6), date(2026, 1, 7)),
        (date(2026, 1, 3), date(2026, 1, 6), date(2026, 1, 8), date(2026, 1, 9)),
        (date(2026, 1, 5), date(2026, 1, 8), date(2026, 1, 10), date(2026, 1, 11)),
    ]
    assert splits[0].purge_embargo.purge_start == date(2026, 1, 5)
    assert splits[0].purge_embargo.purge_end == date(2026, 1, 5)
    assert splits[0].purge_embargo.embargo_start == date(2026, 1, 8)
    assert splits[0].purge_embargo.embargo_end == date(2026, 1, 8)
    assert splits[0].purge_embargo.removed_between_train_and_test_days == 1
    assert splits[0].purge_embargo.embargoed_after_test_days == 1


def test_walk_forward_validation_runs_test_windows_only_and_blocks_promotion() -> None:
    runs = run_walk_forward_validation(
        _request(),
        _rows(),
        train_size=4,
        test_size=2,
        step_size=2,
        purge_days=1,
        embargo_days=1,
        data_mode="fixture",
    )

    assert [run.status for run in runs] == ["completed", "completed", "completed"]
    assert [run.metrics["filled_trades"] for run in runs] == [1, 1, 1]
    assert all(run.promotion_allowed is False for run in runs)
    assert all(run.live_trading_approval is False for run in runs)
    assert all(run.data_mode == "fixture" for run in runs)
    assert runs[0].backtest_result is not None
    assert runs[0].backtest_result.start_date == date(2026, 1, 6)
    assert runs[0].backtest_result.end_date == date(2026, 1, 7)
