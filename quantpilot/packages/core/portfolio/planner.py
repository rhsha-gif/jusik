from __future__ import annotations

from datetime import date
from hashlib import sha256

from quantpilot.packages.core.portfolio.optimizer import DeterministicPortfolioOptimizer
from quantpilot.packages.core.portfolio.optimizer_types import (
    ExpectedReturnRiskProxy,
    OptimizationConstraints,
    OptimizationInput,
)
from quantpilot.packages.core.schemas import (
    DataMode,
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


DEFAULT_REBALANCE_BAND = 0.001


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


def current_weight(snapshot: PortfolioSnapshot, symbol: str) -> float:
    return _current_weight(snapshot, symbol)


def proposal_idempotency_key(
    *,
    policy: UserPolicy,
    strategy_id: str,
    strategy_version: str,
    symbol: str,
    side: str,
    trading_date: str | date,
) -> str:
    raw = (
        f"{policy.policy_id}:{policy.version}:{strategy_id}:{strategy_version}:"
        f"{symbol}:{side}:{trading_date}"
    )
    return sha256(raw.encode("utf-8")).hexdigest()[:32]


def _quote_for_symbol(snapshot: PortfolioSnapshot, symbol: str, quotes: dict[str, float] | None) -> float:
    if quotes and symbol in quotes:
        return quotes[symbol]
    for position in snapshot.positions:
        if position.symbol == symbol:
            return position.market_price
    return 100.0


def _symbol(value: str) -> str:
    return value.strip().upper()


def _cash_target_from_targets(
    *,
    snapshot: PortfolioSnapshot,
    target_weights: dict[str, float],
) -> float:
    planned_symbols = {_symbol(symbol) for symbol in target_weights}
    other_weight = sum(
        position.market_value / snapshot.equity
        for position in snapshot.positions
        if _symbol(position.symbol) not in planned_symbols
    )
    invested_target = other_weight + sum(target_weights.values())
    return round(max(0.0, min(1.0, 1.0 - invested_target)), 6)


def _volatility_proxy_from_signal(signal: Signal) -> float:
    scores = [score for score in (signal.technical_score, signal.quant_score) if score is not None]
    quality_score = sum(scores) / len(scores) if scores else signal.strength * 100
    return round(max(0.05, min(1.0, (100.0 - quality_score) / 100.0)), 6)


def _expected_return_proxy_from_signal(signal: Signal) -> float:
    if signal.action in {SignalAction.blocked, SignalAction.exit}:
        return -signal.strength
    if signal.action == SignalAction.trim:
        return signal.strength * 0.25
    if signal.action == SignalAction.buy_wait:
        return 0.0
    return signal.strength


def _uncalibrated_proxy_from_signal(signal: Signal) -> ExpectedReturnRiskProxy:
    return ExpectedReturnRiskProxy(
        symbol=signal.symbol,
        expected_return=_expected_return_proxy_from_signal(signal),
        volatility=_volatility_proxy_from_signal(signal),
        expected_return_source="uncalibrated_signal_strength",
        volatility_source="uncalibrated_signal_quality_gap",
        calibrated=False,
        data_mode=DataMode.fixture,
        metadata={
            "signal_id": signal.signal_id,
            "action": signal.action.value,
            "source": signal.source,
        },
    )


def constraints_from_policy(
    *,
    policy: UserPolicy,
    snapshot: PortfolioSnapshot,
    rebalance_band: float = DEFAULT_REBALANCE_BAND,
) -> OptimizationConstraints:
    return OptimizationConstraints(
        max_position_weight=policy.max_position_weight,
        max_sector_weight=policy.max_sector_weight,
        min_cash_weight=policy.min_cash_weight,
        max_turnover_weight=round(min(2.0, policy.max_daily_turnover / snapshot.equity), 6),
        rebalance_band=rebalance_band,
        max_order_weight=None,
    )


def build_optimization_input(
    *,
    policy: UserPolicy,
    signals: list[Signal],
    snapshot: PortfolioSnapshot,
    expected_return_risk_proxies: dict[str, ExpectedReturnRiskProxy] | None = None,
    sector_metadata: dict[str, str] | None = None,
    optimizer_constraints: OptimizationConstraints | None = None,
    rebalance_band: float = DEFAULT_REBALANCE_BAND,
) -> OptimizationInput:
    snapshot_sectors = {_symbol(position.symbol): position.sector for position in snapshot.positions}
    proxies = expected_return_risk_proxies or {
        _symbol(signal.symbol): _uncalibrated_proxy_from_signal(signal)
        for signal in signals
    }
    return OptimizationInput(
        signals=signals,
        proxies=proxies,
        sector_metadata={**snapshot_sectors, **(sector_metadata or {})},
        snapshot=snapshot,
        constraints=optimizer_constraints or constraints_from_policy(
            policy=policy,
            snapshot=snapshot,
            rebalance_band=rebalance_band,
        ),
        risk_budget={
            "max_daily_turnover": policy.max_daily_turnover,
            "single_order_cash_limit": policy.single_order_cash_limit,
        },
        data_mode=DataMode.fixture,
        proxy_metadata={
            "calibrated": False,
            "source": "planner_adapter_uncalibrated_signal_proxy",
        },
    )


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
    expected_return_risk_proxies: dict[str, ExpectedReturnRiskProxy] | None = None,
    sector_metadata: dict[str, str] | None = None,
    optimizer_constraints: OptimizationConstraints | None = None,
    rebalance_band: float = DEFAULT_REBALANCE_BAND,
) -> PortfolioPlan:
    optimization_input = build_optimization_input(
        policy=policy,
        signals=signals,
        snapshot=snapshot,
        expected_return_risk_proxies=expected_return_risk_proxies,
        sector_metadata=sector_metadata,
        optimizer_constraints=optimizer_constraints,
        rebalance_band=rebalance_band,
    )
    optimization_result = DeterministicPortfolioOptimizer().optimize(optimization_input)
    optimized_targets = {
        target.symbol: target.target_weight
        for target in optimization_result.target_weights
    }
    target_weights: dict[str, float] = {}
    order_intents: list[OrderIntent] = []
    current_weights = {_symbol(position.symbol): _current_weight(snapshot, position.symbol) for position in snapshot.positions}
    available_to_spend = max(0.0, snapshot.cash - policy.min_cash_weight * snapshot.equity)

    for signal in signals:
        symbol = _symbol(signal.symbol)
        current = current_weights.get(symbol, 0.0)
        target = optimized_targets.get(symbol, _initial_target(policy, signal, current))
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
            notional = min(abs(delta_notional), policy.single_order_cash_limit, current * snapshot.equity)
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

        if optimization_result.status == "fail_closed":
            target_weights[signal.symbol] = round(max(0.0, target), 6)
        else:
            target_weights[signal.symbol] = round(max(0.0, min(target, policy.max_position_weight)), 6)

    cash_target_weight = _cash_target_from_targets(snapshot=snapshot, target_weights=target_weights)
    if optimization_result.status != "fail_closed":
        cash_target_weight = max(policy.min_cash_weight, cash_target_weight)
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
