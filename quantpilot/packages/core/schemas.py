from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class ExecutionMode(str, Enum):
    backtest_only = "backtest_only"
    paper_trading = "paper_trading"
    approval_required = "approval_required"
    guarded_autopilot = "guarded_autopilot"
    fully_automated = "fully_automated"


class SignalAction(str, Enum):
    buy_ready = "buy_ready"
    buy_wait = "buy_wait"
    hold = "hold"
    trim = "trim"
    exit = "exit"
    watch = "watch"
    blocked = "blocked"


class OrderStatus(str, Enum):
    draft = "draft"
    risk_checked = "risk_checked"
    proposed = "proposed"
    modified = "modified"
    user_approved = "user_approved"
    submitted = "submitted"
    accepted = "accepted"
    partially_filled = "partially_filled"
    filled = "filled"
    cancelled = "cancelled"
    rejected = "rejected"
    expired = "expired"
    failed = "failed"


class BrokerMode(str, Enum):
    mock = "mock"
    paper = "paper"
    live_disabled = "live_disabled"


class DataMode(str, Enum):
    fixture = "fixture"
    local_historical = "local_historical"
    external_historical = "external_historical"
    realtime_market_data = "realtime_market_data"
    paper_trading = "paper_trading"
    live_trading = "live_trading"


class OrderType(str, Enum):
    limit = "limit"
    market = "market"


class StrategyStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    disabled = "disabled"


class HarnessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserPolicy(HarnessModel):
    policy_id: str = Field(default_factory=lambda: new_id("pol"))
    user_id: str = "fixture-user"
    version: int = 1
    market: str = "KR_STOCK"
    risk_profile: str = "moderate"
    max_positions: int = Field(default=8, gt=0)
    max_position_weight: float = Field(default=0.15, gt=0, le=1)
    max_sector_weight: float = Field(default=0.40, gt=0, le=1)
    min_cash_weight: float = Field(default=0.20, ge=0, lt=1)
    daily_loss_limit: float = -0.03
    monthly_loss_limit: float = -0.05
    max_daily_orders: int = Field(default=3, gt=0)
    max_daily_turnover: float = Field(default=3_000_000, gt=0)
    monthly_loss_pause_new_buys: float = -0.05
    monthly_loss_stop_all_autotrading: float = -0.10
    stale_quote_max_age_seconds: int = Field(default=30, gt=0)
    human_review_quote_max_age_seconds: int = Field(default=120, gt=0)
    order_expiry_minutes: int = Field(default=30, gt=0)
    authority_level: int = Field(default=2, ge=1, le=5)
    kill_switch_engaged: bool = False
    guarded_autopilot_enabled: bool = False
    fully_automated_operator_enabled: bool = False
    single_order_cash_limit: float = Field(default=1_000_000, gt=0)
    rebalance_frequency: str = "weekly"
    execution_mode: ExecutionMode = ExecutionMode.approval_required
    allowed_order_types: list[OrderType] = Field(default_factory=lambda: [OrderType.limit])
    broker: BrokerMode = BrokerMode.mock
    preferred_themes: list[str] = Field(default_factory=list)
    preferred_sectors: list[str] = Field(default_factory=list)
    blocklist: list[str] = Field(default_factory=list)
    min_avg_daily_value: float = Field(default=5_000_000, ge=0)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("daily_loss_limit", "monthly_loss_limit", "monthly_loss_pause_new_buys", "monthly_loss_stop_all_autotrading")
    @classmethod
    def loss_limits_must_be_negative(cls, value: float) -> float:
        if not -1 < value < 0:
            raise ValueError("loss limits must be negative fractions between -1 and 0")
        return value

    @field_validator("preferred_themes", "preferred_sectors")
    @classmethod
    def normalize_lowercase_lists(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().lower() for item in value if item.strip()})

    @field_validator("blocklist")
    @classmethod
    def normalize_blocklist(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().upper() for item in value if item.strip()})

    @model_validator(mode="after")
    def validate_weights(self) -> "UserPolicy":
        if self.max_position_weight > 1 - self.min_cash_weight:
            raise ValueError("max_position_weight must leave room for min_cash_weight")
        if self.max_sector_weight < self.max_position_weight:
            raise ValueError("max_sector_weight cannot be below max_position_weight")
        if self.monthly_loss_limit > self.daily_loss_limit:
            raise ValueError("monthly_loss_limit must be at least as conservative as daily_loss_limit")
        if self.monthly_loss_pause_new_buys > self.daily_loss_limit:
            raise ValueError("monthly_loss_pause_new_buys must be at least as conservative as daily_loss_limit")
        if self.monthly_loss_stop_all_autotrading > self.monthly_loss_pause_new_buys:
            raise ValueError("monthly_loss_stop_all_autotrading must be at least as conservative as monthly_loss_pause_new_buys")
        if self.human_review_quote_max_age_seconds < self.stale_quote_max_age_seconds:
            raise ValueError("human_review_quote_max_age_seconds cannot be below stale_quote_max_age_seconds")
        if not self.allowed_order_types:
            raise ValueError("at least one order type must be allowed")
        return self


