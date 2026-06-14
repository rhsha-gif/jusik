from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from quantpilot.packages.core.schemas import BrokerMode, DataMode, ExecutionMode, HarnessModel, OrderType, UserPolicy


PolicyIntentType = Literal["long_only_buy", "cash_raise", "trim_sector", "hold", "unsupported"]
PolicyASTStatus = Literal["ready", "blocked", "review_required"]


class PolicyIntent(HarnessModel):
    raw_text: str
    user_id: str = "fixture-user"
    intent_type: PolicyIntentType = "long_only_buy"
    market: str = "KR_STOCK"
    risk_profile: str = "moderate"
    preferred_symbols: list[str] = Field(default_factory=list)
    preferred_themes: list[str] = Field(default_factory=list)
    preferred_sectors: list[str] = Field(default_factory=list)
    blocklist: list[str] = Field(default_factory=list)
    long_only: bool = True
    cash_raise: bool = False
    trim_sector: bool = False
    no_short_supported: bool = True
    requested_order_types: list[OrderType] = Field(default_factory=lambda: [OrderType.limit])
    requested_execution_mode: ExecutionMode = ExecutionMode.approval_required
    broker: BrokerMode = BrokerMode.mock
    ambiguity: bool = False
    unsupported_intent: bool = False
    human_review_required: bool = False
    reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unsupported_or_ambiguous_requires_review(self) -> "PolicyIntent":
        if self.ambiguity or self.unsupported_intent:
            self.human_review_required = True
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PolicyAST(HarnessModel):
    intent: PolicyIntent
    policy: UserPolicy
    status: PolicyASTStatus = "ready"
    data_mode: DataMode = DataMode.fixture
    order_submission_enabled: bool = False
    live_trading_enabled: bool = False
    broker_mode: BrokerMode = BrokerMode.mock
    allowed_order_types: list[OrderType] = Field(default_factory=lambda: [OrderType.limit])
    long_only: bool = True
    cash_raise: bool = False
    trim_sector: bool = False
    no_short_supported: bool = True
    ambiguity: bool = False
    unsupported_intent: bool = False
    human_review_required: bool = False
    fail_closed: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def policy_ast_must_remain_non_executable(self) -> "PolicyAST":
        if self.order_submission_enabled:
            raise ValueError("policy AST cannot enable order submission")
        if self.live_trading_enabled:
            raise ValueError("policy AST cannot enable live trading")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
