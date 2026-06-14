from __future__ import annotations

from quantpilot.packages.core.schemas import PortfolioPosition, PortfolioSnapshot, UserPolicy
from quantpilot.packages.core.universe.builder import build_candidate_universe
from quantpilot.packages.core.universe.ranking import CandidateRankingEngine


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=8_000_000,
        equity=10_000_000,
        positions=[
            PortfolioPosition(symbol="CCC", quantity=10_000, market_price=100, sector="technology"),
        ],
    )


def test_candidate_ranking_scores_all_components_deterministically() -> None:
    securities = [
        {
            "ticker": "AAA",
            "name": "Alpha AI Semiconductors",
            "market": "US_STOCK",
            "sector": "technology",
            "themes": ["ai", "semiconductor"],
            "avg_daily_value": 20_000_000,
            "data_ready": True,
            "volatility": 0.15,
            "correlation_to_portfolio": 0.10,
            "fundamental_available": True,
        },
        {
            "ticker": "CCC",
            "name": "Core AI Software",
            "market": "US_STOCK",
            "sector": "technology",
            "themes": ["ai"],
            "avg_daily_value": 6_000_000,
            "data_ready": True,
            "volatility": 0.25,
            "correlation_to_portfolio": 0.20,
            "fundamental_score": 75,
        },
    ]
    policy = UserPolicy(
        preferred_themes=["ai", "semiconductor"],
        preferred_sectors=["technology"],
        min_avg_daily_value=5_000_000,
        max_position_weight=0.20,
    )

    ranked = CandidateRankingEngine(max_candidates=5).rank(
        policy=policy,
        candidates=build_candidate_universe(policy, securities),
        securities=securities,
        portfolio_snapshot=_snapshot(),
    )

    assert [item.candidate.ticker for item in ranked] == ["AAA", "CCC"]
    aaa = ranked[0]
    assert aaa.selected is True
    assert aaa.selected_rank == 1
    assert aaa.score.theme == 100
    assert aaa.score.sector == 100
    assert aaa.score.liquidity == 100
    assert aaa.score.data_quality == 100
    assert aaa.score.volatility == 85
    assert aaa.score.correlation == 90
    assert aaa.score.existing_exposure == 100
    assert aaa.score.fundamental_availability == 100
    assert aaa.score.final_score > ranked[1].score.final_score

    ccc = ranked[1]
    assert ccc.score.theme == 50
    assert ccc.score.existing_exposure == 50
    assert ccc.score.fundamental_availability == 75


def test_candidate_ranking_records_filter_exclusion_reasons() -> None:
    securities = [
        {
            "ticker": "AAA",
            "name": "Alpha AI",
            "market": "US_STOCK",
            "sector": "technology",
            "themes": ["ai"],
            "avg_daily_value": 10_000_000,
            "data_ready": True,
        },
        {
            "ticker": "BBB",
            "name": "Blocked Dividend Bank",
            "market": "US_STOCK",
            "sector": "financials",
            "themes": ["dividend"],
            "avg_daily_value": 10_000_000,
            "data_ready": True,
        },
    ]
    policy = UserPolicy(preferred_themes=["ai"], min_avg_daily_value=5_000_000)

    ranked = CandidateRankingEngine().rank(
        policy=policy,
        candidates=build_candidate_universe(policy, securities),
        securities=securities,
    )

    by_ticker = {item.candidate.ticker: item for item in ranked}
    assert by_ticker["AAA"].selected is True
    assert by_ticker["BBB"].selected is False
    assert by_ticker["BBB"].exclusion_reason == "theme_mismatch"

