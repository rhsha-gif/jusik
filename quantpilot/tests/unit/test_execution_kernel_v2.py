from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal, getcontext
import hashlib
import importlib
from importlib.machinery import ModuleSpec
import inspect
from itertools import product
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import get_args, get_origin, get_type_hints

import pytest

NOW = datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc)
KERNEL_PATH = Path(__file__).parents[2] / "packages" / "core" / "execution" / "kernel.py"
# Any semantic AST change to this high-assurance boundary requires a fresh
# independent review and an explicit digest update in the reviewed test artifact.
REVIEWED_KERNEL_AST_SHA256 = (
    "750680273FB34423CD095E8C8E64B384D2ECB6FBD9154271A03CC104C6065102"
)
_KERNEL_MODULE: ModuleType | None = None

DIRECT_CHECKS = (
    "local_approval_transition_recorded",
    "order_state_approved",
)
LEVEL4_CHECKS = (
    "guarded_autopilot_enabled",
    "kill_switch_not_engaged",
    "autopilot_not_paused",
    "broker_mode_safe",
    "authority_level_4",
    "policy_version_match",
    "policy_identity_match",
    "broker_health",
    "quote_not_stale",
    "strategy_promotion_approved",
    "strategy_level_allowed",
    "krx_auto_order_window",
    "order_type_allowed",
    "monthly_loss_stop_not_triggered",
    "monthly_loss_pause_allows_order",
    "no_unfilled_conflicting_order",
    "no_unresolved_paper_buy_order",
    "idempotency_key_new",
    "fresh_risk_check_passed",
)
LEVEL5_CHECKS = (
    "fully_automated_operator_enabled",
    "live_trading_disabled",
    "kill_switch_not_engaged",
    "operator_not_paused",
    "broker_mode_safe",
    "authority_level_5",
    "policy_version_match",
    "broker_health",
    "snapshot_not_stale",
    "quote_not_stale",
    "risk_reducing_purpose_verified",
    "strategy_registry_validated_l5",
    "strategy_level_allowed",
    "strategy_recipe_matches_registry",
    "krx_auto_order_window",
    "order_type_allowed",
    "monthly_loss_stop_not_triggered",
    "monthly_loss_pause_allows_order",
    "no_unfilled_conflicting_order",
    "no_unresolved_paper_buy_order",
    "idempotency_key_new",
    "fresh_risk_check_passed",
)


def _checks(names: tuple[str, ...], *, failed: str | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in names:
        passed = failed is None or name != failed
        rows.append({"name": name, "passed": passed, "detail_code": name})
        if not passed:
            break
    return rows


def _valid_direct_raw() -> dict[str, object]:
    return {
        "schema_version": 1,
        "observation_phase": "ready_to_submit",
        "candidate": {
            "order_plan_id": "oplan-kernel-001",
            "intent": {
                "intent_id": "oint-kernel-001",
                "symbol": "005930",
                "side": "buy",
                "order_type": "limit",
                "quantity": Decimal("10"),
                "limit_price": Decimal("70000"),
                "notional": Decimal("700000"),
                "target_weight": Decimal("0.07"),
                "reason": "fixture order",
                "quote_time": NOW - timedelta(minutes=4),
            },
            "policy_id": "policy-kernel",
            "policy_version": 7,
            "purpose": "rebalance",
            "status": "user_approved",
            "idempotency_key": "idem-kernel-001",
            "risk_check_id": "risk-kernel-001",
            "risk_check_expires_at": NOW + timedelta(minutes=5),
            "approved_by": "local-operator-label",
            "order_expires_at": NOW + timedelta(minutes=10),
            "strategy_binding": None,
        },
        "authorization": {
            "kind": "human_direct_level_3",
            "evaluation_state": "passed",
            "authority_algorithm_version": 1,
            "source": "level3_direct_transition",
            "authorized": True,
            "policy_id": "policy-kernel",
            "policy_version": 7,
            "policy_user_id": "fixture-user",
            "assurance": "unverified_local",
            "evaluated_at": NOW - timedelta(minutes=3),
            "checks": _checks(DIRECT_CHECKS),
            "first_failed_check": None,
            "approval_transition_source": "approve_order_plan",
            "approval_transition_at": NOW - timedelta(minutes=3),
            "authenticated_subject_id": None,
            "authentication_reference": None,
        },
        "single_risk": {
            "evaluation_state": "passed",
            "risk_check_id": "risk-kernel-001",
            "order_plan_id": "oplan-kernel-001",
            "passed": True,
            "policy_id": "policy-kernel",
            "policy_version": 7,
            "policy_user_id": "fixture-user",
            "snapshot_user_id": "fixture-user",
            "idempotency_key": "idem-kernel-001",
            "created_at": NOW - timedelta(minutes=2),
            "expires_at": NOW + timedelta(minutes=5),
            "passed_checks": ["order_type_allowed", "cash_available"],
            "failed_checks": [],
            "snapshot_id": "snapshot-kernel-001",
            "snapshot_captured_at": NOW - timedelta(minutes=3),
            "snapshot_fingerprint_schema_version": 1,
            "snapshot_fingerprint": "sha256:" + "a" * 64,
            "submit_market_quote_symbol": None,
            "submit_market_quote_as_of": None,
            "submit_market_quote_fingerprint_schema_version": None,
            "submit_market_quote_fingerprint": None,
            "guardrail_fingerprint_schema_version": 1,
            "guardrail_fingerprint": "sha256:" + "b" * 64,
            "reservation_state": "none",
        },
        "batch_risk": {
            "evaluation_state": "passed",
            "passed": True,
            "mode": "full_batch",
            "policy_version": 7,
            "accepted_order_plan_ids": ["oplan-kernel-001"],
            "failed_checks": [],
        },
        "final_safety": {
            "evaluation_state": "passed",
            "captured_at": NOW - timedelta(minutes=1),
            "policy_snapshot_current": True,
            "policy_kill_switch_engaged": False,
            "live_trading_enabled": False,
            "operator_kill_switch_engaged": False,
            "autopilot_paused": False,
            "broker_healthy": True,
            "failed_checks": [],
        },
        "context": {
            "data_mode": "fixture",
            "run_mode": "level_3_direct",
            "market_orders_enabled": False,
            "current_policy_id": "policy-kernel",
            "current_policy_version": 7,
            "external_paper_enabled": False,
            "policy_user_id": "fixture-user",
            "operator_run_id": None,
        },
        "paper": {
            "evaluation_state": "not_applicable",
        },
        "capability": {
            "evaluation_state": "passed",
            "profile_id": "mock_v1",
        },
        "evaluated_at": NOW,
    }


def _valid_ticket_raw() -> dict[str, object]:
    raw = _valid_direct_raw()
    raw["context"]["run_mode"] = "level_3_ticket"
    authorization = raw["authorization"]
    authorization["kind"] = "human_ticket_level_3"
    authorization["source"] = "level3_ticket"
    authorization["checks"] = _checks((
        "ticket_status_approved",
        "ticket_time_valid",
        "ticket_identity_match",
        "ticket_data_mode_match",
        "order_state_approved",
    ))
    authorization["approval_transition_source"] = None
    authorization["approval_transition_at"] = None
    authorization.update({
        "ticket_id": "ticket-kernel-001",
        "ticket_user_id": "fixture-user",
        "ticket_policy_id": "policy-kernel",
        "ticket_policy_version": 7,
        "ticket_order_plan_id": "oplan-kernel-001",
        "ticket_data_mode": "fixture",
        "ticket_status": "approved",
        "requested_at": NOW - timedelta(minutes=6),
        "approved_at": NOW - timedelta(minutes=4),
        "expires_at": NOW + timedelta(minutes=5),
        "approved_by_label": "local-operator-label",
        "authentication_assurance": "caller_label_only",
    })
    return raw


def _valid_level4_raw() -> dict[str, object]:
    raw = _valid_direct_raw()
    raw["context"]["run_mode"] = "guarded_level_4"
    candidate = raw["candidate"]
    candidate["strategy_binding"] = {
        "strategy_id": "default_strategy",
        "strategy_version": "2.0",
        "symbol": "005930",
        "side": "buy",
        "policy_version": 7,
    }
    authorization = raw["authorization"]
    authorization["kind"] = "guarded_level_4"
    authorization["source"] = "guarded_authority_v1"
    authorization["assurance"] = "policy_authorized"
    authorization["checks"] = _checks(LEVEL4_CHECKS)
    authorization["approval_transition_source"] = None
    authorization["approval_transition_at"] = None
    authorization.update({
        "recipe_strategy_id": "default_strategy",
        "recipe_version": "2",
        "promotion_status": "validated_l4",
        "allowed_execution_levels": ["level_4", "guarded_autopilot"],
    })
    return raw


def _valid_level5_raw() -> dict[str, object]:
    raw = _valid_direct_raw()
    raw["context"]["run_mode"] = "operator_mock_submit"
    raw["context"]["operator_run_id"] = "run-kernel-001"
    candidate = raw["candidate"]
    candidate["strategy_binding"] = {
        "strategy_id": "default_strategy",
        "strategy_version": "2.0",
        "symbol": "005930",
        "side": "buy",
        "policy_version": 7,
    }
    authorization = raw["authorization"]
    authorization["kind"] = "automated_level_5"
    authorization["source"] = "level5_authority_v1"
    authorization["assurance"] = "operator_authorized"
    authorization["evaluated_at"] = NOW
    authorization["checks"] = _checks(LEVEL5_CHECKS)
    authorization["approval_transition_source"] = None
    authorization["approval_transition_at"] = None
    authorization.update({
        "operator_run_id": "run-kernel-001",
        "recipe_strategy_id": "default_strategy",
        "recipe_version": "2",
        "registry_strategy_id": "default_strategy",
        "registry_version": "2.0",
        "registry_spec_hash": "sha256:" + "c" * 64,
        "registry_status": "validated_l5",
        "registry_allowed_execution_levels": ["level_5", "fully_automated"],
        "registry_min_policy_version": 1,
        "registry_max_policy_version": 10,
        "lifecycle_strategy_id": "default_strategy",
        "lifecycle_version": "2.0",
        "lifecycle_status": "paper_validated",
        "lifecycle_spec_hash": "sha256:" + "c" * 64,
    })
    raw["single_risk"]["created_at"] = NOW
    return raw


def _valid_kis_level5_raw() -> dict[str, object]:
    raw = _valid_level5_raw()
    context = raw["context"]
    context["data_mode"] = "paper_trading"
    context["run_mode"] = "operator_paper_submit"
    context["external_paper_enabled"] = True
    single = raw["single_risk"]
    single.update({
        "submit_market_quote_symbol": "005930",
        "submit_market_quote_as_of": NOW - timedelta(minutes=1),
        "submit_market_quote_fingerprint_schema_version": 1,
        "submit_market_quote_fingerprint": "sha256:" + "d" * 64,
        "reservation_state": "required_not_prepared",
    })
    paper_run_projection = {
        "paper_run_id": "run-kernel-001",
        "run_user_id": "fixture-user",
        "run_policy_id": "policy-kernel",
        "run_policy_version": 7,
        "run_mode": "operator_paper_submit",
        "data_mode": "paper_trading",
        "checkpoint_status": "started",
    }
    paper_run_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(
            paper_run_projection,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    raw["paper"] = {
        "evaluation_state": "passed",
        "paper_run_id": "run-kernel-001",
        "paper_run_fingerprint_schema_version": 1,
        "paper_run_fingerprint": paper_run_fingerprint,
        "run_user_id": "fixture-user",
        "run_policy_id": "policy-kernel",
        "run_policy_version": 7,
        "run_mode": "operator_paper_submit",
        "data_mode": "paper_trading",
        "checkpoint_status": "started",
        "snapshot_id": "snapshot-kernel-001",
        "snapshot_captured_at": NOW - timedelta(minutes=3),
        "snapshot_fingerprint_schema_version": 1,
        "snapshot_fingerprint": "sha256:" + "a" * 64,
        "snapshot_deadline": NOW + timedelta(minutes=4),
        "quote_symbol": "005930",
        "quote_as_of": NOW - timedelta(minutes=1),
        "quote_fingerprint_schema_version": 1,
        "quote_fingerprint": "sha256:" + "d" * 64,
        "quote_deadline": NOW + timedelta(minutes=2),
        "entry_atr14": "none",
        "store_id": "paper-store-kernel",
        "account_scope_fingerprint": "sha256:" + "f" * 64,
        "session_id": "session-kernel-001",
        "fencing_token": 3,
        "session_revision": 4,
        "session_status": "active",
        "session_lease_deadline": NOW + timedelta(minutes=3),
    }
    raw["capability"] = {
        "evaluation_state": "passed",
        "profile_id": "kis_paper_v1",
    }
    return raw


def _set_phase(raw: dict[str, object], phase: str) -> None:
    raw["observation_phase"] = phase
    single = raw["single_risk"]
    batch = raw["batch_risk"]
    final = raw["final_safety"]
    capability = raw["capability"]
    assert isinstance(single, dict)
    assert isinstance(batch, dict)
    assert isinstance(final, dict)
    assert isinstance(capability, dict)
    if phase == "candidate_failure":
        raw["single_risk"] = {"evaluation_state": "not_evaluated"}
        raw["batch_risk"] = {"evaluation_state": "not_evaluated"}
        raw["final_safety"] = {"evaluation_state": "not_evaluated"}
        raw["capability"] = {"evaluation_state": "not_evaluated"}
    elif phase == "single_risk_failure":
        single["evaluation_state"] = "failed"
        single["passed"] = False
        raw["batch_risk"] = {"evaluation_state": "not_evaluated"}
        raw["final_safety"] = {"evaluation_state": "not_evaluated"}
        raw["capability"] = {"evaluation_state": "not_evaluated"}
    elif phase == "batch_risk_failure":
        batch["evaluation_state"] = "failed"
        batch["passed"] = False
        raw["final_safety"] = {"evaluation_state": "not_evaluated"}
        raw["capability"] = {"evaluation_state": "not_evaluated"}
    elif phase == "final_safety_failure":
        final["evaluation_state"] = "failed"
        raw["capability"] = {"evaluation_state": "not_evaluated"}
    elif phase == "capability_failure":
        capability["evaluation_state"] = "failed"


def _kernel() -> ModuleType:
    global _KERNEL_MODULE
    source = KERNEL_PATH.read_text(encoding="utf-8")
    errors = _purity_errors(source)
    if errors:
        raise AssertionError(f"kernel purity violations: {errors}")
    if _KERNEL_MODULE is None:
        _KERNEL_MODULE = importlib.import_module("quantpilot.packages.core.execution.kernel")
        runtime_errors = _runtime_global_errors(_KERNEL_MODULE)
        if runtime_errors:
            raise AssertionError(f"kernel runtime purity violations: {runtime_errors}")
    return _KERNEL_MODULE


def _validate(raw: object):
    return _kernel().validate_kernel_input_v1(raw)


def _decision(raw: dict[str, object]):
    module = _kernel()
    return module.evaluate_execution(module.validate_kernel_input_v1(raw))


def test_valid_level3_direct_mock_is_pure_and_deterministic() -> None:
    raw = _valid_direct_raw()
    before = deepcopy(raw)
    evidence = _validate(raw)
    first = _kernel().evaluate_execution(evidence)
    second = _kernel().evaluate_execution(evidence)

    assert first == second
    assert first.verdict == "eligible_for_legacy_submit"
    assert first.blocked_stage == "none"
    assert first.reason_codes == ()
    assert first.durable_prepare_requirement == "not_required"
    assert first.atomic_reservation_requirement == "not_required"
    assert first.intended_next_stage == "legacy_submit_handoff"
    assert raw == before
    assert evidence.candidate.intent.quantity == Decimal("10")
    with pytest.raises(Exception):
        evidence.candidate.status = "submitted"


def test_valid_ticket_simulated_level4_and_level5_profiles_are_representable() -> None:
    ticket = _decision(_valid_ticket_raw())
    assert ticket.verdict == "eligible_for_legacy_submit"

    simulated = _valid_direct_raw()
    simulated["capability"]["profile_id"] = "simulated_paper_v1"
    simulated_decision = _decision(simulated)
    assert simulated_decision.verdict == "eligible_for_legacy_submit"

    level4 = _decision(_valid_level4_raw())
    assert level4.verdict == "eligible_for_legacy_submit"

    level5 = _decision(_valid_level5_raw())
    assert level5.verdict == "eligible_for_legacy_submit"


def _valid_level12_raw() -> dict[str, object]:
    raw = _valid_direct_raw()
    raw["context"]["run_mode"] = "level_1_2_mock"
    authorization = raw["authorization"]
    authorization["kind"] = "simulated_level_1_2"
    authorization["source"] = "simulated_harness"
    authorization["assurance"] = "simulated"
    authorization["checks"] = _checks((
        "simulated_execution_only",
        "mock_profile_required",
    ))
    authorization["approval_transition_source"] = None
    authorization["approval_transition_at"] = None
    authorization["simulation_reference"] = "fixture-simulation-001"
    return raw


@pytest.mark.parametrize(
    "factory",
    [
        _valid_level12_raw,
        _valid_direct_raw,
        _valid_ticket_raw,
        _valid_level4_raw,
        _valid_level5_raw,
        _valid_kis_level5_raw,
    ],
)
def test_all_primary_profile_rows_are_eligible(factory) -> None:
    assert _decision(factory()).verdict == "eligible_for_legacy_submit"


def test_remaining_simulated_paper_profile_rows_are_eligible() -> None:
    ticket = _valid_ticket_raw()
    ticket["context"]["data_mode"] = "paper_trading"
    ticket["authorization"]["ticket_data_mode"] = "paper_trading"
    ticket["capability"]["profile_id"] = "simulated_paper_v1"

    level4 = _valid_level4_raw()
    level4["capability"]["profile_id"] = "simulated_paper_v1"

    level5 = _valid_level5_raw()
    level5["context"]["run_mode"] = "operator_paper_submit"
    level5["capability"]["profile_id"] = "simulated_paper_v1"

    for raw in (ticket, level4, level5):
        assert _decision(raw).verdict == "eligible_for_legacy_submit"


def test_profile_matrix_is_the_exact_closed_cartesian_set() -> None:
    allowed_rows = {
        ("mock_v1", "fixture", "level_1_2_mock", "simulated_level_1_2", False, "not_applicable"),
        ("mock_v1", "fixture", "level_3_direct", "human_direct_level_3", False, "not_applicable"),
        ("mock_v1", "fixture", "level_3_ticket", "human_ticket_level_3", False, "not_applicable"),
        ("mock_v1", "fixture", "guarded_level_4", "guarded_level_4", False, "not_applicable"),
        ("mock_v1", "fixture", "operator_mock_submit", "automated_level_5", False, "not_applicable"),
        ("simulated_paper_v1", "fixture", "level_3_direct", "human_direct_level_3", False, "not_applicable"),
        ("simulated_paper_v1", "paper_trading", "level_3_ticket", "human_ticket_level_3", False, "not_applicable"),
        ("simulated_paper_v1", "fixture", "guarded_level_4", "guarded_level_4", False, "not_applicable"),
        ("simulated_paper_v1", "fixture", "operator_paper_submit", "automated_level_5", False, "not_applicable"),
        ("kis_paper_v1", "paper_trading", "operator_paper_submit", "automated_level_5", True, "passed"),
    }
    profiles = (None, "mock_v1", "simulated_paper_v1", "kis_paper_v1")
    data_modes = (
        "fixture",
        "local_historical",
        "external_historical",
        "realtime_market_data",
        "paper_trading",
        "live_trading_candidate",
        "live_canary",
        "live_scaled",
    )
    run_modes = (
        "level_1_2_mock",
        "level_3_direct",
        "level_3_ticket",
        "guarded_level_4",
        "operator_mock_submit",
        "operator_paper_submit",
        "professional_risk_reduction",
    )
    authorization_kinds = (
        "simulated_level_1_2",
        "human_direct_level_3",
        "human_ticket_level_3",
        "guarded_level_4",
        "automated_level_5",
        "professional_risk_reduction",
    )
    paper_states = ("passed", "failed", "not_evaluated", "not_applicable")
    matrix = _kernel()._profile_matrix_valid

    for row in product(
        profiles,
        data_modes,
        run_modes,
        authorization_kinds,
        (False, True),
        paper_states,
    ):
        profile, data_mode, run_mode, kind, external, paper_state = row
        evidence = SimpleNamespace(
            capability=SimpleNamespace(profile_id=profile),
            context=SimpleNamespace(
                data_mode=data_mode,
                run_mode=run_mode,
                external_paper_enabled=external,
            ),
            authorization=SimpleNamespace(kind=kind),
            paper=SimpleNamespace(evaluation_state=paper_state),
        )
        assert matrix(evidence) is (row in allowed_rows), row


def test_global_market_order_enablement_blocks_an_otherwise_valid_limit_order() -> None:
    raw = _valid_direct_raw()
    raw["context"]["market_orders_enabled"] = True

    decision = _decision(raw)

    assert decision.blocked_stage == "capability"
    assert decision.reason_codes == ("market_order_disabled",)


@pytest.mark.parametrize(
    "raw",
    [
        {"factory": "direct_wrong_data"},
        {"factory": "direct_wrong_profile"},
        {"factory": "external_wrong_profile"},
        {"factory": "mock_claims_kis_profile"},
    ],
)
def test_unlisted_profile_compositions_fail_closed(raw: dict[str, str]) -> None:
    case = raw["factory"]
    if case == "direct_wrong_data":
        evidence = _valid_direct_raw()
        evidence["context"]["data_mode"] = "paper_trading"
    elif case == "direct_wrong_profile":
        evidence = _valid_direct_raw()
        evidence["capability"]["profile_id"] = "kis_paper_v1"
    elif case == "external_wrong_profile":
        evidence = _valid_kis_level5_raw()
        evidence["capability"]["profile_id"] = "mock_v1"
    else:
        evidence = _valid_level5_raw()
        evidence["capability"]["profile_id"] = "kis_paper_v1"
    _set_phase(evidence, "capability_failure")
    decision = _decision(evidence)
    assert decision.blocked_stage == "capability"
    assert "broker_capability_mismatch" in decision.reason_codes


def test_professional_risk_reduction_v1_stays_closed() -> None:
    raw = _valid_level5_raw()
    raw["candidate"]["purpose"] = "protective_exit"
    raw["candidate"]["intent"]["side"] = "sell"
    raw["candidate"]["strategy_binding"]["side"] = "sell"
    raw["context"]["run_mode"] = "professional_risk_reduction"
    raw["context"]["operator_run_id"] = None
    raw["authorization"]["kind"] = "professional_risk_reduction"
    raw["authorization"]["source"] = "professional_authority_v1"
    raw["authorization"]["operator_run_id"] = None

    decision = _decision(raw)

    assert decision.verdict == "blocked"
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("professional_binding_not_supported",)


def test_kis_ticket_level3_is_closed_at_actor_assurance() -> None:
    raw = _valid_ticket_raw()
    raw["context"]["data_mode"] = "paper_trading"
    raw["context"]["external_paper_enabled"] = True
    raw["authorization"]["ticket_data_mode"] = "paper_trading"
    raw["paper"] = {"evaluation_state": "passed"}
    raw["capability"] = {"evaluation_state": "passed", "profile_id": "kis_paper_v1"}
    decision = _decision(raw)
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("actor_assurance_missing",)


def test_optional_evidence_gaps_fail_closed_without_uncaught_evaluation() -> None:
    direct = _valid_direct_raw()
    direct["authorization"]["approval_transition_at"] = None

    ticket = _valid_ticket_raw()
    ticket["authorization"]["approved_at"] = None

    level5 = _valid_level5_raw()
    level5["authorization"]["recipe_version"] = None

    paper = _valid_kis_level5_raw()
    paper["paper"]["snapshot_deadline"] = None

    risk = _valid_direct_raw()
    risk["single_risk"]["expires_at"] = None

    for raw in (direct, ticket, level5, paper, risk):
        decision = _decision(raw)
        assert decision.verdict == "blocked"
        assert decision.blocked_stage != "none"


@pytest.mark.parametrize(
    ("recipe_version", "registry_version", "lifecycle_version", "eligible"),
    [
        ("release-alpha", "release-alpha", "release-alpha", True),
        ("²", "2", "2", False),
        ("²", "²", "²", False),
        ("２", "2", "2", True),
        ("２.０", "2", "2", True),
    ],
)
def test_strategy_version_matching_is_total_and_preserves_legacy_unicode(
    recipe_version: str,
    registry_version: str,
    lifecycle_version: str,
    eligible: bool,
) -> None:
    raw = _valid_level5_raw()
    authorization = raw["authorization"]
    candidate = raw["candidate"]
    authorization["recipe_version"] = recipe_version
    authorization["registry_version"] = registry_version
    authorization["lifecycle_version"] = lifecycle_version
    candidate["strategy_binding"]["strategy_version"] = recipe_version
    decision = _decision(raw)
    assert (decision.verdict == "eligible_for_legacy_submit") is eligible
    if not eligible:
        assert decision.blocked_stage == "authorization"
        assert set(decision.reason_codes) <= {
            "strategy_authority_mismatch",
            "strategy_binding_mismatch",
        }


def test_long_numeric_version_is_independent_of_interpreter_digit_limit() -> None:
    original_limit = sys.get_int_max_str_digits()
    version = "1" * 5000
    try:
        sys.set_int_max_str_digits(640)
        low_limit = _valid_level5_raw()
        low_limit["candidate"]["strategy_binding"]["strategy_version"] = version
        low_limit["authorization"]["recipe_version"] = version
        low_limit["authorization"]["registry_version"] = version
        low_limit["authorization"]["lifecycle_version"] = version
        low_decision = _decision(low_limit)

        sys.set_int_max_str_digits(10000)
        high_decision = _decision(low_limit)
    finally:
        sys.set_int_max_str_digits(original_limit)
    assert low_decision.verdict == "eligible_for_legacy_submit"
    assert low_decision == high_decision


@pytest.mark.parametrize("mutation", ["missing", "reordered", "duplicated", "post_failure"])
def test_level5_exact_authority_sequence_is_closed(mutation: str) -> None:
    raw = _valid_level5_raw()
    authorization = raw["authorization"]
    checks = authorization["checks"]
    if mutation == "missing":
        authorization["checks"] = checks[:-1]
    elif mutation == "reordered":
        authorization["checks"] = [checks[1], checks[0], *checks[2:]]
    elif mutation == "duplicated":
        authorization["checks"] = [checks[0], checks[0], *checks[1:]]
    else:
        checks[3]["passed"] = False
        authorization["authorized"] = False
        authorization["evaluation_state"] = "failed"
        authorization["first_failed_check"] = checks[3]["name"]
        raw["observation_phase"] = "authorization_failure"
        raw["paper"] = {"evaluation_state": "not_evaluated"}
        raw["single_risk"] = {"evaluation_state": "not_evaluated"}
        raw["batch_risk"] = {"evaluation_state": "not_evaluated"}
        raw["final_safety"] = {"evaluation_state": "not_evaluated"}
        raw["capability"] = {"evaluation_state": "not_evaluated"}
    decision = _decision(raw)
    assert decision.blocked_stage == "authorization"
    assert "authorization_evidence_mismatch" in decision.reason_codes


def test_kis_level5_is_only_declaratively_eligible_and_requires_durability() -> None:
    class Sentinel:
        calls = 0

        def __call__(self):
            type(self).calls += 1
            raise AssertionError("side effect port was called")

        def __iter__(self):
            return self()

        def __repr__(self):
            return self()

        def __str__(self):
            return self()

    broker = Sentinel()
    client = Sentinel()
    store = Sentinel()
    repository = Sentinel()
    audit = Sentinel()
    module = _kernel()
    assert tuple(inspect.signature(module.evaluate_execution).parameters) == ("evidence",)
    assert tuple(inspect.signature(module.validate_kernel_input_v1).parameters) == ("raw_snapshot",)
    evidence = _validate(_valid_kis_level5_raw())
    decision = module.evaluate_execution(evidence)
    assert decision.verdict == "eligible_for_legacy_submit"
    assert decision.durable_prepare_requirement == "required"
    assert decision.atomic_reservation_requirement == "required"
    assert decision.intended_next_stage == "legacy_submit_handoff"
    with pytest.raises(TypeError):
        module.evaluate_execution(evidence, broker=broker)
    hostile_raw = _valid_kis_level5_raw()
    hostile_raw.update({
        "broker": broker,
        "client": client,
        "store": store,
        "repository": repository,
        "audit": audit,
    })
    with pytest.raises(module.KernelEvidenceValidationError):
        module.validate_kernel_input_v1(hostile_raw)
    assert broker.calls == client.calls == store.calls == repository.calls == audit.calls == 0


def test_kis_level3_stays_closed_without_authenticated_subject_binding() -> None:
    raw = _valid_direct_raw()
    raw["context"]["data_mode"] = "paper_trading"
    raw["context"]["external_paper_enabled"] = True
    raw["paper"] = {"evaluation_state": "passed"}
    raw["capability"] = {"evaluation_state": "passed", "profile_id": "kis_paper_v1"}
    decision = _decision(raw)
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("actor_assurance_missing",)
    assert decision.durable_prepare_requirement == "not_evaluated"


def test_kis_missing_strategy_binding_fails_at_paper_stage_before_candidate() -> None:
    raw = _valid_kis_level5_raw()
    raw["candidate"]["strategy_binding"] = None
    raw["candidate"]["status"] = "proposed"
    _set_phase(raw, "candidate_failure")
    decision = _decision(raw)
    assert decision.blocked_stage == "paper_evidence"
    assert decision.reason_codes == ("paper_strategy_binding_missing",)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("quantity", Decimal("10.5"), "quantity_step_mismatch"),
        ("limit_price", Decimal("70000.5"), "price_step_mismatch"),
    ],
)
def test_kis_integer_quantity_and_krw_price_steps(field: str, value: Decimal, reason: str) -> None:
    raw = _valid_kis_level5_raw()
    raw["candidate"]["intent"][field] = value
    _set_phase(raw, "capability_failure")
    decision = _decision(raw)
    assert decision.blocked_stage == "capability"
    assert reason in decision.reason_codes


