from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any, Literal

from quantpilot.packages.core.backtest.metrics import build_backtest_metrics
from quantpilot.packages.core.backtest.schemas import (
    BacktestEquityPoint,
    BacktestRequest,
    BacktestResult,
    BacktestSignal,
    BacktestTrade,
)
from quantpilot.packages.core.backtest.validation import overfit_warnings
from quantpilot.packages.core.data.providers import MarketDataProvider
from quantpilot.packages.core.normalization import symbol_key
from quantpilot.packages.core.schemas import SignalAction


@dataclass(frozen=True)
class _PendingExecution:
    signal: BacktestSignal
    side: Literal["buy", "sell"]
    fill_date: date
    signal_price: float
    limit_price: float
    reference_price: float
    fill_price: float
    target_notional: float
    target_weight: float


@dataclass(frozen=True)
class _PreparedBacktest:
    data_boundary: str
    rows: list[dict[str, Any]]
    dataset_hash: str
    input_hash: str
    rows_by_symbol: dict[str, list[dict[str, Any]]]
    trading_dates: list[date]
    signals_by_date: dict[date, list[BacktestSignal]]


@dataclass(frozen=True)
class _BacktestLoopResult:
    trades: list[BacktestTrade]
    equity_curve: list[BacktestEquityPoint]


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_rows(rows: list[dict[str, Any]], request: BacktestRequest) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        symbol = symbol_key(row.get("symbol", row.get("ticker", "")))
        if not symbol:
            raise ValueError("price history row is missing symbol/ticker")
        session = _parse_date(row["date"])
        if request.start_date is not None and session < request.start_date:
            continue
        if request.end_date is not None and session > request.end_date:
            continue
        normalized.append(
            {
                "symbol": symbol,
                "ticker": symbol,
                "date": session,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
        )
    normalized.sort(key=lambda item: (item["date"], item["symbol"]))
    if not normalized:
        raise ValueError("backtest requires at least one price history row")
    return normalized


def _serializable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": row["symbol"],
            "date": row["date"].isoformat(),
            "open": _round(row["open"]),
            "high": _round(row["high"]),
            "low": _round(row["low"]),
            "close": _round(row["close"]),
            "volume": _round(row["volume"]),
        }
        for row in rows
    ]


def _target_weight(signal: BacktestSignal, current_weight: float) -> float:
    if signal.action == SignalAction.buy_ready:
        if signal.target_weight_hint is not None:
            return _round(signal.target_weight_hint)
        return _round(min(0.15, max(0.01, signal.strength * 0.15)))
    if signal.action == SignalAction.trim:
        target = signal.target_weight_hint if signal.target_weight_hint is not None else current_weight * 0.5
        return _round(min(current_weight, max(0.0, target)))
    if signal.action == SignalAction.exit:
        return 0.0
    return _round(current_weight)


def _blocked_trade(
    signal: BacktestSignal,
    *,
    reason: str,
    side: Literal["buy", "sell", "none"] = "none",
    signal_price: float | None = None,
    limit_price: float | None = None,
    fill_date: date | None = None,
    target_weight: float = 0.0,
) -> BacktestTrade:
    return BacktestTrade(
        symbol=signal.symbol,
        side=side,
        status="blocked",
        signal_date=signal.signal_date,
        fill_date=fill_date,
        signal_price=_round(signal_price) if signal_price is not None else None,
        limit_price=_round(limit_price) if limit_price is not None else None,
        target_weight=_round(target_weight),
        blocked_reason=reason,
        reason=signal.reason,
    )


def _close_prices_for_date(rows: list[dict[str, Any]], session: date) -> dict[str, float]:
    return {row["symbol"]: row["close"] for row in rows if row["date"] == session}


def _portfolio_value(
    *,
    cash: float,
    positions: dict[str, float],
    prices: dict[str, float],
) -> tuple[float, float]:
    positions_value = 0.0
    for symbol, quantity in positions.items():
        price = prices.get(symbol)
        if price is not None:
            positions_value += quantity * price
    return cash + positions_value, positions_value


