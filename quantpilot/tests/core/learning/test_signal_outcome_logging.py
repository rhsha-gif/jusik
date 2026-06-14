from __future__ import annotations

from datetime import datetime, timezone

from quantpilot.packages.core.learning.offline import SignalOutcomeLogger
from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType
from quantpilot.packages.core.schemas import DataMode, SignalAction
from quantpilot.packages.core.signals.types import (
    CalibratedSignal,
    CalibratedSignalSet,
    CalibrationGuardResult,
    EnsembleVote,
    ExpectedReturnRiskProxy,
    MultiFactorScore,
)


def _calibrated_signal(symbol: str, *, action: SignalAction, signal_id: str | None = None) -> CalibratedSignal:
    score = MultiFactorScore(
        symbol=symbol,
        momentum=70,
        trend=72,
        volume=65,
        volatility=60,
        data_quality=95,
        final_score=71,
        regime="uptrend",
        weights={"momentum": 0.4, "trend": 0.4, "volume": 0.2},
        reason_codes=["fixture_score"],
    )
    return CalibratedSignal(
        signal_id=signal_id or f"sig_{symbol.lower()}",
        symbol=symbol,
        base_action=action,
        calibrated_action=action,
        strength=0.8,
        confidence=0.72,
        decay=1.0,
        multi_factor_score=score,
        expected_return_risk=ExpectedReturnRiskProxy(
            symbol=symbol,
            horizon="daily",
            expected_return=0.035,
            risk=0.2,
            risk_adjusted_return=0.175,
            confidence=0.72,
            data_mode=DataMode.fixture,
        ),
        ensemble_vote=EnsembleVote(
            symbol=symbol,
            votes={action.value: 1.0},
            selected_action=action,
            reason_codes=["unanimous_fixture_vote"],
        ),
        guard=CalibrationGuardResult(
            passed=True,
            status="available",
            action_allowed=True,
        ),
        target_weight_hint=0.05,
        reason_codes=["fixture_signal"],
        generated_at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
    )


def _entry(
    symbol: str,
    event_type: LedgerEventType,
    *,
    notional: float = 1_000,
    reason: str | None = None,
) -> LedgerEntry:
    order_plan_id = f"oplan_{symbol.lower()}"
    return LedgerEntry(
        event_type=event_type,
        policy_id="pol_fixture",
        policy_version=1,
        order_plan_id=order_plan_id,
        intent_id=f"intent_{symbol.lower()}",
        broker_order_id=f"broker_{symbol.lower()}" if event_type != LedgerEventType.order_intent else None,
        fill_id=f"fill_{symbol.lower()}" if event_type in {LedgerEventType.fill, LedgerEventType.partial_fill} else None,
        idempotency_key=f"idem_{symbol.lower()}",
        dedupe_key=f"{event_type.value}:{order_plan_id}",
        source="mock",
        data_mode="fixture",
        symbol=symbol,
        side="sell" if event_type == LedgerEventType.partial_fill else "buy",
        quantity=10,
        price=notional / 10,
        notional=notional,
        metadata={"reason": reason} if reason else {},
        occurred_at=datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
    )


def test_signal_outcome_logger_links_predictions_to_fill_reject_and_trim_outcomes() -> None:
    signal_set = CalibratedSignalSet(
        signals=[
            _calibrated_signal("AAA", action=SignalAction.buy_ready),
            _calibrated_signal("BBB", action=SignalAction.buy_ready),
            _calibrated_signal("CCC", action=SignalAction.trim),
        ],
        provider_status={},
        data_quality={"usable": True, "data_mode": "fixture"},
    )
    entries = [
        _entry("AAA", LedgerEventType.order_intent, notional=1_000),
        _entry("AAA", LedgerEventType.fill, notional=1_000),
        _entry("BBB", LedgerEventType.order_intent, notional=800),
        _entry("BBB", LedgerEventType.reject, notional=800, reason="risk_budget_exceeded"),
        _entry("CCC", LedgerEventType.order_intent, notional=600),
        _entry("CCC", LedgerEventType.partial_fill, notional=300),
    ]

    outcome_log = SignalOutcomeLogger().build_from_calibrated_signal_set(
        calibrated_signal_set=signal_set,
        ledger_entries=entries,
    )

    records = {record.symbol: record for record in outcome_log.records}
    assert outcome_log.status == "available"
    assert outcome_log.ledger_event_count == len(entries)
    assert records["AAA"].realized_outcome == "filled"
    assert records["AAA"].fill_ratio == 1.0
    assert records["BBB"].realized_outcome == "rejected"
    assert records["BBB"].rejection_reasons == ["risk_budget_exceeded"]
    assert records["CCC"].predicted_action == "trim"
    assert records["CCC"].realized_outcome == "trimmed"
    assert records["CCC"].filled_notional == 300
    assert outcome_log.live_auto_update is False