def test_kis_paper_mismatch_precedes_candidate_and_risk() -> None:
    raw = _valid_kis_level5_raw()
    raw["paper"]["snapshot_fingerprint"] = "sha256:" + "0" * 64
    raw["candidate"]["status"] = "proposed"
    raw["observation_phase"] = "external_paper_input_failure"
    raw["paper"]["evaluation_state"] = "failed"
    raw["single_risk"] = {"evaluation_state": "not_evaluated"}
    raw["batch_risk"] = {"evaluation_state": "not_evaluated"}
    raw["final_safety"] = {"evaluation_state": "not_evaluated"}
    raw["capability"] = {"evaluation_state": "not_evaluated"}
    decision = _decision(raw)
    assert decision.blocked_stage == "paper_evidence"
    assert "paper_evidence_mismatch" in decision.reason_codes


@pytest.mark.parametrize("missing_field", ["paper_run_id", "snapshot_id", "quote_symbol"])
def test_kis_required_run_snapshot_and_quote_payloads_fail_at_paper_stage(
    missing_field: str,
) -> None:
    raw = _valid_kis_level5_raw()
    raw["paper"][missing_field] = None
    raw["candidate"]["status"] = "proposed"
    raw["observation_phase"] = "external_paper_input_failure"
    raw["paper"]["evaluation_state"] = "failed"
    raw["single_risk"] = {"evaluation_state": "not_evaluated"}
    raw["batch_risk"] = {"evaluation_state": "not_evaluated"}
    raw["final_safety"] = {"evaluation_state": "not_evaluated"}
    raw["capability"] = {"evaluation_state": "not_evaluated"}

    decision = _decision(raw)

    assert decision.verdict == "blocked"
    assert decision.blocked_stage == "paper_evidence"
    assert decision.reason_codes == ("paper_evidence_mismatch",)


@pytest.mark.parametrize("factory", [_valid_direct_raw, _valid_kis_level5_raw])
def test_declared_capability_failure_never_becomes_eligible(factory) -> None:
    raw = factory()
    _set_phase(raw, "capability_failure")
    decision = _decision(raw)
    assert decision.verdict == "blocked"
    assert decision.blocked_stage == "capability"
    assert decision.reason_codes == ("broker_capability_mismatch",)


def test_declared_final_safety_failure_never_falls_through() -> None:
    raw = _valid_direct_raw()
    _set_phase(raw, "final_safety_failure")
    decision = _decision(raw)
    assert decision.verdict == "blocked"
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("evidence_prefix_mismatch",)


@pytest.mark.parametrize("failed_checks", [None, ["broker_health"]])
def test_safe_final_payload_cannot_claim_a_failure_prefix(
    failed_checks: object,
) -> None:
    raw = _valid_direct_raw()
    _set_phase(raw, "final_safety_failure")
    raw["final_safety"]["failed_checks"] = failed_checks

    decision = _decision(raw)

    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("evidence_prefix_mismatch",)


def test_kis_submit_quote_symbol_and_time_are_bound_at_risk_stage() -> None:
    future = _valid_kis_level5_raw()
    future_time = NOW + timedelta(seconds=1)
    future["single_risk"]["submit_market_quote_as_of"] = future_time
    future["paper"]["quote_as_of"] = future_time
    future_decision = _decision(future)
    assert future_decision.blocked_stage == "risk"
    assert future_decision.reason_codes == ("future_evidence_timestamp",)

    wrong_symbol = _valid_kis_level5_raw()
    wrong_symbol["single_risk"]["submit_market_quote_symbol"] = "999999"
    wrong_symbol["paper"]["quote_symbol"] = "999999"
    symbol_decision = _decision(wrong_symbol)
    assert symbol_decision.blocked_stage == "risk"
    assert symbol_decision.reason_codes == ("risk_quote_mismatch",)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("paper_run_fingerprint", "sha256:not-a-digest", "paper_evidence_mismatch"),
        ("account_scope_fingerprint", "not-account-provenance", "account_provenance_missing"),
        ("session_status", "closed", "paper_session_status_invalid"),
        ("checkpoint_status", "completed", "checkpoint_status_invalid"),
    ],
)
def test_kis_durable_evidence_shape_and_state_fail_closed(
    field: str,
    value: object,
    reason: str,
) -> None:
    raw = _valid_kis_level5_raw()
    raw["paper"][field] = value
    decision = _decision(raw)
    assert decision.blocked_stage == "paper_evidence"
    assert reason in decision.reason_codes


def test_retained_argument_mutation_changes_diagnostic_fingerprint() -> None:
    baseline = _decision(_valid_kis_level5_raw())
    raw = _valid_kis_level5_raw()
    raw["paper"]["entry_atr14"] = Decimal("1200")
    changed = _decision(raw)
    assert baseline.evidence_fingerprint != changed.evidence_fingerprint


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("status", "proposed", "order_not_user_approved"),
        ("risk_check_id", None, "risk_check_missing"),
        ("order_expires_at", None, "order_expiry_missing"),
        ("order_expires_at", NOW, "order_expired"),
        ("risk_check_expires_at", NOW, "prior_risk_check_expired"),
    ],
)
def test_candidate_failures_are_closed(field: str, value: object, reason: str) -> None:
    raw = _valid_direct_raw()
    candidate = raw["candidate"]
    assert isinstance(candidate, dict)
    candidate[field] = value
    _set_phase(raw, "candidate_failure")
    decision = _decision(raw)
    assert decision.verdict == "blocked"
    assert decision.blocked_stage == "candidate"
    assert decision.reason_codes == (reason,)
    assert decision.intended_next_stage == "none"


def test_identity_precedes_candidate_and_later_failures() -> None:
    raw = _valid_direct_raw()
    candidate = raw["candidate"]
    context = raw["context"]
    assert isinstance(candidate, dict)
    assert isinstance(context, dict)
    candidate["status"] = "proposed"
    context["current_policy_id"] = "different-policy"
    _set_phase(raw, "candidate_failure")
    decision = _decision(raw)
    assert decision.blocked_stage == "identity"
    assert decision.reason_codes == ("policy_identity_mismatch",)


def test_authorization_check_mutation_blocks_without_calling_authority() -> None:
    raw = _valid_direct_raw()
    authorization = raw["authorization"]
    assert isinstance(authorization, dict)
    authorization["checks"] = list(reversed(authorization["checks"]))
    decision = _decision(raw)
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("authorization_evidence_mismatch",)


def test_authorization_failure_precedes_candidate_status_failure() -> None:
    raw = _valid_direct_raw()
    raw["candidate"]["status"] = "proposed"
    raw["observation_phase"] = "authorization_failure"
    raw["authorization"]["evaluation_state"] = "failed"
    raw["authorization"]["authorized"] = False
    raw["authorization"]["checks"] = _checks(
        (DIRECT_CHECKS[0],),
        failed=DIRECT_CHECKS[0],
    )
    raw["authorization"]["first_failed_check"] = DIRECT_CHECKS[0]
    raw["paper"] = {"evaluation_state": "not_evaluated"}
    raw["single_risk"] = {"evaluation_state": "not_evaluated"}
    raw["batch_risk"] = {"evaluation_state": "not_evaluated"}
    raw["final_safety"] = {"evaluation_state": "not_evaluated"}
    raw["capability"] = {"evaluation_state": "not_evaluated"}

    decision = _decision(raw)

    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("authorization_denied",)


def test_authorization_kind_rejects_foreign_variant_payload() -> None:
    raw = _valid_direct_raw()
    raw["authorization"].update({
        "recipe_strategy_id": "foreign-recipe",
        "recipe_version": "1",
        "promotion_status": "validated_l4",
        "allowed_execution_levels": ["level_4"],
    })
    decision = _decision(raw)
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("authorization_evidence_mismatch",)

    level5 = _valid_level5_raw()
    level5["authorization"]["authenticated_subject_id"] = "foreign-subject"
    level5["authorization"]["authentication_reference"] = "foreign-session"
    level5_decision = _decision(level5)
    assert level5_decision.blocked_stage == "authorization"
    assert level5_decision.reason_codes == ("authorization_evidence_mismatch",)


def test_ticket_approval_must_precede_authorization_evaluation() -> None:
    raw = _valid_ticket_raw()
    raw["authorization"]["approved_at"] = raw["authorization"]["evaluated_at"] + timedelta(seconds=1)
    decision = _decision(raw)
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("ticket_expired",)


def test_l5_none_policy_bounds_are_unbounded() -> None:
    raw = _valid_level5_raw()
    raw["authorization"]["registry_min_policy_version"] = None
    raw["authorization"]["registry_max_policy_version"] = None
    assert _decision(raw).verdict == "eligible_for_legacy_submit"


def test_unknown_execution_level_requires_live_candidate_lifecycle() -> None:
    raw = _valid_level5_raw()
    raw["authorization"]["registry_allowed_execution_levels"].append("unknown_live_marker")
    decision = _decision(raw)
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("lifecycle_binding_mismatch",)

    raw["authorization"]["lifecycle_status"] = "live_candidate"
    assert _decision(raw).verdict == "eligible_for_legacy_submit"


def test_l4_validated_l3_is_a_semantic_authority_failure() -> None:
    raw = _valid_level4_raw()
    raw["authorization"]["promotion_status"] = "validated_l3"
    decision = _decision(raw)
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("strategy_authority_mismatch",)


def test_not_applicable_paper_variant_forbids_evaluated_payload() -> None:
    raw = _valid_direct_raw()
    raw["paper"]["paper_run_id"] = "mixed-paper-payload"
    decision = _decision(raw)
    assert decision.blocked_stage == "paper_evidence"
    assert decision.reason_codes == ("paper_evidence_mismatch",)


def test_impossible_observation_prefix_blocks_at_authorization() -> None:
    raw = _valid_direct_raw()
    raw["observation_phase"] = "single_risk_failure"
    decision = _decision(raw)
    assert decision.blocked_stage == "authorization"
    assert decision.reason_codes == ("evidence_prefix_mismatch",)


def test_single_and_batch_risk_failures_keep_first_stage_only() -> None:
    single_raw = _valid_direct_raw()
    single = single_raw["single_risk"]
    assert isinstance(single, dict)
    single["failed_checks"] = ["cash_available"]
    _set_phase(single_raw, "single_risk_failure")
    single_decision = _decision(single_raw)
    assert single_decision.blocked_stage == "risk"
    assert single_decision.reason_codes == ("single_order_risk_failed",)

    batch_raw = _valid_direct_raw()
    batch = batch_raw["batch_risk"]
    assert isinstance(batch, dict)
    batch["mode"] = "partial_batch"
    batch["failed_checks"] = ["portfolio_limit"]
    _set_phase(batch_raw, "batch_risk_failure")
    batch_decision = _decision(batch_raw)
    assert batch_decision.blocked_stage == "risk"
    assert batch_decision.reason_codes == (
        "batch_risk_failed",
        "partial_batch_not_allowed_at_submit",
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("risk_id", "risk_check_mismatch"),
        ("risk_expired", "risk_check_expired"),
        ("batch_membership", "batch_order_not_accepted"),
    ],
)
def test_fresh_risk_and_batch_bindings_fail_closed(mutation: str, reason: str) -> None:
    raw = _valid_direct_raw()
    if mutation == "risk_id":
        raw["single_risk"]["risk_check_id"] = "different-risk"
    elif mutation == "risk_expired":
        raw["single_risk"]["expires_at"] = NOW
    else:
        raw["batch_risk"]["accepted_order_plan_ids"] = ["another-order"]
    decision = _decision(raw)
    assert decision.blocked_stage == "risk"
    assert reason in decision.reason_codes


def test_market_order_fails_at_legacy_first_stage_by_authority_level() -> None:
    direct = _valid_direct_raw()
    direct["candidate"]["intent"]["order_type"] = "market"
    direct["candidate"]["intent"]["limit_price"] = None
    direct["single_risk"]["failed_checks"] = ["order_type_allowed"]
    _set_phase(direct, "single_risk_failure")
    direct_decision = _decision(direct)
    assert direct_decision.blocked_stage == "risk"
    assert direct_decision.reason_codes == ("single_order_risk_failed",)

    for factory, checks in ((_valid_level4_raw, LEVEL4_CHECKS), (_valid_level5_raw, LEVEL5_CHECKS)):
        raw = factory()
        raw["candidate"]["intent"]["order_type"] = "market"
        raw["candidate"]["intent"]["limit_price"] = None
        failed_index = checks.index("order_type_allowed")
        raw["authorization"]["checks"] = _checks(
            checks[: failed_index + 1],
            failed="order_type_allowed",
        )
        raw["authorization"]["evaluation_state"] = "failed"
        raw["authorization"]["authorized"] = False
        raw["authorization"]["first_failed_check"] = "order_type_allowed"
        raw["observation_phase"] = "authorization_failure"
        raw["paper"] = {"evaluation_state": "not_evaluated"}
        raw["single_risk"] = {"evaluation_state": "not_evaluated"}
        raw["batch_risk"] = {"evaluation_state": "not_evaluated"}
        raw["final_safety"] = {"evaluation_state": "not_evaluated"}
        raw["capability"] = {"evaluation_state": "not_evaluated"}
        decision = _decision(raw)
        assert decision.blocked_stage == "authorization"
        assert decision.reason_codes == ("authorization_denied",)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("policy_snapshot_current", False, "policy_snapshot_changed"),
        ("policy_kill_switch_engaged", True, "policy_kill_switch_engaged"),
        ("live_trading_enabled", True, "live_trading_enabled"),
        ("operator_kill_switch_engaged", True, "operator_kill_switch_engaged"),
        ("autopilot_paused", True, "autopilot_paused"),
        ("broker_healthy", False, "broker_unhealthy"),
    ],
)
def test_final_safety_failures(field: str, value: object, reason: str) -> None:
    raw = _valid_direct_raw()
    final = raw["final_safety"]
    assert isinstance(final, dict)
    final[field] = value
    check_by_field = {
        "policy_snapshot_current": "policy_version_match",
        "policy_kill_switch_engaged": "kill_switch_not_engaged",
        "live_trading_enabled": "live_trading_disabled",
        "operator_kill_switch_engaged": "operator_kill_switch_not_engaged",
        "autopilot_paused": "operator_not_paused",
        "broker_healthy": "broker_health",
    }
    final["failed_checks"] = [check_by_field[field]]
    _set_phase(raw, "final_safety_failure")
    decision = _decision(raw)
    assert decision.blocked_stage == "final_safety"
    assert decision.reason_codes == (reason,)


def test_multiple_final_failures_are_sorted_and_later_stages_are_ignored() -> None:
    raw = _valid_direct_raw()
    raw["final_safety"].update({
        "policy_kill_switch_engaged": True,
        "live_trading_enabled": True,
        "broker_healthy": False,
        "failed_checks": [
            "kill_switch_not_engaged",
            "live_trading_disabled",
            "broker_health",
        ],
    })
    _set_phase(raw, "final_safety_failure")
    decision = _decision(raw)
    assert decision.blocked_stage == "final_safety"
    assert decision.reason_codes == (
        "broker_unhealthy",
        "live_trading_enabled",
        "policy_kill_switch_engaged",
    )


def test_capability_rejects_market_order_defensively() -> None:
    raw = _valid_direct_raw()
    candidate = raw["candidate"]
    single = raw["single_risk"]
    assert isinstance(candidate, dict)
    assert isinstance(single, dict)
    intent = candidate["intent"]
    assert isinstance(intent, dict)
    intent["order_type"] = "market"
    intent["limit_price"] = None
    single["passed_checks"] = ["order_type_allowed"]
    _set_phase(raw, "capability_failure")
    decision = _decision(raw)
    assert decision.blocked_stage == "capability"
    assert decision.reason_codes == (
        "broker_capability_mismatch",
        "market_order_disabled",
    )


def test_structural_errors_are_aggregated_and_sanitized() -> None:
    two_naive = _valid_direct_raw()
    two_naive["evaluated_at"] = NOW.replace(tzinfo=None)
    candidate = two_naive["candidate"]
    assert isinstance(candidate, dict)
    intent = candidate["intent"]
    assert isinstance(intent, dict)
    intent["quote_time"] = (NOW - timedelta(minutes=4)).replace(tzinfo=None)
    with pytest.raises(_kernel().KernelEvidenceValidationError) as captured:
        _validate(two_naive)
    assert captured.value.args[0] == "naive_or_invalid_timestamp"
    assert captured.value.args[1] == ("candidate.intent.quote_time", "evaluated_at")
    assert captured.value.__cause__ is None

    mixed = deepcopy(two_naive)
    mixed_candidate = mixed["candidate"]
    assert isinstance(mixed_candidate, dict)
    mixed_intent = mixed_candidate["intent"]
    assert isinstance(mixed_intent, dict)
    mixed_intent["quantity"] = "10"
    with pytest.raises(_kernel().KernelEvidenceValidationError) as mixed_captured:
        _validate(mixed)
    assert mixed_captured.value.args[0] == "invalid_evidence_schema"
    text = str(mixed_captured.value)
    assert "10" not in text


def test_extra_secret_field_is_never_echoed() -> None:
    raw = _valid_direct_raw()
    raw["api_key"] = "TOP-SECRET-ATTACKER-VALUE"
    with pytest.raises(_kernel().KernelEvidenceValidationError) as captured:
        _validate(raw)
    assert captured.value.args == ("invalid_evidence_schema", ("$extra",))
    text = str(captured.value)
    assert "api_key" not in text
    assert "TOP-SECRET" not in text


