from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from quantpilot.packages.core.schemas import HarnessModel, SignalAction


class BacktestAssumptions(HarnessModel):
    fee_bps: float = Field(default=15.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)
    sell_tax_bps: float = Field(default=0.0, ge=0)
    fill_model: Literal["next_open_limit_touch"] = "next_open_limit_touch"
    allow_fractional_shares: bool = True
    min_trade_notional: float = Field(default=1.0, ge=0)
    annualization_days: int = Field(default=252, gt=0)
    min_trading_days: int = Field(default=20, gt=0)
    min_filled_trades: int = Field(default=3, ge=0)


class BacktestSignal(HarnessModel):
    symbol: str
    signal_date: date
    action: SignalAction
    strength: float = Field(default=0.0, ge=0, le=1)
    target_weight_hint: float | None = Field(default=None, ge=0, le=1)
    limit_price: float | None = Field(default=None, gt=0)
    reason: str = "backtest_signal"
    source: str = "deterministic_backtest_input"

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("symbol is required")
        return cleaned


class BacktestRequest(HarnessModel):
    strategy_id: str
    recipe_version: str
    signals: list[BacktestSignal]
    initial_cash: float = Field(default=10_000_000.0, ge=0)
    initial_positions: dict[str, float] = Field(default_factory=dict)
    assumptions: BacktestAssumptions = Field(default_factory=BacktestAssumptions)
    start_date: date | None = None
    end_date: date | None = None
    tested_variants: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("strategy_id", "recipe_version")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value is required")
        return cleaned

    @field_validator("signals", mode="before")
    @classmethod
    def normalize_signal_inputs(cls, value: Any) -> Any:
        if value is None:
            return value
        allowed = {"symbol", "signal_date", "action", "strength", "target_weight_hint", "limit_price", "reason", "source"}
        normalized: list[Any] = []
        for item in value:
            if isinstance(item, BacktestSignal):
                normalized.append(item)
                continue
            if hasattr(item, "model_dump"):
                raw = item.model_dump()
            elif isinstance(item, dict):
                raw = dict(item)
            else:
                normalized.append(item)
                continue
            if "symbol" not in raw and "ticker" in raw:
                raw["symbol"] = raw["ticker"]
            normalized.append({key: raw[key] for key in allowed if key in raw})
        return normalized

    @field_validator("initial_positions")
    @classmethod
    def normalize_positions(cls, value: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for symbol, quantity in value.items():
            cleaned = symbol.strip().upper()
            if not cleaned:
                raise ValueError("initial position symbol is required")
            if quantity < 0:
                raise ValueError("initial position quantities must be non-negative")
            if quantity > 0:
                normalized[cleaned] = float(quantity)
        return normalized

    @model_validator(mode="after")
    def validate_date_range(self) -> "BacktestRequest":
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date cannot be after end_date")
        return self


class BacktestTrade(HarnessModel):
    symbol: str
    side: Literal["buy", "sell", "none"]
    status: Literal["filled", "blocked"]
    signal_date: date
    fill_date: date | None = None
    signal_price: float | None = Field(default=None, ge=0)
    limit_price: float | None = Field(default=None, ge=0)
    fill_price: float | None = Field(default=None, ge=0)
    quantity: float = Field(default=0.0, ge=0)
    notional: float = Field(default=0.0, ge=0)
    fees: float = Field(default=0.0, ge=0)
    slippage_cost: float = Field(default=0.0, ge=0)
    tax: float = Field(default=0.0, ge=0)
    target_weight: float = Field(default=0.0, ge=0, le=1)
    realized_pnl: float | None = None
    blocked_reason: str | None = None
    reason: str = "backtest_trade"


class BacktestEquityPoint(HarnessModel):
    date: date
    cash: float = Field(ge=0)
    positions_value: float = Field(ge=0)
    gross_exposure: float = Field(ge=0)
    equity: float = Field(ge=0)
    daily_return: float = 0.0
    positions: dict[str, float] = Field(default_factory=dict)


class BacktestMetrics(HarnessModel):
    total_return: float
    annualized_return: float | None
    max_drawdown: float = Field(ge=0)
    volatility: float = Field(ge=0)
    simplified_sharpe: float
    turnover: float = Field(ge=0)
    hit_rate: float = Field(ge=0, le=1)
    exposure: float = Field(ge=0)
    cash_utilization: float = Field(ge=0)
    number_of_rebalances: int = Field(ge=0)
    number_of_blocked_trades: int = Field(ge=0)
    filled_trades: int = Field(ge=0)
    final_cash: float = Field(ge=0)
    final_gross_exposure: float = Field(ge=0)


class BacktestResult(HarnessModel):
    result_id: str
    strategy_id: str
    recipe_version: str
    dataset_hash: str
    input_hash: str
    input_summary: dict[str, Any]
    assumptions: BacktestAssumptions
    start_date: date | None
    end_date: date | None
    equity_curve: list[BacktestEquityPoint]
    trades: list[BacktestTrade]
    metrics: BacktestMetrics
    warnings: list[str] = Field(default_factory=list)
    research_only: bool = True
    live_trading_approval: bool = False


class BacktestWindow(HarnessModel):
    window_id: str
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    train_days: int = Field(gt=0)
    test_days: int = Field(gt=0)


class AcceptanceThresholds(HarnessModel):
    min_total_return: float | None = None
    min_annualized_return: float | None = None
    max_drawdown: float | None = None
    min_simplified_sharpe: float | None = None
    min_filled_trades: int | None = Field(default=None, ge=0)
    max_turnover: float | None = Field(default=None, ge=0)


class AcceptanceCheck(HarnessModel):
    name: str
    passed: bool
    observed: float | int | None
    threshold: float | int | None
    detail: str


class AcceptanceEvaluation(HarnessModel):
    passed: bool
    checks: list[AcceptanceCheck]
    warnings: list[str] = Field(default_factory=list)
    research_only: bool = True
    live_trading_approval: bool = False
