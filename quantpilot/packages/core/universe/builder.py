from __future__ import annotations

from typing import Any

from quantpilot.packages.core.schemas import CandidateUniverseItem, UserPolicy


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
    security_themes = {str(theme).lower() for theme in security.get("themes", [])}
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
