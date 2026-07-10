from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionState,
    OperatorCycleClaim,
    OperatorSafetyState,
    PaperExecutionSession,
    PaperOrderDispatch,
    PendingLiquidationCheckpoint,
    StateStoreProvenance,
    StrategyOperatorState,
)
from quantpilot.packages.core.operator.status_snapshot import (
    build_professional_operator_status,
    unavailable_professional_operator_status,
)


NOW = datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc)


def _provenance() -> StateStoreProvenance:
    return StateStoreProvenance(
        store_id="store-status",
        schema_version=8,
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint="sha256:" + "a" * 64,
        created_at=NOW - timedelta(days=1),
    )


def _safety() -> OperatorSafetyState:
    return OperatorSafetyState(
        policy_id="policy-1",
        autopilot_paused=False,
        broker_healthy=True,
        updated_at=NOW - timedelta(seconds=10),
    )


def _position(symbol: str = "005930") -> ManagedPositionState:
    reconciled_at = NOW - timedelta(seconds=20)
    return ManagedPositionState(
        policy_id="policy-1",
        policy_version=1,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol=symbol,
        quantity=3,
        average_entry_price=70000,
        atr14=1200,
        active_stop=64400,
        opened_at=NOW - timedelta(days=1),
        updated_at=reconciled_at,
        reconciled_snapshot_id="snapshot-1",
        reconciled_at=reconciled_at,
    )


def _strategy() -> StrategyOperatorState:
    return StrategyOperatorState(
        policy_id="policy-1",
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        health_status="active",
        retirement_phase="none",
        last_risk_evaluated_at=NOW - timedelta(seconds=20),
        last_rebalance_session="2026-W28",
        updated_at=NOW - timedelta(seconds=10),
    )


def _completed_weekly_claim() -> OperatorCycleClaim:
    return OperatorCycleClaim(
        policy_id="policy-1",
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        cycle_kind="weekly_rebalance",
        bucket="2026-W28",
        claimed_at=NOW - timedelta(minutes=1),
        lease_expires_at=NOW + timedelta(minutes=1),
        completed_at=NOW - timedelta(seconds=30),
    )


def _closed_session() -> PaperExecutionSession:
    ended_at = NOW - timedelta(seconds=5)
    return PaperExecutionSession(
        session_id="session-status",
        store_id="store-status",
        account_scope_fingerprint="sha256:" + "a" * 64,
        fencing_token=1,
        status="closed",
        started_at=NOW - timedelta(minutes=2),
        lease_expires_at=NOW + timedelta(minutes=3),
        updated_at=ended_at,
        ended_at=ended_at,
    )


def _unknown_liquidation() -> PendingLiquidationCheckpoint:
    return PendingLiquidationCheckpoint(
        order_plan_id="order-risk-1",
        policy_id="policy-1",
        policy_version=1,
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        symbol="005930",
        purpose="protective_exit",
        idempotency_key="sha256:" + "b" * 64,
        quantity_before=3,
        quantity_requested=2,
        expected_quantity_after=1,
        account_quantity_before=3,
        expected_account_quantity_after=1,
        limit_price=65000,
        quote_as_of=NOW - timedelta(seconds=31),
        reconciled_snapshot_id="snapshot-1",
        status="outcome_unknown",
        broker_submission_attempted=True,
        risk_check_id="risk-1",
        created_at=NOW - timedelta(seconds=30),
        updated_at=NOW - timedelta(seconds=10),
    )


