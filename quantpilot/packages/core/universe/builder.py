from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from quantpilot.packages.core.schemas import CandidateUniverseItem, PortfolioSnapshot, UserPolicy
from quantpilot.packages.core.universe.ranking import CandidateRankingEngine, MAX_CANDIDATES_DEFAULT
from quantpilot.packages.core.universe.ranking_types import RankedCandidate


FIXTURE_SECURITIES: list[dict[str, Any]] = [
    {
        "ticker": "AAA",
        "name": "Alpha AI Semiconductors",
        "market": "US_STOCK",
        "sector": "technology",
        "themes": ["ai", "semiconductor"],
        "avg_daily_value": 10_500_000,
        "data_ready": True,
    },
    {
        "ticker": "BBB",
        "name": "Beta Cloud Platforms",
        "market": "US_STOCK",
        "sector": "technology",
        "themes": ["ai", "software"],
        "avg_daily_value": 9_180_000,
        "data_ready": True,
    },
    {
        "ticker": "CCC",
        "name": "Core Dividend Bank",
        "market": "KR_STOCK",
        "sector": "financials",
        "themes": ["dividend", "defensive"],
        "avg_daily_value": 6_060_000,
        "data_ready": True,
    },
    {
        "ticker": "DDD",
        "name": "Delta Automation",
        "market": "KR_STOCK",
        "sector": "industrial",
        "themes": ["automation", "cyclical"],
        "avg_daily_value": 16_250_000,
        "data_ready": True,
    },
    {
        "ticker": "EEE",
        "name": "Echo Materials",
        "market": "KR_STOCK",
        "sector": "materials",
        "themes": ["cyclical"],
        "avg_daily_value": 12_600_000,
        "data_ready": True,
    },
    {
        "ticker": "FFF",
        "name": "Foxtrot Thin Liquidity",
        "market": "KR_STOCK",
        "sector": "healthcare",
        "themes": ["bio"],
        "avg_daily_value": 3_800_000,
        "data_ready": True,
    },
    {
        "ticker": "GGG",
        "name": "Gamma Halted Security",
        "market": "KR_STOCK",
        "sector": "technology",
        "themes": ["ai"],
        "avg_daily_value": 0,
        "data_ready": False,
        "fixture_blocked": True,
    },
]


def _theme_matches(policy: UserPolicy, security: dict[str, Any]) -> bool:
    if not policy.preferred_themes:
        return True
    raw_themes = security.get("themes", [])
    if isinstance(raw_themes, str):
        raw_themes = raw_themes.replace("|", ",").split(",")
    security_themes = {str(theme).strip().lower() for theme in raw_themes if str(theme).strip()}
    return bool(security_themes.intersection(policy.preferred_themes))


def _block_reason(
    *,
    policy: UserPolicy,
    security: dict[str, Any],
    theme_match: bool,
    liquidity_pass: bool,
    data_ready: bool,
) -> str | None:
    ticker = str(security["ticker"]).upper()
    if ticker in policy.blocklist:
        return "policy_blocklist"
    if security.get("fixture_blocked", False):
        return "fixture_blocked"
    if not liquidity_pass:
        return "liquidity_below_minimum"
    if not data_ready:
        return "data_unavailable"
    if not theme_match:
        return "theme_mismatch"
    return None


def build_candidate_universe(policy: UserPolicy, securities: list[dict[str, Any]] | None = None) -> list[CandidateUniverseItem]:
    selected = securities or FIXTURE_SECURITIES
    universe: list[CandidateUniverseItem] = []
    for security in selected:
        theme_match = _theme_matches(policy, security)
        liquidity_pass = float(security.get("avg_daily_value", 0)) >= policy.min_avg_daily_value
        data_ready = bool(security.get("data_ready", False))
        block_reason = _block_reason(
            policy=policy,
            security=security,
            theme_match=theme_match,
            liquidity_pass=liquidity_pass,
            data_ready=data_ready,
        )
        universe.append(
            CandidateUniverseItem(
                ticker=str(security["ticker"]).upper(),
                name=str(security["name"]),
                market=str(security["market"]),
                sector=str(security["sector"]),
                theme_match=theme_match,
                liquidity_pass=liquidity_pass,
                data_ready=data_ready,
                block_reason=block_reason,
                analyst_required=block_reason is None,
            )
        )
    return universe


def build_ranked_candidate_universe(
    policy: UserPolicy,
    securities: list[dict[str, Any]] | None = None,
    *,
    provider_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    portfolio_snapshot: PortfolioSnapshot | None = None,
    max_candidates: int = MAX_CANDIDATES_DEFAULT,
    include_excluded: bool = False,
) -> list[RankedCandidate]:
    selected = securities or FIXTURE_SECURITIES
    candidates = build_candidate_universe(policy, selected)
    ranked = CandidateRankingEngine(max_candidates=max_candidates).rank(
        policy=policy,
        candidates=candidates,
        securities=selected,
        provider_metadata=provider_metadata,
        portfolio_snapshot=portfolio_snapshot,
    )
    if include_excluded:
        return ranked
    return [candidate for candidate in ranked if candidate.selected]
