from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.execution.state_machine import authorize_level5
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
from quantpilot.packages.core.strategies.registry import StrategyRegistryEntry


KRX_TRADING_TIME = datetime(2026, 6, 12, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def _policy(**updates: Any) -> UserPolicy:
    values: dict[str, Any] = {
        "execution_mode": ExecutionMode.fully_automated,
        "broker": BrokerMode.mock,
        "authority_level": 5,
        "fully_automated_operator_enabled": True,
    }
    values.update(updates)
    return UserPolicy(**values)


def _registry_entry(**updates: Any) -> StrategyRegistryEntry:
    values: dict[str, Any] = {
        "strategy_id": "pullback_trend_v1",
        "version": "1.0",
        "status": "validated_l5",
        "allowed_execution_levels": ["level_5", "fully_automated"],
    }
    values.update(updates)
    return StrategyRegistryEntry(**values)


def _recipe(**updates: Any) -> StrategyRecipe:
    values: dict[str, Any] = {
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


def _order(policy: UserPolicy, **intent_updates: Any) -> OrderPlan:
    intent_values: dict[str, Any] = {
        "symbol": "AAA",
        "side": "buy",
        "order_type": OrderType.limit,
        "quantity": 1_000,
        "limit_price": 100,
        "notional": 100_000,
        "target_weight": 0.01,
        "reason": "level 5 authority check",
        "quote_time": KRX_TRADING_TIME,
    }
    intent_values.update(intent_updates)
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=OrderIntent(**intent_values),
        idempotency_key="idem-level5-auth",
    )


def test_level5_authority_is_disabled_by_default() -> None:
    policy = UserPolicy()

    result = authorize_level5(
        order_plan=_order(policy),
        policy=policy,
        registry_entry=_registry_entry(),
        strategy=_recipe(),
        snapshot=fixture_portfolio_snapshot(),
        now=KRX_TRADING_TIME,
    )

    assert not result.authorized
    assert result.first_failed_check == "fully_automated_operator_enabled"


def test_level5_authority_authorizes_promoted_policy_with_validated_l5_entry() -> None:
    policy = _policy()

    result = authorize_level5(
        order_plan=_order(policy),
        policy=policy,
        registry_entry=_registry_entry(),
        strategy=_recipe(),
        snapshot=fixture_portfolio_snapshot(),
        now=KRX_TRADING_TIME,
    )

    assert result.authorized
    assert result.first_failed_check is None


def test_level5_authority_refuses_guarded_only_registry_entry() -> None:
    policy = _policy()

    result = authorize_level5(
        order_plan=_order(policy),
        policy=policy,
        registry_entry=_registry_entry(status="validated_l4", allowed_execution_levels=["level_4", "guarded_autopilot"]),
        strategy=_recipe(),
        snapshot=fixture_portfolio_snapshot(),
        now=KRX_TRADING_TIME,
    )

    assert not result.authorized
    assert result.first_failed_check == "strategy_registry_validated_l5"


def test_level5_authority_refuses_recipe_registry_mismatch() -> None:
    policy = _policy()

    result = authorize_level5(
        order_plan=_order(policy),
        policy=policy,
        registry_entry=_registry_entry(),
        strategy=_recipe(strategy_id="another_strategy"),
        snapshot=fixture_portfolio_snapshot(),
        now=KRX_TRADING_TIME,
    )

    assert not result.authorized
    assert result.first_failed_check == "strategy_recipe_matches_registry"


def test_level5_authority_blocks_market_orders_when_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKET_ORDERS_ENABLED", raising=False)
    policy = _policy(allowed_order_types=[OrderType.limit, OrderType.market])

    result = authorize_level5(
        order_plan=_order(policy, order_type=OrderType.market, limit_price=None),
        policy=policy,
        registry_entry=_registry_entry(),
        strategy=_recipe(),
        snapshot=fixture_portfolio_snapshot(),
        now=KRX_TRADING_TIME,
    )

    assert not result.authorized
    assert result.first_failed_check == "order_type_allowed"


def test_level5_authority_blocks_on_monthly_loss_stop() -> None:
    policy = _policy()

    result = authorize_level5(
        order_plan=_order(policy),
        policy=policy,
        registry_entry=_registry_entry(),
        strategy=_recipe(),
        snapshot=fixture_portfolio_snapshot(monthly_loss_ratio=-0.12),
        now=KRX_TRADING_TIME,
    )

    assert not result.authorized
    assert result.first_failed_check == "monthly_loss_stop_not_triggered"


def test_level5_authority_blocks_duplicate_idempotency_key() -> None:
    policy = _policy()
    order = _order(policy)

    result = authorize_level5(
        order_plan=order,
        policy=policy,
        registry_entry=_registry_entry(),
        strategy=_recipe(),
        snapshot=fixture_portfolio_snapshot(),
        seen_idempotency_keys={order.idempotency_key},
        now=KRX_TRADING_TIME,
    )

    assert not result.authorized
    assert result.first_failed_check == "idempotency_key_new"


def test_level5_authority_short_circuits_on_kill_switch_before_strategy_checks() -> None:
    policy = _policy(kill_switch_engaged=True)

    result = authorize_level5(
        order_plan=_order(policy),
        policy=policy,
        registry_entry=_registry_entry(status="draft", allowed_execution_levels=[]),
        strategy=_recipe(),
        snapshot=fixture_portfolio_snapshot(),
        state=GuardrailState(),
        now=KRX_TRADING_TIME,
    )

    assert not result.authorized
    assert result.first_failed_check == "kill_switch_not_engaged"
    assert [step.check_name for step in result.steps] == [
        "fully_automated_operator_enabled",
        "live_trading_disabled",
        "kill_switch_not_engaged",
    ]


def test_order_plan_requires_idempotency_key() -> None:
    policy = _policy()

    with pytest.raises(ValidationError):
        OrderPlan(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            intent=OrderIntent(
                symbol="AAA",
                side="buy",
                order_type=OrderType.limit,
                quantity=10,
                limit_price=100,
                notional=1_000,
                target_weight=0.01,
                reason="missing idempotency key",
            ),
            idempotency_key="   ",
        )
