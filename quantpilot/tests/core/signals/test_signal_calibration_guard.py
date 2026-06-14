from __future__ import annotations

from quantpilot.packages.core.marketdata import FakeOHLCVProvider, FakeQuoteProvider
from quantpilot.packages.core.schemas import SignalAction, UserPolicy
from quantpilot.packages.core.signals.service import generate_provider_bound_signals, load_fixture_ohlcv
from quantpilot.packages.core.strategies.loader import load_default_strategy


def _security(symbol: str) -> dict[str, object]:
    return {
        "ticker": symbol,
        "name": symbol,
        "market": "KR_STOCK",
        "sector": "technology",
        "themes": ["ai"],
        "avg_daily_value": 10_000_000,
        "data_ready": True,
    }


def test_provider_unavailable_calibration_guard_blocks_buy_ready() -> None:
    signal_set = generate_provider_bound_signals(
        load_default_strategy(),
        FakeOHLCVProvider.unavailable(reason="fixture outage"),
        policy=UserPolicy(),
        securities=[_security("AAA")],
    )

    assert signal_set.calibrated_signal_set is not None
    assert {signal.calibrated_action for signal in signal_set.calibrated_signal_set.signals} == {SignalAction.blocked}
    guarded = signal_set.calibrated_signal_set.signals[0]
    assert guarded.guard.action_allowed is False
    assert "market_data_unusable" in guarded.guard.reason_codes
    assert guarded.expected_return_risk.expected_return == 0.0


def test_stale_provider_calibration_guard_returns_no_buy_ready() -> None:
    bars = load_fixture_ohlcv()
    signal_set = generate_provider_bound_signals(
        load_default_strategy(),
        FakeOHLCVProvider.stale(bars, reason="stale fixture"),
        quote_provider=FakeQuoteProvider.from_bars(bars),
        policy=UserPolicy(),
    )

    assert signal_set.calibrated_signal_set is not None
    assert all(signal.calibrated_action != SignalAction.buy_ready for signal in signal_set.calibrated_signal_set.signals)
    assert all("provider_stale" in signal.reason_codes for signal in signal_set.calibrated_signal_set.signals)
