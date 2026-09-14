"""GDELT DOC 2.0 API client (no key; terms: unlimited use with a citation, one request per 5 s).

Two calls per theme: `timelinevol` (share of global coverage per day, from
which code computes a 7-day-vs-30-day attention ratio) and `artlist` (a few
recent articles as C-grade evidence, cited by `gdelt:<sha256(url)[:10]>`).
The rate limit is enforced client-side with an injectable sleep so tests do
not wait. Any failure returns the theme with a note instead of raising.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from statistics import mean
from typing import Any, Callable

from quantpilot.services.research_agents.models import GdeltArticle, GdeltTheme

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_CITATION = "GDELT Project (https://www.gdeltproject.org/)"
_USER_AGENT = "QuantPilot-research/1.0"
_MIN_INTERVAL_S = 8.0  # the documented limit is 5 s; measured 2026-09-15: 6 s still drew HTTP 429
_RETRY_BACKOFF_S = 25.0

Fetch = Callable[[str], bytes]


def _default_fetch(url: str, timeout_s: float = 45.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed https host  # nosemgrep
        return response.read()


def article_id(url: str) -> str:
    return "gdelt:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]


def _seendate_iso(value: str) -> str:
    text = str(value).strip()
    if len(text) >= 15 and text[8] == "T":
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}T{text[9:11]}:{text[11:13]}:{text[13:15]}Z"
    return text


class GdeltClient:
    def __init__(
        self,
        *,
        fetch: Fetch | None = None,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_s: float = _MIN_INTERVAL_S,
        retry_backoff_s: float = _RETRY_BACKOFF_S,
    ) -> None:
        self._fetch = fetch or _default_fetch
        self._sleep = sleep
        self._min_interval_s = min_interval_s
        self._retry_backoff_s = retry_backoff_s
        self._last_call = 0.0

    def _fetch_once(self, url: str) -> bytes:
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < self._min_interval_s:
            self._sleep(self._min_interval_s - elapsed)
        try:
            return self._fetch(url)
        finally:
            self._last_call = time.monotonic()

    def _get(self, params: dict[str, str]) -> dict[str, Any]:
        url = f"{GDELT_DOC_API}?{urllib.parse.urlencode({**params, 'format': 'json'})}"
        try:
            raw = self._fetch_once(url)
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            self._sleep(self._retry_backoff_s)  # one back-off retry; a second 429 propagates as a note
            raw = self._fetch_once(url)
        text = raw.decode("utf-8", errors="replace").strip()
        if not text.startswith("{"):
            raise ValueError(f"gdelt: non-JSON answer ({text[:80]!r})")
        return json.loads(text)

    def attention(self, query: str, *, timespan: str = "30d") -> tuple[float | None, float | None, int]:
        """(latest-7-day mean, prior-window mean, days) of coverage share, from `timelinevol`."""

        payload = self._get({"query": query, "mode": "timelinevol", "timespan": timespan})
        series = (payload.get("timeline") or [{}])[0].get("data") or []
        values = [float(item.get("value", 0.0)) for item in series]
        if len(values) < 10:
            return None, None, len(values)
        recent = values[-7:]
        prior = values[:-7]
        return round(mean(recent), 4), round(mean(prior), 4) if prior else None, len(values)

    def articles(self, query: str, *, timespan: str = "7d", max_records: int = 5) -> list[GdeltArticle]:
        payload = self._get({"query": query, "mode": "artlist", "timespan": timespan, "maxrecords": str(max_records), "sort": "hybridrel"})
        out: list[GdeltArticle] = []
        for item in payload.get("articles") or []:
            url = str(item.get("url", "")).strip()
            if not url:
                continue
            out.append(
                GdeltArticle(
                    id=article_id(url),
                    title=str(item.get("title", "")).strip(),
                    url=url,
                    domain=str(item.get("domain", "")),
                    source_country=str(item.get("sourcecountry", "")),
                    language=str(item.get("language", "")),
                    seen_at=_seendate_iso(str(item.get("seendate", ""))),
                )
            )
        return out


def collect_gdelt_themes(client: GdeltClient, themes: list[dict[str, Any]], *, timespan_volume: str = "30d", timespan_articles: str = "7d", max_records: int = 5) -> list[GdeltTheme]:
    out: list[GdeltTheme] = []
    for theme in themes:
        query = str(theme["query"])
        item = GdeltTheme(id=str(theme["id"]), label=str(theme.get("label", theme["id"])), query=query)
        try:
            recent, prior, days = client.attention(query, timespan=timespan_volume)
            item.volume_recent_7d = recent
            item.volume_prior = prior
            item.days = days
            item.attention_ratio = round(recent / prior, 3) if recent is not None and prior else None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            item.note = f"volume unavailable ({type(exc).__name__})"
        try:
            item.articles = client.articles(query, timespan=timespan_articles, max_records=max_records)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            item.note = (item.note + "; " if item.note else "") + f"articles unavailable ({type(exc).__name__})"
        out.append(item)
    return out