class StrategyRecipe(HarnessModel):
    strategy_id: str
    version: str
    description: str | None = None
    market: str = "KR_STOCK"
    timeframe: str = "daily"
    universe_filter: dict[str, Any] = Field(default_factory=dict)
    features: list[dict[str, Any]] = Field(default_factory=list)
    entry_rules: list[str]
    exit_rules: list[str]
    no_chasing_rules: dict[str, Any] = Field(default_factory=dict)
    position_sizing: dict[str, Any]
    risk_rules: list[str]
    rebalance: str
    execution_permissions: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    promotion_status: Literal["draft", "approved", "validated_l3", "validated_l4", "revoked"] = "draft"
    allowed_execution_levels: list[str] = Field(default_factory=list)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)
    status: StrategyStatus = StrategyStatus.draft

    @field_validator("allowed_execution_levels", mode="before")
    @classmethod
    def normalize_execution_levels(cls, value: Any) -> list[str]:
        if value is None:
            return []
        normalized: list[str] = []
        for item in value:
            raw = str(item).strip().lower()
            if raw in {"3", "level3", "level_3", "approval_required"}:
                normalized.append("level_3")
            elif raw in {"4", "level4", "level_4", "guarded_autopilot"}:
                normalized.append("guarded_autopilot" if raw == "guarded_autopilot" else "level_4")
            else:
                normalized.append(raw)
        return normalized

    @model_validator(mode="after")
    def validate_strategy_permissions(self) -> "StrategyRecipe":
        method = str(self.position_sizing.get("method", "")).strip()
        if method not in {"capped_target_weight", "capped_score_weight", "inverse_volatility"}:
            raise ValueError("position_sizing.method is not implemented")

        earned_levels = {
            "draft": set(),
            "revoked": set(),
            "validated_l3": {"level_3"},
            "approved": {"level_3", "level_4", "guarded_autopilot"},
            "validated_l4": {"level_3", "level_4", "guarded_autopilot"},
        }[self.promotion_status]
        requested_levels = set(self.allowed_execution_levels)
        if not requested_levels.issubset(earned_levels):
            raise ValueError("allowed_execution_levels exceeds promotion_status")
        return self


class Signal(HarnessModel):
    signal_id: str = Field(default_factory=lambda: new_id("sig"))
    strategy_id: str
    recipe_version: str
    symbol: str
    ticker: str | None = None
    signal_date: date = Field(default_factory=lambda: utc_now().date())
    action: SignalAction
    strength: float = Field(default=0.0, ge=0, le=1)
    technical_score: float | None = Field(default=None, ge=0, le=100)
    quant_score: float | None = Field(default=None, ge=0, le=100)
    target_weight_hint: float | None = Field(default=None, ge=0, le=1)
    stop_price_hint: float | None = Field(default=None, gt=0)
    take_profit_hint: float | None = Field(default=None, gt=0)
    valid_until: date | None = None
    policy_version: int | None = None
    reason_codes: list[str] = Field(default_factory=list)
    reason: str
    generated_at: datetime = Field(default_factory=utc_now)
    source: str = "fixture_signal_stub"

    @model_validator(mode="after")
    def mirror_symbol_to_ticker(self) -> "Signal":
        if self.ticker is None:
            self.ticker = self.symbol
        return self


