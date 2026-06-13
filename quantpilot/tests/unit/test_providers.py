from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from quantpilot.packages.core.data.providers import (
    CsvMarketDataProvider,
    CsvSecurityProvider,
    FixtureMarketDataProvider,
    FixtureSecurityProvider,
    MarketDataProvider,
    ProviderError,
    SecurityProvider,
    build_providers,
    build_providers_from_env,
)
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import DataMode, SignalAction
from quantpilot.packages.core.universe.builder import FIXTURE_SECURITIES
from quantpilot.packages.db.repositories import RepositoryRegistry

LOCAL_DATA_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "local_data"
INVALID_DATA_DIR = LOCAL_DATA_DIR / "invalid"


# --- Protocol structural satisfaction ---------------------------------------


def test_fixture_providers_satisfy_protocols() -> None:
    assert isinstance(FixtureSecurityProvider(), SecurityProvider)
    assert isinstance(FixtureMarketDataProvider(), MarketDataProvider)


def test_csv_providers_satisfy_protocols() -> None:
    assert isinstance(CsvSecurityProvider(LOCAL_DATA_DIR / "securities.csv"), SecurityProvider)
    assert isinstance(CsvMarketDataProvider(LOCAL_DATA_DIR / "ohlcv.csv"), MarketDataProvider)


# --- Fixture providers remain unchanged -------------------------------------


def test_fixture_security_provider_returns_all_securities() -> None:
    securities = FixtureSecurityProvider().get_securities()
    assert {s["ticker"] for s in securities} == {s["ticker"] for s in FIXTURE_SECURITIES}


def test_fixture_security_provider_returns_a_copy() -> None:
    result = FixtureSecurityProvider().get_securities()
    result.clear()
    assert len(FIXTURE_SECURITIES) == 7