def test_root_timestamp_and_nonnegative_structural_contracts_are_sanitized() -> None:
    with pytest.raises(_kernel().KernelEvidenceValidationError) as root_error:
        _validate(7)
    assert root_error.value.args[0] == "invalid_evidence_schema"
    assert "$" in root_error.value.args[1]

    timestamp = _valid_direct_raw()
    timestamp["evaluated_at"] = "2026-07-13T02:00:00"
    with pytest.raises(_kernel().KernelEvidenceValidationError) as timestamp_error:
        _validate(timestamp)
    assert timestamp_error.value.args == (
        "naive_or_invalid_timestamp",
        ("evaluated_at",),
    )

    negative_fence = _valid_kis_level5_raw()
    negative_fence["paper"]["fencing_token"] = -1
    with pytest.raises(_kernel().KernelEvidenceValidationError) as fence_error:
        _validate(negative_fence)
    assert fence_error.value.args[0] == "invalid_evidence_schema"
    assert "paper.fencing_token" in fence_error.value.args[1]


def test_misplaced_timestamp_key_is_an_extra_field_not_timestamp_evidence() -> None:
    raw = _valid_direct_raw()
    raw["candidate"]["expires_at"] = NOW

    with pytest.raises(_kernel().KernelEvidenceValidationError) as captured:
        _validate(raw)

    assert captured.value.args == ("invalid_evidence_schema", ("$extra",))


def test_blank_descriptive_reason_preserves_legacy_input_parity() -> None:
    raw = _valid_direct_raw()
    raw["candidate"]["intent"]["reason"] = ""

    evidence = _validate(raw)
    decision = _kernel().evaluate_execution(evidence)

    assert evidence.candidate.intent.reason == ""
    assert decision.verdict == "eligible_for_legacy_submit"
    assert decision.blocked_stage == "none"
    assert decision.reason_codes == ()


def test_raw_size_and_decimal_bounds_fail_without_echoing_values() -> None:
    oversized = _valid_direct_raw()
    oversized["candidate"]["intent"]["reason"] = "x" * 65537
    with pytest.raises(_kernel().KernelEvidenceValidationError) as text_error:
        _validate(oversized)
    assert text_error.value.args[0] == "invalid_evidence_schema"
    assert text_error.value.args[1] == ("candidate.intent.reason",)

    nonfinite = _valid_direct_raw()
    nonfinite["candidate"]["intent"]["quantity"] = Decimal("NaN")
    with pytest.raises(_kernel().KernelEvidenceValidationError) as decimal_error:
        _validate(nonfinite)
    assert decimal_error.value.args[0] == "invalid_evidence_schema"
    assert decimal_error.value.args[1] == ("candidate.intent.quantity",)

    oversized_int = _valid_direct_raw()
    oversized_int["context"]["current_policy_version"] = 9223372036854775808
    with pytest.raises(_kernel().KernelEvidenceValidationError) as int_error:
        _validate(oversized_int)
    assert int_error.value.args[0] == "invalid_evidence_schema"
    assert "context.current_policy_version" in int_error.value.args[1]

    oversized_container = _valid_direct_raw()
    oversized_container["single_risk"]["passed_checks"] = ["safe"] * 10001
    with pytest.raises(_kernel().KernelEvidenceValidationError) as container_error:
        _validate(oversized_container)
    assert container_error.value.args[0] == "invalid_evidence_schema"
    assert "single_risk.passed_checks" in container_error.value.args[1]

    deep = _valid_direct_raw()
    nested: dict[str, object] = {"candidate": None}
    root = nested
    for _ in range(65):
        child: dict[str, object] = {"candidate": None}
        root["candidate"] = child
        root = child
    deep["candidate"] = nested
    with pytest.raises(_kernel().KernelEvidenceValidationError) as depth_error:
        _validate(deep)
    assert depth_error.value.args[0] == "invalid_evidence_schema"


class _Exploding:
    calls = 0

    def _explode(self) -> None:
        type(self).calls += 1
        raise AssertionError("hostile object was invoked")

    def __iter__(self):
        self._explode()

    def __getitem__(self, key):
        self._explode()

    def __eq__(self, other):
        self._explode()

    def __str__(self):
        self._explode()

    def __repr__(self):
        self._explode()

    def __format__(self, spec):
        self._explode()

    def __call__(self):
        self._explode()


class _HostileTimezone(tzinfo):
    calls = 0

    def utcoffset(self, dt):
        type(self).calls += 1
        raise AssertionError("hostile tzinfo was invoked")


def test_hostile_raw_objects_cycles_and_custom_timezone_are_total() -> None:
    _Exploding.calls = 0
    raw = _valid_direct_raw()
    raw["candidate"] = _Exploding()
    with pytest.raises(_kernel().KernelEvidenceValidationError):
        _validate(raw)
    assert _Exploding.calls == 0

    cycle: dict[str, object] = {}
    cycle["candidate"] = cycle
    cyclic = _valid_direct_raw()
    cyclic["candidate"] = cycle
    kernel_module = _kernel()
    validator = kernel_module.validate_kernel_input_v1
    validation_error = kernel_module.KernelEvidenceValidationError
    original_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(80)
        with pytest.raises(validation_error):
            validator(cyclic)
    finally:
        sys.setrecursionlimit(original_limit)

    list_cycle: list[object] = []
    list_cycle.append(list_cycle)
    cyclic_list = _valid_direct_raw()
    cyclic_list["candidate"] = list_cycle
    with pytest.raises(validation_error) as captured_cycle:
        validator(cyclic_list)
    assert captured_cycle.value.__cause__ is None

    _HostileTimezone.calls = 0
    hostile_time = _valid_direct_raw()
    hostile_time["evaluated_at"] = datetime(2026, 7, 13, tzinfo=_HostileTimezone())
    with pytest.raises(_kernel().KernelEvidenceValidationError):
        _validate(hostile_time)
    assert _HostileTimezone.calls == 0


@pytest.mark.parametrize("container_kind", ["dict", "list", "tuple"])
def test_deep_acyclic_raw_input_is_total_with_low_recursion_headroom(
    container_kind: str,
) -> None:
    raw = _valid_direct_raw()
    secret = "must-not-escape-low-recursion-copy"
    if container_kind == "dict":
        nested: object = secret
        for _ in range(70):
            nested = {"candidate": nested}
    elif container_kind == "list":
        nested = secret
        for _ in range(70):
            nested = [nested]
    else:
        nested = secret
        for _ in range(70):
            nested = (nested,)
    raw["candidate"] = nested
    kernel_module = _kernel()
    validator = kernel_module.validate_kernel_input_v1
    validation_error = kernel_module.KernelEvidenceValidationError
    captured = None
    original_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(65)
        try:
            validator(raw)
        except validation_error as error:
            captured = error
    finally:
        sys.setrecursionlimit(original_limit)

    assert captured is not None
    assert captured.args[0] == "invalid_evidence_schema"
    assert captured.__cause__ is None
    assert secret not in str(captured)


def _rewrite_datetimes(value: object, offset: timezone) -> object:
    if type(value) is dict:
        return {key: _rewrite_datetimes(item, offset) for key, item in value.items()}
    if type(value) is list:
        return [_rewrite_datetimes(item, offset) for item in value]
    if type(value) is tuple:
        return tuple(_rewrite_datetimes(item, offset) for item in value)
    if type(value) is datetime:
        return value.astimezone(offset)
    return value


def test_fingerprint_is_canonical_and_detached_from_source() -> None:
    first_raw = _valid_direct_raw()
    candidate = first_raw["candidate"]
    assert isinstance(candidate, dict)
    intent = candidate["intent"]
    assert isinstance(intent, dict)
    intent["quantity"] = Decimal("10.00")
    intent["target_weight"] = Decimal("-0")
    first_evidence = _validate(first_raw)
    first = _kernel().evaluate_execution(first_evidence)

    second_raw = _rewrite_datetimes(deepcopy(first_raw), timezone(timedelta(hours=9)))
    assert isinstance(second_raw, dict)
    second_raw = dict(reversed(tuple(second_raw.items())))
    second_candidate = second_raw["candidate"]
    assert isinstance(second_candidate, dict)
    second_intent = second_candidate["intent"]
    assert isinstance(second_intent, dict)
    second_intent["quantity"] = Decimal("1E+1")
    second_intent["target_weight"] = Decimal("0.000")
    second = _decision(second_raw)
    assert first.evidence_fingerprint == second.evidence_fingerprint

    first_raw["schema_version"] = 999
    assert _kernel().evaluate_execution(first_evidence) == first


def test_nested_source_mutation_cannot_change_validated_evidence_or_decision() -> None:
    raw = _valid_direct_raw()
    evidence = _validate(raw)
    baseline = _kernel().evaluate_execution(evidence)

    raw["authorization"]["checks"][0]["passed"] = False
    raw["single_risk"]["passed_checks"].append("source-mutated")
    raw["batch_risk"]["accepted_order_plan_ids"].append("different-order")
    raw["candidate"]["intent"]["symbol"] = "999999"

    repeated = _kernel().evaluate_execution(evidence)
    assert repeated == baseline
    assert repeated.evidence_fingerprint == baseline.evidence_fingerprint
    assert evidence.authorization.checks[0].passed is True
    assert "source-mutated" not in evidence.single_risk.passed_checks
    assert "different-order" not in evidence.batch_risk.accepted_order_plan_ids
    assert evidence.candidate.intent.symbol == "005930"


def _contains_callable(value: object) -> bool:
    if callable(value):
        return True
    if isinstance(value, _kernel().FrozenKernelModel):
        return _contains_callable(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return any(_contains_callable(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_callable(item) for item in value)
    return False


def test_decision_contains_no_command_or_callable() -> None:
    decision = _decision(_valid_direct_raw())
    assert not _contains_callable(decision)
    assert not hasattr(decision, "submit")
    assert not hasattr(decision, "execute")


def test_decimal_context_is_not_observed_or_mutated() -> None:
    original_precision = getcontext().prec
    try:
        getcontext().prec = 3
        low_precision = _decision(_valid_direct_raw())
        getcontext().prec = 50
        high_precision = _decision(_valid_direct_raw())
    finally:
        getcontext().prec = original_precision
    assert low_precision == high_precision


def _model_types(annotation: object) -> tuple[object, ...]:
    origin = get_origin(annotation)
    if origin is None:
        return (annotation,)
    nested: tuple[object, ...] = ()
    for argument in get_args(annotation):
        nested += _model_types(argument)
    return (origin,) + nested


def test_model_tree_is_frozen_closed_and_has_no_mutable_collection_types() -> None:
    module = importlib.import_module("quantpilot.packages.core.execution.kernel")
    model_classes = [
        value
        for value in vars(module).values()
        if inspect.isclass(value)
        and issubclass(value, module.FrozenKernelModel)
    ]
    assert model_classes
    for model_class in model_classes:
        assert model_class.model_config["frozen"] is True
        assert model_class.model_config["extra"] == "forbid"
        assert model_class.model_config["strict"] is True
        assert model_class.model_config["revalidate_instances"] == "always"
        for annotation in get_type_hints(model_class).values():
            types = _model_types(annotation)
            assert list not in types
            assert dict not in types
            assert set not in types
            assert object not in types


ALLOWED_IMPORTS = {
    ("from", "datetime", ("datetime", "timezone")),
    ("from", "decimal", ("Decimal",)),
    ("import", "hashlib", ()),
    ("import", "json", ()),
    ("from", "typing", ("Literal",)),
    ("from", "pydantic", ("BaseModel", "ConfigDict", "ValidationError")),
}


def _immutable_constant(node: ast.AST, prior_constants: set[str] | None = None) -> bool:
    known = prior_constants or set()
    if isinstance(node, ast.Constant):
        return node.value is None or type(node.value) in {bool, int, str, bytes}
    if isinstance(node, ast.Tuple):
        return all(_immutable_constant(item, known) for item in node.elts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return isinstance(node.operand, ast.Constant) and type(node.operand.value) is int
    if isinstance(node, ast.Name):
        return node.id in known
    return False


def _type_expression_valid(node: ast.AST | None, class_names: set[str]) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Constant):
        return node.value is None or node.value is Ellipsis or type(node.value) in {str, int, bool}
    if isinstance(node, ast.Name):
        return node.id in {"str", "int", "bool", "bytes", "tuple", "datetime", "timezone", "Decimal", "Literal"} | class_names
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _type_expression_valid(node.left, class_names) and _type_expression_valid(node.right, class_names)
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        if node.value.id not in {"tuple", "Literal"}:
            return False
        if isinstance(node.slice, ast.Tuple):
            return all(_type_expression_valid(item, class_names) for item in node.slice.elts)
        return _type_expression_valid(node.slice, class_names)
    return False


def _call_errors(
    node: ast.Call,
    module_functions: set[str],
    module_classes: set[str],
) -> list[str]:
    errors: list[str] = []
    if any(isinstance(argument, ast.Starred) for argument in node.args):
        errors.append("star_call")
    if any(keyword.arg is None for keyword in node.keywords):
        errors.append("starstar_call")
    safe_builtins = {
        "all", "any", "enumerate", "isinstance", "issubclass", "len", "max",
        "min", "range", "sorted", "str", "int", "bool", "bytes", "tuple",
        "type", "zip",
    }
    if isinstance(node.func, ast.Name):
        name = node.func.id
        if name not in safe_builtins | module_functions | module_classes | {"ConfigDict"}:
            errors.append("indirect_or_unknown_call")
        if name in {"all", "any", "len", "tuple", "type"} and (len(node.args) != 1 or node.keywords):
            errors.append("builtin_signature")
        if name == "enumerate" and (len(node.args) not in {1, 2} or node.keywords):
            errors.append("builtin_signature")
        if name == "range" and (len(node.args) not in {1, 2, 3} or node.keywords):
            errors.append("builtin_signature")
        if name == "zip" and node.keywords:
            errors.append("builtin_signature")
        if name == "sorted" and (len(node.args) != 1 or node.keywords):
            errors.append("sorted_signature")
        if name in {"min", "max"} and (not node.args or node.keywords):
            errors.append("builtin_signature")
        if name in {"str", "int", "bool", "bytes"} and (len(node.args) > 1 or node.keywords):
            errors.append("builtin_signature")
        if name in {"isinstance", "issubclass"} and (len(node.args) != 2 or node.keywords):
            errors.append("builtin_signature")
    elif isinstance(node.func, ast.Attribute):
        attribute = node.func.attr
        allowed_methods = {
            "strip", "split", "isdigit", "encode", "replace", "join", "items",
            "as_tuple", "is_finite", "utcoffset", "astimezone", "isoformat",
            "model_validate", "model_dump", "errors", "hexdigest", "dumps", "sha256",
        }
        if attribute not in allowed_methods:
            errors.append("unknown_method")
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "json":
            keyword_names = {keyword.arg for keyword in node.keywords}
            if attribute != "dumps" or len(node.args) != 1 or keyword_names != {"ensure_ascii", "allow_nan", "sort_keys", "separators"}:
                errors.append("json_signature")
            else:
                keyword_values = {keyword.arg: keyword.value for keyword in node.keywords}
                separators = keyword_values["separators"]
                constants_valid = isinstance(keyword_values["ensure_ascii"], ast.Constant) and keyword_values["ensure_ascii"].value is False and isinstance(keyword_values["allow_nan"], ast.Constant) and keyword_values["allow_nan"].value is False and isinstance(keyword_values["sort_keys"], ast.Constant) and keyword_values["sort_keys"].value is True and isinstance(separators, ast.Tuple) and len(separators.elts) == 2 and all(isinstance(item, ast.Constant) for item in separators.elts) and tuple(item.value for item in separators.elts) == (",", ":")
                if not constants_valid:
                    errors.append("json_constants")
        elif isinstance(node.func.value, ast.Name) and node.func.value.id == "hashlib":
            if attribute != "sha256" or len(node.args) != 1 or node.keywords:
                errors.append("hash_signature")
        elif attribute in {"strip", "isdigit", "items", "as_tuple", "is_finite", "utcoffset", "hexdigest"}:
            if node.args or node.keywords:
                errors.append("method_signature")
        elif attribute == "split":
            if len(node.args) != 1 or node.keywords or not isinstance(node.args[0], ast.Constant) or node.args[0].value != ".":
                errors.append("method_signature")
        elif attribute == "encode":
            if len(node.args) != 1 or node.keywords or not isinstance(node.args[0], ast.Constant) or node.args[0].value != "utf-8":
                errors.append("method_signature")
        elif attribute == "replace":
            if len(node.args) != 2 or node.keywords or not all(isinstance(argument, ast.Constant) for argument in node.args) or tuple(argument.value for argument in node.args) != ("+00:00", "Z"):
                errors.append("method_signature")
        elif attribute == "join":
            if len(node.args) != 1 or node.keywords:
                errors.append("method_signature")
        elif attribute == "astimezone":
            valid_timezone = len(node.args) == 1 and isinstance(node.args[0], ast.Attribute) and isinstance(node.args[0].value, ast.Name) and node.args[0].value.id == "timezone" and node.args[0].attr == "utc"
            if not valid_timezone or node.keywords:
                errors.append("method_signature")
        elif attribute == "isoformat":
            keyword_values = {keyword.arg: keyword.value for keyword in node.keywords}
            if node.args or set(keyword_values) != {"timespec"} or not isinstance(keyword_values["timespec"], ast.Constant) or keyword_values["timespec"].value != "microseconds":
                errors.append("method_signature")
        elif attribute == "model_validate":
            if len(node.args) != 1 or node.keywords:
                errors.append("method_signature")
        elif attribute == "model_dump":
            keyword_values = {keyword.arg: keyword.value for keyword in node.keywords}
            if node.args or set(keyword_values) != {"mode"} or not isinstance(keyword_values["mode"], ast.Constant) or keyword_values["mode"].value != "python":
                errors.append("method_signature")
        elif attribute == "errors":
            keyword_values = {keyword.arg: keyword.value for keyword in node.keywords}
            if node.args or set(keyword_values) != {"include_url", "include_context", "include_input"} or not all(isinstance(value, ast.Constant) and value.value is False for value in keyword_values.values()):
                errors.append("method_signature")
    else:
        errors.append("dynamic_call")
    return errors


def _annotation_contains(annotation: ast.AST | None, name: str) -> bool:
    if annotation is None:
        return False
    return any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(annotation))


def _guarded_types(test: ast.AST) -> set[str]:
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and len(test.comparators) == 1
    ):
        comparator = test.comparators[0]
        if (
            isinstance(test.left, ast.Name)
            and test.left.id == "value_type"
            and isinstance(comparator, ast.Name)
        ):
            return {comparator.id}
        if (
            isinstance(test.left, ast.Name)
            and test.left.id == "value"
            and isinstance(comparator, ast.Constant)
            and comparator.value is None
        ):
            return {"None"}
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        guarded: set[str] = set()
        for value in test.values:
            value_types = _guarded_types(value)
            if not value_types:
                return set()
            guarded.update(value_types)
        return guarded
    return set()


def _dominated_by_raw_type(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    allowed_types: set[str],
) -> bool:
    current = node
    while current in parents:
        child = current
        current = parents[current]
        if isinstance(current, ast.If) and child in current.body:
            guarded_types = _guarded_types(current.test)
            if guarded_types and guarded_types <= allowed_types:
                return True
    return False


