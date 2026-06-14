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


def _symbol_matches(policy: UserPolicy, security: dict[str, Any]) -> bool:
    if not policy.preferred_symbols:
        return True
    return str(security["ticker"]).upper() in policy.preferred_symbols


def _sector_matches(policy: UserPolicy, security: dict[str, Any]) -> bool:
    if not policy.preferred_sectors:
        return True
    return str(security["sector"]).lower() in policy.preferred_sectors


def _has_focus(policy: UserPolicy) -> bool:
    return bool(policy.preferred_symbols or policy.preferred_sectors or policy.preferred_themes)


def _focus_matches(
    policy: UserPolicy,
    *,
    symbol_match: bool,
    sector_match: bool,
    theme_match: bool,
) -> bool:
    if not _has_focus(policy):
        return True
    return any(
        (
            bool(policy.preferred_symbols and symbol_match),
            bool(policy.preferred_sectors and sector_match),
            bool(policy.preferred_themes and theme_match),
        )
    )


def _focus_mismatch_reason(policy: UserPolicy) -> str:
    focus_count = sum(bool(item) for item in (policy.preferred_symbols, policy.preferred_sectors, policy.preferred_themes))
    if focus_count != 1:
        return "focus_mismatch"
    if policy.preferred_symbols:
        return "symbol_mismatch"
    if policy.preferred_sectors:
        return "sector_mismatch"
    return "theme_mismatch"


def _block_reason(
    *,
    policy: UserPolicy,
    security: dict[str, Any],
    focus_match: bool,
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
    if not focus_match:
        return _focus_mismatch_reason(policy)
    return None


def build_candidate_universe(policy: UserPolicy, securities: list[dict[str, Any]] | None = None) -> list[CandidateUniverseItem]:
    selected = securities or FIXTURE_SECURITIES
    universe: list[CandidateUniverseItem] = []
    for security in selected:
        symbol_match = _symbol_matches(policy, security)
        sector_match = _sector_matches(policy, security)
        theme_match = _theme_matches(policy, security)
        focus_match = _focus_matches(
            policy,
            symbol_match=symbol_match,
            sector_match=sector_match,
            theme_match=theme_match,
        )
        liquidity_pass = float(security.get("avg_daily_value", 0)) >= policy.min_avg_daily_value
        data_ready = bool(security.get("data_ready", False))
        block_reason = _block_reason(
            policy=policy,
            security=security,
            focus_match=focus_match,
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
                symbol_match=symbol_match,
                sector_match=sector_match,
                theme_match=theme_match,
                focus_match=focus_match,
                liquidity_pass=liquidity_pass,
                data_ready=data_ready,
                block_reason=block_reason,
                analyst_required=block_reason is None,
            )
        )
    return universe


def missing_preferred_symbols(policy: UserPolicy, securities: list[dict[str, Any]] | None = None) -> list[str]:
    selected = securities or FIXTURE_SECURITIES
    available = {str(security["ticker"]).upper() for security in selected}
    return [symbol for symbol in policy.preferred_symbols if symbol not in available]


def build_focus_summary(policy: UserPolicy, securities: list[dict[str, Any]] | None = None) -> dict[str, object]:
    return {
        "preferred_symbols": list(policy.preferred_symbols),
        "preferred_sectors": list(policy.preferred_sectors),
        "preferred_themes": list(policy.preferred_themes),
        "missing_preferred_symbols": missing_preferred_symbols(policy, securities),
    }
