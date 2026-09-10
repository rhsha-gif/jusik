"""Pure, deterministic intraday strategies for offline paper-trading research.

All thresholds are deliberately frozen here so a backtest cannot acquire new
behaviour through configuration drift.  Inputs are completed one-minute bars;
the module never fills gaps or manufactures bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Mapping

# Frozen strategy thresholds.
OPENING_RANGE_MINUTES = 15
OPENING_VOLUME_MULTIPLIER = 1.5
OPENING_BREAKOUT_MAX_MINUTES = 90
EMA_PERIOD = 20
ATR_PERIOD = 14
TREND_PULLBACK_TOLERANCE = 0.003
RANGE_WINDOW = 20
RANGE_MAX_WIDTH = 0.03
RANGE_LOWER_ZONE = 0.20
RSI_PERIOD = 14
RSI_OVERSOLD = 35.0
STOP_ATR_MULTIPLIER = 1.0
TARGET_R_MULTIPLE = 2.0
AI_SCORE_SHARE = 0.20
PERFORMANCE_MAX_ADJUSTMENT = 0.10
PERFORMANCE_SHRINK_TRADES = 30.0


def _aware(value: datetime) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


@dataclass(frozen=True)
class Bar:
    symbol: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("bar symbol must be a non-empty string")
        if not _aware(self.start):
            raise ValueError("bar start must be timezone-aware")
        prices = (self.open, self.high, self.low, self.close)
        if not all(_finite_number(value) and float(value) > 0.0 for value in prices):
            raise ValueError("OHLC values must be finite and positive")
        if self.high < max(self.open, self.low, self.close) or self.low > min(
            self.open, self.high, self.close
        ):
            raise ValueError("bar high/low do not contain open and close")
        if not _finite_number(self.volume) or float(self.volume) < 0.0:
            raise ValueError("bar volume must be finite and nonnegative")


@dataclass(frozen=True)
class Signal:
    strategy_id: str
    symbol: str
    price: float
    stop: float
    target: float
    score: float
    reason: str
    version: str = "1"
    entry_atr14: float | None = None

    def __post_init__(self) -> None:
        text_fields = (self.strategy_id, self.symbol, self.reason, self.version)
        if self.entry_atr14 is not None and (
            not _finite_number(self.entry_atr14) or self.entry_atr14 <= 0
        ):
            raise ValueError("entry ATR must be finite and positive")
        if not all(isinstance(value, str) and value.strip() for value in text_fields):
            raise ValueError("signal text fields must be non-empty")
        if not all(
            _finite_number(value)
            for value in (self.price, self.stop, self.target, self.score)
        ):
            raise ValueError("signal numeric fields must be finite")
        if not (0.0 < self.stop < self.price < self.target):
            raise ValueError("signal must satisfy 0 < stop < price < target")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("signal score must be between zero and one")


def _require_aware(value: datetime, name: str) -> None:
    if not _aware(value):
        raise ValueError(f"{name} must be timezone-aware")


def validate_bars(bars: list[Bar], now: datetime) -> list[Bar]:
    """Return chronologically sorted, complete 1m bars or reject the batch.

    A date change is treated as a session boundary.  Within one local trading
    date, timestamps must be exactly one minute apart.
    """

    _require_aware(now, "now")
    if not isinstance(bars, list):
        raise TypeError("bars must be a list")
    if not bars:
        return []
    if not all(isinstance(bar, Bar) for bar in bars):
        raise TypeError("bars must contain Bar instances")

    symbols = {bar.symbol for bar in bars}
    if len(symbols) != 1:
        raise ValueError("bars must contain exactly one symbol")

    ordered = sorted(bars, key=lambda bar: bar.start)
    seen: set[datetime] = set()
    for bar in ordered:
        if bar.start.second or bar.start.microsecond:
            raise ValueError("one-minute bars must start on a minute boundary")
        if bar.start in seen:
            raise ValueError("duplicate bar timestamp")
        seen.add(bar.start)
        if bar.start + timedelta(minutes=1) > now:
            raise ValueError("future or incomplete one-minute bar")

    for previous, current in zip(ordered, ordered[1:]):
        current_on_previous_clock = current.start.astimezone(previous.start.tzinfo)
        same_session = previous.start.date() == current_on_previous_clock.date()
        if same_session and current.start - previous.start != timedelta(minutes=1):
            raise ValueError("gap within trading session")
    return ordered


def aggregate_five_minutes(bars: list[Bar]) -> list[Bar]:
    """Aggregate only aligned groups containing all five constituent 1m bars."""

    if not isinstance(bars, list):
        raise TypeError("bars must be a list")
    if not bars:
        return []
    if not all(isinstance(bar, Bar) for bar in bars):
        raise TypeError("bars must contain Bar instances")
    if len({bar.symbol for bar in bars}) != 1:
        raise ValueError("bars must contain exactly one symbol")

    ordered = sorted(bars, key=lambda bar: bar.start)
    if len({bar.start for bar in ordered}) != len(ordered):
        raise ValueError("duplicate bar timestamp")
    for bar in ordered:
        if bar.start.second or bar.start.microsecond:
            raise ValueError("one-minute bars must start on a minute boundary")

    buckets: dict[datetime, list[Bar]] = {}
    for bar in ordered:
        bucket = bar.start.replace(minute=bar.start.minute - bar.start.minute % 5)
        buckets.setdefault(bucket, []).append(bar)

    result: list[Bar] = []
    for bucket, group in sorted(buckets.items()):
        expected = [bucket + timedelta(minutes=index) for index in range(5)]
        if len(group) != 5 or [bar.start for bar in group] != expected:
            continue
        if any(bar.start.date() != bucket.date() for bar in group):
            continue
        result.append(
            Bar(
                symbol=group[0].symbol,
                start=bucket,
                open=group[0].open,
                high=max(bar.high for bar in group),
                low=min(bar.low for bar in group),
                close=group[-1].close,
                volume=sum(bar.volume for bar in group),
            )
        )
    return result


def _ema(values: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1.0)
    output = [float(values[0])]
    for value in values[1:]:
        output.append(alpha * float(value) + (1.0 - alpha) * output[-1])
    return output


def _true_ranges(bars: list[Bar]) -> list[float]:
    ranges: list[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            ranges.append(bar.high - bar.low)
        else:
            previous_close = bars[index - 1].close
            ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            )
    return ranges


def _atr(bars: list[Bar], period: int = ATR_PERIOD) -> float | None:
    if len(bars) < period + 1:
        return None
    ranges = _true_ranges(bars)
    value = sum(ranges[1 : period + 1]) / period
    for item in ranges[period + 1 :]:
        value = ((period - 1) * value + item) / period
    return value if value > 0.0 and math.isfinite(value) else None


def _rsi(values: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return output
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period

    def value() -> float:
        if average_loss == 0.0:
            return 100.0 if average_gain > 0.0 else 50.0
        return 100.0 - 100.0 / (1.0 + average_gain / average_loss)

    output[period] = value()
    for index, change in enumerate(changes[period:], start=period + 1):
        average_gain = ((period - 1) * average_gain + max(change, 0.0)) / period
        average_loss = ((period - 1) * average_loss + max(-change, 0.0)) / period
        output[index] = value()
    return output


def _risk_levels(price: float, atr: float) -> tuple[float, float]:
    risk = min(price * 0.95, max(atr * STOP_ATR_MULTIPLIER, price * 0.001))
    stop = price - risk
    target = price + TARGET_R_MULTIPLE * risk
    return stop, target


def _opening_range_signal(bars: list[Bar], session_open: datetime) -> Signal | None:
    expected = [
        session_open + timedelta(minutes=index)
        for index in range(OPENING_RANGE_MINUTES)
    ]
    by_time = {bar.start: bar for bar in bars}
    opening = [by_time.get(timestamp) for timestamp in expected]
    if any(bar is None for bar in opening):
        return None
    completed_opening = [bar for bar in opening if bar is not None]
    current = bars[-1]
    previous = bars[-2] if len(bars) >= 2 else None
    if previous is None or current.start < session_open + timedelta(
        minutes=OPENING_RANGE_MINUTES
    ):
        return None
    if current.start >= session_open + timedelta(minutes=OPENING_BREAKOUT_MAX_MINUTES):
        return None
    range_high = max(bar.high for bar in completed_opening)
    average_volume = sum(bar.volume for bar in completed_opening) / len(
        completed_opening
    )
    if not (previous.close <= range_high < current.close):
        return None
    if (
        average_volume <= 0.0
        or current.volume < average_volume * OPENING_VOLUME_MULTIPLIER
    ):
        return None
    opening_low = min(bar.low for bar in completed_opening)
    atr_value = _atr(bars) or max(range_high - opening_low, current.close * 0.001)
    stop, target = _risk_levels(current.close, atr_value)
    volume_ratio = current.volume / average_volume
    score = min(1.0, 0.60 + min(volume_ratio - OPENING_VOLUME_MULTIPLIER, 1.0) * 0.20)
    return Signal(
        strategy_id="opening_range_breakout",
        symbol=current.symbol,
        price=current.close,
        stop=stop,
        target=target,
        score=score,
        reason="15m opening-range close crossover with confirmed volume",
        entry_atr14=atr_value,
    )


def _trend_pullback_signal(five: list[Bar]) -> Signal | None:
    if len(five) < EMA_PERIOD + 2:
        return None
    closes = [bar.close for bar in five]
    averages = _ema(closes, EMA_PERIOD)
    before, pullback, current = five[-3:]
    before_ema, pullback_ema, current_ema = averages[-3:]
    if not (
        before.close > before_ema
        and pullback.low <= pullback_ema * (1.0 + TREND_PULLBACK_TOLERANCE)
        and pullback.close <= pullback_ema
        and current.close > current_ema
        and current.close > pullback.close
        and current_ema > before_ema
    ):
        return None
    atr = _atr(five)
    if atr is None:
        return None
    stop, target = _risk_levels(current.close, atr)
    recovery = max(0.0, (current.close - current_ema) / current.close)
    return Signal(
        strategy_id="trend_pullback",
        symbol=current.symbol,
        price=current.close,
        stop=stop,
        target=target,
        score=min(1.0, 0.60 + recovery * 10.0),
        reason="rising EMA20 pullback followed by a completed 5m recovery",
        entry_atr14=atr,
    )


def _range_reversion_signal(five: list[Bar]) -> Signal | None:
    if len(five) < RANGE_WINDOW + 2:
        return None
    closes = [bar.close for bar in five]
    relative_strength = _rsi(closes)
    prior_rsi, current_rsi = relative_strength[-2:]
    if prior_rsi is None or current_rsi is None:
        return None
    reference = five[-(RANGE_WINDOW + 1) : -1]
    band_low = min(bar.low for bar in reference)
    band_high = max(bar.high for bar in reference)
    midpoint = (band_high + band_low) / 2.0
    width = (band_high - band_low) / midpoint if midpoint > 0.0 else math.inf
    lower_limit = band_low + (band_high - band_low) * RANGE_LOWER_ZONE
    previous, current = five[-2:]
    if not (
        width <= RANGE_MAX_WIDTH
        and previous.close <= lower_limit
        and prior_rsi <= RSI_OVERSOLD < current_rsi
        and current.close > previous.close
        and current.close <= band_high * (1.0 + TREND_PULLBACK_TOLERANCE)
    ):
        return None
    atr = _atr(five)
    if atr is None:
        return None
    stop, target = _risk_levels(current.close, atr)
    return Signal(
        strategy_id="range_reversion",
        symbol=current.symbol,
        price=current.close,
        stop=stop,
        target=target,
        score=min(1.0, 0.60 + (current_rsi - RSI_OVERSOLD) / 100.0),
        reason="sideways 5m lower-band test with RSI recovery",
        entry_atr14=atr,
    )


def evaluate_strategies(
    bars: list[Bar], now: datetime, session_open: datetime
) -> list[Signal]:
    """Evaluate the latest completed bar only and return long signals."""

    _require_aware(session_open, "session_open")
    if session_open.second or session_open.microsecond:
        raise ValueError("session_open must be on a minute boundary")
    ordered = validate_bars(bars, now)
    if not ordered:
        return []
    current_session = [
        bar
        for bar in ordered
        if bar.start >= session_open
        and bar.start.date() == session_open.astimezone(bar.start.tzinfo).date()
    ]
    if not current_session:
        return []

    signals: list[Signal] = []
    opening_signal = _opening_range_signal(current_session, session_open)
    if opening_signal is not None:
        signals.append(opening_signal)
    five = aggregate_five_minutes(current_session)
    if five and five[-1].start + timedelta(minutes=5) == current_session[
        -1
    ].start + timedelta(minutes=1):
        for evaluator in (_trend_pullback_signal, _range_reversion_signal):
            signal = evaluator(five)
            if signal is not None:
                signals.append(signal)
    return signals


def _valid_unit_score(value: object) -> float | None:
    if not _finite_number(value):
        return None
    numeric = float(value)
    return numeric if 0.0 <= numeric <= 1.0 else None


def _performance_factor(metrics: object) -> float:
    if not isinstance(metrics, Mapping):
        return 1.0
    trades = metrics.get("trades")
    mean_r = metrics.get("mean_r")
    drawdown = metrics.get("max_drawdown")
    if not all(_finite_number(value) for value in (trades, mean_r, drawdown)):
        return 1.0
    trades_value = float(trades)
    drawdown_value = float(drawdown)
    if trades_value < 0.0 or drawdown_value < 0.0:
        return 1.0
    reliability = trades_value / (trades_value + PERFORMANCE_SHRINK_TRADES)
    quality = math.tanh(float(mean_r)) - min(drawdown_value, 1.0)
    adjustment = PERFORMANCE_MAX_ADJUSTMENT * reliability * max(-1.0, min(1.0, quality))
    return 1.0 + adjustment


def allocate_weights(
    market_scores: dict[str, float],
    performance: dict[str, dict],
    ai_scores: dict[str, float] | None = None,
    cap: float = 0.6,
) -> dict[str, float]:
    """Allocate among strategies with positive market evidence, retaining cash.

    AI contributes no more than 20% of a candidate's combined score.  Historical
    performance can adjust that score by at most 10%, with ``trades/(trades+30)``
    shrinkage.  Capped excess is intentionally not redistributed.
    """

    if not isinstance(market_scores, dict) or not isinstance(performance, dict):
        raise TypeError("market_scores and performance must be dictionaries")
    if ai_scores is not None and not isinstance(ai_scores, dict):
        raise TypeError("ai_scores must be a dictionary or None")
    if not _finite_number(cap) or not 0.0 < float(cap) <= 1.0:
        raise ValueError("cap must be finite and in (0, 1]")

    effective: dict[str, float] = {}
    for strategy_id, raw_market in market_scores.items():
        if not isinstance(strategy_id, str) or not strategy_id:
            continue
        market = _valid_unit_score(raw_market)
        if market is None or market <= 0.0:
            continue
        combined = market
        if ai_scores is not None and strategy_id in ai_scores:
            ai = _valid_unit_score(ai_scores[strategy_id])
            if ai is not None:
                combined = (1.0 - AI_SCORE_SHARE) * market + AI_SCORE_SHARE * ai
        combined *= _performance_factor(performance.get(strategy_id))
        if combined > 0.0 and math.isfinite(combined):
            effective[strategy_id] = combined

    total = sum(effective.values())
    if total <= 0.0:
        return {}
    cap_value = float(cap)
    return {
        strategy_id: min(score / total, cap_value)
        for strategy_id, score in sorted(effective.items())
    }


def select_signals(
    signals: list[Signal], weights: dict[str, float], occupied_symbols: set[str]
) -> list[Signal]:
    """Select the highest-score funded signal for each unoccupied symbol."""

    if (
        not isinstance(signals, list)
        or not isinstance(weights, dict)
        or not isinstance(occupied_symbols, set)
    ):
        raise TypeError(
            "signals, weights, and occupied_symbols have invalid container types"
        )
    winners: dict[str, Signal] = {}
    for signal in signals:
        if not isinstance(signal, Signal):
            raise TypeError("signals must contain Signal instances")
        weight = weights.get(signal.strategy_id)
        if (
            signal.symbol in occupied_symbols
            or not _finite_number(weight)
            or float(weight) <= 0.0
        ):
            continue
        current = winners.get(signal.symbol)
        if current is None or (
            -signal.score,
            signal.strategy_id,
            signal.version,
            signal.reason,
        ) < (
            -current.score,
            current.strategy_id,
            current.version,
            current.reason,
        ):
            winners[signal.symbol] = signal
    return [winners[symbol] for symbol in sorted(winners)]
