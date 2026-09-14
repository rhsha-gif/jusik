"""Pydantic shapes for market-structure evidence.

Numbers live here, computed by code in `analytics.market_structure`. Agents
receive these objects serialised as JSON and are instructed to quote values
verbatim, never to derive new ones. `signal_input` is the constant `False` by
type so the evidence can never be marked as a trading input.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BreadthPoint(BaseModel):
    window: int
    pct_above_sma: float
    count_above: int
    count_total: int


class VolatilityRegime(BaseModel):
    window: int
    realized_vol_annualized: float
    percentile_1y: float | None = None
    label: Literal["low", "normal", "high", "extreme"]


class CorrelationSummary(BaseModel):
    window: int
    mean_pairwise: float
    max_pairwise: float
    min_pairwise: float
    n_symbols: int
    label: Literal["dispersed", "normal", "crowded"]


class SectorMomentum(BaseModel):
    name: str
    change_pct: float
    rank: int  # 1 = strongest change_pct


class FlowZScore(BaseModel):
    group: Literal["foreign", "institution", "individual"]
    latest: float
    mean_60d: float
    stdev_60d: float
    z: float | None = None


class SymbolStructure(BaseModel):
    symbol: str
    name: str = ""
    sector: str = ""
    close: float
    ret_20d: float | None = None
    ret_60d: float | None = None
    ret_120d: float | None = None
    above_sma20: bool | None = None
    above_sma60: bool | None = None
    above_sma120: bool | None = None
    realized_vol_20d: float | None = None
    distance_from_high_252d_pct: float | None = None


class MarketStructureEvidence(BaseModel):
    as_of: str  # YYYY-MM-DD, last bar date used
    universe_size: int
    date_range: tuple[str, str]  # (first bar date, last bar date) actually used
    breadth: list[BreadthPoint] = Field(default_factory=list)
    volatility: VolatilityRegime
    correlation: CorrelationSummary | None = None
    sector_momentum: list[SectorMomentum] = Field(default_factory=list)
    flows: list[FlowZScore] = Field(default_factory=list)
    symbols: list[SymbolStructure] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    signal_input: Literal[False] = False  # constant by design: research never feeds signals
