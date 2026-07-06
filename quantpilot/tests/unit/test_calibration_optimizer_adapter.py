from __future__ import annotations

from datetime import datetime, timedelta

from quantpilot.packages.core.portfolio.calibration_adapter import (
    CalibrationAdapterConfig,
    build_calibrated_proxies,
)
from quantpilot.packages.core.portfolio.planner import (
    build_optimization_input,
    build_portfolio_plan,
    fixture_portfolio_snapshot,
)
from quantpilot.packages.core.schemas import Signal, SignalAction, UserPolicy, utc_now
from quantpilot.packages.core.signals.types import (
    CalibratedSignal,
    CalibratedSignalSet,
    CalibrationGuardResult,
    EnsembleVote,
    ExpectedReturnRiskProxy,
    MultiFactorScore,
)


def _signal(symbol: str, *, action: SignalAction = SignalAction.buy_ready, strength: float = 0.8) -> Signal:
    return Signal(
        strategy_id="test_strategy",
        recipe_version="1",
        symbol=symbol,
        action=action,
        strength=strength,
        reason="calibration adapter unit test",
        source="fixture",
    )


def _calibrated(
    symbol: str,
    *,
    confidence: float = 0.8,
    guard_passed: bool = True,
    guard_status: str = "available",
    expected_return: float = 0.06,
    risk: float = 0.2,
    generated_at: datetime | None = None,
    action: SignalAction = SignalAction.buy_ready,
) -> CalibratedSignal:
    return CalibratedSignal(
        signal_id=f"sig_{symbol}",
        symbol=symbol,
        base_action=action,
        calibrated_action=action,
        strength=0.8,
        confidence=confidence,
        decay=0.9,
        multi_factor_score=MultiFactorScore(
            symbol=symbol,
            momentum=70,
            trend=70,
            volume=60,
            volatility=50,
            data_quality=80,
            final_score=68,
            regime="uptrend",
            weights={"momentum": 0.3, "trend": 0.3, "volume": 0.2, "volatility": 0.1, "data_quality": 0.1},
        ),
        expected_return_risk=ExpectedReturnRiskProxy(
            symbol=symbol,
            horizon="20d",
            expected_return=expected_return,
            risk=risk,
            risk_adjusted_return=round(expected_return / max(risk, 0.01), 6),
            confidence=confidence,
        ),
        ensemble_vote=EnsembleVote(symbol=symbol, votes={"buy_ready": 0.8}, selected_action=action),
        guard=CalibrationGuardResult(passed=guard_passed, status=guard_status, action_allowed=guard_passed),
        generated_at=generated_at or utc_now(),
    )


def _calibrated_set(
    signals: list[CalibratedSignal],
    *,
    provider_state: str = "available",
    usable: bool = True,
    order_submission_enabled: bool = False,
) -> CalibratedSignalSet:
    return CalibratedSignalSet(
        signals=signals,
        provider_status={"fixture_quote": {"state": provider_state}},
        data_quality={"usable": usable},
        order_submission_enabled=order_submission_enabled,
    )


def test_adapter_applies_calibrated_proxies_with_confidence_shrinkage() -> None:
    signals = [_signal("AAA"), _signal("BBB")]
    calibrated_set = _calibrated_set([_calibrated("AAA", confidence=0.5), _calibrated("BBB", confidence=1.0)])

    result = build_calibrated_proxies(calibrated_set, signals=signals)

    assert result.status == "applied"
    assert set(result.proxies) == {"AAA", "BBB"}
    assert result.proxies["AAA"].calibrated is True
    assert result.proxies["AAA"].expected_return == round(0.06 * 0.5, 6)
    assert result.proxies["BBB"].expected_return == 0.06
    assert result.proxies["AAA"].metadata["confidence_shrinkage_applied"] is True


def test_adapter_fails_closed_when_provider_is_unavailable() -> None:
    signals = [_signal("AAA")]
    calibrated_set = _calibrated_set([_calibrated("AAA")], provider_state="unavailable")

    result = build_calibrated_proxies(calibrated_set, signals=signals)

    assert result.status == "fail_closed"
    assert result.proxies == {}
    assert "provider_fixture_quote_unavailable" in result.reason_codes


def test_adapter_fails_closed_when_data_quality_is_not_usable() -> None:
    result = build_calibrated_proxies(
        _calibrated_set([_calibrated("AAA")], usable=False),
        signals=[_signal("AAA")],
    )

    assert result.status == "fail_closed"
    assert result.proxies == {}
    assert "data_quality_not_usable" in result.reason_codes


def test_adapter_fails_closed_when_order_submission_flag_is_set() -> None:
    result = build_calibrated_proxies(
        _calibrated_set([_calibrated("AAA")], order_submission_enabled=True),
        signals=[_signal("AAA")],
    )

    assert result.status == "fail_closed"
    assert "order_submission_flag_unexpected" in result.reason_codes


