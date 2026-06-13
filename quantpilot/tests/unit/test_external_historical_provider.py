from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

from quantpilot.packages.core.backtest import BacktestAssumptions, BacktestRequest, BacktestSignal, run_backtest
from quantpilot.packages.core.data.external import (
    DataQualityIssue,
    ExternalHistoricalMarketDataProvider,
    ExternalHistoricalSecurityProvider,
    HistoricalDataRequest,
    HistoricalDataResponse,
)
from quantpilot.packages.core.data.kis_historical import (
    KIS_DOMESTIC_DAILY_TR_ID,
    KisHistoricalMarketDataProvider,
    KisOpenApiConfig,
    KisOpenApiHistoricalClient,
)
from quantpilot.packages.core.data.providers import ProviderError, build_providers
from quantpilot.packages.core.schemas import DataMode, SignalAction


FETCHED_AT = datetime(2026, 1, 4, 9, 0, tzinfo=timezone.utc)


class FakeHistoricalClient:
    def __init__(
        self,
        payloads: list[dict[str, Any]],
        *,
        provider_name: str = "fake_external",
        quality_issues: list[DataQualityIssue | dict[str, Any]] | None = None,
        retry_count: int = 0,
    ) -> None:
        self.payloads = payloads
        self.provider_name = provider_name
        self.quality_issues = quality_issues or []
        self.retry_count = retry_count
        self.requests: list[HistoricalDataRequest] = []

    def fetch_daily_bars(self, request: HistoricalDataRequest) -> HistoricalDataResponse:
        self.requests.append(request)
        return HistoricalDataResponse(
            payloads=[dict(row) for row in self.payloads],
            provider_name=self.provider_name,
            fetched_at=FETCHED_AT,
            market=request.market,
            adjusted=request.adjusted,
            rate_limit={"remaining": 99, "reset_at": "2026-01-04T09:01:00+00:00"},
            retry_count=self.retry_count,
            quality_issues=list(self.quality_issues),
        )


def _rows(symbols: tuple[str, ...] = ("AAA",), days: int = 4) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        for index in range(days):
            close = 100.0 + index
            rows.append(
                {
                    "ticker": symbol,
                    "session_date": date(2026, 1, index + 1).isoformat(),
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 100_000 + index,
                }
            )
    return rows


def _provider(payloads: list[dict[str, Any]] | None = None) -> ExternalHistoricalMarketDataProvider:
    return ExternalHistoricalMarketDataProvider(
        FakeHistoricalClient(payloads or _rows()),
        symbols=["AAA"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 4),
        market="KR_STOCK",
        adjusted=False,
    )


def test_fake_client_payload_maps_to_internal_price_history_and_snapshot_bars() -> None:
    client = FakeHistoricalClient(_rows(("AAA", "BBB"), days=3))

    provider = ExternalHistoricalMarketDataProvider(
        client,
        symbols=["aaa", "bbb"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        market="KR_STOCK",
        adjusted=True,
    )

    history = provider.get_price_history()
    assert client.requests[0].symbols == ("AAA", "BBB")
    assert {row["symbol"] for row in history} == {"AAA", "BBB"}
    assert history[0]["date"] == "2026-01-01"
    assert set(history[0]) == {"symbol", "ticker", "date", "open", "high", "low", "close", "volume"}

    bars = provider.get_bars()
    assert {bar["symbol"] for bar in bars} == {"AAA", "BBB"}
    assert {"ma20", "rsi", "volume_ratio", "position_weight"}.issubset(bars[0])


def test_external_provider_unit_tests_do_not_require_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    provider = _provider()

    assert provider.get_price_history()[0]["symbol"] == "AAA"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({**_rows()[0], "close": ""}, "missing required field 'close'"),
        ({**_rows()[0], "close": "not-a-number"}, "not a number"),
        ({**_rows()[0], "high": 98.0, "low": 101.0}, "invalid_ohlc|high .* low"),
    ],
)
def test_missing_or_malformed_provider_payload_fails_closed(payload: dict[str, Any], message: str) -> None:
    with pytest.raises(ProviderError, match=message):
        _provider([payload])


