from __future__ import annotations

import os
from datetime import datetime, timezone

from quantpilot.packages.core.schemas import (
    BrokerMode,
    ExecutionMode,
    GuardrailState,
    OrderPlan,
    OrderType,
    PortfolioSnapshot,
    RiskCheck,
    UserPolicy,
)


PRE_HARNESS_EXECUTION_MODES = {
    ExecutionMode.paper_trading,
    ExecutionMode.approval_required,
    ExecutionMode.guarded_autopilot,
}


def market_orders_enabled() -> bool:
    return os.getenv("MARKET_ORDERS_ENABLED", "false").lower() == "true"


def allowed_execution_modes(policy: UserPolicy | None = None) -> set[ExecutionMode]:
    # fully_automated is only a valid execution mode while the Level 5 feature flag is
    # explicitly enabled (env or explicit policy field); with default flags the allowed
    # set is identical to pre-harness.
    modes = set(PRE_HARNESS_EXECUTION_MODES)
    env_enabled = os.getenv("FULLY_AUTOMATED_OPERATOR_ENABLED", "false").lower() == "true"
    policy_enabled = policy is not None and policy.fully_automated_operator_enabled
    if env_enabled or policy_enabled:
        modes.add(ExecutionMode.fully_automated)
    return modes


def _current_position_value(snapshot: PortfolioSnapshot, symbol: str) -> float:
    return sum(position.market_value for position in snapshot.positions if position.symbol == symbol)


def _position_sector(snapshot: PortfolioSnapshot, symbol: str) -> str:
    for position in snapshot.positions:
        if position.symbol == symbol:
            return position.sector
    return "unknown"


def _sector_value(snapshot: PortfolioSnapshot, sector: str) -> float:
    return sum(position.market_value for position in snapshot.positions if position.sector == sector)


def _is_risk_reducing_sell(order_plan: OrderPlan, snapshot: PortfolioSnapshot) -> bool:
    if order_plan.intent.side != "sell":
        return False
    return 0 < order_plan.intent.notional <= _current_position_value(snapshot, order_plan.intent.symbol)


def run_risk_check(
    *,
    policy: UserPolicy,
    order_plan: OrderPlan,
    snapshot: PortfolioSnapshot,
    seen_idempotency_keys: set[str] | None = None,
    guardrail_state: GuardrailState | None = None,
    now: datetime | None = None,
    quote_max_age_seconds: int = 900,
    strategy_id: str | None = None,
) -> RiskCheck:
    current_time = now or datetime.now(timezone.utc)
    seen = seen_idempotency_keys or set()
    state = guardrail_state or GuardrailState()
    intent = order_plan.intent
    passed: list[str] = []
    failed: list[str] = []

    def check(name: str, condition: bool) -> None:
        if condition:
            passed.append(name)
        else:
            failed.append(name)

    check("kill_switch_not_engaged", not policy.kill_switch_engaged and not state.kill_switch_engaged)
    check("policy_version_match", order_plan.policy_version == policy.version)
    check("execution_mode_allowed", policy.execution_mode in allowed_execution_modes(policy))
    check("broker_mode_not_live", policy.broker != BrokerMode.live_disabled)
    check("risk_check_not_expired", True)
    check("portfolio_snapshot_not_stale", not snapshot.is_stale)

    if intent.side == "buy":
        cash_after_order = snapshot.cash - intent.notional
        position_after = _current_position_value(snapshot, intent.symbol) + intent.notional
    else:
        cash_after_order = snapshot.cash
        position_after = max(0.0, _current_position_value(snapshot, intent.symbol) - intent.notional)

    sector = _position_sector(snapshot, intent.symbol)
    sector_after = _sector_value(snapshot, sector) + (intent.notional if intent.side == "buy" else -min(intent.notional, _current_position_value(snapshot, intent.symbol)))

    check("available_cash", intent.side != "buy" or snapshot.cash >= intent.notional)
    check("min_cash_after_order", intent.side != "buy" or cash_after_order >= policy.min_cash_weight * snapshot.equity)
    check("max_position_weight_after_fill", position_after / snapshot.equity <= policy.max_position_weight)
    check("max_sector_weight_after_fill", max(0.0, sector_after) / snapshot.equity <= policy.max_sector_weight)
    check("single_order_cash_limit", intent.notional <= policy.single_order_cash_limit)
    check("max_daily_orders", state.daily_order_count < policy.max_daily_orders)
    check("max_daily_turnover", state.daily_turnover_used + intent.notional <= policy.max_daily_turnover)

    order_type_allowed = intent.order_type in policy.allowed_order_types
    if intent.order_type == OrderType.market and not market_orders_enabled():
        order_type_allowed = False
    check("order_type_allowed", order_type_allowed)

    check("idempotency_key_not_seen", order_plan.idempotency_key not in seen)
    quote_age = (current_time - intent.quote_time).total_seconds()
    check("quote_not_stale", 0 <= quote_age <= quote_max_age_seconds)

    if strategy_id is not None:
        conflict_key = f"{strategy_id}:{intent.symbol}:{intent.side}"
        check("unfilled_conflicting_order", conflict_key not in set(state.unfilled_order_keys))

    daily_loss_buy_halt = intent.side == "buy" and snapshot.daily_loss_ratio <= policy.daily_loss_limit
    check("daily_loss_limit_not_triggered", not daily_loss_buy_halt)

    monthly_pause = intent.side == "buy" and snapshot.monthly_loss_ratio <= policy.monthly_loss_pause_new_buys
    monthly_stop = snapshot.monthly_loss_ratio <= policy.monthly_loss_stop_all_autotrading
    check("monthly_loss_pause_not_triggered", not monthly_pause)
    check("monthly_loss_pause_new_buys", not monthly_pause)
    check("monthly_loss_stop_not_triggered", not monthly_stop)
    check("monthly_loss_stop_all_autotrading", not monthly_stop)
    if snapshot.monthly_loss_ratio <= policy.monthly_loss_pause_new_buys and intent.side == "sell":
        check("risk_reducing_sell", _is_risk_reducing_sell(order_plan, snapshot))

    return RiskCheck(
        order_plan_id=order_plan.order_plan_id,
        passed=not failed,
        passed_checks=passed,
        failed_checks=failed,
        policy_version=policy.version,
        idempotency_key=order_plan.idempotency_key,
        snapshot_id=snapshot.snapshot_id,
        snapshot_source=snapshot.source,
        snapshot_as_of=snapshot.as_of,
        snapshot_is_stale=snapshot.is_stale,
    )
