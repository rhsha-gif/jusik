from __future__ import annotations

from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.types import BatchRiskConfig
from quantpilot.packages.core.schemas import OrderIntent, OrderType, PortfolioPlan, UserPolicy


def _intent(symbol: str, notional: float) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        side="buy",
        order_type=OrderType.limit,
        quantity=notional / 100,
        limit_price=100,
        notional=notional,
        target_weight=round(notional / 10_000_000, 6),
        reason="partial batch test",
    )


def _plan(policy: UserPolicy, intents: list[OrderIntent]) -> PortfolioPlan:
    return PortfolioPlan(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        target_weights={intent.symbol: intent.target_weight for intent in intents},
        cash_target_weight=policy.min_cash_weight,
        order_intents=intents,
    )


def test_partial_allow_false_rejects_full_batch() -> None:
    policy = UserPolicy(max_daily_turnover=500_000)
    intents = [_intent("AAA", 300_000), _intent("BBB", 300_000)]

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, intents),
        snapshot=fixture_portfolio_snapshot(),
        quotes={"AAA": 100, "BBB": 100},
    )

    assert not decision.passed
    assert decision.mode == "rejected"
    assert decision.accepted_intent_ids == []
    assert set(decision.rejected_intent_ids) == {intent.intent_id for intent in intents}
    assert "max_daily_turnover_after_batch" in decision.failed_checks


def test_partial_allow_true_returns_safe_subset() -> None:
    policy = UserPolicy(max_daily_turnover=500_000)
    first = _intent("AAA", 300_000)
    second = _intent("BBB", 300_000)

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, [first, second]),
        snapshot=fixture_portfolio_snapshot(),
        quotes={"AAA": 100, "BBB": 100},
        config=BatchRiskConfig(partial_allow=True),
    )

    assert decision.passed
    assert decision.mode == "partial_batch"
    assert decision.accepted_intent_ids == [first.intent_id]
    assert decision.rejected_intent_ids == [second.intent_id]
    assert "max_daily_turnover_after_batch" in decision.rejected_reasons[second.intent_id]
    assert decision.portfolio_after_batch.cash == 5_700_000
