from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from quantpilot.packages.core.schemas import DataMode, HarnessModel, Signal, utc_now
from quantpilot.packages.core.signals.types import CalibratedSignalSet


ProviderState = Literal["available", "unavailable", "stale"]


class ProviderStatus(HarnessModel):
    provider_name: str
    state: ProviderState = "available"
    data_mode: DataMode = DataMode.fixture
    reason: str | None = None
    observed_at: datetime = Field(default_factory=utc_now)
    as_of: datetime | None = None
    stale_after_seconds: int | None = Field(default=None, ge=0)
    observed_age_seconds: float | None = Field(default=None, ge=0)


class MarketDataQuality(HarnessModel):
    usable: bool = True
    degraded: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    symbol_count: int = Field(default=0, ge=0)
    data_mode: DataMode = DataMode.fixture


class OHLCVSnapshot(HarnessModel):
    bars: list[dict[str, Any]]
    provider_status: ProviderStatus
    data_quality: MarketDataQuality


class Quote(HarnessModel):
    symbol: str
    last: float = Field(gt=0)
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    as_of: datetime = Field(default_factory=utc_now)


class QuoteSnapshot(HarnessModel):
    quotes: dict[str, Quote]
    provider_status: ProviderStatus
    data_quality: MarketDataQuality


class L2Snapshot(HarnessModel):
    symbol: str
    bids: list[dict[str, float]] = Field(default_factory=list)
    asks: list[dict[str, float]] = Field(default_factory=list)
    as_of: datetime = Field(default_factory=utc_now)


class SignalSet(HarnessModel):
    signals: list[Signal]
    provider_status: dict[str, ProviderStatus]
    data_quality: MarketDataQuality
    calibrated_signal_set: CalibratedSignalSet | None = None
    order_submission_enabled: bool = False
    source: str = "provider_bound_signal_engine"
