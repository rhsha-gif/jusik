from __future__ import annotations

import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

from quantpilot.packages.core.risk.gatekeeper import (
    RISK_REDUCING_PURPOSES,
    aware_age_seconds,
    is_verified_risk_reducing_order,
    market_orders_enabled,
    run_risk_check,
)
from quantpilot.packages.core.operator.position_ledger import ManagedPositionBinding
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.strategies.registry import StrategyRegistryEntry
from quantpilot.packages.core.schemas import (
    AuthorityCheckResult,
    AuthorityCheckStep,
    BrokerMode,
    ExecutionMode,
    GuardrailState,
    OrderPlan,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    StrategyRecipe,
    UserPolicy,
    utc_now,
)
from quantpilot.packages.db.audit import AuditRecorder


class InvalidOrderTransition(RuntimeError):
    pass


class RiskCheckRequired(RuntimeError):
    pass


class ApprovalRequired(RuntimeError):
    pass


TERMINAL_STATES = {
    OrderStatus.modified,
    OrderStatus.cancelled,
    OrderStatus.rejected,
    OrderStatus.expired,
    OrderStatus.failed,
}

VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.draft: {OrderStatus.risk_checked, *TERMINAL_STATES},
    OrderStatus.risk_checked: {OrderStatus.proposed, *TERMINAL_STATES},
    OrderStatus.proposed: {OrderStatus.user_approved, *TERMINAL_STATES},
    OrderStatus.modified: set(),
    OrderStatus.user_approved: {OrderStatus.submitted, *TERMINAL_STATES},
    OrderStatus.submitted: {OrderStatus.accepted, *TERMINAL_STATES},
    OrderStatus.accepted: {OrderStatus.partially_filled, OrderStatus.filled, *TERMINAL_STATES},
    OrderStatus.partially_filled: {OrderStatus.filled, *TERMINAL_STATES},
    OrderStatus.filled: set(),
    OrderStatus.cancelled: set(),
    OrderStatus.rejected: set(),
    OrderStatus.expired: set(),
    OrderStatus.failed: set(),
}

ACTION_BY_STATUS = {
    OrderStatus.risk_checked: "risk_check_passed",
    OrderStatus.proposed: "order_proposed",
    OrderStatus.modified: "proposal_modified",
    OrderStatus.user_approved: "order_approved",
    OrderStatus.submitted: "order_submitted",
    OrderStatus.accepted: "broker_order_accepted",
    OrderStatus.partially_filled: "fill_recorded",
    OrderStatus.filled: "order_filled",
    OrderStatus.cancelled: "order_cancelled",
    OrderStatus.rejected: "order_rejected",
    OrderStatus.expired: "order_expired",
    OrderStatus.failed: "order_failed",
}


KRX_TIMEZONE = ZoneInfo("Asia/Seoul")
KRX_OPEN = time(9, 0)
KRX_CLOSE = time(15, 30)
KRX_OPENING_AUCTION_BLOCK_MINUTES = 10
KRX_CLOSING_AUCTION_BLOCK_MINUTES = 20


def strategy_versions_match(left: str, right: str) -> bool:
    def normalized(value: str) -> tuple[int, ...] | None:
        parts = value.strip().split(".")
        if not parts or any(not part.isdigit() for part in parts):
            return None
        numbers = [int(part) for part in parts]
        while len(numbers) > 1 and numbers[-1] == 0:
            numbers.pop()
        return tuple(numbers)

    left_numeric = normalized(left)
    right_numeric = normalized(right)
    if left_numeric is not None and right_numeric is not None:
        return left_numeric == right_numeric
    return left.strip() == right.strip()


def is_krx_auto_order_window(now: datetime | None = None) -> bool:
    current = (now or utc_now()).astimezone(KRX_TIMEZONE)
    if current.weekday() >= 5:
        return False
    minutes = current.hour * 60 + current.minute
    open_minutes = KRX_OPEN.hour * 60 + KRX_OPEN.minute + KRX_OPENING_AUCTION_BLOCK_MINUTES
    close_minutes = KRX_CLOSE.hour * 60 + KRX_CLOSE.minute - KRX_CLOSING_AUCTION_BLOCK_MINUTES
    return open_minutes <= minutes < close_minutes


def guarded_autopilot_flag_enabled(policy: UserPolicy) -> bool:
    env_enabled = os.getenv("GUARDED_AUTOPILOT_ENABLED", "false").lower() == "true"
    return policy.guarded_autopilot_enabled or env_enabled