class CandidateUniverseItem(HarnessModel):
    ticker: str
    name: str
    market: str
    sector: str
    theme_match: bool
    liquidity_pass: bool
    data_ready: bool
    block_reason: str | None = None
    analyst_required: bool


class TechnicalIndicatorSnapshot(HarnessModel):
    ticker: str
    signal_date: date
    close: float = Field(gt=0)
    moving_averages: dict[str, float]
    returns: dict[str, float]
    volatility: float = Field(ge=0)
    rsi: float = Field(ge=0, le=100)
    volume_ratio: float = Field(ge=0)
    momentum_score: float = Field(ge=0, le=100)
    technical_score: float = Field(ge=0, le=100)
    liquidity_score: float = Field(ge=0, le=100)
    defensive_score: float = Field(ge=0, le=100)
    data_points: int = Field(ge=1)


class AnalystReport(HarnessModel):
    ticker: str
    rating: Literal["positive", "neutral", "caution", "blocked"]
    confidence: float = Field(ge=0, le=1)
    summary: str
    investment_thesis: list[str]
    catalysts: list[str]
    financial_snapshot: dict[str, Any]
    valuation_view: str
    technical_view: str
    operation_view: str
    watch_conditions: list[str]
    data_as_of: date


class RebalanceSuggestion(HarnessModel):
    ticker: str
    current_weight: float = Field(ge=0, le=1)
    target_weight_suggestion: float = Field(ge=0, le=1)
    cash_target: float = Field(ge=0, le=1)
    risk_reason: str
    suggested_action: Literal["buy", "sell", "hold", "blocked"]


class RebalanceSuggestionReport(HarnessModel):
    policy_id: str
    policy_version: int
    portfolio_plan: "PortfolioPlan"
    suggestions: list[RebalanceSuggestion]
    order_submission_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class PortfolioPosition(HarnessModel):
    symbol: str
    quantity: float = Field(ge=0)
    market_price: float = Field(gt=0)
    sector: str = "unknown"

    @property
    def market_value(self) -> float:
        return self.quantity * self.market_price


class PortfolioSnapshot(HarnessModel):
    snapshot_id: str = Field(default_factory=lambda: new_id("snap"))
    user_id: str = "fixture-user"
    cash: float = Field(ge=0)
    equity: float = Field(gt=0)
    positions: list[PortfolioPosition] = Field(default_factory=list)
    daily_loss_ratio: float = 0.0
    monthly_loss_ratio: float = 0.0
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "fixture_portfolio"

    @model_validator(mode="after")
    def cash_cannot_exceed_equity_by_large_margin(self) -> "PortfolioSnapshot":
        position_value = sum(position.market_value for position in self.positions)
        if self.cash + position_value > self.equity * 1.05:
            raise ValueError("cash plus position value is inconsistent with equity")
        return self

    @property
    def cash_weight(self) -> float:
        return self.cash / self.equity


class OrderIntent(HarnessModel):
    intent_id: str = Field(default_factory=lambda: new_id("intent"))
    symbol: str
    side: Literal["buy", "sell"]
    order_type: OrderType = OrderType.limit
    quantity: float = Field(gt=0)
    limit_price: float | None = Field(default=None, gt=0)
    notional: float = Field(gt=0)
    target_weight: float = Field(ge=0, le=1)
    reason: str
    quote_time: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def limit_orders_need_limit_price(self) -> "OrderIntent":
        if self.order_type == OrderType.limit and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        return self


