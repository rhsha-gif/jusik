from __future__ import annotations

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.schemas import Signal, SignalAction
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
        reason="harness calibrated planning test",
        source="fixture",
    )


def _calibrated(symbol: str, *, expected_return: float = 0.08, risk: float = 0.2) -> CalibratedSignal:
    return CalibratedSignal(
        signal_id=f"sig_{symbol}",
        symbol=symbol,
        base_action=SignalAction.buy_ready,
        calibrated_action=SignalAction.buy_ready,
        strength=0.8,
        confidence=0.9,
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
            confidence=0.9,
        ),
        ensemble_vote=EnsembleVote(symbol=symbol, votes={"buy_ready": 0.9}, selected_action=SignalAction.buy_ready),
        guard=CalibrationGuardResult(passed=True, status="available", action_allowed=True),
    )


def _calibrated_set(symbols: list[str]) -> CalibratedSignalSet:
    return CalibratedSignalSet(
        signals=[_calibrated(symbol) for symbol in symbols],
        provider_status={"fixture_quote": {"state": "available"}},
        data_quality={"usable": True},
    )


def _service_with_policy() -> tuple[HarnessService, str, list[Signal]]:
    service = HarnessService()
    policy = service.parse_policy()
    signals = service.run_signals()
    return service, policy.policy_id, signals


def test_calibrated_planning_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("CALIBRATED_PLANNING_ENABLED", raising=False)
    service, policy_id, signals = _service_with_policy()
    calibrated_set = _calibrated_set([signal.symbol for signal in signals])

    plan = service.create_portfolio_plan(
        policy_id=policy_id,
        signals=signals,
        calibrated_signal_set=calibrated_set,
    )

    # Flag off: the calibrated set is ignored and the uncalibrated proxy path runs.
    assert plan.proxy_metadata is not None
    assert plan.proxy_metadata["calibrated"] is False
    assert plan.proxy_metadata["source"] == "planner_adapter_uncalibrated_signal_proxy"


def test_calibrated_planning_used_when_flag_enabled(monkeypatch) -> None:
    monkeypatch.setenv("CALIBRATED_PLANNING_ENABLED", "true")
    service, policy_id, signals = _service_with_policy()
    calibrated_set = _calibrated_set([signal.symbol for signal in signals])

    plan = service.create_portfolio_plan(
        policy_id=policy_id,
        signals=signals,
        calibrated_signal_set=calibrated_set,
    )

    assert plan.proxy_metadata is not None
    assert plan.proxy_metadata["source"] == "planner_calibration_adapter"
    assert plan.proxy_metadata["calibration_status"] in {"applied", "partial"}
    # Planning never enables order submission regardless of the calibration flag.
    assert all(intent.order_type.value != "market" for intent in plan.order_intents)


def test_calibrated_planning_falls_back_when_flag_enabled_but_no_set(monkeypatch) -> None:
    monkeypatch.setenv("CALIBRATED_PLANNING_ENABLED", "true")
    service, policy_id, signals = _service_with_policy()

    plan = service.create_portfolio_plan(policy_id=policy_id, signals=signals)

    assert plan.proxy_metadata is not None
    assert plan.proxy_metadata["calibrated"] is False
    assert plan.proxy_metadata["source"] == "planner_adapter_uncalibrated_signal_proxy"


def test_calibrated_planning_flag_fail_closed_provider_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("CALIBRATED_PLANNING_ENABLED", "true")
    service, policy_id, signals = _service_with_policy()
    calibrated_set = CalibratedSignalSet(
        signals=[_calibrated(signal.symbol) for signal in signals],
        provider_status={"fixture_quote": {"state": "unavailable"}},
        data_quality={"usable": True},
    )

    plan = service.create_portfolio_plan(
        policy_id=policy_id,
        signals=signals,
        calibrated_signal_set=calibrated_set,
    )

    # Adapter fails closed at the set level; planning uses uncalibrated proxies.
    assert plan.proxy_metadata is not None
    assert plan.proxy_metadata["calibration_status"] == "fail_closed"
    assert plan.proxy_metadata["calibrated"] is False
