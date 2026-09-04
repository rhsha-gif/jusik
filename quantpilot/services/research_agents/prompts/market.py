"""Prompts for the market team. The evidence JSON is embedded verbatim (UTF-8, not escaped)."""

from __future__ import annotations

import json
from pathlib import Path

from quantpilot.services.research_agents.models import EvidenceBundle

EDITOR_SCHEMA = {
    "type": "object",
    "properties": {
        "slack_text": {"type": "string", "description": "12줄 이내 슬랙 평문"},
        "note_markdown": {"type": "string", "description": "원장 노트 마크다운, 마지막 절은 ## 출처"},
    },
    "required": ["slack_text", "note_markdown"],
    "additionalProperties": False,
}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def snapshot_summary(bundle: EvidenceBundle) -> str:
    snap = bundle.snapshot
    flows = snap.investor_flows
    return (
        f"코스피 {snap.kospi_close} ({snap.kospi_change_pct:+}%), 코스닥 {snap.kosdaq_close} ({snap.kosdaq_change_pct:+}%)\n"
        f"외국인 {flows.foreign:+}억, 기관 {flows.institution:+}억, 개인 {flows.individual:+}억\n"
        f"관심종목: {', '.join(f'{r.name}({r.symbol})' for r in snap.watchlist_rows)}"
    )


def price_flow_prompt(bundle: EvidenceBundle) -> str:
    return (
        f"세션 날짜: {bundle.date}\n"
        f"증거 수집 시각: {bundle.collected_at}\n\n"
        "아래 `snapshot` JSON만 근거로 에이전트 정의의 출력 절 5개를 작성하라. 수치는 그대로 옮기고 새로 계산하지 않는다.\n\n"
        "```json\n" + _json(bundle.snapshot.model_dump()) + "\n```\n"
    )


def macro_news_prompt(bundle: EvidenceBundle) -> str:
    return (
        f"세션 날짜: {bundle.date}\n\n"
        "스냅샷 요약(관심종목 연결용, 수치 재인용 금지):\n" + snapshot_summary(bundle) + "\n\n"
        "아래 `news` JSON만 근거로 에이전트 정의의 출력 절 4개를 작성하라. `id`와 `link` 외의 출처를 만들지 않는다.\n"
        + ("이번 실행은 뉴스 수집을 건너뛰어 헤드라인이 없다. 네 절 모두 \"헤드라인 없음(수집 생략)\"으로 답하고 아무 출처도 만들지 않는다.\n" if not bundle.news else "")
        + "\n```json\n" + _json([item.model_dump() for item in bundle.news]) + "\n```\n"
    )


def editor_prompt(bundle: EvidenceBundle, price_flow_text: str, macro_news_text: str, evidence_path: Path) -> str:
    return (
        f"세션 날짜: {bundle.date}\n"
        f"증거 파일 경로: {evidence_path}\n\n"
        "아래 두 분석가 출력만으로 `slack_text`와 `note_markdown`을 만든다. 사실·수치·출처를 추가하지 않는다.\n\n"
        "===== 가격·수급 분석가 =====\n" + price_flow_text.strip() + "\n\n"
        "===== 거시·뉴스 분석가 =====\n" + macro_news_text.strip() + "\n"
    )