class PortfolioPlan(HarnessModel):
    plan_id: str = Field(default_factory=lambda: new_id("pplan"))
    policy_id: str
    policy_version: int
    target_weights: dict[str, float]
    cash_target_weight: float = Field(ge=0, le=1)
    order_intents: list[OrderIntent]
    created_at: datetime = Field(default_factory=utc_now)


class RiskCheck(HarnessModel):
    risk_check_id: str = Field(default_factory=lambda: new_id("risk"))
    order_plan_id: str
    passed: bool
    passed_checks: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    policy_version: int
    idempotency_key: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime = Field(default_factory=lambda: utc_now() + timedelta(minutes=10))


class ProposalExplanation(HarnessModel):
    symbol: str
    action: Literal["buy", "sell"]
    quantity: float
    target_weight_delta: float
    reference_price: float
    estimated_cash_impact: float
    strategy_id: str
    strategy_version: str
    signal_reason: str
    reason_codes: list[str] = Field(default_factory=list)
    current_weight: float
    target_weight: float
    weight_delta: float
    quote_price: float
    quote_age_seconds: float
    limit_price: float | None = None
    estimated_notional: float
    estimated_cost_bps: float = 0.0
    stop_price_hint: float | None = None
    take_profit_hint: float | None = None
    risk_checks_passed: list[str] = Field(default_factory=list)
    risk_checks_failed: list[str] = Field(default_factory=list)
    risk_check_id: str | None = None
    risk_check_expires_at: datetime | None = None
    idempotency_key: str
    policy_version: int
    warnings: list[str] = Field(default_factory=list)


class AuthorityCheckStep(HarnessModel):
    check_name: str
    passed: bool
    detail: str


class AuthorityCheckResult(HarnessModel):
    authorized: bool
    policy_version: int
    steps: list[AuthorityCheckStep]
    first_failed_check: str | None = None


class GuardrailState(HarnessModel):
    daily_order_count: int = 0
    daily_turnover_used: float = 0.0
    monthly_loss_pause_active: bool = False
    monthly_loss_stop_active: bool = False
    kill_switch_engaged: bool = False
    broker_healthy: bool = True
    last_broker_heartbeat: datetime | None = Field(default_factory=utc_now)
    last_broker_error_at: datetime | None = None
    autopilot_paused: bool = False
    last_blocked_reason: str | None = None
    unfilled_order_keys: list[str] = Field(default_factory=list)
    submitted_idempotency_keys: list[str] = Field(default_factory=list)


class OrderPlan(HarnessModel):
    order_plan_id: str = Field(default_factory=lambda: new_id("oplan"))
    policy_id: str
    policy_version: int
    intent: OrderIntent
    status: OrderStatus = OrderStatus.draft
    idempotency_key: str
    risk_check_id: str | None = None
    risk_check_expires_at: datetime | None = None
    explanation: ProposalExplanation | None = None
    auto_order_reference_price: float | None = None
    replaces_order_plan_id: str | None = None
    blocked_reason: str | None = None
    approved_by: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def idempotency_key_required(self) -> "OrderPlan":
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        return self


class BrokerOrder(HarnessModel):
    broker_order_id: str = Field(default_factory=lambda: new_id("bord"))
    order_plan_id: str
    broker_mode: BrokerMode
    status: OrderStatus = OrderStatus.accepted
    accepted_at: datetime = Field(default_factory=utc_now)
    broker_reference: str | None = None


class Fill(HarnessModel):
    fill_id: str = Field(default_factory=lambda: new_id("fill"))
    broker_order_id: str
    order_plan_id: str
    symbol: str
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    notional: float = Field(gt=0)
    filled_at: datetime = Field(default_factory=utc_now)


class AuditLogEvent(HarnessModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    user_id: str
    entity_type: str
    entity_id: str
    action: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)
    source: str


class OperationReport(HarnessModel):
    report_id: str = Field(default_factory=lambda: new_id("rpt"))
    user_id: str
    policy_id: str
    summary: dict[str, Any]
    order_plan_ids: list[str] = Field(default_factory=list)
    fill_ids: list[str] = Field(default_factory=list)
    audit_event_count: int
    live_trading_enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)