def _record_equity_point(
    *,
    session: date,
    cash: float,
    positions: dict[str, float],
    prices: dict[str, float],
    previous_equity: float | None,
) -> BacktestEquityPoint:
    equity, positions_value = _portfolio_value(cash=cash, positions=positions, prices=prices)
    daily_return = equity / previous_equity - 1 if previous_equity and previous_equity > 0 else 0.0
    gross_exposure = positions_value / equity if equity > 0 else 0.0
    return BacktestEquityPoint(
        date=session,
        cash=_round(cash),
        positions_value=_round(positions_value),
        gross_exposure=_round(gross_exposure),
        equity=_round(equity),
        daily_return=_round(daily_return),
        positions={symbol: _round(quantity) for symbol, quantity in sorted(positions.items()) if quantity > 0},
    )


def _signal_execution_bars(
    signal: BacktestSignal,
    symbol_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], float, float] | BacktestTrade:
    current_bar = next((row for row in symbol_rows if row["date"] == signal.signal_date), None)
    if current_bar is None:
        return _blocked_trade(signal, reason="no_signal_bar")
    next_bar = next((row for row in symbol_rows if row["date"] > signal.signal_date), None)
    signal_price = float(current_bar["close"])
    limit_price = float(signal.limit_price or signal_price)
    if next_bar is None:
        return _blocked_trade(signal, reason="no_next_bar", signal_price=signal_price, limit_price=limit_price)
    return next_bar, signal_price, limit_price


def _target_notional_and_side(
    *,
    signal: BacktestSignal,
    next_bar: dict[str, Any],
    signal_price: float,
    limit_price: float,
    positions: dict[str, float],
    equity: float,
) -> tuple[float, float, Literal["buy", "sell"]] | BacktestTrade:
    current_quantity = positions.get(signal.symbol, 0.0)
    current_value = current_quantity * signal_price
    current_weight = current_value / equity
    target_weight = _target_weight(signal, current_weight)
    target_value = target_weight * equity

    if signal.action == SignalAction.buy_ready:
        return target_value - current_value, target_weight, "buy"
    if current_quantity <= 0:
        return _blocked_trade(
            signal,
            reason="no_position",
            side="sell",
            signal_price=signal_price,
            limit_price=limit_price,
            fill_date=next_bar["date"],
            target_weight=target_weight,
        )
    return current_value - target_value, target_weight, "sell"


def _limit_fill_prices(
    *,
    signal: BacktestSignal,
    side: Literal["buy", "sell"],
    next_bar: dict[str, Any],
    signal_price: float,
    limit_price: float,
    target_weight: float,
    slippage_bps: float,
) -> tuple[float, float] | BacktestTrade:
    if side == "buy":
        if float(next_bar["low"]) > limit_price:
            return _blocked_trade(
                signal,
                reason="limit_not_touched",
                side=side,
                signal_price=signal_price,
                limit_price=limit_price,
                fill_date=next_bar["date"],
                target_weight=target_weight,
            )
        reference_price = min(float(next_bar["open"]), limit_price)
        return reference_price, reference_price * (1 + slippage_bps / 10_000)

    if float(next_bar["high"]) < limit_price:
        return _blocked_trade(
            signal,
            reason="limit_not_touched",
            side=side,
            signal_price=signal_price,
            limit_price=limit_price,
            fill_date=next_bar["date"],
            target_weight=target_weight,
        )
    reference_price = max(float(next_bar["open"]), limit_price)
    return reference_price, reference_price * (1 - slippage_bps / 10_000)


