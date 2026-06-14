from __future__ import annotations

from quantpilot.packages.core.policy.adapter import adapt_legacy_policy_to_ast
from quantpilot.packages.core.policy.parser import parse_policy_text
from quantpilot.packages.core.policy.semantic import PolicyCompilationResult, SemanticPolicyCompiler
from quantpilot.packages.core.policy.types import PolicyAST, PolicyIntent
from quantpilot.packages.core.schemas import UserPolicy


def compile_policy_text(text: str, *, user_id: str = "fixture-user") -> PolicyAST:
    try:
        legacy_policy = parse_policy_text(text, user_id=user_id)
    except Exception as exc:  # pragma: no cover - defensive fail-closed bridge.
        fallback_policy = UserPolicy(user_id=user_id)
        intent = PolicyIntent(
            raw_text=text,
            user_id=user_id,
            intent_type="unsupported",
            ambiguity=True,
            unsupported_intent=True,
            human_review_required=True,
            reason_codes=["legacy_parser_error"],
        )
        return PolicyAST(
            intent=intent,
            policy=fallback_policy,
            status="blocked",
            fail_closed=True,
            human_review_required=True,
            ambiguity=True,
            unsupported_intent=True,
            blocked_reasons=["legacy_parser_error"],
            warnings=[str(exc)],
        )

    return adapt_legacy_policy_to_ast(legacy_policy, raw_text=text)


def compile_policy_ast_semantics(policy_ast: PolicyAST) -> PolicyCompilationResult:
    return SemanticPolicyCompiler().compile(policy_ast)


def compile_semantic_policy_text(text: str, *, user_id: str = "fixture-user") -> PolicyCompilationResult:
    return compile_policy_ast_semantics(compile_policy_text(text, user_id=user_id))
