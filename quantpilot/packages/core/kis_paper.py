from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from calendar import monthrange
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable


KIS_PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
KIS_TOKEN_ENDPOINT = "/oauth2/tokenP"
KIS_CURRENT_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"
KIS_CURRENT_PRICE_TR_ID = "FHKST01010100"
KIS_L2_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
KIS_L2_TR_ID = "FHKST01010200"
KIS_BALANCE_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-balance"
KIS_BALANCE_TR_ID = "VTTC8434R"
KIS_BUYING_POWER_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
KIS_BUYING_POWER_TR_ID = "VTTC8908R"
KIS_DAILY_ORDERS_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
KIS_DAILY_ORDERS_TR_ID = "VTTC0081R"
KIS_CASH_ORDER_ENDPOINT = "/uapi/domestic-stock/v1/trading/order-cash"
KIS_CASH_BUY_TR_ID = "VTTC0012U"
KIS_CASH_SELL_TR_ID = "VTTC0011U"

_PAPER_HOST = "openapivts.koreainvestment.com"
_PAPER_PORT = 29443
_ALLOWED_ENDPOINTS = frozenset(
    {
        KIS_TOKEN_ENDPOINT,
        KIS_CURRENT_PRICE_ENDPOINT,
        KIS_L2_ENDPOINT,
        KIS_BALANCE_ENDPOINT,
        KIS_BUYING_POWER_ENDPOINT,
        KIS_DAILY_ORDERS_ENDPOINT,
        KIS_CASH_ORDER_ENDPOINT,
    }
)
_SAFE_CODE = re.compile(r"[A-Za-z0-9_.-]{1,32}\Z")
_SYMBOL = re.compile(r"[A-Z0-9]{6}\Z")
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_PAGES = 100


def kis_recent_three_month_start(reference_date: date) -> date:
    """Return the inclusive calendar-three-month KIS history boundary."""

    if isinstance(reference_date, datetime) or not isinstance(
        reference_date,
        date,
    ):
        raise KisPaperConfigurationError("daily-order as-of date must be a date value")
    month_index = reference_date.year * 12 + (reference_date.month - 1) - 3
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(reference_date.day, monthrange(year, month)[1])
    return date(year, month, day)


class KisPaperError(RuntimeError):
    """Base class for fail-closed KIS paper boundary errors."""


class KisPaperConfigurationError(KisPaperError):
    """Local configuration was unsafe or incomplete."""


class KisPaperTransportError(KisPaperError):
    """The HTTP exchange did not produce a usable KIS response."""


class KisPaperProtocolError(KisPaperError):
    """KIS returned a malformed or ambiguous response."""


class KisPaperBusinessError(KisPaperError):
    """KIS explicitly rejected a valid request."""


class KisPaperOrderOutcomeUnknown(KisPaperError):
    """An order may have reached KIS, but no definitive outcome was received."""


@dataclass(frozen=True)
class KisHttpResponse:
    status_code: int
    payload: Mapping[str, Any] = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int):
            raise TypeError("status_code must be an integer")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be an object mapping")
        if not isinstance(self.headers, Mapping):
            raise TypeError("headers must be a string mapping")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(
            self,
            "headers",
            MappingProxyType({str(key).lower(): str(value) for key, value in self.headers.items()}),
        )


