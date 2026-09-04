from __future__ import annotations

import json
from datetime import date

import pytest

from quantpilot.services.research_agents.collectors.krx import CollectionError
from quantpilot.services.research_agents.collectors.naver_market import NaverMarketClient

_FCHART = (
    '<?xml version="1.0" encoding="EUC-KR" ?><protocol><chartdata symbol="005930" count="3">'
    '<item data="20260902|252000|255500|249500|250500|15176841" />'
    '<item data="20260903|254000|255000|243000|250000|13756022" />'
    '<item data="20260904|254000|256500|252500|256250|7335478" />'
    "</chartdata></protocol>"
).encode("cp949")
_TREND = json.dumps({"bizdate": "20260904", "personalValue": "-22,947", "foreignValue": "+2,840", "institutionalValue": "+9,581"}).encode("utf-8")
_INDUSTRY = json.dumps({"groups": [{"no": 288, "name": "건강관리기술", "changeRate": "19.61"}, {"no": 305, "name": "항공사", "changeRate": "-5.80"}]}).encode("utf-8")
_BASIC = json.dumps({"itemCode": "005930", "stockName": "삼성전자"}).encode("utf-8")


class FakeFetch:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if "fchart" in url:
            return _FCHART
        if url.endswith("/trend"):
            return _TREND
        if "stocks/industry" in url:
            return _INDUSTRY
        if url.endswith("/basic"):
            return _BASIC
        raise AssertionError(url)


def _client(today: date = date(2026, 9, 4)) -> tuple[NaverMarketClient, FakeFetch]:
    fetch = FakeFetch()
    return NaverMarketClient(sleep=lambda _: None, today=lambda: today, fetch=fetch), fetch


def test_stock_ohlcv_parses_fchart_and_computes_change_in_code() -> None:
    client, fetch = _client()
    rows = client.stock_ohlcv("005930", "20260903", "20260904")

    assert [r["date"] for r in rows] == ["2026-09-03", "2026-09-04"]
    assert rows[1]["close"] == 256250.0 and rows[1]["volume"] == 7335478
    assert rows[1]["change_pct"] == 2.5  # 256250 / 250000
    assert "symbol=005930" in fetch.urls[0] and "timeframe=day" in fetch.urls[0]
    with pytest.raises(CollectionError):
        client.stock_ohlcv("KOSPI", "20260903", "20260904")


def test_index_flows_are_converted_from_100m_krw_and_dated() -> None:
    client, _ = _client()
    rows = client.trading_value_by_date("KOSPI", "20260904", "20260904")
    assert rows == [{"date": "2026-09-04", "foreign": 2840.0 * 1e8, "institution": 9581.0 * 1e8, "individual": -22947.0 * 1e8}]
    assert client.trading_value_by_date("KOSPI", "20260903", "20260903") == []


def test_sector_changes_only_for_today() -> None:
    client, _ = _client(today=date(2026, 9, 4))
    assert client.index_changes("KOSPI", "20260903", "20260904") == [
        {"name": "건강관리기술", "change_pct": 19.61},
        {"name": "항공사", "change_pct": -5.8},
    ]
    assert client.index_changes("KOSPI", "20260902", "20260903") == []


def test_ticker_name_and_retry_then_fail_closed() -> None:
    client, _ = _client()
    assert client.ticker_name("005930") == "삼성전자"

    calls = {"n": 0}

    def flaky(url: str) -> bytes:
        calls["n"] += 1
        raise ConnectionError("down")

    failing = NaverMarketClient(sleep=lambda _: None, retries=2, fetch=flaky)
    with pytest.raises(CollectionError, match="after 2 attempts"):
        failing.ticker_name("005930")
    assert calls["n"] == 2
