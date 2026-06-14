from __future__ import annotations

from quantpilot.packages.core.marketdata import (
    FakeOHLCVProvider,
    FakeQuoteProvider,
    FixtureOHLCVProvider,
    FixtureQuoteProvider,
    L2Provider,
    OHLCVProvider,
    QuoteProvider,
)
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


def test_fixture_marketdata_providers_satisfy_provider_bound_protocols() -> None:
    bars = load_fixture_ohlcv()

    assert isinstance(FixtureOHLCVProvider(), OHLCVProvider)
    assert isinstance(FixtureQuoteProvider(bars), QuoteProvider)


def test_fake_ohlcv_provider_generates_serializable_signal_set() -> None:
    bars = load_fixture_ohlcv()
    signal_set = generate_provider_bound_signals(
        load_default_strategy(),
        FakeOHLCVProvider(bars),
        quote_provider=FakeQuoteProvider.from_bars(bars),
        policy=UserPolicy(),
    )

    assert signal_set.data_quality.usable is True
    assert signal_set.provider_status["ohlcv"].state == "available"
    assert any(signal.action == SignalAction.buy_ready for signal in signal_set.signals)
    dumped = signal_set.model_dump(mode="json")
    assert dumped["provider_status"]["quote"]["state"] == "available"
    assert dumped["order_submission_enabled"] is False


def test_fake_quote_provider_returns_requested_quotes() -> None:
    snapshot = FakeQuoteProvider({"AAA": 105.0, "BBB": 102.0}).get_quotes(["AAA"])

    assert set(snapshot.quotes) == {"AAA"}
    assert snapshot.quotes["AAA"].last == 105.0
    assert snapshot.provider_status.state == "available"


def test_provider_symbol_matching_is_normalized() -> None:
    bars = [{"symbol": " aaa ", "close": 105.0}]

    ohlcv = FakeOHLCVProvider(bars).get_ohlcv(["AAA"])
    quotes = FakeQuoteProvider.from_bars(bars).get_quotes([" aaa "])

    assert len(ohlcv.bars) == 1
    assert ohlcv.data_quality.symbol_count == 1
    assert set(quotes.quotes) == {"AAA"}
    assert quotes.provider_status.state == "available"


def test_provider_unavailable_returns_no_buy_ready_signals() -> None:
    signal_set = generate_provider_bound_signals(
        load_default_strategy(),
        FakeOHLCVProvider.unavailable(reason="fixture outage"),
        policy=UserPolicy(),
        securities=[_security("AAA")],
    )

    assert signal_set.data_quality.usable is False
    assert "provider_unavailable" in signal_set.data_quality.reason_codes
    assert {signal.action for signal in signal_set.signals} == {SignalAction.blocked}
    assert all(signal.target_weight_hint == 0.0 for signal in signal_set.signals)


def test_stale_provider_returns_degraded_fail_closed_signals() -> None:
    bars = load_fixture_ohlcv()
    signal_set = generate_provider_bound_signals(
        load_default_strategy(),
        FakeOHLCVProvider.stale(bars, reason="stale fixture"),
        quote_provider=FakeQuoteProvider.from_bars(bars),
        policy=UserPolicy(),
    )

    assert signal_set.data_quality.degraded is True
    assert "provider_stale" in signal_set.data_quality.reason_codes
    assert all(signal.action != SignalAction.buy_ready for signal in signal_set.signals)
    assert {signal.action for signal in signal_set.signals} == {SignalAction.blocked}


def test_l2_provider_interface_is_importable_only() -> None:
    assert L2Provider.__name__ == "L2Provider"
