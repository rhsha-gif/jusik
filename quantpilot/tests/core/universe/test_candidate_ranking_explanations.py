from __future__ import annotations

from quantpilot.packages.core.universe.builder import build_ranked_candidate_universe
from quantpilot.packages.core.schemas import UserPolicy


def test_ranking_explanation_marks_unavailable_inputs_as_neutral_degraded() -> None:
    securities = [
        {
            "ticker": "AAA",
            "name": "Alpha AI",
            "market": "US_STOCK",
            "sector": "technology",
            "themes": ["ai"],
            "avg_daily_value": 8_000_000,
            "data_ready": True,
        }
    ]
    policy = UserPolicy(preferred_themes=["ai"], preferred_sectors=["technology"])

    ranked = build_ranked_candidate_universe(policy, securities)

    assert len(ranked) == 1
    item = ranked[0]
    assert item.score.volatility == 50
    assert item.score.correlation == 50
    assert item.score.existing_exposure == 50
    assert item.score.fundamental_availability == 50
    assert item.explanation.unavailable_data == [
        "volatility",
        "correlation",
        "existing_exposure",
        "fundamental_availability",
    ]
    assert "volatility_unavailable" in item.explanation.degraded_reason_codes
    assert "neutral" in item.explanation.component_explanations["volatility"]
    assert item.model_dump(mode="json")["candidate"]["ticker"] == "AAA"


def test_ranking_explanation_preserves_policy_blocklist_exclusion_reason() -> None:
    securities = [
        {
            "ticker": "AAA",
            "name": "Alpha AI",
            "market": "US_STOCK",
            "sector": "technology",
            "themes": ["ai"],
            "avg_daily_value": 8_000_000,
            "data_ready": True,
        }
    ]
    policy = UserPolicy(preferred_themes=["ai"], blocklist=["AAA"])

    ranked = build_ranked_candidate_universe(policy, securities, include_excluded=True)

    assert len(ranked) == 1
    assert ranked[0].selected is False
    assert ranked[0].selected_rank is None
    assert ranked[0].exclusion_reason == "policy_blocklist"
    assert ranked[0].explanation.summary.startswith("final_score=")

