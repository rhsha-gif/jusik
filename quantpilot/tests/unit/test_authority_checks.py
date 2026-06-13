from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from quantpilot.packages.core.execution.state_machine import authorize_level4, is_krx_auto_order_window
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    GuardrailState,
    OrderIntent,
    OrderPlan,
    OrderType,
    StrategyRecipe,
    UserPolicy,
)


def _policy(**updates: object) -> UserPolicy:
    values = {
        "execution_mode": ExecutionMode.guarded_autopilot,
        "broker": BrokerMode.mock,
        "authority_level": 4,
        "guarded_autopilot_enabled": True,
    }
    values.update(updates)
    return UserPolicy(**values)


def _strategy(**updates: object) -> StrategyRecipe:
    values = {
        "strategy_id": "pullback_trend_v1",
        "version": "1.0",
        "entry_rules": ["fixture"],
        "exit_rules": ["fixture"],
        "position_sizing": {"method": "capped_target_weight", "max_target_weight": 0.15},
        "risk_rules": ["limit orders only"],
        "rebalance": "weekly",
        "promotion_status": "validated_l4",
        "allowed_execution_levels": ["level_3", "level_4", "guarded_autopilot"],
    }
    values.update(updates)
    return StrategyRecipe(**values)


def _order(policy: UserPolicy) -> OrderPlan:
    intent = OrderIntent(
        symbol="AAA",
        side="buy",
        order_type=OrderType.limit,
        quantity=1_000,
        limit_price=100,
        notional=100_000,
        target_weight=0.01,
        reason="authority check",
    )
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=intent,
        idempotency_key="idem-auth",
    )


def test_guarded_autopilot_default_disabled() -> None:
    policy = UserPolicy()

    result = authorize_level4(
        order_plan=_order(policy),
        policy=policy,
        strategy=_strategy(),
        snapshot=fixture_portfolio_snapshot(),
        state=GuardrailState(),
    )

    assert not result.authorized
    assert result.first_failed_check == "guarded_autopilot_enabled"


def test_authority_check_short_circuits_on_kill_switch_before_later_checks() -> None:
    policy = _policy(kill_switch_engaged=True)

    result = authorize_level4(
        order_plan=_order(policy),
        policy=policy,
        strategy=_strategy(promotion_status="draft", allowed_execution_levels=[]),
        snapshot=fixture_portfolio_snapshot(),
        state=GuardrailState(),
    )

    assert not result.authorized
    assert [step.check_name for step in result.steps] == ["guarded_autopilot_enabled", "kill_switch_not_engaged"]
    assert result.first_failed_check == "kill_switch_not_engaged"


def test_guarded_autopilot_requires_approved_strategy_and_allowed_level() -> None:
    policy = _policy()

    result = authorize_level4(
        order_plan=_order(policy),
        policy=policy,
        strategy=_strategy(promotion_status="draft", allowed_execution_levels=[]),
        snapshot=fixture_portfolio_snapshot(),
        state=GuardrailState(),
    )

    assert not result.authorized
    assert result.first_failed_check == "strategy_promotion_approved"


def test_krx_auction_windows_block_auto_orders() -> None:
    seoul = ZoneInfo("Asia/Seoul")

    assert not is_krx_auto_order_window(datetime(2026, 6, 12, 9, 5, tzinfo=seoul))
    assert is_krx_auto_order_window(datetime(2026, 6, 12, 10, 0, tzinfo=seoul))
    assert not is_krx_auto_order_window(datetime(2026, 6, 12, 15, 20, tzinfo=seoul))
    assert not is_krx_auto_order_window(datetime(2026, 6, 12, 23, 0, tzinfo=timezone.utc))
