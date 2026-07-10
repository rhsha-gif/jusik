from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from quantpilot.packages.core.risk.gatekeeper import (
    RISK_REDUCING_PURPOSES,
    aware_age_seconds,
    allowed_execution_modes,
    is_verified_risk_reducing_order,
    market_orders_enabled,
)
from quantpilot.packages.core.operator.position_ledger import ManagedPositionBinding
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.risk.types import BatchPortfolioExposure, BatchRiskConfig, BatchRiskDecision, BatchRiskInput
from quantpilot.packages.core.schemas import (
    BrokerMode,
    GuardrailState,
    OrderIntent,
    OrderPlan,
    OrderType,
    PortfolioPlan,
    PortfolioSnapshot,
    UserPolicy,
    utc_now,
)


@dataclass(frozen=True)
class _Candidate:
    intent: OrderIntent
    order_plan: OrderPlan | None = None
    order_plan_id: str | None = None
    policy_id: str | None = None
    idempotency_key: str | None = None
    policy_version: int | None = None


@dataclass(frozen=True)
class _BatchEvaluation:
    passed: bool
    passed_checks: list[str]
    failed_checks: list[str]
    stale_input_reasons: list[str]
    portfolio_after_batch: BatchPortfolioExposure


def _symbol(value: str) -> str:
    return value.strip().upper()


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _candidate_key(candidate: _Candidate) -> str:
    return candidate.order_plan_id or candidate.intent.intent_id


def _candidates_from_plan(
    *,
    portfolio_plan: PortfolioPlan,
    order_plans: Sequence[OrderPlan] | None,
) -> list[_Candidate]:
    if order_plans is not None:
        return [
            _Candidate(
                intent=order.intent,
                order_plan=order,
                order_plan_id=order.order_plan_id,
                policy_id=order.policy_id,
                idempotency_key=order.idempotency_key,
                policy_version=order.policy_version,
            )
            for order in order_plans
        ]
    return [
        _Candidate(
            intent=intent,
            policy_version=portfolio_plan.policy_version,
        )
        for intent in portfolio_plan.order_intents
    ]


def _initial_position_values(snapshot: PortfolioSnapshot) -> dict[str, float]:
    values: dict[str, float] = {}
    for position in snapshot.positions:
        symbol = _symbol(position.symbol)
        values[symbol] = round(values.get(symbol, 0.0) + position.market_value, 2)
    return values


def _sector_by_symbol(snapshot: PortfolioSnapshot) -> dict[str, str]:
    return {_symbol(position.symbol): position.sector for position in snapshot.positions}


def _build_exposure(
    *,
    snapshot: PortfolioSnapshot,
    candidates: Sequence[_Candidate],
) -> tuple[BatchPortfolioExposure, list[str]]:
    cash = snapshot.cash
    position_values = _initial_position_values(snapshot)
    position_quantities: dict[str, float] = {}
    for position in snapshot.positions:
        symbol = _symbol(position.symbol)
        position_quantities[symbol] = (
            position_quantities.get(symbol, 0.0) + position.quantity
        )
    sectors = _sector_by_symbol(snapshot)
    oversold_symbols: list[str] = []

    for candidate in candidates:
        intent = candidate.intent
        symbol = _symbol(intent.symbol)
        notional = round(intent.notional, 2)
        if intent.side == "buy":
            cash = round(cash - notional, 2)
            position_values[symbol] = round(position_values.get(symbol, 0.0) + notional, 2)
            position_quantities[symbol] = (
                position_quantities.get(symbol, 0.0) + intent.quantity
            )
        else:
            current_value = position_values.get(symbol, 0.0)
            current_quantity = position_quantities.get(symbol, 0.0)
            if intent.quantity > current_quantity + 0.000001:
                oversold_symbols.append(symbol)
            sale_quantity = min(intent.quantity, current_quantity)
            remaining_quantity = max(0.0, current_quantity - sale_quantity)
            sale_price = (
                intent.limit_price
                if intent.limit_price is not None
                else notional / intent.quantity
            )
            cash = round(cash + sale_quantity * sale_price, 2)
            position_quantities[symbol] = remaining_quantity
            position_values[symbol] = round(
                current_value * remaining_quantity / current_quantity
                if current_quantity > 0
                else 0.0,
                2,
            )

    position_values = {symbol: value for symbol, value in position_values.items() if value > 0.000001}
    position_weights = {
        symbol: round(value / snapshot.equity, 6)
        for symbol, value in sorted(position_values.items())
    }
    sector_values: dict[str, float] = {}
    for symbol, value in position_values.items():
        sector = sectors.get(symbol, "unknown")
        sector_values[sector] = round(sector_values.get(sector, 0.0) + value, 2)
    sector_weights = {
        sector: round(value / snapshot.equity, 6)
        for sector, value in sorted(sector_values.items())
    }
    return (
        BatchPortfolioExposure(
            cash=round(cash, 2),
            equity=snapshot.equity,
            cash_weight=round(cash / snapshot.equity, 6),
            position_values=dict(sorted(position_values.items())),
            position_weights=position_weights,
            sector_values=dict(sorted(sector_values.items())),
            sector_weights=sector_weights,
        ),
        _unique(oversold_symbols),
    )


