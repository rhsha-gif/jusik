"""Naver news search collector (standard library only).

Two credential providers exist for the same search API and they are not
interchangeable: NAVER API HUB (`NCP_APIGW_API_KEY_ID` / `NCP_APIGW_API_KEY`,
the platform Naver is migrating to) and the legacy Developers Center
(`NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET`, support ends 2027-06-30). The HUB
pair wins when both are present. Credentials come from the environment only;
a missing pair is an explicit error rather than an empty result, so a brief
can never silently ship without its news section.
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

PROVIDERS: dict[str, dict[str, str]] = {
    "hub": {
        "endpoint": "https://naverapihub.apigw.ntruss.com/search/v1/news",
        "id_env": "NCP_APIGW_API_KEY_ID",
        "secret_env": "NCP_APIGW_API_KEY",
        "id_header": "X-NCP-APIGW-API-KEY-ID",
        "secret_header": "X-NCP-APIGW-API-KEY",
    },
    "legacy": {
        "endpoint": "https://openapi.naver.com/v1/search/news.json",
        "id_env": "NAVER_CLIENT_ID",
        "secret_env": "NAVER_CLIENT_SECRET",
        "id_header": "X-Naver-Client-Id",
        "secret_header": "X-Naver-Client-Secret",
    },
}
DEFAULT_QUERIES = ("코스피", "코스닥", "금리", "환율")
_TAG_RE = re.compile(r"<[^>]+>")


class NewsCollectionError(RuntimeError):
    pass


class NewsClient(Protocol):
    def search(self, query: str, display: int) -> dict[str, Any]:
        """Raw Naver news search response for `query`, newest first."""
        ...


def resolve_provider(environ: dict[str, str] | None = None) -> str:
    """'hub' when the API HUB pair is set, else 'legacy' when that pair is set, else an error."""

    env = os.environ if environ is None else environ
    for name in ("hub", "legacy"):
        spec = PROVIDERS[name]
        if env.get(spec["id_env"], "").strip() and env.get(spec["secret_env"], "").strip():
            return name
    raise NewsCollectionError(
        "no Naver search credentials in the environment: set NCP_APIGW_API_KEY_ID/NCP_APIGW_API_KEY "
        "(NAVER API HUB) or NAVER_CLIENT_ID/NAVER_CLIENT_SECRET (Developers Center)"
    )


class NaverNewsClient:
    def __init__(self, provider: str | None = None, timeout_s: float = 15.0, environ: dict[str, str] | None = None) -> None:
        env = os.environ if environ is None else environ
        self.provider = provider or resolve_provider(env)
        spec = PROVIDERS[self.provider]
        self._id = env.get(spec["id_env"], "").strip()
        self._secret = env.get(spec["secret_env"], "").strip()
        if not self._id or not self._secret:
            raise NewsCollectionError(f"{spec['id_env']} / {spec['secret_env']} are not set in the environment")
        self._endpoint = spec["endpoint"]
        self._headers = {spec["id_header"]: self._id, spec["secret_header"]: self._secret}
        self._timeout_s = timeout_s

    def search(self, query: str, display: int) -> dict[str, Any]:
        params = urllib.parse.urlencode({"query": query, "display": display, "sort": "date"})
        request = urllib.request.Request(f"{self._endpoint}?{params}", headers=dict(self._headers))
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310 - fixed https host  # nosemgrep
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # never echo headers or body: the response may quote the request
            raise NewsCollectionError(f"naver news search ({self.provider}) failed for {query!r}: HTTP {exc.code}") from exc
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