def _dominating_raw_types(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> set[str]:
    current = node
    while current in parents:
        child = current
        current = parents[current]
        if isinstance(current, ast.If) and child in current.body:
            guarded_types = _guarded_types(current.test)
            if guarded_types:
                return guarded_types
    return set()


def _dominated_by_local_exact_type(
    node: ast.AST,
    name: str,
    parents: dict[ast.AST, ast.AST],
    type_alias_sources: dict[str, str],
    allowed_types: set[str],
) -> bool:
    current = node
    while current in parents:
        child = current
        current = parents[current]
        if not isinstance(current, ast.If):
            continue
        test = current.test
        if (
            not isinstance(test, ast.Compare)
            or not isinstance(test.left, ast.Name)
            or type_alias_sources.get(test.left.id) != name
            or len(test.ops) != 1
            or len(test.comparators) != 1
            or not isinstance(test.comparators[0], ast.Name)
            or test.comparators[0].id not in allowed_types
        ):
            continue
        if isinstance(test.ops[0], ast.Is) and child in current.body:
            return True
        if isinstance(test.ops[0], ast.IsNot) and child in current.orelse:
            return True
    return False


def _inside_return(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Return):
            return True
    return False


def _dominated_by_known_raw_key(
    node: ast.AST,
    name: str,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = node
    while current in parents:
        child = current
        current = parents[current]
        if not isinstance(current, ast.If):
            continue
        test = current.test
        if (
            not isinstance(test, ast.Compare)
            or not isinstance(test.left, ast.Name)
            or test.left.id != name
            or len(test.ops) != 1
            or len(test.comparators) != 1
            or not isinstance(test.comparators[0], ast.Name)
            or test.comparators[0].id != "KNOWN_RAW_KEYS"
        ):
            continue
        if isinstance(test.ops[0], ast.In) and child in current.body:
            return True
        if isinstance(test.ops[0], ast.NotIn) and child in current.orelse:
            return True
    return False


def _annotation_provenance(
    annotation: ast.AST | None,
    model_classes: set[str],
) -> tuple[set[str], set[str]]:
    if annotation is None:
        return set(), set()
    if isinstance(annotation, ast.Name):
        if annotation.id in model_classes:
            return {annotation.id}, set()
        return set(), set()
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left_models, left_tuple_models = _annotation_provenance(
            annotation.left,
            model_classes,
        )
        right_models, right_tuple_models = _annotation_provenance(
            annotation.right,
            model_classes,
        )
        return left_models | right_models, left_tuple_models | right_tuple_models
    if (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "tuple"
    ):
        _, tuple_models = _annotation_provenance(annotation.slice, model_classes)
        direct_models, _ = _annotation_provenance(annotation.slice, model_classes)
        return set(), direct_models | tuple_models
    if isinstance(annotation, ast.Tuple):
        models: set[str] = set()
        tuple_models: set[str] = set()
        for item in annotation.elts:
            item_models, item_tuple_models = _annotation_provenance(
                item,
                model_classes,
            )
            models.update(item_models)
            tuple_models.update(item_tuple_models)
        return models, tuple_models
    return set(), set()


def _annotation_scalar_provenance(
    annotation: ast.AST | None,
) -> tuple[set[str], set[str]]:
    scalar_names = {
        "str", "int", "bool", "bytes", "tuple", "Decimal", "datetime",
        "ValidationError",
    }
    if annotation is None:
        return set(), set()
    if isinstance(annotation, ast.Name):
        if annotation.id in scalar_names:
            return {annotation.id}, set()
        return set(), set()
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return {"None"}, set()
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left, left_items = _annotation_scalar_provenance(annotation.left)
        right, right_items = _annotation_scalar_provenance(annotation.right)
        return left | right, left_items | right_items
    if (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
    ):
        if annotation.value.id == "tuple":
            direct, nested_items = _annotation_scalar_provenance(annotation.slice)
            return {"tuple"}, direct | nested_items
        if annotation.value.id == "Literal":
            literal_nodes = (
                annotation.slice.elts
                if isinstance(annotation.slice, ast.Tuple)
                else (annotation.slice,)
            )
            literal_types: set[str] = set()
            for literal_node in literal_nodes:
                if isinstance(literal_node, ast.Constant):
                    literal_type = type(literal_node.value)
                    if literal_type is str:
                        literal_types.add("str")
                    elif literal_type is int:
                        literal_types.add("int")
                    elif literal_type is bool:
                        literal_types.add("bool")
                    elif literal_type is bytes:
                        literal_types.add("bytes")
            return literal_types, set()
    if isinstance(annotation, ast.Tuple):
        direct: set[str] = set()
        items: set[str] = set()
        for item in annotation.elts:
            item_direct, item_items = _annotation_scalar_provenance(item)
            direct.update(item_direct)
            items.update(item_items)
        return direct, items
    return set(), set()


def _expression_model_provenance(
    expression: ast.AST,
    model_variables: dict[str, set[str]],
    tuple_model_variables: dict[str, set[str]],
    model_fields: dict[str, dict[str, ast.AST]],
    model_classes: set[str],
    function_returns: dict[str, ast.AST | None] | None = None,
) -> tuple[set[str], set[str]]:
    if isinstance(expression, ast.Name):
        return (
            set(model_variables.get(expression.id, set())),
            set(tuple_model_variables.get(expression.id, set())),
        )
    if isinstance(expression, ast.Attribute):
        receiver_models, _ = _expression_model_provenance(
            expression.value,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        result_models: set[str] = set()
        result_tuple_models: set[str] = set()
        for receiver_model in receiver_models:
            annotation = model_fields.get(receiver_model, {}).get(expression.attr)
            annotation_models, annotation_tuple_models = _annotation_provenance(
                annotation,
                model_classes,
            )
            result_models.update(annotation_models)
            result_tuple_models.update(annotation_tuple_models)
        return result_models, result_tuple_models
    if isinstance(expression, ast.Subscript):
        _, tuple_models = _expression_model_provenance(
            expression.value,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        return tuple_models, set()
    if isinstance(expression, ast.Call):
        if isinstance(expression.func, ast.Name) and expression.func.id in model_classes:
            return {expression.func.id}, set()
        if (
            isinstance(expression.func, ast.Attribute)
            and expression.func.attr == "model_validate"
            and isinstance(expression.func.value, ast.Name)
            and expression.func.value.id in model_classes
        ):
            return {expression.func.value.id}, set()
        if (
            isinstance(expression.func, ast.Name)
            and function_returns is not None
            and expression.func.id in function_returns
        ):
            return _annotation_provenance(
                function_returns[expression.func.id],
                model_classes,
            )
        return set(), set()
    if isinstance(expression, ast.IfExp):
        body_models, body_tuple_models = _expression_model_provenance(
            expression.body,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        else_models, else_tuple_models = _expression_model_provenance(
            expression.orelse,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        return body_models | else_models, body_tuple_models | else_tuple_models
    if isinstance(expression, ast.Tuple):
        item_models: set[str] = set()
        for item in expression.elts:
            direct_models, nested_models = _expression_model_provenance(
                item,
                model_variables,
                tuple_model_variables,
                model_fields,
                model_classes,
                function_returns,
            )
            item_models.update(direct_models)
            item_models.update(nested_models)
        return set(), item_models
    if isinstance(expression, ast.BoolOp):
        result_models: set[str] = set()
        result_tuple_models: set[str] = set()
        for value in expression.values:
            value_models, value_tuple_models = _expression_model_provenance(
                value,
                model_variables,
                tuple_model_variables,
                model_fields,
                model_classes,
                function_returns,
            )
            result_models.update(value_models)
            result_tuple_models.update(value_tuple_models)
        return result_models, result_tuple_models
    return set(), set()


def _expression_scalar_provenance(
    expression: ast.AST,
    scalar_variables: dict[str, set[str]],
    tuple_scalar_variables: dict[str, set[str]],
    model_variables: dict[str, set[str]],
    tuple_model_variables: dict[str, set[str]],
    model_fields: dict[str, dict[str, ast.AST]],
    model_classes: set[str],
    function_returns: dict[str, ast.AST | None],
) -> tuple[set[str], set[str]]:
    if isinstance(expression, ast.Name):
        return (
            set(scalar_variables.get(expression.id, set())),
            set(tuple_scalar_variables.get(expression.id, set())),
        )
    if isinstance(expression, ast.Constant):
        value_type = type(expression.value)
        if value_type is str:
            return {"str"}, set()
        if value_type is int:
            return {"int"}, set()
        if value_type is bool:
            return {"bool"}, set()
        if value_type is bytes:
            return {"bytes"}, set()
        if expression.value is None:
            return {"None"}, set()
        return set(), set()
    if isinstance(expression, ast.Compare):
        return {"bool"}, set()
    if isinstance(expression, ast.BoolOp):
        result_types: set[str] = set()
        result_items: set[str] = set()
        for value in expression.values:
            value_types, value_items = _expression_scalar_provenance(
                value,
                scalar_variables,
                tuple_scalar_variables,
                model_variables,
                tuple_model_variables,
                model_fields,
                model_classes,
                function_returns,
            )
            result_types.update(value_types)
            result_items.update(value_items)
        return result_types, result_items
    if isinstance(expression, ast.UnaryOp):
        if isinstance(expression.op, ast.Not):
            return {"bool"}, set()
        operand_types, _ = _expression_scalar_provenance(
            expression.operand,
            scalar_variables,
            tuple_scalar_variables,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        if "int" in operand_types:
            return {"int"}, set()
        return set(), set()
    if isinstance(expression, ast.BinOp):
        left_types, left_items = _expression_scalar_provenance(
            expression.left,
            scalar_variables,
            tuple_scalar_variables,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        right_types, right_items = _expression_scalar_provenance(
            expression.right,
            scalar_variables,
            tuple_scalar_variables,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        if isinstance(expression.op, ast.Add):
            if left_types == {"str"} and right_types == {"str"}:
                return {"str"}, set()
            if left_types == {"tuple"} and right_types == {"tuple"}:
                return {"tuple"}, left_items | right_items
            if left_types == {"int"} and right_types == {"int"}:
                return {"int"}, set()
            if left_types == {"bytes"} and right_types == {"bytes"}:
                return {"bytes"}, set()
        if (
            isinstance(expression.op, ast.Sub)
            and left_types == {"int"}
            and right_types == {"int"}
        ):
            return {"int"}, set()
        if isinstance(expression.op, ast.Mult):
            if left_types == {"int"} and right_types == {"int"}:
                return {"int"}, set()
            if (
                (left_types == {"str"} and right_types == {"int"})
                or (left_types == {"int"} and right_types == {"str"})
            ):
                return {"str"}, set()
            if (
                (left_types == {"tuple"} and right_types == {"int"})
                or (left_types == {"int"} and right_types == {"tuple"})
            ):
                return {"tuple"}, left_items | right_items
        return set(), set()
    if isinstance(expression, ast.Attribute):
        receiver_models, _ = _expression_model_provenance(
            expression.value,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        field_types: set[str] = set()
        field_item_types: set[str] = set()
        for receiver_model in receiver_models:
            annotation = model_fields.get(receiver_model, {}).get(expression.attr)
            direct, items = _annotation_scalar_provenance(annotation)
            field_types.update(direct)
            field_item_types.update(items)
        if field_types or field_item_types:
            return field_types, field_item_types
        receiver_types, _ = _expression_scalar_provenance(
            expression.value,
            scalar_variables,
            tuple_scalar_variables,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        if "decimal_tuple" in receiver_types:
            if expression.attr == "digits":
                return {"tuple"}, {"int"}
            if expression.attr in {"exponent", "sign"}:
                return {"int"}, set()
        return set(), set()
    if isinstance(expression, ast.Subscript):
        direct_types, item_types = _expression_scalar_provenance(
            expression.value,
            scalar_variables,
            tuple_scalar_variables,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        if "str" in direct_types:
            return {"str"}, set()
        if "bytes" in direct_types:
            return {"int"}, set()
        if "validation_rows" in direct_types:
            return {"dict"}, set()
        if (
            "validation_error_row" in direct_types
            and isinstance(expression.slice, ast.Constant)
        ):
            if expression.slice.value == "loc":
                return {"tuple"}, {"str", "int"}
            if expression.slice.value == "type":
                return {"str"}, set()
        return item_types, set()
    if isinstance(expression, ast.Tuple):
        item_types: set[str] = set()
        for item in expression.elts:
            direct, nested = _expression_scalar_provenance(
                item,
                scalar_variables,
                tuple_scalar_variables,
                model_variables,
                tuple_model_variables,
                model_fields,
                model_classes,
                function_returns,
            )
            item_types.update(direct)
            item_types.update(nested)
        return {"tuple"}, item_types
    if isinstance(expression, ast.Dict):
        return {"dict"}, set()
    if isinstance(expression, ast.IfExp):
        body, body_items = _expression_scalar_provenance(
            expression.body,
            scalar_variables,
            tuple_scalar_variables,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        other, other_items = _expression_scalar_provenance(
            expression.orelse,
            scalar_variables,
            tuple_scalar_variables,
            model_variables,
            tuple_model_variables,
            model_fields,
            model_classes,
            function_returns,
        )
        return body | other, body_items | other_items
    if not isinstance(expression, ast.Call):
        return set(), set()
    if isinstance(expression.func, ast.Name):
        if expression.func.id in {"str", "int", "bool", "bytes"}:
            return {expression.func.id}, set()
        if expression.func.id == "tuple":
            item_types: set[str] = set()
            if expression.args:
                direct, nested = _expression_scalar_provenance(
                    expression.args[0],
                    scalar_variables,
                    tuple_scalar_variables,
                    model_variables,
                    tuple_model_variables,
                    model_fields,
                    model_classes,
                    function_returns,
                )
                item_types.update(nested)
                if "sequence_str" in direct:
                    item_types.add("str")
            return {"tuple"}, item_types
        if expression.func.id == "type":
            return {"type"}, set()
        if expression.func.id in {"all", "any", "isinstance", "issubclass"}:
            return {"bool"}, set()
        if expression.func.id == "len":
            return {"int"}, set()
        if expression.func.id == "_canonical_value":
            return {"canonical"}, set()
        if expression.func.id in function_returns:
            return _annotation_scalar_provenance(
                function_returns[expression.func.id]
            )
        return set(), set()
    if not isinstance(expression.func, ast.Attribute):
        return set(), set()
    if isinstance(expression.func.value, ast.Name):
        if expression.func.value.id == "json" and expression.func.attr == "dumps":
            return {"str"}, set()
        if expression.func.value.id == "hashlib" and expression.func.attr == "sha256":
            return {"hash"}, set()
    receiver_types, receiver_items = _expression_scalar_provenance(
        expression.func.value,
        scalar_variables,
        tuple_scalar_variables,
        model_variables,
        tuple_model_variables,
        model_fields,
        model_classes,
        function_returns,
    )
    method = expression.func.attr
    if method in {"strip", "replace", "join"}:
        return {"str"}, set()
    if method == "split":
        return {"sequence_str"}, {"str"}
    if method == "encode":
        return {"bytes"}, set()
    if method == "as_tuple":
        return {"decimal_tuple"}, set()
    if method == "astimezone":
        return {"datetime"}, set()
    if method == "isoformat":
        return {"str"}, set()
    if method == "hexdigest":
        return {"str"}, set()
    if method == "model_dump":
        return {"dict"}, set()
    if method == "errors":
        return {"validation_rows"}, {"validation_error_row"}
    if method == "items" and "dict" in receiver_types:
        return {"items"}, receiver_items
    return set(), set()


def _bare_untyped_truthiness(expression: ast.AST, untyped_names: set[str]) -> bool:
    if isinstance(expression, ast.Name):
        return expression.id in untyped_names
    if isinstance(expression, ast.BoolOp):
        return any(
            _bare_untyped_truthiness(value, untyped_names)
            for value in expression.values
        )
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        return _bare_untyped_truthiness(expression.operand, untyped_names)
    if isinstance(expression, ast.IfExp):
        return (
            _bare_untyped_truthiness(expression.body, untyped_names)
            or _bare_untyped_truthiness(expression.orelse, untyped_names)
        )
    return False


def _target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Tuple):
        names: set[str] = set()
        for item in target.elts:
            names.update(_target_names(item))
        return names
    return set()


def _same_expression(left: ast.AST, right: ast.AST) -> bool:
    return ast.dump(left, include_attributes=False) == ast.dump(
        right,
        include_attributes=False,
    )


def _none_expression_comparison(
    test: ast.AST,
    expression: ast.AST,
    *,
    is_not: bool,
) -> bool:
    return (
        isinstance(test, ast.Compare)
        and _same_expression(test.left, expression)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.IsNot if is_not else ast.Is)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    )


def _test_includes_none_expression_case(
    test: ast.AST,
    expression: ast.AST,
    *,
    is_not: bool,
) -> bool:
    if _none_expression_comparison(test, expression, is_not=is_not):
        return True
    expected_operator = ast.And if is_not else ast.Or
    return isinstance(test, ast.BoolOp) and isinstance(test.op, expected_operator) and any(
        _test_includes_none_expression_case(
            value,
            expression,
            is_not=is_not,
        )
        for value in test.values
    )


def _expression_none_is_excluded(
    node: ast.AST,
    expression: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = node
    while current in parents:
        child = current
        current = parents[current]
        if isinstance(current, ast.If):
            if child in current.body and _test_includes_none_expression_case(
                current.test,
                expression,
                is_not=True,
            ):
                return True
            if child in current.orelse and _test_includes_none_expression_case(
                current.test,
                expression,
                is_not=False,
            ):
                return True
        if isinstance(current, ast.BoolOp) and child in current.values:
            child_index = current.values.index(child)
            preceding = current.values[:child_index]
            if isinstance(current.op, ast.Or) and any(
                _test_includes_none_expression_case(
                    value,
                    expression,
                    is_not=False,
                )
                for value in preceding
            ):
                return True
            if isinstance(current.op, ast.And) and any(
                _test_includes_none_expression_case(
                    value,
                    expression,
                    is_not=True,
                )
                for value in preceding
            ):
                return True
        body = getattr(current, "body", None)
        if type(body) is list and child in body:
            child_index = body.index(child)
            for preceding in body[:child_index]:
                if (
                    isinstance(preceding, ast.If)
                    and _test_includes_none_expression_case(
                        preceding.test,
                        expression,
                        is_not=False,
                    )
                    and len(preceding.body) == 1
                    and isinstance(preceding.body[0], ast.Return)
                ):
                    return True
    return False


def _none_is_excluded(
    node: ast.AST,
    name: str,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    return _expression_none_is_excluded(
        node,
        ast.Name(id=name, ctx=ast.Load()),
        parents,
    )


def _copy_recursion_try_valid(node: ast.Try) -> bool:
    if node.orelse or node.finalbody or len(node.handlers) != 1:
        return False
    handler = node.handlers[0]
    if (
        not isinstance(handler.type, ast.Name)
        or handler.type.id != "RecursionError"
        or handler.name is not None
    ):
        return False
    if len(node.body) != 1 or not isinstance(node.body[0], ast.Assign):
        return False
    assignment = node.body[0]
    if (
        len(assignment.targets) != 1
        or not isinstance(assignment.targets[0], ast.Tuple)
        or tuple(
            item.id if isinstance(item, ast.Name) else None
            for item in assignment.targets[0].elts
        ) != ("copied_item", "remaining_budget", "child_findings")
        or not isinstance(assignment.value, ast.Call)
        or not isinstance(assignment.value.func, ast.Name)
        or assignment.value.func.id != "_copy_raw_value"
        or len(assignment.value.args) != 6
        or assignment.value.keywords
    ):
        return False
    actual_arguments = tuple(
        ast.dump(argument, include_attributes=False)
        for argument in assignment.value.args
    )
    dict_arguments = tuple(
        ast.dump(
            ast.parse(expression, mode="eval").body,
            include_attributes=False,
        )
        for expression in (
            "raw_item",
            "child_path",
            "raw_key",
            "depth + 1",
            "remaining_budget",
            "child_ancestors",
        )
    )
    sequence_arguments = tuple(
        ast.dump(
            ast.parse(expression, mode="eval").body,
            include_attributes=False,
        )
        for expression in (
            "raw_item",
            'path + "[]"',
            "field_name",
            "depth + 1",
            "remaining_budget",
            "child_ancestors",
        )
    )
    if actual_arguments == dict_arguments:
        argument_variant = "dict"
    elif actual_arguments == sequence_arguments:
        argument_variant = "sequence"
    else:
        return False
    if len(handler.body) != 3 or not all(
        isinstance(statement, ast.Assign) for statement in handler.body
    ):
        return False
    copied_assignment, budget_assignment, findings_assignment = handler.body
    expected_targets = ("copied_item", "remaining_budget", "child_findings")
    actual_targets = []
    for statement in handler.body:
        if (
            len(statement.targets) != 1
            or not isinstance(statement.targets[0], ast.Name)
        ):
            return False
        actual_targets.append(statement.targets[0].id)
    if tuple(actual_targets) != expected_targets:
        return False
    if not isinstance(copied_assignment.value, ast.Constant) or copied_assignment.value.value is not None:
        return False
    if not isinstance(budget_assignment.value, ast.Constant) or budget_assignment.value.value != 0:
        return False
    finding_value = findings_assignment.value
    if not (
        isinstance(finding_value, ast.Tuple)
        and len(finding_value.elts) == 1
        and isinstance(finding_value.elts[0], ast.Tuple)
        and len(finding_value.elts[0].elts) == 2
        and isinstance(finding_value.elts[0].elts[0], ast.Constant)
        and finding_value.elts[0].elts[0].value == "schema"
    ):
        return False
    path_expression = finding_value.elts[0].elts[1]
    dict_path = (
        isinstance(path_expression, ast.Name)
        and path_expression.id == "child_path"
    )
    sequence_path = (
        isinstance(path_expression, ast.BinOp)
        and isinstance(path_expression.op, ast.Add)
        and isinstance(path_expression.left, ast.Name)
        and path_expression.left.id == "path"
        and isinstance(path_expression.right, ast.Constant)
        and path_expression.right.value == "[]"
    )
    return (argument_variant == "dict" and dict_path) or (
        argument_variant == "sequence" and sequence_path
    )


def _copy_recursion_try_variant(node: ast.Try) -> str | None:
    if not _copy_recursion_try_valid(node):
        return None
    assignment = node.body[0]
    assert isinstance(assignment, ast.Assign)
    assert isinstance(assignment.value, ast.Call)
    path_argument = assignment.value.args[1]
    if isinstance(path_argument, ast.Name) and path_argument.id == "child_path":
        return "dict"
    return "sequence"


def _copy_recursion_loop_variant(
    node: ast.Try,
    parents: dict[ast.AST, ast.AST],
) -> str | None:
    current: ast.AST = node
    containing_loop = None
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.For):
            containing_loop = current
            break
    if containing_loop is None:
        return None
    target = containing_loop.target
    iterable = containing_loop.iter
    if (
        isinstance(target, ast.Tuple)
        and tuple(
            item.id if isinstance(item, ast.Name) else None
            for item in target.elts
        ) == ("raw_key", "raw_item")
        and isinstance(iterable, ast.Call)
        and isinstance(iterable.func, ast.Attribute)
        and isinstance(iterable.func.value, ast.Name)
        and iterable.func.value.id == "value"
        and iterable.func.attr == "items"
        and not iterable.args
        and not iterable.keywords
    ):
        return "dict"
    if (
        isinstance(target, ast.Name)
        and target.id == "raw_item"
        and isinstance(iterable, ast.Name)
        and iterable.id == "value"
    ):
        return "sequence"
    return None


def _kernel_decision_constructor_valid(
    node: ast.Call,
    function_name: str,
) -> bool:
    if (
        not isinstance(node.func, ast.Name)
        or node.func.id != "KernelDecisionV1"
        or node.args
        or any(keyword.arg is None for keyword in node.keywords)
    ):
        return False
    if function_name == "_blocked_decision":
        expected = {
            "schema_version": "1",
            "order_plan_id": "evidence.candidate.order_plan_id",
            "verdict": '"blocked"',
            "blocked_stage": "stage",
            "reason_codes": "_sorted_unique(reasons)",
            "durable_prepare_requirement": "durable_requirement",
            "atomic_reservation_requirement": "reservation_requirement",
            "intended_next_stage": '"none"',
            "evaluated_at": "evidence.evaluated_at",
            "evidence_fingerprint": "fingerprint",
        }
    elif function_name == "evaluate_execution":
        expected = {
            "schema_version": "1",
            "order_plan_id": "evidence.candidate.order_plan_id",
            "verdict": '"eligible_for_legacy_submit"',
            "blocked_stage": '"none"',
            "reason_codes": "()",
            "durable_prepare_requirement": "durable_requirement",
            "atomic_reservation_requirement": "reservation_requirement",
            "intended_next_stage": '"legacy_submit_handoff"',
            "evaluated_at": "evidence.evaluated_at",
            "evidence_fingerprint": "fingerprint",
        }
    else:
        return False
    actual = {
        keyword.arg: ast.dump(keyword.value, include_attributes=False)
        for keyword in node.keywords
        if keyword.arg is not None
    }
    expected_dump = {
        name: ast.dump(
            ast.parse(expression, mode="eval").body,
            include_attributes=False,
        )
        for name, expression in expected.items()
    }
    return len(actual) == len(node.keywords) and actual == expected_dump


def _reason_assignment_valid(
    assignment: ast.Assign,
    allowed_reason_codes: set[str],
    function_name: str,
) -> bool:
    if (
        len(assignment.targets) != 1
        or not isinstance(assignment.targets[0], ast.Name)
        or assignment.targets[0].id != "reasons"
    ):
        return False
    value = assignment.value
    if isinstance(value, ast.Tuple) and not value.elts:
        return True
    if (
        not isinstance(value, ast.BinOp)
        or not isinstance(value.op, ast.Add)
        or not isinstance(value.left, ast.Name)
        or value.left.id != "reasons"
    ):
        return False
    if isinstance(value.right, ast.Tuple):
        return bool(value.right.elts) and all(
            isinstance(item, ast.Constant)
            and type(item.value) is str
            and item.value in allowed_reason_codes
            for item in value.right.elts
        )
    allowed_nested_helpers = {
        "_authorization_reasons": {"_strategy_binding_reasons"},
    }.get(function_name, set())
    return (
        isinstance(value.right, ast.Call)
        and isinstance(value.right.func, ast.Name)
        and value.right.func.id in allowed_nested_helpers
        and len(value.right.args) == 1
        and isinstance(value.right.args[0], ast.Name)
        and value.right.args[0].id == "evidence"
        and not value.right.keywords
    )


def _reason_return_valid(node: ast.Return) -> bool:
    return (
        isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_sorted_unique"
        and len(node.value.args) == 1
        and isinstance(node.value.args[0], ast.Name)
        and node.value.args[0].id == "reasons"
        and not node.value.keywords
    )


def _blocked_decision_call_valid(
    node: ast.Call,
    allowed_reason_codes: set[str],
) -> bool:
    if len(node.args) != 5 or node.keywords:
        return False
    evidence, stage, reasons, fingerprint, capability_reached = node.args
    allowed_stages = {
        "identity", "authorization", "paper_evidence", "candidate", "risk",
        "final_safety", "capability",
    }
    if (
        not isinstance(evidence, ast.Name)
        or evidence.id != "evidence"
        or not isinstance(stage, ast.Constant)
        or type(stage.value) is not str
        or stage.value not in allowed_stages
        or not isinstance(fingerprint, ast.Name)
        or fingerprint.id != "fingerprint"
        or not isinstance(capability_reached, ast.Constant)
        or type(capability_reached.value) is not bool
        or capability_reached.value != (stage.value == "capability")
    ):
        return False
    if isinstance(reasons, ast.Name):
        return reasons.id == "reasons"
    return (
        isinstance(reasons, ast.Tuple)
        and bool(reasons.elts)
        and all(
            isinstance(item, ast.Constant)
            and type(item.value) is str
            and item.value in allowed_reason_codes
            for item in reasons.elts
        )
    )


def _assignment_values_match(
    assignments: list[ast.Assign],
    target_name: str,
    expected_expressions: tuple[str, ...],
) -> bool:
    actual = sorted(
        ast.dump(assignment.value, include_attributes=False)
        for assignment in assignments
        if len(assignment.targets) == 1
        and isinstance(assignment.targets[0], ast.Name)
        and assignment.targets[0].id == target_name
    )
    expected = sorted(
        ast.dump(
            ast.parse(expression, mode="eval").body,
            include_attributes=False,
        )
        for expression in expected_expressions
    )
    return actual == expected


def _purity_errors(source: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    errors: list[str] = []
    reviewed_ast_sha256 = hashlib.sha256(
        ast.dump(tree, include_attributes=False).encode("utf-8")
    ).hexdigest().upper()
    if reviewed_ast_sha256 != REVIEWED_KERNEL_AST_SHA256:
        errors.append("reviewed_kernel_ast")
    interpreter_metadata = {
        "__name__", "__doc__", "__package__", "__loader__", "__spec__",
        "__file__", "__cached__", "__builtins__", "__annotations__",
    }
    forbidden_loads = {
        "open", "__import__", "eval", "exec", "compile", "getattr", "setattr",
        "delattr", "globals", "locals", "vars", "print", "input", "breakpoint",
        "exit", "quit", "help", "SystemExit", "KeyboardInterrupt", "GeneratorExit",
        "BaseException",
    } | interpreter_metadata
    module_functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    function_returns = {
        node.name: node.returns
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    function_parameters = {
        node.name: tuple(
            argument.annotation
            for argument in (
                node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            )
        )
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    module_classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    model_classes = {"FrozenKernelModel"} | {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and len(node.bases) == 1
        and isinstance(node.bases[0], ast.Name)
        and node.bases[0].id == "FrozenKernelModel"
    }
    model_fields = {
        node.name: {
            statement.target.id: statement.annotation
            for statement in node.body
            if isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
        }
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in model_classes
    }
    model_required_fields = {
        node.name: {
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is None
        }
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in model_classes
    }
    reason_code_annotation = model_fields.get("KernelDecisionV1", {}).get(
        "reason_codes"
    )
    allowed_reason_codes = (
        {
            node.value
            for node in ast.walk(reason_code_annotation)
            if isinstance(node, ast.Constant) and type(node.value) is str
        }
        if reason_code_annotation is not None
        else set()
    )
    declaration_names: list[str] = []
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            declaration_names.extend(
                alias.asname or alias.name for alias in statement.names
            )
        elif isinstance(statement, ast.ImportFrom):
            declaration_names.extend(
                alias.asname or alias.name for alias in statement.names
            )
        elif isinstance(statement, ast.Assign):
            declaration_names.extend(
                target.id for target in statement.targets if isinstance(target, ast.Name)
            )
        elif isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
            declaration_names.append(statement.name)
    if len(declaration_names) != len(set(declaration_names)):
        errors.append("module_rebind")
    declaration_reserved = {
        "all", "any", "enumerate", "isinstance", "issubclass", "len", "max",
        "min", "range", "sorted", "str", "int", "bool", "bytes", "tuple",
        "type", "zip", "dict", "list", "ValueError", "RecursionError",
    }
    if set(declaration_names) & (declaration_reserved | forbidden_loads):
        errors.append("reserved_declaration")
    import_bindings: set[str] = set()
    constants: set[str] = set()
    for statement_index, statement in enumerate(tree.body):
        if isinstance(statement, ast.Import):
            if len(statement.names) != 1 or statement.names[0].asname is not None:
                errors.append("import_form")
            else:
                row = ("import", statement.names[0].name, ())
                if row not in ALLOWED_IMPORTS:
                    errors.append("import_member")
                if statement.names[0].name in import_bindings | constants:
                    errors.append("module_rebind")
                import_bindings.add(statement.names[0].name)
        elif isinstance(statement, ast.ImportFrom):
            names = tuple(item.name for item in statement.names)
            if any(item.asname is not None for item in statement.names):
                errors.append("import_alias")
            if ("from", statement.module, names) not in ALLOWED_IMPORTS:
                errors.append("import_member")
            if any(name in import_bindings | constants for name in names):
                errors.append("module_rebind")
            import_bindings.update(names)
        elif isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                errors.append("module_assignment_target")
            else:
                target = statement.targets[0].id
                if target in import_bindings | module_functions | module_classes | constants:
                    errors.append("module_rebind")
                constants.add(target)
            if not _immutable_constant(statement.value, constants):
                errors.append("mutable_or_executable_global")
        elif isinstance(statement, ast.FunctionDef):
            if statement.decorator_list:
                errors.append("function_decorator")
            if statement.args.vararg is not None or statement.args.kwarg is not None:
                errors.append("variadic_definition")
            definition_values = tuple(statement.args.defaults) + tuple(
                value for value in statement.args.kw_defaults if value is not None
            )
            if any(not _immutable_constant(value, constants) for value in definition_values):
                errors.append("definition_default")
            annotations = [argument.annotation for argument in statement.args.posonlyargs + statement.args.args + statement.args.kwonlyargs]
            annotations.append(statement.returns)
            if any(not _type_expression_valid(annotation, module_classes) for annotation in annotations):
                errors.append("definition_annotation")
        elif isinstance(statement, ast.ClassDef):
            if statement.decorator_list or statement.keywords:
                errors.append("class_header")
            expected_base = "BaseModel" if statement.name == "FrozenKernelModel" else "ValueError" if statement.name == "KernelEvidenceValidationError" else "FrozenKernelModel"
            if len(statement.bases) != 1 or not isinstance(statement.bases[0], ast.Name) or statement.bases[0].id != expected_base:
                errors.append("class_base")
            config_count = 0
            declared_field_names = [
                class_statement.target.id
                for class_statement in statement.body
                if isinstance(class_statement, ast.AnnAssign)
                and isinstance(class_statement.target, ast.Name)
            ]
            if len(declared_field_names) != len(set(declared_field_names)):
                errors.append("duplicate_class_field")
            for class_statement_index, class_statement in enumerate(statement.body):
                if (
                    class_statement_index == 0
                    and isinstance(class_statement, ast.Expr)
                    and isinstance(class_statement.value, ast.Constant)
                    and type(class_statement.value.value) is str
                ):
                    continue
                if isinstance(class_statement, ast.AnnAssign):
                    if not isinstance(class_statement.target, ast.Name) or not _type_expression_valid(class_statement.annotation, module_classes):
                        errors.append("class_field")
                    if (
                        isinstance(class_statement.target, ast.Name)
                        and class_statement.target.id == "model_config"
                    ):
                        errors.append("model_config")
                    if class_statement.value is not None and not _immutable_constant(class_statement.value, constants):
                        errors.append("class_field_default")
                    continue
                if (
                    isinstance(class_statement, ast.Assign)
                    and len(class_statement.targets) == 1
                    and isinstance(class_statement.targets[0], ast.Name)
                    and class_statement.targets[0].id == "model_config"
                ):
                    config_count += 1
                    config_call = class_statement.value
                    keyword_values = {
                        keyword.arg: keyword.value
                        for keyword in config_call.keywords
                        if keyword.arg is not None
                    } if isinstance(config_call, ast.Call) else {}
                    expected_values = {
                        "frozen": True,
                        "extra": "forbid",
                        "strict": True,
                        "revalidate_instances": "always",
                    }
                    exact_values = (
                        isinstance(config_call, ast.Call)
                        and isinstance(config_call.func, ast.Name)
                        and config_call.func.id == "ConfigDict"
                        and not config_call.args
                        and len(config_call.keywords) == 4
                        and set(keyword_values) == set(expected_values)
                        and all(
                            isinstance(keyword_values[name], ast.Constant)
                            and keyword_values[name].value == value
                            and type(keyword_values[name].value) is type(value)
                            for name, value in expected_values.items()
                        )
                    )
                    if statement.name != "FrozenKernelModel" or not exact_values:
                        errors.append("model_config")
                    continue
                errors.append("class_body")
            expected_config_count = 1 if statement.name == "FrozenKernelModel" else 0
            if config_count != expected_config_count:
                errors.append("model_config")
        else:
            if not (
                statement_index == 0
                and
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and type(statement.value.value) is str
            ):
                errors.append("module_statement")
    forbidden_nodes = (
        ast.AsyncFunctionDef, ast.Await, ast.Lambda, ast.Global, ast.Nonlocal,
        ast.While, ast.With, ast.AsyncWith, ast.Match, ast.Yield, ast.YieldFrom,
        ast.AugAssign, ast.Delete, ast.NamedExpr, ast.Assert, ast.Break, ast.Continue,
        ast.List, ast.Set, ast.ListComp, ast.SetComp, ast.JoinedStr, ast.FormattedValue,
        ast.Pass, ast.Starred,
    )
    safe_builtins = {"all", "any", "enumerate", "isinstance", "issubclass", "len", "max", "min", "range", "sorted", "str", "int", "bool", "bytes", "tuple", "type", "zip"}
    reserved_bindings = (
        import_bindings
        | module_functions
        | module_classes
        | safe_builtins
        | {"RecursionError"}
        | interpreter_metadata
    )
    module_scalar_constants: dict[str, set[str]] = {}
    module_tuple_scalar_constants: dict[str, set[str]] = {}
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            scalar_kinds, tuple_scalar_kinds = _expression_scalar_provenance(
                statement.value,
                module_scalar_constants,
                module_tuple_scalar_constants,
                {},
                {},
                model_fields,
                model_classes,
                function_returns,
            )
            if scalar_kinds:
                module_scalar_constants[statement.targets[0].id] = scalar_kinds
            if tuple_scalar_kinds:
                module_tuple_scalar_constants[statement.targets[0].id] = (
                    tuple_scalar_kinds
                )
    for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
        arguments = function.args.posonlyargs + function.args.args + function.args.kwonlyargs
        argument_annotations = {argument.arg: argument.annotation for argument in arguments}
        annotation_nodes = {
            nested
            for annotation in tuple(argument_annotations.values()) + (function.returns,)
            if annotation is not None
            for nested in ast.walk(annotation)
        }
        untyped_provenance = {
            name for name, annotation in argument_annotations.items() if annotation is None
        }
        untyped_assignments = [
            node for node in ast.walk(function) if isinstance(node, ast.Assign)
        ]
        for _ in range(len(untyped_assignments) + 1):
            changed = False
            for assignment in untyped_assignments:
                if (
                    len(assignment.targets) == 1
                    and isinstance(assignment.targets[0], ast.Name)
                    and isinstance(assignment.value, ast.Name)
                    and assignment.value.id in untyped_provenance
                    and assignment.targets[0].id not in untyped_provenance
                ):
                    untyped_provenance.add(assignment.targets[0].id)
                    changed = True
            if not changed:
                break
        model_variables: dict[str, set[str]] = {}
        tuple_model_variables: dict[str, set[str]] = {}
        for argument_name, annotation in argument_annotations.items():
            argument_models, argument_tuple_models = _annotation_provenance(
                annotation,
                model_classes,
            )
            if argument_models:
                model_variables[argument_name] = argument_models
            if argument_tuple_models:
                tuple_model_variables[argument_name] = argument_tuple_models
        assignments = [
            node for node in ast.walk(function) if isinstance(node, ast.Assign)
        ]
        parents = {
            child: parent
            for parent in ast.walk(function)
            for child in ast.iter_child_nodes(parent)
        }
        if function.name.endswith("_reasons"):
            reason_assignments = [
                assignment
                for assignment in assignments
                if any(
                    isinstance(target, ast.Name) and target.id == "reasons"
                    for target in assignment.targets
                )
            ]
            reason_returns = [
                node for node in ast.walk(function) if isinstance(node, ast.Return)
            ]
            if (
                not reason_assignments
                or not all(
                    _reason_assignment_valid(
                        assignment,
                        allowed_reason_codes,
                        function.name,
                    )
                    for assignment in reason_assignments
                )
                or not reason_returns
                or not all(_reason_return_valid(node) for node in reason_returns)
            ):
                errors.append("reason_provenance")
        if function.name == "evaluate_execution":
            expected_reason_helpers = [
                "_identity_reasons",
                "_authorization_reasons",
                "_paper_reasons",
                "_candidate_reasons",
                "_risk_reasons",
                "_final_safety_reasons",
                "_capability_reasons",
            ]
            reason_assignments = sorted(
                (
                    assignment
                    for assignment in assignments
                    if len(assignment.targets) == 1
                    and isinstance(assignment.targets[0], ast.Name)
                    and assignment.targets[0].id == "reasons"
                ),
                key=lambda assignment: assignment.lineno,
            )
            actual_reason_helpers = []
            for assignment in reason_assignments:
                value = assignment.value
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and len(value.args) == 1
                    and isinstance(value.args[0], ast.Name)
                    and value.args[0].id == "evidence"
                    and not value.keywords
                ):
                    actual_reason_helpers.append(value.func.id)
                else:
                    actual_reason_helpers.append(None)
            blocked_calls = sorted(
                (
                    node
                    for node in ast.walk(function)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_blocked_decision"
                ),
                key=lambda node: node.lineno,
            )
            actual_blocked_stages = [
                node.args[1].value
                if len(node.args) == 5
                and isinstance(node.args[1], ast.Constant)
                and type(node.args[1].value) is str
                else None
                for node in blocked_calls
            ]
            if (
                not _assignment_values_match(
                    assignments,
                    "fingerprint",
                    ("_evidence_fingerprint(evidence)",),
                )
                or actual_reason_helpers != expected_reason_helpers
                or actual_blocked_stages
                != [
                    "identity", "authorization", "authorization",
                    "paper_evidence", "candidate", "risk", "final_safety",
                    "capability",
                ]
                or not _assignment_values_match(
                    assignments,
                    "durable_requirement",
                    ('"required"', '"not_required"'),
                )
                or not _assignment_values_match(
                    assignments,
                    "reservation_requirement",
                    ('"required"', '"not_required"'),
                )
            ):
                errors.append("evaluation_flow")
        if function.name == "_blocked_decision":
            for requirement_name in {
                "durable_requirement", "reservation_requirement"
            }:
                requirement_assignments = [
                    assignment
                    for assignment in assignments
                    if len(assignment.targets) == 1
                    and isinstance(assignment.targets[0], ast.Name)
                    and assignment.targets[0].id == requirement_name
                ]
                if len(requirement_assignments) != 3 or not all(
                    isinstance(assignment.value, ast.Constant)
                    and type(assignment.value.value) is str
                    and assignment.value.value
                    in {"required", "not_required", "not_evaluated"}
                    for assignment in requirement_assignments
                ):
                    errors.append("decision_requirement_provenance")
        if function.name == "validate_kernel_input_v1":
            preflight_assignments = [
                assignment
                for assignment in assignments
                if len(assignment.targets) == 1
                and isinstance(assignment.targets[0], ast.Tuple)
                and tuple(
                    item.id if isinstance(item, ast.Name) else None
                    for item in assignment.targets[0].elts
                )
                == ("detached", "remaining_budget", "preflight_findings")
            ]
            expected_preflight_call = ast.dump(
                ast.parse(
                    '_copy_raw_value(raw_snapshot, "$", "$", 0, '
                    'MAX_RAW_NODES, ())',
                    mode="eval",
                ).body,
                include_attributes=False,
            )
            if (
                len(preflight_assignments) != 1
                or ast.dump(
                    preflight_assignments[0].value,
                    include_attributes=False,
                )
                != expected_preflight_call
                or not _assignment_values_match(
                    assignments,
                    "timestamp_paths",
                    ('_finding_paths(preflight_findings, "timestamp")',),
                )
                or not _assignment_values_match(
                    assignments,
                    "pydantic_findings",
                    (
                        "()",
                        'pydantic_findings + (("schema", path),)',
                    ),
                )
                or not _assignment_values_match(
                    assignments,
                    "timestamp_findings",
                    ("_validated_timestamp_findings(validated)",),
                )
                or not _assignment_values_match(
                    assignments,
                    "all_findings",
                    (
                        "preflight_findings + pydantic_findings + "
                        "timestamp_findings",
                    ),
                )
            ):
                errors.append("validation_finding_provenance")
            error_code_assignments = [
                assignment
                for assignment in assignments
                if len(assignment.targets) == 1
                and isinstance(assignment.targets[0], ast.Name)
                and assignment.targets[0].id == "error_code"
            ]
            error_code_values = {
                assignment.value.value
                for assignment in error_code_assignments
                if isinstance(assignment.value, ast.Constant)
                and type(assignment.value.value) is str
            }
            if (
                len(error_code_assignments) != 2
                or error_code_values
                != {"naive_or_invalid_timestamp", "invalid_evidence_schema"}
            ):
                errors.append("validation_error_code_provenance")
            all_paths_assignments = [
                assignment
                for assignment in assignments
                if len(assignment.targets) == 1
                and isinstance(assignment.targets[0], ast.Name)
                and assignment.targets[0].id == "all_paths"
            ]
            allowed_all_paths_values = {
                ast.dump(
                    ast.parse(expression, mode="eval").body,
                    include_attributes=False,
                )
                for expression in ("()", "all_paths + (finding_path,)")
            }
            if len(all_paths_assignments) != 2 or {
                ast.dump(assignment.value, include_attributes=False)
                for assignment in all_paths_assignments
            } != allowed_all_paths_values:
                errors.append("validation_path_provenance")
        if function.name == "_copy_raw_value":
            recursive_calls = [
                node
                for node in ast.walk(function)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_copy_raw_value"
                )
            ]
            recursion_tries = [
                node for node in ast.walk(function) if isinstance(node, ast.Try)
            ]
            if len(recursive_calls) != 2:
                errors.append("recursion_call_count")
            if len(recursion_tries) != 2:
                errors.append("recursion_try_count")
            dict_tries = [
                node
                for node in recursion_tries
                if _dominated_by_raw_type(node, parents, {"dict"})
            ]
            sequence_tries = [
                node
                for node in recursion_tries
                if _dominated_by_raw_type(node, parents, {"list", "tuple"})
            ]
            dict_try_set = set(dict_tries)
            sequence_try_set = set(sequence_tries)
            if (
                len(dict_tries) != 1
                or len(sequence_tries) != 1
                or bool(dict_try_set & sequence_try_set)
                or dict_try_set | sequence_try_set != set(recursion_tries)
                or any(
                    _copy_recursion_try_variant(node) != "dict"
                    or _copy_recursion_loop_variant(node, parents) != "dict"
                    for node in dict_tries
                )
                or any(
                    _copy_recursion_try_variant(node) != "sequence"
                    or _copy_recursion_loop_variant(node, parents) != "sequence"
                    for node in sequence_tries
                )
            ):
                errors.append("recursion_try_placement")
        type_alias_sources: dict[str, str] = {}
        for assignment in assignments:
            if (
                len(assignment.targets) == 1
                and isinstance(assignment.targets[0], ast.Name)
                and isinstance(assignment.value, ast.Call)
                and isinstance(assignment.value.func, ast.Name)
                and assignment.value.func.id == "type"
                and len(assignment.value.args) == 1
                and isinstance(assignment.value.args[0], ast.Name)
                and not assignment.value.keywords
            ):
                type_alias_sources[assignment.targets[0].id] = (
                    assignment.value.args[0].id
                )
        raw_container_provenance: set[str] = set()
        if function.name == "_copy_raw_value":
            raw_container_provenance.add("value")
            for _ in range(len(assignments) + 1):
                changed = False
                for assignment in assignments:
                    if (
                        len(assignment.targets) != 1
                        or not isinstance(assignment.targets[0], ast.Name)
                    ):
                        continue
                    source_names = {
                        node.id
                        for node in ast.walk(assignment.value)
                        if isinstance(node, ast.Name)
                        and isinstance(node.ctx, ast.Load)
                        and node.id in raw_container_provenance
                    }
                    target_name = assignment.targets[0].id
                    if source_names and target_name not in raw_container_provenance:
                        raw_container_provenance.add(target_name)
                        changed = True
                if not changed:
                    break
        iterations = [
            node
            for node in ast.walk(function)
            if isinstance(node, (ast.For, ast.comprehension))
        ]
        raw_derived_provenance: set[str] = set()
        raw_key_provenance: set[str] = set()
        if function.name == "_copy_raw_value":
            for iteration in (
                node for node in ast.walk(function) if isinstance(node, ast.For)
            ):
                if (
                    isinstance(iteration.iter, ast.Name)
                    and iteration.iter.id in {"value", "ancestors"}
                ):
                    raw_derived_provenance.update(_target_names(iteration.target))
                if (
                    isinstance(iteration.iter, ast.Call)
                    and isinstance(iteration.iter.func, ast.Attribute)
                    and isinstance(iteration.iter.func.value, ast.Name)
                    and iteration.iter.func.value.id == "value"
                    and iteration.iter.func.attr == "items"
                    and isinstance(iteration.target, ast.Tuple)
                    and len(iteration.target.elts) == 2
                ):
                    raw_derived_provenance.update(_target_names(iteration.target))
                    raw_key_provenance.update(
                        _target_names(iteration.target.elts[0])
                    )
            for _ in range(len(assignments) + 1):
                changed = False
                for assignment in assignments:
                    if (
                        len(assignment.targets) == 1
                        and isinstance(assignment.targets[0], ast.Name)
                        and assignment.targets[0].id not in raw_derived_provenance
                    ):
                        source_names = {
                            node.id
                            for node in ast.walk(assignment.value)
                            if isinstance(node, ast.Name)
                            and isinstance(node.ctx, ast.Load)
                            and node.id in raw_derived_provenance
                        }
                        if not source_names:
                            continue
                        raw_type_metadata = (
                            isinstance(assignment.value, ast.Call)
                            and isinstance(assignment.value.func, ast.Name)
                            and assignment.value.func.id == "type"
                            and len(assignment.value.args) == 1
                            and isinstance(assignment.value.args[0], ast.Name)
                            and assignment.value.args[0].id in source_names
                            and not assignment.value.keywords
                        )
                        if raw_type_metadata:
                            continue
                        sanitized_raw_keys = all(
                            source_name in raw_key_provenance
                            and _dominated_by_local_exact_type(
                                assignment,
                                source_name,
                                parents,
                                type_alias_sources,
                                {"str"},
                            )
                            and _dominated_by_known_raw_key(
                                assignment,
                                source_name,
                                parents,
                            )
                            for source_name in source_names
                        )
                        if sanitized_raw_keys:
                            continue
                        raw_derived_provenance.add(assignment.targets[0].id)
                        changed = True
                if not changed:
                    break
        for _ in range(len(assignments) + len(iterations) + 1):
            changed = False
            for assignment in assignments:
                if (
                    len(assignment.targets) != 1
                    or not isinstance(assignment.targets[0], ast.Name)
                ):
                    continue
                target_name = assignment.targets[0].id
                assigned_models, assigned_tuple_models = _expression_model_provenance(
                    assignment.value,
                    model_variables,
                    tuple_model_variables,
                    model_fields,
                    model_classes,
                    function_returns,
                )
                new_models = model_variables.get(target_name, set()) | assigned_models
                new_tuple_models = (
                    tuple_model_variables.get(target_name, set())
                    | assigned_tuple_models
                )
                if new_models != model_variables.get(target_name, set()):
                    model_variables[target_name] = new_models
                    changed = True
                if new_tuple_models != tuple_model_variables.get(target_name, set()):
                    tuple_model_variables[target_name] = new_tuple_models
                    changed = True
            for iteration in iterations:
                target = iteration.target
                iterable = iteration.iter if isinstance(iteration, ast.comprehension) else iteration.iter
                if not isinstance(target, ast.Name):
                    continue
                _, iterable_tuple_models = _expression_model_provenance(
                    iterable,
                    model_variables,
                    tuple_model_variables,
                    model_fields,
                    model_classes,
                    function_returns,
                )
                new_models = (
                    model_variables.get(target.id, set()) | iterable_tuple_models
                )
                if new_models != model_variables.get(target.id, set()):
                    model_variables[target.id] = new_models
                    changed = True
            if not changed:
                break
        for assignment in assignments:
            if (
                len(assignment.targets) != 1
                or not isinstance(assignment.targets[0], ast.Name)
                or assignment.targets[0].id not in model_variables
            ):
                continue
            if (
                isinstance(assignment.value, ast.Constant)
                and assignment.value.value is None
            ):
                continue
            assigned_models, _ = _expression_model_provenance(
                assignment.value,
                model_variables,
                tuple_model_variables,
                model_fields,
                model_classes,
                function_returns,
            )
            if not assigned_models:
                errors.append("model_provenance_conflict")
        scalar_variables: dict[str, set[str]] = {
            name: set(kinds) for name, kinds in module_scalar_constants.items()
        }
        tuple_scalar_variables: dict[str, set[str]] = {
            name: set(kinds)
            for name, kinds in module_tuple_scalar_constants.items()
        }
        for argument_name, annotation in argument_annotations.items():
            argument_scalars, argument_tuple_scalars = _annotation_scalar_provenance(
                annotation
            )
            if argument_scalars:
                scalar_variables[argument_name] = argument_scalars
            if argument_tuple_scalars:
                tuple_scalar_variables[argument_name] = argument_tuple_scalars
        for handler in (
            node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)
        ):
            if (
                isinstance(handler.type, ast.Name)
                and handler.type.id == "ValidationError"
                and handler.name is not None
            ):
                scalar_variables[handler.name] = {"ValidationError"}
        for assignment in assignments:
            if (
                len(assignment.targets) == 1
                and isinstance(assignment.targets[0], ast.Tuple)
                and len(assignment.targets[0].elts) == 3
                and isinstance(assignment.value, ast.Call)
                and isinstance(assignment.value.func, ast.Name)
                and assignment.value.func.id == "_copy_raw_value"
            ):
                budget_target = assignment.targets[0].elts[1]
                findings_target = assignment.targets[0].elts[2]
                if isinstance(budget_target, ast.Name):
                    scalar_variables[budget_target.id] = {"int"}
                if isinstance(findings_target, ast.Name):
                    scalar_variables[findings_target.id] = {"tuple"}
                    tuple_scalar_variables[findings_target.id] = {
                        "tuple", "str"
                    }
        for _ in range(len(assignments) + len(iterations) + 1):
            changed = False
            for assignment in assignments:
                if (
                    len(assignment.targets) != 1
                    or not isinstance(assignment.targets[0], ast.Name)
                ):
                    continue
                target_name = assignment.targets[0].id
                assigned_scalars, assigned_tuple_scalars = _expression_scalar_provenance(
                    assignment.value,
                    scalar_variables,
                    tuple_scalar_variables,
                    model_variables,
                    tuple_model_variables,
                    model_fields,
                    model_classes,
                    function_returns,
                )
                new_scalars = scalar_variables.get(target_name, set()) | assigned_scalars
                new_tuple_scalars = (
                    tuple_scalar_variables.get(target_name, set())
                    | assigned_tuple_scalars
                )
                if new_scalars != scalar_variables.get(target_name, set()):
                    scalar_variables[target_name] = new_scalars
                    changed = True
                if new_tuple_scalars != tuple_scalar_variables.get(target_name, set()):
                    tuple_scalar_variables[target_name] = new_tuple_scalars
                    changed = True
            for iteration in iterations:
                target = iteration.target
                iterable = iteration.iter if isinstance(iteration, ast.comprehension) else iteration.iter
                if not isinstance(target, ast.Name):
                    continue
                iterable_scalars, iterable_items = _expression_scalar_provenance(
                    iterable,
                    scalar_variables,
                    tuple_scalar_variables,
                    model_variables,
                    tuple_model_variables,
                    model_fields,
                    model_classes,
                    function_returns,
                )
                if "str" in iterable_scalars:
                    iterable_items.add("str")
                if "bytes" in iterable_scalars:
                    iterable_items.add("int")
                new_scalars = scalar_variables.get(target.id, set()) | iterable_items
                if new_scalars != scalar_variables.get(target.id, set()):
                    scalar_variables[target.id] = new_scalars
                    changed = True
            if not changed:
                break
        if raw_derived_provenance & set(scalar_variables):
            errors.append("raw_taint_scalar_conflict")
        expected_return_models, expected_return_tuple_models = _annotation_provenance(
            function.returns,
            model_classes,
        )
        expected_return_scalars, expected_return_items = (
            _annotation_scalar_provenance(function.returns)
        )
        if function.returns is not None:
            for return_node in (
                node for node in ast.walk(function) if isinstance(node, ast.Return)
            ):
                return_expression = (
                    return_node.value
                    if return_node.value is not None
                    else ast.Constant(value=None)
                )
                actual_return_models, actual_return_tuple_models = (
                    _expression_model_provenance(
                    return_expression,
                    model_variables,
                    tuple_model_variables,
                    model_fields,
                    model_classes,
                    function_returns,
                    )
                )
                actual_return_scalars, actual_return_items = (
                    _expression_scalar_provenance(
                        return_expression,
                        scalar_variables,
                        tuple_scalar_variables,
                        model_variables,
                        tuple_model_variables,
                        model_fields,
                        model_classes,
                        function_returns,
                    )
                )
                has_return_provenance = bool(
                    actual_return_models
                    or actual_return_tuple_models
                    or actual_return_scalars
                )
                model_return_valid = not actual_return_models or (
                    bool(expected_return_models)
                    and actual_return_models <= expected_return_models
                )
                tuple_model_return_valid = not actual_return_tuple_models or (
                    bool(expected_return_tuple_models)
                    and actual_return_tuple_models <= expected_return_tuple_models
                )
                scalar_return_valid = not actual_return_scalars or (
                    bool(expected_return_scalars)
                    and actual_return_scalars <= expected_return_scalars
                    and (
                        not actual_return_items
                        or actual_return_items <= expected_return_items
                    )
                )
                if (
                    not has_return_provenance
                    or not model_return_valid
                    or not tuple_model_return_valid
                    or not scalar_return_valid
                ):
                    errors.append("return_provenance")
        decimal_parameters = {
            name
            for name, annotation in argument_annotations.items()
            if _annotation_contains(annotation, "Decimal")
        }
        local_names = {argument.arg for argument in arguments}
        for assignment in assignments:
            for target in assignment.targets:
                local_names.update(_target_names(target))
        for iteration in iterations:
            local_names.update(_target_names(iteration.target))
        for handler in (
            node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)
        ):
            if handler.name is not None:
                local_names.add(handler.name)
        allowed_lexical_names = (
            local_names
            | import_bindings
            | constants
            | module_functions
            | module_classes
            | safe_builtins
            | {"dict", "list", "ValueError", "RecursionError"}
        )
        if any(argument.arg in reserved_bindings for argument in arguments):
            errors.append("reserved_parameter")
        if function.name in {"_copy_raw_value", "_canonical_value"}:
            first = function.body[0] if function.body else None
            valid_first = isinstance(first, ast.Assign) and len(first.targets) == 1 and isinstance(first.targets[0], ast.Name) and first.targets[0].id == "value_type" and isinstance(first.value, ast.Call) and isinstance(first.value.func, ast.Name) and first.value.func.id == "type" and len(first.value.args) == 1 and isinstance(first.value.args[0], ast.Name) and first.value.args[0].id == "value"
            if not valid_first:
                errors.append("classified_first_operation")
        allowed_function_docstring = None
        if (
            function.body
            and isinstance(function.body[0], ast.Expr)
            and isinstance(function.body[0].value, ast.Constant)
            and type(function.body[0].value.value) is str
        ):
            allowed_function_docstring = function.body[0]
        validation_error_names = {
            handler.name
            for handler in ast.walk(function)
            if isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and handler.type.id == "ValidationError"
            and handler.name is not None
        }
        for node in ast.walk(function):
            if node is not function and isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                errors.append("nested_definition")
            if isinstance(node, forbidden_nodes):
                errors.append(type(node).__name__)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                errors.append("local_import")
            if isinstance(node, ast.Expr) and node is not allowed_function_docstring:
                errors.append("function_expression_statement")
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in forbidden_loads:
                errors.append("forbidden_name")
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in validation_error_names
            ):
                parent = parents.get(node)
                grandparent = parents.get(parent) if parent is not None else None
                allowed_validation_error_use = (
                    isinstance(parent, ast.Attribute)
                    and parent.value is node
                    and parent.attr == "errors"
                    and isinstance(grandparent, ast.Call)
                    and grandparent.func is parent
                )
                if not allowed_validation_error_use:
                    errors.append("validation_error_use")
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id not in allowed_lexical_names
            ):
                errors.append("unknown_lexical_name")
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and function.name == "_copy_raw_value"
                and node.id in raw_container_provenance
                and _inside_return(node, parents)
                and not _dominated_by_raw_type(
                    node,
                    parents,
                    {
                        "None", "bool", "int", "str", "bytes", "Decimal",
                        "datetime",
                    },
                )
            ):
                errors.append("raw_container_return")
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in untyped_provenance
            ):
                parent = parents.get(node)
                raw_or_canonical_branch = (
                    function.name in {"_copy_raw_value", "_canonical_value"}
                    and _dominated_by_raw_type(
                        node,
                        parents,
                        {
                            "None", "bool", "int", "str", "bytes", "Decimal",
                            "datetime", "dict", "list", "tuple",
                        },
                    )
                )
                validation_boundary_call = (
                    function.name == "validate_kernel_input_v1"
                    and isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Name)
                    and parent.func.id == "_copy_raw_value"
                    and parent.args
                    and parent.args[0] is node
                )
                canonical_boundary_call = (
                    function.name == "_canonical_sha256"
                    and isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Name)
                    and parent.func.id == "_canonical_value"
                    and parent.args
                    and parent.args[0] is node
                )
                type_classification_call = (
                    isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Name)
                    and parent.func.id == "type"
                    and parent.args == [node]
                )
                runtime_data_parent = isinstance(
                    parent,
                    (ast.Return, ast.Tuple, ast.Dict, ast.List, ast.Set),
                ) or (
                    isinstance(parent, ast.Call) and node in parent.args
                )
                if (
                    runtime_data_parent
                    and not raw_or_canonical_branch
                    and not validation_boundary_call
                    and not canonical_boundary_call
                    and not type_classification_call
                ):
                    errors.append("untyped_parameter_as_data")
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in raw_derived_provenance
            ):
                parent = parents.get(node)
                recursive_child_call = (
                    isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Name)
                    and parent.func.id == "_copy_raw_value"
                    and parent.args
                    and parent.args[0] is node
                )
                identity_compare = (
                    isinstance(parent, ast.Compare)
                    and all(isinstance(operator, (ast.Is, ast.IsNot)) for operator in parent.ops)
                )
                type_classification_call = (
                    isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Name)
                    and parent.func.id == "type"
                    and parent.args == [node]
                    and not parent.keywords
                )
                exact_local_type_branch = _dominated_by_local_exact_type(
                    node,
                    node.id,
                    parents,
                    type_alias_sources,
                    {"str"},
                )
                raw_key_membership_check = (
                    node.id in raw_key_provenance
                    and
                    exact_local_type_branch
                    and isinstance(parent, ast.Compare)
                    and parent.left is node
                    and len(parent.ops) == 1
                    and isinstance(parent.ops[0], (ast.In, ast.NotIn))
                    and len(parent.comparators) == 1
                    and isinstance(parent.comparators[0], ast.Name)
                    and parent.comparators[0].id == "KNOWN_RAW_KEYS"
                )
                sanitized_raw_key_branch = (
                    node.id in raw_key_provenance
                    and
                    exact_local_type_branch
                    and _dominated_by_known_raw_key(node, node.id, parents)
                )
                if not (
                    recursive_child_call
                    or identity_compare
                    or type_classification_call
                    or raw_key_membership_check
                    or sanitized_raw_key_branch
                ):
                    errors.append("raw_derived_value_use")
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in (reserved_bindings | {"dict", "list", "ValueError", "RecursionError"}) and node not in annotation_nodes:
                parent = parents.get(node)
                valid_callable_context = isinstance(parent, ast.Call) and parent.func is node
                valid_callable_context = valid_callable_context or (isinstance(parent, ast.Attribute) and parent.value is node and ((node.id == "timezone" and parent.attr == "utc") or (isinstance(parents.get(parent), ast.Call) and parents[parent].func is parent)))
                valid_callable_context = valid_callable_context or isinstance(parent, (ast.Compare, ast.ExceptHandler))
                if isinstance(parent, ast.Tuple):
                    grandparent = parents.get(parent)
                    valid_callable_context = valid_callable_context or (isinstance(grandparent, ast.Call) and isinstance(grandparent.func, ast.Name) and grandparent.func.id in {"isinstance", "issubclass"})
                if not valid_callable_context:
                    errors.append("callable_name_as_data")
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in reserved_bindings:
                errors.append("reserved_rebind")
            if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
                errors.append("external_mutation")
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                if node.attr.startswith("__") or node.attr.endswith("__"):
                    errors.append("dunder_attribute")
                parent = parents.get(node)
                is_call_target = isinstance(parent, ast.Call) and parent.func is node
                if isinstance(node.value, ast.Name) and node.value.id in model_classes:
                    approved_model_class_call = (
                        node.value.id == "KernelEvaluationInputV1"
                        and node.attr == "model_validate"
                        and is_call_target
                    )
                    if not approved_model_class_call:
                        errors.append("model_class_attribute")
                if isinstance(node.value, ast.Name) and node.value.id in {"json", "hashlib"}:
                    if node.value.id == "json" and node.attr != "dumps":
                        errors.append("imported_attribute")
                    if node.value.id == "hashlib" and node.attr != "sha256":
                        errors.append("imported_attribute")
                    if not is_call_target:
                        errors.append("callable_attribute_as_data")
                if isinstance(node.value, ast.Name) and node.value.id in import_bindings:
                    if not (node.value.id == "timezone" and node.attr == "utc") and not is_call_target:
                        errors.append("imported_attribute")
                if node.attr in {"strip", "split", "isdigit", "encode", "replace", "join", "items", "as_tuple", "is_finite", "utcoffset", "astimezone", "isoformat", "model_validate", "model_dump", "errors", "hexdigest", "dumps", "sha256"} and not is_call_target:
                    errors.append("callable_attribute_as_data")
                if isinstance(node.value, ast.Name) and node.value.id in argument_annotations:
                    annotation = argument_annotations[node.value.id]
                    typed_receiver = any(
                        _annotation_contains(annotation, name)
                        for name in ({"str", "Decimal", "datetime", "ValidationError"} | module_classes)
                    )
                    trusted_untyped = function.name in {"_copy_raw_value", "_canonical_value"}
                    if not typed_receiver and not trusted_untyped:
                        errors.append("unknown_parameter_receiver")
                receiver_models, _ = _expression_model_provenance(
                    node.value,
                    model_variables,
                    tuple_model_variables,
                    model_fields,
                    model_classes,
                    function_returns,
                )
                if receiver_models:
                    approved_model_method = node.attr == "model_dump" and is_call_target
                    if not approved_model_method and any(
                        node.attr not in model_fields.get(model_name, {})
                        for model_name in receiver_models
                    ):
                        errors.append("undeclared_model_field")
                receiver_scalars, _ = _expression_scalar_provenance(
                    node.value,
                    scalar_variables,
                    tuple_scalar_variables,
                    model_variables,
                    tuple_model_variables,
                    model_fields,
                    model_classes,
                    function_returns,
                )
                if (
                    "None" in receiver_scalars
                    and isinstance(node.value, ast.Name)
                    and _none_is_excluded(node, node.value.id, parents)
                ):
                    receiver_scalars = receiver_scalars - {"None"}
                scalar_method_table = {
                    "str": {"strip", "split", "isdigit", "encode", "replace", "join"},
                    "Decimal": {"as_tuple", "is_finite"},
                    "datetime": {"utcoffset", "astimezone", "isoformat"},
                    "ValidationError": {"errors"},
                    "dict": {"items"},
                    "hash": {"hexdigest"},
                }
                scalar_slot_table = {
                    "datetime": {"tzinfo"},
                    "decimal_tuple": {"digits", "exponent", "sign"},
                }
                imported_call = (
                    isinstance(node.value, ast.Name)
                    and node.value.id in {"json", "hashlib"}
                    and is_call_target
                )
                model_class_call = (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "KernelEvaluationInputV1"
                    and node.attr == "model_validate"
                    and is_call_target
                )
                model_instance_call = bool(receiver_models) and node.attr == "model_dump" and is_call_target
                raw_receiver = (
                    function.name == "_copy_raw_value"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "value"
                    and _dominated_by_raw_type(
                        node,
                        parents,
                        {"str", "Decimal", "datetime", "dict"},
                    )
                )
                canonical_receiver = (
                    function.name == "_canonical_value"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "value"
                    and _dominated_by_raw_type(
                        node,
                        parents,
                        {"datetime", "dict"},
                    )
                )
                if is_call_target:
                    scalar_call_valid = bool(receiver_scalars) and all(
                        node.attr in scalar_method_table.get(kind, set())
                        for kind in receiver_scalars
                    )
                    if not (
                        imported_call
                        or model_class_call
                        or model_instance_call
                        or raw_receiver
                        or canonical_receiver
                        or scalar_call_valid
                    ):
                        errors.append("unknown_method_receiver")
                elif receiver_scalars and not all(
                    node.attr in scalar_slot_table.get(kind, set())
                    for kind in receiver_scalars
                ):
                    errors.append("scalar_attribute")
                timezone_constant = (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "timezone"
                    and node.attr == "utc"
                )
                raw_datetime_slot = (
                    function.name == "_copy_raw_value"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "value"
                    and node.attr == "tzinfo"
                    and _dominated_by_raw_type(
                        node,
                        parents,
                        {"datetime"},
                    )
                )
                if (
                    not is_call_target
                    and not receiver_models
                    and not receiver_scalars
                    and not timezone_constant
                    and not raw_datetime_slot
                ):
                    errors.append("unknown_attribute_receiver")
            if isinstance(node, ast.Call):
                errors.extend(_call_errors(node, module_functions, module_classes))
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id in function_parameters
                ):
                    expected_parameters = function_parameters[node.func.id]
                    if (
                        len(node.args) != len(expected_parameters)
                        or node.keywords
                    ):
                        errors.append("module_call_signature")
                    for argument, expected_annotation in zip(
                        node.args,
                        (
                            ()
                            if node.func.id == "_copy_raw_value"
                            else expected_parameters
                        ),
                    ):
                        if expected_annotation is None:
                            continue
                        expected_models, expected_tuple_models = (
                            _annotation_provenance(
                                expected_annotation,
                                model_classes,
                            )
                        )
                        expected_scalars, expected_scalar_items = (
                            _annotation_scalar_provenance(expected_annotation)
                        )
                        actual_models, actual_tuple_models = (
                            _expression_model_provenance(
                                argument,
                                model_variables,
                                tuple_model_variables,
                                model_fields,
                                model_classes,
                                function_returns,
                            )
                        )
                        actual_scalars, actual_scalar_items = (
                            _expression_scalar_provenance(
                                argument,
                                scalar_variables,
                                tuple_scalar_variables,
                                model_variables,
                                tuple_model_variables,
                                model_fields,
                                model_classes,
                                function_returns,
                            )
                        )
                        if (
                            isinstance(argument, ast.Name)
                            and argument.id in untyped_provenance
                            and function.name in {
                                "_copy_raw_value", "_canonical_value"
                            }
                        ):
                            guarded_argument_types = _dominating_raw_types(
                                node,
                                parents,
                            )
                            if guarded_argument_types:
                                actual_scalars = guarded_argument_types
                        if (
                            "None" in actual_scalars
                            and "None" not in expected_scalars
                            and _expression_none_is_excluded(
                                node,
                                argument,
                                parents,
                            )
                        ):
                            actual_scalars = actual_scalars - {"None"}
                        expected_provenance_known = bool(
                            expected_models
                            or expected_tuple_models
                            or expected_scalars
                        )
                        actual_provenance_known = bool(
                            actual_models
                            or actual_tuple_models
                            or actual_scalars
                        )
                        argument_valid = (
                            (
                                not expected_provenance_known
                                or actual_provenance_known
                            )
                            and
                            (not actual_models or (
                                bool(expected_models)
                                and actual_models <= expected_models
                            ))
                            and (not actual_tuple_models or (
                                bool(expected_tuple_models)
                                and actual_tuple_models <= expected_tuple_models
                            ))
                            and (not actual_scalars or (
                                bool(expected_scalars)
                                and actual_scalars <= expected_scalars
                                and (
                                    not actual_scalar_items
                                    or actual_scalar_items <= expected_scalar_items
                                )
                            ))
                        )
                        if not argument_valid:
                            errors.append("call_argument_provenance")
                    if (
                        node.func.id == "_blocked_decision"
                        and not _blocked_decision_call_valid(
                            node,
                            allowed_reason_codes,
                        )
                    ):
                        errors.append("blocked_decision_call_semantics")
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id in model_fields
                    and node.func.id != "FrozenKernelModel"
                ):
                    keyword_names = [keyword.arg for keyword in node.keywords]
                    keyword_set = {
                        name for name in keyword_names if name is not None
                    }
                    model_constructor_valid = (
                        not node.args
                        and None not in keyword_names
                        and len(keyword_names) == len(keyword_set)
                        and keyword_set <= set(model_fields[node.func.id])
                        and model_required_fields[node.func.id] <= keyword_set
                    )
                    if not model_constructor_valid:
                        errors.append("model_constructor_signature")
                    if (
                        node.func.id == "KernelDecisionV1"
                        and not _kernel_decision_constructor_valid(
                            node,
                            function.name,
                        )
                    ):
                        errors.append("model_constructor_semantics")
                    for keyword in node.keywords:
                        if (
                            keyword.arg is None
                            or keyword.arg not in model_fields[node.func.id]
                        ):
                            continue
                        field_annotation = model_fields[node.func.id][keyword.arg]
                        expected_models, expected_tuple_models = (
                            _annotation_provenance(
                                field_annotation,
                                model_classes,
                            )
                        )
                        expected_scalars, expected_scalar_items = (
                            _annotation_scalar_provenance(field_annotation)
                        )
                        actual_models, actual_tuple_models = (
                            _expression_model_provenance(
                                keyword.value,
                                model_variables,
                                tuple_model_variables,
                                model_fields,
                                model_classes,
                                function_returns,
                            )
                        )
                        actual_scalars, actual_scalar_items = (
                            _expression_scalar_provenance(
                                keyword.value,
                                scalar_variables,
                                tuple_scalar_variables,
                                model_variables,
                                tuple_model_variables,
                                model_fields,
                                model_classes,
                                function_returns,
                            )
                        )
                        expected_known = bool(
                            expected_models
                            or expected_tuple_models
                            or expected_scalars
                        )
                        actual_known = bool(
                            actual_models
                            or actual_tuple_models
                            or actual_scalars
                        )
                        field_value_valid = (
                            (not expected_known or actual_known)
                            and (not actual_models or (
                                bool(expected_models)
                                and actual_models <= expected_models
                            ))
                            and (not actual_tuple_models or (
                                bool(expected_tuple_models)
                                and actual_tuple_models <= expected_tuple_models
                            ))
                            and (not actual_scalars or (
                                bool(expected_scalars)
                                and actual_scalars <= expected_scalars
                                and (
                                    not actual_scalar_items
                                    or actual_scalar_items <= expected_scalar_items
                                )
                            ))
                        )
                        if not field_value_valid:
                            errors.append("model_constructor_argument_provenance")
                if any(isinstance(argument, ast.Name) and argument.id in (reserved_bindings | {"dict", "list", "ValueError"}) for argument in node.args):
                    if not (isinstance(node.func, ast.Name) and node.func.id in {"isinstance", "issubclass"}):
                        errors.append("callable_argument")
                if isinstance(node.func, ast.Name) and node.func.id == "ConfigDict":
                    errors.append("config_call_outside_class")
                if isinstance(node.func, ast.Name) and node.func.id in {"isinstance", "issubclass"} and len(node.args) == 2:
                    type_operand = node.args[1]
                    valid_type_operand = isinstance(type_operand, ast.Name) and (type_operand.id in module_classes or type_operand.id in {"str", "int", "bool", "bytes", "tuple", "dict", "list", "Decimal", "datetime", "timezone"})
                    if isinstance(type_operand, ast.Tuple):
                        valid_type_operand = all(isinstance(item, ast.Name) and (item.id in module_classes or item.id in {"str", "int", "bool", "bytes", "tuple", "dict", "list", "Decimal", "datetime", "timezone"}) for item in type_operand.elts)
                    if not valid_type_operand:
                        errors.append("type_operand")
                if isinstance(node.func, ast.Name) and node.func.id == "sorted" and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in argument_annotations:
                    if not _annotation_contains(argument_annotations[node.args[0].id], "tuple"):
                        errors.append("sorted_unknown_provenance")
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in {"json", "hashlib"} and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in untyped_provenance:
                    errors.append("hash_or_json_unknown_provenance")
                if function.name == "_copy_raw_value":
                    if isinstance(node.func, ast.Name) and node.func.id in module_functions and node.func.id != "_copy_raw_value":
                        errors.append("raw_external_helper_call")
                    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "value":
                        method_types = {
                            "strip": {"str"},
                            "is_finite": {"Decimal"},
                            "as_tuple": {"Decimal"},
                            "utcoffset": {"datetime"},
                            "items": {"dict"},
                        }
                        if node.func.attr not in method_types or not _dominated_by_raw_type(node, parents, method_types[node.func.attr]):
                            errors.append("raw_receiver_not_dominated")
                    if isinstance(node.func, ast.Name) and node.func.id == "len" and len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id == "value":
                        if not _dominated_by_raw_type(node, parents, {"str", "bytes", "dict", "list", "tuple"}):
                            errors.append("raw_len_not_dominated")
                if function.name == "_canonical_value" and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "value":
                    method_types = {"astimezone": {"datetime"}, "items": {"dict"}}
                    if node.func.attr not in method_types or not _dominated_by_raw_type(node, parents, method_types[node.func.attr]):
                        errors.append("canonical_receiver_not_dominated")
                if isinstance(node.func, ast.Name) and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in untyped_provenance:
                    if node.func.id in {"all", "any", "enumerate", "len", "max", "min", "sorted", "str", "int", "bool", "bytes", "tuple", "zip"}:
                        allowed_untyped = node.func.id == "type" or (function.name == "_copy_raw_value" and node.func.id == "len" and _dominated_by_raw_type(node, parents, {"str", "bytes", "dict", "list", "tuple"}))
                        if not allowed_untyped:
                            errors.append("untyped_parameter_call")
            if isinstance(node, ast.For) and function.name == "_copy_raw_value" and isinstance(node.iter, ast.Name) and node.iter.id == "value":
                if not _dominated_by_raw_type(node, parents, {"list", "tuple"}):
                    errors.append("raw_iteration_not_dominated")
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and node.iter.id in untyped_provenance and function.name != "_copy_raw_value":
                errors.append("untyped_parameter_iteration")
            if isinstance(node, ast.comprehension) and isinstance(node.iter, ast.Name) and node.iter.id in untyped_provenance:
                if function.name == "_canonical_value" and node.iter.id == "value":
                    if not _dominated_by_raw_type(node, parents, {"tuple"}):
                        errors.append("canonical_iteration_not_dominated")
                elif function.name != "_copy_raw_value":
                    errors.append("untyped_parameter_iteration")
            if isinstance(node, ast.Subscript) and function.name == "_copy_raw_value" and isinstance(node.value, ast.Name) and node.value.id == "value":
                if not _dominated_by_raw_type(node, parents, {"dict", "list", "tuple", "str", "bytes"}):
                    errors.append("raw_subscript_not_dominated")
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in untyped_provenance and function.name != "_copy_raw_value":
                annotation = argument_annotations.get(node.value.id)
                if not (_annotation_contains(annotation, "str") or _annotation_contains(annotation, "bytes") or _annotation_contains(annotation, "tuple")):
                    errors.append("unknown_subscript_provenance")
            if isinstance(node, ast.UnaryOp) and not isinstance(node.op, (ast.Not, ast.UAdd, ast.USub)):
                errors.append("unary_operator")
            if (
                isinstance(node, ast.UnaryOp)
                and isinstance(node.op, ast.Not)
                and _bare_untyped_truthiness(
                    node.operand,
                    untyped_provenance | raw_derived_provenance,
                )
            ):
                errors.append("untyped_parameter_truthiness")
            if (
                isinstance(node, (ast.If, ast.IfExp))
                and _bare_untyped_truthiness(
                    node.test,
                    untyped_provenance | raw_derived_provenance,
                )
            ):
                errors.append("untyped_parameter_truthiness")
            if (
                isinstance(node, ast.BoolOp)
                and _bare_untyped_truthiness(
                    node,
                    untyped_provenance | raw_derived_provenance,
                )
            ):
                errors.append("untyped_parameter_truthiness")
            if isinstance(node, ast.comprehension) and any(
                _bare_untyped_truthiness(
                    condition,
                    untyped_provenance | raw_derived_provenance,
                )
                for condition in node.ifs
            ):
                errors.append("untyped_parameter_truthiness")
            if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Name) and node.operand.id in decimal_parameters:
                errors.append("decimal_unary")
            if isinstance(node, ast.BinOp):
                decimal_operand = (isinstance(node.left, ast.Name) and node.left.id in decimal_parameters) or (isinstance(node.right, ast.Name) and node.right.id in decimal_parameters)
                if decimal_operand:
                    errors.append("decimal_arithmetic")
                if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod, ast.BitOr)):
                    errors.append("binary_operator")
                untyped_operand = (isinstance(node.left, ast.Name) and node.left.id in untyped_provenance) or (isinstance(node.right, ast.Name) and node.right.id in untyped_provenance)
                if untyped_operand:
                    errors.append("untyped_parameter_arithmetic")
            if isinstance(node, ast.Compare):
                ordering_operators = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
                invalid_ordering = False
                comparison_operands = (node.left, *node.comparators)
                for index, operator in enumerate(node.ops):
                    if not isinstance(operator, ordering_operators):
                        continue
                    left_operand = comparison_operands[index]
                    right_operand = comparison_operands[index + 1]
                    left_types, _ = _expression_scalar_provenance(
                        left_operand,
                        scalar_variables,
                        tuple_scalar_variables,
                        model_variables,
                        tuple_model_variables,
                        model_fields,
                        model_classes,
                        function_returns,
                    )
                    right_types, _ = _expression_scalar_provenance(
                        right_operand,
                        scalar_variables,
                        tuple_scalar_variables,
                        model_variables,
                        tuple_model_variables,
                        model_fields,
                        model_classes,
                        function_returns,
                    )
                    if (
                        function.name == "_copy_raw_value"
                        and isinstance(left_operand, ast.Name)
                        and left_operand.id in untyped_provenance
                    ):
                        left_types = _dominating_raw_types(node, parents)
                    if (
                        function.name == "_copy_raw_value"
                        and isinstance(right_operand, ast.Name)
                        and right_operand.id in untyped_provenance
                    ):
                        right_types = _dominating_raw_types(node, parents)
                    if "None" in left_types:
                        if _expression_none_is_excluded(
                            node,
                            left_operand,
                            parents,
                        ):
                            left_types = left_types - {"None"}
                        else:
                            invalid_ordering = True
                            continue
                    if "None" in right_types:
                        if _expression_none_is_excluded(
                            node,
                            right_operand,
                            parents,
                        ):
                            right_types = right_types - {"None"}
                        else:
                            invalid_ordering = True
                            continue
                    same_orderable_type = (
                        len(left_types) == 1
                        and left_types == right_types
                        and bool(
                            left_types
                            & {"int", "str", "Decimal", "datetime"}
                        )
                    )
                    decimal_integer_pair = (
                        bool(left_types)
                        and bool(right_types)
                        and left_types <= {"Decimal", "int"}
                        and right_types <= {"Decimal", "int"}
                    )
                    if not same_orderable_type and not decimal_integer_pair:
                        invalid_ordering = True
                if invalid_ordering:
                    errors.append("comparison_provenance")
                compared_names = []
                if isinstance(node.left, ast.Name):
                    compared_names.append(node.left.id)
                compared_names.extend(item.id for item in node.comparators if isinstance(item, ast.Name))
                for name in compared_names:
                    if name in untyped_provenance:
                        identity_compare = all(
                            isinstance(operator, (ast.Is, ast.IsNot))
                            for operator in node.ops
                        )
                        safe_ordering = any(
                            isinstance(operator, ordering_operators)
                            for operator in node.ops
                        ) and not invalid_ordering
                        if not identity_compare and not safe_ordering:
                            errors.append("untyped_parameter_compare")
            if isinstance(node, ast.Raise):
                valid_raise = (
                    function.name == "validate_kernel_input_v1"
                    and isinstance(node.exc, ast.Call)
                    and isinstance(node.exc.func, ast.Name)
                    and node.exc.func.id == "KernelEvidenceValidationError"
                    and len(node.exc.args) == 2
                    and not node.exc.keywords
                    and isinstance(node.exc.args[0], ast.Name)
                    and node.exc.args[0].id == "error_code"
                    and isinstance(node.exc.args[1], ast.Call)
                    and isinstance(node.exc.args[1].func, ast.Name)
                    and node.exc.args[1].func.id == "_sorted_unique"
                    and len(node.exc.args[1].args) == 1
                    and isinstance(node.exc.args[1].args[0], ast.Name)
                    and node.exc.args[1].args[0].id == "all_paths"
                    and not node.exc.args[1].keywords
                    and isinstance(node.cause, ast.Constant)
                    and node.cause.value is None
                )
                if not valid_raise:
                    errors.append("raise_form")
            if isinstance(node, ast.Try):
                valid_try = False
                if function.name in {"validate_kernel_input_v1", "_versions_match"} and not node.orelse and not node.finalbody and len(node.handlers) == 1:
                    expected_exception = "ValidationError" if function.name == "validate_kernel_input_v1" else "ValueError"
                    handler_type = node.handlers[0].type
                    valid_try = isinstance(handler_type, ast.Name) and handler_type.id == expected_exception
                    expected_handler_name = "validation_error" if function.name == "validate_kernel_input_v1" else None
                    valid_try = valid_try and node.handlers[0].name == expected_handler_name
                if function.name == "_copy_raw_value":
                    valid_try = _copy_recursion_try_valid(node)
                if not valid_try:
                    errors.append("try_form")
    return tuple(sorted(errors))