def _quote_input_available(intent: OrderIntent, quotes: dict[str, float]) -> bool:
    quote = quotes.get(_symbol(intent.symbol), quotes.get(intent.symbol))
    if quote is not None:
        return quote > 0
    return intent.limit_price is not None and intent.limit_price > 0


def _evaluate_candidates(
    *,
    policy: UserPolicy,
    portfolio_plan: PortfolioPlan,
    snapshot: PortfolioSnapshot,
    candidates: Sequence[_Candidate],
    quotes: dict[str, float],
    config: BatchRiskConfig,
    guardrail_state: GuardrailState,
    seen_idempotency_keys: set[str],
    now: datetime,
    position_bindings: dict[str, ManagedPositionBinding],
    market_quotes: dict[str, Quote],
) -> _BatchEvaluation:
    exposure, oversold_symbols = _build_exposure(snapshot=snapshot, candidates=candidates)
    before_exposure, _ = _build_exposure(snapshot=snapshot, candidates=[])
    sectors = _sector_by_symbol(snapshot)
    passed: list[str] = []
    failed: list[str] = []
    stale_reasons: list[str] = []

    def check(name: str, condition: bool) -> None:
        if condition:
            passed.append(name)
        else:
            failed.append(name)

    snapshot_age = aware_age_seconds(now, snapshot.captured_at)
    if snapshot_age is None or not 0 <= snapshot_age <= config.snapshot_max_age_seconds:
        stale_reasons.append("snapshot_stale")

    stale_quote_symbols = [
        _symbol(candidate.intent.symbol)
        for candidate in candidates
        if (
            (age := aware_age_seconds(now, candidate.intent.quote_time)) is None
            or not 0 <= age <= config.quote_max_age_seconds
        )
    ]
    stale_reasons.extend(f"quote_stale:{symbol}" for symbol in _unique(stale_quote_symbols))

    candidate_policy_versions = [
        candidate.policy_version
        for candidate in candidates
        if candidate.policy_version is not None
    ]
    candidate_policy_ids = [
        candidate.policy_id
        for candidate in candidates
        if candidate.policy_id is not None
    ]
    idempotency_keys = [
        candidate.idempotency_key
        for candidate in candidates
        if candidate.idempotency_key is not None
    ]
    seen_keys = set(seen_idempotency_keys).union(guardrail_state.submitted_idempotency_keys)
    batch_notional = round(sum(candidate.intent.notional for candidate in candidates), 2)
    has_buy = any(candidate.intent.side == "buy" for candidate in candidates)
    claimed_risk_reducing = [
        candidate
        for candidate in candidates
        if candidate.order_plan is not None
        and candidate.order_plan.purpose in RISK_REDUCING_PURPOSES
    ]
    claimed_risk_reducing_valid = all(
        candidate.order_plan is not None
        and is_verified_risk_reducing_order(
            candidate.order_plan,
            snapshot,
            position_binding=position_bindings.get(candidate.order_plan.order_plan_id),
            market_quote=market_quotes.get(candidate.order_plan.order_plan_id),
            reserved_sell_quantities=guardrail_state.reserved_sell_quantities,
            now=now,
            quote_max_age_seconds=config.quote_max_age_seconds,
        )
        for candidate in claimed_risk_reducing
    )
    all_verified_risk_reducing = bool(candidates) and all(
        candidate.order_plan is not None
        and is_verified_risk_reducing_order(
            candidate.order_plan,
            snapshot,
            position_binding=position_bindings.get(candidate.order_plan.order_plan_id),
            market_quote=market_quotes.get(candidate.order_plan.order_plan_id),
            reserved_sell_quantities=guardrail_state.reserved_sell_quantities,
            now=now,
            quote_max_age_seconds=config.quote_max_age_seconds,
        )
        for candidate in candidates
    )

    held_quantities: dict[str, float] = {}
    for position in snapshot.positions:
        symbol = _symbol(position.symbol)
        held_quantities[symbol] = held_quantities.get(symbol, 0.0) + position.quantity
    requested_sell_quantities: dict[str, float] = {}
    for candidate in candidates:
        if candidate.intent.side != "sell":
            continue
        symbol = _symbol(candidate.intent.symbol)
        requested_sell_quantities[symbol] = (
            requested_sell_quantities.get(symbol, 0.0) + candidate.intent.quantity
        )
    reserved_sell_quantities: dict[str, float] = {}
    for symbol, quantity in guardrail_state.reserved_sell_quantities.items():
        normalized = _symbol(symbol)
        reserved_sell_quantities[normalized] = (
            reserved_sell_quantities.get(normalized, 0.0) + quantity
        )
    aggregate_sell_quantities_ok = all(
        requested_quantity + reserved_sell_quantities.get(symbol, 0.0)
        <= held_quantities.get(symbol, 0.0) + 0.000001
        for symbol, requested_quantity in requested_sell_quantities.items()
    )

    order_types_allowed = all(candidate.intent.order_type in policy.allowed_order_types for candidate in candidates)
    if any(candidate.intent.order_type == OrderType.market for candidate in candidates) and not market_orders_enabled():
        order_types_allowed = False
    notionals_match = all(
        candidate.intent.order_type != OrderType.limit
        or (
            candidate.intent.limit_price is not None
            and abs(
                candidate.intent.quantity * candidate.intent.limit_price
                - candidate.intent.notional
            )
            <= max(0.01, candidate.intent.notional * 0.000001)
        )
        for candidate in candidates
    )

    touched_symbols = {_symbol(candidate.intent.symbol) for candidate in candidates}
    touched_sectors = {sectors.get(symbol, "unknown") for symbol in touched_symbols}
    max_position_ok = not any(
        exposure.position_weights.get(symbol, 0.0) > policy.max_position_weight
        and exposure.position_weights.get(symbol, 0.0) > before_exposure.position_weights.get(symbol, 0.0)
        for symbol in touched_symbols
    )
    max_sector_ok = not any(
        exposure.sector_weights.get(sector, 0.0) > policy.max_sector_weight
        and exposure.sector_weights.get(sector, 0.0) > before_exposure.sector_weights.get(sector, 0.0)
        for sector in touched_sectors
    )

    check("batch_not_empty", bool(candidates))
    check("kill_switch_not_engaged", not policy.kill_switch_engaged and not guardrail_state.kill_switch_engaged)
    check(
        "policy_identity_match",
        portfolio_plan.policy_id == policy.policy_id
        and snapshot.user_id == policy.user_id
        and all(policy_id == policy.policy_id for policy_id in candidate_policy_ids),
    )
    check("policy_version_match", portfolio_plan.policy_version == policy.version and all(version == policy.version for version in candidate_policy_versions))
    check("execution_mode_allowed", policy.execution_mode in allowed_execution_modes(policy))
    check("broker_mode_not_live", policy.broker != BrokerMode.live_disabled)
    check("risk_check_not_expired", True)
    check("risk_reducing_purpose_verified", claimed_risk_reducing_valid)
    check("snapshot_not_stale", "snapshot_stale" not in stale_reasons)
    check("quotes_not_stale", not any(reason.startswith("quote_stale:") for reason in stale_reasons))
    check("quotes_available", all(_quote_input_available(candidate.intent, quotes) for candidate in candidates))
    check("available_cash_after_batch", exposure.cash >= 0)
    check("min_cash_after_batch", exposure.cash >= policy.min_cash_weight * snapshot.equity)
    check("max_position_weight_after_batch", max_position_ok)
    check("max_concentration_weight_after_batch", max_position_ok)
    check("max_sector_weight_after_batch", max_sector_ok)
    check(
        "no_short_sell_after_batch",
        not oversold_symbols and aggregate_sell_quantities_ok,
    )
    check("max_daily_orders_after_batch", guardrail_state.daily_order_count + len(candidates) <= policy.max_daily_orders)
    check("max_daily_turnover_after_batch", guardrail_state.daily_turnover_used + batch_notional <= policy.max_daily_turnover)
    check("order_type_allowed", order_types_allowed)
    check("order_notional_matches_quantity_price", notionals_match)
    check("idempotency_keys_not_seen", len(idempotency_keys) == len(set(idempotency_keys)) and not any(key in seen_keys for key in idempotency_keys))
    check("daily_loss_limit_not_triggered", not (has_buy and snapshot.daily_loss_ratio <= policy.daily_loss_limit))
    check("monthly_loss_pause_new_buys", not (has_buy and snapshot.monthly_loss_ratio <= policy.monthly_loss_pause_new_buys))
    check(
        "monthly_loss_stop_all_autotrading",
        snapshot.monthly_loss_ratio > policy.monthly_loss_stop_all_autotrading
        or all_verified_risk_reducing,
    )

    return _BatchEvaluation(
        passed=not failed,
        passed_checks=passed,
        failed_checks=failed,
        stale_input_reasons=_unique(stale_reasons),
        portfolio_after_batch=exposure,
    )


