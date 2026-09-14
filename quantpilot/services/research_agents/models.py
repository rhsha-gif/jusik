"""Pydantic shapes shared by the collectors, the pipelines and the publishers.

Numbers live here, computed by code. Agents receive these objects serialised
as JSON and are instructed to quote values verbatim, never to derive new ones.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SectorMove(BaseModel):
    name: str
    change_pct: float


class InvestorFlows(BaseModel):
    """Net buying by investor group, in 억원 (KRW 100M), positive = net buy."""

    foreign: float
    institution: float
    individual: float


class WatchlistRow(BaseModel):
    symbol: str
    name: str
    theme: str = ""
    close: float
    change_pct: float
    volume: int
    volume_ratio_20d: float | None = Field(
        default=None,
        description="Today's volume divided by the trailing 20-session mean; None when history is short.",
    )


class MarketSnapshot(BaseModel):
    date: str  # YYYY-MM-DD, KRX session date
    kospi_close: float
    kospi_change_pct: float
    kosdaq_close: float
    kosdaq_change_pct: float
    sector_top: list[SectorMove] = Field(default_factory=list)
    sector_bottom: list[SectorMove] = Field(default_factory=list)
    investor_flows: InvestorFlows
    watchlist_rows: list[WatchlistRow] = Field(default_factory=list)


class NewsItem(BaseModel):
    id: str  # "news:<sha1(link)[:10]>" — the only citation handle agents may use
    title: str
    link: str
    source_domain: str
    published_at: str
    query: str


class EvidenceSource(BaseModel):
    id: str
    fetched_at: str
    detail: str = ""


class EvidenceBundle(BaseModel):
    date: str
    collected_at: str
    snapshot: MarketSnapshot
    news: list[NewsItem] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)
    signal_input: Literal[False] = False  # constant by design: research never feeds signals


class AgentResult(BaseModel):
    agent: str
    model: str
    text: str
    json_output: dict[str, Any] | None = None
    elapsed_s: float
    exit_code: int


class SecurityFinding(BaseModel):
    id: str
    severity: Literal["low", "standard", "high", "critical"]
    path: str
    line: int | None = None
    rule: str
    evidence: str
    proposal: str


class SecurityVerdict(BaseModel):
    verdict: Literal["pass", "block"]
    findings: list[SecurityFinding] = Field(default_factory=list)
    files_opened: list[str] = Field(default_factory=list)

# --- Strategist team (macro / geopolitics) ------------------------------------


class MacroPoint(BaseModel):
    time: str  # sortable label: YYYY-MM-DD, YYYY-MM, YYYYQn or YYYY
    value: float


class MacroSeries(BaseModel):
    """One numeric series plus the derived statistics the agents may quote (all computed by code)."""

    id: str
    label: str
    source: Literal["ecos", "fred", "gpr"]
    code: str
    cycle: Literal["D", "M", "Q", "A"] = "M"
    unit: str = ""
    role: str = ""
    verified: bool = True
    points: list[MacroPoint] = Field(default_factory=list)
    latest: float | None = None
    latest_time: str | None = None
    change_3m: float | None = Field(default=None, description="latest minus the value ~3 months earlier, same unit")
    yoy_pct: float | None = Field(default=None, description="latest vs ~12 months earlier, percent; None for rates")
    percentile_5y: float | None = Field(default=None, description="rank of latest among the trailing ~5 years, 0-100")
    zscore_24m: float | None = None
    zscore_60m: float | None = None
    note: str = ""


class RegimeCall(BaseModel):
    quadrant: Literal[
        "Q1_growth_up_inflation_down",
        "Q2_growth_up_inflation_up",
        "Q3_growth_down_inflation_up",
        "Q4_growth_down_inflation_down",
        "undetermined",
    ]
    growth_score: float | None = None
    inflation_score: float | None = None
    growth_inputs: list[str] = Field(default_factory=list)
    inflation_inputs: list[str] = Field(default_factory=list)
    method: str = ""
    note: str = ""


class GprSummary(BaseModel):
    as_of_month: str
    gpr: float
    gpr_percentile_10y: float | None = None
    gpr_change_3m: float | None = None
    gpr_threats: float | None = None
    gpr_acts: float | None = None
    gpr_korea: float | None = None
    gpr_korea_percentile_10y: float | None = None
    citation: str = ""
    note: str = ""


class MacroEvidence(BaseModel):
    as_of: str
    collected_at: str
    series: list[MacroSeries] = Field(default_factory=list)
    regime: RegimeCall | None = None
    gpr: GprSummary | None = None
    skipped: list[str] = Field(default_factory=list, description="sources not collected and why (no key, download failed)")
    sources: list[EvidenceSource] = Field(default_factory=list)
    signal_input: Literal[False] = False


class MacroEvidenceBundle(BaseModel):
    """What the strategist pipeline hands to its five agents; news is the same collector as the market brief."""

    date: str
    collected_at: str
    macro: MacroEvidence
    news: list[NewsItem] = Field(default_factory=list)
    open_decisions: list[str] = Field(default_factory=list, description="decision ids currently open in the ledger")
    signal_input: Literal[False] = False