def test_fixture_market_data_provider_returns_seven_bars() -> None:
    bars = FixtureMarketDataProvider().get_bars()
    assert {bar["symbol"] for bar in bars} == {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"}


def test_fixture_market_data_provider_returns_copies_not_cached_refs() -> None:
    provider = FixtureMarketDataProvider()
    provider.get_bars().clear()
    provider.get_price_history().clear()
    assert len(provider.get_bars()) == 7
    assert len(provider.get_price_history()) > 0


# --- CSV security provider ---------------------------------------------------


def test_csv_security_provider_loads_local_securities() -> None:
    securities = CsvSecurityProvider(LOCAL_DATA_DIR / "securities.csv").get_securities()
    assert {s["ticker"] for s in securities} == {"AAA", "BBB"}
    aaa = next(s for s in securities if s["ticker"] == "AAA")
    assert aaa["themes"] == ["ai", "semiconductor"]  # pipe-delimited -> list
    assert aaa["avg_daily_value"] == 10_500_000.0  # coerced to float
    assert aaa["data_ready"] is True
    assert aaa["market"] == "US_STOCK"


def test_csv_security_provider_copy_does_not_mutate_cache() -> None:
    provider = CsvSecurityProvider(LOCAL_DATA_DIR / "securities.csv")
    first = provider.get_securities()
    first[0]["themes"].append("mutated")
    assert "mutated" not in provider.get_securities()[0]["themes"]


# --- CSV market data provider ------------------------------------------------


def test_csv_market_data_provider_loads_price_history() -> None:
    history = CsvMarketDataProvider(LOCAL_DATA_DIR / "ohlcv.csv").get_price_history()
    assert len(history) == 16  # 8 days x 2 symbols
    assert {row["symbol"] for row in history} == {"AAA", "BBB"}
    first = history[0]
    assert set(first) == {"symbol", "ticker", "date", "open", "high", "low", "close", "volume"}
    assert first["date"] == "2026-06-01"  # normalized ISO session date


def test_csv_market_data_provider_derives_one_snapshot_bar_per_symbol() -> None:
    bars = CsvMarketDataProvider(LOCAL_DATA_DIR / "ohlcv.csv").get_bars()
    assert {bar["symbol"] for bar in bars} == {"AAA", "BBB"}
    for bar in bars:
        assert {"close", "ma20", "rsi", "volume_ratio", "position_weight"}.issubset(bar)
    aaa = next(bar for bar in bars if bar["symbol"] == "AAA")
    assert aaa["close"] == 107.0  # latest close in the series


def test_csv_market_data_provider_is_deterministic() -> None:
    provider = CsvMarketDataProvider(LOCAL_DATA_DIR / "ohlcv.csv")
    assert provider.get_bars() == provider.get_bars()
    assert provider.get_price_history() == provider.get_price_history()


# --- Schema validation / clear errors ---------------------------------------


def test_missing_required_column_raises_clear_error() -> None:
    with pytest.raises(ProviderError, match="missing required column"):
        CsvMarketDataProvider(LOCAL_DATA_DIR / "invalid_ohlcv.csv")


def test_missing_file_raises_clear_error() -> None:
    with pytest.raises(ProviderError, match="not found"):
        CsvMarketDataProvider(INVALID_DATA_DIR / "no_such_file.csv")


def test_non_numeric_value_raises_clear_error() -> None:
    with pytest.raises(ProviderError, match="not a number"):
        CsvMarketDataProvider(INVALID_DATA_DIR / "bad_number.csv")


def test_duplicate_bar_raises_clear_error() -> None:
    with pytest.raises(ProviderError, match="duplicate bar"):
        CsvMarketDataProvider(INVALID_DATA_DIR / "duplicate_bar.csv")


def test_bad_date_raises_clear_error() -> None:
    with pytest.raises(ProviderError, match="invalid date"):
        CsvMarketDataProvider(INVALID_DATA_DIR / "bad_date.csv")


def test_timezone_date_is_rejected() -> None:
    with pytest.raises(ProviderError, match="plain session date"):
        CsvMarketDataProvider(INVALID_DATA_DIR / "tz_date.csv")


def test_empty_symbol_raises_clear_error() -> None:
    with pytest.raises(ProviderError, match="empty symbol"):
        CsvMarketDataProvider(INVALID_DATA_DIR / "empty_symbol.csv")


def test_duplicate_security_symbol_raises_clear_error() -> None:
    with pytest.raises(ProviderError, match="duplicate symbol"):
        CsvSecurityProvider(INVALID_DATA_DIR / "duplicate_symbol_securities.csv")


# --- Factory: fixtures stay the default -------------------------------------


def test_factory_returns_fixture_providers_by_default() -> None:
    sp, mp = build_providers(DataMode.fixture)
    assert isinstance(sp, FixtureSecurityProvider)
    assert isinstance(mp, FixtureMarketDataProvider)


def test_factory_local_historical_requires_data_dir() -> None:
    with pytest.raises(ProviderError, match="requires a data directory"):
        build_providers(DataMode.local_historical, data_dir=None)


def test_factory_local_historical_builds_csv_providers() -> None:
    sp, mp = build_providers(DataMode.local_historical, data_dir=LOCAL_DATA_DIR)
    assert isinstance(sp, CsvSecurityProvider)
    assert isinstance(mp, CsvMarketDataProvider)


def test_factory_rejects_security_without_ohlcv_rows(tmp_path: Path) -> None:
    (tmp_path / "securities.csv").write_text(
        "\n".join(
            [
                "symbol,name,market,sector,themes,avg_daily_value,data_ready",
                "AAA,Alpha,US_STOCK,technology,ai,1000000,true",
                "ZZZ,Missing History,US_STOCK,technology,ai,1000000,true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "ohlcv.csv").write_text(
        "\n".join(
            [
                "symbol,date,open,high,low,close,volume",
                "AAA,2026-06-01,99.5,100.5,99.0,100.0,100000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProviderError, match="missing OHLCV rows.*ZZZ"):
        build_providers(DataMode.local_historical, data_dir=tmp_path)


def test_factory_future_data_modes_fail_closed() -> None:
    future_modes = [
        DataMode.realtime_market_data,
        DataMode.paper_trading,
        DataMode.live_trading,
    ]
    for mode in future_modes:
        with pytest.raises(ProviderError, match="not implemented"):
            build_providers(mode)


def test_factory_external_historical_requires_injection_or_provider_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXTERNAL_HISTORICAL_PROVIDER", raising=False)

    with pytest.raises(ProviderError, match="requires explicit provider injection"):
        build_providers(DataMode.external_historical)


def test_factory_from_env_builds_local_historical_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_MODE", "local_historical")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(LOCAL_DATA_DIR))

    sp, mp = build_providers_from_env()

    assert isinstance(sp, CsvSecurityProvider)
    assert isinstance(mp, CsvMarketDataProvider)


# --- HarnessService default construction & injection ------------------------


def test_harness_service_defaults_to_fixture_providers() -> None:
    service = HarnessService()
    assert isinstance(service.security_provider, FixtureSecurityProvider)
    assert isinstance(service.market_data_provider, FixtureMarketDataProvider)


def test_harness_service_positional_repositories_still_works() -> None:
    repos = RepositoryRegistry()
    service = HarnessService(repos)
    assert service.repositories is repos
    assert isinstance(service.security_provider, SecurityProvider)


def test_harness_service_from_environment_uses_local_historical_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_MODE", "local_historical")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(LOCAL_DATA_DIR))

    service = HarnessService.from_environment()

    assert isinstance(service.security_provider, CsvSecurityProvider)
    assert isinstance(service.market_data_provider, CsvMarketDataProvider)


def test_custom_security_provider_replaces_universe() -> None:
    class TwoSecurityProvider:
        def get_securities(self) -> list[dict[str, Any]]:
            return [
                {"ticker": "AAA", "name": "Alpha", "market": "US_STOCK", "sector": "technology",
                 "themes": ["ai"], "avg_daily_value": 10_000_000, "data_ready": True},
                {"ticker": "BBB", "name": "Beta", "market": "US_STOCK", "sector": "technology",
                 "themes": ["ai"], "avg_daily_value": 10_000_000, "data_ready": True},
            ]

    service = HarnessService(security_provider=TwoSecurityProvider())
    policy = service.parse_policy()
    result = service.run_level_1_2(policy_id=policy.policy_id)
    assert {item.ticker for item in result["universe"]} == {"AAA", "BBB"}


def test_custom_market_data_provider_replaces_bars() -> None:
    class SingleBlockedBarProvider:
        def get_bars(self) -> list[dict[str, Any]]:
            return [{
                "symbol": "ZZZ", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                "volume": 10000, "ma20": 100.0, "rsi": 50.0, "volume_ratio": 1.0,
                "position_weight": 0.0, "blocked": True,
            }]

        def get_price_history(self) -> list[dict[str, Any]]:
            return []

    service = HarnessService(market_data_provider=SingleBlockedBarProvider())
    signals = service.run_signals()
    assert len(signals) == 1
    assert signals[0].symbol == "ZZZ"
    assert signals[0].action == SignalAction.blocked


# --- Local historical end-to-end: signals from CSV, deterministic -----------


def test_local_csv_providers_generate_expected_signals() -> None:
    sp, mp = build_providers(DataMode.local_historical, data_dir=LOCAL_DATA_DIR)
    service = HarnessService(security_provider=sp, market_data_provider=mp)
    signals = service.run_signals()

    by_symbol = {s.symbol: s for s in signals}
    assert set(by_symbol) == {"AAA", "BBB"}
    # AAA: clean uptrend, overbought RSI -> pullback strategy stays on the watchlist.
    assert by_symbol["AAA"].action == SignalAction.watch
    # BBB: downtrend, price below its moving average -> watch.
    assert by_symbol["BBB"].action == SignalAction.watch


def test_local_csv_signal_generation_is_deterministic() -> None:
    sp, mp = build_providers(DataMode.local_historical, data_dir=LOCAL_DATA_DIR)
    first = [(s.symbol, s.action) for s in HarnessService(security_provider=sp, market_data_provider=mp).run_signals()]
    second = [(s.symbol, s.action) for s in HarnessService(security_provider=sp, market_data_provider=mp).run_signals()]
    assert first == second


# --- Smoke invariant regression with default providers ----------------------


def test_smoke_unchanged_with_default_providers() -> None:
    summary = HarnessService().run_smoke()
    assert summary["signals"] == 7
    assert summary["live_trading_enabled"] is False
