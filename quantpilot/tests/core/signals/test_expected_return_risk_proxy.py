from __future__ import annotations

from datetime import date, timedelta

from quantpilot.packages.core.marketdata.types import MarketDataQuality
from quantpilot.packages.core.schemas import DataMode, Signal, SignalAction
from quantpilot.packages.core.signals.calibration import (
    build_expected_return_risk_proxy,
    calculate_signal_decay,
)
from quantpilot.packages.core.signals.types import MultiFactorScore


def _signal(action: SignalAction = SignalAction.buy_ready) -> Signal:
    signal_date = date(2026, 6, 10)
    return Signal(
        strategy_id="fixture",
        recipe_version="v1",
        symbol="AAA",
        signal_date=signal_date,
        action=action,
        strength=0.8,
        technical_score=75.0,
        quant_score=72.0,
        target_weight_hint=0.10,
        valid_until=signal_date + timedelta(days=5),
        reason="fixture",
    )


def _score() -> MultiFactorScore:
    return MultiFactorScore(
        symbol="AAA",
        momentum=72.0,
        trend=75.0,
        volume=70.0,
        volatility=80.0,
        data_quality=100.0,
        final_score=74.0,
        regime="uptrend",
        weights={"momentum": 0.24, "trend": 0.30, "volume": 0.18, "volatility": 0.16, "data_quality": 0.12},
        reason_codes=["regime_uptrend"],
    )


def test_signal_decay_reaches_zero_after_valid_until() -> None:
    signal = _signal()

    assert calculate_signal_decay(signal, as_of=signal.signal_date) == 1.0
    assert calculate_signal_decay(signal, as_of=signal.valid_until + timedelta(days=1)) == 0.0  # type: ignore[operator]


def test_horizon_expected_return_proxy_scales_without_live_learning() -> None:
    signal = _signal()
    score = _score()
    daily = build_expected_return_risk_proxy(
        signal=signal,
        action=SignalAction.buy_ready,
        score=score,
        confidence=0.70,
        horizon="daily",
        data_mode=DataMode.fixture,
    )
    monthly = build_expected_return_risk_proxy(
        signal=signal,
        action=SignalAction.buy_ready,
        score=score,
        confidence=0.70,
        horizon="monthly",
        data_mode=DataMode.fixture,
    )

    assert daily.calibrated is True
    assert monthly.expected_return > daily.expected_return
    assert daily.risk == monthly.risk
    assert daily.source == "calibrated_multifactor_signal_model"


def test_unusable_market_data_quality_is_explicit_fixture_mode() -> None:
    quality = MarketDataQuality(usable=False, degraded=True, reason_codes=["provider_unavailable"], data_mode=DataMode.fixture)

    assert quality.data_mode == DataMode.fixture
