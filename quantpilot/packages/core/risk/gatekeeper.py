from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import ManagedPositionBinding
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
    normalized = symbol.strip().upper()
    return sum(
        position.market_value
        for position in snapshot.positions
        if position.symbol.strip().upper() == normalized
    )


def _current_orderable_quantity(snapshot: PortfolioSnapshot, symbol: str) -> float:
    normalized = symbol.strip().upper()
    return sum(
        position.effective_orderable_quantity
        for position in snapshot.positions
        if position.symbol.strip().upper() == normalized
    )


def _position_sector(snapshot: PortfolioSnapshot, symbol: str) -> str:
    normalized = symbol.strip().upper()
    for position in snapshot.positions:
        if position.symbol.strip().upper() == normalized:
            return position.sector
    return "unknown"


def _sector_value(snapshot: PortfolioSnapshot, sector: str) -> float:
    return sum(position.market_value for position in snapshot.positions if position.sector == sector)


RISK_REDUCING_PURPOSES = {"protective_exit", "strategy_retirement"}


def aware_age_seconds(now: datetime, observed_at: datetime) -> float | None:
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        return None
    return (now - observed_at).total_seconds()


def is_verified_risk_reducing_order(
    order_plan: OrderPlan,
    snapshot: PortfolioSnapshot,
    *,
    position_binding: ManagedPositionBinding | None = None,
    market_quote: Quote | None = None,
    reserved_sell_quantities: dict[str, float] | None = None,
    now: datetime | None = None,
    quote_max_age_seconds: int = 30,
) -> bool:
    intent = order_plan.intent
    if order_plan.purpose not in RISK_REDUCING_PURPOSES:
        return False
    if position_binding is None or order_plan.explanation is None:
        return False
    current_time = now or datetime.now(timezone.utc)
    market_quote_age = (
        None
        if market_quote is None
        else aware_age_seconds(current_time, market_quote.as_of)
    )
    if (
        market_quote is None
        or market_quote.bid is None
        or market_quote.symbol.strip().upper() != intent.symbol.strip().upper()
        or market_quote_age is None
    ):
        return False
    if not 0 <= market_quote_age <= quote_max_age_seconds:
        return False
    if intent.limit_price is None or abs(intent.limit_price - market_quote.bid) > 0.000001:
        return False
    if (
        position_binding.policy_id != order_plan.policy_id
        # A current policy may always reduce a position opened under an older
        # version of the same immutable policy identity. A future-position
        # version still proves the caller is stale and must fail closed.
        or position_binding.policy_version > order_plan.policy_version
        or position_binding.strategy_id != order_plan.explanation.strategy_id
        or position_binding.strategy_version != order_plan.explanation.strategy_version
        or position_binding.symbol != intent.symbol.strip().upper()
        or intent.quantity > position_binding.quantity + 0.000001
        or position_binding.reconciled_snapshot_id != snapshot.snapshot_id
        or position_binding.reconciled_at != snapshot.captured_at
        or position_binding.updated_at > position_binding.reconciled_at
    ):
        return False
    if intent.side != "sell" or intent.order_type != OrderType.limit:
        return False
    if intent.limit_price is None or intent.limit_price <= 0:
        return False
    normalized_symbol = intent.symbol.strip().upper()
    reserved_quantity = sum(
        quantity
        for symbol, quantity in (reserved_sell_quantities or {}).items()
        if symbol.strip().upper() == normalized_symbol
    )
    orderable_quantity = _current_orderable_quantity(snapshot, intent.symbol)
    available_quantity = max(0.0, orderable_quantity - reserved_quantity)
    if available_quantity <= 0 or intent.quantity > available_quantity + 0.000001:
        return False
    expected_notional = intent.quantity * intent.limit_price
    tolerance = max(0.01, intent.notional * 0.000001)
    if abs(expected_notional - intent.notional) > tolerance:
        return False
    current_weight = _current_position_value(snapshot, intent.symbol) / snapshot.equity
    if intent.target_weight > current_weight + 0.000001:
        return False
    return True


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
    position_binding: ManagedPositionBinding | None = None,
    market_quote: Quote | None = None,
    snapshot_max_age_seconds: int = 900,
) -> RiskCheck:
    current_time = now or datetime.now(timezone.utc)
    seen = seen_idempotency_keys or set()
    state = guardrail_state or GuardrailState()
    intent = order_plan.intent
    claims_risk_reducing_purpose = order_plan.purpose in RISK_REDUCING_PURPOSES
    verified_risk_reducing = is_verified_risk_reducing_order(
        order_plan,
        snapshot,
        position_binding=position_binding,
        market_quote=market_quote,
        reserved_sell_quantities=state.reserved_sell_quantities,
        now=current_time,
        quote_max_age_seconds=quote_max_age_seconds,
    )
    passed: list[str] = []
    failed: list[str] = []

    def check(name: str, condition: bool) -> None:
        if condition:
            passed.append(name)
        else:
            failed.append(name)

    check("kill_switch_not_engaged", not policy.kill_switch_engaged and not state.kill_switch_engaged)
    check(
        "policy_identity_match",
        order_plan.policy_id == policy.policy_id and snapshot.user_id == policy.user_id,
    )
    check("policy_version_match", order_plan.policy_version == policy.version)
    check("execution_mode_allowed", policy.execution_mode in allowed_execution_modes(policy))
    check("broker_mode_not_live", policy.broker != BrokerMode.live_disabled)
    check("risk_check_not_expired", True)
    expected_limit_notional = (
        intent.quantity * intent.limit_price
        if intent.order_type == OrderType.limit and intent.limit_price is not None
        else intent.notional
    )
    notional_tolerance = max(0.01, intent.notional * 0.000001)
    check(
        "order_notional_matches_quantity_price",
        abs(expected_limit_notional - intent.notional) <= notional_tolerance,
    )
    snapshot_age = aware_age_seconds(current_time, snapshot.captured_at)
    check(
        "snapshot_not_stale",
        snapshot_age is not None and 0 <= snapshot_age <= snapshot_max_age_seconds,
    )
    check(
        "risk_reducing_purpose_verified",
        not claims_risk_reducing_purpose or verified_risk_reducing,
    )

    if intent.side == "buy":
        cash_after_order = snapshot.cash - intent.notional
        position_after = _current_position_value(snapshot, intent.symbol) + intent.notional
    else:
        cash_after_order = snapshot.cash
        position_after = max(0.0, _current_position_value(snapshot, intent.symbol) - intent.notional)

    sector = _position_sector(snapshot, intent.symbol)
    sector_after = _sector_value(snapshot, sector) + (intent.notional if intent.side == "buy" else -min(intent.notional, _current_position_value(snapshot, intent.symbol)))

    check("available_cash", intent.side != "buy" or snapshot.cash >= intent.notional)
    reserved_sell_quantity = sum(
        quantity
        for symbol, quantity in state.reserved_sell_quantities.items()
        if symbol.strip().upper() == intent.symbol.strip().upper()
    )
    available_sell_quantity = max(
        0.0,
        _current_orderable_quantity(snapshot, intent.symbol) - reserved_sell_quantity,
    )
    check(
        "no_short_sell",
        intent.side != "sell" or intent.quantity <= available_sell_quantity + 0.000001,
    )
    check("min_cash_after_order", intent.side != "buy" or cash_after_order >= policy.min_cash_weight * snapshot.equity)
    position_before = _current_position_value(snapshot, intent.symbol)
    sector_before = _sector_value(snapshot, sector)
    check(
        "max_position_weight_after_fill",
        position_after / snapshot.equity <= policy.max_position_weight
        or (intent.side == "sell" and position_after <= position_before),
    )
    check(
        "max_sector_weight_after_fill",
        max(0.0, sector_after) / snapshot.equity <= policy.max_sector_weight
        or (intent.side == "sell" and sector_after <= sector_before),
    )
    check("single_order_cash_limit", intent.notional <= policy.single_order_cash_limit)
    check("max_daily_orders", state.daily_order_count < policy.max_daily_orders)
    check("max_daily_turnover", state.daily_turnover_used + intent.notional <= policy.max_daily_turnover)

    order_type_allowed = intent.order_type in policy.allowed_order_types
    if intent.order_type == OrderType.market and not market_orders_enabled():
        order_type_allowed = False
    check("order_type_allowed", order_type_allowed)

    check("idempotency_key_not_seen", order_plan.idempotency_key not in seen)
    quote_age = aware_age_seconds(current_time, intent.quote_time)
    check(
        "quote_not_stale",
        quote_age is not None and 0 <= quote_age <= quote_max_age_seconds,
    )

    if strategy_id is not None:
        conflict_key = f"{strategy_id}:{intent.symbol}:{intent.side}"
        check("unfilled_conflicting_order", conflict_key not in set(state.unfilled_order_keys))
    check(
        "no_unresolved_paper_buy_order",
        intent.side != "buy" or not state.unresolved_paper_buy_order,
    )

    daily_loss_buy_halt = intent.side == "buy" and snapshot.daily_loss_ratio <= policy.daily_loss_limit
    check("daily_loss_limit_not_triggered", not daily_loss_buy_halt)

    monthly_pause = intent.side == "buy" and snapshot.monthly_loss_ratio <= policy.monthly_loss_pause_new_buys
    monthly_stop = (
        snapshot.monthly_loss_ratio <= policy.monthly_loss_stop_all_autotrading
        and not verified_risk_reducing
    )
    check("monthly_loss_pause_not_triggered", not monthly_pause)
    check("monthly_loss_pause_new_buys", not monthly_pause)
    check("monthly_loss_stop_not_triggered", not monthly_stop)
    check("monthly_loss_stop_all_autotrading", not monthly_stop)
    if snapshot.monthly_loss_ratio <= policy.monthly_loss_pause_new_buys and intent.side == "sell":
        check(
            "risk_reducing_sell",
            verified_risk_reducing or order_plan.purpose == "rebalance",
        )

    return RiskCheck(
        order_plan_id=order_plan.order_plan_id,
        passed=not failed,
        passed_checks=passed,
        failed_checks=failed,
        policy_version=policy.version,
        idempotency_key=order_plan.idempotency_key,
        created_at=current_time,
        expires_at=current_time + timedelta(minutes=10),
    )