def _schedule_signal(
    *,
    signal: BacktestSignal,
    symbol_rows: list[dict[str, Any]],
    positions: dict[str, float],
    prices: dict[str, float],
    cash: float,
    min_trade_notional: float,
    slippage_bps: float,
) -> _PendingExecution | BacktestTrade | None:
    if signal.action == SignalAction.blocked:
        return _blocked_trade(signal, reason="blocked_signal")
    if signal.action in {SignalAction.buy_wait, SignalAction.hold, SignalAction.watch}:
        return None

    bars = _signal_execution_bars(signal, symbol_rows)
    if isinstance(bars, BacktestTrade):
        return bars
    next_bar, signal_price, limit_price = bars
    equity, _positions_value = _portfolio_value(cash=cash, positions=positions, prices=prices)
    if equity <= 0:
        return _blocked_trade(signal, reason="no_equity", signal_price=signal_price, limit_price=limit_price)
    target = _target_notional_and_side(
        signal=signal,
        next_bar=next_bar,
        signal_price=signal_price,
        limit_price=limit_price,
        positions=positions,
        equity=equity,
    )
    if isinstance(target, BacktestTrade):
        return target
    target_notional, target_weight, side = target

    if target_notional < min_trade_notional:
        return _blocked_trade(
            signal,
            reason="below_min_trade_notional",
            side=side,
            signal_price=signal_price,
            limit_price=limit_price,
            fill_date=next_bar["date"],
            target_weight=target_weight,
        )

    fill_prices = _limit_fill_prices(
        signal=signal,
        side=side,
        next_bar=next_bar,
        signal_price=signal_price,
        limit_price=limit_price,
        target_weight=target_weight,
        slippage_bps=slippage_bps,
    )
    if isinstance(fill_prices, BacktestTrade):
        return fill_prices
    reference_price, fill_price = fill_prices

    return _PendingExecution(
        signal=signal,
        side=side,
        fill_date=next_bar["date"],
        signal_price=signal_price,
        limit_price=limit_price,
        reference_price=reference_price,
        fill_price=fill_price,
        target_notional=target_notional,
        target_weight=target_weight,
    )


def _filled_trade(
    pending: _PendingExecution,
    *,
    quantity: float,
    notional: float,
    fees: float,
    tax: float,
    realized_pnl: float | None,
) -> BacktestTrade:
    slippage_cost = abs(pending.fill_price - pending.reference_price) * quantity
    return BacktestTrade(
        symbol=pending.signal.symbol,
        side=pending.side,
        status="filled",
        signal_date=pending.signal.signal_date,
        fill_date=pending.fill_date,
        signal_price=_round(pending.signal_price),
        limit_price=_round(pending.limit_price),
        fill_price=_round(pending.fill_price),
        quantity=_round(quantity),
        notional=_round(notional),
        fees=_round(fees),
        slippage_cost=_round(slippage_cost),
        tax=_round(tax),
        target_weight=_round(pending.target_weight),
        realized_pnl=_round(realized_pnl) if realized_pnl is not None else None,
        reason=pending.signal.reason,
    )


def _execute_buy_pending(
    pending: _PendingExecution,
    *,
    cash: float,
    positions: dict[str, float],
    cost_basis: dict[str, float],
    fee_rate: float,
    min_trade_notional: float,
) -> tuple[float, BacktestTrade]:
    quantity = pending.target_notional / pending.fill_price if pending.fill_price > 0 else 0.0
    notional = quantity * pending.fill_price
    fees = notional * fee_rate
    if notional < min_trade_notional:
        return cash, _blocked_trade(
            pending.signal,
            reason="below_min_trade_notional",
            side="buy",
            signal_price=pending.signal_price,
            limit_price=pending.limit_price,
            fill_date=pending.fill_date,
            target_weight=pending.target_weight,
        )
    if cash + 1e-9 < notional + fees:
        return cash, _blocked_trade(
            pending.signal,
            reason="insufficient_cash",
            side="buy",
            signal_price=pending.signal_price,
            limit_price=pending.limit_price,
            fill_date=pending.fill_date,
            target_weight=pending.target_weight,
        )
    cash -= notional + fees
    positions[pending.signal.symbol] = positions.get(pending.signal.symbol, 0.0) + quantity
    cost_basis[pending.signal.symbol] = cost_basis.get(pending.signal.symbol, 0.0) + notional
    return cash, _filled_trade(
        pending,
        quantity=quantity,
        notional=notional,
        fees=fees,
        tax=0.0,
        realized_pnl=None,
    )