def test_provider_reports_data_provenance_and_quality_issues() -> None:
    client = FakeHistoricalClient(
        _rows(days=5),
        quality_issues=[
            DataQualityIssue(code="market_holiday", message="exchange closed", session_date=date(2026, 1, 4)),
            {"code": "rate_limited", "message": "client backed off", "severity": "info"},
        ],
        retry_count=1,
    )
    provider = ExternalHistoricalMarketDataProvider(
        client,
        symbols=["AAA"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
        market="KR_STOCK",
    )

    provenance = provider.get_provenance()
    assert provenance["provider_name"] == "fake_external"
    assert provenance["data_mode"] == "external_historical"
    assert provenance["fetched_at"] == FETCHED_AT.isoformat()
    assert provenance["market"] == "KR_STOCK"
    assert provenance["adjusted"] is False
    assert provenance["rate_limit"]["remaining"] == 99
    assert provenance["retry_count"] == 1
    assert {issue["code"] for issue in provenance["quality_issues"]} >= {
        "market_holiday",
        "rate_limited",
    }
    assert provenance["quality"]["status"] == "warning"
    assert provenance["quality"]["has_blocking_issues"] is False

    provenance["quality_issues"].clear()
    assert provider.get_provenance()["quality_issues"]


def test_backtest_consumes_external_provider_through_market_data_boundary() -> None:
    provider = _provider()
    request = BacktestRequest(
        strategy_id="external_fake_strategy",
        recipe_version="1.0",
        initial_cash=10_000,
        assumptions=BacktestAssumptions(sell_tax_bps=10.0),
        signals=[
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 1, 1),
                action=SignalAction.buy_ready,
                target_weight_hint=0.5,
                reason="fake external buy",
            )
        ],
    )

    result = run_backtest(request, provider)

    assert result.metrics.filled_trades == 1
    assert result.input_summary["data_boundary"] == "MarketDataProvider.get_price_history"
    assert result.input_summary["data_provenance"]["provider_name"] == "fake_external"
    assert result.input_summary["data_quality"]["status"] == "passed"
    assert result.research_only is True
    assert result.live_trading_approval is False


def test_external_historical_factory_accepts_explicit_injected_providers() -> None:
    security_provider = ExternalHistoricalSecurityProvider(["AAA"], market="KR_STOCK")
    market_data_provider = _provider()

    built_security_provider, built_market_data_provider = build_providers(
        DataMode.external_historical,
        external_security_provider=security_provider,
        external_market_data_provider=market_data_provider,
    )

    assert built_security_provider is security_provider
    assert built_market_data_provider is market_data_provider


def test_kis_provider_maps_kis_daily_payload_aliases_with_fake_client() -> None:
    class FakeKisClient:
        def fetch_daily_bars(self, request: HistoricalDataRequest) -> HistoricalDataResponse:
            return HistoricalDataResponse(
                payloads=[
                    {
                        "symbol": "005930",
                        "stck_bsop_date": "20260101",
                        "stck_oprc": "70000",
                        "stck_hgpr": "71000",
                        "stck_lwpr": "69000",
                        "stck_clpr": "70500",
                        "acml_vol": "1234567",
                    }
                ],
                provider_name="kis_open_api",
                fetched_at=FETCHED_AT,
                market=request.market,
                adjusted=request.adjusted,
            )

    provider = KisHistoricalMarketDataProvider(
        FakeKisClient(),  # type: ignore[arg-type]
        symbols=["005930"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
    )

    assert provider.get_price_history() == [
        {
            "symbol": "005930",
            "ticker": "005930",
            "date": "2026-01-01",
            "open": 70000.0,
            "high": 71000.0,
            "low": 69000.0,
            "close": 70500.0,
            "volume": 1234567,
        }
    ]


def test_kis_client_uses_injected_transport_without_network() -> None:
    class RecordingTransport:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def get_json(
            self,
            url: str,
            *,
            headers: dict[str, str],
            params: dict[str, str],
            timeout_seconds: float,
        ) -> dict[str, Any]:
            self.calls.append(
                {
                    "url": url,
                    "headers": dict(headers),
                    "params": dict(params),
                    "timeout_seconds": timeout_seconds,
                }
            )
            return {
                "rt_cd": "0",
                "output2": [
                    {
                        "stck_bsop_date": "20260101",
                        "stck_oprc": "70000",
                        "stck_hgpr": "71000",
                        "stck_lwpr": "69000",
                        "stck_clpr": "70500",
                        "acml_vol": "1234567",
                    }
                ],
            }

    transport = RecordingTransport()
    client = KisOpenApiHistoricalClient(
        KisOpenApiConfig(
            app_key="fake-app-key",
            app_secret="fake-app-secret",
            access_token="fake-token",
            base_url="https://kis.example.test",
            retry_backoff_seconds=0.0,
        ),
        transport=transport,
    )

    response = client.fetch_daily_bars(
        HistoricalDataRequest(
            symbols=("005930",),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 1),
            market="KR_STOCK",
            adjusted=False,
        )
    )

    assert response.payloads[0]["symbol"] == "005930"
    assert transport.calls[0]["headers"]["tr_id"] == KIS_DOMESTIC_DAILY_TR_ID
    assert transport.calls[0]["params"]["FID_INPUT_ISCD"] == "005930"
    assert "CANO" not in transport.calls[0]["params"]
