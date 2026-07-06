from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator

from quantpilot.packages.core.schemas import DataMode, HarnessModel, OrderPlan, new_id, utc_now


class SlicingAlgorithm(str, Enum):
    twap = "twap"
    vwap = "vwap"
    pov = "pov"


class ExecutionStatus(str, Enum):
    blocked = "blocked"
    unavailable = "unavailable"
    simulated = "simulated"
    partially_filled = "partially_filled"
    filled = "filled"


ExecutionEventType = Literal[
    "slice_scheduled",
    "broker_acceptance_simulated",
    "queue_estimated",
    "adverse_selection_estimated",
    "partial_fill",
    "fill",
    "cancel_replace_requested",
    "cancel_replace_simulated",
    "blocked",
    "unavailable",
    "completed",
]


class ExecutionSimulatorConfig(HarnessModel):
    algorithm: SlicingAlgorithm = SlicingAlgorithm.twap
    slice_count: int = Field(default=4, gt=0)
    interval_seconds: int = Field(default=60, ge=0)
    start_at: datetime = Field(default_factory=utc_now)
    volume_curve: list[float] = Field(default_factory=list)
    max_participation_rate: float = Field(default=0.10, gt=0, le=1)
    expected_slice_volumes: list[float] = Field(default_factory=list)
    simulate_cancel_replace: bool = False
    cancel_replace_at_slice: int | None = Field(default=None, gt=0)
    data_mode: DataMode = DataMode.fixture

    @field_validator("volume_curve", "expected_slice_volumes")
    @classmethod
    def values_must_be_non_negative(cls, values: list[float]) -> list[float]:
        if any(value < 0 for value in values):
            raise ValueError("schedule inputs must be non-negative")
        return values


class ExecutionSlice(HarnessModel):
    slice_id: int = Field(ge=1)
    scheduled_at: datetime
    quantity: float = Field(ge=0)
    expected_volume: float | None = Field(default=None, ge=0)
    participation_rate: float | None = Field(default=None, ge=0, le=1)


class SliceSchedule(HarnessModel):
    order_plan_id: str
    algorithm: SlicingAlgorithm
    slices: list[ExecutionSlice]
    total_requested_quantity: float = Field(ge=0)
    total_scheduled_quantity: float = Field(ge=0)
    unscheduled_quantity: float = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class ExecutionEvent(HarnessModel):
    event_id: str = Field(default_factory=lambda: new_id("exec_evt"))
    event_type: ExecutionEventType
    order_plan_id: str
    symbol: str
    slice_id: int | None = None
    quantity: float | None = Field(default=None, ge=0)
    price: float | None = Field(default=None, gt=0)
    filled_quantity: float | None = Field(default=None, ge=0)
    remaining_quantity: float | None = Field(default=None, ge=0)
    fill_probability: float | None = Field(default=None, ge=0, le=1)
    queue_ahead_quantity: float | None = Field(default=None, ge=0)
    adverse_selection_bps: float | None = Field(default=None, ge=0)
    slippage_bps: float | None = None
    reason_code: str | None = None
    message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ExecutionSimulationRequest(HarnessModel):
    request_id: str = Field(default_factory=lambda: new_id("exec_req"))
    order_plan: OrderPlan
    config: ExecutionSimulatorConfig = Field(default_factory=ExecutionSimulatorConfig)
    requested_at: datetime = Field(default_factory=utc_now)


class ExecutionSimulationResult(HarnessModel):
    request_id: str
    order_plan_id: str
    symbol: str
    status: ExecutionStatus
    schedule: SliceSchedule
    events: list[ExecutionEvent]
    requested_quantity: float = Field(ge=0)
    filled_quantity: float = Field(ge=0)
    remaining_quantity: float = Field(ge=0)
    average_fill_price: float | None = Field(default=None, gt=0)
    estimated_slippage_bps: float = 0.0
    adverse_selection_bps: float = 0.0
    queue_ahead_quantity: float = 0.0
    broker_order_sent: bool = False
    live_trading_enabled: bool = False
    market_orders_enabled: bool = False
    data_mode: DataMode = DataMode.fixture
    provider_state: str = "available"
    completed_at: datetime = Field(default_factory=utc_now)