@runtime_checkable
class KisJsonTransport(Protocol):
    def request_json(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None,
        body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> KisHttpResponse: ...


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class StrictUrllibKisPaperTransport:
    """One-attempt, no-proxy HTTPS transport pinned to the KIS paper origin."""

    def __init__(self) -> None:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RejectRedirects(),
            urllib.request.HTTPSHandler(context=context),
        )

    def request_json(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None,
        body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> KisHttpResponse:
        _validate_request_url(url)
        if method not in {"GET", "POST"}:
            raise KisPaperConfigurationError("KIS paper transport only supports GET and POST")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise KisPaperConfigurationError("KIS paper timeout must be finite and positive")
        if method == "GET" and body is not None:
            raise KisPaperConfigurationError("GET requests cannot contain a JSON body")
        if method == "POST" and params:
            raise KisPaperConfigurationError("POST requests cannot contain query parameters")

        request_url = url
        request_data: bytes | None = None
        if method == "GET" and params:
            request_url = f"{url}?{urllib.parse.urlencode(dict(params))}"
        if method == "POST":
            request_data = json.dumps(dict(body or {}), separators=(",", ":")).encode("utf-8")

        request_headers = dict(headers)
        if method == "POST":
            request_headers.setdefault("content-type", "application/json; charset=utf-8")
        request = urllib.request.Request(
            request_url,
            data=request_data,
            headers=request_headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise KisPaperProtocolError("KIS paper response exceeded the safety limit")
                status_code = int(response.getcode())
                response_headers = {"tr_cont": response.headers.get("tr_cont", "")}
        except urllib.error.HTTPError as exc:
            raise KisPaperTransportError(f"KIS paper HTTP status {exc.code}") from None
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            OSError,
        ):
            raise KisPaperTransportError("KIS paper transport failed before a usable response") from None

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise KisPaperProtocolError("KIS paper response was not valid UTF-8 JSON") from None
        if not isinstance(decoded, dict):
            raise KisPaperProtocolError("KIS paper response JSON must be an object")
        return KisHttpResponse(
            status_code=status_code,
            payload=decoded,
            headers=response_headers,
        )


@dataclass(frozen=True)
class KisPaperConfig:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    account_number: str = field(repr=False)
    product_code: str = field(default="01", repr=False)
    access_token: str = field(default="", repr=False)
    base_url: str = KIS_PAPER_BASE_URL
    customer_type: str = "P"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        _validate_base_url(self.base_url)
        for value, label in (
            (self.app_key, "app key"),
            (self.app_secret, "app secret"),
            (self.account_number, "account number"),
            (self.product_code, "account product code"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise KisPaperConfigurationError(f"KIS paper {label} is required")
        normalized_account = self.account_number.strip()
        normalized_product = self.product_code.strip()
        if re.fullmatch(r"\d{8}", normalized_account) is None:
            raise KisPaperConfigurationError(
                "KIS paper account number must be the eight-digit account scope"
            )
        if re.fullmatch(r"\d{2}", normalized_product) is None:
            raise KisPaperConfigurationError(
                "KIS paper account product code must contain two digits"
            )
        object.__setattr__(self, "app_key", self.app_key.strip())
        object.__setattr__(self, "app_secret", self.app_secret.strip())
        object.__setattr__(self, "account_number", normalized_account)
        object.__setattr__(self, "product_code", normalized_product)
        object.__setattr__(self, "access_token", self.access_token.strip())
        if self.customer_type != "P":
            raise KisPaperConfigurationError("KIS paper customer type must be P")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise KisPaperConfigurationError("KIS paper timeout must be finite and positive")

    @property
    def account_scope_fingerprint(self) -> str:
        return paper_account_scope_fingerprint(
            self.account_number,
            self.product_code,
        )

    def with_access_token(self, access_token: str) -> KisPaperConfig:
        if not isinstance(access_token, str) or not access_token.strip():
            raise KisPaperConfigurationError("KIS paper access token is required")
        return replace(self, access_token=access_token)


def paper_account_scope_fingerprint(
    account_number: str,
    product_code: str,
) -> str:
    """Return the only account identity permitted in durable paper state."""

    account = account_number.strip()
    product = product_code.strip()
    if re.fullmatch(r"\d{8}", account) is None or re.fullmatch(
        r"\d{2}",
        product,
    ) is None:
        raise KisPaperConfigurationError(
            "KIS paper account scope must use eight and two digits"
        )
    normalized = f"{account}\x1f{product}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class KisPaperAccessToken:
    access_token: str = field(repr=False)
    token_type: str
    expires_in_seconds: int


@dataclass(frozen=True)
class KisCurrentPrice:
    symbol: str
    last_price: Decimal
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    accumulated_volume: int
    transaction_id: str = KIS_CURRENT_PRICE_TR_ID


@dataclass(frozen=True)
class KisOrderBookLevel:
    level: int
    ask_price: Decimal
    bid_price: Decimal
    ask_quantity: int
    bid_quantity: int


@dataclass(frozen=True)
class KisL2Snapshot:
    symbol: str
    accepted_at_hhmmss: str
    levels: tuple[KisOrderBookLevel, ...]
    transaction_id: str = KIS_L2_TR_ID


@dataclass(frozen=True)
class KisBalancePosition:
    symbol: str
    product_name: str
    holding_quantity: int
    orderable_quantity: int
    purchase_average_price: Decimal
    current_price: Decimal
    purchase_amount: Decimal
    evaluation_amount: Decimal


@dataclass(frozen=True)
class KisBalanceSummary:
    deposit_amount: Decimal
    next_day_settlement_amount: Decimal
    total_purchase_amount: Decimal
    total_evaluation_amount: Decimal
    net_asset_amount: Decimal
    evaluation_profit_loss: Decimal


@dataclass(frozen=True)
class KisBalanceResult:
    positions: tuple[KisBalancePosition, ...]
    summary: KisBalanceSummary
    pages_fetched: int
    transaction_id: str = KIS_BALANCE_TR_ID


@dataclass(frozen=True)
class KisBuyingPower:
    symbol: str
    limit_price: Decimal
    orderable_cash: Decimal
    no_receivable_buy_amount: Decimal
    no_receivable_buy_quantity: int
    maximum_buy_amount: Decimal
    maximum_buy_quantity: int
    calculation_price: Decimal
    transaction_id: str = KIS_BUYING_POWER_TR_ID


@dataclass(frozen=True)
class KisDailyOrderFill:
    order_number: str
    original_order_number: str
    order_branch_number: str
    order_date: str
    order_time: str
    symbol: str
    product_name: str
    side: Literal["buy", "sell"]
    order_quantity: int
    order_price: Decimal
    total_filled_quantity: int
    average_fill_price: Decimal
    remaining_quantity: int
    rejected_quantity: int
    cancelled: bool
    confirmed_cancel_quantity: int
    total_filled_amount: Decimal


@dataclass(frozen=True)
class KisDailyOrdersResult:
    rows: tuple[KisDailyOrderFill, ...]
    pages_fetched: int
    transaction_id: str = KIS_DAILY_ORDERS_TR_ID


@dataclass(frozen=True)
class KisCashOrderResult:
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    limit_price: Decimal
    order_number: str
    krx_forwarding_order_org_number: str
    order_time: str
    message_code: str
    transaction_id: str


class KisPaperClient:
    def __init__(
        self,
        config: KisPaperConfig,
        *,
        transport: KisJsonTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or StrictUrllibKisPaperTransport()

    @property
    def account_scope_fingerprint(self) -> str:
        return self._config.account_scope_fingerprint

    def request_access_token(self) -> KisPaperAccessToken:
        response = self._request(
            "POST",
            KIS_TOKEN_ENDPOINT,
            headers={"content-type": "application/json; charset=utf-8"},
            body={
                "grant_type": "client_credentials",
                "appkey": self._config.app_key,
                "appsecret": self._config.app_secret,
            },
        )
        _ensure_http_success(response, "token request")
        if "rt_cd" in response.payload:
            _assert_business_success(response, "token request")
        token = _required_text(response.payload, "access_token", "token response")
        token_type = _required_text(response.payload, "token_type", "token response")
        expires_in = _required_int(response.payload, "expires_in", "token response", minimum=1)
        return KisPaperAccessToken(
            access_token=token,
            token_type=token_type,
            expires_in_seconds=expires_in,
        )

    def get_current_price(self, symbol: str, *, exchange: str = "KRX") -> KisCurrentPrice:
        normalized_symbol = _validate_market_request(symbol, exchange)
        response = self._authenticated_get(
            KIS_CURRENT_PRICE_ENDPOINT,
            KIS_CURRENT_PRICE_TR_ID,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": normalized_symbol},
        )
        _assert_business_success(response, "current-price request")
        output = _required_mapping(response.payload, "output", "current-price response")
        return KisCurrentPrice(
            symbol=normalized_symbol,
            last_price=_required_decimal(output, "stck_prpr", "current-price output"),
            open_price=_required_decimal(output, "stck_oprc", "current-price output"),
            high_price=_required_decimal(output, "stck_hgpr", "current-price output"),
            low_price=_required_decimal(output, "stck_lwpr", "current-price output"),
            accumulated_volume=_required_int(
                output, "acml_vol", "current-price output", minimum=0
            ),
        )

    def get_l2(self, symbol: str, *, exchange: str = "KRX") -> KisL2Snapshot:
        normalized_symbol = _validate_market_request(symbol, exchange)
        response = self._authenticated_get(
            KIS_L2_ENDPOINT,
            KIS_L2_TR_ID,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": normalized_symbol},
        )
        _assert_business_success(response, "L2 request")
        output = _required_mapping(response.payload, "output1", "L2 response")
        accepted_at = _required_text(output, "aspr_acpt_hour", "L2 output")
        if not re.fullmatch(r"\d{6}", accepted_at):
            raise KisPaperProtocolError("L2 output has an invalid acceptance time")
        levels = tuple(
            KisOrderBookLevel(
                level=level,
                ask_price=_required_decimal(output, f"askp{level}", "L2 output"),
                bid_price=_required_decimal(output, f"bidp{level}", "L2 output"),
                ask_quantity=_required_int(
                    output, f"askp_rsqn{level}", "L2 output", minimum=0
                ),
                bid_quantity=_required_int(
                    output, f"bidp_rsqn{level}", "L2 output", minimum=0
                ),
            )
            for level in range(1, 11)
        )
        return KisL2Snapshot(
            symbol=normalized_symbol,
            accepted_at_hhmmss=accepted_at,
            levels=levels,
        )

    def get_balance(self, *, exchange: str = "KRX") -> KisBalanceResult:
        _validate_exchange(exchange)
        params = {
            "CANO": self._config.account_number,
            "ACNT_PRDT_CD": self._config.product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        positions: list[KisBalancePosition] = []
        seen_symbols: set[str] = set()
        summary: KisBalanceSummary | None = None
        pages = 0
        for response in self._paginated_get(KIS_BALANCE_ENDPOINT, KIS_BALANCE_TR_ID, params):
            pages += 1
            raw_positions = _required_list(response.payload, "output1", "balance response")
            raw_summaries = _required_list(response.payload, "output2", "balance response")
            if len(raw_summaries) != 1 or not isinstance(raw_summaries[0], Mapping):
                raise KisPaperProtocolError("balance response must contain exactly one summary")
            page_summary = _parse_balance_summary(raw_summaries[0])
            if summary is None:
                summary = page_summary
            elif page_summary != summary:
                raise KisPaperProtocolError(
                    "balance response summary changed across pagination"
                )
            for raw_position in raw_positions:
                position = _parse_balance_position(raw_position)
                if position.symbol in seen_symbols:
                    raise KisPaperProtocolError(
                        "balance response repeated a symbol across pagination"
                    )
                seen_symbols.add(position.symbol)
                positions.append(position)
        if summary is None:
            raise KisPaperProtocolError("balance response did not contain a summary")
        return KisBalanceResult(tuple(positions), summary, pages)

    def get_daily_orders_and_fills(
        self,
        start_date: date,
        end_date: date,
        *,
        exchange: str = "KRX",
        as_of_date: date | None = None,
    ) -> KisDailyOrdersResult:
        _validate_exchange(exchange)
        if any(
            isinstance(value, datetime) or not isinstance(value, date)
            for value in (start_date, end_date)
        ):
            raise KisPaperConfigurationError("daily-order dates must be date values")
        if start_date > end_date:
            raise KisPaperConfigurationError("daily-order start date cannot follow end date")
        reference_date = as_of_date or date.today()
        if isinstance(reference_date, datetime) or not isinstance(
            reference_date,
            date,
        ):
            raise KisPaperConfigurationError("daily-order as-of date must be a date value")
        if end_date > reference_date:
            raise KisPaperConfigurationError("daily-order end date cannot be in the future")
        if start_date < kis_recent_three_month_start(reference_date):
            raise KisPaperConfigurationError(
                "daily-order reconciliation is limited to the recent-three-month paper TR"
            )
        params = {
            "CANO": self._config.account_number,
            "ACNT_PRDT_CD": self._config.product_code,
            "INQR_STRT_DT": start_date.strftime("%Y%m%d"),
            "INQR_END_DT": end_date.strftime("%Y%m%d"),
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": "KRX",
        }
        rows: list[KisDailyOrderFill] = []
        pages = 0
        for response in self._paginated_get(
            KIS_DAILY_ORDERS_ENDPOINT, KIS_DAILY_ORDERS_TR_ID, params
        ):
            pages += 1
            raw_rows = _required_list(response.payload, "output1", "daily-order response")
            rows.extend(_parse_daily_order(row) for row in raw_rows)
        return KisDailyOrdersResult(tuple(rows), pages)

    def get_buying_power(
        self,
        symbol: str,
        limit_price: Decimal,
        *,
        exchange: str = "KRX",
    ) -> KisBuyingPower:
        """Return no-receivable paper buying capacity for one domestic symbol.

        The official KIS sample requires ``ORD_DVSN=01`` when checking the
        quantity for a full purchase so the symbol margin rate is reflected.
        This is an inquiry only; the executable order remains limit-only.
        """

        normalized_symbol = _validate_market_request(symbol, exchange)
        price = _coerce_limit_price(limit_price)
        response = self._authenticated_get(
            KIS_BUYING_POWER_ENDPOINT,
            KIS_BUYING_POWER_TR_ID,
            params={
                "CANO": self._config.account_number,
                "ACNT_PRDT_CD": self._config.product_code,
                "PDNO": normalized_symbol,
                "ORD_UNPR": format(price, "f"),
                "ORD_DVSN": "01",
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
            },
        )
        _assert_business_success(response, "buying-power request")
        output = _required_mapping(response.payload, "output", "buying-power response")
        result = KisBuyingPower(
            symbol=normalized_symbol,
            limit_price=price,
            orderable_cash=_required_decimal(
                output,
                "ord_psbl_cash",
                "buying-power output",
            ),
            no_receivable_buy_amount=_required_decimal(
                output,
                "nrcvb_buy_amt",
                "buying-power output",
            ),
            no_receivable_buy_quantity=_required_int(
                output,
                "nrcvb_buy_qty",
                "buying-power output",
                minimum=0,
            ),
            maximum_buy_amount=_required_decimal(
                output,
                "max_buy_amt",
                "buying-power output",
            ),
            maximum_buy_quantity=_required_int(
                output,
                "max_buy_qty",
                "buying-power output",
                minimum=0,
            ),
            calculation_price=_required_decimal(
                output,
                "psbl_qty_calc_unpr",
                "buying-power output",
            ),
        )
        if min(
            result.orderable_cash,
            result.no_receivable_buy_amount,
            result.maximum_buy_amount,
            result.calculation_price,
        ) < 0:
            raise KisPaperProtocolError(
                "KIS paper buying-power output contains a negative amount"
            )
        return result

    def place_limit_cash_order(
        self,
        *,
        symbol: str,
        side: Literal["buy", "sell"],
        quantity: int,
        limit_price: Decimal,
        exchange: str = "KRX",
    ) -> KisCashOrderResult:
        normalized_symbol = _validate_market_request(symbol, exchange)
        if side not in {"buy", "sell"}:
            raise KisPaperConfigurationError("cash-order side must be buy or sell")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise KisPaperConfigurationError("cash-order quantity must be a positive integer")
        price = _coerce_limit_price(limit_price)
        tr_id = KIS_CASH_BUY_TR_ID if side == "buy" else KIS_CASH_SELL_TR_ID
        body = {
            "CANO": self._config.account_number,
            "ACNT_PRDT_CD": self._config.product_code,
            "PDNO": normalized_symbol,
            "ORD_DVSN": "00",
            "ORD_QTY": str(quantity),
            "ORD_UNPR": format(price, "f"),
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01" if side == "sell" else "",
            "CNDT_PRIC": "",
        }
        try:
            response = self._request(
                "POST",
                KIS_CASH_ORDER_ENDPOINT,
                headers=self._auth_headers(tr_id),
                body=body,
            )
            _ensure_http_success(response, "cash-order request")
            _assert_business_success(response, "cash-order request")
            output = _required_mapping(response.payload, "output", "cash-order response")
            order_number = _required_text(output, "ODNO", "cash-order output")
            forwarding_number = _required_text(
                output, "KRX_FWDG_ORD_ORGNO", "cash-order output"
            )
            order_time = _required_text(output, "ORD_TMD", "cash-order output")
            if not re.fullmatch(r"\d{6}", order_time):
                raise KisPaperProtocolError("cash-order output has an invalid order time")
            message_code = _required_safe_code(response.payload, "msg_cd", "cash-order response")
        except KisPaperBusinessError:
            raise
        except KisPaperOrderOutcomeUnknown:
            raise
        except (
            KisPaperTransportError,
            KisPaperProtocolError,
            http.client.HTTPException,
            TimeoutError,
            ConnectionError,
            OSError,
        ):
            raise KisPaperOrderOutcomeUnknown(
                "KIS paper cash-order outcome is unknown; reconcile before any retry"
            ) from None
        return KisCashOrderResult(
            symbol=normalized_symbol,
            side=side,
            quantity=quantity,
            limit_price=price,
            order_number=order_number,
            krx_forwarding_order_org_number=forwarding_number,
            order_time=order_time,
            message_code=message_code,
            transaction_id=tr_id,
        )

    def _paginated_get(
        self,
        endpoint: str,
        tr_id: str,
        initial_params: Mapping[str, str],
    ) -> tuple[KisHttpResponse, ...]:
        params = dict(initial_params)
        continuation_request = False
        seen_cursors: set[tuple[str, str]] = set()
        responses: list[KisHttpResponse] = []
        for _ in range(_MAX_PAGES):
            extra_headers = {"tr_cont": "N"} if continuation_request else None
            response = self._authenticated_get(
                endpoint,
                tr_id,
                params=params,
                extra_headers=extra_headers,
            )
            _assert_business_success(response, f"{tr_id} request")
            responses.append(response)
            continuation = response.headers.get("tr_cont", "").strip().upper()
            if continuation in {"", "D", "E"}:
                return tuple(responses)
            if continuation not in {"F", "M"}:
                raise KisPaperProtocolError("KIS pagination returned an unknown continuation state")
            foreign_key = _required_text(
                response.payload, "ctx_area_fk100", "paginated response"
            )
            next_key = _required_text(response.payload, "ctx_area_nk100", "paginated response")
            cursor = (foreign_key, next_key)
            if cursor in seen_cursors:
                raise KisPaperProtocolError("KIS pagination repeated a continuation cursor")
            seen_cursors.add(cursor)
            params["CTX_AREA_FK100"] = foreign_key
            params["CTX_AREA_NK100"] = next_key
            continuation_request = True
        raise KisPaperProtocolError("KIS pagination exceeded the page safety limit")

    def _authenticated_get(
        self,
        endpoint: str,
        tr_id: str,
        *,
        params: Mapping[str, str],
        extra_headers: Mapping[str, str] | None = None,
    ) -> KisHttpResponse:
        headers = self._auth_headers(tr_id)
        if extra_headers:
            headers.update(extra_headers)
        response = self._request("GET", endpoint, headers=headers, params=params)
        _ensure_http_success(response, f"{tr_id} request")
        return response

    def _auth_headers(self, tr_id: str) -> dict[str, str]:
        if not self._config.access_token.strip():
            raise KisPaperConfigurationError("KIS paper access token is required")
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._config.access_token}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": tr_id,
            "custtype": self._config.customer_type,
        }

    def _request(
        self,
        method: Literal["GET", "POST"],
        endpoint: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> KisHttpResponse:
        if endpoint not in _ALLOWED_ENDPOINTS:
            raise KisPaperConfigurationError("KIS paper endpoint is not allowlisted")
        return self._transport.request_json(
            method,
            f"{self._config.base_url}{endpoint}",
            headers=headers,
            params=params,
            body=body,
            timeout_seconds=self._config.timeout_seconds,
        )


def _validate_base_url(base_url: str) -> None:
    if base_url != KIS_PAPER_BASE_URL:
        raise KisPaperConfigurationError("KIS paper base URL must match the approved paper origin")
    parsed = urllib.parse.urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _PAPER_HOST
        or parsed.port != _PAPER_PORT
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise KisPaperConfigurationError("KIS paper base URL is invalid")


def _validate_request_url(url: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise KisPaperConfigurationError("KIS paper request URL is invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != _PAPER_HOST
        or port != _PAPER_PORT
        or parsed.netloc != f"{_PAPER_HOST}:{_PAPER_PORT}"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in _ALLOWED_ENDPOINTS
        or parsed.query
        or parsed.fragment
    ):
        raise KisPaperConfigurationError("KIS paper request URL is outside the approved boundary")


def _validate_exchange(exchange: str) -> None:
    if exchange != "KRX":
        raise KisPaperConfigurationError("KIS paper domestic trading supports KRX only")


def _validate_market_request(symbol: str, exchange: str) -> str:
    _validate_exchange(exchange)
    if not isinstance(symbol, str):
        raise KisPaperConfigurationError("KIS paper symbol must be a string")
    normalized = symbol.strip().upper()
    if not _SYMBOL.fullmatch(normalized):
        raise KisPaperConfigurationError("KIS paper symbol must be six alphanumeric characters")
    return normalized


def _ensure_http_success(response: KisHttpResponse, operation: str) -> None:
    if not 200 <= response.status_code < 300:
        raise KisPaperTransportError(f"KIS paper {operation} returned a non-success HTTP status")


def _assert_business_success(response: KisHttpResponse, operation: str) -> None:
    _ensure_http_success(response, operation)
    if "rt_cd" not in response.payload:
        raise KisPaperProtocolError(f"KIS paper {operation} omitted the business result code")
    result_code = str(response.payload["rt_cd"]).strip()
    if result_code != "0":
        code = _safe_response_code(response.payload.get("msg_cd"))
        raise KisPaperBusinessError(f"KIS paper {operation} was rejected (code={code})")


def _safe_response_code(value: Any) -> str:
    code = str(value or "").strip()
    return code if _SAFE_CODE.fullmatch(code) else "unavailable"


def _required_safe_code(source: Mapping[str, Any], key: str, context: str) -> str:
    value = source.get(key)
    code = _safe_response_code(value)
    if code == "unavailable":
        raise KisPaperProtocolError(f"KIS paper {context} has an invalid {key} field")
    return code


def _required_mapping(source: Mapping[str, Any], key: str, context: str) -> Mapping[str, Any]:
    value = source.get(key)
    if not isinstance(value, Mapping):
        raise KisPaperProtocolError(f"KIS paper {context} has an invalid {key} object")
    return value


def _required_list(source: Mapping[str, Any], key: str, context: str) -> list[Any]:
    value = source.get(key)
    if not isinstance(value, list):
        raise KisPaperProtocolError(f"KIS paper {context} has an invalid {key} list")
    return value


def _required_text(source: Mapping[str, Any], key: str, context: str) -> str:
    if key not in source or not isinstance(source[key], str):
        raise KisPaperProtocolError(f"KIS paper {context} has an invalid {key} field")
    value = str(source[key]).strip()
    if not value:
        raise KisPaperProtocolError(f"KIS paper {context} has an empty {key} field")
    return value


def _required_decimal(source: Mapping[str, Any], key: str, context: str) -> Decimal:
    if key not in source or isinstance(source[key], bool):
        raise KisPaperProtocolError(f"KIS paper {context} has an invalid {key} field")
    try:
        value = Decimal(str(source[key]).strip())
    except (InvalidOperation, ValueError):
        raise KisPaperProtocolError(f"KIS paper {context} has an invalid {key} number") from None
    if not value.is_finite():
        raise KisPaperProtocolError(f"KIS paper {context} has a non-finite {key} number")
    return value


def _required_int(
    source: Mapping[str, Any],
    key: str,
    context: str,
    *,
    minimum: int | None = None,
) -> int:
    value = _required_decimal(source, key, context)
    if value != value.to_integral_value():
        raise KisPaperProtocolError(f"KIS paper {context} has a non-integer {key} field")
    parsed = int(value)
    if minimum is not None and parsed < minimum:
        raise KisPaperProtocolError(f"KIS paper {context} has an out-of-range {key} field")
    return parsed


def _parse_balance_position(raw: Any) -> KisBalancePosition:
    if not isinstance(raw, Mapping):
        raise KisPaperProtocolError("KIS paper balance position must be an object")
    symbol = _required_text(raw, "pdno", "balance position").upper()
    if not _SYMBOL.fullmatch(symbol):
        raise KisPaperProtocolError("KIS paper balance position has an invalid symbol")
    return KisBalancePosition(
        symbol=symbol,
        product_name=_required_text(raw, "prdt_name", "balance position"),
        holding_quantity=_required_int(raw, "hldg_qty", "balance position", minimum=0),
        orderable_quantity=_required_int(raw, "ord_psbl_qty", "balance position", minimum=0),
        purchase_average_price=_required_decimal(raw, "pchs_avg_pric", "balance position"),
        current_price=_required_decimal(raw, "prpr", "balance position"),
        purchase_amount=_required_decimal(raw, "pchs_amt", "balance position"),
        evaluation_amount=_required_decimal(raw, "evlu_amt", "balance position"),
    )


def _parse_balance_summary(raw: Mapping[str, Any]) -> KisBalanceSummary:
    return KisBalanceSummary(
        deposit_amount=_required_decimal(raw, "dnca_tot_amt", "balance summary"),
        next_day_settlement_amount=_required_decimal(
            raw, "nxdy_excc_amt", "balance summary"
        ),
        total_purchase_amount=_required_decimal(raw, "pchs_amt_smtl_amt", "balance summary"),
        total_evaluation_amount=_required_decimal(raw, "tot_evlu_amt", "balance summary"),
        net_asset_amount=_required_decimal(raw, "nass_amt", "balance summary"),
        evaluation_profit_loss=_required_decimal(
            raw, "evlu_pfls_smtl_amt", "balance summary"
        ),
    )


def _parse_daily_order(raw: Any) -> KisDailyOrderFill:
    if not isinstance(raw, Mapping):
        raise KisPaperProtocolError("KIS paper daily-order row must be an object")
    side_code = _required_text(raw, "sll_buy_dvsn_cd", "daily-order row")
    if side_code not in {"01", "02"}:
        raise KisPaperProtocolError("KIS paper daily-order row has an unknown side")
    symbol = _required_text(raw, "pdno", "daily-order row").upper()
    if not _SYMBOL.fullmatch(symbol):
        raise KisPaperProtocolError("KIS paper daily-order row has an invalid symbol")
    order_date = _required_text(raw, "ord_dt", "daily-order row")
    order_time = _required_text(raw, "ord_tmd", "daily-order row")
    if not re.fullmatch(r"\d{8}", order_date) or not re.fullmatch(r"\d{6}", order_time):
        raise KisPaperProtocolError("KIS paper daily-order row has an invalid date or time")
    cancel_flag = _required_text(raw, "cncl_yn", "daily-order row").upper()
    if cancel_flag not in {"Y", "N"}:
        raise KisPaperProtocolError("KIS paper daily-order row has an invalid cancel flag")
    raw_original_order_number = raw.get("orgn_odno", "")
    if not isinstance(raw_original_order_number, str):
        raise KisPaperProtocolError(
            "KIS paper daily-order row has an invalid orgn_odno field"
        )
    original_order_number = raw_original_order_number.strip()
    return KisDailyOrderFill(
        order_number=_required_text(raw, "odno", "daily-order row"),
        original_order_number=original_order_number,
        order_branch_number=_required_text(
            raw,
            "ord_gno_brno",
            "daily-order row",
        ),
        order_date=order_date,
        order_time=order_time,
        symbol=symbol,
        product_name=_required_text(raw, "prdt_name", "daily-order row"),
        side="sell" if side_code == "01" else "buy",
        order_quantity=_required_int(raw, "ord_qty", "daily-order row", minimum=0),
        order_price=_required_decimal(raw, "ord_unpr", "daily-order row"),
        total_filled_quantity=_required_int(
            raw, "tot_ccld_qty", "daily-order row", minimum=0
        ),
        average_fill_price=_required_decimal(raw, "avg_prvs", "daily-order row"),
        remaining_quantity=_required_int(raw, "rmn_qty", "daily-order row", minimum=0),
        rejected_quantity=_required_int(raw, "rjct_qty", "daily-order row", minimum=0),
        cancelled=cancel_flag == "Y",
        confirmed_cancel_quantity=_required_int(
            raw, "cnc_cfrm_qty", "daily-order row", minimum=0
        ),
        total_filled_amount=_required_decimal(raw, "tot_ccld_amt", "daily-order row"),
    )


def _coerce_limit_price(value: Decimal) -> Decimal:
    if isinstance(value, bool):
        raise KisPaperConfigurationError("cash-order limit price must be a Decimal")
    try:
        price = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise KisPaperConfigurationError("cash-order limit price is invalid") from None
    if not price.is_finite() or price <= 0 or price != price.to_integral_value():
        raise KisPaperConfigurationError(
            "cash-order limit price must be a positive whole-won amount"
        )
    return price
