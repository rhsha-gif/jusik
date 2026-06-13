"""Market-data and security-metadata providers.

This module introduces a Protocol seam in front of the in-repo fixtures so the
operator can optionally run signals against real historical data stored in local
CSV files -- with no network calls, no broker calls, and no credentials.

Fixtures remain the default. The CSV providers only activate when
``DATA_MODE=local_historical`` is explicitly configured together with a data
directory (see :func:`build_providers`). Misconfiguration fails closed by
raising :class:`ProviderError` rather than silently falling back.

External historical providers are available only through explicit injection or
through the opt-in ``DATA_MODE=external_historical`` plus provider-specific
configuration path. They remain historical/reference-data only.

Both market-data providers expose the same two shapes consumers already use:

* ``get_price_history()`` -- raw time-series OHLCV rows (drop-in for
  :func:`fixture_price_history`), consumed by the technical-indicator engine.
* ``get_bars()`` -- one snapshot bar per symbol (drop-in for
  :func:`load_fixture_ohlcv`), consumed by the deterministic signal classifier.

The CSV provider derives snapshot bars by *reusing*
:func:`calculate_technical_indicators`, so local data travels the exact same
decision path as fixtures.
"""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from quantpilot.packages.core.data.mode import resolve_data_mode
from quantpilot.packages.core.schemas import DataMode
from quantpilot.packages.core.signals.service import load_fixture_ohlcv
from quantpilot.packages.core.technical.indicators import (
    calculate_technical_indicators,
    fixture_price_history,
)
from quantpilot.packages.core.universe.builder import FIXTURE_SECURITIES


class ProviderError(ValueError):
    """Raised when a local data file is missing, malformed, or fails validation.

    Subclasses :class:`ValueError` so existing callers that catch ``ValueError``
    keep working, while giving local-data code a precise, catchable type.
    """


# --- Protocols ---------------------------------------------------------------


