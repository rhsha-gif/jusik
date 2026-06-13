from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from quantpilot.packages.core.backtest import BacktestAssumptions, BacktestRequest, BacktestSignal, run_backtest
from quantpilot.packages.core.data.external import (
    ExternalHistoricalMarketDataProvider,
    HistoricalDataRequest,
    HistoricalDataResponse,
)
from quantpilot.packages.core.data.providers import ProviderError
from quantpilot.packages.core.data.quality import (
    SimpleKrxCalendar,
    evaluate_historical_data_quality,
)
from quantpilot.packages.core.schemas import SignalAction


FETCHED_AT = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


class FakeHistoricalClient:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.requests: list[HistoricalDataRequest] = []

    def fetch_daily_bars(self, request: HistoricalDataRequest) -> HistoricalDataResponse:
        self.requests.append(request)
        return HistoricalDataResponse(
            payloads=[dict(row) for row in self.payloads],
            provider_name="fake_external",
            fetched_at=FETCHED_AT,
            market=request.market,
            adjusted=request.adjusted,
        )


def _bar(
    symbol: str,
    session: date,
    *,
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    close: float = 100.0,
    volume: int = 100_000,
) -> dict[str, Any]:
    open_price = close if open_price is None else open_price
    high = close + 1.0 if high is None else high
    low = close - 1.0 if low is None else low
    return {
        "symbol": symbol,
        "date": session.isoformat(),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _report(
    rows: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
    holidays: tuple[date, ...] = (),
):
    return evaluate_historical_data_quality(
        rows,
        symbols=("AAA",),
        start_date=start_date,
        end_date=end_date,
        market="KR_STOCK",
        provider_name="fake_external",
        calendar=SimpleKrxCalendar(holidays=holidays),
    )


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_weekend_days_are_not_missing_bars() -> None:
    report = _report(
        [_bar("AAA", date(2026, 1, 2))],
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 4),
    )

    assert report.status == "passed"
    assert report.expected_session_count == 1
    assert _codes(report) == set()


def test_configured_holidays_are_not_missing_bars() -> None:
    report = _report(
        [_bar("AAA", date(2026, 1, 5)), _bar("AAA", date(2026, 1, 7))],
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
        holidays=(date(2026, 1, 6),),
    )

    assert report.status == "passed"
    assert report.expected_session_count == 2
    assert "missing_bar" not in _codes(report)


def test_weekday_gap_is_missing_bar() -> None:
    report = _report(
        [_bar("AAA", date(2026, 1, 5)), _bar("AAA", date(2026, 1, 7))],
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
    )

    assert report.has_blocking_issues is True
    assert "missing_bar" in _codes(report)
    assert any(issue.session_date == date(2026, 1, 6) for issue in report.blocking_issues)


def test_stale_latest_bar_is_flagged_with_calendar_expected_session() -> None:
    report = _report(
        [_bar("AAA", date(2026, 1, 5)), _bar("AAA", date(2026, 1, 6))],
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
    )

    assert report.expected_latest_session == date(2026, 1, 7)
    assert "stale_latest_bar" in _codes(report)
    assert any(issue.blocking for issue in report.issues if issue.code == "stale_latest_bar")


def test_invalid_ohlc_blocks() -> None:
    report = _report(
        [_bar("AAA", date(2026, 1, 5), high=98.0, low=99.0)],
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 5),
    )

    assert report.has_blocking_issues is True
    assert "invalid_ohlc" in _codes(report)


def test_duplicate_date_blocks() -> None:
    report = _report(
        [_bar("AAA", date(2026, 1, 5)), _bar("AAA", date(2026, 1, 5), close=100.5)],
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 5),
    )

    assert report.has_blocking_issues is True
    assert "duplicate_bar" in _codes(report)


def test_non_monotonic_bars_block() -> None:
    report = _report(
        [
            _bar("AAA", date(2026, 1, 5)),
            _bar("AAA", date(2026, 1, 7)),
            _bar("AAA", date(2026, 1, 6)),
        ],
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
    )

    assert report.has_blocking_issues is True
    assert "non_monotonic_dates" in _codes(report)


def test_symbol_mismatch_blocks() -> None:
    report = _report(
        [_bar("BBB", date(2026, 1, 5))],
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 5),
    )

    assert report.has_blocking_issues is True
    assert "symbol_mismatch" in _codes(report)


def test_external_provider_fails_closed_on_blocking_quality_issue() -> None:
    client = FakeHistoricalClient([_bar("AAA", date(2026, 1, 5), volume=0)])

    with pytest.raises(ProviderError, match="invalid_volume"):
        ExternalHistoricalMarketDataProvider(
            client,
            symbols=["AAA"],
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            market="KR_STOCK",
        )


def test_backtest_records_provenance_and_quality() -> None:
    client = FakeHistoricalClient(
        [
            _bar("AAA", date(2026, 1, 5), close=100.0),
            _bar("AAA", date(2026, 1, 6), close=101.0),
            _bar("AAA", date(2026, 1, 7), close=102.0),
        ]
    )
    provider = ExternalHistoricalMarketDataProvider(
        client,
        symbols=["AAA"],
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
        market="KR_STOCK",
    )
    request = BacktestRequest(
        strategy_id="quality_gate_backtest",
        recipe_version="1.0",
        initial_cash=10_000,
        assumptions=BacktestAssumptions(sell_tax_bps=10.0),
        signals=[
            BacktestSignal(
                symbol="AAA",
                signal_date=date(2026, 1, 5),
                action=SignalAction.buy_ready,
                target_weight_hint=0.5,
                reason="quality summary coverage",
            )
        ],
    )

    result = run_backtest(request, provider)

    assert result.input_summary["data_provenance"]["provider_name"] == "fake_external"
    assert result.input_summary["data_quality"]["status"] == "passed"
    assert result.input_summary["data_quality"]["has_blocking_issues"] is False


def test_external_provider_remains_fake_client_testable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    client = FakeHistoricalClient([_bar("AAA", date(2026, 1, 5))])

    provider = ExternalHistoricalMarketDataProvider(
        client,
        symbols=["AAA"],
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 5),
        market="KR_STOCK",
    )

    assert provider.get_price_history()[0]["symbol"] == "AAA"
    assert client.requests[0].symbols == ("AAA",)


def test_kis_manual_integration_skips_by_default() -> None:
    manual_test = Path(__file__).resolve().parents[1] / "integration" / "test_kis_historical_manual.py"
    source = manual_test.read_text(encoding="utf-8")

    assert "pytest.mark.skipif" in source
    assert 'os.environ.get("RUN_KIS_MANUAL_INTEGRATION") != "1"' in source