def fully_automated_operator_flag_enabled(policy: UserPolicy | None = None) -> bool:
    env_enabled = os.getenv("FULLY_AUTOMATED_OPERATOR_ENABLED", "false").lower() == "true"
    policy_enabled = policy.fully_automated_operator_enabled if policy is not None else False
    return policy_enabled or env_enabled


def operator_kill_switch_engaged() -> bool:
    return os.getenv("OPERATOR_KILL_SWITCH", "false").lower() == "true"


def live_trading_flag_enabled() -> bool:
    return os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"


def authorize_level4(
    *,
    order_plan: OrderPlan,
    policy: UserPolicy,
    strategy: StrategyRecipe,
    snapshot: PortfolioSnapshot,
    state: GuardrailState | None = None,
    seen_idempotency_keys: set[str] | None = None,
    now: datetime | None = None,
) -> AuthorityCheckResult:
    current_time = now or utc_now()
    guardrail_state = state or GuardrailState()
    seen = seen_idempotency_keys or set()
    steps: list[AuthorityCheckStep] = []

    def record(check_name: str, passed: bool, detail: str) -> AuthorityCheckResult | None:
        steps.append(AuthorityCheckStep(check_name=check_name, passed=passed, detail=detail))
        if not passed:
            return AuthorityCheckResult(
                authorized=False,
                policy_version=policy.version,
                steps=steps,
                first_failed_check=check_name,
            )
        return None

    if result := record("guarded_autopilot_enabled", guarded_autopilot_flag_enabled(policy), "guarded autopilot flag must be enabled"):
        return result
    if result := record("kill_switch_not_engaged", not policy.kill_switch_engaged and not guardrail_state.kill_switch_engaged, "kill switch must be off"):
        return result
    if result := record("autopilot_not_paused", not guardrail_state.autopilot_paused, "guarded autopilot must not be paused"):
        return result
    if result := record("broker_mode_safe", policy.broker in {BrokerMode.mock, BrokerMode.paper}, "broker must be mock or paper"):
        return result
    if result := record(
        "authority_level_4",
        policy.authority_level == 4 and policy.execution_mode == ExecutionMode.guarded_autopilot,
        "policy must be promoted to guarded autopilot",
    ):
        return result
    if result := record("policy_version_match", order_plan.policy_version == policy.version, "plan policy version must match current policy"):
        return result
    if result := record(
        "policy_identity_match",
        order_plan.policy_id == policy.policy_id and snapshot.user_id == policy.user_id,
        "plan, policy, and reconciled account snapshot must share one identity",
    ):
        return result
    if result := record("broker_health", guardrail_state.broker_healthy, "broker heartbeat must be healthy"):
        return result

    quote_age = (current_time - order_plan.intent.quote_time).total_seconds()
    if result := record("quote_not_stale", 0 <= quote_age <= policy.stale_quote_max_age_seconds, "quote must be fresh for automatic submission"):
        return result

    promotion_ok = strategy.promotion_status in {"approved", "validated_l4"}
    if result := record("strategy_promotion_approved", promotion_ok, "strategy must be approved for Level 4"):
        return result
    level_ok = bool({"level_4", "guarded_autopilot"}.intersection(set(strategy.allowed_execution_levels)))
    if result := record("strategy_level_allowed", level_ok, "strategy must allow guarded autopilot execution"):
        return result

    if result := record("krx_auto_order_window", is_krx_auto_order_window(current_time), "automatic orders are blocked during auction windows"):
        return result

    order_type_allowed = order_plan.intent.order_type in policy.allowed_order_types
    if order_plan.intent.order_type == OrderType.market and not market_orders_enabled():
        order_type_allowed = False
    if result := record("order_type_allowed", order_type_allowed, "market orders require MARKET_ORDERS_ENABLED=true"):
        return result

    monthly_stop = snapshot.monthly_loss_ratio <= policy.monthly_loss_stop_all_autotrading
    if result := record("monthly_loss_stop_not_triggered", not monthly_stop, "monthly stop blocks all automatic trading"):
        return result
    monthly_pause_buy = order_plan.intent.side == "buy" and snapshot.monthly_loss_ratio <= policy.monthly_loss_pause_new_buys
    if result := record("monthly_loss_pause_allows_order", not monthly_pause_buy, "monthly pause blocks new automatic buys"):
        return result

    conflict_key = f"{strategy.strategy_id}:{order_plan.intent.symbol}:{order_plan.intent.side}"
    if result := record("no_unfilled_conflicting_order", conflict_key not in set(guardrail_state.unfilled_order_keys), "no matching unfilled order may exist"):
        return result
    if result := record(
        "no_unresolved_paper_buy_order",
        order_plan.intent.side != "buy"
        or not guardrail_state.unresolved_paper_buy_order,
        "an unresolved paper buy blocks additional buys",
    ):
        return result
    if result := record("idempotency_key_new", order_plan.idempotency_key not in seen and order_plan.idempotency_key not in set(guardrail_state.submitted_idempotency_keys), "idempotency key must be new"):
        return result

    risk_check = run_risk_check(
        policy=policy,
        order_plan=order_plan,
        snapshot=snapshot,
        seen_idempotency_keys=seen,
        guardrail_state=guardrail_state,
        quote_max_age_seconds=policy.stale_quote_max_age_seconds,
        strategy_id=strategy.strategy_id,
        now=current_time,
    )
    if result := record("fresh_risk_check_passed", risk_check.passed, ",".join(risk_check.failed_checks) or "risk check passed"):
        return result

    return AuthorityCheckResult(
        authorized=True,
        policy_version=policy.version,
        steps=steps,
        first_failed_check=None,
    )


