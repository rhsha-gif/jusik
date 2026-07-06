from __future__ import annotations

import json
from datetime import date
from hashlib import sha256
from statistics import mean
from typing import Any, Sequence

from quantpilot.packages.core.backtest.engine import run_backtest
from quantpilot.packages.core.backtest.schemas import BacktestRequest, BacktestResult
from quantpilot.packages.core.backtest.validation import trading_dates_from_price_history
from quantpilot.packages.core.data.providers import MarketDataProvider
from quantpilot.packages.core.validation.types import (
    BenchmarkRelativeAttribution,
    DataModeLabel,
    PurgeEmbargoMetadata,
    SlippageScenarioResult,
    SlippageSensitivityResult,
    ValidationRunResult,
    WalkForwardSplit,
)


MarketDataSource = MarketDataProvider | list[dict[str, Any]] | dict[str, list[dict[str, Any]]]


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def _parse_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _load_price_history(source: MarketDataSource) -> list[dict[str, Any]]:
    if hasattr(source, "get_price_history"):
        return [dict(row) for row in source.get_price_history()]  # type: ignore[union-attr]
    if isinstance(source, list):
        return [dict(row) for row in source]
    if isinstance(source, dict):
        rows: list[dict[str, Any]] = []
        for symbol, symbol_rows in source.items():
            for row in symbol_rows:
                copied = dict(row)
                copied.setdefault("symbol", symbol)
                rows.append(copied)
        return rows
    raise TypeError("market data source must be a MarketDataProvider, list of bars, or dict of bars")


def trading_dates_from_market_data(source: MarketDataSource) -> list[date]:
    return trading_dates_from_price_history(_load_price_history(source))


def build_walk_forward_splits(
    trading_dates: Sequence[date | str],
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
    purge_days: int = 0,
    embargo_days: int = 0,
) -> list[WalkForwardSplit]:
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    step = step_size or test_size
    if step <= 0:
        raise ValueError("step_size must be positive")
    if purge_days < 0 or embargo_days < 0:
        raise ValueError("purge_days and embargo_days must be non-negative")

    dates = sorted({_parse_date(value) for value in trading_dates})
    windows: list[WalkForwardSplit] = []
    start = 0
    required = train_size + purge_days + test_size
    while start + required <= len(dates):
        train_start_index = start
        train_end_index = start + train_size - 1
        purge_start_index = train_end_index + 1
        test_start_index = purge_start_index + purge_days
        test_end_index = test_start_index + test_size - 1
        embargo_start_index = test_end_index + 1
        embargo_end_index = min(embargo_start_index + embargo_days, len(dates)) - 1

        purge_start = dates[purge_start_index] if purge_days else None
        purge_end = dates[test_start_index - 1] if purge_days else None
        embargo_start = (
            dates[embargo_start_index]
            if embargo_days and embargo_start_index < len(dates)
            else None
        )
        embargo_end = (
            dates[embargo_end_index]
            if embargo_days and embargo_start_index <= embargo_end_index
            else None
        )

        split_id = f"wf_{len(windows) + 1:03d}"
        windows.append(
            WalkForwardSplit(
                split_id=split_id,
                train_start=dates[train_start_index],
                train_end=dates[train_end_index],
                test_start=dates[test_start_index],
                test_end=dates[test_end_index],
                train_days=train_size,
                test_days=test_size,
                purge_embargo=PurgeEmbargoMetadata(
                    purge_days=purge_days,
                    embargo_days=embargo_days,
                    purge_start=purge_start,
                    purge_end=purge_end,
                    embargo_start=embargo_start,
                    embargo_end=embargo_end,
                    removed_between_train_and_test_days=purge_days,
                    embargoed_after_test_days=max(0, embargo_end_index - embargo_start_index + 1)
                    if embargo_start is not None
                    else 0,
                ),
            )
        )
        start += step
    return windows


def _request_for_test_window(
    request: BacktestRequest,
    *,
    start_date: date,
    end_date: date,
) -> BacktestRequest:
    test_signals = [
        signal
        for signal in request.signals
        if start_date <= _parse_date(signal.signal_date) <= end_date
    ]
    return request.model_copy(
        update={
            "signals": test_signals,
            "start_date": start_date,
            "end_date": end_date,
        }
    )


