from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, runtime_checkable

from quantpilot.packages.core.data.external import (
    DataQualityIssue,
    ExternalHistoricalMarketDataProvider,
    ExternalHistoricalSecurityProvider,
    HistoricalDataRequest,
    HistoricalDataResponse,
)
from quantpilot.packages.core.data.providers import MarketDataProvider, ProviderError, SecurityProvider
from quantpilot.packages.core.data.quality import ExchangeCalendar, SimpleKrxCalendar


KIS_DOMESTIC_DAILY_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
KIS_DOMESTIC_DAILY_TR_ID = "FHKST03010100"
KIS_DAILY_BAR_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "ticker", "pdno", "mksc_shrn_iscd"),
    "date": ("date", "stck_bsop_date"),
    "open": ("open", "stck_oprc"),
    "high": ("high", "stck_hgpr"),
    "low": ("low", "stck_lwpr"),
    "close": ("close", "stck_clpr"),
    "volume": ("volume", "acml_vol"),
}


@runtime_checkable
class JsonGetTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]: ...


class UrllibJsonTransport:
    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{url}?{query}", headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"KIS HTTP {exc.code}: {exc.reason}")
        except urllib.error.URLError as exc:
            raise ProviderError(f"KIS request failed: {exc.reason}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"KIS response was not valid JSON: {exc.msg}")
        if not isinstance(parsed, dict):
            raise ProviderError("KIS response JSON must be an object")
        return parsed


@dataclass(frozen=True)
class KisOpenApiConfig:
    app_key: str
    app_secret: str
    access_token: str
    base_url: str = "https://openapi.koreainvestment.com:9443"
    customer_type: str = "P"
    timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.25


class KisOpenApiHistoricalClient:
    """KIS Open API daily-bar client for historical/reference data only."""

    def __init__(
        self,
        config: KisOpenApiConfig,
        *,
        transport: JsonGetTransport | None = None,
    ) -> None:
        if not config.app_key.strip():
            raise ProviderError("KIS app key is required")
        if not config.app_secret.strip():
            raise ProviderError("KIS app secret is required")
        if not config.access_token.strip():
            raise ProviderError("KIS access token is required")
        self._config = config
        self._transport = transport or UrllibJsonTransport()

    def fetch_daily_bars(self, request: HistoricalDataRequest) -> HistoricalDataResponse:
        payloads: list[dict[str, Any]] = []
        quality_issues: list[DataQualityIssue] = []
        retry_count = 0
        for symbol in request.symbols:
            response, symbol_retries = self._fetch_symbol(symbol, request)
            retry_count += symbol_retries
            code = str(response.get("rt_cd", "0"))
            if code != "0":
                message = str(response.get("msg1", response.get("message", "KIS request rejected")))
                raise ProviderError(f"KIS rejected daily-bar request for {symbol}: {message}")
            raw_bars = response.get("output2")
            if not isinstance(raw_bars, list):
                raise ProviderError(f"KIS response for {symbol} is missing output2 daily-bar list")
            if not raw_bars:
                quality_issues.append(
                    DataQualityIssue(
                        code="missing_bar",
                        message="KIS returned no daily bars for requested symbol and window",
                        symbol=symbol,
                    )
                )
            for raw_bar in raw_bars:
                if not isinstance(raw_bar, dict):
                    raise ProviderError(f"KIS response for {symbol} contained a non-object daily bar")
                copied = dict(raw_bar)
                copied.setdefault("symbol", symbol)
                payloads.append(copied)

        return HistoricalDataResponse(
            payloads=payloads,
            provider_name="kis_open_api",
            market=request.market,
            adjusted=request.adjusted,
            retry_count=retry_count,
            quality_issues=quality_issues,
        )

    def _fetch_symbol(
        self,
        symbol: str,
        request: HistoricalDataRequest,
    ) -> tuple[dict[str, Any], int]:
        retries = 0
        last_error: ProviderError | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                return self._transport.get_json(
                    self._url(),
                    headers=self._headers(),
                    params=self._params(symbol, request),
                    timeout_seconds=self._config.timeout_seconds,
                ), retries
            except ProviderError as exc:
                last_error = exc
                if attempt >= self._config.max_retries:
                    break
                retries += 1
                if self._config.retry_backoff_seconds > 0:
                    time.sleep(self._config.retry_backoff_seconds)
        raise ProviderError(
            f"KIS daily-bar request failed after {self._config.max_retries + 1} attempt(s) for {symbol}: {last_error}"
        )

    def _url(self) -> str:
        return f"{self._config.base_url.rstrip('/')}{KIS_DOMESTIC_DAILY_ENDPOINT}"

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._config.access_token}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": KIS_DOMESTIC_DAILY_TR_ID,
            "custtype": self._config.customer_type,
        }

    def _params(self, symbol: str, request: HistoricalDataRequest) -> dict[str, str]:
        return {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": request.start_date.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": request.end_date.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0" if request.adjusted else "1",
        }


