from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from quantpilot.packages.core.backtest.schemas import BacktestResult
from quantpilot.packages.core.schemas import HarnessModel


DataModeLabel = Literal[
    "fixture",
    "local_historical",
    "external_historical",
    "realtime_market_data",
    "paper_trading",
    "live_trading_candidate",
    "live_canary",
    "live_scaled",
]


class PurgeEmbargoMetadata(HarnessModel):
    purge_days: int = Field(ge=0)
    embargo_days: int = Field(ge=0)
    purge_start: date | None = None
    purge_end: date | None = None
    embargo_start: date | None = None
    embargo_end: date | None = None
    removed_between_train_and_test_days: int = Field(default=0, ge=0)
    embargoed_after_test_days: int = Field(default=0, ge=0)


class WalkForwardSplit(HarnessModel):
    split_id: str
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    train_days: int = Field(gt=0)
    test_days: int = Field(gt=0)
    purge_embargo: PurgeEmbargoMetadata


class ValidationRunResult(HarnessModel):
    run_id: str
    split: WalkForwardSplit
    status: Literal["completed", "unavailable"]
    data_mode: DataModeLabel = "fixture"
    backtest_result: BacktestResult | None = None
    metrics: dict[str, float | int | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    deterministic_research_only: Literal[True] = True
    promotion_allowed: Literal[False] = False
    live_trading_approval: Literal[False] = False


class SlippageScenarioResult(HarnessModel):
    scenario_id: str
    slippage_bps: float = Field(ge=0)
    status: Literal["completed", "unavailable"]
    result_id: str | None = None
    total_return: float | None = None
    max_drawdown: float | None = None
    simplified_sharpe: float | None = None
    filled_trades: int | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class SlippageSensitivityResult(HarnessModel):
    base_slippage_bps: float = Field(ge=0)
    data_mode: DataModeLabel = "fixture"
    status: Literal["completed", "partial", "unavailable"]
    scenarios: list[SlippageScenarioResult]
    worst_total_return: float | None = None
    best_total_return: float | None = None
    total_return_range: float | None = Field(default=None, ge=0)
    conservative_pass: Literal[False] = False
    warnings: list[str] = Field(default_factory=list)


class BenchmarkRelativeAttribution(HarnessModel):
    benchmark_label: str = "benchmark"
    status: Literal["completed", "unavailable"]
    start_date: date | None = None
    end_date: date | None = None
    matched_days: int = Field(default=0, ge=0)
    strategy_total_return: float | None = None
    benchmark_total_return: float | None = None
    excess_return: float | None = None
    average_daily_excess_return: float | None = None
    warnings: list[str] = Field(default_factory=list)


class ExtensionPointStatus(HarnessModel):
    name: Literal["survivorship_bias", "corporate_actions"]
    status: Literal["not_configured", "configured", "unavailable"] = "not_configured"
    implemented: bool = False
    required_for_promotion: bool = True
    detail: str


class DiagnosticPlaceholder(HarnessModel):
    name: Literal["pbo", "dsr"]
    status: Literal["placeholder"] = "placeholder"
    implemented: Literal[False] = False
    value: float | None = None
    required_for_promotion: bool = True
    detail: str


class PromotionEvidenceReport(HarnessModel):
    report_id: str
    strategy_id: str
    recipe_version: str
    data_mode: DataModeLabel = "fixture"
    validation_runs: list[ValidationRunResult] = Field(default_factory=list)
    slippage_sensitivity: SlippageSensitivityResult | None = None
    benchmark_attribution: BenchmarkRelativeAttribution | None = None
    survivorship_status: ExtensionPointStatus
    corporate_action_status: ExtensionPointStatus
    pbo: DiagnosticPlaceholder
    dsr: DiagnosticPlaceholder
    deterministic_only: Literal[True] = True
    promotion_allowed: Literal[False] = False
    human_review_required: Literal[True] = True
    research_only: Literal[True] = True
    live_trading_approval: Literal[False] = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
