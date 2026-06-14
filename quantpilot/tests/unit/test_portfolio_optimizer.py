from __future__ import annotations

from quantpilot.packages.core.policy.parser import parse_policy_text
from quantpilot.packages.core.portfolio.optimizer import DeterministicPortfolioOptimizer
from quantpilot.packages.core.portfolio.optimizer_types import (
    ExpectedReturnRiskProxy,
    OptimizationConstraints,
    OptimizationInput,
)
from quantpilot.packages.core.portfolio.planner import build_portfolio_plan, fixture_portfolio_snapshot
from quantpilot.packages.core.schemas import PortfolioPosition, PortfolioSnapshot, Signal, SignalAction, UserPolicy
from quantpilot.packages.core.signals.service import generate_signals, load_fixture_ohlcv
from quantpilot.packages.core.strategies.loader import load_default_strategy


def _signal(
    symbol: str,
    *,
    action: SignalAction = SignalAction.buy_ready,
    strength: float = 1.0,
) -> Signal:
    return Signal(
        strategy_id="test_strategy",
        recipe_version="1",
        symbol=symbol,
        action=action,
        strength=strength,
        reason="optimizer unit test",
        source="fixture",
    )


def _proxy(symbol: str, *, expected_return: float, volatility: float) -> ExpectedReturnRiskProxy:
    return ExpectedReturnRiskProxy(
        symbol=symbol,
        expected_return=expected_return,
        volatility=volatility,
        calibrated=False,
        metadata={"fixture": True},
    )


def _constraints(
    *,
    max_position_weight: float = 0.20,
    max_sector_weight: float = 0.60,
    min_cash_weight: float = 0.20,
    max_turnover_weight: float = 1.0,
    rebalance_band: float = 0.0,
) -> OptimizationConstraints:
    return OptimizationConstraints(
        max_position_weight=max_position_weight,
        max_sector_weight=max_sector_weight,
        min_cash_weight=min_cash_weight,
        max_turnover_weight=max_turnover_weight,
        rebalance_band=rebalance_band,
    )


