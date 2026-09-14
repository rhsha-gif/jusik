"""Prompts and schemas for the strategist team. Evidence JSON is embedded verbatim (UTF-8)."""

from __future__ import annotations

import json
from pathlib import Path

from quantpilot.services.research_agents.models import MacroEvidenceBundle

_INVALIDATION = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "threshold": {"type": "string"},
        "cadence": {"type": "string"},
        "source": {"type": "string"},
    },
    "required": ["id", "threshold", "cadence", "source"],
    "additionalProperties": False,
}

_SCENARIO = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "probability": {"type": "number", "minimum": 0, "maximum": 1},
        "thesis": {"type": "string"},
        "supporting_precedent": {"type": "string"},
        "countervailing_precedent": {"type": "string"},
        "observable_change_factor": {"type": "string"},
        "question": {"type": "string"},
        "deadline": {"type": "string", "description": "YYYY-MM-DD"},
        "resolution_source": {"type": "string"},
        "invalidation": _INVALIDATION,
        "korea_exposure": {"type": "string"},
    },
    "required": [
        "name",
        "probability",
        "thesis",
        "supporting_precedent",
        "countervailing_precedent",
        "observable_change_factor",
        "question",
        "deadline",
        "resolution_source",
        "invalidation",
        "korea_exposure",
    ],
    "additionalProperties": False,
}

SCENARIO_SCHEMA = {
    "type": "object",
    "properties": {
        "regime_summary": {"type": "string"},
        "base_case": {"type": "string"},
        "scenarios": {"type": "array", "minItems": 2, "maxItems": 4, "items": _SCENARIO},
    },
    "required": ["regime_summary", "base_case", "scenarios"],
    "additionalProperties": False,
}

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


def macro_regime_prompt(bundle: MacroEvidenceBundle) -> str:
    return (
        f"세션 날짜: {bundle.date}\n"
        f"증거 수집 시각: {bundle.collected_at}\n\n"
        "아래 `macro` JSON만 근거로 에이전트 정의의 출력 절 4개를 작성하라. 수치는 그대로 옮기고 새로 계산하지 않는다.\n\n"
        "```json\n" + _json(bundle.macro.model_dump(exclude={"series": {"__all__": {"points"}}})) + "\n```\n"
    )


def geopolitics_prompt(bundle: MacroEvidenceBundle) -> str:
    gpr = bundle.macro.gpr.model_dump() if bundle.macro.gpr else None
    gdelt = [t.model_dump() for t in bundle.macro.gdelt]
    gdelt_note = "" if gdelt else "이번 실행은 GDELT 주제 집계를 수집하지 못했다. `## 글로벌 보도 강도`는 \"GDELT 미수집\" 한 줄로 답한다.\n"
    gpr_note = "" if gpr else "이번 실행은 GPR 지수를 수집하지 못했다(`skipped` 참조). `## GPR 판독`은 \"GPR 미수집\" 한 줄로 답한다.\n"
    news_note = "" if bundle.news else "이번 실행은 뉴스 수집을 건너뛰어 헤드라인이 없다. 이슈 묶음은 \"헤드라인 없음(수집 생략)\"으로 답하고 아무 출처도 만들지 않는다.\n"
    return (
        f"세션 날짜: {bundle.date}\n\n"
        + gpr_note
        + gdelt_note
        + news_note
        + "아래 `gpr`·`gdelt`·`news` JSON만 근거로 에이전트 정의의 출력 절 5개를 작성하라. 뉴스는 `id`와 `link`, GDELT 기사는 `id`와 `url` 외의 출처를 만들지 않는다.\n\n"
        + "```json\n" + _json({"gpr": gpr, "gdelt": gdelt, "skipped": bundle.macro.skipped, "news": [n.model_dump() for n in bundle.news]}) + "\n```\n"
    )


def scenario_prompt(bundle: MacroEvidenceBundle, macro_text: str, geo_text: str) -> str:
    markets = [m.model_dump() for m in bundle.macro.markets]
    market_block = (
        "외부 예측시장 사전확률(Manifold, 플레이머니 — 출처가 아니라 캘리브레이션 참고. 시나리오 확률이 크게 다르면 `probability_note`가 아닌 `thesis` 끝에 한 문장으로 차이를 밝힌다):\n```json\n" + _json(markets) + "\n```\n\n"
        if markets
        else ""
    )
    return (
        f"세션 날짜: {bundle.date}\n"
        f"열린 결정 레코드 id: {', '.join(bundle.open_decisions) or '없음'}\n\n"
        + market_block +
        "아래 두 분석가 출력만으로 시나리오 2~4개를 요청된 JSON 스키마로 만든다. 시한(`deadline`)은 세션 날짜로부터 1~6개월 안의 YYYY-MM-DD.\n\n"
        "===== 매크로 레짐 분석가 =====\n" + macro_text.strip() + "\n\n"
        "===== 지정학 분석가 =====\n" + geo_text.strip() + "\n"
    )


def refuter_prompt(bundle: MacroEvidenceBundle, macro_text: str, geo_text: str) -> str:
    return (
        f"세션 날짜: {bundle.date}\n\n"
        "아래 두 분석가 출력만 입력이다(시나리오는 일부러 주어지지 않는다). 에이전트 정의의 출력 절 5개를 작성하라.\n\n"
        "===== 매크로 레짐 분석가 =====\n" + macro_text.strip() + "\n\n"
        "===== 지정학 분석가 =====\n" + geo_text.strip() + "\n"
    )


def editor_prompt(
    bundle: MacroEvidenceBundle,
    macro_text: str,
    geo_text: str,
    scenarios: dict,
    refuter_text: str,
    evidence_path: Path,
) -> str:
    citation = bundle.macro.gpr.citation if bundle.macro.gpr else ""
    return (
        f"세션 날짜: {bundle.date}\n"
        f"증거 파일 경로: {evidence_path}\n"
        f"열린 결정 레코드 id: {', '.join(bundle.open_decisions) or '없음'}\n"
        + (f"GPR 인용문: {citation}\n" if citation else "")
        + "\n아래 네 입력만으로 `slack_text`와 `note_markdown`을 만든다. 사실·수치·출처를 추가하지 않는다.\n\n"
        "===== 매크로 레짐 분석가 =====\n" + macro_text.strip() + "\n\n"
        "===== 지정학 분석가 =====\n" + geo_text.strip() + "\n\n"
        "===== 시나리오 작성자 (JSON) =====\n```json\n" + _json(scenarios) + "\n```\n\n"
        "===== 독립 반증자 =====\n" + refuter_text.strip() + "\n"
    )
