"""Standard-library client for the Bank of Korea ECOS Open API.

`StatisticSearch/{key}/json/kr/{start_row}/{end_row}/{stat}/{cycle}/{from}/{to}/{item}`
returns `{"StatisticSearch": {"list_total_count": n, "row": [...]}}`; a failure
or an empty window comes back as `{"RESULT": {"CODE": ..., "MESSAGE": ...}}`
with HTTP 200. The key travels inside the URL, so nothing here ever puts a
URL into a log line or an exception message: every error is redacted first.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from quantpilot.services.research_agents.models import MacroPoint

ECOS_BASE = "https://ecos.bok.or.kr/api"
ECOS_KEY_ENV = "ECOS_API_KEY"
_USER_AGENT = "QuantPilot-research/1.0"
_NO_DATA_CODES = {"INFO-200"}  # "해당하는 데이터가 없습니다"

Fetch = Callable[[str], bytes]


class MacroCollectionError(RuntimeError):
    pass


def _default_fetch(url: str, timeout_s: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed https host  # nosemgrep
        return response.read()


def ecos_time_to_iso(value: str, cycle: str) -> str:
    """ECOS TIME → sortable ISO-ish label: D 20260912→2026-09-12, M 202609→2026-09, Q 2026Q1, A 2026."""

    text = str(value).strip()
    if cycle == "D" and len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if cycle in {"M", "SM"} and len(text) >= 6:
        return f"{text[:4]}-{text[4:6]}"
    return text


class EcosClient:
    def __init__(self, api_key: str, *, fetch: Fetch | None = None, max_rows: int = 5000) -> None:
        if not api_key:
            raise MacroCollectionError("ECOS key missing")
        self._key = api_key
        self._fetch = fetch or _default_fetch
        self._max_rows = max_rows

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "<ECOS_KEY>")

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{ECOS_BASE}/{path}"
        try:
            raw = self._fetch(url)
        except urllib.error.HTTPError as exc:
            raise MacroCollectionError(f"ecos: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MacroCollectionError(f"ecos: unreachable ({type(exc).__name__})") from None
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            raise MacroCollectionError("ecos: response is not JSON") from None
        if not isinstance(payload, dict):
            raise MacroCollectionError("ecos: unexpected payload shape")
        return payload

    def series(self, stat_code: str, cycle: str, start: str, end: str, item_code: str) -> list[MacroPoint]:
        """Points for one (stat, item) in [start, end]; an INFO-200 "no data" answer is an empty list."""

        payload = self._get(f"StatisticSearch/{self._key}/json/kr/1/{self._max_rows}/{stat_code}/{cycle}/{start}/{end}/{item_code}")
        result = payload.get("RESULT")
        if isinstance(result, dict):
            code = str(result.get("CODE", ""))
            if code in _NO_DATA_CODES:
                return []
            raise MacroCollectionError(f"ecos {stat_code}/{item_code}: {code} {self._redact(str(result.get('MESSAGE', '')))[:120]}")
        rows = (payload.get("StatisticSearch") or {}).get("row") or []
        points: list[MacroPoint] = []
        for row in rows:
            value = str(row.get("DATA_VALUE", "")).replace(",", "").strip()
            if not value:
                continue
            try:
                points.append(MacroPoint(time=ecos_time_to_iso(str(row.get("TIME", "")), cycle), value=float(value)))
            except ValueError:
                continue
        points.sort(key=lambda p: p.time)
        return points

    def items(self, stat_code: str) -> list[dict[str, str]]:
        """`StatisticItemList` rows (ITEM_CODE, ITEM_NAME, CYCLE, START_TIME, END_TIME) for verifying config codes."""

        payload = self._get(f"StatisticItemList/{self._key}/json/kr/1/500/{stat_code}")
        result = payload.get("RESULT")
        if isinstance(result, dict):
            raise MacroCollectionError(f"ecos items {stat_code}: {result.get('CODE', '')}")
        rows = (payload.get("StatisticItemList") or {}).get("row") or []
        keys = ("ITEM_CODE", "ITEM_NAME", "CYCLE", "START_TIME", "END_TIME", "UNIT_NAME")
        return [{key: str(row.get(key, "")) for key in keys} for row in rows]
