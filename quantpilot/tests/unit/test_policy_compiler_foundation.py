from __future__ import annotations

from quantpilot.packages.core.policy import (
    PolicyAST,
    PolicyIntent,
    adapt_legacy_policy_to_ast,
    compile_policy_text,
)
from quantpilot.packages.core.policy.parser import parse_policy_text
from quantpilot.packages.core.schemas import OrderType


def test_policy_intent_and_ast_are_importable() -> None:
    intent = PolicyIntent(raw_text="AAA moderate risk")
    ast = PolicyAST(intent=intent, policy=parse_policy_text("AAA moderate risk"))

    assert intent.long_only is True
    assert ast.order_submission_enabled is False
    assert ast.live_trading_enabled is False


def test_legacy_parser_result_can_be_adapted_without_losing_focus() -> None:
    legacy_policy = parse_policy_text("AAA technology moderate risk")

    ast = adapt_legacy_policy_to_ast(legacy_policy, raw_text="AAA technology moderate risk")

    assert ast.policy.preferred_symbols == ["AAA"]
    assert ast.policy.preferred_sectors == ["technology"]
    assert ast.status == "ready"
    assert ast.fail_closed is False


def test_long_only_buy_intent_compiles_to_safe_ast() -> None:
    ast = compile_policy_text("buy AAA long only with moderate risk")

    assert ast.intent.intent_type == "long_only_buy"
    assert ast.intent.long_only is True
    assert ast.no_short_supported is True
    assert ast.policy.allowed_order_types == [OrderType.limit]
    assert ast.status == "ready"


def test_cash_raise_intent_is_explicit_and_serializable() -> None:
    ast = compile_policy_text("raise cash to 30% with limit orders only")

    assert ast.intent.intent_type == "cash_raise"
    assert ast.cash_raise is True
    assert ast.policy.min_cash_weight == 0.30
    assert ast.order_submission_enabled is False


def test_sector_trim_intent_is_explicit() -> None:
    ast = compile_policy_text("trim technology sector exposure")

    assert ast.intent.intent_type == "trim_sector"
    assert ast.trim_sector is True
    assert ast.policy.preferred_sectors == ["technology"]
    assert ast.status == "ready"


def test_short_and_inverse_intents_fail_closed() -> None:
    short_ast = compile_policy_text("short AAA")
    inverse_ast = compile_policy_text("buy inverse ETF")

    assert short_ast.status == "blocked"
    assert short_ast.unsupported_intent is True
    assert short_ast.human_review_required is True
    assert "short_not_supported" in short_ast.blocked_reasons

    assert inverse_ast.status == "blocked"
    assert inverse_ast.unsupported_intent is True
    assert "inverse_not_supported" in inverse_ast.blocked_reasons


def test_market_order_intent_is_disabled_in_typed_ast() -> None:
    ast = compile_policy_text("buy AAA with market orders")

    assert ast.status == "blocked"
    assert ast.fail_closed is True
    assert ast.human_review_required is True
    assert "market_orders_disabled" in ast.blocked_reasons
    assert ast.intent.requested_order_types == [OrderType.limit, OrderType.market]
    assert ast.policy.allowed_order_types == [OrderType.limit]


def test_live_trading_intent_fails_closed() -> None:
    ast = compile_policy_text("run live trading with a real broker")

    assert ast.status == "blocked"
    assert ast.unsupported_intent is True
    assert ast.live_trading_enabled is False
    assert "live_trading_not_supported" in ast.blocked_reasons


def test_ambiguous_intent_fails_closed() -> None:
    ast = compile_policy_text("buy or sell AAA")

    assert ast.status == "blocked"
    assert ast.ambiguity is True
    assert ast.human_review_required is True
    assert "ambiguous_policy_intent" in ast.blocked_reasons


def test_policy_ast_serialization_round_trip() -> None:
    ast = compile_policy_text("raise cash to 30% and trim technology sector")

    payload = ast.model_dump(mode="json")
    restored = PolicyAST.model_validate(payload)

    assert restored.intent.intent_type == "cash_raise"
    assert restored.cash_raise is True
    assert restored.trim_sector is True
    assert restored.policy.min_cash_weight == 0.30
    assert restored.model_dump(mode="json") == payload