def _terminal_pending_dispatch() -> PaperOrderDispatch:
    return PaperOrderDispatch(
        order_plan_id="order-terminal-pending",
        run_id="run-status",
        idempotency_key="sha256:" + "c" * 64,
        request_fingerprint="sha256:" + "d" * 64,
        policy_id="policy-1",
        policy_version=1,
        user_id="paper-user",
        strategy_id="pullback_trend_v2",
        strategy_version="2.0",
        purpose="rebalance",
        symbol="005930",
        side="buy",
        quantity=1,
        limit_price=70000,
        quote_as_of=NOW - timedelta(seconds=70),
        quote_last=70100,
        quote_bid=69900,
        quote_ask=70100,
        quote_reference_basis="best_ask",
        risk_check_id="risk-terminal",
        risk_check_expires_at=NOW + timedelta(minutes=1),
        submission_evidence_expires_at=NOW + timedelta(seconds=30),
        reconciled_snapshot_id="snapshot-1",
        reconciled_snapshot_at=NOW - timedelta(seconds=70),
        snapshot_cash=300000,
        snapshot_equity=1000000,
        snapshot_symbol_quantity=0,
        snapshot_symbol_orderable_quantity=0,
        snapshot_daily_loss_ratio=-0.01,
        snapshot_monthly_loss_ratio=-0.02,
        broker_orderable_cash=250000,
        broker_orderable_buy_quantity=3,
        entry_atr14=1200,
        store_id="store-status",
        session_id="session-status",
        fencing_token=1,
        account_scope_fingerprint="sha256:" + "a" * 64,
        status="rejected",
        reconciliation_status="pending",
        attempt_count=1,
        dispatch_claimed_at=NOW - timedelta(seconds=50),
        last_error_code="secret_account_123",
        prepared_at=NOW - timedelta(seconds=60),
        updated_at=NOW - timedelta(seconds=10),
    )


def test_fresh_complete_evidence_projects_safe_sorted_secret_free_status() -> None:
    snapshot = build_professional_operator_status(
        observed_at=NOW,
        provenance=_provenance(),
        safety_states=[_safety()],
        positions=[_position("000660"), _position("005930")],
        strategy_states=[_strategy()],
        cycle_claims=[_completed_weekly_claim()],
        sessions=[_closed_session()],
        dispatches=[],
        pending_liquidations=[],
    )

    assert snapshot.available is True
    assert snapshot.overall_status == "safe"
    assert snapshot.live_trading_enabled is False
    assert snapshot.freshness.status == "fresh"
    assert [item.symbol for item in snapshot.positions] == ["000660", "005930"]
    assert snapshot.rebalance[0].claim_status == "completed"
    assert snapshot.reconciliation.unresolved_count == 0

    payload = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True)
    for forbidden in (
        "account_scope_fingerprint",
        "idempotency_key",
        "request_fingerprint",
        "order_plan_payload",
        "broker_order_reference",
        "broker_forwarding_order_org_number",
        "broker_order_branch_number",
    ):
        assert forbidden not in payload
    assert "current_price" not in payload
    assert "profit" not in payload
    assert "stop_distance" not in payload


def test_missing_or_stale_durable_evidence_never_renders_safe() -> None:
    empty = build_professional_operator_status(
        observed_at=NOW,
        provenance=_provenance(),
        safety_states=[],
        positions=[],
        strategy_states=[],
        cycle_claims=[],
        sessions=[],
        dispatches=[],
        pending_liquidations=[],
    )
    assert empty.available is True
    assert empty.overall_status == "attention"
    assert empty.freshness.status == "unavailable"
    assert "operator_safety_state_missing" in empty.safety.reason_codes

    session_missing = build_professional_operator_status(
        observed_at=NOW,
        provenance=_provenance(),
        safety_states=[_safety()],
        positions=[_position()],
        strategy_states=[_strategy()],
        cycle_claims=[_completed_weekly_claim()],
        sessions=[],
        dispatches=[],
        pending_liquidations=[],
    )
    assert session_missing.overall_status == "attention"
    assert "paper_session_evidence_missing" in session_missing.safety.reason_codes

    stale_safety = _safety().model_copy(
        update={"updated_at": NOW - timedelta(minutes=4)}
    )
    stale = build_professional_operator_status(
        observed_at=NOW,
        provenance=_provenance(),
        safety_states=[stale_safety],
        positions=[],
        strategy_states=[_strategy()],
        cycle_claims=[_completed_weekly_claim()],
        sessions=[],
        dispatches=[],
        pending_liquidations=[],
        stale_after_seconds=180,
    )
    assert stale.overall_status == "attention"
    assert stale.safety.policies[0].stale is True


def test_outcome_unknown_liquidation_is_critical_and_allowlisted() -> None:
    snapshot = build_professional_operator_status(
        observed_at=NOW,
        provenance=_provenance(),
        safety_states=[_safety()],
        positions=[_position()],
        strategy_states=[_strategy()],
        cycle_claims=[_completed_weekly_claim()],
        sessions=[],
        dispatches=[],
        pending_liquidations=[_unknown_liquidation()],
    )

    assert snapshot.overall_status == "critical"
    assert snapshot.reconciliation.status == "critical"
    assert snapshot.reconciliation.outcome_unknown_count == 1
    pending = snapshot.reconciliation.pending_liquidations[0]
    assert pending.remaining_quantity == 2
    assert not hasattr(pending, "idempotency_key")


