from __future__ import annotations

from datetime import timedelta

from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionBinding,
    ManagedPositionState,
)
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.types import BatchRiskConfig
from quantpilot.packages.core.schemas import (
    OrderIntent,
    OrderPlan,
    OrderType,
    PortfolioPlan,
    PortfolioPosition,
    PortfolioSnapshot,
    ProposalExplanation,
    UserPolicy,
    utc_now,
)


def _intent(symbol: str, notional: float, *, quote_age_seconds: int = 0) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        side="buy",
        order_type=OrderType.limit,
        quantity=notional / 100,
        limit_price=100,
        notional=notional,
        target_weight=round(notional / 10_000_000, 6),
        reason="batch risk test",
        quote_time=utc_now() - timedelta(seconds=quote_age_seconds),
    )


def _plan(policy: UserPolicy, intents: list[OrderIntent]) -> PortfolioPlan:
    return PortfolioPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        target_weights={intent.symbol: intent.target_weight for intent in intents},
        cash_target_weight=policy.min_cash_weight,
        order_intents=intents,
    )


def _sell_order(
    policy: UserPolicy,
    *,
    notional: float = 500_000,
    purpose: str = "protective_exit",
) -> OrderPlan:
    intent = OrderIntent(
        symbol="CCC",
        side="sell",
        order_type=OrderType.limit,
        quantity=notional / 100,
        limit_price=100,
        notional=notional,
        target_weight=max(0.0, 0.10 - notional / 10_000_000),
        reason="verified protective batch test",
    )
    key = f"protective-CCC-{notional}"
    explanation = None
    if purpose != "rebalance":
        explanation = ProposalExplanation(
            symbol="CCC",
            action="sell",
            quantity=intent.quantity,
            target_weight_delta=intent.target_weight - 0.10,
            reference_price=100,
            estimated_cash_impact=-notional,
            strategy_id="pullback_trend_v1",
            strategy_version="1.0",
            signal_reason="protective batch test",
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
) -> tuple[dict[str, ManagedPositionBinding], dict[str, Quote]]:
    position = next(position for position in snapshot.positions if position.symbol == "CCC")
    state = ManagedPositionState(
        policy_id=policy.policy_id,
        strategy_id="pullback_trend_v1",
        strategy_version="1.0",
        symbol="CCC",
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
    return (
        {order.order_plan_id: ManagedPositionBinding.from_position(state)},
        {
            order.order_plan_id: Quote(
                symbol="CCC",
                last=100,
                bid=100,
                as_of=order.intent.quote_time,
            )
        },
    )


def _cash_buffer_snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash=2_500_000,
        equity=10_000_000,
        positions=[
            PortfolioPosition(symbol="CCC", quantity=15_000, market_price=100, sector="tech"),
            PortfolioPosition(symbol="DDD", quantity=15_000, market_price=100, sector="tech"),
            PortfolioPosition(symbol="EEE", quantity=15_000, market_price=100, sector="industrial"),
            PortfolioPosition(symbol="FFF", quantity=15_000, market_price=100, sector="industrial"),
            PortfolioPosition(symbol="GGG", quantity=15_000, market_price=100, sector="healthcare"),
        ],
    )


def test_cash_buffer_breach_rejects_after_batch() -> None:
    policy = UserPolicy()
    intents = [_intent("AAA", 300_000), _intent("BBB", 300_000)]

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, intents),
        snapshot=_cash_buffer_snapshot(),
        quotes={"AAA": 100, "BBB": 100},
    )

    assert not decision.passed
    assert decision.mode == "rejected"
    assert "min_cash_after_batch" in decision.failed_checks
    assert decision.portfolio_after_batch.cash == 1_900_000


def test_sector_cap_breach_rejects_after_batch() -> None:
    policy = UserPolicy(max_position_weight=0.30, max_sector_weight=0.40)
    intents = [_intent("CCC", 1_100_000)]

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, intents),
        snapshot=fixture_portfolio_snapshot(),
        quotes={"CCC": 100},
    )

    assert not decision.passed
    assert "max_sector_weight_after_batch" in decision.failed_checks
    assert decision.portfolio_after_batch.sector_weights["tech"] == 0.41


