"""Deterministic historical signal replay for research backtests.

Walks provider price history one session at a time and re-runs the same
deterministic Level 1-2 snapshot classifier (``classify_fixture_bar``) the
harness uses, producing ``BacktestSignal`` inputs for ``run_backtest``.

No lookahead: indicators for a signal date are computed by
``calculate_technical_indicators``, which only consumes bars dated on or
before that date — appending future rows can never change an earlier signal
(enforced by a regression test).

Research-only: this module emits backtest inputs. It never touches brokers,
order plans, repositories, or strategy promotion state.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from quantpilot.packages.core.backtest.schemas import BacktestSignal
from quantpilot.packages.core.schemas import SignalAction
from quantpilot.packages.core.signals.service import classify_fixture_bar
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators

_ACTIONABLE_ACTIONS = {SignalAction.buy_ready, SignalAction.trim, SignalAction.exit}


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def replay_signals(
    price_history: list[dict[str, Any]],
    *,
    warmup_bars: int = 20,
    max_position_weight: float = 0.15,
    initial_positions: dict[str, float] | None = None,
    limit_buffer_bps: float = 0.0,
) -> list[BacktestSignal]:
    """Replay the deterministic snapshot classifier over historical bars.

    ``initial_positions`` maps symbol -> assumed starting weight so exit/trim
    rules apply to pre-existing holdings. During the replay an assumed
    position weight is tracked per symbol (set on ``buy_ready``, halved on
    ``trim``, cleared on ``exit``) because the classifier's exit/trim rules
    only arm while a position is held; the backtest engine independently
    simulates the actual fills and cash.

    Only actionable signals (``buy_ready``/``trim``/``exit``) are returned;
    ``hold``/``watch`` classifications are no-ops for the engine.

    ``limit_buffer_bps`` widens each signal's limit price away from the
    signal-day close (buys up, sells down) so fill sensitivity of the
    ``next_open_limit_touch`` model can be studied. ``0`` keeps the engine
    default (limit == signal-day close), which systematically misses fills
    when momentum gaps the next bar away from the close.
    """
    if warmup_bars < 1:
        raise ValueError("warmup_bars must be at least 1")
    if limit_buffer_bps < 0:
        raise ValueError("limit_buffer_bps must be non-negative")

    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in price_history:
        symbol = str(row.get("symbol", row.get("ticker", ""))).strip().upper()
        if not symbol:
            raise ValueError("price history row is missing symbol/ticker")
        rows_by_symbol.setdefault(symbol, []).append(dict(row, symbol=symbol))
    for rows in rows_by_symbol.values():
        rows.sort(key=lambda item: _parse_date(item["date"]))

    held: dict[str, float] = {
        symbol.strip().upper(): float(weight)
        for symbol, weight in (initial_positions or {}).items()
        if float(weight) > 0
    }

    trading_dates = sorted(
        {_parse_date(row["date"]) for rows in rows_by_symbol.values() for row in rows}
    )
    signals: list[BacktestSignal] = []
    for session in trading_dates:
        for symbol in sorted(rows_by_symbol):
            rows = rows_by_symbol[symbol]
            bars_through_session = [row for row in rows if _parse_date(row["date"]) <= session]
            if len(bars_through_session) < warmup_bars:
                continue
            if _parse_date(bars_through_session[-1]["date"]) != session:
                continue  # symbol did not trade this session
            indicator = calculate_technical_indicators(
                bars_through_session, ticker=symbol, signal_date=session
            )
            snapshot_bar = {
                "symbol": symbol,
                "close": indicator.close,
                "ma20": indicator.moving_averages["ma20"],
                "rsi": indicator.rsi,
                "volume_ratio": indicator.volume_ratio,
                "position_weight": held.get(symbol, 0.0),
                "blocked": False,
            }
            action, strength, reason = classify_fixture_bar(snapshot_bar)

            if action == SignalAction.buy_ready:
                held[symbol] = min(max_position_weight, max(0.01, strength * max_position_weight))
            elif action == SignalAction.trim:
                remaining = held.get(symbol, 0.0) * 0.5
                if remaining > 0.005:
                    held[symbol] = remaining
                else:
                    held.pop(symbol, None)
            elif action == SignalAction.exit:
                held.pop(symbol, None)

            if action in _ACTIONABLE_ACTIONS:
                limit_price: float | None = None
                if limit_buffer_bps > 0:
                    buffer = limit_buffer_bps / 10_000.0
                    if action == SignalAction.buy_ready:
                        limit_price = round(indicator.close * (1 + buffer), 6)
                    else:  # trim/exit accept a worse price to secure the fill
                        limit_price = round(indicator.close * (1 - buffer), 6)
                signals.append(
                    BacktestSignal(
                        symbol=symbol,
                        signal_date=session,
                        action=action,
                        strength=strength,
                        limit_price=limit_price,
                        reason=reason,
                        source="deterministic_signal_replay",
                    )
                )
    return signals