@pytest.mark.parametrize(
    "source",
    [
        'CHECKS = sorted(("b", "a"))',
        'import json\ndef f(cache=json.loads("[]")):\n    return cache',
        'from decimal import DefaultContext',
        'def f(values):\n    return sorted(values, key=open)',
        'import json\ndef f(value):\n    return json.dumps(value, default=eval)',
        'X: int = 1',
        'raise SystemExit(1)',
        'import json\ndef f(value: json.loads("[]")):\n    return value',
        'import json\nclass X(json.loads("[]")):\n    pass',
        'import json\n@json.loads("[]")\ndef f():\n    return 1',
        'import json\njson = 1',
        'import hashlib\ndef f():\n    return hashlib.algorithms_available',
        'def f(callback):\n    return callback()',
        'def f(values):\n    values.append(1)\n    return values',
        'def f(cache=[]):\n    return cache',
        'def f():\n    import json\n    return json',
        'def f():\n    return [1, 2]',
        'class FrozenKernelModel(BaseModel):\n    def mutate(self):\n        return 1',
        'from datetime import datetime\ndef f():\n    return datetime.now()',
        'def f(value):\n    return value.strip()',
        'from decimal import Decimal\ndef f(value: Decimal):\n    return -value',
        'def _copy_raw_value(value, path, field_name, depth, remaining_budget):\n    value_type = type(value)\n    for item in value:\n        return item',
        'from pydantic import ValidationError\ndef validate_kernel_input_v1(raw_snapshot):\n    try:\n        return raw_snapshot\n    except Exception:\n        return None',
        'def f():\n    return 2 ** 10',
        'def f(value):\n    return value[0]',
        'import json\ndef f():\n    return json.dumps',
        'from datetime import datetime, timezone\ndef f():\n    return datetime.max',
        'from pydantic import BaseModel, ConfigDict, ValidationError\ndef f():\n    return ConfigDict(frozen=True)',
        'def f(value):\n    return isinstance(value, value)',
        'def f(value):\n    return sorted(value)',
        'def f(value):\n    for item in value:\n        return item',
        'def f(value):\n    return tuple(item for item in value)',
        'def f(value):\n    return len(value)',
        'def f(value):\n    return value == 1',
        'def f(value: str):\n    return value.replace("x", "y")',
        'from datetime import datetime, timezone\ndef f(value: datetime):\n    return value.astimezone(value)',
        'from datetime import datetime, timezone\ndef f(value: datetime):\n    return value.isoformat(timespec="seconds")',
        'import json\ndef f(value):\n    return json.dumps(value, ensure_ascii=True, allow_nan=True, sort_keys=False, separators=(", ", ": "))',
        'import hashlib\ndef f(value):\n    return hashlib.sha256(value).hexdigest()',
        'def helper():\n    return 1\ndef f():\n    return helper',
        'def f():\n    return ~1',
        'def f(value):\n    return value + 1',
        'def f(value):\n    local = value\n    return local[0]',
        'json = ()\nimport json',
        'def f(value):\n    if value:\n        return 1\n    return 2',
        'def f(value):\n    return not value',
        'def f():\n    "doc"\n    "second expression"\n    return 1',
        'from pydantic import BaseModel, ConfigDict, ValidationError\nclass FrozenKernelModel(BaseModel):\n    model_config = ConfigDict(frozen=False, extra="forbid", strict=True, revalidate_instances="always")',
        'from pydantic import BaseModel, ConfigDict, ValidationError\nclass FrozenKernelModel(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")\n    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")',
        'from pydantic import BaseModel, ConfigDict, ValidationError\nclass FrozenKernelModel(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")\nclass Evidence(FrozenKernelModel):\n    value: int\ndef f(evidence: Evidence):\n    return evidence.missing',
        'from pydantic import BaseModel, ConfigDict, ValidationError\nclass FrozenKernelModel(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")\nclass Evidence(FrozenKernelModel):\n    value: int\ndef f(evidence: Evidence):\n    return evidence.items()',
        'def f(value):\n    return value',
        'def f(value):\n    return (value,)',
        'def f(value):\n    return {"safe": value}',
        'def helper(value):\n    return 1\ndef f(value):\n    return helper(value)',
        'def len(value):\n    return 1',
        'def f(value: str):\n    return value.as_tuple()',
        'def f(value: str):\n    return value.missing',
        'from pydantic import BaseModel, ConfigDict, ValidationError\nclass FrozenKernelModel(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")\nclass Evidence(FrozenKernelModel):\n    value: str\ndef f(evidence: Evidence):\n    return evidence.value.items()',
        'from pydantic import BaseModel, ConfigDict, ValidationError\nclass FrozenKernelModel(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")\nclass Evidence(FrozenKernelModel):\n    value: str\ndef f(evidence: Evidence):\n    return evidence.value.missing',
        'from pydantic import BaseModel, ConfigDict, ValidationError\nclass FrozenKernelModel(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")\nclass Evidence(FrozenKernelModel):\n    model_config: str = "x"',
        'from pydantic import BaseModel, ConfigDict, ValidationError\nclass FrozenKernelModel(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")\nclass Evidence(FrozenKernelModel):\n    value: int\n    value: str',
        'def f():\n    return undeclared_name',
        'def f(values: tuple):\n    first, *rest = values\n    return first',
        'from pydantic import BaseModel, ConfigDict, ValidationError\ndef validate_kernel_input_v1(raw_snapshot):\n    try:\n        return 1\n    except ValidationError as json:\n        return None',
        '__name__ = "quantpilot.packages.core.execution.kernel"',
        '__doc__ = "Side-effect-free execution evidence validation and eligibility decisions."',
        '__package__ = "quantpilot.packages.core.execution"',
        'def f() -> tuple[str, ...]:\n    return 1',
        'from pydantic import BaseModel, ConfigDict, ValidationError\nclass FrozenKernelModel(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, revalidate_instances="always")\nclass Evidence(FrozenKernelModel):\n    value: int\ndef helper() -> Evidence:\n    return Evidence(value=1)\ndef f():\n    return helper().missing',
    ],
)
def test_static_purity_checker_rejects_binding_regressions(source: str) -> None:
    assert _purity_errors(source)


