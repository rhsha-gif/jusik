from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev

from quantpilot.packages.core.backtest.schemas import (
    BacktestEquityPoint,
    BacktestMetrics,
    BacktestTrade,
)


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def daily_returns(equity_values: list[float]) -> list[float]:
    returns: list[float] = []
    for index in range(1, len(equity_values)):
        previous = equity_values[index - 1]
        current = equity_values[index]
        if previous > 0:
            returns.append(current / previous - 1)
    return returns


def calculate_total_return(equity_values: list[float]) -> float:
    if len(equity_values) < 2 or equity_values[0] <= 0:
        return 0.0
    return _round(equity_values[-1] / equity_values[0] - 1)


def calculate_annualized_return(
    total_return: float,
    periods: int,
    *,
    annualization_days: int = 252,
) -> float | None:
    if periods <= 0 or total_return <= -1:
        return None
    return _round((1 + total_return) ** (annualization_days / periods) - 1)


def calculate_max_drawdown(equity_values: list[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for value in equity_values:
        if value > peak:
            peak = value
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    return _round(max_drawdown)


def calculate_volatility(returns: list[float], *, annualization_days: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    return _round(pstdev(returns) * sqrt(annualization_days))


def calculate_simplified_sharpe(returns: list[float], *, annualization_days: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    volatility = pstdev(returns)
    if volatility == 0:
        return 0.0
    return _round(mean(returns) / volatility * sqrt(annualization_days))


def build_backtest_metrics(
    *,
    equity_curve: list[BacktestEquityPoint],
    trades: list[BacktestTrade],
    annualization_days: int = 252,
) -> BacktestMetrics:
    equity_values = [point.equity for point in equity_curve]
    returns = daily_returns(equity_values)
    total_return = calculate_total_return(equity_values)
    filled_trades = [trade for trade in trades if trade.status == "filled"]
    blocked_trades = [trade for trade in trades if trade.status == "blocked"]
    sell_trades = [trade for trade in filled_trades if trade.side == "sell" and trade.realized_pnl is not None]
    profitable_sells = [trade for trade in sell_trades if (trade.realized_pnl or 0.0) > 0]
    initial_equity = equity_values[0] if equity_values else 0.0
    final_point = equity_curve[-1] if equity_curve else None
    turnover = sum(trade.notional for trade in filled_trades) / initial_equity if initial_equity > 0 else 0.0
    exposure_values = [
        point.positions_value / point.equity
        for point in equity_curve
        if point.equity > 0
    ]
    cash_utilization_values = [
        max(0.0, 1 - point.cash / point.equity)
        for point in equity_curve
        if point.equity > 0
    ]

    return BacktestMetrics(
        total_return=total_return,
        annualized_return=calculate_annualized_return(
            total_return,
            len(equity_curve) - 1,
            annualization_days=annualization_days,
        ),
        max_drawdown=calculate_max_drawdown(equity_values),
        volatility=calculate_volatility(returns, annualization_days=annualization_days),
        simplified_sharpe=calculate_simplified_sharpe(returns, annualization_days=annualization_days),
        turnover=_round(turnover),
        hit_rate=_round(len(profitable_sells) / len(sell_trades)) if sell_trades else 0.0,
        exposure=_round(mean(exposure_values)) if exposure_values else 0.0,
        cash_utilization=_round(mean(cash_utilization_values)) if cash_utilization_values else 0.0,
        number_of_rebalances=len(filled_trades),
        number_of_blocked_trades=len(blocked_trades),
        filled_trades=len(filled_trades),
        final_cash=_round(final_point.cash) if final_point else 0.0,
        final_gross_exposure=_round(final_point.gross_exposure) if final_point else 0.0,
    )
