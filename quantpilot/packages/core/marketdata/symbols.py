from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from quantpilot.packages.core.normalization import symbol_key


def symbol_from_bar(bar: Mapping[str, Any]) -> str:
    return symbol_key(bar.get("symbol", bar.get("ticker", "")))


def symbol_from_security(security: Mapping[str, Any]) -> str:
    return symbol_key(security.get("ticker", security.get("symbol", "")))


def symbol_set(symbols: Sequence[Any]) -> set[str]:
    return {symbol for value in symbols if (symbol := symbol_key(value))}


def unique_symbols_from_bars(bars: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({symbol for bar in bars if (symbol := symbol_from_bar(bar))})


def unique_symbols_from_securities(securities: Sequence[Mapping[str, Any]] | None) -> list[str]:
    if securities is None:
        return []
    return sorted({symbol for security in securities if (symbol := symbol_from_security(security))})


def filter_bars_by_symbols(
    bars: Sequence[Mapping[str, Any]],
    symbols: Sequence[Any] | None,
) -> list[dict[str, Any]]:
    copied = [dict(bar) for bar in bars]
    if symbols is None:
        return copied
    wanted = symbol_set(symbols)
    return [bar for bar in copied if symbol_from_bar(bar) in wanted]