def authorize_level5(
    *,
    order_plan: OrderPlan,
    policy: UserPolicy,
    registry_entry: StrategyRegistryEntry,
    strategy: StrategyRecipe,
    snapshot: PortfolioSnapshot,
    state: GuardrailState | None = None,
    seen_idempotency_keys: set[str] | None = None,
    now: datetime | None = None,
    position_binding: ManagedPositionBinding | None = None,
    market_quote: Quote | None = None,
) -> AuthorityCheckResult:
    current_time = now or utc_now()
    guardrail_state = state or GuardrailState()
    seen = seen_idempotency_keys or set()
    claims_risk_reducing = order_plan.purpose in RISK_REDUCING_PURPOSES
    verified_risk_reducing = is_verified_risk_reducing_order(
        order_plan,
        snapshot,
        position_binding=position_binding,
        market_quote=market_quote,
        reserved_sell_quantities=guardrail_state.reserved_sell_quantities,
        now=current_time,
        quote_max_age_seconds=policy.stale_quote_max_age_seconds,
    )
    steps: list[AuthorityCheckStep] = []

    def record(check_name: str, passed: bool, detail: str) -> AuthorityCheckResult | None:
        steps.append(AuthorityCheckStep(check_name=check_name, passed=passed, detail=detail))
        if not passed:
            return AuthorityCheckResult(
                authorized=False,
                policy_version=policy.version,
                steps=steps,
                first_failed_check=check_name,
            )
        return None

    if result := record("fully_automated_operator_enabled", fully_automated_operator_flag_enabled(policy), "Level 5 operator flag must be enabled"):
        return result
    if result := record("live_trading_disabled", not live_trading_flag_enabled(), "LIVE_TRADING_ENABLED must remain false"):
        return result
    if result := record(
        "kill_switch_not_engaged",
        not policy.kill_switch_engaged and not guardrail_state.kill_switch_engaged and not operator_kill_switch_engaged(),
        "kill switch must be off",
    ):
        return result
    if result := record("operator_not_paused", not guardrail_state.autopilot_paused, "operator must not be paused"):
        return result
    if result := record("broker_mode_safe", policy.broker in {BrokerMode.mock, BrokerMode.paper}, "broker must be mock or paper"):
        return result
    if result := record(
        "authority_level_5",
        policy.authority_level == 5 and policy.execution_mode == ExecutionMode.fully_automated,
        "policy must be explicitly promoted to the fully automated operator",
    ):
        return result
    if result := record("policy_version_match", order_plan.policy_version == policy.version, "plan policy version must match current policy"):
        return result
    if result := record("broker_health", guardrail_state.broker_healthy, "broker heartbeat must be healthy"):
        return result

    snapshot_age = aware_age_seconds(current_time, snapshot.captured_at)
    snapshot_max_age_seconds = (
        policy.stale_quote_max_age_seconds if claims_risk_reducing else 900
    )
    if result := record(
        "snapshot_not_stale",
        snapshot_age is not None
        and 0 <= snapshot_age <= snapshot_max_age_seconds,
        "reconciled portfolio snapshot must be timezone-aware and fresh for the order purpose",
    ):
        return result

    quote_age = aware_age_seconds(current_time, order_plan.intent.quote_time)
    if result := record(
        "quote_not_stale",
        quote_age is not None and 0 <= quote_age <= policy.stale_quote_max_age_seconds,
        "quote must be timezone-aware and fresh for automatic submission",
    ):
        return result

    if result := record(
        "risk_reducing_purpose_verified",
        not claims_risk_reducing or verified_risk_reducing,
        "protective and retirement purposes must be verified against the reconciled long position",
    ):
        return result

    registry_ok = registry_entry.status == "validated_l5" or (
        registry_entry.status == "disabled" and verified_risk_reducing
    )
    if result := record(
        "strategy_registry_validated_l5",
        registry_ok,
        "registry entry must be validated_l5; disabled strategies may only reduce attributed risk",
    ):
        return result
    level_ok = bool(
        {"level_5", "fully_automated"}.intersection(set(registry_entry.allowed_execution_levels))
    ) or (registry_entry.status == "disabled" and verified_risk_reducing)
    if result := record(
        "strategy_level_allowed",
        level_ok,
        "registry entry must allow fully automated execution unless closing disabled-strategy risk",
    ):
        return result
    if result := record(
        "strategy_recipe_matches_registry",
        strategy.strategy_id == registry_entry.strategy_id
        and strategy_versions_match(strategy.version, registry_entry.version)
        and (
            order_plan.explanation is None
            or (
                order_plan.explanation.strategy_id == strategy.strategy_id
                and (
                    verified_risk_reducing
                    or strategy_versions_match(
                        order_plan.explanation.strategy_version,
                        strategy.version,
                    )
                )
            )
        )
        and (
            not claims_risk_reducing
            or order_plan.explanation is not None
        ),
        "signal recipe and risk-order attribution must match the registry version",
    ):
        return result

    if result := record("krx_auto_order_window", is_krx_auto_order_window(current_time), "automatic orders are blocked during auction windows"):
        return result

    order_type_allowed = order_plan.intent.order_type in policy.allowed_order_types
    if order_plan.intent.order_type == OrderType.market and not market_orders_enabled():
        order_type_allowed = False
    if result := record("order_type_allowed", order_type_allowed, "market orders require MARKET_ORDERS_ENABLED=true"):
        return result

    monthly_stop = (
        snapshot.monthly_loss_ratio <= policy.monthly_loss_stop_all_autotrading
        and not verified_risk_reducing
    )
    if result := record("monthly_loss_stop_not_triggered", not monthly_stop, "monthly stop blocks all automatic trading"):
        return result
    monthly_pause_buy = order_plan.intent.side == "buy" and snapshot.monthly_loss_ratio <= policy.monthly_loss_pause_new_buys
    if result := record("monthly_loss_pause_allows_order", not monthly_pause_buy, "monthly pause blocks new automatic buys"):
        return result

    conflict_key = f"{registry_entry.strategy_id}:{order_plan.intent.symbol}:{order_plan.intent.side}"
    if result := record("no_unfilled_conflicting_order", conflict_key not in set(guardrail_state.unfilled_order_keys), "no matching unfilled order may exist"):
        return result
    if result := record(
        "no_unresolved_paper_buy_order",
        order_plan.intent.side != "buy"
        or not guardrail_state.unresolved_paper_buy_order,
        "an unresolved paper buy blocks additional buys",
    ):
        return result
    if result := record("idempotency_key_new", order_plan.idempotency_key not in seen and order_plan.idempotency_key not in set(guardrail_state.submitted_idempotency_keys), "idempotency key must be new"):
        return result

    risk_check = run_risk_check(
        policy=policy,
        order_plan=order_plan,
        snapshot=snapshot,
        seen_idempotency_keys=seen,
        guardrail_state=guardrail_state,
        quote_max_age_seconds=policy.stale_quote_max_age_seconds,
        strategy_id=registry_entry.strategy_id,
        now=current_time,
        position_binding=position_binding,
        market_quote=market_quote,
        snapshot_max_age_seconds=snapshot_max_age_seconds,
    )
    if result := record("fresh_risk_check_passed", risk_check.passed, ",".join(risk_check.failed_checks) or "risk check passed"):
        return result

    return AuthorityCheckResult(
        authorized=True,
        policy_version=policy.version,
        steps=steps,
        first_failed_check=None,
    )


def transition_order_plan(
    *,
    order_plan: OrderPlan,
    new_status: OrderStatus,
    audit: AuditRecorder,
    user_id: str,
    source: str,
    action: str | None = None,
) -> OrderPlan:
    if new_status not in VALID_TRANSITIONS[order_plan.status]:
        raise InvalidOrderTransition(f"invalid order transition: {order_plan.status.value} -> {new_status.value}")

    before = order_plan.model_copy(deep=True)
    order_plan.status = new_status
    order_plan.updated_at = utc_now()
    audit.emit(
        user_id=user_id,
        entity_type="order_plan",
        entity_id=order_plan.order_plan_id,
        action=action or ACTION_BY_STATUS[new_status],
        before_state=before,
        after_state=order_plan,
        source=source,
    )
    return order_plan