def test_orphan_weekly_claim_is_visible_as_critical_evidence_mismatch() -> None:
    snapshot = build_professional_operator_status(
        observed_at=NOW,
        provenance=_provenance(),
        safety_states=[_safety()],
        positions=[],
        strategy_states=[],
        cycle_claims=[_completed_weekly_claim()],
        sessions=[_closed_session()],
        dispatches=[],
        pending_liquidations=[],
    )

    assert snapshot.overall_status == "critical"
    assert len(snapshot.rebalance) == 1
    assert snapshot.rebalance[0].claim_status == "evidence_mismatch"
    assert "strategy_state_missing_for_rebalance_claim" in (
        snapshot.rebalance[0].reason_codes
    )


def test_terminal_pending_reconciliation_and_free_text_are_fail_closed() -> None:
    conflict_at = NOW - timedelta(seconds=5)
    conflicted_position = ManagedPositionState.model_validate(
        _position().model_copy(
            update={
                "attribution_status": "conflicted",
                "attribution_conflict_reason": "SECRET_ACCOUNT_1234567890",
                "attribution_conflicted_at": conflict_at,
                "updated_at": conflict_at,
            }
        ).model_dump()
    )
    safety = _safety().model_copy(
        update={"last_blocked_reason": "SECRET_ACCOUNT_1234567890"}
    )
    strategy = _strategy().model_copy(
        update={"reason_codes": ["SECRET_ACCOUNT_1234567890"]}
    )
    pending = _unknown_liquidation().model_copy(
        update={"last_error_code": "SECRET_ACCOUNT_1234567890"}
    )

    snapshot = build_professional_operator_status(
        observed_at=NOW,
        provenance=_provenance(),
        safety_states=[safety],
        positions=[conflicted_position],
        strategy_states=[strategy],
        cycle_claims=[_completed_weekly_claim()],
        sessions=[_closed_session()],
        dispatches=[_terminal_pending_dispatch()],
        pending_liquidations=[pending],
    )

    assert snapshot.overall_status == "critical"
    assert snapshot.reconciliation.unresolved_count == 2
    assert "paper_terminal_reconciliation_pending" in (
        snapshot.reconciliation.reason_codes
    )
    assert snapshot.reconciliation.dispatches[0].last_error_code == (
        "paper_dispatch_error_redacted"
    )
    assert snapshot.reconciliation.pending_liquidations[0].last_error_code == (
        "pending_liquidation_error_redacted"
    )
    rendered = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True)
    assert "SECRET_ACCOUNT_1234567890" not in rendered
    assert "secret_account_123" not in rendered


def test_history_window_manual_resolution_code_is_visible_without_free_text() -> None:
    base = _terminal_pending_dispatch()
    dispatch = PaperOrderDispatch.model_validate(
        base.model_copy(
            update={
                "reconciliation_status": "blocked",
                "last_error_code": (
                    "broker_history_window_manual_resolution_required"
                ),
            }
        ).model_dump()
    )

    snapshot = build_professional_operator_status(
        observed_at=NOW,
        provenance=_provenance(),
        safety_states=[_safety()],
        positions=[_position()],
        strategy_states=[_strategy()],
        cycle_claims=[_completed_weekly_claim()],
        sessions=[_closed_session()],
        dispatches=[dispatch],
        pending_liquidations=[],
    )

    assert snapshot.overall_status == "critical"
    assert snapshot.reconciliation.dispatches[0].last_error_code == (
        "broker_history_window_manual_resolution_required"
    )


def test_unavailable_and_naive_clock_fail_closed() -> None:
    unavailable = unavailable_professional_operator_status(
        observed_at=NOW,
        reason_code="paper_state_db_not_configured",
    )
    assert unavailable.available is False
    assert unavailable.overall_status == "unavailable"
    assert unavailable.freshness.status == "unavailable"

    with pytest.raises(ValueError, match="UTC offset"):
        unavailable_professional_operator_status(
            observed_at=NOW.replace(tzinfo=None),
            reason_code="paper_state_db_not_configured",
        )