class KisHistoricalMarketDataProvider(ExternalHistoricalMarketDataProvider):
    def __init__(
        self,
        client: KisOpenApiHistoricalClient,
        *,
        symbols: list[str] | tuple[str, ...],
        start_date: date,
        end_date: date,
        market: str = "KR_STOCK",
        adjusted: bool = False,
        calendar: ExchangeCalendar | None = None,
    ) -> None:
        super().__init__(
            client,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            market=market,
            adjusted=adjusted,
            provider_name="kis_open_api",
            payload_aliases=KIS_DAILY_BAR_ALIASES,
            calendar=calendar,
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ProviderError(f"KIS external historical provider requires {name}")
    return value


def _optional_env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y"}


def _env_date(name: str) -> date:
    raw = _required_env(name)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ProviderError(f"{name} must be YYYY-MM-DD")


def _env_holidays() -> tuple[date, ...]:
    raw = os.environ.get("EXTERNAL_HISTORICAL_HOLIDAYS", "").strip()
    if not raw:
        return ()
    holidays: list[date] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        try:
            holidays.append(date.fromisoformat(token))
        except ValueError:
            raise ProviderError("EXTERNAL_HISTORICAL_HOLIDAYS must contain comma-separated YYYY-MM-DD dates")
    return tuple(holidays)


def _env_symbols() -> tuple[str, ...]:
    symbols = [item.strip() for item in _required_env("EXTERNAL_HISTORICAL_SYMBOLS").split(",")]
    cleaned = tuple(symbol.upper() for symbol in symbols if symbol)
    if not cleaned:
        raise ProviderError("EXTERNAL_HISTORICAL_SYMBOLS must contain at least one symbol")
    return cleaned


def build_kis_historical_client_from_env() -> KisOpenApiHistoricalClient:
    return KisOpenApiHistoricalClient(
        KisOpenApiConfig(
            app_key=_required_env("KIS_APP_KEY"),
            app_secret=_required_env("KIS_APP_SECRET"),
            access_token=_required_env("KIS_ACCESS_TOKEN"),
            base_url=_optional_env("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
        )
    )


def build_kis_historical_providers_from_env() -> tuple[SecurityProvider, MarketDataProvider]:
    symbols = _env_symbols()
    market = _optional_env("EXTERNAL_HISTORICAL_MARKET", "KR_STOCK")
    start_date = _env_date("EXTERNAL_HISTORICAL_START")
    end_date = _env_date("EXTERNAL_HISTORICAL_END")
    adjusted = _env_bool("EXTERNAL_HISTORICAL_ADJUSTED", default=False)
    security_provider = ExternalHistoricalSecurityProvider(symbols, market=market)
    market_data_provider = KisHistoricalMarketDataProvider(
        build_kis_historical_client_from_env(),
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        market=market,
        adjusted=adjusted,
        calendar=SimpleKrxCalendar(holidays=_env_holidays()),
    )
    return security_provider, market_data_provider
