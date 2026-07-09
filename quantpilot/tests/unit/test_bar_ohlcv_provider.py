from __future__ import annotations

from datetime import date
from typing import Any

from quantpilot.packages.core.marketdata.providers import BarOHLCVProvider


class SpyBarSource:
    def __init__(
        self,
        *,
        bars: list[dict[str, Any]] | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> None:
        self.bars = bars or []
        self.history = history or []
        self.get_bars_calls = 0
        self.get_price_history_calls = 0

    def get_bars(self) -> list[dict[str, Any]]:
        self.get_bars_calls += 1
        return self.bars

    def get_price_history(self) -> list[dict[str, Any]]:
        self.get_price_history_calls += 1
        return self.history


def _history_bar(
    symbol: str,
    session_date: str | date,
    *,
    ticker_only: bool = False,
) -> dict[str, Any]:
    identity = {"ticker": symbol} if ticker_only else {"symbol": symbol}
    return {
        **identity,
        "date": session_date,
        "open": "99.0",
        "high": 102,
        "low": 98,
        "close": 101,
        "volume": 1_000,
        "ignored_provider_field": "not-forwarded",
    }


def test_bar_provider_preserves_legacy_get_bars_path_for_other_horizons() -> None:
    legacy_bar = {"symbol": "aaa", "close": 101.0}
    source = SpyBarSource(
        bars=[legacy_bar, {"symbol": "BBB", "close": 202.0}],
        history=[_history_bar("AAA", "2026-07-01")],
    )
    provider = BarOHLCVProvider(source)

    snapshot = provider.get_ohlcv(["AAA"], horizon="daily")

    assert snapshot.bars == [legacy_bar]
    assert source.get_bars_calls == 1
    assert source.get_price_history_calls == 0


def test_completed_history_uses_history_api_and_returns_canonical_copies() -> None:
    source = SpyBarSource(
        bars=[{"symbol": "SHOULD_NOT_BE_USED", "close": 1.0}],
        history=[
            _history_bar("bbb", "2026-07-02"),
            _history_bar("aaa", date(2026, 7, 2), ticker_only=True),
            _history_bar("AAA", "2026-07-01"),
        ],
    )
    provider = BarOHLCVProvider(source)

    snapshot = provider.get_ohlcv([" aAa "], horizon="completed_history")

    assert source.get_bars_calls == 0
    assert source.get_price_history_calls == 1
    assert snapshot.data_quality.usable is True
    assert snapshot.bars == [
        {
            "symbol": "AAA",
            "ticker": "AAA",
            "date": "2026-07-01",
            "open": 99.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0,
            "volume": 1000.0,
        },
        {
            "symbol": "AAA",
            "ticker": "AAA",
            "date": "2026-07-02",
            "open": 99.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0,
            "volume": 1000.0,
        },
    ]

    snapshot.bars[0]["close"] = 1.0
    assert source.history[2]["close"] == 101


def test_completed_history_fails_closed_for_invalid_or_missing_requested_rows() -> None:
    invalid = _history_bar("AAA", "not-a-session-date")
    source = SpyBarSource(history=[invalid, _history_bar("BBB", "2026-07-01")])

    snapshot = BarOHLCVProvider(source).get_ohlcv(
        ["AAA", "BBB", "CCC"],
        horizon="completed_history",
    )

    assert snapshot.bars == [
        {
            "symbol": "BBB",
            "ticker": "BBB",
            "date": "2026-07-01",
            "open": 99.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0,
            "volume": 1000.0,
        }
    ]
    assert snapshot.data_quality.usable is False
    assert snapshot.data_quality.degraded is True
    assert set(snapshot.data_quality.reason_codes) == {
        "ohlcv_history_row_invalid",
        "ohlcv_symbol_missing",
    }
    assert snapshot.provider_status.state == "unavailable"


def test_completed_history_never_falls_back_when_source_has_no_history_api() -> None:
    class LegacyOnlySource:
        def __init__(self) -> None:
            self.get_bars_calls = 0

        def get_bars(self) -> list[dict[str, Any]]:
            self.get_bars_calls += 1
            return [{"symbol": "AAA", "close": 101.0}]

    source = LegacyOnlySource()

    snapshot = BarOHLCVProvider(source).get_ohlcv(["AAA"], horizon="completed_history")

    assert source.get_bars_calls == 0
    assert snapshot.bars == []
    assert snapshot.data_quality.usable is False
    assert snapshot.data_quality.reason_codes == ["ohlcv_completed_history_unavailable"]
    assert snapshot.provider_status.state == "unavailable"