def _execute_sell_pending(
    pending: _PendingExecution,
    *,
    cash: float,
    positions: dict[str, float],
    cost_basis: dict[str, float],
    fee_rate: float,
    tax_rate: float,
    min_trade_notional: float,
) -> tuple[float, BacktestTrade]:
    available_quantity = positions.get(pending.signal.symbol, 0.0)
    if available_quantity <= 0:
        return cash, _blocked_trade(
            pending.signal,
            reason="no_position",
            side="sell",
            signal_price=pending.signal_price,
            limit_price=pending.limit_price,
            fill_date=pending.fill_date,
            target_weight=pending.target_weight,
        )
    quantity = min(available_quantity, pending.target_notional / pending.fill_price)
    notional = quantity * pending.fill_price
    if notional < min_trade_notional:
        return cash, _blocked_trade(
            pending.signal,
            reason="below_min_trade_notional",
            side="sell",
            signal_price=pending.signal_price,
            limit_price=pending.limit_price,
            fill_date=pending.fill_date,
            target_weight=pending.target_weight,
        )
    fees = notional * fee_rate
    tax = notional * tax_rate
    basis = cost_basis.get(pending.signal.symbol, 0.0)
    average_basis = basis / available_quantity if available_quantity > 0 else 0.0
    realized_pnl = notional - fees - tax - average_basis * quantity
    cash += notional - fees - tax
    remaining_quantity = available_quantity - quantity
    if remaining_quantity <= 1e-9:
        positions.pop(pending.signal.symbol, None)
        cost_basis.pop(pending.signal.symbol, None)
    else:
        positions[pending.signal.symbol] = remaining_quantity
        cost_basis[pending.signal.symbol] = max(0.0, basis - average_basis * quantity)
    return cash, _filled_trade(
        pending,
        quantity=quantity,
        notional=notional,
        fees=fees,
        tax=tax,
        realized_pnl=realized_pnl,
    )


def _execute_pending(
    pending: _PendingExecution,
    *,
    cash: float,
    positions: dict[str, float],
    cost_basis: dict[str, float],
    fee_bps: float,
    sell_tax_bps: float,
    min_trade_notional: float,
) -> tuple[float, BacktestTrade]:
    fee_rate = fee_bps / 10_000
    tax_rate = sell_tax_bps / 10_000
    if pending.side == "buy":
        return _execute_buy_pending(
            pending,
            cash=cash,
            positions=positions,
            cost_basis=cost_basis,
            fee_rate=fee_rate,
            min_trade_notional=min_trade_notional,
        )
    return _execute_sell_pending(
        pending,
        cash=cash,
        positions=positions,
        cost_basis=cost_basis,
        fee_rate=fee_rate,
        tax_rate=tax_rate,
        min_trade_notional=min_trade_notional,
    )


def _build_warnings(
    *,
    request: BacktestRequest,
    trading_dates: list[date],
    filled_trades: int,
) -> list[str]:
    warnings: list[str] = []
    assumptions = request.assumptions
    if len(trading_dates) < assumptions.min_trading_days:
        warnings.append(
            f"insufficient_data: trading_dates={len(trading_dates)} below min_trading_days={assumptions.min_trading_days}"
        )
    if filled_trades < assumptions.min_filled_trades:
        warnings.append(
            f"too_few_trades: filled_trades={filled_trades} below min_filled_trades={assumptions.min_filled_trades}"
        )
    if assumptions.fee_bps <= 0 or assumptions.slippage_bps <= 0:
        warnings.append("unrealistic_cost_assumption: fee_bps and slippage_bps should be positive")
    if assumptions.sell_tax_bps == 0:
        warnings.append("sell_tax_omitted: sell_tax_bps is zero")
    warnings.append("simplified_fill_model: next_open_limit_touch is deterministic research-only")
    warnings.extend(overfit_warnings(filled_trades=filled_trades, tested_variants=request.tested_variants))
    return warnings