def _snapshot(
    *,
    cash: float = 1_000_000,
    equity: float = 1_000_000,
    positions: list[PortfolioPosition] | None = None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(cash=cash, equity=equity, positions=positions or [])


def _optimize(
    signals: list[Signal],
    proxies: dict[str, ExpectedReturnRiskProxy],
    *,
    snapshot: PortfolioSnapshot | None = None,
    constraints: OptimizationConstraints | None = None,
    sectors: dict[str, str] | None = None,
):
    return DeterministicPortfolioOptimizer().optimize(
        OptimizationInput(
            signals=signals,
            proxies=proxies,
            sector_metadata=sectors or {},
            snapshot=snapshot or _snapshot(),
            constraints=constraints or _constraints(),
        )
    )


def _target_map(result) -> dict[str, float]:
    return {target.symbol: target.target_weight for target in result.target_weights}


def test_expected_return_proxy_increases_candidate_weight() -> None:
    signals = [_signal("AAA"), _signal("BBB")]
    result = _optimize(
        signals,
        {
            "AAA": _proxy("AAA", expected_return=0.90, volatility=0.10),
            "BBB": _proxy("BBB", expected_return=0.30, volatility=0.10),
        },
        sectors={"AAA": "tech", "BBB": "industrial"},
    )

    targets = _target_map(result)
    assert result.status == "optimized"
    assert targets["AAA"] > targets["BBB"]


def test_volatility_proxy_penalizes_weight() -> None:
    signals = [_signal("AAA"), _signal("BBB")]
    result = _optimize(
        signals,
        {
            "AAA": _proxy("AAA", expected_return=0.80, volatility=0.05),
            "BBB": _proxy("BBB", expected_return=0.80, volatility=1.00),
        },
        sectors={"AAA": "tech", "BBB": "industrial"},
    )

    targets = _target_map(result)
    assert targets["AAA"] > targets["BBB"]


def test_sector_cap_limits_combined_sector_weight() -> None:
    signals = [_signal("AAA"), _signal("BBB"), _signal("CCC")]
    result = _optimize(
        signals,
        {
            "AAA": _proxy("AAA", expected_return=1.00, volatility=0.01),
            "BBB": _proxy("BBB", expected_return=1.00, volatility=0.01),
            "CCC": _proxy("CCC", expected_return=1.00, volatility=0.01),
        },
        constraints=_constraints(max_sector_weight=0.25),
        sectors={"AAA": "tech", "BBB": "tech", "CCC": "tech"},
    )

    tech_weight = sum(target.target_weight for target in result.target_weights if target.sector == "tech")
    assert result.status == "optimized"
    assert tech_weight <= 0.25
    assert "max_sector_weight" in result.constraints_applied


def test_cash_buffer_limits_total_invested_weight() -> None:
    signals = [_signal("AAA"), _signal("BBB"), _signal("CCC"), _signal("DDD")]
    result = _optimize(
        signals,
        {symbol: _proxy(symbol, expected_return=1.00, volatility=0.01) for symbol in ["AAA", "BBB", "CCC", "DDD"]},
        constraints=_constraints(max_position_weight=0.30, max_sector_weight=0.80, min_cash_weight=0.50),
        sectors={"AAA": "tech", "BBB": "industrial", "CCC": "healthcare", "DDD": "consumer"},
    )

    assert result.status == "optimized"
    assert result.cash_target_weight >= 0.50
    assert sum(target.target_weight for target in result.target_weights) <= 0.50
    assert "min_cash_weight" in result.constraints_applied


def test_max_position_weight_caps_single_candidate() -> None:
    result = _optimize(
        [_signal("AAA")],
        {"AAA": _proxy("AAA", expected_return=2.00, volatility=0.01)},
        constraints=_constraints(max_position_weight=0.10, max_sector_weight=0.40),
        sectors={"AAA": "tech"},
    )

    assert _target_map(result)["AAA"] <= 0.10
    assert "max_position_weight" in result.target_weights[0].constrained_by


def test_turnover_constraint_scales_target_changes() -> None:
    snapshot = _snapshot(
        cash=800_000,
        positions=[PortfolioPosition(symbol="AAA", quantity=2_000, market_price=100, sector="tech")],
    )
    result = _optimize(
        [_signal("AAA", action=SignalAction.exit), _signal("BBB")],
        {
            "AAA": _proxy("AAA", expected_return=-1.00, volatility=0.10),
            "BBB": _proxy("BBB", expected_return=1.00, volatility=0.10),
        },
        snapshot=snapshot,
        constraints=_constraints(max_turnover_weight=0.05),
        sectors={"AAA": "tech", "BBB": "industrial"},
    )

    assert result.status == "optimized"
    assert result.turnover_weight <= 0.05
    assert "max_turnover_weight" in result.constraints_applied


def test_rebalance_band_suppresses_small_target_change() -> None:
    snapshot = _snapshot(
        cash=900_000,
        positions=[PortfolioPosition(symbol="AAA", quantity=1_000, market_price=100, sector="tech")],
    )
    result = _optimize(
        [_signal("AAA")],
        {"AAA": _proxy("AAA", expected_return=0.55, volatility=0.0)},
        snapshot=snapshot,
        constraints=_constraints(rebalance_band=0.02),
        sectors={"AAA": "tech"},
    )

    assert result.status == "no_trade"
    assert _target_map(result)["AAA"] == 0.10
    assert "rebalance_band" in result.constraints_applied


def test_infeasible_constraints_fail_closed_with_no_trade_targets() -> None:
    snapshot = _snapshot(
        cash=50_000,
        positions=[PortfolioPosition(symbol="ZZZ", quantity=9_500, market_price=100, sector="tech")],
    )
    result = _optimize(
        [_signal("AAA")],
        {"AAA": _proxy("AAA", expected_return=1.00, volatility=0.10)},
        snapshot=snapshot,
        constraints=_constraints(max_sector_weight=1.0, min_cash_weight=0.50),
        sectors={"AAA": "industrial", "ZZZ": "tech"},
    )

    assert result.status == "fail_closed"
    assert result.turnover_weight == 0.0
    assert result.order_submission_enabled is False
    assert _target_map(result)["AAA"] == 0.0
    assert "constraints_infeasible" in result.reason_codes


def test_legacy_planner_adapter_still_returns_serializable_portfolio_plan() -> None:
    policy = parse_policy_text("fixture")
    signals = generate_signals(load_default_strategy(), load_fixture_ohlcv())
    quotes = {bar["symbol"]: float(bar["close"]) for bar in load_fixture_ohlcv()}

    plan = build_portfolio_plan(
        policy=policy,
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
        quotes=quotes,
    )

    dumped = plan.model_dump(mode="json")
    assert dumped["policy_id"] == policy.policy_id
    assert plan.cash_target_weight >= policy.min_cash_weight
    assert all(intent.order_type.value == "limit" for intent in plan.order_intents)
    assert all(intent.notional <= policy.single_order_cash_limit for intent in plan.order_intents)
    assert plan.target_weights["GGG"] == 0.0


def test_planner_accepts_explicit_expected_return_risk_proxies() -> None:
    policy = UserPolicy(max_position_weight=0.20, max_sector_weight=0.60, min_cash_weight=0.20)
    signals = [_signal("AAA"), _signal("BBB")]
    plan = build_portfolio_plan(
        policy=policy,
        signals=signals,
        snapshot=_snapshot(),
        expected_return_risk_proxies={
            "AAA": _proxy("AAA", expected_return=0.90, volatility=0.10),
            "BBB": _proxy("BBB", expected_return=0.30, volatility=0.10),
        },
        sector_metadata={"AAA": "tech", "BBB": "industrial"},
    )

    assert plan.target_weights["AAA"] > plan.target_weights["BBB"]
