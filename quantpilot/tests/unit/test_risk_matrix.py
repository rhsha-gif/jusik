from __future__ import annotations

from datetime import timedelta

from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionBinding,
    ManagedPositionState,
)
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.schemas import (
    GuardrailState,
    OrderIntent,
    OrderPlan,
    OrderType,
    PortfolioSnapshot,
    ProposalExplanation,
    UserPolicy,
    utc_now,
)


def _order(
    policy: UserPolicy,
    *,
    side: str = "buy",
    symbol: str = "AAA",
    notional: float = 500_000,
    purpose: str = "rebalance",
) -> OrderPlan:
    intent = OrderIntent(
        symbol=symbol,
        side=side,
        order_type=OrderType.limit,
        quantity=notional / 100,
        limit_price=100,
        notional=notional,
        target_weight=notional / 10_000_000,
        reason="risk matrix test",
    )
    key = f"idem-{symbol}-{side}-{notional}"
    explanation = None
    if purpose != "rebalance":
        explanation = ProposalExplanation(
            symbol=symbol,
            action=side,
            quantity=intent.quantity,
            target_weight_delta=0.0,
            reference_price=100,
            estimated_cash_impact=-notional if side == "sell" else notional,
            strategy_id="pullback_trend_v1",
            strategy_version="1.0",
            signal_reason="protective risk test",
            current_weight=0.10,
            target_weight=intent.target_weight,
            weight_delta=intent.target_weight - 0.10,
            quote_price=100,
            quote_age_seconds=0,
            estimated_notional=notional,
            idempotency_key=key,
            policy_version=policy.version,
        )
    return OrderPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        intent=intent,
        purpose=purpose,
        idempotency_key=key,
        explanation=explanation,
    )


def _risk_evidence(
    policy: UserPolicy,
    snapshot: PortfolioSnapshot,
    order: OrderPlan,
) -> tuple[ManagedPositionBinding, Quote]:
    position = next(
        position
        for position in snapshot.positions
        if position.symbol == order.intent.symbol
    )
    state = ManagedPositionState(
        policy_id=policy.policy_id,
        strategy_id="pullback_trend_v1",
        strategy_version="1.0",
        symbol=position.symbol,
        quantity=position.quantity,
        average_entry_price=100,
        atr14=2,
        active_stop=96,
        policy_version=policy.version,
        opened_at=snapshot.captured_at - timedelta(days=1),
        updated_at=snapshot.captured_at,
        reconciled_snapshot_id=snapshot.snapshot_id,
        reconciled_at=snapshot.captured_at,
    )
    return ManagedPositionBinding.from_position(state), Quote(
        symbol=order.intent.symbol,
        last=100,
        bid=order.intent.limit_price,
        as_of=order.intent.quote_time,
    )


def test_max_daily_order_count_blocks_new_auto_order() -> None:
    policy = UserPolicy(max_daily_orders=3)
    state = GuardrailState(daily_order_count=3)

    risk = run_risk_check(policy=policy, order_plan=_order(policy), snapshot=fixture_portfolio_snapshot(), guardrail_state=state)

    assert not risk.passed
    assert "max_daily_orders" in risk.failed_checks


def test_max_daily_turnover_blocks_new_auto_order() -> None:
    policy = UserPolicy(max_daily_turnover=1_000_000)
    state = GuardrailState(daily_turnover_used=750_000)

    risk = run_risk_check(policy=policy, order_plan=_order(policy, notional=500_000), snapshot=fixture_portfolio_snapshot(), guardrail_state=state)

    assert not risk.passed
    assert "max_daily_turnover" in risk.failed_checks


def test_monthly_loss_pause_blocks_new_buys_but_allows_risk_reducing_sells() -> None:
    policy = UserPolicy()
    snapshot = fixture_portfolio_snapshot(monthly_loss_ratio=-0.06)

    buy_risk = run_risk_check(policy=policy, order_plan=_order(policy, side="buy", symbol="AAA"), snapshot=snapshot)
    sell_risk = run_risk_check(policy=policy, order_plan=_order(policy, side="sell", symbol="CCC"), snapshot=snapshot)

    assert "monthly_loss_pause_new_buys" in buy_risk.failed_checks
    assert "monthly_loss_pause_new_buys" not in sell_risk.failed_checks
    assert sell_risk.passed