def _validation_run_id(split: WalkForwardSplit, result: BacktestResult | None, error: str | None) -> str:
    payload = {
        "split": split.model_dump(mode="json"),
        "result_id": result.result_id if result is not None else None,
        "error": error,
    }
    return f"val_{_stable_hash(payload)[:24]}"


def _metrics_summary(result: BacktestResult) -> dict[str, float | int | None]:
    return {
        "total_return": result.metrics.total_return,
        "annualized_return": result.metrics.annualized_return,
        "max_drawdown": result.metrics.max_drawdown,
        "simplified_sharpe": result.metrics.simplified_sharpe,
        "turnover": result.metrics.turnover,
        "filled_trades": result.metrics.filled_trades,
        "blocked_trades": result.metrics.number_of_blocked_trades,
    }


def run_walk_forward_validation(
    request: BacktestRequest,
    market_data_source: MarketDataSource,
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
    purge_days: int = 0,
    embargo_days: int = 0,
    data_mode: DataModeLabel = "fixture",
) -> list[ValidationRunResult]:
    rows = _load_price_history(market_data_source)
    splits = build_walk_forward_splits(
        trading_dates_from_price_history(rows),
        train_size=train_size,
        test_size=test_size,
        step_size=step_size,
        purge_days=purge_days,
        embargo_days=embargo_days,
    )

    results: list[ValidationRunResult] = []
    for split in splits:
        try:
            split_request = _request_for_test_window(
                request,
                start_date=split.test_start,
                end_date=split.test_end,
            )
            result = run_backtest(split_request, rows)
            warning_set = sorted(
                set(
                    result.warnings
                    + [
                        "deterministic_research_only",
                        "walk_forward_validation_does_not_grant_promotion",
                    ]
                )
            )
            results.append(
                ValidationRunResult(
                    run_id=_validation_run_id(split, result, None),
                    split=split,
                    status="completed",
                    data_mode=data_mode,
                    backtest_result=result,
                    metrics=_metrics_summary(result),
                    warnings=warning_set,
                )
            )
        except Exception as exc:  # pragma: no cover - exercised by fail-closed callers.
            error = str(exc)
            results.append(
                ValidationRunResult(
                    run_id=_validation_run_id(split, None, error),
                    split=split,
                    status="unavailable",
                    data_mode=data_mode,
                    warnings=["validation_run_unavailable", "promotion_blocked_fail_closed"],
                    error=error,
                )
            )
    return results


