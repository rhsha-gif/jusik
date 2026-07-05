from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from quantpilot.packages.core.backtest.replay import replay_signals
from quantpilot.packages.core.schemas import SignalAction


def _bar(symbol: str, session: date, close: float, *, volume: int = 10_000) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": session.isoformat(),
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": volume,
    }


def _series(symbol: str, closes: list[float], *, start: date = date(2026, 1, 1)) -> list[dict[str, Any]]:
    return [
        _bar(symbol, start + timedelta(days=offset), close)
        for offset, close in enumerate(closes)
    ]


def test_replay_emits_nothing_before_warmup() -> None:
    history = _series("AAA", [100.0 + i for i in range(19)])

    assert replay_signals(history, warmup_bars=20) == []


def test_replay_has_no_lookahead() -> None:
    # 30 flat sessions, then an ma20 break that emits a real exit signal.
    closes = [100.0] * 30 + [90.0, 91.0, 92.0]
    history = _series("AAA", closes)
    cutoff = date.fromisoformat(str(history[-1]["date"]))

    baseline = replay_signals(history, warmup_bars=20, initial_positions={"AAA": 0.1})
    assert any(signal.action == SignalAction.exit for signal in baseline)

    # Append wild future bars: crash then melt-up. Signals on or before the
    # cutoff date must be byte-identical.
    future_start = cutoff + timedelta(days=1)
    future = _series("AAA", [10.0, 5.0, 500.0, 1000.0], start=future_start)
    extended = replay_signals(
        history + future, warmup_bars=20, initial_positions={"AAA": 0.1}
    )

    extended_until_cutoff = [signal for signal in extended if signal.signal_date <= cutoff]
    assert [signal.model_dump() for signal in extended_until_cutoff] == [
        signal.model_dump() for signal in baseline
    ]


def test_replay_emits_exit_for_initial_position_on_ma_break() -> None:
    # Flat at 100 for 25 sessions, then a collapse to 90 (< ma20 * 0.94).
    history = _series("AAA", [100.0] * 25 + [90.0])

    signals = replay_signals(history, warmup_bars=20, initial_positions={"AAA": 0.1})

    exits = [signal for signal in signals if signal.action == SignalAction.exit]
    assert len(exits) == 1
    assert exits[0].symbol == "AAA"
    assert exits[0].signal_date == date(2026, 1, 26)
    # Once exited, the same break must not re-emit an exit for a flat book.
    assert all(signal.signal_date <= date(2026, 1, 26) for signal in exits)


def test_replay_tracks_assumed_position_state_through_trim() -> None:
    # Overheat rule: held position with close >= ma20 * 1.2 triggers trim, and
    # the assumed weight halves so repeated overheat keeps trimming until the
    # remainder is negligible.
    history = _series("AAA", [100.0] * 25 + [130.0, 131.0])

    signals = replay_signals(history, warmup_bars=20, initial_positions={"AAA": 0.1})

    trims = [signal for signal in signals if signal.action == SignalAction.trim]
    assert [signal.signal_date for signal in trims] == [date(2026, 1, 26), date(2026, 1, 27)]


def test_replay_limit_buffer_widens_limits_directionally() -> None:
    # Same crafted series as the exit test: one exit signal at 90.0.
    history = _series("AAA", [100.0] * 25 + [90.0])

    tight = replay_signals(history, warmup_bars=20, initial_positions={"AAA": 0.1})
    buffered = replay_signals(
        history,
        warmup_bars=20,
        initial_positions={"AAA": 0.1},
        limit_buffer_bps=100.0,
    )

    assert tight[0].limit_price is None  # engine defaults to signal-day close
    assert buffered[0].action == SignalAction.exit
    assert buffered[0].limit_price == round(90.0 * 0.99, 6)  # sells widen down


def test_replay_rejects_negative_limit_buffer() -> None:
    with pytest.raises(ValueError, match="limit_buffer_bps"):
        replay_signals(_series("AAA", [100.0] * 25), limit_buffer_bps=-1.0)


def test_replay_rejects_rows_without_symbol() -> None:
    with pytest.raises(ValueError, match="missing symbol"):
        replay_signals([{"date": "2026-01-01", "close": 1.0}])
