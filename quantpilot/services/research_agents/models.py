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
