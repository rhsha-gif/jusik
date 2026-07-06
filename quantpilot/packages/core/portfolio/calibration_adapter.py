from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from quantpilot.packages.core.portfolio.optimizer_types import ExpectedReturnRiskProxy
from quantpilot.packages.core.schemas import HarnessModel, Signal, utc_now
from quantpilot.packages.core.signals.types import CalibratedSignal, CalibratedSignalSet


CalibrationAdapterStatus = Literal["applied", "partial", "fail_closed"]


class CalibrationAdapterConfig(HarnessModel):
    min_confidence: float = Field(default=0.35, ge=0, le=1)
    max_age_seconds: int = Field(default=900, ge=0)
    require_provider_available: bool = True


class CalibratedProxyAdapterResult(HarnessModel):
    status: CalibrationAdapterStatus
    proxies: dict[str, ExpectedReturnRiskProxy] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    excluded_symbols: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _symbol(value: str) -> str:
    return value.strip().upper()


def _set_level_failures(
    calibrated_set: CalibratedSignalSet,
    config: CalibrationAdapterConfig,
) -> list[str]:
    reasons: list[str] = []
    if calibrated_set.order_submission_enabled:
        reasons.append("order_submission_flag_unexpected")
    if config.require_provider_available:
        for name, status in sorted(calibrated_set.provider_status.items()):
            state = str(status.get("state", "unknown"))
            if state != "available":
                reasons.append(f"provider_{name}_{state}")
    if not bool(calibrated_set.data_quality.get("usable", False)):
        reasons.append("data_quality_not_usable")
    return reasons


def _signal_level_failures(
    calibrated: CalibratedSignal,
    config: CalibrationAdapterConfig,
    now: datetime,
) -> list[str]:
    reasons: list[str] = []
    if not calibrated.guard.passed or calibrated.guard.status != "available":
        reasons.append(f"calibration_guard_{calibrated.guard.status}")
    if calibrated.confidence < config.min_confidence:
        reasons.append("low_confidence")
    age_seconds = (now - calibrated.generated_at).total_seconds()
    if age_seconds > config.max_age_seconds:
        reasons.append("stale_calibration")
    return reasons


def _proxy_from_calibrated(calibrated: CalibratedSignal) -> ExpectedReturnRiskProxy:
    proxy = calibrated.expected_return_risk
    # Confidence-weighted shrinkage keeps low-certainty estimates conservative.
    shrunk_expected_return = round(proxy.expected_return * calibrated.confidence, 6)
    return ExpectedReturnRiskProxy(
        symbol=calibrated.symbol,
        expected_return=shrunk_expected_return,
        volatility=proxy.risk,
        expected_return_source="calibrated_multifactor_signal_model",
        volatility_source="calibrated_multifactor_signal_model",
        calibrated=True,
        data_mode=proxy.data_mode,
        metadata={
            "signal_id": calibrated.signal_id,
            "horizon": proxy.horizon,
            "confidence": calibrated.confidence,
            "raw_expected_return": proxy.expected_return,
            "confidence_shrinkage_applied": True,
            "regime": calibrated.multi_factor_score.regime,
            "guard_status": calibrated.guard.status,
        },
    )


def build_calibrated_proxies(
    calibrated_set: CalibratedSignalSet | None,
    *,
    signals: list[Signal],
    config: CalibrationAdapterConfig | None = None,
    now: datetime | None = None,
) -> CalibratedProxyAdapterResult:
    """Translate a calibrated signal set into optimizer proxies, failing closed.

    Provider failures, unusable data quality, guarded/blocked calibrations,
    stale calibrations, and low-confidence signals never produce calibrated
    proxies; callers fall back to the conservative uncalibrated planner proxy
    for any symbol this adapter excludes.
    """
    adapter_config = config or CalibrationAdapterConfig()
    decision_time = now or utc_now()
    requested = {_symbol(signal.symbol) for signal in signals}

    if calibrated_set is None:
        return CalibratedProxyAdapterResult(
            status="fail_closed",
            reason_codes=["calibrated_signal_set_missing"],
            metadata={"calibrated": False},
        )

    set_failures = _set_level_failures(calibrated_set, adapter_config)
    if set_failures:
        return CalibratedProxyAdapterResult(
            status="fail_closed",
            reason_codes=set_failures,
            metadata={"calibrated": False},
        )

    proxies: dict[str, ExpectedReturnRiskProxy] = {}
    excluded: dict[str, list[str]] = {}
    for calibrated in calibrated_set.signals:
        symbol = _symbol(calibrated.symbol)
        if symbol not in requested:
            continue
        failures = _signal_level_failures(calibrated, adapter_config, decision_time)
        if failures:
            excluded[symbol] = failures
            continue
        proxies[symbol] = _proxy_from_calibrated(calibrated)

    missing = sorted(requested - set(proxies) - set(excluded))
    for symbol in missing:
        excluded[symbol] = ["calibrated_signal_missing"]

    if not proxies:
        status: CalibrationAdapterStatus = "fail_closed"
        reason_codes = ["no_calibrated_proxies_accepted"]
    elif excluded:
        status = "partial"
        reason_codes = ["calibrated_proxies_partially_applied"]
    else:
        status = "applied"
        reason_codes = ["calibrated_proxies_applied"]

    return CalibratedProxyAdapterResult(
        status=status,
        proxies=proxies,
        reason_codes=reason_codes,
        excluded_symbols=excluded,
        metadata={
            "calibrated": bool(proxies),
            "accepted_symbols": sorted(proxies),
            "excluded_symbols": {symbol: reasons for symbol, reasons in sorted(excluded.items())},
            "min_confidence": adapter_config.min_confidence,
            "max_age_seconds": adapter_config.max_age_seconds,
        },
    )
