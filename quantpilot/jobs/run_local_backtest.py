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

from quantpilot.packages.core.backtest.costs import (
    KIS_BANKIS_ONLINE_FEE_BPS,
    KRX_SELL_TAX_BPS_FROM_2026,
    RESEARCH_SLIPPAGE_BPS,
    cost_basis_label,
)
from quantpilot.packages.core.backtest.engine import run_backtest
from quantpilot.packages.core.backtest.metrics import daily_returns
from quantpilot.packages.core.backtest.replay import replay_signals
from quantpilot.packages.core.backtest.schemas import (
    AcceptanceThresholds,
    BacktestAssumptions,
    BacktestRequest,
    BacktestResult,
    BacktestSignal,
)
from quantpilot.packages.core.backtest.statistics import (
    deflated_sharpe,
    min_track_record_length,
    sharpe_moments,
    trial_sharpe_variance,
)
from quantpilot.packages.core.backtest.validation import (
    build_walk_forward_windows,
    evaluate_acceptance,
    trading_dates_from_price_history,
)
from quantpilot.packages.core.data.providers import build_providers
from quantpilot.packages.core.schemas import DataMode
from quantpilot.packages.core.strategies.loader import load_default_strategy


def _parse_sharpe_list(value: str) -> list[float]:
    try:
        return [float(item) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"--trial-sharpes must be comma-separated numbers, got {value!r}"
        ) from error


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"expected a non-negative integer, got {value!r}")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("LOCAL_DATA_DIR"),
        help="directory with securities.csv/ohlcv.csv (default: LOCAL_DATA_DIR)",
    )
    parser.add_argument("--initial-cash", type=float, default=10_000_000.0)
    parser.add_argument(
        "--fee-bps",
        type=float,
        default=KIS_BANKIS_ONLINE_FEE_BPS,
        help="per-side commission in bps (default: KIS BanKIS online 0.0140527%%; "
        "branch-opened accounts should pass 14.7)",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=RESEARCH_SLIPPAGE_BPS,
        help="research slippage assumption in bps (not broker-confirmed)",
    )
    parser.add_argument(
        "--sell-tax-bps",
        type=float,
        default=KRX_SELL_TAX_BPS_FROM_2026,
        help="sell-side transaction tax in bps (default: KRX 2026 schedule, "
        "KOSPI 0.05%%+rural 0.15%% / KOSDAQ 0.20%%)",
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
    parser.add_argument(
        "--purge-bars",
        type=_non_negative_int,
        default=0,
        help="drop this many bars from the end of each walk-forward train span",
    )
    parser.add_argument(
        "--embargo-bars",
        type=_non_negative_int,
        default=0,
        help="gap of this many bars between each train end and test start",
    )
    parser.add_argument(
        "--trials-so-far",
        type=_non_negative_int,
        default=0,
        help="number of prior strategy variants already tested on this data "
        "(n_trials for the deflated Sharpe ratio = trials_so_far + 1)",
    )
    parser.add_argument(
        "--trial-sharpes",
        type=_parse_sharpe_list,
        default=None,
        help="comma-separated per-period Sharpe ratios of the prior trials; "
        "when absent, the trial variance falls back to the spread of this run's "
        "walk-forward window Sharpes (an approximation)",
    )
    parser.add_argument("--min-total-return", type=float, default=None)
    parser.add_argument("--max-drawdown", type=float, default=None)
    parser.add_argument("--min-simplified-sharpe", type=float, default=None)
    parser.add_argument("--min-filled-trades", type=int, default=None)
    parser.add_argument("--max-turnover", type=float, default=None)
    return parser.parse_args(argv)


def _metrics_summary(result: BacktestResult) -> dict[str, Any]:
    return result.metrics.model_dump(mode="json")


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _per_period_sharpe(result: BacktestResult) -> float | None:
    return sharpe_moments(daily_returns([point.equity for point in result.equity_curve])).sharpe


def _statistics_summary(
    result: BacktestResult,
    *,
    trials_so_far: int,
    trial_sharpes: list[float] | None,
    window_sharpes: list[float],
) -> dict[str, Any]:
    """PSR/DSR/MinTRL of the full-period equity curve (per-period, not annualized).

    ``n_trials`` counts this run as one more trial on top of ``trials_so_far``.
    The trial-Sharpe variance comes from ``--trial-sharpes`` when supplied;
    otherwise the spread of this run's walk-forward window Sharpes stands in
    for it, which is only an approximation of the true cross-trial variance.
    """
    moments = sharpe_moments(daily_returns([point.equity for point in result.equity_curve]))
    n_trials = trials_so_far + 1
    if trial_sharpes is not None:
        variance_source = "trial_sharpes"
        trials_sr_variance = trial_sharpe_variance(trial_sharpes)
    else:
        variance_source = "walk_forward_windows"
        trials_sr_variance = trial_sharpe_variance(window_sharpes)

    psr = dsr = expected_max_sr = min_trl = None
    if moments.sharpe is not None and moments.skew is not None and moments.kurtosis is not None:
        deflated = deflated_sharpe(
            moments.sharpe,
            n=moments.n,
            skew=moments.skew,
            kurtosis=moments.kurtosis,
            n_trials=n_trials,
            trials_sr_variance=trials_sr_variance,
        )
        psr, dsr, expected_max_sr = deflated.psr, deflated.dsr, deflated.expected_max_sr
        min_trl = min_track_record_length(
            moments.sharpe, skew=moments.skew, kurtosis=moments.kurtosis, confidence=0.95
        )

    return {
        "sharpe_per_period": _round_or_none(moments.sharpe),
        "n": moments.n,
        "skew": _round_or_none(moments.skew),
        "kurtosis": _round_or_none(moments.kurtosis),
        "psr": _round_or_none(psr),
        "dsr": _round_or_none(dsr),
        "expected_max_sr": _round_or_none(expected_max_sr),
        "n_trials": n_trials,
        "trials_sr_variance": _round_or_none(trials_sr_variance),
        "min_trl_95": _round_or_none(min_trl),
        "variance_source": variance_source,
    }


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
        trading_dates,
        train_size=args.train_size,
        test_size=args.test_size,
        purge_bars=args.purge_bars,
        embargo_bars=args.embargo_bars,
    )
    window_results: list[dict[str, Any]] = []
    window_sharpes: list[float] = []
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
        window_sharpe = _per_period_sharpe(window_result)
        if window_sharpe is not None:
            window_sharpes.append(window_sharpe)
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
        "cost_basis": cost_basis_label(assumptions),
        "full_period": {
            "metrics": _metrics_summary(full_result),
            "warnings": list(full_result.warnings),
            "dataset_hash": full_result.dataset_hash,
        },
        "walk_forward": {
            "train_size": args.train_size,
            "test_size": args.test_size,
            "purge_bars": args.purge_bars,
            "embargo_bars": args.embargo_bars,
            "windows": window_results,
        },
        "statistics": _statistics_summary(
            full_result,
            trials_so_far=args.trials_so_far,
            trial_sharpes=args.trial_sharpes,
            window_sharpes=window_sharpes,
        ),
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
