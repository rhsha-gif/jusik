"""Prompts and JSON schemas for the designer team. Evidence JSON is embedded verbatim (UTF-8)."""

from __future__ import annotations

import json
from typing import Any

_SOURCE = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string", "enum": ["peer_reviewed", "working_paper", "practitioner", "other"]},
        "title": {"type": "string"},
        "year": {"type": "integer"},
        "url_or_doi": {"type": "string"},
        "key_claim": {"type": "string"},
        "grade": {"type": "string", "enum": ["A", "B", "C"]},
        "verified": {"type": "boolean"},
        "challenges_hypothesis": {"type": "boolean"},
    },
    "required": ["id", "type", "title", "year", "url_or_doi", "key_claim", "grade", "verified", "challenges_hypothesis"],
    "additionalProperties": False,
}

_FEATURE = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "formula": {"type": "string"},
        "lookback_days": {"type": "integer", "minimum": 1},
        "source_citation": {"type": "string"},
    },
    "required": ["name", "formula", "lookback_days", "source_citation"],
    "additionalProperties": False,
}

_RISK_MATRIX = {
    "type": "object",
    "properties": {
        "sizing_formula": {"type": "string", "enum": ["fractional_kelly", "fixed_fraction", "vol_targeting"]},
        "max_position_pct": {"type": "number"},
        "max_sector_pct": {"type": "number"},
        "max_portfolio_drawdown_pct": {"type": "number"},
        "stop_loss_pct": {"type": "number"},
        "correlation_budget": {"type": "number"},
        "leverage_max": {"type": "number"},
        "circuit_breakers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"trigger": {"type": "string"}, "action": {"type": "string"}},
                "required": ["trigger", "action"],
                "additionalProperties": False,
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sizing_formula", "max_position_pct", "max_sector_pct", "max_portfolio_drawdown_pct", "stop_loss_pct", "correlation_budget", "leverage_max", "circuit_breakers", "sources"],
    "additionalProperties": False,
}

DESIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "strategy_id": {"type": "string", "pattern": "^[a-z0-9_]+$"},
        "version": {"type": "string"},
        "description": {"type": "string"},
        "hypothesis": {"type": "string"},
        "design_notes": {"type": "string", "description": "시장구조 관찰을 규칙에 어떻게 반영했는지, 반영하지 않은 이유"},
        "universe_filter": {
            "type": "object",
            "properties": {"min_avg_daily_value": {"type": "number"}, "max_universe_size": {"type": "integer"}},
            "required": ["min_avg_daily_value", "max_universe_size"],
            "additionalProperties": False,
        },
        "features": {"type": "array", "minItems": 1, "items": _FEATURE},
        "entry_rules": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "exit_rules": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "position_sizing": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["capped_target_weight", "capped_score_weight", "inverse_volatility"]},
                "max_target_weight": {"type": "number", "minimum": 0.01, "maximum": 0.15},
            },
            "required": ["method", "max_target_weight"],
            "additionalProperties": False,
        },
        "risk_rules": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "rebalance": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
        "validation": {
            "type": "object",
            "properties": {
                "walk_forward": {
                    "type": "object",
                    "properties": {
                        "train_size": {"type": "integer", "minimum": 20},
                        "test_size": {"type": "integer", "minimum": 5},
                        "purge_bars": {"type": "integer", "minimum": 0},
                        "embargo_bars": {"type": "integer", "minimum": 0},
                    },
                    "required": ["train_size", "test_size", "purge_bars", "embargo_bars"],
                    "additionalProperties": False,
                }
            },
            "required": ["walk_forward"],
            "additionalProperties": False,
        },
        "sources": {"type": "array", "minItems": 1, "items": _SOURCE},
        "risk_matrix": _RISK_MATRIX,
    },
    "required": [
        "strategy_id",
        "version",
        "description",
        "hypothesis",
        "design_notes",
        "universe_filter",
        "features",
        "entry_rules",
        "exit_rules",
        "position_sizing",
        "risk_rules",
        "rebalance",
        "validation",
        "sources",
        "risk_matrix",
    ],
    "additionalProperties": False,
}

