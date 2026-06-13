from __future__ import annotations

import os

import pytest

from quantpilot.packages.core.data.kis_historical import KisHistoricalMarketDataProvider, build_kis_historical_providers_from_env


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KIS_MANUAL_INTEGRATION") != "1",
    reason="manual KIS historical market-data check; set RUN_KIS_MANUAL_INTEGRATION=1 and explicit KIS env vars",
)


def test_kis_historical_provider_manual_fetch_from_env() -> None:
    _security_provider, market_data_provider = build_kis_historical_providers_from_env()

    assert isinstance(market_data_provider, KisHistoricalMarketDataProvider)
    history = market_data_provider.get_price_history()
    provenance = market_data_provider.get_provenance()

    assert history
    assert provenance["provider_name"] == "kis_open_api"
    assert provenance["data_mode"] == "external_historical"
