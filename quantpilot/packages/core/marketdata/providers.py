from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from quantpilot.packages.core.schemas import DataMode, utc_now
from quantpilot.packages.core.marketdata.symbols import (
    filter_bars_by_symbols,
    symbol_from_bar,
    symbol_set,
    unique_symbols_from_bars,
)
from quantpilot.packages.core.marketdata.types import (
    L2Snapshot,
    MarketDataQuality,
    OHLCVSnapshot,
    ProviderStatus,
    Quote,
    QuoteSnapshot,
)


@runtime_checkable
class OHLCVProvider(Protocol):
    def get_ohlcv(
        self,
        symbols: Sequence[str] | None = None,
        *,
        horizon: str | None = None,
    ) -> OHLCVSnapshot: ...


@runtime_checkable
class QuoteProvider(Protocol):
    def get_quotes(self, symbols: Sequence[str]) -> QuoteSnapshot: ...


@runtime_checkable
class L2Provider(Protocol):
    def get_l2_snapshot(self, symbol: str) -> L2Snapshot: ...


def _quality_for_bars(
    bars: list[dict[str, Any]],
    *,
    data_mode: DataMode,
    reason_code: str = "ohlcv_empty",
) -> MarketDataQuality:
    usable = bool(bars)
    return MarketDataQuality(
        usable=usable,
        degraded=not usable,
        reason_codes=[] if usable else [reason_code],
        symbol_count=len(unique_symbols_from_bars(bars)),
        data_mode=data_mode,
    )


class BarOHLCVProvider:
    """Adapts existing get_bars() providers to the provider-bound signal path."""

    def __init__(
        self,
        source: Any,
        *,
        provider_name: str = "bar_ohlcv_provider",
        data_mode: DataMode = DataMode.fixture,
    ) -> None:
        self.source = source
        self.provider_name = provider_name
        self.data_mode = data_mode

    def get_ohlcv(
        self,
        symbols: Sequence[str] | None = None,
        *,
        horizon: str | None = None,
    ) -> OHLCVSnapshot:
        del horizon
        bars = filter_bars_by_symbols(self.source.get_bars(), symbols)
        return OHLCVSnapshot(
            bars=bars,
            provider_status=ProviderStatus(provider_name=self.provider_name, data_mode=self.data_mode),
            data_quality=_quality_for_bars(bars, data_mode=self.data_mode),
        )


class BarQuoteProvider:
    """Derives quote snapshots from existing get_bars() providers.

    This is fixture/local-data safe only: it exposes snapshot closes as reference
    quotes and never reaches a broker or realtime market API.
    """

    def __init__(
        self,
        source: Any,
        *,
        provider_name: str = "bar_quote_provider",
        data_mode: DataMode = DataMode.fixture,
    ) -> None:
        self.source = source
        self.provider_name = provider_name
        self.data_mode = data_mode

    def get_quotes(self, symbols: Sequence[str]) -> QuoteSnapshot:
        wanted = symbol_set(symbols)
        quotes: dict[str, Quote] = {}
        for bar in self.source.get_bars():
            symbol = symbol_from_bar(bar)
            if not wanted or symbol in wanted:
                quotes[symbol] = Quote(symbol=symbol, last=float(bar["close"]), as_of=utc_now())

        missing = sorted(wanted - set(quotes))
        usable = not missing
        status = ProviderStatus(
            provider_name=self.provider_name,
            state="available" if usable else "unavailable",
            data_mode=self.data_mode,
            reason=None if usable else f"missing quotes for: {', '.join(missing)}",
        )
        return QuoteSnapshot(
            quotes=quotes,
            provider_status=status,
            data_quality=MarketDataQuality(
                usable=usable,
                degraded=not usable,
                reason_codes=[] if usable else ["quote_missing"],
                symbol_count=len(quotes),
                data_mode=self.data_mode,
            ),
        )