FORENSICS_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_confidence": {"type": "string", "enum": ["high", "medium", "low", "reject"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["data_integrity", "statistical", "microstructure", "regime", "engine_limit"]},
                    "finding": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "major", "minor", "info"]},
                    "remediation": {"type": "string"},
                },
                "required": ["category", "finding", "severity", "remediation"],
                "additionalProperties": False,
            },
        },
        "deflated_sharpe_quoted": {"type": ["number", "null"]},
        "recommended_action": {"type": "string", "enum": ["approve", "revise", "reject"]},
        "notes": {"type": "string"},
    },
    "required": ["overall_confidence", "findings", "deflated_sharpe_quoted", "recommended_action", "notes"],
    "additionalProperties": False,
}

RISK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "block"]},
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "status": {"type": "string", "enum": ["pass", "fail", "unverified"]},
                    "observed": {"type": "string"},
                    "threshold": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["name", "status", "observed", "threshold", "detail"],
                "additionalProperties": False,
            },
        },
        "blocking_reasons": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["verdict", "checks", "blocking_reasons", "notes"],
    "additionalProperties": False,
}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def market_structure_prompt(session_date: str, evidence_json: str) -> str:
    return (
        f"세션 날짜: {session_date}\n\n"
        "아래 `market_structure` JSON만 근거로 에이전트 정의의 출력 절 4개를 작성하라. 수치는 그대로 옮기고 새로 계산하지 않는다.\n\n"
        "```json\n" + evidence_json.strip() + "\n```\n"
    )


def designer_prompt(
    session_date: str,
    hypothesis: str,
    structure_text: str,
    grammar_help: str,
    *,
    existing_ids: list[str],
    retry_errors: list[str] | None = None,
    previous: dict[str, Any] | None = None,
) -> str:
    retry = ""
    if retry_errors:
        retry = (
            "\n===== 재시도: 코드 검증 실패 =====\n"
            "직전 초안이 아래 오류로 거부됐다. 오류 항목만 고치고 나머지는 유지하라.\n"
            + "\n".join(f"- {e}" for e in retry_errors)
            + "\n\n직전 초안:\n```json\n" + _json(previous or {}) + "\n```\n"
        )
    return (
        f"세션 날짜: {session_date}\n"
        f"사용자 가설: {hypothesis}\n"
        f"이미 존재하는 strategy_id(사용 금지): {', '.join(existing_ids) or '없음'}\n\n"
        "===== 규칙 문법 (GRAMMAR) — 모든 formula·entry_rules·exit_rules는 이 문법으로만 =====\n"
        + grammar_help.strip()
        + "\n\n===== 시장구조 분석가 =====\n"
        + structure_text.strip()
        + "\n\n요청된 JSON 스키마로 StrategyRecipe 초안을 낸다. `hypothesis`에는 사용자 가설을 그대로 옮긴다.\n"
        + retry
    )


def forensics_prompt(session_date: str, recipe: dict[str, Any], report: dict[str, Any]) -> str:
    return (
        f"세션 날짜: {session_date}\n\n"
        "아래 레시피와 잡이 계산한 백테스트 보고서만 근거로 포렌식 판정을 요청된 JSON 스키마로 낸다. "
        "DSR·PSR·MinTRL은 `statistics`의 값을 인용만 한다.\n\n"
        "===== 레시피 =====\n```json\n" + _json(recipe) + "\n```\n\n"
        "===== 백테스트 보고서 =====\n```json\n" + _json(report) + "\n```\n"
    )


def risk_gate_prompt(session_date: str, recipe: dict[str, Any], metrics: dict[str, Any]) -> str:
    subset = {
        "strategy_id": recipe.get("strategy_id"),
        "position_sizing": recipe.get("position_sizing"),
        "risk_rules": recipe.get("risk_rules"),
        "exit_rules": recipe.get("exit_rules"),
        "execution_permissions": recipe.get("execution_permissions"),
        "risk_matrix": (recipe.get("audit_metadata") or {}).get("risk_matrix"),
    }
    return (
        f"세션 날짜: {session_date}\n\n"
        "아래 레시피 발췌와 백테스트 지표만 근거로 리스크 게이트 판정을 요청된 JSON 스키마로 낸다.\n\n"
        "===== 레시피 발췌 =====\n```json\n" + _json(subset) + "\n```\n\n"
        "===== 백테스트 지표 =====\n```json\n" + _json(metrics) + "\n```\n"
    )