def test_kernel_source_uses_only_the_closed_import_and_statement_boundary() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    assert _purity_errors(source) == ()


def test_recursion_handler_cannot_embed_raw_input_in_sanitized_findings() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    mutated = source.replace(
        'child_findings = (("schema", child_path),)',
        'child_findings = (("schema", value),)',
        1,
    )
    assert mutated != source
    assert "try_form" in _purity_errors(mutated)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            "raw_item,\n                            child_path,\n                            raw_key,",
            "raw_item,\n                            raw_item,\n                            raw_key,",
        ),
        (
            "depth + 1,\n                            remaining_budget,\n                            child_ancestors,",
            "depth + 1,\n                            MAX_RAW_NODES,\n                            child_ancestors,",
        ),
        (
            "remaining_budget,\n                            child_ancestors,",
            "remaining_budget,\n                            (),",
        ),
    ],
)
def test_recursion_handler_binds_all_six_recursive_arguments(
    before: str,
    after: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "try_form" in _purity_errors(mutated)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            'raw_item,\n                    path + "[]",\n                    field_name,',
            'raw_item,\n                    raw_item,\n                    field_name,',
        ),
        (
            "depth + 1,\n                    remaining_budget,\n                    child_ancestors,",
            "depth + 1,\n                    MAX_RAW_NODES,\n                    child_ancestors,",
        ),
        (
            "remaining_budget,\n                    child_ancestors,",
            "remaining_budget,\n                    (),",
        ),
    ],
)
def test_sequence_recursion_handler_binds_all_six_recursive_arguments(
    before: str,
    after: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    sequence_start = source.index("    if value_type is list or value_type is tuple:")
    sequence_source = source[sequence_start:]
    assert before in sequence_source
    mutated = source[:sequence_start] + sequence_source.replace(before, after, 1)
    assert "try_form" in _purity_errors(mutated)


def test_raw_type_dominance_requires_every_possible_branch_type_to_be_safe() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    mutated = source.replace(
        "if value_type is str:",
        "if value_type is str or value_type is dict:",
        1,
    )
    assert mutated != source
    assert _purity_errors(mutated)


@pytest.mark.parametrize("raw_type", ["str", "dict"])
def test_ordering_comparisons_require_compatible_exact_raw_types(
    raw_type: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = f"    if value_type is {raw_type}:\n"
    injected = marker + "        probe = value < 0\n"
    assert marker in source
    mutated = source.replace(marker, injected, 1)
    assert "comparison_provenance" in _purity_errors(mutated)


@pytest.mark.parametrize(
    "replacement",
    [
        "    probe = value < 0\n    if value is None or value <= 0:",
        "    if value <= 0 or value is None:",
    ],
)
def test_optional_ordering_requires_a_preceding_non_none_proof(
    replacement: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = "    if value is None or value <= 0:"
    assert marker in source
    mutated = source.replace(marker, replacement, 1)
    assert "comparison_provenance" in _purity_errors(mutated)


def test_optional_module_function_argument_guard_must_precede_the_call() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = (
        "authorization.recipe_version is None or not "
        "_versions_match(binding.strategy_version, authorization.recipe_version)"
    )
    after = (
        "_versions_match(binding.strategy_version, authorization.recipe_version) "
        "or authorization.recipe_version is None"
    )
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "call_argument_provenance" in _purity_errors(mutated)


@pytest.mark.parametrize(
    ("replacement", "expected_error"),
    [
        (
            "_versions_match(binding.strategy_version)",
            "module_call_signature",
        ),
        (
            "_versions_match(binding.strategy_version, "
            "authorization.recipe_version, authorization.recipe_version)",
            "module_call_signature",
        ),
        (
            "_versions_match(left=binding.strategy_version, "
            "bogus=authorization.recipe_version)",
            "module_call_signature",
        ),
        (
            "_versions_match((item for item in ()), "
            "authorization.recipe_version)",
            "call_argument_provenance",
        ),
        (
            "_versions_match({item: item for item in ()}, "
            "authorization.recipe_version)",
            "call_argument_provenance",
        ),
    ],
)
def test_module_function_calls_bind_exact_signature_and_argument_provenance(
    replacement: str,
    expected_error: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    call = "_versions_match(binding.strategy_version, authorization.recipe_version)"
    assert call in source
    mutated = source.replace(call, replacement, 1)
    assert expected_error in _purity_errors(mutated)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            "        order_plan_id=evidence.candidate.order_plan_id,\n",
            "",
        ),
        (
            "        schema_version=1,\n",
            "        schema_version=1,\n        bogus=1,\n",
        ),
        (
            "        schema_version=1,\n",
            "        1,\n",
        ),
    ],
)
def test_model_constructor_calls_bind_required_and_declared_fields(
    before: str,
    after: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "model_constructor_signature" in _purity_errors(mutated)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("        schema_version=1,\n", '        schema_version="1",\n'),
        (
            "        reason_codes=_sorted_unique(reasons),\n",
            "        reason_codes=(item for item in ()),\n",
        ),
    ],
)
def test_model_constructor_field_values_match_declared_provenance(
    before: str,
    after: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "model_constructor_argument_provenance" in _purity_errors(mutated)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('        verdict="blocked",\n', '        verdict="evil",\n'),
        (
            '        verdict="blocked",\n',
            '        verdict="eligible_for_legacy_submit",\n',
        ),
        (
            "        reason_codes=_sorted_unique(reasons),\n",
            '        reason_codes=("bogus",),\n',
        ),
        ("        schema_version=1,\n", "        schema_version=2,\n"),
    ],
)
def test_kernel_decision_constructor_semantics_are_exact(
    before: str,
    after: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "model_constructor_semantics" in _purity_errors(mutated)


@pytest.mark.parametrize(
    "replacement",
    [
        "raise KernelEvidenceValidationError() from None",
        "raise KernelEvidenceValidationError(error_code) from None",
        (
            "raise KernelEvidenceValidationError(error_code, "
            "_sorted_unique(all_paths), all_paths) from None"
        ),
        (
            "raise KernelEvidenceValidationError(code=error_code, "
            "paths=_sorted_unique(all_paths)) from None"
        ),
        "raise KernelEvidenceValidationError(error_code, all_paths) from None",
    ],
)
def test_validation_error_constructor_keeps_the_exact_sanitized_envelope(
    replacement: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = (
        "raise KernelEvidenceValidationError("
        "error_code, _sorted_unique(all_paths)) from None"
    )
    mutated = source.replace(before, replacement, 1)
    assert "raise_form" in _purity_errors(mutated)


def test_validation_error_cannot_embed_validation_exception_text() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = "    except ValidationError as validation_error:\n"
    injected = marker + "        leak = str(validation_error)\n"
    before = (
        "raise KernelEvidenceValidationError("
        "error_code, _sorted_unique(all_paths)) from None"
    )
    after = "raise KernelEvidenceValidationError(error_code, leak) from None"
    mutated = source.replace(marker, injected, 1).replace(before, after, 1)
    assert "raise_form" in _purity_errors(mutated)


def test_blocked_decision_call_stage_is_a_closed_literal() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = '_blocked_decision(evidence, "identity", reasons, fingerprint, False)'
    after = '_blocked_decision(evidence, "evil", reasons, fingerprint, False)'
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "blocked_decision_call_semantics" in _purity_errors(mutated)


def test_decision_requirement_values_have_closed_provenance() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = '            durable_requirement = "required"'
    after = '            durable_requirement = "evil"'
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "decision_requirement_provenance" in _purity_errors(mutated)


def test_reason_helpers_only_accumulate_declared_decision_codes() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = 'reasons = reasons + ("policy_identity_mismatch",)'
    after = 'reasons = reasons + ("evil",)'
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "reason_provenance" in _purity_errors(mutated)


def test_evaluation_flow_binds_identity_stage_to_identity_reason_helper() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = "    reasons = _identity_reasons(evidence)"
    after = "    reasons = _capability_reasons(evidence)"
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "evaluation_flow" in _purity_errors(mutated)


def test_authorization_reasons_bind_strategy_helper_at_its_declared_site() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = "    reasons = reasons + _strategy_binding_reasons(evidence)"
    after = "    reasons = reasons + _capability_reasons(evidence)"
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "reason_provenance" in _purity_errors(mutated)


def test_evaluation_flow_rejects_reason_rebinding_before_blocked_decision() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = (
        "    if len(reasons) > 0:\n"
        "        return _blocked_decision("
        "evidence, \"identity\", reasons, fingerprint, False)"
    )
    after = (
        "    if len(reasons) > 0:\n"
        "        reasons = (\"evil\",)\n"
        "        return _blocked_decision("
        "evidence, \"identity\", reasons, fingerprint, False)"
    )
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "evaluation_flow" in _purity_errors(mutated)


def test_evaluation_flow_rejects_invalid_eligible_requirement_rebinding() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = (
        '        reservation_requirement = "not_required"\n'
        "    return KernelDecisionV1(\n"
    )
    after = before.replace(
        "    return KernelDecisionV1(\n",
        '    durable_requirement = "evil"\n    return KernelDecisionV1(\n',
    )
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "evaluation_flow" in _purity_errors(mutated)


def test_validation_findings_cannot_be_rebound_to_unsanitized_tree_text() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = (
        "    all_findings = preflight_findings + "
        "pydantic_findings + timestamp_findings\n"
    )
    after = before + '    all_findings = (("schema", str(validation_tree)),)\n'
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "validation_finding_provenance" in _purity_errors(mutated)


def test_reviewed_ast_rejects_reason_helper_control_flow_reordering() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    authorization = "    reasons = _authorization_reasons(evidence)\n"
    identity = "    reasons = _identity_reasons(evidence)\n"
    assert source.count(authorization) == 1
    assert source.count(identity) == 1
    mutated = source.replace(authorization, "", 1).replace(
        identity,
        identity + authorization,
        1,
    )
    assert "reviewed_kernel_ast" in _purity_errors(mutated)


def test_reviewed_ast_rejects_kis_requirement_branch_inversion() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = (
        '    if evidence.capability.profile_id == "kis_paper_v1":\n'
        '        durable_requirement = "required"\n'
        '        reservation_requirement = "required"\n'
        "    else:\n"
        '        durable_requirement = "not_required"\n'
        '        reservation_requirement = "not_required"\n'
    )
    after = (
        '    if evidence.capability.profile_id == "kis_paper_v1":\n'
        '        durable_requirement = "not_required"\n'
        '        reservation_requirement = "not_required"\n'
        "    else:\n"
        '        durable_requirement = "required"\n'
        '        reservation_requirement = "required"\n'
    )
    assert source.count(before) == 1
    mutated = source.replace(before, after, 1)
    assert "reviewed_kernel_ast" in _purity_errors(mutated)


def test_reviewed_ast_rejects_capability_guard_inversion() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = (
        "    reasons = _capability_reasons(evidence)\n"
        "    if len(reasons) > 0:\n"
    )
    after = before.replace("> 0", "< 0")
    assert source.count(before) == 1
    mutated = source.replace(before, after, 1)
    assert "reviewed_kernel_ast" in _purity_errors(mutated)


def test_reviewed_ast_rejects_early_reason_helper_return() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = "    reasons = reasons + _strategy_binding_reasons(evidence)\n"
    after = "    return _sorted_unique(reasons)\n" + before
    assert source.count(before) == 1
    mutated = source.replace(before, after, 1)
    assert "reviewed_kernel_ast" in _purity_errors(mutated)


def test_reviewed_ast_rejects_stale_validation_finding_aggregation() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    timestamp = (
        "    timestamp_findings = _validated_timestamp_findings(validated)\n"
    )
    all_findings = (
        "    all_findings = preflight_findings + "
        "pydantic_findings + timestamp_findings\n"
    )
    anchor = (
        "    try:\n"
        "        validated = KernelEvaluationInputV1.model_validate(validation_tree)\n"
    )
    assert source.count(timestamp) == 1
    assert source.count(all_findings) == 1
    assert source.count(anchor) == 1
    mutated = source.replace(timestamp, "", 1).replace(all_findings, "", 1)
    mutated = mutated.replace(
        anchor,
        timestamp + all_findings + anchor,
        1,
    )
    assert "reviewed_kernel_ast" in _purity_errors(mutated)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            '                path = "$extra"\n',
            "                path = str(validation_tree)\n",
        ),
        (
            "    timestamp_paths = _finding_paths(preflight_findings, \"timestamp\")\n",
            '    preflight_findings = (("schema", str(validation_tree)),)\n'
            "    timestamp_paths = _finding_paths(preflight_findings, \"timestamp\")\n",
        ),
        (
            "        for finding_kind, finding_path in all_findings:\n",
            "        for finding_kind, finding_path in all_findings:\n"
            "            finding_path = str(validation_tree)\n",
        ),
    ],
)
def test_reviewed_ast_rejects_unsanitized_validation_path_rebinding(
    before: str,
    after: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    assert source.count(before) == 1
    mutated = source.replace(before, after, 1)
    assert "reviewed_kernel_ast" in _purity_errors(mutated)


def test_exact_error_envelope_cannot_hide_upstream_validation_text_rebinding() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    handler = "    except ValidationError as validation_error:\n"
    raise_line = (
        "        raise KernelEvidenceValidationError("
        "error_code, _sorted_unique(all_paths)) from None"
    )
    mutated = source.replace(
        handler,
        handler + "        leak = str(validation_error)\n",
        1,
    ).replace(
        raise_line,
        "        all_paths = (leak,)\n" + raise_line,
        1,
    )
    errors = _purity_errors(mutated)
    assert "validation_error_use" in errors
    assert "validation_path_provenance" in errors


@pytest.mark.parametrize(
    "before",
    [
        '            error_code = "naive_or_invalid_timestamp"',
        '            error_code = "invalid_evidence_schema"',
    ],
)
def test_validation_error_codes_are_the_exact_closed_pair(before: str) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    indentation = before[: len(before) - len(before.lstrip())]
    mutated = source.replace(before, indentation + 'error_code = "evil"', 1)
    assert "validation_error_code_provenance" in _purity_errors(mutated)


@pytest.mark.parametrize(
    "injected_statement",
    [
        "probe = str(raw_item)",
        "if raw_item:\n                probe = 1",
        "path = raw_item",
    ],
)
def test_raw_container_items_remain_tainted_until_recursive_copy(
    injected_statement: str,
) -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = "        for raw_item in value:\n            try:"
    injected = "        for raw_item in value:\n            " + injected_statement + "\n            try:"
    assert marker in source
    mutated = source.replace(marker, injected, 1)
    assert _purity_errors(mutated)


def test_raw_dictionary_keys_remain_tainted_until_exact_str_classification() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = (
        "        for raw_key, raw_item in value.items():\n"
        "            raw_key_type = type(raw_key)"
    )
    injected = (
        "        for raw_key, raw_item in value.items():\n"
        "            probe = str(raw_key)\n"
        "            raw_key_type = type(raw_key)"
    )
    assert marker in source
    mutated = source.replace(marker, injected, 1)
    assert "raw_derived_value_use" in _purity_errors(mutated)


def test_exact_type_classification_cannot_launder_a_raw_sequence_item() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = "        for raw_item in value:\n            try:"
    injected = (
        "        for raw_item in value:\n"
        "            raw_item_type = type(raw_item)\n"
        "            if raw_item_type is str:\n"
        '                findings = findings + (("schema", str(raw_item)),)\n'
        "            try:"
    )
    assert marker in source
    mutated = source.replace(marker, injected, 1)
    assert "raw_derived_value_use" in _purity_errors(mutated)


def test_known_key_membership_only_sanitizes_dictionary_key_origins() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = "        for raw_item in value:\n            try:"
    injected = (
        "        for raw_item in value:\n"
        "            raw_item_type = type(raw_item)\n"
        "            if raw_item_type is str:\n"
        "                if raw_item in KNOWN_RAW_KEYS:\n"
        '                    findings = findings + (("schema", raw_item),)\n'
        "            try:"
    )
    mutated = source.replace(marker, injected, 1)
    assert "raw_derived_value_use" in _purity_errors(mutated)


def test_raw_sequence_item_taint_propagates_through_expression_aliases() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = "        for raw_item in value:\n            try:"
    injected = (
        "        for raw_item in value:\n"
        "            alias = (raw_item,)[0]\n"
        '            findings = findings + (("schema", alias),)\n'
        "            try:"
    )
    assert marker in source
    mutated = source.replace(marker, injected, 1)
    assert "raw_derived_value_use" in _purity_errors(mutated)


def test_raw_container_cannot_be_returned_instead_of_the_detached_copy() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = (
        "return {key: item for key, item in copied_pairs}, "
        "remaining_budget, findings"
    )
    after = "return value, remaining_budget, findings"
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "raw_container_return" in _purity_errors(mutated)


def test_raw_container_alias_cannot_be_returned_as_a_detached_copy() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = "        copied_pairs = ()\n        findings = ()"
    injected = "        alias = value\n" + marker
    before = (
        "return {key: item for key, item in copied_pairs}, "
        "remaining_budget, findings"
    )
    after = "return alias, remaining_budget, findings"
    assert marker in source
    assert before in source
    mutated = source.replace(marker, injected, 1).replace(before, after, 1)
    assert "raw_container_return" in _purity_errors(mutated)


def test_raw_container_expression_alias_cannot_escape_detachment() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = "        copied_pairs = ()\n        findings = ()"
    injected = "        alias = (value,)[0]\n" + marker
    before = (
        "return {key: item for key, item in copied_pairs}, "
        "remaining_budget, findings"
    )
    after = "return alias, remaining_budget, findings"
    mutated = source.replace(marker, injected, 1).replace(before, after, 1)
    assert "raw_container_return" in _purity_errors(mutated)


def test_raw_value_expression_alias_cannot_escape_from_the_fallback_return() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = "    value_type = type(value)\n"
    assert marker in source
    mutated = source.replace(marker, marker + "    alias = (value,)[0]\n", 1)
    fallback = '    return None, remaining_budget, (("schema", path),)'
    fallback_index = mutated.rfind(fallback)
    assert fallback_index >= 0
    mutated = (
        mutated[:fallback_index]
        + "    return alias, remaining_budget, findings"
        + mutated[fallback_index + len(fallback) :]
    )
    assert "raw_container_return" in _purity_errors(mutated)


def test_scalar_return_provenance_rejects_incompatible_binary_operands() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    before = 'return path + "." + key'
    after = "return path + None"
    assert before in source
    mutated = source.replace(before, after, 1)
    assert "return_provenance" in _purity_errors(mutated)


def test_boolean_return_provenance_rejects_model_or_false() -> None:
    source = (
        "from pydantic import BaseModel, ConfigDict, ValidationError\n"
        "class FrozenKernelModel(BaseModel):\n"
        '    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, '
        'revalidate_instances="always")\n'
        "class Evidence(FrozenKernelModel):\n"
        "    value: int\n"
        "def f(evidence: Evidence) -> bool:\n"
        "    return evidence or False"
    )
    assert "return_provenance" in _purity_errors(source)


def test_tuple_return_provenance_rejects_nested_model_values() -> None:
    source = (
        "from pydantic import BaseModel, ConfigDict, ValidationError\n"
        "class FrozenKernelModel(BaseModel):\n"
        '    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, '
        'revalidate_instances="always")\n'
        "class Evidence(FrozenKernelModel):\n"
        "    value: int\n"
        "def f(evidence: Evidence) -> tuple[str, ...]:\n"
        "    return (evidence,)"
    )
    assert "return_provenance" in _purity_errors(source)


def test_every_recursive_call_must_be_inside_one_of_the_two_exact_handlers() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    copy_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_copy_raw_value"
    )
    dict_try = min(
        (node for node in ast.walk(copy_function) if isinstance(node, ast.Try)),
        key=lambda node: node.lineno,
    )
    recursive_assignment = dict_try.body[0]
    assert isinstance(recursive_assignment, ast.Assign)
    lines = source.splitlines(keepends=True)
    assignment_lines = lines[
        recursive_assignment.lineno - 1 : recursive_assignment.end_lineno
    ]
    unprotected = "".join(line[4:] for line in assignment_lines)
    lines.insert(dict_try.lineno - 1, unprotected)
    mutated = "".join(lines)
    assert "recursion_call_count" in _purity_errors(mutated)


