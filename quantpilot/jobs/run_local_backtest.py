"""Run the Stage 03 research backtest over ``local_historical`` CSV data.

Replays the deterministic Level 1-2 snapshot classifier over the local price
history (no lookahead), feeds the resulting signals to the Stage 03 backtest
engine, and reports full-period metrics plus walk-forward test windows.

Research-only: results carry ``research_only=True`` and never grant
live-trading approval. No broker adapters, order plans, or promotion state
are touched.

Usage:

    python -m quantpilot.jobs.run_local_backtest --data-dir local_data
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from quantpilot.packages.core.backtest.engine import run_backtest
from quantpilot.packages.core.backtest.replay import replay_signals
from quantpilot.packages.core.backtest.schemas import (
    AcceptanceThresholds,
    BacktestAssumptions,
    BacktestRequest,
    BacktestResult,
    BacktestSignal,
)
from quantpilot.packages.core.backtest.validation import (
    build_walk_forward_windows,
    evaluate_acceptance,
    trading_dates_from_price_history,
)
from quantpilot.packages.core.data.providers import build_providers
from quantpilot.packages.core.schemas import DataMode
from quantpilot.packages.core.strategies.loader import load_default_strategy


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("LOCAL_DATA_DIR"),
        help="directory with securities.csv/ohlcv.csv (default: LOCAL_DATA_DIR)",
    )
    parser.add_argument("--initial-cash", type=float, default=10_000_000.0)
    parser.add_argument("--fee-bps", type=float, default=15.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument(
        "--sell-tax-bps",
        type=float,
        default=20.0,
        help="sell-side transaction tax in bps (research assumption; default 20)",
    )
    parser.add_argument("--warmup-bars", type=int, default=20)
    parser.add_argument(
        "--limit-buffer-bps",
        type=float,
        default=0.0,
        help="widen limit prices away from signal-day close (buys up, sells down) "
        "to study fill sensitivity of the next_open_limit_touch model",
    )
    parser.add_argument("--train-size", type=int, default=60, help="walk-forward train days")
    parser.add_argument("--test-size", type=int, default=20, help="walk-forward test days")
    parser.add_argument("--min-total-return", type=float, default=None)
    parser.add_argument("--max-drawdown", type=float, default=None)
    parser.add_argument("--min-simplified-sharpe", type=float, default=None)
    parser.add_argument("--min-filled-trades", type=int, default=None)
    parser.add_argument("--max-turnover", type=float, default=None)
    return parser.parse_args(argv)


def _metrics_summary(result: BacktestResult) -> dict[str, Any]:
    return result.metrics.model_dump(mode="json")


def run_local_backtest(args: argparse.Namespace) -> dict[str, Any]:
    if not args.data_dir:
        raise SystemExit("--data-dir is required (or set LOCAL_DATA_DIR)")

    _, market_data_provider = build_providers(
        DataMode.local_historical, data_dir=Path(args.data_dir)
    )
    history = market_data_provider.get_price_history()
    recipe = load_default_strategy()
    signals = replay_signals(
        history,
        warmup_bars=args.warmup_bars,
        limit_buffer_bps=args.limit_buffer_bps,
    )
    assumptions = BacktestAssumptions(
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        sell_tax_bps=args.sell_tax_bps,
    )

    full_request = BacktestRequest(
        strategy_id=recipe.strategy_id,
        recipe_version=recipe.version,
        signals=signals,
        initial_cash=args.initial_cash,
        assumptions=assumptions,
    )
    full_result = run_backtest(full_request, market_data_provider)

    trading_dates = trading_dates_from_price_history(history)
    # The default strategy is rule-based (nothing is fitted on the train span),
    # so walk-forward windows here measure out-of-sample consistency of the
    # fixed rules across successive test spans.
    windows = build_walk_forward_windows(
        trading_dates, train_size=args.train_size, test_size=args.test_size
    )
    window_results: list[dict[str, Any]] = []
    for window in windows:
        window_signals: list[BacktestSignal] = [
            signal
            for signal in signals
            if window.test_start <= signal.signal_date <= window.test_end
        ]
        window_request = BacktestRequest(
            strategy_id=recipe.strategy_id,
            recipe_version=recipe.version,
            signals=window_signals,
            initial_cash=args.initial_cash,
            assumptions=assumptions,
            start_date=window.test_start,
            end_date=window.test_end,
        )
        window_result = run_backtest(window_request, market_data_provider)
        window_results.append(
            {
                "window_id": window.window_id,
                "test_start": window.test_start.isoformat(),
                "test_end": window.test_end.isoformat(),
                "signals": len(window_signals),
                "metrics": _metrics_summary(window_result),
            }
        )

    thresholds = AcceptanceThresholds(
        min_total_return=args.min_total_return,
        max_drawdown=args.max_drawdown,
        min_simplified_sharpe=args.min_simplified_sharpe,
        min_filled_trades=args.min_filled_trades,
        max_turnover=args.max_turnover,
    )
    thresholds_set = any(
        value is not None for value in thresholds.model_dump().values()
    )
    acceptance: dict[str, Any]
    if thresholds_set:
        acceptance = evaluate_acceptance(full_result, thresholds).model_dump(mode="json")
    else:
        acceptance = {
            "evaluated": False,
            "note": "no thresholds supplied; acceptance criteria are a pending human input",
        }

    return {
        "strategy_id": recipe.strategy_id,
        "recipe_version": recipe.version,
        "data_dir": str(args.data_dir),
        "bars": len(history),
        "trading_days": len(trading_dates),
        "period": {
            "start": trading_dates[0].isoformat(),
            "end": trading_dates[-1].isoformat(),
        },
        "replayed_signals": len(signals),
        "limit_buffer_bps": args.limit_buffer_bps,
        "assumptions": assumptions.model_dump(mode="json"),
        "full_period": {
            "metrics": _metrics_summary(full_result),
            "warnings": list(full_result.warnings),
            "dataset_hash": full_result.dataset_hash,
        },
        "walk_forward": {
            "train_size": args.train_size,
            "test_size": args.test_size,
            "windows": window_results,
        },
        "acceptance": acceptance,
        "research_only": full_result.research_only,
        "live_trading_approval": full_result.live_trading_approval,
    }


def main(argv: list[str] | None = None) -> int:
    summary = run_local_backtest(parse_args(argv))
    print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
