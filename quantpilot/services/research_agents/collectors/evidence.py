"""Evidence bundle: the only thing the agents are allowed to quote numbers from."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from quantpilot.services.research_agents.models import (
    EvidenceBundle,
    EvidenceSource,
    MarketSnapshot,
    NewsItem,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build_evidence(
    session_date: str,
    snapshot: MarketSnapshot,
    news: list[NewsItem],
    *,
    collected_at: str | None = None,
) -> EvidenceBundle:
    stamp = collected_at or _now_iso()
    return EvidenceBundle(
        date=session_date,
        collected_at=stamp,
        snapshot=snapshot,
        news=news,
        sources=[
            EvidenceSource(id="naver_finance", fetched_at=stamp, detail="KRX index/sector/investor/ohlcv via Naver Finance public endpoints"),
            EvidenceSource(id="naver_news", fetched_at=stamp, detail=f"{len(news)} headlines via Naver Search API"),
        ],
    )


def evidence_path(out_dir: Path, session_date: str) -> Path:
    return out_dir / f"evidence_{session_date}.json"


def write_evidence(bundle: EvidenceBundle, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_path(out_dir, bundle.date)
    path.write_text(json.dumps(bundle.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_evidence(path: Path) -> EvidenceBundle:
    return EvidenceBundle.model_validate_json(path.read_text(encoding="utf-8"))