def test_recursive_handlers_have_exact_dict_and_sequence_multiplicity() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    copy_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_copy_raw_value"
    )
    dict_try = min(
        (node for node in ast.walk(copy_function) if isinstance(node, ast.Try)),
        key=lambda node: node.lineno,
    )
    lines = source.splitlines(keepends=True)
    try_block = "".join(lines[dict_try.lineno - 1 : dict_try.end_lineno])
    lines.insert(dict_try.end_lineno, try_block)
    mutated = "".join(lines)
    errors = _purity_errors(mutated)
    assert "recursion_call_count" in errors
    assert "recursion_try_count" in errors
    assert "recursion_try_placement" in errors


def test_recursive_try_placement_is_disjoint_and_covers_every_handler() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    copy_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_copy_raw_value"
    )
    dict_try, sequence_try = sorted(
        (node for node in ast.walk(copy_function) if isinstance(node, ast.Try)),
        key=lambda node: node.lineno,
    )
    lines = source.splitlines(keepends=True)
    sequence_block = lines[sequence_try.lineno - 1 : sequence_try.end_lineno]
    del lines[sequence_try.lineno - 1 : sequence_try.end_lineno]

    dict_length = dict_try.end_lineno - dict_try.lineno + 1
    dict_block = lines[dict_try.lineno - 1 : dict_try.lineno - 1 + dict_length]
    nested_dict_block = [
        "                    if value_type is list or value_type is tuple:\n"
    ] + ["    " + line for line in dict_block]
    lines[
        dict_try.lineno - 1 : dict_try.lineno - 1 + dict_length
    ] = nested_dict_block

    fallback_index = next(
        index
        for index, line in enumerate(lines)
        if line == '    return None, remaining_budget, (("schema", path),)\n'
        and index > dict_try.lineno
    )
    unplaced_sequence_block = [line[8:] for line in sequence_block]
    lines[fallback_index:fallback_index] = unplaced_sequence_block
    mutated = "".join(lines)

    assert "recursion_try_placement" in _purity_errors(mutated)


