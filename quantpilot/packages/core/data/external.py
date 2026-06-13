from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Protocol, runtime_checkable

from quantpilot.packages.core.data.quality import (
    ExchangeCalendar,
    HistoricalDataFreshnessPolicy,
    HistoricalDataQualityIssue,
    HistoricalDataQualityReport,
    MissingBarPolicy,
    SimpleKrxCalendar,
    evaluate_historical_data_quality,
)
from quantpilot.packages.core.data.providers import ProviderError
from quantpilot.packages.core.technical.indicators import calculate_technical_indicators


DataQualityIssue = HistoricalDataQualityIssue


@dataclass(frozen=True)
class HistoricalDataRequest:
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    market: str
    adjusted: bool = False


@dataclass
class HistoricalDataResponse:
    payloads: list[dict[str, Any]]
    provider_name: str
    fetched_at: datetime | None = None
    market: str | None = None
    adjusted: bool | None = None
    rate_limit: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    quality_issues: list[DataQualityIssue | dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class HistoricalDataClient(Protocol):
    """Fetches raw provider daily-bar payloads for an explicit historical window."""

    def fetch_daily_bars(self, request: HistoricalDataRequest) -> HistoricalDataResponse: ...


class ExternalHistoricalSecurityProvider:
    """Minimal security metadata for explicitly selected external symbols.

    The provider carries only reference metadata needed by the existing universe
    boundary. It does not infer liquidity from market data or broker state.
    """

    def __init__(
        self,
        symbols: list[str] | tuple[str, ...],
        *,
        market: str,
        sector: str = "unknown",
        avg_daily_value: float = 0.0,
        data_ready: bool = True,
    ) -> None:
        cleaned = _normalize_symbols(symbols)
        self._securities = [
            {
                "ticker": symbol,
                "name": symbol,
                "market": market,
                "sector": sector,
                "themes": [],
                "avg_daily_value": float(avg_daily_value),
                "data_ready": bool(data_ready),
            }
            for symbol in cleaned
        ]

    def get_securities(self) -> list[dict[str, Any]]:
        return [dict(security, themes=list(security["themes"])) for security in self._securities]


_DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "ticker"),
    "date": ("date", "session_date", "trading_date"),
    "open": ("open",),
    "high": ("high",),
    "low": ("low",),
    "close": ("close",),
    "volume": ("volume",),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_symbols(symbols: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        value = str(symbol).strip().upper()
        if not value:
            raise ProviderError("external historical request contains an empty symbol")
        if value not in seen:
            cleaned.append(value)
            seen.add(value)
    if not cleaned:
        raise ProviderError("external historical request requires at least one symbol")
    return tuple(cleaned)


def _session_date(value: Any, *, provider_name: str, field_name: str, index: int) -> date:
    if isinstance(value, datetime):
        raise ProviderError(
            f"{provider_name} payload {index}: field '{field_name}' must be a plain session date"
        )
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if len(raw) == 8 and raw.isdigit():
        raw = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    if "T" in raw or " " in raw or "+" in raw or raw.endswith("Z"):
        raise ProviderError(
            f"{provider_name} payload {index}: field '{field_name}' must be a plain session date"
        )
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ProviderError(
            f"{provider_name} payload {index}: invalid field '{field_name}' date {value!r}"
        )


def _float_value(value: Any, *, provider_name: str, field_name: str, index: int) -> float:
    raw = str(value).strip().replace(",", "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ProviderError(
            f"{provider_name} payload {index}: field '{field_name}' is not a number: {value!r}"
        )


def _required_value(
    payload: dict[str, Any],
    *,
    aliases: dict[str, tuple[str, ...]],
    field_name: str,
    provider_name: str,
    index: int,
) -> Any:
    for alias in aliases[field_name]:
        if alias in payload and str(payload[alias]).strip() != "":
            return payload[alias]
    joined = ", ".join(aliases[field_name])
    raise ProviderError(
        f"{provider_name} payload {index}: missing required field '{field_name}' "
        f"(accepted aliases: {joined})"
    )


class ExternalHistoricalMarketDataProvider:
    """Maps raw external historical daily bars into QuantPilot OHLCV rows.

    The provider is intentionally pull-on-construction like the CSV provider:
    downstream code receives deterministic copies from an already validated
    in-memory dataset. Unit tests inject fake clients; no credentials or network
    access are required to exercise the mapping boundary.
    """

    def __init__(
        self,
        client: HistoricalDataClient,
        *,
        symbols: list[str] | tuple[str, ...],
        start_date: date,
        end_date: date,
        market: str,
        adjusted: bool = False,
        provider_name: str | None = None,
        payload_aliases: dict[str, tuple[str, ...]] | None = None,
        calendar: ExchangeCalendar | None = None,
        freshness_policy: HistoricalDataFreshnessPolicy | None = None,
        missing_bar_policy: MissingBarPolicy | None = None,
    ) -> None:
        request = HistoricalDataRequest(
            symbols=_normalize_symbols(symbols),
            start_date=start_date,
            end_date=end_date,
            market=market.strip(),
            adjusted=adjusted,
        )
        if not request.market:
            raise ProviderError("external historical request requires a market")
        if request.start_date > request.end_date:
            raise ProviderError("external historical start_date cannot be after end_date")

        self._request = request
        self._aliases = dict(_DEFAULT_ALIASES)
        if payload_aliases:
            self._aliases.update(payload_aliases)
        response = client.fetch_daily_bars(request)
        self._provider_name = provider_name or response.provider_name
        if not self._provider_name.strip():
            raise ProviderError("external historical response is missing provider_name")
        self._calendar = calendar or SimpleKrxCalendar()
        self._price_history = self._map_payloads(response.payloads)
        self._quality_report = evaluate_historical_data_quality(
            self._price_history,
            symbols=request.symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            market=request.market,
            provider_name=self._provider_name,
            calendar=self._calendar,
            freshness_policy=freshness_policy,
            missing_bar_policy=missing_bar_policy,
            provider_issues=response.quality_issues,
        )
        if self._quality_report.has_blocking_issues:
            raise ProviderError(_quality_block_message(self._quality_report))
        if not self._price_history:
            raise ProviderError(f"{self._provider_name} returned no bars in the requested date window")
        self._price_history.sort(key=lambda row: (row["symbol"], row["_date"]))
        self._signal_date = max(row["_date"] for row in self._price_history)
        self._provenance = self._build_provenance(response)

    def _map_payloads(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for index, payload in enumerate(payloads, start=1):
            symbol_raw = _required_value(
                payload,
                aliases=self._aliases,
                field_name="symbol",
                provider_name=self._provider_name,
                index=index,
            )
            symbol = str(symbol_raw).strip().upper()
            if not symbol:
                raise ProviderError(f"{self._provider_name} payload {index}: empty symbol")
            bar_date = _session_date(
                _required_value(
                    payload,
                    aliases=self._aliases,
                    field_name="date",
                    provider_name=self._provider_name,
                    index=index,
                ),
                provider_name=self._provider_name,
                field_name="date",
                index=index,
            )
            if bar_date < self._request.start_date or bar_date > self._request.end_date:
                continue

            values = {
                field_name: _float_value(
                    _required_value(
                        payload,
                        aliases=self._aliases,
                        field_name=field_name,
                        provider_name=self._provider_name,
                        index=index,
                    ),
                    provider_name=self._provider_name,
                    field_name=field_name,
                    index=index,
                )
                for field_name in ("open", "high", "low", "close", "volume")
            }

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

        return parsed

    def _build_provenance(self, response: HistoricalDataResponse) -> dict[str, Any]:
        quality = self._quality_report.as_dict()
        fetched_at = response.fetched_at or _utc_now()
        return {
            "provider_name": self._provider_name,
            "data_mode": "external_historical",
            "fetched_at": fetched_at.isoformat(),
            "market": response.market or self._request.market,
            "adjusted": self._request.adjusted if response.adjusted is None else response.adjusted,
            "symbols": list(self._request.symbols),
            "start_date": self._request.start_date.isoformat(),
            "end_date": self._request.end_date.isoformat(),
            "rate_limit": deepcopy(response.rate_limit),
            "retry_count": response.retry_count,
            "quality": deepcopy(quality),
            "quality_issues": deepcopy(quality["issues"]),
        }

    def get_price_history(self) -> list[dict[str, Any]]:
        return [{key: value for key, value in row.items() if key != "_date"} for row in self._price_history]

    def get_bars(self) -> list[dict[str, Any]]:
        history = self.get_price_history()
        symbols = sorted({row["symbol"] for row in history})
        bars: list[dict[str, Any]] = []
        for symbol in symbols:
            indicator = calculate_technical_indicators(
                history,
                ticker=symbol,
                signal_date=self._signal_date,
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

    def get_provenance(self) -> dict[str, Any]:
        return deepcopy(self._provenance)

    def get_data_quality(self) -> dict[str, Any]:
        return self._quality_report.as_dict()


def _quality_block_message(report: HistoricalDataQualityReport) -> str:
    issue_summaries = []
    for issue in report.blocking_issues[:5]:
        location = ""
        if issue.symbol:
            location += f" {issue.symbol}"
        if issue.session_date:
            location += f" {issue.session_date}"
        issue_summaries.append(f"{issue.code}{location}: {issue.message}")
    suffix = "; ".join(issue_summaries)
    remaining = len(report.blocking_issues) - len(issue_summaries)
    if remaining > 0:
        suffix = f"{suffix}; +{remaining} more"
    return f"{report.provider_name} historical data quality blocked: {suffix}"
