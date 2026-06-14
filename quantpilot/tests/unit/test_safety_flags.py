from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from quantpilot.packages.core.execution.safety_flags import (
    fully_automated_operator_flag_enabled,
    guarded_autopilot_flag_enabled,
    live_trading_flag_enabled,
    market_orders_enabled,
    operator_kill_switch_engaged,
)
from quantpilot.packages.core.execution.state_machine import authorize_level4
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
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


KRX_TRADING_TIME = datetime(2026, 6, 12, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
SAFETY_ENV_FLAGS = (
    "LIVE_TRADING_ENABLED",
    "MARKET_ORDERS_ENABLED",
    "GUARDED_AUTOPILOT_ENABLED",
    "FULLY_AUTOMATED_OPERATOR_ENABLED",
    "OPERATOR_KILL_SWITCH",
)


def _level4_policy() -> UserPolicy:
    return UserPolicy(
        execution_mode=ExecutionMode.guarded_autopilot,
        broker=BrokerMode.mock,
        authority_level=4,
        guarded_autopilot_enabled=True,
        allowed_order_types=[OrderType.limit, OrderType.market],
    )


def _level4_strategy() -> StrategyRecipe:
    return StrategyRecipe(
        strategy_id="pullback_trend_v1",
        version="1.0",
        entry_rules=["fixture"],
        exit_rules=["fixture"],
        position_sizing={"method": "capped_target_weight", "max_target_weight": 0.15},
        risk_rules=["limit orders only"],
        rebalance="weekly",
        promotion_status="validated_l4",
        allowed_execution_levels=["level_3", "level_4", "guarded_autopilot"],
    )


def _market_order(policy: UserPolicy) -> OrderPlan:
    intent = OrderIntent(
        symbol="AAA",
        side="buy",
        order_type=OrderType.market,
        quantity=100,
        limit_price=None,
        notional=10_000,
        target_weight=0.01,
        reason="market order flag consistency",
        quote_time=KRX_TRADING_TIME,
    )
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=intent,
        idempotency_key="idem-market-flag-consistency",
    )


def test_safety_flag_helpers_default_false(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SAFETY_ENV_FLAGS:
        monkeypatch.delenv(name, raising=False)

    policy = UserPolicy()
    assert live_trading_flag_enabled() is False
    assert market_orders_enabled() is False
    assert guarded_autopilot_flag_enabled(policy) is False
    assert fully_automated_operator_flag_enabled(policy) is False
    assert operator_kill_switch_engaged() is False


def test_market_order_flag_is_shared_by_risk_and_level4_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _level4_policy()
    order = _market_order(policy)
    strategy = _level4_strategy()
    snapshot = fixture_portfolio_snapshot()
    state = GuardrailState()

    monkeypatch.delenv("MARKET_ORDERS_ENABLED", raising=False)
    risk_disabled = run_risk_check(policy=policy, order_plan=order, snapshot=snapshot, now=KRX_TRADING_TIME)
    authority_disabled = authorize_level4(
        order_plan=order,
        policy=policy,
        strategy=strategy,
        snapshot=snapshot,
        state=state,
        now=KRX_TRADING_TIME,
    )
    assert "order_type_allowed" in risk_disabled.failed_checks
    assert authority_disabled.first_failed_check == "order_type_allowed"

    monkeypatch.setenv("MARKET_ORDERS_ENABLED", "true")
    risk_enabled = run_risk_check(policy=policy, order_plan=order, snapshot=snapshot, now=KRX_TRADING_TIME)
    authority_enabled = authorize_level4(
        order_plan=order,
        policy=policy,
        strategy=strategy,
        snapshot=snapshot,
        state=state,
        now=KRX_TRADING_TIME,
    )
    assert "order_type_allowed" not in risk_enabled.failed_checks
    assert authority_enabled.first_failed_check != "order_type_allowed"