def test_concentration_breach_rejects_after_batch() -> None:
    policy = UserPolicy(max_position_weight=0.15, max_sector_weight=0.50)
    intents = [_intent("CCC", 600_000)]

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, intents),
        snapshot=fixture_portfolio_snapshot(),
        quotes={"CCC": 100},
    )

    assert not decision.passed
    assert "max_concentration_weight_after_batch" in decision.failed_checks
    assert decision.portfolio_after_batch.position_weights["CCC"] == 0.16


def test_stale_snapshot_rejects_batch() -> None:
    policy = UserPolicy()
    intents = [_intent("AAA", 100_000)]
    snapshot = fixture_portfolio_snapshot().model_copy(
        update={"captured_at": utc_now() - timedelta(minutes=30)}
    )

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, intents),
        snapshot=snapshot,
        quotes={"AAA": 100},
        config=BatchRiskConfig(snapshot_max_age_seconds=60),
    )

    assert not decision.passed
    assert "snapshot_not_stale" in decision.failed_checks
    assert "snapshot_stale" in decision.stale_input_reasons


def test_stale_quote_rejects_batch() -> None:
    policy = UserPolicy()
    intents = [_intent("AAA", 100_000, quote_age_seconds=120)]

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, intents),
        snapshot=fixture_portfolio_snapshot(),
        quotes={"AAA": 100},
        config=BatchRiskConfig(quote_max_age_seconds=30),
    )

    assert not decision.passed
    assert "quotes_not_stale" in decision.failed_checks
    assert "quote_stale:AAA" in decision.stale_input_reasons


def test_monthly_loss_stop_rejects_batch() -> None:
    policy = UserPolicy()
    intents = [_intent("AAA", 100_000)]

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, intents),
        snapshot=fixture_portfolio_snapshot(monthly_loss_ratio=-0.11),
        quotes={"AAA": 100},
    )

    assert not decision.passed
    assert "monthly_loss_stop_all_autotrading" in decision.failed_checks


def test_monthly_loss_stop_allows_verified_protective_batch_only() -> None:
    policy = UserPolicy()
    snapshot = fixture_portfolio_snapshot(monthly_loss_ratio=-0.11)
    protective = _sell_order(policy)
    ordinary = _sell_order(policy, purpose="rebalance")
    bindings, market_quotes = _risk_evidence(policy, snapshot, protective)

    protective_decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, [protective.intent]),
        snapshot=snapshot,
        quotes={"CCC": 100},
        order_plans=[protective],
        position_bindings=bindings,
        market_quotes=market_quotes,
    )
    ordinary_decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, [ordinary.intent]),
        snapshot=snapshot,
        quotes={"CCC": 100},
        order_plans=[ordinary],
    )

    assert protective_decision.passed
    assert protective_decision.accepted_order_plan_ids == [protective.order_plan_id]
    assert not ordinary_decision.passed
    assert "monthly_loss_stop_all_autotrading" in ordinary_decision.failed_checks


def test_forged_protective_batch_cannot_oversell() -> None:
    policy = UserPolicy(single_order_cash_limit=2_000_000, max_daily_turnover=3_000_000)
    oversell = _sell_order(policy, notional=1_100_000)
    snapshot = fixture_portfolio_snapshot(monthly_loss_ratio=-0.11)
    bindings, market_quotes = _risk_evidence(policy, snapshot, oversell)

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, [oversell.intent]),
        snapshot=snapshot,
        quotes={"CCC": 100},
        order_plans=[oversell],
        position_bindings=bindings,
        market_quotes=market_quotes,
    )

    assert not decision.passed
    assert "risk_reducing_purpose_verified" in decision.failed_checks
    assert "no_short_sell_after_batch" in decision.failed_checks
