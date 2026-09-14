from __future__ import annotations

import json
import urllib.error

import pytest

from quantpilot.services.research_agents.collectors.gdelt import GdeltClient, article_id, collect_gdelt_themes
from quantpilot.services.research_agents.collectors.manifold import ManifoldClient, collect_markets


def _timeline(values: list[float]) -> bytes:
    data = [{"date": f"202608{i + 1:02d}T000000Z", "value": v} for i, v in enumerate(values)]
    return json.dumps({"timeline": [{"series": "Volume Intensity", "data": data}]}).encode("utf-8")


def _artlist() -> bytes:
    return json.dumps(
        {
            "articles": [
                {"url": "https://www.etoday.co.kr/news/view/1", "title": "반도체 수출 증가", "seendate": "20260908T071500Z", "domain": "etoday.co.kr", "language": "Korean", "sourcecountry": "South Korea"},
                {"url": "", "title": "dropped"},
            ]
        }
    ).encode("utf-8")


def test_gdelt_attention_ratio_articles_and_rate_limit() -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return _timeline([0.1] * 23 + [0.3] * 7) if "timelinevol" in url else _artlist()

    client = GdeltClient(fetch=fetch, sleep=sleeps.append)
    themes = collect_gdelt_themes(client, [{"id": "nk", "label": "북한", "query": '"North Korea"'}])
    theme = themes[0]
    assert theme.volume_recent_7d == 0.3 and theme.volume_prior == 0.1 and theme.attention_ratio == 3.0 and theme.days == 30
    assert theme.articles[0].id == article_id("https://www.etoday.co.kr/news/view/1") and theme.articles[0].seen_at == "2026-09-08T07:15:00Z"
    assert len(theme.articles) == 1 and theme.note == ""
    assert len(calls) == 2 and "format=json" in calls[0] and "mode=timelinevol" in calls[0] and "mode=artlist" in calls[1]
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 8.0  # second call waited for the interval window


def test_gdelt_backs_off_once_on_http_429_then_succeeds() -> None:
    attempts: list[str] = []
    sleeps: list[float] = []

    def fetch(url: str) -> bytes:
        attempts.append(url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]
        return _timeline([0.2] * 30) if "timelinevol" in url else _artlist()

    client = GdeltClient(fetch=fetch, sleep=sleeps.append, min_interval_s=8.0, retry_backoff_s=25.0)
    theme = collect_gdelt_themes(client, [{"id": "x", "label": "x", "query": "x"}])[0]
    assert theme.attention_ratio == 1.0 and len(attempts) == 3 and 25.0 in sleeps


def test_gdelt_second_429_is_a_note() -> None:
    def fetch(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    theme = collect_gdelt_themes(GdeltClient(fetch=fetch, sleep=lambda s: None), [{"id": "x", "label": "x", "query": "x"}])[0]
    assert theme.attention_ratio is None and "HTTPError" in theme.note


def test_gdelt_rate_limit_message_is_a_note_not_an_exception() -> None:
    client = GdeltClient(fetch=lambda url: b"Please limit requests to one every 5 seconds", sleep=lambda s: None)
    theme = collect_gdelt_themes(client, [{"id": "x", "label": "x", "query": "x"}])[0]
    assert theme.attention_ratio is None and theme.articles == [] and "unavailable" in theme.note


def test_gdelt_short_timeline_gives_no_ratio() -> None:
    client = GdeltClient(fetch=lambda url: _timeline([0.1] * 5) if "timelinevol" in url else _artlist(), sleep=lambda s: None)
    theme = collect_gdelt_themes(client, [{"id": "x", "label": "x", "query": "x"}])[0]
    assert theme.attention_ratio is None and theme.days == 5 and len(theme.articles) == 1


def _manifold_payload() -> bytes:
    return json.dumps(
        [
            {"id": "m1", "question": "Will Kim Jong Un lead North Korea at end of 2026?", "probability": 0.96275, "outcomeType": "BINARY", "closeTime": 1799017140000, "url": "https://manifold.markets/x/m1", "volume": 1200},
            {"id": "m2", "question": "Which month?", "probability": None, "outcomeType": "MULTIPLE_CHOICE", "closeTime": 1799017140000, "url": "https://manifold.markets/x/m2"},
        ]
    ).encode("utf-8")


def test_manifold_keeps_binary_markets_and_dedupes_across_terms() -> None:
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return _manifold_payload()

    markets, notes = collect_markets(ManifoldClient(fetch=fetch), ["North Korea", "Korea"], limit=4)
    assert notes == [] and len(markets) == 1
    m = markets[0]
    assert m.id == "manifold:m1" and m.probability == 0.963 and m.close_date == "2027-01-03" and m.term == "North Korea"
    assert all("filter=open" in c and "limit=4" in c for c in calls) and len(calls) == 2


def test_manifold_failure_is_noted_per_term() -> None:
    def fetch(url: str) -> bytes:
        if "oil" in url:
            raise TimeoutError()
        return _manifold_payload()

    markets, notes = collect_markets(ManifoldClient(fetch=fetch), ["oil price", "Korea"])
    assert len(markets) == 1 and notes == ["manifold 'oil price': unavailable (TimeoutError)"]


@pytest.mark.parametrize("url", ["https://a.example/1", "https://a.example/2"])
def test_article_ids_are_stable_and_prefixed(url: str) -> None:
    assert article_id(url) == article_id(url) and article_id(url).startswith("gdelt:") and len(article_id(url)) == 16
