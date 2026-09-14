"""Manifold Markets public API (no key for reads; non-commercial personal use per its terms).

Binary markets matching a few search terms become *prior probabilities* the
scenario writer may compare its own scenario probabilities against. They are
play-money odds, so the evidence carries the market URL and close time and
the agents are told to treat them as a calibration reference, not a source.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from quantpilot.services.research_agents.models import PredictionMarket

MANIFOLD_SEARCH = "https://api.manifold.markets/v0/search-markets"
MANIFOLD_CITATION = "Manifold Markets (play-money prediction market, non-commercial use)"
_USER_AGENT = "QuantPilot-research/1.0"

Fetch = Callable[[str], bytes]


def _default_fetch(url: str, timeout_s: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed https host  # nosemgrep
        return response.read()


def _close_iso(value: Any) -> str | None:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).date().isoformat()


class ManifoldClient:
    def __init__(self, *, fetch: Fetch | None = None) -> None:
        self._fetch = fetch or _default_fetch

    def search(self, term: str, *, limit: int = 5) -> list[PredictionMarket]:
        query = urllib.parse.urlencode({"term": term, "limit": str(limit), "filter": "open", "sort": "liquidity"})
        raw = self._fetch(f"{MANIFOLD_SEARCH}?{query}")
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        out: list[PredictionMarket] = []
        for market in payload if isinstance(payload, list) else []:
            if not isinstance(market, dict):
                continue
            if market.get("outcomeType") != "BINARY" or market.get("probability") is None:
                continue
            out.append(
                PredictionMarket(
                    id=f"manifold:{market.get('id', '')}",
                    question=str(market.get("question", "")).strip(),
                    probability=round(float(market["probability"]), 3),
                    close_date=_close_iso(market.get("closeTime")),
                    url=str(market.get("url", "")),
                    term=term,
                    volume=float(market.get("volume", 0.0) or 0.0),
                )
            )
        return out


def collect_markets(client: ManifoldClient, terms: list[str], *, limit: int = 5) -> tuple[list[PredictionMarket], list[str]]:
    """(markets, notes) — a failed term is noted, never raised; duplicates across terms are dropped."""

    seen: set[str] = set()
    out: list[PredictionMarket] = []
    notes: list[str] = []
    for term in terms:
        try:
            for market in client.search(term, limit=limit):
                if market.id in seen:
                    continue
                seen.add(market.id)
                out.append(market)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            notes.append(f"manifold '{term}': unavailable ({type(exc).__name__})")
    return out, notes
