"""Prompts for the investment team."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quantpilot.services.research_agents.models import EvidenceBundle
from quantpilot.services.research_agents.prompts.market import snapshot_summary

SCOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "6자리 KRX 종목코드"},
                    "name": {"type": "string"},
                    "why": {"type": "string"},
                    "vault_citations": {"type": "array", "items": {"type": "string"}},
                    "news_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["symbol", "name", "why", "vault_citations", "news_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _notes_block(recent_notes: list[tuple[str, str]]) -> str:
    if not recent_notes:
        return "(최근 시황 노트 없음)"
    return "\n\n".join(f"----- 시황 {day} -----\n{body.strip()}" for day, body in recent_notes)


def scout_prompt(theme: str, bundle: EvidenceBundle, recent_notes: list[tuple[str, str]], watchlist: list[dict[str, str]]) -> str:
    return (
        f"세션 날짜: {bundle.date}\n"
        f"사용자 테마: {theme}\n\n"
        "관심종목(JSON):\n```json\n" + _json(watchlist) + "\n```\n\n"
        "최근 시황 노트:\n" + _notes_block(recent_notes) + "\n\n"
        "오늘의 증거 JSON:\n```json\n" + _json(bundle.model_dump()) + "\n```\n\n"
        "에이전트 정의의 규율대로 후보 최대 5개를 요청된 JSON 스키마로 낸다.\n"
    )


def researcher_prompt(
    candidate: dict[str, Any],
    bundle: EvidenceBundle,
    base_rates: list[dict[str, Any]],
    evidence_path: Path,
    base_rate_path: Path | None = None,
) -> str:
    symbol = candidate["symbol"]
    row = next((r.model_dump() for r in bundle.snapshot.watchlist_rows if r.symbol == symbol), None)
    name = candidate.get("name", "")
    news = [n.model_dump() for n in bundle.news if name and name in n.title]
    return (
        f"세션 날짜: {bundle.date}\n"
        f"증거 파일 경로: {evidence_path}\n"
        + (f"기저율 파일 경로: {base_rate_path}\n" if base_rate_path else "")
        + "\n후보:\n```json\n" + _json(candidate) + "\n```\n\n"
        "오늘 스냅샷 요약:\n" + snapshot_summary(bundle) + "\n\n"
        "이 종목의 관심종목 행(없으면 null):\n```json\n" + _json(row) + "\n```\n\n"
        "이 종목 이름이 제목에 든 헤드라인:\n```json\n" + _json(news) + "\n```\n\n"
        "코드가 계산한 기저율(조건별):\n```json\n" + _json(base_rates) + "\n```\n\n"
        "에이전트 정의의 7절을 이 순서로 작성한다.\n"
    )


def refuter_prompt(candidate: dict[str, Any], researcher_text: str, bundle: EvidenceBundle, base_rate_path: Path | None = None) -> str:
    return (
        f"세션 날짜: {bundle.date}\n"
        f"후보: {candidate.get('name', '')} ({candidate['symbol']})\n"
        + (f"기저율 파일 경로(검증용): {base_rate_path}\n" if base_rate_path else "")
        + "\n오늘 스냅샷 요약:\n" + snapshot_summary(bundle) + "\n\n"
        "===== 종목 리서처 초안 =====\n" + researcher_text.strip() + "\n\n"
        "에이전트 정의의 5절로 판정한다.\n"
    )


def direction_prompt(
    open_decisions: list[tuple[str, str]],
    recent_notes: list[tuple[str, str]],
    candidate_summaries: list[str],
    bundle: EvidenceBundle,
) -> str:
    decisions = "\n\n".join(f"----- 결정 {name} -----\n{text.strip()}" for name, text in open_decisions) or "(열린 결정 레코드 없음)"
    candidates = "\n".join(f"- {s}" for s in candidate_summaries) or "- (오늘 후보 없음)"
    return (
        f"세션 날짜: {bundle.date}\n\n"
        "오늘 스냅샷 요약:\n" + snapshot_summary(bundle) + "\n\n"
        "열린 결정 레코드(frontmatter + 무효화 조건):\n" + decisions + "\n\n"
        "최근 시황 노트:\n" + _notes_block(recent_notes) + "\n\n"
        "오늘 리서치된 후보:\n" + candidates + "\n\n"
        "에이전트 정의의 4절로 방향 메모를 쓴다.\n"
    )
