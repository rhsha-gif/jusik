from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from quantpilot.packages.core.policy.types import PolicyAST
from quantpilot.packages.core.schemas import BrokerMode, DataMode, ExecutionMode, HarnessModel, OrderType


InvestmentDirection = Literal["accumulate", "cash_raise", "trim_sector", "hold", "unsupported"]
PolicyCompilationStatus = Literal["ready", "blocked", "review_required"]
PolicyHorizon = Literal["daily", "weekly", "monthly", "unspecified"]


class UniverseConstraints(HarnessModel):
    market: str = "KR_STOCK"
    preferred_symbols: list[str] = Field(default_factory=list)
    preferred_themes: list[str] = Field(default_factory=list)
    preferred_sectors: list[str] = Field(default_factory=list)
    blocklist: list[str] = Field(default_factory=list)
    max_positions: int = Field(default=8, gt=0)
    min_avg_daily_value: float = Field(default=5_000_000, ge=0)


class ForbiddenConstraints(HarnessModel):
    no_short_supported: bool = True
    short_allowed: bool = False
    inverse_allowed: bool = False
    market_orders_enabled: bool = False
    requested_market_order: bool = False
    live_trading_enabled: bool = False
    allowed_order_types: list[OrderType] = Field(default_factory=lambda: [OrderType.limit])
    forbidden_order_types: list[OrderType] = Field(default_factory=lambda: [OrderType.market])
    forbidden_intents: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def forbidden_constraints_must_remain_non_live(self) -> "ForbiddenConstraints":
        if self.live_trading_enabled:
            raise ValueError("semantic policy cannot enable live trading")
        if self.market_orders_enabled:
            raise ValueError("semantic policy cannot enable market orders")
        if self.short_allowed or self.inverse_allowed:
            raise ValueError("semantic policy cannot enable short or inverse execution")
        if OrderType.market in self.allowed_order_types:
            raise ValueError("semantic policy allowed order types cannot include market")
        return self


class RiskBudget(HarnessModel):
    risk_profile: str = "moderate"
    max_positions: int = Field(default=8, gt=0)
    max_position_weight: float = Field(default=0.15, gt=0, le=1)
    max_sector_weight: float = Field(default=0.40, gt=0, le=1)
    min_cash_weight: float = Field(default=0.20, ge=0, lt=1)
    daily_loss_limit: float = Field(default=-0.03, gt=-1, lt=0)
    monthly_loss_limit: float = Field(default=-0.05, gt=-1, lt=0)
    single_order_cash_limit: float = Field(default=1_000_000, gt=0)
    max_daily_orders: int = Field(default=3, gt=0)
    max_daily_turnover: float = Field(default=3_000_000, gt=0)


