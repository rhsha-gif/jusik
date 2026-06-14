from __future__ import annotations

from quantpilot.packages.core.schemas import CandidateUniverseItem, UserPolicy
from quantpilot.packages.core.universe.builder import build_candidate_universe, build_ranked_candidate_universe


def _securities() -> list[dict[str, object]]:
    return [
        {
            "ticker": "AAA",
            "name": "Alpha",
            "market": "US_STOCK",
            "sector": "technology",
            "themes": "ai|semiconductor",
            "avg_daily_value": 15_000_000,
            "data_ready": True,
            "volatility": 0.10,
            "correlation_to_portfolio": 0.10,
            "fundamental_available": True,
        },
        {
            "ticker": "BBB",
            "name": "Beta",
            "market": "US_STOCK",
            "sector": "technology",
            "themes": ["ai"],
            "avg_daily_value": 9_000_000,
            "data_ready": True,
            "volatility": 0.30,
            "correlation_to_portfolio": 0.30,
            "fundamental_available": True,
        },
        {
            "ticker": "CCC",
            "name": "Core",
            "market": "US_STOCK",
            "sector": "technology",
            "themes": ["ai"],
            "avg_daily_value": 8_000_000,
            "data_ready": True,
            "volatility": 0.40,
            "correlation_to_portfolio": 0.40,
            "fundamental_available": True,
        },
        {
            "ticker": "DDD",
            "name": "Thin",
            "market": "US_STOCK",
            "sector": "technology",
            "themes": ["ai"],
            "avg_daily_value": 500_000,
            "data_ready": True,
        },
    ]


def test_legacy_universe_builder_output_is_unchanged() -> None:
    policy = UserPolicy(preferred_themes=["ai"], min_avg_daily_value=1_000_000)

    universe = build_candidate_universe(policy, _securities())

    assert all(isinstance(candidate, CandidateUniverseItem) for candidate in universe)
    assert [candidate.ticker for candidate in universe] == ["AAA", "BBB", "CCC", "DDD"]
    assert next(candidate for candidate in universe if candidate.ticker == "DDD").block_reason == "liquidity_below_minimum"


def test_ranked_universe_adapter_applies_final_candidate_cap() -> None:
    policy = UserPolicy(preferred_themes=["ai"], min_avg_daily_value=1_000_000)

    ranked = build_ranked_candidate_universe(policy, _securities(), max_candidates=2)

    assert [item.candidate.ticker for item in ranked] == ["AAA", "BBB"]
    assert [item.selected_rank for item in ranked] == [1, 2]
    assert all(item.selected for item in ranked)

    full_ranked = build_ranked_candidate_universe(policy, _securities(), max_candidates=2, include_excluded=True)
    by_ticker = {item.candidate.ticker: item for item in full_ranked}
    assert by_ticker["CCC"].selected is False
    assert by_ticker["CCC"].exclusion_reason == "candidate_cap_exceeded"
    assert by_ticker["DDD"].selected is False
    assert by_ticker["DDD"].exclusion_reason == "liquidity_below_minimum"

