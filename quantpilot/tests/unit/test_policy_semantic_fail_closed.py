from __future__ import annotations

from quantpilot.packages.core.policy import PolicyCompilationResult, compile_semantic_policy_text
from quantpilot.packages.core.schemas import OrderType


def test_short_intent_semantic_fails_closed() -> None:
    result = compile_semantic_policy_text("short AAA")
    semantic = result.semantic_policy

    assert result.status == "blocked"
    assert result.fail_closed is True
    assert result.orderable is False
    assert semantic.direction == "unsupported"
    assert semantic.unsupported_intent is True
    assert semantic.human_review_required is True
    assert semantic.order_submission_enabled is False
    assert "short_not_supported" in result.blocked_reasons
    assert "short" in semantic.forbidden.forbidden_intents
    assert semantic.forbidden.no_short_supported is True


def test_inverse_intent_semantic_fails_closed() -> None:
    result = compile_semantic_policy_text("buy inverse ETF")

    assert result.status == "blocked"
    assert result.orderable is False
    assert result.semantic_policy.confidence == 0.0
    assert "inverse_not_supported" in result.blocked_reasons
    assert "inverse" in result.semantic_policy.forbidden.forbidden_intents


def test_market_order_request_stays_disabled_in_semantic_policy() -> None:
    result = compile_semantic_policy_text("buy AAA with market orders")
    semantic = result.semantic_policy

    assert result.status == "blocked"
    assert result.orderable is False
    assert semantic.forbidden.requested_market_order is True
    assert semantic.forbidden.market_orders_enabled is False
    assert semantic.allowed_order_types == [OrderType.limit]
    assert semantic.forbidden.forbidden_order_types == [OrderType.market]
    assert "market_orders_disabled" in result.blocked_reasons


def test_ambiguous_intent_requires_human_review_and_is_not_orderable() -> None:
    result = compile_semantic_policy_text("buy or sell AAA")

    assert result.status == "blocked"
    assert result.orderable is False
    assert result.semantic_policy.ambiguity is True
    assert result.semantic_policy.human_review_required is True
    assert result.semantic_policy.orderable is False
    assert "ambiguous_policy_intent" in result.blocked_reasons


def test_policy_compilation_result_serializes_round_trip() -> None:
    result = compile_semantic_policy_text("raise cash to 30% and trim technology sector")

    payload = result.model_dump(mode="json")
    restored = PolicyCompilationResult.model_validate(payload)

    assert restored.semantic_policy.direction == "cash_raise"
    assert restored.semantic_policy.cash_raise is True
    assert restored.semantic_policy.trim_sector is True
    assert restored.semantic_policy.risk_budget.min_cash_weight == 0.30
    assert restored.model_dump(mode="json") == payload
