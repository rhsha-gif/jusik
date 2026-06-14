from __future__ import annotations

from typing import Literal

from pydantic import Field

from quantpilot.packages.core.schemas import CandidateUniverseItem, HarnessModel


RankingComponentName = Literal[
    "theme",
    "sector",
    "liquidity",
    "data_quality",
    "volatility",
    "correlation",
    "existing_exposure",
    "fundamental_availability",
]


class CandidateScore(HarnessModel):
    theme: float = Field(ge=0, le=100)
    sector: float = Field(ge=0, le=100)
    liquidity: float = Field(ge=0, le=100)
    data_quality: float = Field(ge=0, le=100)
    volatility: float = Field(ge=0, le=100)
    correlation: float = Field(ge=0, le=100)
    existing_exposure: float = Field(ge=0, le=100)
    fundamental_availability: float = Field(ge=0, le=100)
    final_score: float = Field(ge=0, le=100)


class RankingExplanation(HarnessModel):
    summary: str
    component_explanations: dict[RankingComponentName, str]
    unavailable_data: list[RankingComponentName] = Field(default_factory=list)
    degraded_reason_codes: list[str] = Field(default_factory=list)


class RankedCandidate(HarnessModel):
    candidate: CandidateUniverseItem
    score: CandidateScore
    explanation: RankingExplanation
    score_rank: int
    selected_rank: int | None = None
    selected: bool = False
    exclusion_reason: str | None = None

