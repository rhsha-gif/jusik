from __future__ import annotations

from datetime import date
from typing import Any, Sequence

from quantpilot.packages.core.backtest.schemas import (
    AcceptanceCheck,
    AcceptanceEvaluation,
    AcceptanceThresholds,
    BacktestResult,
    BacktestWindow,
)


def _parse_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def trading_dates_from_price_history(rows: Sequence[dict[str, Any]]) -> list[date]:
    return sorted({_parse_date(row["date"]) for row in rows})


def build_train_test_window(
    trading_dates: Sequence[date | str],
    *,
    train_size: int,
    test_size: int,
) -> BacktestWindow:
    windows = build_walk_forward_windows(
        trading_dates,
        train_size=train_size,
        test_size=test_size,
        step_size=test_size,
    )
    if not windows:
        raise ValueError("not enough trading dates for a train/test split")
    return windows[0]


def build_walk_forward_windows(
    trading_dates: Sequence[date | str],
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
) -> list[BacktestWindow]:
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    step = step_size or test_size
    if step <= 0:
        raise ValueError("step_size must be positive")

    dates = sorted({_parse_date(value) for value in trading_dates})
    windows: list[BacktestWindow] = []
    start = 0
    while start + train_size + test_size <= len(dates):
        train = dates[start : start + train_size]
        test = dates[start + train_size : start + train_size + test_size]
        windows.append(
            BacktestWindow(
                window_id=f"wf_{len(windows) + 1:03d}",
                train_start=train[0],
                train_end=train[-1],
                test_start=test[0],
                test_end=test[-1],
                train_days=len(train),
                test_days=len(test),
            )
        )
        start += step
    return windows


def overfit_warnings(*, filled_trades: int, tested_variants: int | None) -> list[str]:
    if tested_variants is None or tested_variants <= 0:
        return []
    if filled_trades == 0:
        return [f"overfit_risk: tested_variants={tested_variants} with no filled trades"]
    if tested_variants > filled_trades:
        return [f"overfit_risk: tested_variants={tested_variants} exceeds filled_trades={filled_trades}"]
    return []


def _check_min(name: str, observed: float | int | None, threshold: float | int | None) -> AcceptanceCheck | None:
    if threshold is None:
        return None
    passed = observed is not None and observed >= threshold
    return AcceptanceCheck(
        name=name,
        passed=passed,
        observed=observed,
        threshold=threshold,
        detail=f"{name}: observed {observed} >= threshold {threshold}",
    )


def _check_max(name: str, observed: float | int | None, threshold: float | int | None) -> AcceptanceCheck | None:
    if threshold is None:
        return None
    passed = observed is not None and observed <= threshold
    return AcceptanceCheck(
        name=name,
        passed=passed,
        observed=observed,
        threshold=threshold,
        detail=f"{name}: observed {observed} <= threshold {threshold}",
    )


def evaluate_acceptance(
    result: BacktestResult,
    thresholds: AcceptanceThresholds,
) -> AcceptanceEvaluation:
    checks = [
        _check_min("min_total_return", result.metrics.total_return, thresholds.min_total_return),
        _check_min("min_annualized_return", result.metrics.annualized_return, thresholds.min_annualized_return),
        _check_max("max_drawdown", result.metrics.max_drawdown, thresholds.max_drawdown),
        _check_min("min_simplified_sharpe", result.metrics.simplified_sharpe, thresholds.min_simplified_sharpe),
        _check_min("min_filled_trades", result.metrics.filled_trades, thresholds.min_filled_trades),
        _check_max("max_turnover", result.metrics.turnover, thresholds.max_turnover),
    ]
    material_checks = [check for check in checks if check is not None]
    warnings = list(result.warnings)
    if not result.research_only or result.live_trading_approval:
        warnings.append("acceptance_results_must_remain_research_only")
        material_checks.append(
            AcceptanceCheck(
                name="research_only",
                passed=False,
                observed=0,
                threshold=1,
                detail="acceptance evaluation cannot grant live-trading approval",
            )
        )
    return AcceptanceEvaluation(
        passed=all(check.passed for check in material_checks),
        checks=material_checks,
        warnings=warnings,
        research_only=True,
        live_trading_approval=False,
    )
