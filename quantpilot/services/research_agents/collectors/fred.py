"""Standard-library client for the FRED `series/observations` endpoint.

Missing observations arrive as the string "." and are dropped. The key is a
query parameter, so URLs are never logged or raised; errors are redacted.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from quantpilot.services.research_agents.collectors.ecos import MacroCollectionError
from quantpilot.services.research_agents.models import MacroPoint

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_KEY_ENV = "FRED_API_KEY"
_USER_AGENT = "QuantPilot-research/1.0"

Fetch = Callable[[str], bytes]


def _default_fetch(url: str, timeout_s: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed https host  # nosemgrep
        return response.read()


class FredClient:
    def __init__(self, api_key: str, *, fetch: Fetch | None = None) -> None:
        if not api_key:
            raise MacroCollectionError("FRED key missing")
        self._key = api_key
        self._fetch = fetch or _default_fetch

    def observations(self, series_id: str, *, start: str) -> list[MacroPoint]:
        query = urllib.parse.urlencode(
            {"series_id": series_id, "api_key": self._key, "file_type": "json", "observation_start": start}
        )
        try:
            raw = self._fetch(f"{FRED_BASE}?{query}")
        except urllib.error.HTTPError as exc:
            raise MacroCollectionError(f"fred {series_id}: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MacroCollectionError(f"fred {series_id}: unreachable ({type(exc).__name__})") from None
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            raise MacroCollectionError(f"fred {series_id}: response is not JSON") from None
        if not isinstance(payload, dict) or "observations" not in payload:
            message = str(payload.get("error_message", "")) if isinstance(payload, dict) else ""
            raise MacroCollectionError(f"fred {series_id}: {message.replace(self._key, '<FRED_KEY>')[:120] or 'no observations'}")
        points: list[MacroPoint] = []
        for row in payload["observations"]:
            value = str(row.get("value", "")).strip()
            if value in {"", "."}:
                continue
            try:
                points.append(MacroPoint(time=str(row.get("date", "")), value=float(value)))
            except ValueError:
                continue
        points.sort(key=lambda p: p.time)
        return points
