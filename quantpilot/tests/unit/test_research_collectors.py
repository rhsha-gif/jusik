from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from quantpilot.services.research_agents.collectors.evidence import build_evidence, read_evidence, write_evidence
from quantpilot.services.research_agents.collectors.krx import (
    CollectionError,
    WatchlistEntry,
    collect_market_snapshot,
    load_watchlist,
)
from quantpilot.services.research_agents.collectors.naver_news import (
    NaverNewsClient,
    NewsCollectionError,
    collect_news,
    news_id,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "research_agents"
_KRX = json.loads((_FIXTURES / "krx_sample.json").read_text(encoding="utf-8"))
_NAVER = json.loads((_FIXTURES / "naver_news_sample.json").read_text(encoding="utf-8"))
_WATCHLIST = [WatchlistEntry("005930", "삼성전자", "반도체"), WatchlistEntry("000660", "SK하이닉스", "반도체")]


class FakeKrxClient:
    def __init__(self, *, drop_symbol: str | None = None, session_missing: bool = False) -> None:
        self.calls: list[str] = []
        self._drop = drop_symbol
        self._missing = session_missing

    def index_ohlcv(self, ticker: str, start: str, end: str) -> list[dict[str, Any]]:
        self.calls.append(f"index_ohlcv:{ticker}")
        dates = _KRX["session_dates"][:-1] if self._missing else _KRX["session_dates"]
        prev_close, last_close = _KRX["index_close"][ticker]
        rows = [{"date": d, "close": prev_close, "volume": 1} for d in dates]
        if not self._missing:
            rows[-1] = {"date": dates[-1], "close": last_close, "volume": 1}
        return rows

    def index_changes(self, market: str, start: str, end: str) -> list[dict[str, Any]]:
        self.calls.append(f"index_changes:{market}:{start}:{end}")
        return list(_KRX["index_changes"])

    def trading_value_by_date(self, market: str, start: str, end: str) -> list[dict[str, Any]]:
        self.calls.append("flows")
        return [dict(_KRX["flows"])]

    def stock_ohlcv(self, ticker: str, start: str, end: str) -> list[dict[str, Any]]:
        self.calls.append(f"stock:{ticker}")
        if ticker == self._drop:
            raise RuntimeError("simulated KRX block")
        spec = _KRX["stocks"][ticker]
        dates = _KRX["session_dates"]
        rows = [{"date": d, "close": spec["close"], "change_pct": 0.0, "volume": spec["base_volume"]} for d in dates]
        rows[-1] = {"date": dates[-1], "close": spec["close"], "change_pct": spec["change_pct"], "volume": spec["volume"]}
        return rows

    def ticker_name(self, ticker: str) -> str:
        return _KRX["stocks"][ticker]["name"]


def test_snapshot_numbers_are_computed_in_code() -> None:
    snap = collect_market_snapshot("2026-09-03", FakeKrxClient(), _WATCHLIST)

    assert snap.date == "2026-09-03"
    assert snap.kospi_close == 3015.0 and snap.kospi_change_pct == 1.17
    assert snap.kosdaq_change_pct == -0.2
    assert [s.name for s in snap.sector_top] == ["반도체", "은행", "운수장비"]
    assert [s.name for s in snap.sector_bottom] == ["의약품", "화학", "철강금속"]
    assert snap.investor_flows.foreign == 1200.0 and snap.investor_flows.individual == -900.0
    rows = {r.symbol: r for r in snap.watchlist_rows}
    assert rows["005930"].volume_ratio_20d == 3.0
    assert rows["000660"].volume_ratio_20d == 2.0
    assert rows["000660"].theme == "반도체"


def test_snapshot_is_deterministic_and_uses_previous_session_for_sector_window() -> None:
    client = FakeKrxClient()
    first = collect_market_snapshot("2026-09-03", client, _WATCHLIST)
    second = collect_market_snapshot("2026-09-03", FakeKrxClient(), _WATCHLIST)
    assert first == second
    assert "index_changes:KOSPI:20260902:20260903" in client.calls


def test_snapshot_fails_closed_when_any_symbol_fails() -> None:
    with pytest.raises(CollectionError, match="000660|snapshot collection failed"):
        collect_market_snapshot("2026-09-03", FakeKrxClient(drop_symbol="000660"), _WATCHLIST)


def test_snapshot_refuses_a_date_without_a_session_row() -> None:
    with pytest.raises(CollectionError, match="no KOSPI session row"):
        collect_market_snapshot("2026-09-03", FakeKrxClient(session_missing=True), _WATCHLIST)


def test_default_watchlist_has_fifteen_unique_codes() -> None:
    entries = load_watchlist()
    assert len(entries) == 15
    assert len({e.code for e in entries}) == 15
    assert all(len(e.code) == 6 and e.name for e in entries)


class FakeNewsClient:
    def __init__(self, fail_on: str | None = None) -> None:
        self.queries: list[str] = []
        self._fail_on = fail_on

    def search(self, query: str, display: int) -> dict[str, Any]:
        self.queries.append(query)
        if query == self._fail_on:
            raise NewsCollectionError("simulated 429")
        return json.loads(json.dumps(_NAVER))


def test_news_is_deduplicated_cleaned_and_newest_first() -> None:
    items = collect_news(FakeNewsClient(), ["코스피", "금리"])

    links = [i.link for i in items]
    assert len(links) == len(set(links)) == 2
    assert items[0].title == "코스피 외국인 순매수에 0.5% 상승 마감"
    assert "<b>" not in items[0].title
    assert items[0].source_domain == "example-news.co.kr"
    assert items[0].id == news_id("https://example-news.co.kr/article/1001")
    assert items[0].published_at > items[1].published_at


def test_news_fails_closed_on_a_failed_query() -> None:
    with pytest.raises(NewsCollectionError):
        collect_news(FakeNewsClient(fail_on="금리"), ["코스피", "금리"])


def test_naver_client_requires_credentials_from_environment() -> None:
    with pytest.raises(NewsCollectionError, match="NCP_APIGW_API_KEY_ID"):
        NaverNewsClient(environ={})


def test_naver_client_prefers_the_api_hub_pair_and_falls_back_to_legacy() -> None:
    from quantpilot.services.research_agents.collectors.naver_news import PROVIDERS, resolve_provider

    hub = {"NCP_APIGW_API_KEY_ID": "id", "NCP_APIGW_API_KEY": "secret", "NAVER_CLIENT_ID": "x", "NAVER_CLIENT_SECRET": "y"}
    assert resolve_provider(hub) == "hub"
    assert NaverNewsClient(environ=hub).provider == "hub"
    legacy = {"NAVER_CLIENT_ID": "x", "NAVER_CLIENT_SECRET": "y"}
    assert resolve_provider(legacy) == "legacy"
    assert PROVIDERS["hub"]["endpoint"].startswith("https://naverapihub.apigw.ntruss.com/search/v1/")
    assert PROVIDERS["hub"]["id_header"] == "X-NCP-APIGW-API-KEY-ID"
    half = {"NCP_APIGW_API_KEY_ID": "id"}
    with pytest.raises(NewsCollectionError):
        resolve_provider(half)


def test_evidence_round_trip(tmp_path: Path) -> None:
    snap = collect_market_snapshot("2026-09-03", FakeKrxClient(), _WATCHLIST)
    news = collect_news(FakeNewsClient(), ["코스피"])
    bundle = build_evidence("2026-09-03", snap, news, collected_at="2026-09-03T16:10:00+09:00")

    path = write_evidence(bundle, tmp_path)
    assert path.name == "evidence_2026-09-03.json"
    loaded = read_evidence(path)
    assert loaded == bundle
    assert loaded.signal_input is False
    assert {s.id for s in loaded.sources} == {"naver_finance", "naver_news"}
    assert "삼성전자" in path.read_text(encoding="utf-8")  # ensure_ascii=False
