from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypeVar


T = TypeVar("T")


def symbol_key(value: Any) -> str:
    return str(value).strip().upper()


def optional_symbol_key(value: Any) -> str | None:
    if value is None:
        return None
    symbol = symbol_key(value)
    return symbol or None


def first_not_none(values: Iterable[T | None]) -> T | None:
    for value in values:
        if value is not None:
            return value
    return None


def unique_text(values: Iterable[Any | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def unique_symbols(values: Iterable[Any | None]) -> list[str]:
    return unique_text(symbol_key(value) for value in values if value is not None)


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
