from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from quantpilot.packages.core.schemas import DataMode, utc_now
from quantpilot.packages.core.marketdata.symbols import (
    filter_bars_by_symbols,
    symbol_from_bar,
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


def default_ohlcv_fixture_path() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "ohlcv.json"


def load_fixture_ohlcv(path: Path | None = None) -> list[dict[str, Any]]:
    fixture_path = path or default_ohlcv_fixture_path()
    with fixture_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _quality(bars: list[dict[str, Any]]) -> MarketDataQuality:
    usable = bool(bars)
    return MarketDataQuality(
        usable=usable,
        degraded=not usable,
        reason_codes=[] if usable else ["ohlcv_empty"],
        symbol_count=len(unique_symbols_from_bars(bars)),
        data_mode=DataMode.fixture,
    )


class FixtureOHLCVProvider:
    def __init__(self, path: Path | None = None, *, provider_name: str = "fixture_ohlcv") -> None:
        self.provider_name = provider_name
        self._bars = load_fixture_ohlcv(path)

    def get_ohlcv(
        self,
        symbols: Sequence[str] | None = None,
        *,
        horizon: str | None = None,
    ) -> OHLCVSnapshot:
        del horizon
        bars = filter_bars_by_symbols(self._bars, symbols)
        return OHLCVSnapshot(
            bars=bars,
            provider_status=ProviderStatus(provider_name=self.provider_name, data_mode=DataMode.fixture),
            data_quality=_quality(bars),
        )


class FixtureQuoteProvider:
    def __init__(
        self,
        bars: list[dict[str, Any]] | None = None,
        *,
        provider_name: str = "fixture_quote",
    ) -> None:
        self.provider_name = provider_name
        self._bars = [dict(bar) for bar in (bars if bars is not None else load_fixture_ohlcv())]

    def get_quotes(self, symbols: Sequence[str]) -> QuoteSnapshot:
        wanted = symbol_set(symbols)
        quotes: dict[str, Quote] = {}
        for bar in self._bars:
            symbol = symbol_from_bar(bar)
            if not wanted or symbol in wanted:
                quotes[symbol] = Quote(symbol=symbol, last=float(bar["close"]), as_of=utc_now())

        missing = sorted(wanted - set(quotes))
        usable = not missing
        status = ProviderStatus(
            provider_name=self.provider_name,
            state="available" if usable else "unavailable",
            data_mode=DataMode.fixture,
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
                data_mode=DataMode.fixture,
            ),
        )
