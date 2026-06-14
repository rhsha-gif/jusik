from __future__ import annotations

from datetime import timedelta

from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.types import BatchRiskConfig
from quantpilot.packages.core.schemas import (
    OrderIntent,
    OrderType,
    PortfolioPlan,
    PortfolioPosition,
    PortfolioSnapshot,
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


def test_failed_check_order_is_stable_for_multiple_rejections() -> None:
    policy = UserPolicy(kill_switch_engaged=True, max_daily_turnover=50_000)
    intents = [_intent("AAA", 100_000)]

    decision = run_batch_risk_gate(
        policy=policy,
        portfolio_plan=_plan(policy, intents),
        snapshot=fixture_portfolio_snapshot(),
        quotes={"AAA": 100},
    )

    assert decision.failed_checks == [
        "kill_switch_not_engaged",
        "max_daily_turnover_after_batch",
    ]