def test_recursive_argument_variant_must_match_its_container_branch() -> None:
    tree = ast.parse(KERNEL_PATH.read_text(encoding="utf-8"))
    copy_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_copy_raw_value"
    )
    dict_try = min(
        (node for node in ast.walk(copy_function) if isinstance(node, ast.Try)),
        key=lambda node: node.lineno,
    )
    assignment = dict_try.body[0]
    assert isinstance(assignment, ast.Assign)
    assert isinstance(assignment.value, ast.Call)
    assignment.value.args[1] = ast.parse('path + "[]"', mode="eval").body
    assignment.value.args[2] = ast.Name(id="field_name", ctx=ast.Load())
    finding_assignment = dict_try.handlers[0].body[2]
    assert isinstance(finding_assignment, ast.Assign)
    finding_assignment.value = ast.parse(
        '(("schema", path + "[]"),)',
        mode="eval",
    ).body
    mutated = ast.unparse(ast.fix_missing_locations(tree))
    assert "recursion_try_placement" in _purity_errors(mutated)


def test_recursive_try_must_remain_inside_its_container_iteration() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    copy_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_copy_raw_value"
    )
    sequence_try = max(
        (node for node in ast.walk(copy_function) if isinstance(node, ast.Try)),
        key=lambda node: node.lineno,
    )
    lines = source.splitlines(keepends=True)
    sequence_block = lines[sequence_try.lineno - 1 : sequence_try.end_lineno]
    del lines[sequence_try.lineno - 1 : sequence_try.end_lineno]
    return_index = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("        return copied_items, remaining_budget, findings")
    )
    outside_loop_block = [line[4:] for line in sequence_block]
    lines[return_index:return_index] = outside_loop_block
    mutated = "".join(lines)
    assert "recursion_try_placement" in _purity_errors(mutated)


def test_declared_helper_return_type_is_checked_against_every_return() -> None:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    marker = 'def _finding_paths(findings: tuple[tuple[str, str], ...], kind: str) -> tuple[str, ...]:'
    assert marker in source
    start = source.index(marker)
    return_index = source.index("    return _sorted_unique(paths)", start)
    mutated = (
        source[:return_index]
        + "    return 1"
        + source[return_index + len("    return _sorted_unique(paths)") :]
    )
    assert "return_provenance" in _purity_errors(mutated)


def _immutable_runtime(value: object) -> bool:
    if value is None or type(value) in {bool, int, str, bytes}:
        return True
    if type(value) is tuple:
        return all(_immutable_runtime(item) for item in value)
    return False


def _runtime_values_exact(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is tuple:
        return len(left) == len(right) and all(
            _runtime_values_exact(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    if type(left) is dict:
        return set(left) == set(right) and all(
            _runtime_values_exact(left[key], right[key]) for key in left
        )
    return left == right


def _runtime_constant_from_ast(
    node: ast.AST,
    prior_constants: dict[str, object],
) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Tuple):
        return tuple(
            _runtime_constant_from_ast(item, prior_constants) for item in node.elts
        )
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) is int
    ):
        if isinstance(node.op, ast.UAdd):
            return node.operand.value
        return -node.operand.value
    if isinstance(node, ast.Name) and node.id in prior_constants:
        return prior_constants[node.id]
    raise AssertionError("non-constant AST reached runtime purity backstop")


def _runtime_global_errors(module: ModuleType) -> tuple[str, ...]:
    source = KERNEL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    class_names = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    constant_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    function_nodes = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    expected_constant_values: dict[str, object] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            expected_constant_values[node.targets[0].id] = _runtime_constant_from_ast(
                node.value,
                expected_constant_values,
            )
    expected_imports: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                expected_imports[alias.asname or alias.name] = importlib.import_module(alias.name)
        if isinstance(node, ast.ImportFrom):
            source_module = importlib.import_module(node.module)
            for alias in node.names:
                expected_imports[alias.asname or alias.name] = getattr(source_module, alias.name)
    metadata = {
        "__name__", "__doc__", "__package__", "__loader__", "__spec__",
        "__file__", "__cached__", "__builtins__",
    }
    errors: list[str] = []
    namespace = vars(module)
    if "__annotations__" in vars(module):
        errors.append("__annotations__")
    runtime_name = namespace.get("__name__")
    runtime_doc = namespace.get("__doc__")
    runtime_package = namespace.get("__package__")
    runtime_loader = namespace.get("__loader__")
    runtime_spec = namespace.get("__spec__")
    runtime_file = namespace.get("__file__")
    runtime_cached = namespace.get("__cached__")
    expected_name = "quantpilot.packages.core.execution.kernel"
    expected_doc = ast.get_docstring(tree, clean=False)
    expected_package = expected_name.rpartition(".")[0]
    expected_file = str(KERNEL_PATH.resolve())
    expected_cached = importlib.util.cache_from_source(expected_file)
    if type(runtime_name) is not str or runtime_name != expected_name:
        errors.append("__name__")
    if type(runtime_doc) is not str or runtime_doc != expected_doc:
        errors.append("__doc__")
    if type(runtime_package) is not str or runtime_package != expected_package:
        errors.append("__package__")
    if type(runtime_file) is not str or runtime_file != expected_file:
        errors.append("__file__")
    if type(runtime_cached) is not str or runtime_cached != expected_cached:
        errors.append("__cached__")
    if type(runtime_spec) is not ModuleSpec:
        errors.append("__spec__")
    else:
        if runtime_loader is not runtime_spec.loader:
            errors.append("__loader__")
        if type(runtime_spec.name) is not str or runtime_spec.name != expected_name:
            errors.append("__spec__")
        else:
            if runtime_spec.parent != expected_package:
                errors.append("__spec__")
        if type(runtime_spec.origin) is not str or runtime_spec.origin != expected_file:
            errors.append("__spec__")
    builtins_module = importlib.import_module("builtins")
    runtime_builtins = vars(module).get("__builtins__")
    loaded_builtin_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and hasattr(builtins_module, node.id)
    }
    if runtime_builtins is not builtins_module:
        if type(runtime_builtins) is not dict:
            errors.append("__builtins__")
        else:
            for name in loaded_builtin_names:
                if runtime_builtins.get(name) is not getattr(builtins_module, name):
                    errors.append("__builtins__")
    for name, value in vars(module).items():
        if name in metadata:
            continue
        if name in expected_imports:
            if value is not expected_imports[name]:
                errors.append(name)
            continue
        if name in function_names:
            if not inspect.isfunction(value):
                errors.append(name)
            else:
                function_node = function_nodes[name]
                expected_defaults = tuple(
                    _runtime_constant_from_ast(default, expected_constant_values)
                    for default in function_node.args.defaults
                )
                runtime_defaults = value.__defaults__
                if expected_defaults:
                    if (
                        type(runtime_defaults) is not tuple
                        or not _immutable_runtime(runtime_defaults)
                        or not _runtime_values_exact(
                            runtime_defaults,
                            expected_defaults,
                        )
                    ):
                        errors.append(name)
                elif runtime_defaults is not None:
                    errors.append(name)
                expected_kwdefaults = {
                    argument.arg: _runtime_constant_from_ast(
                        default,
                        expected_constant_values,
                    )
                    for argument, default in zip(
                        function_node.args.kwonlyargs,
                        function_node.args.kw_defaults,
                    )
                    if default is not None
                }
                runtime_kwdefaults = value.__kwdefaults__
                if expected_kwdefaults:
                    if (
                        type(runtime_kwdefaults) is not dict
                        or set(runtime_kwdefaults) != set(expected_kwdefaults)
                        or any(
                            type(key) is not str or not _immutable_runtime(item)
                            for key, item in runtime_kwdefaults.items()
                        )
                        or not _runtime_values_exact(
                            runtime_kwdefaults,
                            expected_kwdefaults,
                        )
                    ):
                        errors.append(name)
                elif runtime_kwdefaults is not None:
                    errors.append(name)
            continue
        if name in class_names:
            if not inspect.isclass(value):
                errors.append(name)
            continue
        if name in constant_names:
            if not _immutable_runtime(value):
                errors.append(name)
            continue
        if name not in function_names | class_names | constant_names:
            errors.append(name)
    return tuple(sorted(errors))


def test_runtime_global_scan_rejects_injected_mutable_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _kernel()
    assert _runtime_global_errors(module) == ()
    monkeypatch.setattr(module, "INJECTED_REGISTRY", [], raising=False)
    assert _runtime_global_errors(module) == ("INJECTED_REGISTRY",)


def test_runtime_global_scan_rejects_rebound_import_and_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _kernel()
    original_builtins = vars(module)["__builtins__"]
    monkeypatch.setattr(module, "json", object())
    assert "json" in _runtime_global_errors(module)
    monkeypatch.undo()

    monkeypatch.setitem(vars(module), "__builtins__", {"len": len})
    assert "__builtins__" in _runtime_global_errors(module)
    monkeypatch.setitem(vars(module), "__builtins__", original_builtins)


def test_runtime_global_scan_rejects_malformed_interpreter_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _kernel()
    monkeypatch.setattr(module, "__name__", object())
    assert "__name__" in _runtime_global_errors(module)


@pytest.mark.parametrize(
    "metadata_name",
    ["__doc__", "__package__", "__file__", "__cached__"],
)
def test_runtime_global_scan_requires_source_derived_metadata(
    metadata_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _kernel()
    monkeypatch.setattr(module, metadata_name, None)
    assert metadata_name in _runtime_global_errors(module)


def test_runtime_global_scan_rejects_coherent_fake_or_hostile_module_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _kernel()
    fake_spec = ModuleSpec("evil.module", loader=None, origin=str(KERNEL_PATH.resolve()))
    with monkeypatch.context() as scoped:
        scoped.setattr(module, "__name__", "evil.module")
        scoped.setattr(module, "__package__", "evil")
        scoped.setattr(module, "__loader__", None)
        scoped.setattr(module, "__spec__", fake_spec)
        errors = _runtime_global_errors(module)
        assert "__name__" in errors
        assert "__package__" in errors
        assert "__spec__" in errors

    class HostileSpec(ModuleSpec):
        @property
        def name(self):
            raise RuntimeError("must not be evaluated")

    hostile_spec = object.__new__(HostileSpec)
    monkeypatch.setattr(module, "__spec__", hostile_spec)
    assert "__spec__" in _runtime_global_errors(module)


def test_runtime_global_scan_requires_ast_exact_function_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _kernel()
    function = module._expected_kind
    with monkeypatch.context() as scoped:
        scoped.setattr(function, "__defaults__", (1,))
        assert "_expected_kind" in _runtime_global_errors(module)
    with monkeypatch.context() as scoped:
        scoped.setattr(function, "__kwdefaults__", {"bogus": 1})
        assert "_expected_kind" in _runtime_global_errors(module)
