from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from quantpilot.packages.core.schemas import DataMode, utc_now
from quantpilot.packages.core.marketdata.symbols import (
    filter_bars_by_symbols,
    symbol_from_bar,
    symbol_key,
    symbol_set,
    unique_symbols_from_bars,
)
from quantpilot.packages.core.marketdata.types import (
    MarketDataQuality,
    OHLCVSnapshot,
    ProviderStatus,
    Quote,
    QuoteSnapshot,
)


class FakeOHLCVProvider:
    def __init__(
        self,
        bars: list[dict[str, Any]] | None = None,
        *,
        status: ProviderStatus | None = None,
        quality: MarketDataQuality | None = None,
    ) -> None:
        self._bars = [dict(bar) for bar in (bars or [])]
        self._status = status or ProviderStatus(provider_name="fake_ohlcv", data_mode=DataMode.fixture)
        self._quality = quality

    @classmethod
    def unavailable(cls, *, reason: str = "fake provider unavailable") -> "FakeOHLCVProvider":
        return cls(
            [],
            status=ProviderStatus(provider_name="fake_ohlcv", state="unavailable", reason=reason),
            quality=MarketDataQuality(
                usable=False,
                degraded=True,
                reason_codes=["provider_unavailable"],
                symbol_count=0,
            ),
        )

    @classmethod
    def stale(cls, bars: list[dict[str, Any]], *, reason: str = "fake provider stale") -> "FakeOHLCVProvider":
        return cls(
            bars,
            status=ProviderStatus(provider_name="fake_ohlcv", state="stale", reason=reason),
            quality=MarketDataQuality(
                usable=False,
                degraded=True,
                reason_codes=["provider_stale"],
                symbol_count=len(unique_symbols_from_bars(bars)),
            ),
        )

    def get_ohlcv(
        self,
        symbols: Sequence[str] | None = None,
        *,
        horizon: str | None = None,
    ) -> OHLCVSnapshot:
        del horizon
        bars = filter_bars_by_symbols(self._bars, symbols)
        quality = self._quality or MarketDataQuality(
            usable=bool(bars),
            degraded=not bool(bars),
            reason_codes=[] if bars else ["ohlcv_empty"],
            symbol_count=len(unique_symbols_from_bars(bars)),
        )
        return OHLCVSnapshot(
            bars=bars,
            provider_status=self._status,
            data_quality=quality,
        )


class FakeQuoteProvider:
    def __init__(
        self,
        quotes: dict[str, float] | None = None,
        *,
        status: ProviderStatus | None = None,
        quality: MarketDataQuality | None = None,
    ) -> None:
        self._quotes = {symbol_key(symbol): float(price) for symbol, price in (quotes or {}).items()}
        self._status = status or ProviderStatus(provider_name="fake_quote", data_mode=DataMode.fixture)
        self._quality = quality

    @classmethod
    def from_bars(cls, bars: list[dict[str, Any]]) -> "FakeQuoteProvider":
        return cls({symbol: float(bar["close"]) for bar in bars if (symbol := symbol_from_bar(bar))})

    @classmethod
    def unavailable(cls, *, reason: str = "fake quote provider unavailable") -> "FakeQuoteProvider":
        return cls(
            {},
            status=ProviderStatus(provider_name="fake_quote", state="unavailable", reason=reason),
            quality=MarketDataQuality(
                usable=False,
                degraded=True,
                reason_codes=["provider_unavailable"],
                symbol_count=0,
            ),
        )

    def get_quotes(self, symbols: Sequence[str]) -> QuoteSnapshot:
        wanted = symbol_set(symbols)
        selected = {
            symbol: Quote(symbol=symbol, last=price, as_of=utc_now())
            for symbol, price in self._quotes.items()
            if not wanted or symbol in wanted
        }
        missing = sorted(wanted - set(selected))
        quality = self._quality or MarketDataQuality(
            usable=not missing,
            degraded=bool(missing),
            reason_codes=[] if not missing else ["quote_missing"],
            symbol_count=len(selected),
        )
        status = self._status
        if missing and status.state == "available":
            status = status.model_copy(
                update={
                    "state": "unavailable",
                    "reason": f"missing quotes for: {', '.join(missing)}",
                }
            )
        return QuoteSnapshot(quotes=selected, provider_status=status, data_quality=quality)
