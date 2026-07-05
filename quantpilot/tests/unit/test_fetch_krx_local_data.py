from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from quantpilot.jobs.fetch_krx_local_data import (
    build_security_rows,
    frame_to_bars,
    validate_output,
    write_local_data,
)
from quantpilot.packages.core.data.providers import ProviderError


class _FakeIndex:
    """Mimics the pandas Timestamp index entries pykrx frames use."""

    def __init__(self, value: date) -> None:
        self._value = value

    def date(self) -> date:
        return self._value


class _FakeFrame:
    """Minimal stand-in for a pykrx daily OHLCV DataFrame (no pandas needed)."""

    def __init__(self, rows: list[tuple[date, dict[str, float]]]) -> None:
        self._rows = rows

    def iterrows(self):
        for bar_date, row in self._rows:
            yield _FakeIndex(bar_date), row


def _krx_row(open_: float, high: float, low: float, close: float, volume: float) -> dict[str, float]:
    return {"시가": open_, "고가": high, "저가": low, "종가": close, "거래량": volume}


def _make_bars(symbol: str, start: date, count: int) -> list[dict[str, Any]]:
    frame = _FakeFrame(
        [
            (
                start + timedelta(days=offset),
                _krx_row(100.0 + offset, 101.0 + offset, 99.0 + offset, 100.5 + offset, 10_000 + offset),
            )
            for offset in range(count)
        ]
    )
    bars, skipped = frame_to_bars(symbol, frame)
    assert skipped == 0
    return bars


def test_frame_to_bars_maps_pykrx_columns_and_skips_no_trade_days() -> None:
    frame = _FakeFrame(
        [
            (date(2026, 6, 1), _krx_row(100.0, 101.0, 99.0, 100.5, 10_000)),
            (date(2026, 6, 2), _krx_row(0.0, 0.0, 0.0, 0.0, 0.0)),  # KRX no-trade day
        ]
    )

    bars, skipped = frame_to_bars("005930", frame)

    assert skipped == 1
    assert bars == [
        {
            "symbol": "005930",
            "date": "2026-06-01",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10_000,
        }
    ]


def test_build_security_rows_computes_avg_daily_value_and_sector_override() -> None:
    bars = _make_bars("005930", date(2026, 5, 1), 2)

    rows = build_security_rows(
        {"005930": "삼성전자"},
        {"005930": bars},
        market="KR_STOCK",
        sectors={"005930": "technology"},
    )

    assert rows == [
        {
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR_STOCK",
            "sector": "technology",
            "themes": "",
            "avg_daily_value": round(
                (bars[0]["close"] * bars[0]["volume"] + bars[1]["close"] * bars[1]["volume"]) / 2, 2
            ),
            "data_ready": "true",
        }
    ]


def test_build_security_rows_fails_closed_when_symbol_has_no_bars() -> None:
    with pytest.raises(ProviderError, match="no usable daily bars"):
        build_security_rows({"005930": "삼성전자"}, {"005930": []}, market="KR_STOCK", sectors={})


def test_written_output_round_trips_through_local_historical_providers(tmp_path: Path) -> None:
    bars_by_symbol = {"005930": _make_bars("005930", date(2026, 4, 1), 30)}
    security_rows = build_security_rows(
        {"005930": "삼성전자"}, bars_by_symbol, market="KR_STOCK", sectors={}
    )

    write_local_data(tmp_path, security_rows, bars_by_symbol)
    security_count, bar_count = validate_output(tmp_path)

    assert security_count == 1
    assert bar_count == 30
