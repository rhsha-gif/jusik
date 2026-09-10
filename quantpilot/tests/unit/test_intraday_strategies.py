from datetime import datetime, timedelta, timezone
import importlib.util
import math
from pathlib import Path
import sys

import pytest

MODULE_PATH = Path(__file__).parents[2] / "paper" / "strategy.py"
SPEC = importlib.util.spec_from_file_location("quantpilot_paper_strategy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
strategy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = strategy
SPEC.loader.exec_module(strategy)

Bar = strategy.Bar
Signal = strategy.Signal

UTC = timezone.utc
OPEN = datetime(2026, 9, 10, 9, 30, tzinfo=UTC)


def bar(
    minute: int, close: float = 100.0, volume: float = 100.0, symbol: str = "XYZ"
) -> Bar:
    return Bar(
        symbol=symbol,
        start=OPEN + timedelta(minutes=minute),
        open=close,
        high=close + 0.1,
        low=close - 0.1,
        close=close,
        volume=volume,
    )


def five_minute_series(closes: list[float], volume: float = 20.0) -> list[Bar]:
    bars: list[Bar] = []
    for group, close in enumerate(closes):
        for offset in range(5):
            bars.append(bar(group * 5 + offset, close=close, volume=volume))
    return bars


def evaluate(bars: list[Bar]) -> list[Signal]:
    return strategy.evaluate_strategies(
        bars, bars[-1].start + timedelta(minutes=1), OPEN
    )


def test_bar_validation_rejects_invalid_numeric_and_time_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Bar("XYZ", datetime(2026, 1, 1), 1, 1, 1, 1, 0)
    with pytest.raises(ValueError, match="finite and positive"):
        Bar("XYZ", OPEN, 1, math.nan, 1, 1, 0)
    with pytest.raises(ValueError, match="nonnegative"):
        Bar("XYZ", OPEN, 1, 1, 1, 1, -1)


def test_validate_bars_rejects_gaps_duplicates_mixed_symbols_and_incomplete_bars() -> (
    None
):
    now = OPEN + timedelta(minutes=4)
    with pytest.raises(ValueError, match="gap"):
        strategy.validate_bars([bar(0), bar(2)], now)
    with pytest.raises(ValueError, match="duplicate"):
        strategy.validate_bars([bar(0), bar(0)], now)
    with pytest.raises(ValueError, match="one symbol"):
        strategy.validate_bars([bar(0), bar(1, symbol="ABC")], now)
    with pytest.raises(ValueError, match="incomplete"):
        strategy.validate_bars(
            [bar(0), bar(1), bar(2), bar(3)], OPEN + timedelta(minutes=3, seconds=30)
        )


def test_five_minute_aggregation_uses_only_complete_aligned_groups() -> None:
    bars = [bar(minute, close=100 + minute / 10) for minute in range(1, 11)]
    aggregated = strategy.aggregate_five_minutes(bars)
    assert [item.start for item in aggregated] == [OPEN + timedelta(minutes=5)]
    assert aggregated[0].open == pytest.approx(100.5)
    assert aggregated[0].close == pytest.approx(100.9)


def test_opening_range_breakout_trigger_and_volume_confirmation() -> None:
    bars = [bar(minute, close=99.8, volume=100) for minute in range(15)]
    bars.append(bar(15, close=100.4, volume=200))
    signals = evaluate(bars)
    breakout = next(
        item for item in signals if item.strategy_id == "opening_range_breakout"
    )
    assert breakout.stop < breakout.price < breakout.target
    assert breakout.reason.startswith("15m opening-range")

    quiet = bars[:-1] + [bar(15, close=100.4, volume=149)]
    assert not any(
        item.strategy_id == "opening_range_breakout" for item in evaluate(quiet)
    )


def test_trend_pullback_trigger_on_completed_five_minute_recovery() -> None:
    closes = [100.0 + index for index in range(20)] + [110.5, 115.0]
    signals = evaluate(five_minute_series(closes))
    pullback = next(item for item in signals if item.strategy_id == "trend_pullback")
    assert pullback.stop < pullback.price < pullback.target


def test_range_reversion_trigger_on_rsi_recovery() -> None:
    closes = [100.8 - index * 0.05 for index in range(20)] + [99.4, 100.2]
    signals = evaluate(five_minute_series(closes))
    reversion = next(item for item in signals if item.strategy_id == "range_reversion")
    assert reversion.stop < reversion.price < reversion.target


def test_insufficient_warmup_and_lookahead_are_not_used() -> None:
    short = [bar(minute) for minute in range(10)]
    assert evaluate(short) == []

    completed_now = short[-1].start + timedelta(minutes=1)
    with pytest.raises(ValueError, match="incomplete"):
        strategy.evaluate_strategies(short + [bar(10)], completed_now, OPEN)


def test_allocate_weights_caps_concentration_and_keeps_cash() -> None:
    weights = strategy.allocate_weights(
        {"opening_range_breakout": 0.9, "trend_pullback": 0.1},
        {
            "opening_range_breakout": {
                "trades": 100,
                "mean_r": 1.0,
                "max_drawdown": 0.1,
            },
            "trend_pullback": {"trades": 2, "mean_r": -1.0, "max_drawdown": 0.5},
        },
    )
    assert weights["opening_range_breakout"] == 0.6
    assert all(weight <= 0.6 for weight in weights.values())
    assert sum(weights.values()) < 1.0


def test_allocate_weights_requires_market_evidence_and_ignores_unknown_ai_keys() -> (
    None
):
    assert strategy.allocate_weights({}, {}, {"unknown": 1.0}) == {}
    assert strategy.allocate_weights({"a": 0.0}, {}, {"a": 1.0}) == {}
    assert strategy.allocate_weights({"a": math.nan}, {}) == {}
    assert "unknown" not in strategy.allocate_weights({"a": 0.5}, {}, {"unknown": 1.0})


def test_ai_is_limited_to_twenty_percent_of_combined_score() -> None:
    weights = strategy.allocate_weights(
        {"a": 0.5, "b": 0.5}, {}, {"a": 1.0, "b": 0.0}, cap=1.0
    )
    assert weights == pytest.approx({"a": 0.6 / 1.0, "b": 0.4 / 1.0})


def test_select_signals_chooses_highest_score_per_unoccupied_symbol_deterministically() -> (
    None
):
    low = Signal("opening_range_breakout", "XYZ", 100, 99, 102, 0.6, "low")
    high = Signal("trend_pullback", "XYZ", 100, 99, 102, 0.9, "high")
    other = Signal("range_reversion", "ABC", 50, 49, 52, 0.7, "other")
    weights = {
        "opening_range_breakout": 0.6,
        "trend_pullback": 0.1,
        "range_reversion": 0.3,
    }
    assert strategy.select_signals([low, high, other], weights, set()) == [other, high]
    assert strategy.select_signals([low, high, other], weights, {"XYZ"}) == [other]


def test_signal_rejects_nan_and_invalid_risk_geometry() -> None:
    with pytest.raises(ValueError, match="finite"):
        Signal("x", "XYZ", 100, 99, 102, math.nan, "bad")
    with pytest.raises(ValueError, match="stop"):
        Signal("x", "XYZ", 100, 100, 102, 0.5, "bad")