def _load_price_history(
    source: MarketDataProvider | list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if isinstance(source, MarketDataProvider):
        return source.get_price_history()
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
    raise TypeError("backtest market data source must be a MarketDataProvider, list of bars, or dict of bars")


def _data_boundary(source: MarketDataProvider | list[dict[str, Any]] | dict[str, list[dict[str, Any]]]) -> str:
    if hasattr(source, "get_price_history"):
        return "MarketDataProvider.get_price_history"
    return "injected_price_history"


def _data_provenance(
    source: MarketDataProvider | list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    get_provenance = getattr(source, "get_provenance", None)
    if callable(get_provenance):
        provenance = get_provenance()
        if isinstance(provenance, dict):
            return dict(provenance)
    return None


def _data_quality(
    source: MarketDataProvider | list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    get_data_quality = getattr(source, "get_data_quality", None)
    if callable(get_data_quality):
        quality = get_data_quality()
        if isinstance(quality, dict):
            return dict(quality)
    return None


def _rows_by_symbol(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_symbol.setdefault(row["symbol"], []).append(row)
    return rows_by_symbol


def _signals_by_date(signals: list[BacktestSignal]) -> dict[date, list[BacktestSignal]]:
    signals_by_date: dict[date, list[BacktestSignal]] = {}
    for signal in signals:
        signals_by_date.setdefault(signal.signal_date, []).append(signal)
    for session_signals in signals_by_date.values():
        session_signals.sort(key=lambda signal: (signal.symbol, signal.action.value, signal.reason))
    return signals_by_date


def _prepare_backtest(
    request: BacktestRequest,
    market_data_provider: MarketDataProvider | list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
) -> _PreparedBacktest:
    data_boundary = _data_boundary(market_data_provider)
    rows = _normalize_rows(_load_price_history(market_data_provider), request)
    serializable_rows = _serializable_rows(rows)
    return _PreparedBacktest(
        data_boundary=data_boundary,
        rows=rows,
        dataset_hash=_json_hash(serializable_rows),
        input_hash=_json_hash(request.model_dump(mode="json")),
        rows_by_symbol=_rows_by_symbol(rows),
        trading_dates=sorted({row["date"] for row in rows}),
        signals_by_date=_signals_by_date(request.signals),
    )


def _initial_cost_basis(
    *,
    positions: dict[str, float],
    first_prices: dict[str, float],
) -> dict[str, float]:
    cost_basis: dict[str, float] = {}
    for symbol, quantity in positions.items():
        if symbol in first_prices:
            cost_basis[symbol] = quantity * first_prices[symbol]
    return cost_basis


def _run_backtest_loop(request: BacktestRequest, prepared: _PreparedBacktest) -> _BacktestLoopResult:
    cash = float(request.initial_cash)
    positions = {symbol: float(quantity) for symbol, quantity in request.initial_positions.items()}
    last_prices: dict[str, float] = {}
    first_prices = _close_prices_for_date(prepared.rows, prepared.trading_dates[0])
    cost_basis = _initial_cost_basis(positions=positions, first_prices=first_prices)
    pending_by_date: dict[date, list[_PendingExecution]] = {}
    trades: list[BacktestTrade] = []
    equity_curve: list[BacktestEquityPoint] = []
    previous_equity: float | None = None

    for session in prepared.trading_dates:
        last_prices.update(_close_prices_for_date(prepared.rows, session))
        cash = _execute_session_pending(
            session=session,
            request=request,
            cash=cash,
            positions=positions,
            cost_basis=cost_basis,
            pending_by_date=pending_by_date,
            trades=trades,
        )
        point = _record_equity_point(
            session=session,
            cash=cash,
            positions=positions,
            prices=last_prices,
            previous_equity=previous_equity,
        )
        equity_curve.append(point)
        previous_equity = point.equity
        _schedule_session_signals(
            session=session,
            request=request,
            prepared=prepared,
            cash=cash,
            positions=positions,
            last_prices=last_prices,
            pending_by_date=pending_by_date,
            trades=trades,
        )

    return _BacktestLoopResult(trades=trades, equity_curve=equity_curve)


def _execute_session_pending(
    *,
    session: date,
    request: BacktestRequest,
    cash: float,
    positions: dict[str, float],
    cost_basis: dict[str, float],
    pending_by_date: dict[date, list[_PendingExecution]],
    trades: list[BacktestTrade],
) -> float:
    for pending in pending_by_date.pop(session, []):
        cash, trade = _execute_pending(
            pending,
            cash=cash,
            positions=positions,
            cost_basis=cost_basis,
            fee_bps=request.assumptions.fee_bps,
            sell_tax_bps=request.assumptions.sell_tax_bps,
            min_trade_notional=request.assumptions.min_trade_notional,
        )
        trades.append(trade)
    return cash


def _schedule_session_signals(
    *,
    session: date,
    request: BacktestRequest,
    prepared: _PreparedBacktest,
    cash: float,
    positions: dict[str, float],
    last_prices: dict[str, float],
    pending_by_date: dict[date, list[_PendingExecution]],
    trades: list[BacktestTrade],
) -> None:
    for signal in prepared.signals_by_date.get(session, []):
        scheduled = _schedule_signal(
            signal=signal,
            symbol_rows=prepared.rows_by_symbol.get(signal.symbol, []),
            positions=positions,
            prices=last_prices,
            cash=cash,
            min_trade_notional=request.assumptions.min_trade_notional,
            slippage_bps=request.assumptions.slippage_bps,
        )
        if scheduled is None:
            continue
        if isinstance(scheduled, BacktestTrade):
            trades.append(scheduled)
        else:
            pending_by_date.setdefault(scheduled.fill_date, []).append(scheduled)


def _input_summary(
    *,
    request: BacktestRequest,
    data_boundary: str,
    market_data_provider: MarketDataProvider | list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "signal_count": len(request.signals),
        "initial_cash": request.initial_cash,
        "initial_position_count": len(request.initial_positions),
        "tested_variants": request.tested_variants,
        "data_boundary": data_boundary,
    }
    provenance = _data_provenance(market_data_provider)
    if provenance is not None:
        summary["data_provenance"] = provenance
    quality = _data_quality(market_data_provider)
    if quality is not None:
        summary["data_quality"] = quality
    return summary


def run_backtest(
    request: BacktestRequest,
    market_data_provider: MarketDataProvider | list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
) -> BacktestResult:
    prepared = _prepare_backtest(request, market_data_provider)
    loop_result = _run_backtest_loop(request, prepared)
    metrics = build_backtest_metrics(
        equity_curve=loop_result.equity_curve,
        trades=loop_result.trades,
        annualization_days=request.assumptions.annualization_days,
    )
    warnings = _build_warnings(
        request=request,
        trading_dates=prepared.trading_dates,
        filled_trades=metrics.filled_trades,
    )
    result_id = f"bt_{_json_hash({'dataset_hash': prepared.dataset_hash, 'input_hash': prepared.input_hash})[:24]}"
    return BacktestResult(
        result_id=result_id,
        strategy_id=request.strategy_id,
        recipe_version=request.recipe_version,
        dataset_hash=prepared.dataset_hash,
        input_hash=prepared.input_hash,
        input_summary=_input_summary(
            request=request,
            data_boundary=prepared.data_boundary,
            market_data_provider=market_data_provider,
        ),
        assumptions=request.assumptions,
        start_date=prepared.trading_dates[0],
        end_date=prepared.trading_dates[-1],
        equity_curve=loop_result.equity_curve,
        trades=loop_result.trades,
        metrics=metrics,
        warnings=warnings,
        research_only=True,
        live_trading_approval=False,
    )