@runtime_checkable
class SecurityProvider(Protocol):
    """Returns raw security-metadata dicts consumed by ``build_candidate_universe``.

    Each dict uses the same keys as ``FIXTURE_SECURITIES`` (``ticker``, ``name``,
    ``market``, ``sector``, ``themes``, ``avg_daily_value``, ``data_ready``).
    """

    def get_securities(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """Returns OHLCV data in the two shapes the harness consumes."""

    def get_price_history(self) -> list[dict[str, Any]]: ...

    def get_bars(self) -> list[dict[str, Any]]: ...


# --- Fixture providers (default) --------------------------------------------


class FixtureSecurityProvider:
    """Wraps the module-level ``FIXTURE_SECURITIES`` constant (copy on read)."""

    def get_securities(self) -> list[dict[str, Any]]:
        return [dict(security) for security in FIXTURE_SECURITIES]


class FixtureMarketDataProvider:
    """Wraps the in-repo OHLCV fixtures, caching file reads at construction.

    Returns deep-ish copies on every read so a caller mutating the result can
    never corrupt the cached fixture state.
    """

    def __init__(self) -> None:
        self._bars: list[dict[str, Any]] = load_fixture_ohlcv()
        self._price_history: list[dict[str, Any]] = fixture_price_history()

    def get_bars(self) -> list[dict[str, Any]]:
        return [dict(bar) for bar in self._bars]

    def get_price_history(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._price_history]


# --- CSV parsing helpers -----------------------------------------------------

_SECURITY_REQUIRED = ("name", "market", "sector")
_OHLCV_REQUIRED = ("symbol", "date", "open", "high", "low", "close", "volume")


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV into (header, rows). Raises ProviderError on missing/empty files."""
    if not path.exists():
        raise ProviderError(f"local data file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ProviderError(f"local data file is empty: {path}")
        header = [name.strip() for name in reader.fieldnames]
        rows = [dict(row) for row in reader]
    if not rows:
        raise ProviderError(f"local data file has a header but no rows: {path}")
    return header, rows


def _require_columns(path: Path, header: list[str], required: tuple[str, ...]) -> None:
    missing = [column for column in required if column not in header]
    if missing:
        raise ProviderError(
            f"{path.name} is missing required column(s): {', '.join(missing)} "
            f"(found: {', '.join(header)})"
        )


def _cell(row: dict[str, str], column: str) -> str:
    return (row.get(column) or "").strip()


def _parse_float(path: Path, line: int, column: str, raw: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ProviderError(
            f"{path.name} line {line}: column '{column}' is not a number: {raw!r}"
        )


def _parse_session_date(path: Path, line: int, raw: str) -> date:
    """Parse a plain ISO session date (YYYY-MM-DD).

    Local historical bars are naive *session* dates -- one bar per trading day.
    Timezone-aware or datetime strings are rejected so session/timezone
    assumptions stay explicit rather than being silently coerced.
    """
    if "T" in raw or " " in raw or "+" in raw or "Z" in raw:
        raise ProviderError(
            f"{path.name} line {line}: date must be a plain session date "
            f"(YYYY-MM-DD), got {raw!r}"
        )
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ProviderError(
            f"{path.name} line {line}: invalid date {raw!r} (expected YYYY-MM-DD)"
        )


# --- CSV providers -----------------------------------------------------------


class CsvSecurityProvider:
    """Loads security metadata from a local CSV file.

    Expected columns: ``symbol`` (or ``ticker``), ``name``, ``market``,
    ``sector``, ``themes`` (or ``tags``, pipe-delimited),
    ``avg_daily_value`` (or ``average_daily_value``), and optional
    ``data_ready``. Parsed once at construction; copied on every read.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._securities = self._load()

    def _load(self) -> list[dict[str, Any]]:
        header, rows = _read_rows(self._path)
        _require_columns(self._path, header, _SECURITY_REQUIRED)
        symbol_col = "symbol" if "symbol" in header else ("ticker" if "ticker" in header else None)
        if symbol_col is None:
            raise ProviderError(
                f"{self._path.name} is missing required column(s): symbol "
                f"(found: {', '.join(header)})"
            )
        themes_col = "themes" if "themes" in header else ("tags" if "tags" in header else None)
        value_col = next(
            (column for column in ("avg_daily_value", "average_daily_value", "liquidity") if column in header),
            None,
        )

        securities: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, row in enumerate(rows, start=2):  # line 1 is the header
            ticker = _cell(row, symbol_col).upper()
            if not ticker:
                raise ProviderError(f"{self._path.name} line {index}: empty symbol")
            if ticker in seen:
                raise ProviderError(f"{self._path.name} line {index}: duplicate symbol {ticker}")
            seen.add(ticker)

            themes_raw = _cell(row, themes_col) if themes_col else ""
            themes = [theme.strip().lower() for theme in themes_raw.split("|") if theme.strip()]
            avg_value = (
                _parse_float(self._path, index, value_col, _cell(row, value_col))
                if value_col and _cell(row, value_col)
                else 0.0
            )
            data_ready_raw = _cell(row, "data_ready").lower() if "data_ready" in header else ""
            data_ready = data_ready_raw not in {"false", "0", "no", "n"}

            securities.append(
                {
                    "ticker": ticker,
                    "name": _cell(row, "name"),
                    "market": _cell(row, "market"),
                    "sector": _cell(row, "sector"),
                    "themes": themes,
                    "avg_daily_value": avg_value,
                    "data_ready": data_ready,
                }
            )
        return securities

    def get_securities(self) -> list[dict[str, Any]]:
        return [dict(security, themes=list(security["themes"])) for security in self._securities]


class CsvMarketDataProvider:
    """Loads OHLCV time-series from a local CSV file.

    Expected columns: ``symbol``, ``date``, ``open``, ``high``, ``low``,
    ``close``, ``volume``. Parsed and validated once at construction. Rejects
    duplicate ``(symbol, date)`` bars and non-positive prices. ``get_bars()``
    derives one snapshot bar per symbol by reusing
    :func:`calculate_technical_indicators`.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._price_history = self._load()
        self._signal_date = max(row["_date"] for row in self._price_history)

    def _load(self) -> list[dict[str, Any]]:
        header, rows = _read_rows(self._path)
        _require_columns(self._path, header, _OHLCV_REQUIRED)

        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for index, row in enumerate(rows, start=2):  # line 1 is the header
            symbol = _cell(row, "symbol").upper()
            if not symbol:
                raise ProviderError(f"{self._path.name} line {index}: empty symbol")
            bar_date = _parse_session_date(self._path, index, _cell(row, "date"))
            key = (symbol, bar_date.isoformat())
            if key in seen:
                raise ProviderError(
                    f"{self._path.name} line {index}: duplicate bar for {symbol} on {bar_date.isoformat()}"
                )
            seen.add(key)

            values = {
                column: _parse_float(self._path, index, column, _cell(row, column))
                for column in ("open", "high", "low", "close", "volume")
            }
            if values["high"] < values["low"]:
                raise ProviderError(
                    f"{self._path.name} line {index}: high {values['high']} < low {values['low']} for {symbol}"
                )
            if min(values["open"], values["high"], values["low"], values["close"]) <= 0:
                raise ProviderError(
                    f"{self._path.name} line {index}: non-positive price for {symbol}"
                )
            if values["volume"] < 0:
                raise ProviderError(
                    f"{self._path.name} line {index}: negative volume for {symbol}"
                )

            parsed.append(
                {
                    "symbol": symbol,
                    "ticker": symbol,
                    "date": bar_date.isoformat(),
                    "_date": bar_date,
                    "open": values["open"],
                    "high": values["high"],
                    "low": values["low"],
                    "close": values["close"],
                    "volume": int(values["volume"]),
                }
            )
        parsed.sort(key=lambda row: (row["symbol"], row["_date"]))
        return parsed

    def get_price_history(self) -> list[dict[str, Any]]:
        return [{key: value for key, value in row.items() if key != "_date"} for row in self._price_history]

    def get_bars(self) -> list[dict[str, Any]]:
        history = self.get_price_history()
        symbols = sorted({row["symbol"] for row in history})
        bars: list[dict[str, Any]] = []
        for symbol in symbols:
            indicator = calculate_technical_indicators(
                history, ticker=symbol, signal_date=self._signal_date
            )
            last = max(
                (row for row in history if row["symbol"] == symbol),
                key=lambda row: row["date"],
            )
            bars.append(
                {
                    "symbol": symbol,
                    "open": last["open"],
                    "high": last["high"],
                    "low": last["low"],
                    "close": indicator.close,
                    "volume": last["volume"],
                    "ma20": indicator.moving_averages["ma20"],
                    "rsi": indicator.rsi,
                    "volume_ratio": indicator.volume_ratio,
                    "position_weight": 0.0,
                    "blocked": False,
                }
            )
        return bars


# --- Factory -----------------------------------------------------------------


def resolve_local_data_dir(data_dir: str | Path | None = None) -> Path:
    """Resolve the local data directory from an explicit arg or ``LOCAL_DATA_DIR``."""
    base = data_dir if data_dir is not None else os.environ.get("LOCAL_DATA_DIR")
    if not base:
        raise ProviderError(
            "local_historical mode requires a data directory: pass data_dir or set LOCAL_DATA_DIR"
        )
    return Path(base)


def _validate_local_symbol_coverage(
    security_provider: SecurityProvider,
    market_data_provider: MarketDataProvider,
) -> None:
    security_symbols = {str(security["ticker"]).upper() for security in security_provider.get_securities()}
    history_symbols = {str(row["symbol"]).upper() for row in market_data_provider.get_price_history()}
    missing = sorted(security_symbols - history_symbols)
    if missing:
        raise ProviderError(f"local historical data is missing OHLCV rows for: {', '.join(missing)}")


def build_providers(
    mode: DataMode,
    *,
    data_dir: str | Path | None = None,
    external_security_provider: SecurityProvider | None = None,
    external_market_data_provider: MarketDataProvider | None = None,
) -> tuple[SecurityProvider, MarketDataProvider]:
    """Return ``(security_provider, market_data_provider)`` for the given mode.

    ``external_historical`` is deliberately explicit: callers must inject a
    fake/test provider or set provider-specific environment configuration.
    Later modes must add their own branches instead of silently reusing fixtures.
    """
    if mode == DataMode.fixture:
        return FixtureSecurityProvider(), FixtureMarketDataProvider()
    if mode == DataMode.local_historical:
        base = resolve_local_data_dir(data_dir)
        security_provider = CsvSecurityProvider(base / "securities.csv")
        market_data_provider = CsvMarketDataProvider(base / "ohlcv.csv")
        _validate_local_symbol_coverage(security_provider, market_data_provider)
        return security_provider, market_data_provider
    if mode == DataMode.external_historical:
        if external_security_provider is not None and external_market_data_provider is not None:
            return external_security_provider, external_market_data_provider
        provider_name = os.environ.get("EXTERNAL_HISTORICAL_PROVIDER", "").strip().lower()
        if not provider_name:
            raise ProviderError(
                "external_historical mode requires explicit provider injection "
                "or EXTERNAL_HISTORICAL_PROVIDER"
            )
        if provider_name == "kis":
            from quantpilot.packages.core.data.kis_historical import build_kis_historical_providers_from_env

            return build_kis_historical_providers_from_env()
        raise ProviderError(f"unsupported EXTERNAL_HISTORICAL_PROVIDER {provider_name!r}")
    raise ProviderError(f"{mode.value} data mode is not implemented by the provider factory")


def build_providers_from_env(
    *,
    data_dir: str | Path | None = None,
) -> tuple[SecurityProvider, MarketDataProvider]:
    """Build providers from DATA_MODE/LOCAL_DATA_DIR, failing closed on bad config."""
    return build_providers(resolve_data_mode(), data_dir=data_dir)