def run_slippage_sensitivity(
    request: BacktestRequest,
    market_data_source: MarketDataSource,
    *,
    slippage_bps_values: Sequence[float],
    data_mode: DataModeLabel = "fixture",
) -> SlippageSensitivityResult:
    if not slippage_bps_values:
        raise ValueError("slippage_bps_values must not be empty")
    rows = _load_price_history(market_data_source)
    scenarios: list[SlippageScenarioResult] = []
    for slippage_bps in sorted({float(value) for value in slippage_bps_values}):
        assumptions = request.assumptions.model_copy(update={"slippage_bps": slippage_bps})
        scenario_request = request.model_copy(update={"assumptions": assumptions})
        scenario_id = f"slip_{_stable_hash({'slippage_bps': slippage_bps, 'request': scenario_request.model_dump(mode='json')})[:16]}"
        try:
            result = run_backtest(scenario_request, rows)
            scenarios.append(
                SlippageScenarioResult(
                    scenario_id=scenario_id,
                    slippage_bps=slippage_bps,
                    status="completed",
                    result_id=result.result_id,
                    total_return=result.metrics.total_return,
                    max_drawdown=result.metrics.max_drawdown,
                    simplified_sharpe=result.metrics.simplified_sharpe,
                    filled_trades=result.metrics.filled_trades,
                    warnings=result.warnings,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive fail-closed path.
            scenarios.append(
                SlippageScenarioResult(
                    scenario_id=scenario_id,
                    slippage_bps=slippage_bps,
                    status="unavailable",
                    warnings=["slippage_scenario_unavailable", "promotion_blocked_fail_closed"],
                    error=str(exc),
                )
            )

    completed_returns = [
        scenario.total_return
        for scenario in scenarios
        if scenario.status == "completed" and scenario.total_return is not None
    ]
    if len(completed_returns) == len(scenarios):
        status = "completed"
    elif completed_returns:
        status = "partial"
    else:
        status = "unavailable"
    worst = min(completed_returns) if completed_returns else None
    best = max(completed_returns) if completed_returns else None
    warnings = ["slippage_sensitivity_does_not_grant_promotion"]
    if status != "completed":
        warnings.append("slippage_sensitivity_incomplete")
    return SlippageSensitivityResult(
        base_slippage_bps=request.assumptions.slippage_bps,
        data_mode=data_mode,
        status=status,
        scenarios=scenarios,
        worst_total_return=_round(worst) if worst is not None else None,
        best_total_return=_round(best) if best is not None else None,
        total_return_range=_round(best - worst) if best is not None and worst is not None else None,
        warnings=warnings,
    )


def _benchmark_value(row: dict[str, Any]) -> float:
    for key in ("value", "close", "equity", "benchmark_close"):
        if key in row:
            return float(row[key])
    raise ValueError("benchmark row must include one of value, close, equity, or benchmark_close")


def _daily_returns(values: list[tuple[date, float]]) -> dict[date, float]:
    returns: dict[date, float] = {}
    for index in range(1, len(values)):
        previous = values[index - 1][1]
        current = values[index][1]
        if previous > 0:
            returns[values[index][0]] = current / previous - 1
    return returns


def build_benchmark_relative_attribution(
    result: BacktestResult,
    benchmark_series: Sequence[dict[str, Any]],
    *,
    benchmark_label: str = "benchmark",
) -> BenchmarkRelativeAttribution:
    benchmark_by_date = {
        _parse_date(row["date"]): _benchmark_value(row)
        for row in benchmark_series
    }
    strategy_by_date = {point.date: point.equity for point in result.equity_curve}
    matched_dates = sorted(set(strategy_by_date).intersection(benchmark_by_date))
    if len(matched_dates) < 2:
        return BenchmarkRelativeAttribution(
            benchmark_label=benchmark_label,
            status="unavailable",
            matched_days=len(matched_dates),
            warnings=["benchmark_overlap_insufficient", "promotion_blocked_fail_closed"],
        )

    start = matched_dates[0]
    end = matched_dates[-1]
    strategy_start = strategy_by_date[start]
    strategy_end = strategy_by_date[end]
    benchmark_start = benchmark_by_date[start]
    benchmark_end = benchmark_by_date[end]
    if strategy_start <= 0 or benchmark_start <= 0:
        return BenchmarkRelativeAttribution(
            benchmark_label=benchmark_label,
            status="unavailable",
            start_date=start,
            end_date=end,
            matched_days=len(matched_dates),
            warnings=["benchmark_or_strategy_start_value_invalid", "promotion_blocked_fail_closed"],
        )

    aligned_strategy = [(session, strategy_by_date[session]) for session in matched_dates]
    aligned_benchmark = [(session, benchmark_by_date[session]) for session in matched_dates]
    strategy_returns = _daily_returns(aligned_strategy)
    benchmark_returns = _daily_returns(aligned_benchmark)
    daily_excess_values = [
        strategy_returns[session] - benchmark_returns[session]
        for session in sorted(set(strategy_returns).intersection(benchmark_returns))
    ]
    strategy_total_return = strategy_end / strategy_start - 1
    benchmark_total_return = benchmark_end / benchmark_start - 1
    return BenchmarkRelativeAttribution(
        benchmark_label=benchmark_label,
        status="completed",
        start_date=start,
        end_date=end,
        matched_days=len(matched_dates),
        strategy_total_return=_round(strategy_total_return),
        benchmark_total_return=_round(benchmark_total_return),
        excess_return=_round(strategy_total_return - benchmark_total_return),
        average_daily_excess_return=_round(mean(daily_excess_values)) if daily_excess_values else 0.0,
        warnings=["benchmark_relative_attribution_does_not_grant_promotion"],
    )
