from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from math import isfinite
from typing import Any, Protocol, runtime_checkable

from quantpilot.packages.core.schemas import DataMode, utc_now
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


def _symbol_key(value: Any) -> str:
    return str(value).strip().upper()


def _bar_symbol(bar: Mapping[str, Any]) -> str:
    return _symbol_key(bar.get("symbol", bar.get("ticker", "")))


def _filter_bars(bars: list[dict[str, Any]], symbols: Sequence[str] | None) -> list[dict[str, Any]]:
    copied = [dict(bar) for bar in bars]
    if symbols is None:
        return copied
    wanted = {_symbol_key(symbol) for symbol in symbols}
    return [bar for bar in copied if _bar_symbol(bar) in wanted]


def _session_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value).strip()
    if not raw or "T" in raw or " " in raw or "+" in raw or raw.endswith("Z"):
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _normalize_completed_history(
    rows: Sequence[Any],
    symbols: Sequence[str] | None,
) -> tuple[list[dict[str, Any]], int]:
    wanted = None if symbols is None else {_symbol_key(symbol) for symbol in symbols}
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    invalid_count = 0

    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            invalid_count += 1
            continue
        symbol = _bar_symbol(raw_row)
        if not symbol:
            invalid_count += 1
            continue
        if wanted is not None and symbol not in wanted:
            continue

        raw_session = raw_row.get("date")
        if raw_session in (None, ""):
            raw_session = raw_row.get("session_date")
        session = _session_date(raw_session)
        values = {
            field: _finite_number(raw_row.get(field))
            for field in ("open", "high", "low", "close", "volume")
        }
        if session is None or any(value is None for value in values.values()):
            invalid_count += 1
            continue

        open_price = values["open"]
        high = values["high"]
        low = values["low"]
        close = values["close"]
        volume = values["volume"]
        assert open_price is not None and high is not None and low is not None
        assert close is not None and volume is not None
        if (
            min(open_price, high, low, close) <= 0
            or volume < 0
            or high < max(open_price, close, low)
            or low > min(open_price, close, high)
        ):
            invalid_count += 1
            continue

        key = (symbol, session)
        if key in seen:
            invalid_count += 1
            continue
        seen.add(key)
        normalized.append(
            {
                "symbol": symbol,
                "ticker": symbol,
                "date": session,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )

    normalized.sort(key=lambda row: (row["symbol"], row["date"]))
    return normalized, invalid_count


def _completed_history_quality(
    bars: list[dict[str, Any]],
    symbols: Sequence[str] | None,
    *,
    data_mode: DataMode,
    invalid_count: int,
) -> MarketDataQuality:
    present = {_bar_symbol(bar) for bar in bars if _bar_symbol(bar)}
    wanted = present if symbols is None else {_symbol_key(symbol) for symbol in symbols}
    missing = sorted(wanted - present)
    reason_codes: list[str] = []
    if not bars:
        reason_codes.append("ohlcv_completed_history_empty")
    if invalid_count:
        reason_codes.append("ohlcv_history_row_invalid")
    if missing:
        reason_codes.append("ohlcv_symbol_missing")
    usable = not reason_codes
    return MarketDataQuality(
        usable=usable,
        degraded=not usable,
        reason_codes=reason_codes,
        symbol_count=len(present),
        data_mode=data_mode,
    )


def _quality_for_bars(
    bars: list[dict[str, Any]],
    *,
    data_mode: DataMode,
    reason_code: str = "ohlcv_empty",
) -> MarketDataQuality:
    symbols = {_bar_symbol(bar) for bar in bars if _bar_symbol(bar)}
    usable = bool(bars)
    return MarketDataQuality(
        usable=usable,
        degraded=not usable,
        reason_codes=[] if usable else [reason_code],
        symbol_count=len(symbols),
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
        if horizon == "completed_history":
            history_loader = getattr(self.source, "get_price_history", None)
            if not callable(history_loader):
                return self._unavailable_history("ohlcv_completed_history_unavailable")
            try:
                rows = history_loader()
            except Exception:
                return self._unavailable_history("ohlcv_completed_history_unavailable")
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                return self._unavailable_history("ohlcv_completed_history_invalid_response")

            bars, invalid_count = _normalize_completed_history(rows, symbols)
            quality = _completed_history_quality(
                bars,
                symbols,
                data_mode=self.data_mode,
                invalid_count=invalid_count,
            )
            reason = ", ".join(quality.reason_codes) or None
            return OHLCVSnapshot(
                bars=bars,
                provider_status=ProviderStatus(
                    provider_name=self.provider_name,
                    state="available" if quality.usable else "unavailable",
                    data_mode=self.data_mode,
                    reason=reason,
                ),
                data_quality=quality,
            )

        bars = _filter_bars(self.source.get_bars(), symbols)
        return OHLCVSnapshot(
            bars=bars,
            provider_status=ProviderStatus(provider_name=self.provider_name, data_mode=self.data_mode),
            data_quality=_quality_for_bars(bars, data_mode=self.data_mode),
        )

    def _unavailable_history(self, reason_code: str) -> OHLCVSnapshot:
        return OHLCVSnapshot(
            bars=[],
            provider_status=ProviderStatus(
                provider_name=self.provider_name,
                state="unavailable",
                data_mode=self.data_mode,
                reason=reason_code,
            ),
            data_quality=MarketDataQuality(
                usable=False,
                degraded=True,
                reason_codes=[reason_code],
                symbol_count=0,
                data_mode=self.data_mode,
            ),
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
        wanted = {_symbol_key(symbol) for symbol in symbols}
        quotes: dict[str, Quote] = {}
        for bar in self.source.get_bars():
            symbol = _bar_symbol(bar)
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
