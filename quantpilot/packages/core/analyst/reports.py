from __future__ import annotations

from typing import Literal, Protocol

from quantpilot.packages.core.schemas import AnalystReport, CandidateUniverseItem, Signal, TechnicalIndicatorSnapshot


AnalystRating = Literal["positive", "neutral", "caution", "blocked"]


class AnalystReportAdapter(Protocol):
    def generate(
        self,
        *,
        candidate: CandidateUniverseItem,
        indicator: TechnicalIndicatorSnapshot,
        signal: Signal | None = None,
    ) -> AnalystReport: ...


def _rating(candidate: CandidateUniverseItem, indicator: TechnicalIndicatorSnapshot) -> AnalystRating:
    if candidate.block_reason:
        return "blocked"
    if indicator.technical_score >= 70 and candidate.theme_match:
        return "positive"
    if indicator.technical_score < 40 or indicator.defensive_score < 35:
        return "caution"
    return "neutral"


def generate_analyst_report(
    *,
    candidate: CandidateUniverseItem,
    indicator: TechnicalIndicatorSnapshot,
    signal: Signal | None = None,
    adapter: AnalystReportAdapter | None = None,
) -> AnalystReport:
    if adapter is not None:
        return adapter.generate(candidate=candidate, indicator=indicator, signal=signal)

    rating = _rating(candidate, indicator)
    confidence = 0.35
    if candidate.data_ready:
        confidence += 0.25
    if candidate.liquidity_pass:
        confidence += 0.15
    if indicator.data_points >= 20:
        confidence += 0.15
    if candidate.theme_match:
        confidence += 0.10

    signal_context = f" Current signal action is {signal.action.value}." if signal is not None else ""
    block_context = f" Candidate is blocked by {candidate.block_reason}." if candidate.block_reason else ""

    return AnalystReport(
        ticker=candidate.ticker,
        rating=rating,
        confidence=round(min(confidence, 1.0), 2),
        summary=(
            f"{candidate.name} is a fixture-backed research candidate in {candidate.sector}."
            f"{signal_context}{block_context} This report is informational and does not submit orders."
        ),
        investment_thesis=[
            "Theme alignment is derived from the local candidate universe fixture.",
            "Technical trend and momentum are based only on rows available at the report date.",
        ],
        catalysts=[
            "Sustained volume above trailing average",
            "Improvement in moving-average trend and momentum scores",
        ],
        financial_snapshot={
            "source": "local_fixture",
            "revenue": "data_unavailable",
            "operating_margin": "data_unavailable",
            "net_debt": "data_unavailable",
        },
        valuation_view="Valuation data is unavailable in the local Level 1-2 fixture.",
        technical_view=(
            f"Technical score {indicator.technical_score:.1f}, RSI {indicator.rsi:.1f}, "
            f"volume ratio {indicator.volume_ratio:.2f}."
        ),
        operation_view="Level 1-2 can suggest research, timing, stops, targets, and rebalance weights only.",
        watch_conditions=[
            "Data readiness remains true",
            "Liquidity stays above policy minimum",
            "Signal action is reviewed separately from analyst rating",
        ],
        data_as_of=indicator.signal_date,
    )
