from __future__ import annotations

from quantpilot.packages.core.policy import (
    PolicyCompilationResult,
    SemanticPolicy,
    compile_policy_ast_semantics,
    compile_policy_text,
    compile_semantic_policy_text,
)
from quantpilot.packages.core.schemas import OrderType


def test_accumulate_semantic_compiles_universe_and_risk_budget() -> None:
    result = compile_semantic_policy_text("buy AAA technology moderate risk")
    semantic = result.semantic_policy

    assert isinstance(semantic, SemanticPolicy)
    assert semantic.direction == "accumulate"
    assert semantic.universe.preferred_symbols == ["AAA"]
    assert semantic.universe.preferred_sectors == ["technology"]
    assert semantic.risk_budget.risk_profile == "moderate"
    assert semantic.risk_budget.max_position_weight == 0.15
    assert semantic.horizon == "weekly"
    assert semantic.confidence > 0.80
    assert result.status == "ready"
    assert result.orderable is True
    assert semantic.order_submission_enabled is False
    assert semantic.allowed_order_types == [OrderType.limit]


def test_cash_raise_semantic_preserves_cash_budget() -> None:
    result = compile_semantic_policy_text("raise cash to 30% with limit orders only")
    semantic = result.semantic_policy

    assert semantic.direction == "cash_raise"
    assert semantic.cash_raise is True
    assert semantic.risk_budget.min_cash_weight == 0.30
    assert semantic.orderable is True
    assert semantic.forbidden.market_orders_enabled is False


def test_trim_sector_semantic_preserves_sector_constraint() -> None:
    result = compile_semantic_policy_text("trim technology sector exposure")
    semantic = result.semantic_policy

    assert semantic.direction == "trim_sector"
    assert semantic.trim_sector is True
    assert semantic.universe.preferred_sectors == ["technology"]
    assert semantic.orderable is True


def test_semantic_compiler_can_compile_existing_ast_without_changing_ast_api() -> None:
    ast = compile_policy_text("buy AAA long only with moderate risk")
    result = compile_policy_ast_semantics(ast)

    assert ast.order_submission_enabled is False
    assert result.policy_ast == ast
    assert result.semantic_policy.direction == "accumulate"
    assert result.semantic_policy.long_only is True
