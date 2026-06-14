from __future__ import annotations

from quantpilot.packages.core.marketdata import FakeOHLCVProvider, FakeQuoteProvider
from quantpilot.packages.core.schemas import SignalAction, UserPolicy
from quantpilot.packages.core.signals.service import generate_provider_bound_signals, load_fixture_ohlcv
from quantpilot.packages.core.strategies.loader import load_default_strategy


def test_provider_bound_signals_include_serializable_calibrated_signal_set() -> None:
    bars = load_fixture_ohlcv()
    signal_set = generate_provider_bound_signals(
        load_default_strategy(),
        FakeOHLCVProvider(bars),
        quote_provider=FakeQuoteProvider.from_bars(bars),
        policy=UserPolicy(),
        horizon="weekly",
    )

    assert signal_set.calibrated_signal_set is not None
    assert len(signal_set.calibrated_signal_set.signals) == len(signal_set.signals)
    assert any(signal.action == SignalAction.buy_ready for signal in signal_set.signals)

    calibrated_by_symbol = {signal.symbol: signal for signal in signal_set.calibrated_signal_set.signals}
    aaa = calibrated_by_symbol["AAA"]
    assert aaa.base_action == SignalAction.buy_ready
    assert aaa.calibrated_action in {SignalAction.buy_ready, SignalAction.buy_wait}
    assert aaa.multi_factor_score.regime in {"uptrend", "pullback", "range", "volatile", "downtrend", "risk_off"}
    assert 0 <= aaa.multi_factor_score.final_score <= 100
    assert 0 <= aaa.confidence <= 1

    dumped = signal_set.model_dump(mode="json")
    assert dumped["calibrated_signal_set"]["order_submission_enabled"] is False
    assert dumped["calibrated_signal_set"]["signals"][0]["expected_return_risk"]["calibrated"] is True


def test_multifactor_components_are_deterministic_for_fixture_bars() -> None:
    bars = load_fixture_ohlcv()
    first = generate_provider_bound_signals(
        load_default_strategy(),
        FakeOHLCVProvider(bars),
        quote_provider=FakeQuoteProvider.from_bars(bars),
        policy=UserPolicy(),
    ).calibrated_signal_set
    second = generate_provider_bound_signals(
        load_default_strategy(),
        FakeOHLCVProvider(bars),
        quote_provider=FakeQuoteProvider.from_bars(bars),
        policy=UserPolicy(),
    ).calibrated_signal_set

    assert first is not None
    assert second is not None
    first_scores = [(signal.symbol, signal.multi_factor_score.model_dump()) for signal in first.signals]
    second_scores = [(signal.symbol, signal.multi_factor_score.model_dump()) for signal in second.signals]
    assert first_scores == second_scores
