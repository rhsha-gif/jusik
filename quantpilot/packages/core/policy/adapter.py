from __future__ import annotations

import re

from quantpilot.packages.core.policy.types import PolicyAST, PolicyIntent, PolicyIntentType
from quantpilot.packages.core.schemas import BrokerMode, ExecutionMode, OrderType, UserPolicy


_CASH_RAISE_MARKERS = (
    "raise cash",
    "increase cash",
    "cash raise",
    "sell to cash",
    "de-risk",
    "derisk",
)
_TRIM_SECTOR_MARKERS = (
    "trim sector",
    "trim technology",
    "trim healthcare",
    "trim financial",
    "trim industrial",
    "trim materials",
    "reduce sector",
    "sector trim",
    "underweight sector",
)
_LIVE_TRADING_MARKERS = (
    "live trading",
    "live broker",
    "live order",
    "live orders",
    "real broker",
    "real order",
    "real orders",
    "submit live",
)
_AUTOMATION_MODES = {ExecutionMode.guarded_autopilot, ExecutionMode.fully_automated}


def _contains_phrase(normalized: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in normalized for phrase in phrases)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique_items.append(item)
    return unique_items


def _has_short_intent(normalized: str) -> bool:
    return bool(re.search(r"\bshort(?:ing|s)?\b|\bshort\s+sell\b", normalized))


def _has_inverse_intent(normalized: str) -> bool:
    return bool(re.search(r"\binverse\b|\bbear\s+etf\b", normalized))


def _has_market_order_intent(normalized: str) -> bool:
    return "market order" in normalized or "market orders" in normalized


def _has_ambiguous_intent(raw_text: str, normalized: str) -> bool:
    if not raw_text.strip():
        return True
    directional_terms = ("buy", "sell", "hold", "trim", "raise cash", "short", "inverse")
    if " or " in normalized and sum(term in normalized for term in directional_terms) >= 2:
        return True
    return any(marker in normalized for marker in ("maybe", "not sure", "unclear"))


def build_policy_intent_from_legacy(legacy_policy: UserPolicy, *, raw_text: str) -> PolicyIntent:
    normalized = f" {raw_text.lower()} "
    reason_codes: list[str] = []
    unsupported_intent = False
    human_review_required = False
    cash_raise = _contains_phrase(normalized, _CASH_RAISE_MARKERS)
    trim_sector = _contains_phrase(normalized, _TRIM_SECTOR_MARKERS)
    ambiguity = _has_ambiguous_intent(raw_text, normalized)

    if ambiguity:
        reason_codes.append("ambiguous_policy_intent")
        human_review_required = True
    if _has_short_intent(normalized):
        reason_codes.append("short_not_supported")
        unsupported_intent = True
    if _has_inverse_intent(normalized):
        reason_codes.append("inverse_not_supported")
        unsupported_intent = True
    if _contains_phrase(normalized, _LIVE_TRADING_MARKERS):
        reason_codes.append("live_trading_not_supported")
        unsupported_intent = True
    if _has_market_order_intent(normalized):
        reason_codes.append("market_orders_disabled")
        human_review_required = True
    if legacy_policy.execution_mode in _AUTOMATION_MODES:
        reason_codes.append("automation_requires_separate_enablement")
        human_review_required = True

    if unsupported_intent:
        intent_type: PolicyIntentType = "unsupported"
    elif cash_raise:
        intent_type = "cash_raise"
    elif trim_sector:
        intent_type = "trim_sector"
    else:
        intent_type = "long_only_buy"

    return PolicyIntent(
        raw_text=raw_text,
        user_id=legacy_policy.user_id,
        intent_type=intent_type,
        market=legacy_policy.market,
        risk_profile=legacy_policy.risk_profile,
        preferred_symbols=list(legacy_policy.preferred_symbols),
        preferred_themes=list(legacy_policy.preferred_themes),
        preferred_sectors=list(legacy_policy.preferred_sectors),
        blocklist=list(legacy_policy.blocklist),
        cash_raise=cash_raise,
        trim_sector=trim_sector,
        requested_order_types=list(legacy_policy.allowed_order_types),
        requested_execution_mode=legacy_policy.execution_mode,
        broker=legacy_policy.broker,
        ambiguity=ambiguity,
        unsupported_intent=unsupported_intent,
        human_review_required=human_review_required,
        reason_codes=_unique(reason_codes),
    )


def _safe_policy_copy(legacy_policy: UserPolicy, *, fail_closed: bool) -> UserPolicy:
    broker = legacy_policy.broker if legacy_policy.broker in {BrokerMode.mock, BrokerMode.paper} else BrokerMode.mock
    execution_mode = legacy_policy.execution_mode
    if fail_closed and execution_mode in _AUTOMATION_MODES:
        execution_mode = ExecutionMode.approval_required

    return UserPolicy.model_validate(
        {
            **legacy_policy.model_dump(),
            "allowed_order_types": [OrderType.limit],
            "broker": broker,
            "execution_mode": execution_mode,
            "guarded_autopilot_enabled": False,
            "fully_automated_operator_enabled": False,
        }
    )


def adapt_legacy_policy_to_ast(
    legacy_policy: UserPolicy,
    *,
    raw_text: str | None = None,
    intent: PolicyIntent | None = None,
) -> PolicyAST:
    policy_text = raw_text if raw_text is not None else "legacy policy"
    compiled_intent = intent or build_policy_intent_from_legacy(legacy_policy, raw_text=policy_text)
    blocked_reasons = _unique(list(compiled_intent.reason_codes))
    fail_closed = bool(
        compiled_intent.ambiguity
        or compiled_intent.unsupported_intent
        or "market_orders_disabled" in blocked_reasons
        or "automation_requires_separate_enablement" in blocked_reasons
    )
    human_review_required = compiled_intent.human_review_required or fail_closed
    status = "blocked" if fail_closed else ("review_required" if human_review_required else "ready")
    policy = _safe_policy_copy(legacy_policy, fail_closed=fail_closed)

    return PolicyAST(
        intent=compiled_intent,
        policy=policy,
        status=status,
        order_submission_enabled=False,
        live_trading_enabled=False,
        broker_mode=policy.broker,
        allowed_order_types=[OrderType.limit],
        long_only=compiled_intent.long_only,
        cash_raise=compiled_intent.cash_raise,
        trim_sector=compiled_intent.trim_sector,
        no_short_supported=compiled_intent.no_short_supported,
        ambiguity=compiled_intent.ambiguity,
        unsupported_intent=compiled_intent.unsupported_intent,
        human_review_required=human_review_required,
        fail_closed=fail_closed,
        blocked_reasons=blocked_reasons if fail_closed else [],
    )
