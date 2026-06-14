from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from quantpilot.packages.core.schemas import DataMode, HarnessModel, PortfolioSnapshot, Signal, utc_now


OptimizationStatus = Literal["optimized", "no_trade", "fail_closed", "unavailable"]


class ExpectedReturnRiskProxy(HarnessModel):
    symbol: str
    expected_return: float = 0.0
    volatility: float = Field(default=0.0, ge=0)
    expected_return_source: str = "uncalibrated_signal_strength"
    volatility_source: str = "uncalibrated_signal_proxy"
    calibrated: bool = False
    data_mode: DataMode = DataMode.fixture
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        return symbol


class OptimizationConstraints(HarnessModel):
    max_position_weight: float = Field(gt=0, le=1)
    max_sector_weight: float = Field(gt=0, le=1)
    min_cash_weight: float = Field(ge=0, lt=1)
    max_turnover_weight: float = Field(default=1.0, ge=0, le=2)
    rebalance_band: float = Field(default=0.0, ge=0, le=1)
    max_order_weight: float | None = Field(default=None, gt=0, le=1)


class OptimizationInput(HarnessModel):
    signals: list[Signal]
    proxies: dict[str, ExpectedReturnRiskProxy]
    sector_metadata: dict[str, str] = Field(default_factory=dict)
    snapshot: PortfolioSnapshot
    constraints: OptimizationConstraints
    risk_budget: dict[str, float] = Field(default_factory=dict)
    data_mode: DataMode = DataMode.fixture
    proxy_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sector_metadata")
    @classmethod
    def normalize_sector_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        return {symbol.strip().upper(): sector.strip().lower() for symbol, sector in value.items() if symbol.strip()}

    @field_validator("proxies")
    @classmethod
    def normalize_proxy_keys(cls, value: dict[str, ExpectedReturnRiskProxy]) -> dict[str, ExpectedReturnRiskProxy]:
        return {symbol.strip().upper(): proxy for symbol, proxy in value.items()}


class TargetWeight(HarnessModel):
    symbol: str
    sector: str
    current_weight: float = Field(ge=0, le=1)
    target_weight: float = Field(ge=0, le=1)
    expected_return: float
    volatility: float = Field(ge=0)
    score: float
    reason_codes: list[str] = Field(default_factory=list)
    constrained_by: list[str] = Field(default_factory=list)


class OptimizationResult(HarnessModel):
    status: OptimizationStatus
    target_weights: list[TargetWeight] = Field(default_factory=list)
    cash_target_weight: float = Field(ge=0, le=1)
    turnover_weight: float = Field(ge=0)
    reason_codes: list[str] = Field(default_factory=list)
    constraints_applied: list[str] = Field(default_factory=list)
    proxy_metadata: dict[str, Any] = Field(default_factory=dict)
    order_submission_enabled: bool = False
    source: str = "deterministic_portfolio_optimizer"
    created_at: datetime = Field(default_factory=utc_now)