class SemanticPolicy(HarnessModel):
    policy_id: str
    user_id: str = "fixture-user"
    policy_version: int = 1
    direction: InvestmentDirection = "accumulate"
    long_only: bool = True
    cash_raise: bool = False
    trim_sector: bool = False
    horizon: PolicyHorizon = "weekly"
    confidence: float = Field(default=0.0, ge=0, le=1)
    universe: UniverseConstraints
    forbidden: ForbiddenConstraints
    risk_budget: RiskBudget
    execution_mode: ExecutionMode = ExecutionMode.approval_required
    broker_mode: BrokerMode = BrokerMode.mock
    data_mode: DataMode = DataMode.fixture
    allowed_order_types: list[OrderType] = Field(default_factory=lambda: [OrderType.limit])
    ambiguity: bool = False
    unsupported_intent: bool = False
    human_review_required: bool = False
    fail_closed: bool = False
    orderable: bool = False
    order_submission_enabled: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def semantic_policy_must_remain_safe(self) -> "SemanticPolicy":
        if self.order_submission_enabled:
            raise ValueError("semantic policy cannot enable order submission")
        if OrderType.market in self.allowed_order_types:
            raise ValueError("semantic policy cannot allow market orders")
        if self.fail_closed and self.orderable:
            raise ValueError("fail-closed semantic policy cannot be orderable")
        if self.human_review_required and self.orderable:
            raise ValueError("human-review semantic policy cannot be orderable")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PolicyCompilationResult(HarnessModel):
    policy_ast: PolicyAST
    semantic_policy: SemanticPolicy
    status: PolicyCompilationStatus
    orderable: bool = False
    fail_closed: bool = False
    human_review_required: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_mode: DataMode = DataMode.fixture
    live_trading_enabled: bool = False
    broker_mode: BrokerMode = BrokerMode.mock

    @model_validator(mode="after")
    def compilation_result_must_remain_safe(self) -> "PolicyCompilationResult":
        if self.live_trading_enabled:
            raise ValueError("policy compilation result cannot enable live trading")
        if self.fail_closed and self.orderable:
            raise ValueError("fail-closed policy compilation cannot be orderable")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SemanticPolicyCompiler:
    def compile(self, policy_ast: PolicyAST) -> PolicyCompilationResult:
        policy = policy_ast.policy
        reason_codes = _unique([*policy_ast.intent.reason_codes, *policy_ast.blocked_reasons])
        requested_market_order = OrderType.market in policy_ast.intent.requested_order_types
        if requested_market_order and "market_orders_disabled" not in reason_codes:
            reason_codes.append("market_orders_disabled")

        forbidden_intents = _compile_forbidden_intents(reason_codes)
        unsupported_intent = policy_ast.unsupported_intent or bool(
            {"short_not_supported", "inverse_not_supported", "live_trading_not_supported"}.intersection(reason_codes)
        )
        fail_closed = bool(policy_ast.fail_closed or policy_ast.ambiguity or unsupported_intent or requested_market_order)
        human_review_required = bool(policy_ast.human_review_required or policy_ast.intent.human_review_required or fail_closed)
        status: PolicyCompilationStatus = "blocked" if fail_closed else ("review_required" if human_review_required else "ready")
        orderable = status == "ready"
        safe_broker = policy.broker if policy.broker in {BrokerMode.mock, BrokerMode.paper} else BrokerMode.mock
        preferred_symbols = _compile_symbol_constraints(policy.preferred_symbols)
        blocklist = _compile_symbol_constraints(policy.blocklist)

        semantic_policy = SemanticPolicy(
            policy_id=policy.policy_id,
            user_id=policy.user_id,
            policy_version=policy.version,
            direction=_compile_direction(policy_ast),
            long_only=policy_ast.long_only,
            cash_raise=policy_ast.cash_raise,
            trim_sector=policy_ast.trim_sector,
            horizon=_compile_horizon(policy.rebalance_frequency),
            confidence=_compile_confidence(policy_ast, reason_codes, fail_closed=fail_closed),
            universe=UniverseConstraints(
                market=policy.market,
                preferred_symbols=preferred_symbols,
                preferred_themes=list(policy.preferred_themes),
                preferred_sectors=list(policy.preferred_sectors),
                blocklist=blocklist,
                max_positions=policy.max_positions,
                min_avg_daily_value=policy.min_avg_daily_value,
            ),
            forbidden=ForbiddenConstraints(
                no_short_supported=policy_ast.no_short_supported,
                requested_market_order=requested_market_order,
                allowed_order_types=[OrderType.limit],
                forbidden_order_types=[OrderType.market],
                forbidden_intents=forbidden_intents,
                reason_codes=reason_codes,
            ),
            risk_budget=RiskBudget(
                risk_profile=policy.risk_profile,
                max_positions=policy.max_positions,
                max_position_weight=policy.max_position_weight,
                max_sector_weight=policy.max_sector_weight,
                min_cash_weight=policy.min_cash_weight,
                daily_loss_limit=policy.daily_loss_limit,
                monthly_loss_limit=policy.monthly_loss_limit,
                single_order_cash_limit=policy.single_order_cash_limit,
                max_daily_orders=policy.max_daily_orders,
                max_daily_turnover=policy.max_daily_turnover,
            ),
            execution_mode=policy.execution_mode,
            broker_mode=safe_broker,
            data_mode=policy_ast.data_mode,
            allowed_order_types=[OrderType.limit],
            ambiguity=policy_ast.ambiguity,
            unsupported_intent=unsupported_intent,
            human_review_required=human_review_required,
            fail_closed=fail_closed,
            orderable=orderable,
            order_submission_enabled=False,
            blocked_reasons=reason_codes if fail_closed or human_review_required else [],
            warnings=list(policy_ast.warnings),
        )

        return PolicyCompilationResult(
            policy_ast=policy_ast,
            semantic_policy=semantic_policy,
            status=status,
            orderable=orderable,
            fail_closed=fail_closed,
            human_review_required=human_review_required,
            blocked_reasons=semantic_policy.blocked_reasons,
            warnings=semantic_policy.warnings,
            data_mode=policy_ast.data_mode,
            live_trading_enabled=False,
            broker_mode=safe_broker,
        )


def _compile_direction(policy_ast: PolicyAST) -> InvestmentDirection:
    if policy_ast.intent.intent_type == "cash_raise":
        return "cash_raise"
    if policy_ast.intent.intent_type == "trim_sector":
        return "trim_sector"
    if policy_ast.intent.intent_type == "hold":
        return "hold"
    if policy_ast.intent.intent_type == "unsupported" or policy_ast.unsupported_intent:
        return "unsupported"
    return "accumulate"


def _compile_horizon(rebalance_frequency: str) -> PolicyHorizon:
    normalized = rebalance_frequency.strip().lower()
    if normalized in {"daily", "weekly", "monthly"}:
        return normalized  # type: ignore[return-value]
    return "unspecified"


def _compile_confidence(policy_ast: PolicyAST, reason_codes: list[str], *, fail_closed: bool) -> float:
    if policy_ast.unsupported_intent or "live_trading_not_supported" in reason_codes:
        return 0.0
    if "short_not_supported" in reason_codes or "inverse_not_supported" in reason_codes:
        return 0.0
    if policy_ast.ambiguity:
        return 0.20
    if "market_orders_disabled" in reason_codes:
        return 0.35
    if fail_closed:
        return 0.25

    score = 0.75
    if (
        policy_ast.policy.preferred_symbols
        or policy_ast.policy.preferred_themes
        or policy_ast.policy.preferred_sectors
        or policy_ast.policy.blocklist
    ):
        score += 0.10
    if policy_ast.cash_raise or policy_ast.trim_sector:
        score += 0.05
    if policy_ast.policy.risk_profile in {"conservative", "moderate", "aggressive"}:
        score += 0.05
    return min(score, 0.95)


def _compile_forbidden_intents(reason_codes: list[str]) -> list[str]:
    mapping = {
        "short_not_supported": "short",
        "inverse_not_supported": "inverse",
        "live_trading_not_supported": "live_trading",
        "market_orders_disabled": "market_order",
        "automation_requires_separate_enablement": "automation_without_enablement",
    }
    return _unique([mapping[reason] for reason in reason_codes if reason in mapping])


def _compile_symbol_constraints(symbols: list[str]) -> list[str]:
    action_tokens = {"BUY", "SELL", "HOLD", "SHORT", "INVERSE", "TRIM", "RAISE"}
    return [symbol for symbol in symbols if symbol not in action_tokens]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
