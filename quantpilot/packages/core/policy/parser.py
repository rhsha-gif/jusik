from __future__ import annotations

import re

from quantpilot.packages.core.schemas import BrokerMode, ExecutionMode, OrderType, UserPolicy


DEFAULT_POLICY_TEXT = (
    "KR stock moderate risk weekly rebalance, approval required, mock broker, "
    "limit orders only."
)


def parse_policy_text(text: str, *, user_id: str = "fixture-user") -> UserPolicy:
    """Deterministic local parser stub for natural-language policy text."""
    normalized = text.lower()
    upper_text = text.upper()
    execution_mode = ExecutionMode.approval_required
    broker = BrokerMode.mock
    allowed_order_types = [OrderType.limit]
    market = "KR_STOCK"
    risk_profile = "moderate"
    max_positions = 8
    max_position_weight = 0.15
    max_sector_weight = 0.40
    min_cash_weight = 0.20
    rebalance_frequency = "weekly"
    preferred_themes: list[str] = []
    preferred_sectors: list[str] = []
    blocklist: list[str] = []

    if "paper" in normalized:
        execution_mode = ExecutionMode.paper_trading
        broker = BrokerMode.paper
    if "backtest" in normalized:
        execution_mode = ExecutionMode.backtest_only
    if "guarded" in normalized:
        execution_mode = ExecutionMode.guarded_autopilot
    if "fully" in normalized or "automated" in normalized:
        execution_mode = ExecutionMode.fully_automated
    if "market order" in normalized or "market orders" in normalized:
        allowed_order_types = [OrderType.limit, OrderType.market]
    if any(token in normalized for token in ["us stock", "u.s.", "미국", "나스닥", "s&p", "nasdaq"]):
        market = "US_STOCK"
    if any(token in normalized for token in ["korea", "kr stock", "국내", "한국", "코스피", "코스닥"]):
        market = "KR_STOCK"
    if any(token in normalized for token in ["conservative", "defensive", "보수", "안정", "저위험"]):
        risk_profile = "conservative"
        max_position_weight = 0.10
        min_cash_weight = 0.30
    if any(token in normalized for token in ["aggressive", "growth", "공격", "성장", "고위험"]):
        risk_profile = "aggressive"
        max_position_weight = 0.20
        min_cash_weight = 0.10
    if any(token in normalized for token in ["daily", "매일", "일간"]):
        rebalance_frequency = "daily"
    if any(token in normalized for token in ["monthly", "월간", "매월"]):
        rebalance_frequency = "monthly"
    if any(token in normalized for token in ["weekly", "주간", "매주"]):
        rebalance_frequency = "weekly"

    cash_match = re.search(r"(?:현금|cash)[^\d]{0,12}(\d+(?:\.\d+)?)\s*%", normalized)
    if cash_match:
        min_cash_weight = min(float(cash_match.group(1)) / 100, 0.95)

    position_match = re.search(r"(?:종목당|단일\s*종목|개별|position)[^\d]{0,12}(\d+(?:\.\d+)?)\s*%", normalized)
    if position_match:
        max_position_weight = min(float(position_match.group(1)) / 100, 1 - min_cash_weight)

    sector_match = re.search(r"(?:섹터|sector)[^\d]{0,12}(\d+(?:\.\d+)?)\s*%", normalized)
    if sector_match:
        max_sector_weight = float(sector_match.group(1)) / 100

    max_positions_match = re.search(r"(?:최대|max)[^\d]{0,8}(\d+)\s*(?:개|종목|positions?)", normalized)
    if max_positions_match:
        max_positions = max(1, int(max_positions_match.group(1)))

    if any(token in normalized for token in ["ai", "인공지능"]):
        preferred_themes.append("ai")
    if any(token in normalized for token in ["semiconductor", "chip", "반도체"]):
        preferred_themes.append("semiconductor")
    if any(token in normalized for token in ["battery", "2차전지", "배터리"]):
        preferred_themes.append("battery")
    if any(token in normalized for token in ["dividend", "배당"]):
        preferred_themes.append("dividend")
    if any(token in normalized for token in ["defensive", "방어"]):
        preferred_themes.append("defensive")
    if any(token in normalized for token in ["technology", "tech", "기술"]):
        preferred_sectors.append("technology")
    if any(token in normalized for token in ["healthcare", "bio", "바이오", "헬스케어"]):
        preferred_sectors.append("healthcare")

    if any(token in normalized for token in ["제외", "빼고", "exclude", "blocklist"]):
        candidates = re.findall(r"\b[A-Z0-9][A-Z0-9.\-]{1,12}\b", upper_text)
        ignored = {"AI", "US", "KR", "ETF", "S", "P", "NASDAQ"}
        blocklist = [candidate for candidate in candidates if candidate not in ignored]

    max_sector_weight = max(max_sector_weight, max_position_weight)

    return UserPolicy(
        user_id=user_id,
        market=market,
        risk_profile=risk_profile,
        max_positions=max_positions,
        max_position_weight=max_position_weight,
        max_sector_weight=max_sector_weight,
        min_cash_weight=min_cash_weight,
        daily_loss_limit=-0.03,
        monthly_loss_limit=-0.05,
        single_order_cash_limit=1_000_000,
        rebalance_frequency=rebalance_frequency,
        execution_mode=execution_mode,
        allowed_order_types=allowed_order_types,
        broker=broker,
        preferred_themes=preferred_themes,
        preferred_sectors=preferred_sectors,
        blocklist=blocklist,
    )