def test_adapter_excludes_low_confidence_and_stale_and_guarded_signals() -> None:
    now = utc_now()
    signals = [_signal("AAA"), _signal("BBB"), _signal("CCC"), _signal("DDD")]
    calibrated_set = _calibrated_set(
        [
            _calibrated("AAA"),
            _calibrated("BBB", confidence=0.10),
            _calibrated("CCC", generated_at=now - timedelta(seconds=1_800)),
            _calibrated("DDD", guard_passed=False, guard_status="blocked"),
        ]
    )

    result = build_calibrated_proxies(calibrated_set, signals=signals, now=now)

    assert result.status == "partial"
    assert set(result.proxies) == {"AAA"}
    assert result.excluded_symbols["BBB"] == ["low_confidence"]
    assert result.excluded_symbols["CCC"] == ["stale_calibration"]
    assert result.excluded_symbols["DDD"] == ["calibration_guard_blocked"]


def test_adapter_fails_closed_when_every_signal_is_excluded() -> None:
    result = build_calibrated_proxies(
        _calibrated_set([_calibrated("AAA", confidence=0.05)]),
        signals=[_signal("AAA")],
        config=CalibrationAdapterConfig(min_confidence=0.5),
    )

    assert result.status == "fail_closed"
    assert result.proxies == {}
    assert "no_calibrated_proxies_accepted" in result.reason_codes


def test_adapter_marks_missing_calibrations_for_requested_symbols() -> None:
    result = build_calibrated_proxies(
        _calibrated_set([_calibrated("AAA")]),
        signals=[_signal("AAA"), _signal("BBB")],
    )

    assert result.status == "partial"
    assert result.excluded_symbols["BBB"] == ["calibrated_signal_missing"]


def test_optimization_input_uses_calibrated_proxies_and_falls_back_for_excluded() -> None:
    signals = [_signal("AAA"), _signal("BBB", strength=0.6)]
    calibrated_set = _calibrated_set([_calibrated("AAA"), _calibrated("BBB", confidence=0.10)])

    optimization_input = build_optimization_input(
        policy=UserPolicy(),
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
        calibrated_signal_set=calibrated_set,
    )

    assert optimization_input.proxies["AAA"].calibrated is True
    assert optimization_input.proxies["BBB"].calibrated is False
    assert optimization_input.proxies["BBB"].expected_return_source == "uncalibrated_signal_strength"
    assert optimization_input.proxy_metadata["calibration_status"] == "partial"
    assert optimization_input.proxy_metadata["calibrated"] is True
    assert "BBB" in optimization_input.proxy_metadata["calibration_excluded_symbols"]


def test_optimization_input_falls_back_entirely_when_adapter_fails_closed() -> None:
    signals = [_signal("AAA")]
    calibrated_set = _calibrated_set([_calibrated("AAA")], provider_state="stale")

    optimization_input = build_optimization_input(
        policy=UserPolicy(),
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
        calibrated_signal_set=calibrated_set,
    )

    assert optimization_input.proxies["AAA"].calibrated is False
    assert optimization_input.proxy_metadata["calibrated"] is False
    assert optimization_input.proxy_metadata["calibration_status"] == "fail_closed"


def test_explicit_proxies_take_precedence_over_calibrated_set() -> None:
    signals = [_signal("AAA")]
    from quantpilot.packages.core.portfolio.optimizer_types import (
        ExpectedReturnRiskProxy as OptimizerProxy,
    )

    explicit = {"AAA": OptimizerProxy(symbol="AAA", expected_return=0.9, volatility=0.1)}
    optimization_input = build_optimization_input(
        policy=UserPolicy(),
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
        expected_return_risk_proxies=explicit,
        calibrated_signal_set=_calibrated_set([_calibrated("AAA")]),
    )

    assert optimization_input.proxies["AAA"].expected_return == 0.9
    assert optimization_input.proxy_metadata["source"] == "planner_adapter_uncalibrated_signal_proxy"


def test_portfolio_plan_builds_from_calibrated_signal_set_without_order_submission() -> None:
    signals = [_signal("AAA"), _signal("CCC", action=SignalAction.hold)]
    calibrated_set = _calibrated_set([_calibrated("AAA"), _calibrated("CCC", action=SignalAction.hold)])

    plan = build_portfolio_plan(
        policy=UserPolicy(),
        signals=signals,
        snapshot=fixture_portfolio_snapshot(),
        quotes={"AAA": 100.0, "CCC": 100.0},
        calibrated_signal_set=calibrated_set,
    )

    assert plan.policy_id
    assert all(intent.order_type.value != "market" for intent in plan.order_intents)
