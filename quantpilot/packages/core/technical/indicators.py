from __future__ import annotations

from datetime import date, timedelta
from math import sqrt
from statistics import mean, pstdev
from typing import Any

from quantpilot.packages.core.schemas import TechnicalIndicatorSnapshot


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _moving_average(values: list[float], window: int) -> float:
    if not values:
        raise ValueError("moving average requires at least one value")
    selected = values[-window:]
    return mean(selected)


def _return(values: list[float], periods: int) -> float:
    if len(values) <= periods:
        if len(values) < 2:
            return 0.0
        return values[-1] / values[0] - 1
    return values[-1] / values[-periods - 1] - 1


def _rsi(values: list[float], lookback: int = 14) -> float:
    if len(values) < 2:
        return 50.0
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    selected = changes[-lookback:]
    gains = [change for change in selected if change > 0]
    losses = [-change for change in selected if change < 0]
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def _daily_returns(values: list[float]) -> list[float]:
    return [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]


def _score_from_ratio(ratio: float, *, midpoint: float = 1.0, slope: float = 100.0) -> float:
    return _clamp(50 + (ratio - midpoint) * slope)


def calculate_technical_indicators(
    rows: list[dict[str, Any]],
    *,
    ticker: str,
    signal_date: date,
) -> TechnicalIndicatorSnapshot:
    ticker_key = ticker.upper()
    usable_rows = [
        row
        for row in rows
        if str(row.get("ticker", row.get("symbol", ""))).upper() == ticker_key and _parse_date(row["date"]) <= signal_date
    ]
    usable_rows.sort(key=lambda row: _parse_date(row["date"]))
    if not usable_rows:
        raise ValueError(f"no price rows available for {ticker_key} on or before {signal_date.isoformat()}")

    closes = [float(row["close"]) for row in usable_rows]
    volumes = [float(row.get("volume", 0)) for row in usable_rows]
    close = closes[-1]
    ma5 = _moving_average(closes, 5)
    ma20 = _moving_average(closes, 20)
    returns = {
        "return_1d": round(_return(closes, 1), 6),
        "return_5d": round(_return(closes, 5), 6),
        "return_20d": round(_return(closes, 20), 6),
    }
    daily_returns = _daily_returns(closes[-21:])
    volatility = pstdev(daily_returns) * sqrt(252) if len(daily_returns) > 1 else 0.0
    rsi = _rsi(closes)
    prior_volumes = volumes[-21:-1]
    prior_volume_average = mean(prior_volumes) if prior_volumes else volumes[-1] or 1.0
    volume_ratio = volumes[-1] / prior_volume_average if prior_volume_average > 0 else 0.0

    trend_score = _score_from_ratio(close / ma20 if ma20 else 1.0, slope=250)
    momentum_score = _clamp(50 + returns["return_5d"] * 240 + returns["return_20d"] * 120)
    rsi_score = _clamp(100 - abs(rsi - 50) * 1.7)
    volume_score = _score_from_ratio(volume_ratio, slope=35)
    liquidity_score = _clamp((mean(volumes[-20:]) * close) / 10_000_000 * 100)
    defensive_score = _clamp(100 - volatility * 180)
    technical_score = _clamp(trend_score * 0.35 + momentum_score * 0.30 + rsi_score * 0.25 + volume_score * 0.10)

    return TechnicalIndicatorSnapshot(
        ticker=ticker_key,
        signal_date=signal_date,
        close=round(close, 6),
        moving_averages={"ma5": round(ma5, 6), "ma20": round(ma20, 6)},
        returns=returns,
        volatility=round(volatility, 6),
        rsi=round(rsi, 6),
        volume_ratio=round(volume_ratio, 6),
        momentum_score=round(momentum_score, 6),
        technical_score=round(technical_score, 6),
        liquidity_score=round(liquidity_score, 6),
        defensive_score=round(defensive_score, 6),
        data_points=len(usable_rows),
    )


def fixture_price_history(*, end_date: date = date(2026, 6, 10), days: int = 30) -> list[dict[str, Any]]:
    profiles = {
        "AAA": {"start": 84.0, "step": 0.78, "volume": 100_000},
        "BBB": {"start": 98.0, "step": 0.14, "volume": 90_000},
        "CCC": {"start": 100.0, "step": 0.04, "volume": 60_000},
        "DDD": {"start": 104.0, "step": 0.72, "volume": 130_000},
        "EEE": {"start": 108.0, "step": -0.62, "volume": 140_000},
        "FFF": {"start": 95.0, "step": 0.02, "volume": 40_000},
        "GGG": {"start": 50.0, "step": 0.0, "volume": 0},
    }
    rows: list[dict[str, Any]] = []
    for ticker, profile in profiles.items():
        for offset in range(days):
            row_date = end_date - timedelta(days=days - offset - 1)
            close = float(profile["start"]) + float(profile["step"]) * offset
            rows.append(
                {
                    "ticker": ticker,
                    "date": row_date.isoformat(),
                    "open": round(close * 0.995, 4),
                    "high": round(close * 1.015, 4),
                    "low": round(close * 0.985, 4),
                    "close": round(close, 4),
                    "volume": int(profile["volume"]),
                }
            )
    return rows
