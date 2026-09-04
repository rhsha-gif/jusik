"""Naver Search (news) collector.

Uses the standard library only. Credentials come from the environment
(`NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`) and never from a file; a missing
credential is an explicit error rather than an empty result, so a brief can
never silently ship without its news section.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from quantpilot.services.research_agents.models import NewsItem

NAVER_NEWS_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
DEFAULT_QUERIES = ("코스피", "코스닥", "금리", "환율")
_TAG_RE = re.compile(r"<[^>]+>")


class NewsCollectionError(RuntimeError):
    pass


class NewsClient(Protocol):
    def search(self, query: str, display: int) -> dict[str, Any]:
        """Raw Naver news search response for `query`, newest first."""
        ...


class NaverNewsClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None, timeout_s: float = 15.0) -> None:
        self._id = client_id or os.environ.get("NAVER_CLIENT_ID", "")
        self._secret = client_secret or os.environ.get("NAVER_CLIENT_SECRET", "")
        if not self._id or not self._secret:
            raise NewsCollectionError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET are not set in the environment")
        self._timeout_s = timeout_s

    def search(self, query: str, display: int) -> dict[str, Any]:
        params = urllib.parse.urlencode({"query": query, "display": display, "sort": "date"})
        request = urllib.request.Request(
            f"{NAVER_NEWS_ENDPOINT}?{params}",
            headers={"X-Naver-Client-Id": self._id, "X-Naver-Client-Secret": self._secret},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310 - fixed https host  # nosemgrep
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # never echo headers or body: the response may quote the request
            raise NewsCollectionError(f"naver news search failed for {query!r}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise NewsCollectionError(f"naver news search unreachable for {query!r}: {type(exc).__name__}") from exc


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _domain(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def news_id(link: str) -> str:
    return "news:" + hashlib.sha256(link.encode("utf-8")).hexdigest()[:10]


def _published(value: str) -> str:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value


def collect_news(
    client: NewsClient,
    queries: list[str] | tuple[str, ...],
    *,
    per_query: int = 10,
    max_items: int = 60,
) -> list[NewsItem]:
    """Newest-first, de-duplicated by link. Raises on the first failed query (fail-closed)."""

    if not queries:
        raise NewsCollectionError("no news queries given")
    seen: set[str] = set()
    items: list[NewsItem] = []
    for query in queries:
        payload = client.search(query, per_query)
        for raw in payload.get("items", []):
            link = raw.get("originallink") or raw.get("link") or ""
            if not link or link in seen:
                continue
            seen.add(link)
            items.append(
                NewsItem(
                    id=news_id(link),
                    title=_clean(raw.get("title", "")),
                    link=link,
                    source_domain=_domain(link),
                    published_at=_published(raw.get("pubDate", "")),
                    query=query,
                )
            )
    items.sort(key=lambda item: item.published_at, reverse=True)
    return items[:max_items]