def _decision_reasons(evaluation: _BatchEvaluation) -> list[str]:
    return _unique([*evaluation.failed_checks, *evaluation.stale_input_reasons])


def _build_decision(
    *,
    policy: UserPolicy,
    mode: str,
    accepted: Sequence[_Candidate],
    rejected: Sequence[_Candidate],
    evaluation: _BatchEvaluation,
    rejected_reasons: dict[str, list[str]] | None = None,
) -> BatchRiskDecision:
    return BatchRiskDecision(
        passed=mode != "rejected",
        mode=mode,  # type: ignore[arg-type]
        policy_version=policy.version,
        accepted_intent_ids=[candidate.intent.intent_id for candidate in accepted],
        rejected_intent_ids=[candidate.intent.intent_id for candidate in rejected],
        accepted_order_plan_ids=[
            candidate.order_plan_id
            for candidate in accepted
            if candidate.order_plan_id is not None
        ],
        rejected_order_plan_ids=[
            candidate.order_plan_id
            for candidate in rejected
            if candidate.order_plan_id is not None
        ],
        passed_checks=evaluation.passed_checks,
        failed_checks=evaluation.failed_checks,
        rejected_reasons=rejected_reasons or {},
        stale_input_reasons=evaluation.stale_input_reasons,
        portfolio_after_batch=evaluation.portfolio_after_batch,
    )


