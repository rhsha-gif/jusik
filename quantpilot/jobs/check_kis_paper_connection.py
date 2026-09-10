"""Authenticate and query KIS domestic-stock paper trading without submitting orders."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping

from quantpilot.packages.core.kis_paper import (
    KIS_BALANCE_ENDPOINT,
    KIS_CURRENT_PRICE_ENDPOINT,
    KIS_PAPER_BASE_URL,
    KIS_TOKEN_ENDPOINT,
    KisPaperClient,
    KisPaperBusinessError,
    KisPaperConfig,
    KisPaperConfigurationError,
    StrictUrllibKisPaperTransport,
)


class ConnectionTransport(StrictUrllibKisPaperTransport):
    """Limit this command to authentication and the two read-only probes."""

    def request_json(self, method, url, **kwargs):
        allowed = {
            ("POST", KIS_PAPER_BASE_URL + KIS_TOKEN_ENDPOINT),
            ("GET", KIS_PAPER_BASE_URL + KIS_CURRENT_PRICE_ENDPOINT),
            ("GET", KIS_PAPER_BASE_URL + KIS_BALANCE_ENDPOINT),
        }
        if (method, url) not in allowed:
            raise KisPaperConfigurationError("connection probe endpoint blocked")
        return super().request_json(method, url, **kwargs)


def connection_config(environment: Mapping[str, str]) -> KisPaperConfig:
    account = environment.get("KIS_PAPER_ACCOUNT_NUMBER", "").strip()
    match = re.fullmatch(r"([0-9]{8})(?:-?([0-9]{2}))?", account)
    if match is None:
        raise KisPaperConfigurationError("paper account format invalid")
    explicit_product = environment.get("KIS_PAPER_PRODUCT_CODE", "").strip()
    suffix = match.group(2)
    if suffix and explicit_product and suffix != explicit_product:
        raise KisPaperConfigurationError("paper account product conflict")
    return KisPaperConfig(
        app_key=environment.get("KIS_PAPER_APP_KEY", ""),
        app_secret=environment.get("KIS_PAPER_APP_SECRET", ""),
        account_number=match.group(1),
        product_code=explicit_product or suffix or "01",
        access_token=environment.get("KIS_PAPER_ACCESS_TOKEN", ""),
    )


def check_connection(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    result = {"data_mode": "paper_trading", "status": "failed", "stage": "configuration"}
    try:
        config = connection_config(os.environ if environment is None else environment)
        client = KisPaperClient(config, transport=ConnectionTransport())
        result["stage"] = "authentication"
        if not config.access_token:
            token = client.request_access_token()
            config = config.with_access_token(token.access_token)
            client = KisPaperClient(config, transport=ConnectionTransport())
        result["stage"] = "price_query"
        client.get_current_price("005930")
        result["price_query"] = "passed"
        result["stage"] = "balance_query"
        client.get_balance()
        result.update(status="connected", stage="complete", balance_query="passed")
    except Exception as exc:
        # Never serialize provider responses, credentials, accounts or exception text.
        result["error_type"] = type(exc).__name__
        if isinstance(exc, KisPaperBusinessError) and "(code=90070000)" in str(exc):
            result["reason"] = "paper_account_user_mismatch"
            result["broker_code"] = "90070000"
    return result


def main() -> int:
    result = check_connection()
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["status"] == "connected" else 1


if __name__ == "__main__":
    raise SystemExit(main())
