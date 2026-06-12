from __future__ import annotations

from quantpilot.packages.core.schemas import (
    OrderIntent,
    OrderType,
    PortfolioPlan,
    PortfolioPosition,
    PortfolioSnapshot,
    RebalanceSuggestion,
    RebalanceSuggestionReport,
    Signal,
    SignalAction,
    UserPolicy,
)


def fixture_portfolio_snapshot(*, monthly_loss_ratio: float = 0.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=6_000_000,
        equity=10_000_000,
        positions=[
            PortfolioPosition(symbol="CCC", quantity=10_000, market_price=100, sector="tech"),
            PortfolioPosition(symbol="DDD", quantity=20_000, market_price=100, sector="tech"),
            PortfolioPosition(symbol="EEE", quantity=10_000, market_price=100, sector="industrial"),
        ],
        monthly_loss_ratio=monthly_loss_ratio,
    )


def _current_weight(snapshot: PortfolioSnapshot, symbol: str) -> float:
    position_value = sum(position.market_value for position in snapshot.positions if position.symbol == symbol)
    return position_value / snapshot.equity


def _quote_for_symbol(snapshot: PortfolioSnapshot, symbol: str, quotes: dict[str, float] | None) -> float:
    if quotes and symbol in quotes:
        return quotes[symbol]
    for position in snapshot.positions:
        if position.symbol == symbol:
            return position.market_price
    return 100.0


def _initial_target(policy: UserPolicy, signal: Signal, current_weight: float) -> float:
    if signal.action in {SignalAction.blocked, SignalAction.exit, SignalAction.buy_wait}:
        return 0.0
    if signal.action == SignalAction.buy_ready:
        return min(policy.max_position_weight, max(0.01, signal.strength * policy.max_position_weight))
    if signal.action == SignalAction.trim:
        return max(0.0, min(current_weight * 0.5, policy.max_position_weight))
    if signal.action == SignalAction.hold:
        return min(current_weight, policy.max_position_weight)
    return current_weight


def build_portfolio_plan(
    *,
    policy: UserPolicy,
    signals: list[Signal],
    snapshot: PortfolioSnapshot,
    quotes: dict[str, float] | None = None,
) -> PortfolioPlan:
    target_weights: dict[str, float] = {}
    order_intents: list[OrderIntent] = []
    current_weights = {position.symbol: _current_weight(snapshot, position.symbol) for position in snapshot.positions}
    available_to_spend = max(0.0, snapshot.cash - policy.min_cash_weight * snapshot.equity)

    for signal in signals:
        current = current_weights.get(signal.symbol, 0.0)
        target = _initial_target(policy, signal, current)
        price = _quote_for_symbol(snapshot, signal.symbol, quotes)
        delta_notional = (target - current) * snapshot.equity

        if delta_notional > 1:
            notional = min(delta_notional, policy.single_order_cash_limit, available_to_spend)
            if notional <= 1:
                target = current
            else:
                target = current + notional / snapshot.equity
                available_to_spend -= notional
                order_intents.append(
                    OrderIntent(
                        symbol=signal.symbol,
                        side="buy",
                        order_type=OrderType.limit,
                        quantity=round(notional / price, 6),
                        limit_price=price,
                        notional=round(notional, 2),
                        target_weight=round(target, 6),
                        reason=signal.reason,
                    )
                )
        elif delta_notional < -1:
            notional = min(abs(delta_notional), policy.single_order_cash_limit)
            if notional > 1:
                target = current - notional / snapshot.equity
                order_intents.append(
                    OrderIntent(
                        symbol=signal.symbol,
                        side="sell",
                        order_type=OrderType.limit,
                        quantity=round(notional / price, 6),
                        limit_price=price,
                        notional=round(notional, 2),
                        target_weight=round(max(target, 0.0), 6),
                        reason=signal.reason,
                    )
                )

        target_weights[signal.symbol] = round(max(0.0, min(target, policy.max_position_weight)), 6)

    invested_target = sum(target_weights.values())
    cash_target_weight = max(policy.min_cash_weight, round(max(0.0, 1 - invested_target), 6))
    return PortfolioPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        target_weights=target_weights,
        cash_target_weight=cash_target_weight,
        order_intents=order_intents,
    )


def build_rebalance_suggestion_report(
    *,
    policy: UserPolicy,
    signals: list[Signal],
    snapshot: PortfolioSnapshot,
    quotes: dict[str, float] | None = None,
) -> RebalanceSuggestionReport:
    executable_shape_plan = build_portfolio_plan(policy=policy, signals=signals, snapshot=snapshot, quotes=quotes)
    suggestion_plan = executable_shape_plan.model_copy(update={"order_intents": []})
    current_weights = {position.symbol: _current_weight(snapshot, position.symbol) for position in snapshot.positions}
    signals_by_symbol = {signal.symbol: signal for signal in signals}
    suggestions: list[RebalanceSuggestion] = []

    for symbol, target in suggestion_plan.target_weights.items():
        current = current_weights.get(symbol, 0.0)
        signal = signals_by_symbol.get(symbol)
        if signal is not None and signal.action == SignalAction.blocked:
            suggested_action = "blocked"
            risk_reason = "blocked_by_signal_reason_codes"
        elif target > current + 0.001:
            suggested_action = "buy"
            risk_reason = "target_weight_above_current_within_policy_limits"
        elif target < current - 0.001:
            suggested_action = "sell"
            risk_reason = "target_weight_below_current_or_risk_reduction"
        else:
            suggested_action = "hold"
            risk_reason = "current_weight_close_to_target"
        if target >= policy.max_position_weight:
            risk_reason = f"{risk_reason}; capped_by_max_position_weight"
        suggestions.append(
            RebalanceSuggestion(
                ticker=symbol,
                current_weight=round(current, 6),
                target_weight_suggestion=round(target, 6),
                cash_target=suggestion_plan.cash_target_weight,
                risk_reason=risk_reason,
                suggested_action=suggested_action,  # type: ignore[arg-type]
            )
        )

    return RebalanceSuggestionReport(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        portfolio_plan=suggestion_plan,
        suggestions=suggestions,
        order_submission_enabled=False,
    )
