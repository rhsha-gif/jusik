"""Versioned operator policy; no credentials belong in this model."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

STRATEGIES = ("opening_range_breakout", "trend_pullback", "range_reversion")


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: int = Field(default=1, ge=1)
    data_mode: Literal["fixture", "paper_trading"] = "fixture"
    strategy_generation: Literal["legacy", "intraday_v2"] = "legacy"
    initial_capital: int = Field(default=5_000_000, gt=0, le=5_000_000)
    trade_risk: float = Field(default=0.005, gt=0, le=0.005)
    symbol_cap: float = Field(default=0.25, gt=0, le=0.25)
    strategy_cap: float = Field(default=0.60, gt=0, le=0.60)
    max_positions: int = Field(default=4, ge=1, le=4)
    daily_loss_limit: float = Field(default=0.01, gt=0, le=0.01)
    peak_drawdown_limit: float = Field(default=0.05, gt=0, le=0.05)
    fee_bps: float = Field(default=1.40527, ge=0, le=100)
    sell_tax_bps: float = Field(default=20, ge=0, le=100)
    slippage_bps: float = Field(default=5, ge=0, le=100)
    quote_ttl_seconds: int = Field(default=15, ge=1, le=30)
    cycle_seconds: int = Field(default=10, ge=1, le=60)
    entry_cutoff_minutes: int = Field(default=30, ge=20, le=120)
    liquidation_minutes: int = Field(default=20, ge=10, le=60)
    primary_ai: Literal["claude", "codex"] = "claude"
    ai_enabled: bool = False
    research_enabled: bool = False
    slack_enabled: bool = False
    sandbox_image: str = ""
    max_candidates: int = Field(default=20, ge=1, le=30)
    active_strategies: tuple[str, ...] = STRATEGIES

    @model_validator(mode="after")
    def valid(self):
        if self.strategy_generation == "intraday_v2":
            if self.max_positions > 2 or self.research_enabled:
                raise ValueError(
                    "intraday_v2 requires at most two positions and frozen strategies"
                )
        if self.entry_cutoff_minutes < self.liquidation_minutes:
            raise ValueError("entry cutoff must precede liquidation")
        if not self.active_strategies or len(set(self.active_strategies)) != len(
            self.active_strategies
        ):
            raise ValueError("unique active strategies required")
        if any(s not in STRATEGIES for s in self.active_strategies):
            raise ValueError("generated strategies use the verified lab registry")
        return self


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value


def environment_safe(env: dict[str, str]) -> bool:
    # Malformed flag values are not a safe disabled state.
    return all(
        env.get(k, "false").lower() == "false"
        for k in (
            "LIVE_TRADING_ENABLED",
            "MARKET_ORDERS_ENABLED",
            "GUARDED_AUTOPILOT_ENABLED",
            "FULLY_AUTOMATED_OPERATOR_ENABLED",
        )
    ) and env.get("BROKER_MODE", "mock") in {"mock", "paper"}