def test_monthly_loss_stop_blocks_all_autotrading() -> None:
    policy = UserPolicy()
    snapshot = fixture_portfolio_snapshot(monthly_loss_ratio=-0.11)

    risk = run_risk_check(policy=policy, order_plan=_order(policy, side="sell", symbol="CCC"), snapshot=snapshot)

    assert not risk.passed
    assert "monthly_loss_stop_all_autotrading" in risk.failed_checks


def test_monthly_loss_stop_allows_only_verified_protective_sell() -> None:
    policy = UserPolicy()
    snapshot = fixture_portfolio_snapshot(monthly_loss_ratio=-0.11)

    protective_order = _order(
        policy,
        side="sell",
        symbol="CCC",
        purpose="protective_exit",
    )
    binding, quote = _risk_evidence(policy, snapshot, protective_order)
    protective = run_risk_check(
        policy=policy,
        order_plan=protective_order,
        snapshot=snapshot,
        position_binding=binding,
        market_quote=quote,
    )
    forged_buy = run_risk_check(
        policy=policy,
        order_plan=_order(
            policy,
            side="buy",
            symbol="CCC",
            purpose="protective_exit",
        ),
        snapshot=snapshot,
    )

    assert protective.passed
    assert "monthly_loss_stop_all_autotrading" not in protective.failed_checks
    assert not forged_buy.passed
    assert "risk_reducing_purpose_verified" in forged_buy.failed_checks


def test_claimed_protective_sell_cannot_exceed_reconciled_holding() -> None:
    policy = UserPolicy(single_order_cash_limit=2_000_000, max_daily_turnover=3_000_000)
    snapshot = fixture_portfolio_snapshot(monthly_loss_ratio=-0.11)
    oversell = _order(
        policy,
        side="sell",
        symbol="CCC",
        notional=1_100_000,
        purpose="protective_exit",
    )

    binding, quote = _risk_evidence(policy, snapshot, oversell)
    risk = run_risk_check(
        policy=policy,
        order_plan=oversell,
        snapshot=snapshot,
        position_binding=binding,
        market_quote=quote,
    )

    assert not risk.passed
    assert "risk_reducing_purpose_verified" in risk.failed_checks
    assert "monthly_loss_stop_all_autotrading" in risk.failed_checks


def test_kill_switch_blocks_risk_check() -> None:
    policy = UserPolicy(kill_switch_engaged=True)

    risk = run_risk_check(policy=policy, order_plan=_order(policy), snapshot=fixture_portfolio_snapshot())

    assert not risk.passed
    assert "kill_switch_not_engaged" in risk.failed_checks


def test_kill_switch_still_blocks_verified_protective_sell() -> None:
    policy = UserPolicy(kill_switch_engaged=True)
    order = _order(
        policy,
        side="sell",
        symbol="CCC",
        purpose="protective_exit",
    )

    snapshot = fixture_portfolio_snapshot(monthly_loss_ratio=-0.11)
    binding, quote = _risk_evidence(policy, snapshot, order)
    risk = run_risk_check(
        policy=policy,
        order_plan=order,
        snapshot=snapshot,
        position_binding=binding,
        market_quote=quote,
    )

    assert not risk.passed
    assert "kill_switch_not_engaged" in risk.failed_checks


def test_stale_quote_uses_human_review_window_for_proposals() -> None:
    policy = UserPolicy(stale_quote_max_age_seconds=30, human_review_quote_max_age_seconds=120)
    order = _order(policy)
    order.intent.quote_time = utc_now() - timedelta(seconds=90)

    human_review = run_risk_check(
        policy=policy,
        order_plan=order,
        snapshot=fixture_portfolio_snapshot(),
        quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
    )
    auto_submit = run_risk_check(
        policy=policy,
        order_plan=order,
        snapshot=fixture_portfolio_snapshot(),
        quote_max_age_seconds=policy.stale_quote_max_age_seconds,
    )

    assert human_review.passed
    assert "quote_not_stale" in auto_submit.failed_checks


def test_unfilled_conflicting_order_blocks_duplicate_symbol_side_strategy() -> None:
    policy = UserPolicy()
    state = GuardrailState(unfilled_order_keys=["pullback_trend_v1:AAA:buy"])

    risk = run_risk_check(
        policy=policy,
        order_plan=_order(policy, side="buy", symbol="AAA"),
        snapshot=fixture_portfolio_snapshot(),
        guardrail_state=state,
        strategy_id="pullback_trend_v1",
    )

    assert not risk.passed
    assert "unfilled_conflicting_order" in risk.failed_checks