def run_batch_risk_gate(
    *,
    policy: UserPolicy,
    portfolio_plan: PortfolioPlan,
    snapshot: PortfolioSnapshot,
    quotes: dict[str, float] | None = None,
    order_plans: Sequence[OrderPlan] | None = None,
    config: BatchRiskConfig | None = None,
    guardrail_state: GuardrailState | None = None,
    seen_idempotency_keys: set[str] | None = None,
    now: datetime | None = None,
    position_bindings: dict[str, ManagedPositionBinding] | None = None,
    market_quotes: dict[str, Quote] | None = None,
) -> BatchRiskDecision:
    risk_config = config or BatchRiskConfig()
    state = guardrail_state or GuardrailState()
    current_time = now or utc_now()
    normalized_quotes = {_symbol(symbol): price for symbol, price in (quotes or {}).items()}
    seen_keys = seen_idempotency_keys or set()
    bindings = position_bindings or {}
    trusted_quotes = market_quotes or {}
    candidates = _candidates_from_plan(portfolio_plan=portfolio_plan, order_plans=order_plans)

    full_evaluation = _evaluate_candidates(
        policy=policy,
        portfolio_plan=portfolio_plan,
        snapshot=snapshot,
        candidates=candidates,
        quotes=normalized_quotes,
        config=risk_config,
        guardrail_state=state,
        seen_idempotency_keys=seen_keys,
        now=current_time,
        position_bindings=bindings,
        market_quotes=trusted_quotes,
    )
    if full_evaluation.passed:
        return _build_decision(
            policy=policy,
            mode="full_batch",
            accepted=candidates,
            rejected=[],
            evaluation=full_evaluation,
        )

    if not risk_config.partial_allow:
        reasons = _decision_reasons(full_evaluation)
        return _build_decision(
            policy=policy,
            mode="rejected",
            accepted=[],
            rejected=candidates,
            evaluation=full_evaluation,
            rejected_reasons={_candidate_key(candidate): reasons for candidate in candidates},
        )

    accepted: list[_Candidate] = []
    rejected: list[_Candidate] = []
    rejected_reasons: dict[str, list[str]] = {}
    for candidate in candidates:
        trial = [*accepted, candidate]
        trial_evaluation = _evaluate_candidates(
            policy=policy,
            portfolio_plan=portfolio_plan,
            snapshot=snapshot,
            candidates=trial,
            quotes=normalized_quotes,
            config=risk_config,
            guardrail_state=state,
            seen_idempotency_keys=seen_keys,
            now=current_time,
            position_bindings=bindings,
            market_quotes=trusted_quotes,
        )
        if trial_evaluation.passed:
            accepted.append(candidate)
            continue
        rejected.append(candidate)
        rejected_reasons[_candidate_key(candidate)] = _decision_reasons(trial_evaluation)

    accepted_evaluation = _evaluate_candidates(
        policy=policy,
        portfolio_plan=portfolio_plan,
        snapshot=snapshot,
        candidates=accepted,
        quotes=normalized_quotes,
        config=risk_config,
        guardrail_state=state,
        seen_idempotency_keys=seen_keys,
        now=current_time,
        position_bindings=bindings,
        market_quotes=trusted_quotes,
    )
    if accepted and accepted_evaluation.passed:
        return _build_decision(
            policy=policy,
            mode="partial_batch",
            accepted=accepted,
            rejected=rejected,
            evaluation=accepted_evaluation,
            rejected_reasons=rejected_reasons,
        )

    fallback_reasons = _decision_reasons(full_evaluation)
    return _build_decision(
        policy=policy,
        mode="rejected",
        accepted=[],
        rejected=candidates,
        evaluation=full_evaluation,
        rejected_reasons={
            _candidate_key(candidate): rejected_reasons.get(_candidate_key(candidate), fallback_reasons)
            for candidate in candidates
        },
    )


def run_batch_risk_gate_from_input(
    *,
    policy: UserPolicy,
    batch_input: BatchRiskInput,
    order_plans: Sequence[OrderPlan] | None = None,
) -> BatchRiskDecision:
    return run_batch_risk_gate(
        policy=policy,
        portfolio_plan=batch_input.portfolio_plan,
        snapshot=batch_input.snapshot,
        quotes=batch_input.quotes,
        order_plans=order_plans,
        config=batch_input.config,
        guardrail_state=batch_input.guardrail_state,
        seen_idempotency_keys=set(batch_input.seen_idempotency_keys),
        now=batch_input.now,
    )
