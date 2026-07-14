"""Side-effect-free execution evidence validation and eligibility decisions."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, ValidationError


MAX_RAW_DEPTH = 64
MAX_RAW_NODES = 50000
MAX_RAW_CONTAINER_ITEMS = 10000
MAX_RAW_TEXT_CHARS = 65536
MAX_RAW_BYTES = 65536
MAX_RAW_ABS_INT = 9223372036854775807
MAX_DECIMAL_DIGITS = 128
MAX_DECIMAL_ABS_EXPONENT = 128

SIMULATED_CHECKS = (
    "simulated_execution_only",
    "mock_profile_required",
)
DIRECT_LEVEL3_CHECKS = (
    "local_approval_transition_recorded",
    "order_state_approved",
)
TICKET_LEVEL3_CHECKS = (
    "ticket_status_approved",
    "ticket_time_valid",
    "ticket_identity_match",
    "ticket_data_mode_match",
    "order_state_approved",
)
GUARDED_LEVEL4_CHECKS = (
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
AUTOMATED_LEVEL5_CHECKS = (
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
FINAL_SAFETY_CHECKS = (
    "policy_version_match",
    "kill_switch_not_engaged",
    "live_trading_disabled",
    "operator_kill_switch_not_engaged",
    "operator_not_paused",
    "broker_health",
)
NONNEGATIVE_INT_KEYS = (
    "schema_version",
    "policy_version",
    "authority_algorithm_version",
    "ticket_policy_version",
    "registry_min_policy_version",
    "registry_max_policy_version",
    "snapshot_fingerprint_schema_version",
    "submit_market_quote_fingerprint_schema_version",
    "guardrail_fingerprint_schema_version",
    "current_policy_version",
    "paper_run_fingerprint_schema_version",
    "run_policy_version",
    "quote_fingerprint_schema_version",
    "fencing_token",
    "session_revision",
)
TIMESTAMP_PATHS = (
    "evaluated_at",
    "candidate.intent.quote_time",
    "candidate.risk_check_expires_at",
    "candidate.order_expires_at",
    "authorization.evaluated_at",
    "authorization.approval_transition_at",
    "authorization.requested_at",
    "authorization.approved_at",
    "authorization.expires_at",
    "single_risk.created_at",
    "single_risk.expires_at",
    "single_risk.snapshot_captured_at",
    "single_risk.submit_market_quote_as_of",
    "final_safety.captured_at",
    "paper.snapshot_captured_at",
    "paper.snapshot_deadline",
    "paper.quote_as_of",
    "paper.quote_deadline",
    "paper.session_lease_deadline",
)
BLANK_ALLOWED_PATHS = ("candidate.intent.reason",)
KNOWN_RAW_KEYS = (
    "schema_version",
    "observation_phase",
    "candidate",
    "authorization",
    "single_risk",
    "batch_risk",
    "final_safety",
    "context",
    "paper",
    "capability",
    "evaluated_at",
    "order_plan_id",
    "intent",
    "policy_id",
    "policy_version",
    "purpose",
    "status",
    "idempotency_key",
    "risk_check_id",
    "risk_check_expires_at",
    "approved_by",
    "order_expires_at",
    "strategy_binding",
    "intent_id",
    "symbol",
    "side",
    "order_type",
    "quantity",
    "limit_price",
    "notional",
    "target_weight",
    "reason",
    "quote_time",
    "strategy_id",
    "strategy_version",
    "kind",
    "evaluation_state",
    "authority_algorithm_version",
    "source",
    "authorized",
    "policy_user_id",
    "assurance",
    "checks",
    "first_failed_check",
    "name",
    "passed",
    "detail_code",
    "simulation_reference",
    "approval_transition_source",
    "approval_transition_at",
    "authenticated_subject_id",
    "authentication_reference",
    "ticket_id",
    "ticket_user_id",
    "ticket_policy_id",
    "ticket_policy_version",
    "ticket_order_plan_id",
    "ticket_data_mode",
    "ticket_status",
    "requested_at",
    "approved_at",
    "expires_at",
    "approved_by_label",
    "authentication_assurance",
    "recipe_strategy_id",
    "recipe_version",
    "promotion_status",
    "allowed_execution_levels",
    "operator_run_id",
    "registry_strategy_id",
    "registry_version",
    "registry_spec_hash",
    "registry_status",
    "registry_allowed_execution_levels",
    "registry_min_policy_version",
    "registry_max_policy_version",
    "lifecycle_strategy_id",
    "lifecycle_version",
    "lifecycle_status",
    "lifecycle_spec_hash",
    "snapshot_user_id",
    "created_at",
    "passed_checks",
    "failed_checks",
    "snapshot_id",
    "snapshot_captured_at",
    "snapshot_fingerprint_schema_version",
    "snapshot_fingerprint",
    "submit_market_quote_symbol",
    "submit_market_quote_as_of",
    "submit_market_quote_fingerprint_schema_version",
    "submit_market_quote_fingerprint",
    "guardrail_fingerprint_schema_version",
    "guardrail_fingerprint",
    "reservation_state",
    "mode",
    "accepted_order_plan_ids",
    "captured_at",
    "policy_snapshot_current",
    "policy_kill_switch_engaged",
    "live_trading_enabled",
    "operator_kill_switch_engaged",
    "autopilot_paused",
    "broker_healthy",
    "data_mode",
    "run_mode",
    "market_orders_enabled",
    "current_policy_id",
    "current_policy_version",
    "external_paper_enabled",
    "paper_run_id",
    "paper_run_fingerprint_schema_version",
    "paper_run_fingerprint",
    "run_user_id",
    "run_policy_id",
    "run_policy_version",
    "checkpoint_status",
    "snapshot_deadline",
    "quote_symbol",
    "quote_as_of",
    "quote_fingerprint_schema_version",
    "quote_fingerprint",
    "quote_deadline",
    "entry_atr14",
    "store_id",
    "account_scope_fingerprint",
    "session_id",
    "fencing_token",
    "session_revision",
    "session_status",
    "session_lease_deadline",
    "profile_id",
)


class FrozenKernelModel(BaseModel):
    """Strict immutable base for all kernel evidence and decisions."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        revalidate_instances="always",
    )


class KernelEvidenceValidationError(ValueError):
    """Sanitized structural validation failure."""


class KernelAuthorityCheckV1(FrozenKernelModel):
    name: Literal[
        "simulated_execution_only",
        "mock_profile_required",
        "local_approval_transition_recorded",
        "order_state_approved",
        "ticket_status_approved",
        "ticket_time_valid",
        "ticket_identity_match",
        "ticket_data_mode_match",
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
        "fully_automated_operator_enabled",
        "live_trading_disabled",
        "operator_not_paused",
        "authority_level_5",
        "snapshot_not_stale",
        "risk_reducing_purpose_verified",
        "strategy_registry_validated_l5",
        "strategy_recipe_matches_registry",
    ]
    passed: bool
    detail_code: Literal[
        "simulated_execution_only",
        "mock_profile_required",
        "local_approval_transition_recorded",
        "order_state_approved",
        "ticket_status_approved",
        "ticket_time_valid",
        "ticket_identity_match",
        "ticket_data_mode_match",
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
        "fully_automated_operator_enabled",
        "live_trading_disabled",
        "operator_not_paused",
        "authority_level_5",
        "snapshot_not_stale",
        "risk_reducing_purpose_verified",
        "strategy_registry_validated_l5",
        "strategy_recipe_matches_registry",
    ]


class KernelIntentSnapshotV1(FrozenKernelModel):
    intent_id: str
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"]
    quantity: Decimal
    limit_price: Decimal | None
    notional: Decimal
    target_weight: Decimal | None
    reason: str
    quote_time: datetime


class KernelStrategyBindingV1(FrozenKernelModel):
    strategy_id: str
    strategy_version: str
    symbol: str
    side: Literal["buy", "sell"]
    policy_version: int


class KernelOrderCandidateV1(FrozenKernelModel):
    order_plan_id: str
    intent: KernelIntentSnapshotV1
    policy_id: str
    policy_version: int
    purpose: Literal["rebalance", "protective_exit", "strategy_retirement"]
    status: Literal[
        "draft",
        "risk_checked",
        "proposed",
        "modified",
        "user_approved",
        "submitted",
        "accepted",
        "partially_filled",
        "filled",
        "cancelled",
        "rejected",
        "expired",
        "failed",
    ]
    idempotency_key: str
    risk_check_id: str | None
    risk_check_expires_at: datetime | None
    approved_by: str | None
    order_expires_at: datetime | None
    strategy_binding: KernelStrategyBindingV1 | None


class AuthorizationEvidenceV1(FrozenKernelModel):
    kind: Literal[
        "simulated_level_1_2",
        "human_direct_level_3",
        "human_ticket_level_3",
        "guarded_level_4",
        "automated_level_5",
        "professional_risk_reduction",
    ]
    evaluation_state: Literal["passed", "failed", "not_evaluated"]
    authority_algorithm_version: int
    source: Literal[
        "simulated_harness",
        "level3_direct_transition",
        "level3_ticket",
        "guarded_authority_v1",
        "level5_authority_v1",
        "professional_authority_v1",
    ]
    authorized: bool | None
    policy_id: str
    policy_version: int
    policy_user_id: str
    assurance: Literal[
        "simulated",
        "unverified_local",
        "authenticated_subject",
        "policy_authorized",
        "operator_authorized",
    ]
    evaluated_at: datetime
    checks: tuple[KernelAuthorityCheckV1, ...]
    first_failed_check: Literal[
        "simulated_execution_only",
        "mock_profile_required",
        "local_approval_transition_recorded",
        "order_state_approved",
        "ticket_status_approved",
        "ticket_time_valid",
        "ticket_identity_match",
        "ticket_data_mode_match",
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
        "fully_automated_operator_enabled",
        "live_trading_disabled",
        "operator_not_paused",
        "authority_level_5",
        "snapshot_not_stale",
        "risk_reducing_purpose_verified",
        "strategy_registry_validated_l5",
        "strategy_recipe_matches_registry",
    ] | None
    simulation_reference: str | None = None
    approval_transition_source: str | None = None
    approval_transition_at: datetime | None = None
    authenticated_subject_id: str | None = None
    authentication_reference: str | None = None
    ticket_id: str | None = None
    ticket_user_id: str | None = None
    ticket_policy_id: str | None = None
    ticket_policy_version: int | None = None
    ticket_order_plan_id: str | None = None
    ticket_data_mode: Literal[
        "fixture",
        "local_historical",
        "external_historical",
        "realtime_market_data",
        "paper_trading",
        "live_trading_candidate",
        "live_canary",
        "live_scaled",
    ] | None = None
    ticket_status: Literal[
        "pending", "approved", "rejected", "expired", "submitted", "blocked"
    ] | None = None
    requested_at: datetime | None = None
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    approved_by_label: str | None = None
    authentication_assurance: Literal[
        "none", "caller_label_only", "authenticated_session"
    ] | None = None
    recipe_strategy_id: str | None = None
    recipe_version: str | None = None
    promotion_status: Literal[
        "draft", "approved", "validated_l3", "validated_l4", "revoked"
    ] | None = None
    allowed_execution_levels: tuple[str, ...] | None = None
    operator_run_id: str | None = None
    registry_strategy_id: str | None = None
    registry_version: str | None = None
    registry_spec_hash: str | None = None
    registry_status: Literal[
        "draft", "validated_l3", "validated_l4", "validated_l5", "disabled", "revoked"
    ] | None = None
    registry_allowed_execution_levels: tuple[str, ...] | None = None
    registry_min_policy_version: int | None = None
    registry_max_policy_version: int | None = None
    lifecycle_strategy_id: str | None = None
    lifecycle_version: str | None = None
    lifecycle_status: Literal[
        "draft",
        "backtested",
        "paper_candidate",
        "paper_validated",
        "live_candidate",
        "disabled",
        "revoked",
    ] | None = None
    lifecycle_spec_hash: str | None = None


class SingleRiskEvidenceV1(FrozenKernelModel):
    evaluation_state: Literal["passed", "failed", "not_evaluated"]
    risk_check_id: str | None = None
    order_plan_id: str | None = None
    passed: bool | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    policy_user_id: str | None = None
    snapshot_user_id: str | None = None
    idempotency_key: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    passed_checks: tuple[str, ...] | None = None
    failed_checks: tuple[str, ...] | None = None
    snapshot_id: str | None = None
    snapshot_captured_at: datetime | None = None
    snapshot_fingerprint_schema_version: int | None = None
    snapshot_fingerprint: str | None = None
    submit_market_quote_symbol: str | None = None
    submit_market_quote_as_of: datetime | None = None
    submit_market_quote_fingerprint_schema_version: int | None = None
    submit_market_quote_fingerprint: str | None = None
    guardrail_fingerprint_schema_version: int | None = None
    guardrail_fingerprint: str | None = None
    reservation_state: Literal[
        "none", "required_not_prepared", "prepared_authoritatively"
    ] | None = None


class BatchRiskEvidenceV1(FrozenKernelModel):
    evaluation_state: Literal["passed", "failed", "not_evaluated"]
    passed: bool | None = None
    mode: Literal["full_batch", "partial_batch", "rejected"] | None = None
    policy_version: int | None = None
    accepted_order_plan_ids: tuple[str, ...] | None = None
    failed_checks: tuple[str, ...] | None = None


class FinalSafetyEvidenceV1(FrozenKernelModel):
    evaluation_state: Literal["passed", "failed", "not_evaluated"]
    captured_at: datetime | None = None
    policy_snapshot_current: bool | None = None
    policy_kill_switch_engaged: bool | None = None
    live_trading_enabled: bool | None = None
    operator_kill_switch_engaged: bool | None = None
    autopilot_paused: bool | None = None
    broker_healthy: bool | None = None
    failed_checks: tuple[
        Literal[
            "policy_version_match",
            "kill_switch_not_engaged",
            "live_trading_disabled",
            "operator_kill_switch_not_engaged",
            "operator_not_paused",
            "broker_health",
        ],
        ...,
    ] | None = None


class ExecutionContextSnapshotV1(FrozenKernelModel):
    data_mode: Literal[
        "fixture",
        "local_historical",
        "external_historical",
        "realtime_market_data",
        "paper_trading",
        "live_trading_candidate",
        "live_canary",
        "live_scaled",
    ]
    run_mode: Literal[
        "level_1_2_mock",
        "level_3_direct",
        "level_3_ticket",
        "guarded_level_4",
        "operator_mock_submit",
        "operator_paper_submit",
        "professional_risk_reduction",
    ]
    market_orders_enabled: bool
    current_policy_id: str
    current_policy_version: int
    external_paper_enabled: bool
    policy_user_id: str
    operator_run_id: str | None


class PaperSubmissionEvidenceV1(FrozenKernelModel):
    evaluation_state: Literal["passed", "failed", "not_evaluated", "not_applicable"]
    paper_run_id: str | None = None
    paper_run_fingerprint_schema_version: int | None = None
    paper_run_fingerprint: str | None = None
    run_user_id: str | None = None
    run_policy_id: str | None = None
    run_policy_version: int | None = None
    run_mode: Literal[
        "level_1_2_mock",
        "level_3_direct",
        "level_3_ticket",
        "guarded_level_4",
        "operator_mock_submit",
        "operator_paper_submit",
        "professional_risk_reduction",
    ] | None = None
    data_mode: Literal[
        "fixture",
        "local_historical",
        "external_historical",
        "realtime_market_data",
        "paper_trading",
        "live_trading_candidate",
        "live_canary",
        "live_scaled",
    ] | None = None
    checkpoint_status: Literal["started", "completed", "blocked", "failed"] | None = None
    snapshot_id: str | None = None
    snapshot_captured_at: datetime | None = None
    snapshot_fingerprint_schema_version: int | None = None
    snapshot_fingerprint: str | None = None
    snapshot_deadline: datetime | None = None
    quote_symbol: str | None = None
    quote_as_of: datetime | None = None
    quote_fingerprint_schema_version: int | None = None
    quote_fingerprint: str | None = None
    quote_deadline: datetime | None = None
    entry_atr14: Decimal | Literal["none"] | None = None
    store_id: str | None = None
    account_scope_fingerprint: str | None = None
    session_id: str | None = None
    fencing_token: int | None = None
    session_revision: int | None = None
    session_status: Literal["active", "closed", "abandoned"] | None = None
    session_lease_deadline: datetime | None = None


class CapabilityEvidenceV1(FrozenKernelModel):
    evaluation_state: Literal["passed", "failed", "not_evaluated"]
    profile_id: Literal["mock_v1", "simulated_paper_v1", "kis_paper_v1"] | None = None


class KernelEvaluationInputV1(FrozenKernelModel):
    schema_version: Literal[1]
    observation_phase: Literal[
        "authorization_failure",
        "external_paper_input_failure",
        "candidate_failure",
        "single_risk_failure",
        "batch_risk_failure",
        "final_safety_failure",
        "capability_failure",
        "ready_to_submit",
    ]
    candidate: KernelOrderCandidateV1
    authorization: AuthorizationEvidenceV1
    single_risk: SingleRiskEvidenceV1
    batch_risk: BatchRiskEvidenceV1
    final_safety: FinalSafetyEvidenceV1
    context: ExecutionContextSnapshotV1
    paper: PaperSubmissionEvidenceV1
    capability: CapabilityEvidenceV1
    evaluated_at: datetime


class KernelDecisionV1(FrozenKernelModel):
    schema_version: Literal[1]
    order_plan_id: str
    verdict: Literal["eligible_for_legacy_submit", "blocked"]
    blocked_stage: Literal[
        "identity",
        "candidate",
        "authorization",
        "risk",
        "final_safety",
        "paper_evidence",
        "capability",
        "none",
    ]
    reason_codes: tuple[
        Literal[
            "order_identity_mismatch",
            "policy_identity_mismatch",
            "policy_version_mismatch",
            "order_not_user_approved",
            "order_expiry_missing",
            "order_expired",
            "risk_check_missing",
            "prior_risk_check_expired",
            "evidence_prefix_mismatch",
            "authorization_denied",
            "authorization_evidence_mismatch",
            "authorization_kind_mismatch",
            "execution_mode_mismatch",
            "actor_assurance_missing",
            "ticket_expired",
            "strategy_binding_missing",
            "strategy_binding_mismatch",
            "strategy_authority_mismatch",
            "lifecycle_binding_mismatch",
            "operator_run_mismatch",
            "professional_binding_not_supported",
            "risk_check_mismatch",
            "risk_check_expired",
            "risk_evidence_not_evaluated",
            "risk_quote_mismatch",
            "single_order_risk_failed",
            "batch_risk_failed",
            "batch_order_not_accepted",
            "partial_batch_not_allowed_at_submit",
            "future_evidence_timestamp",
            "policy_snapshot_changed",
            "live_trading_enabled",
            "policy_kill_switch_engaged",
            "operator_kill_switch_engaged",
            "autopilot_paused",
            "broker_unhealthy",
            "paper_evidence_mismatch",
            "paper_strategy_binding_missing",
            "checkpoint_status_invalid",
            "paper_session_status_invalid",
            "data_mode_mismatch",
            "broker_environment_mismatch",
            "market_order_disabled",
            "quantity_step_mismatch",
            "price_step_mismatch",
            "account_provenance_missing",
            "paper_session_fence_missing",
            "broker_capability_mismatch",
        ],
        ...,
    ]
    durable_prepare_requirement: Literal["not_evaluated", "not_required", "required"]
    atomic_reservation_requirement: Literal["not_evaluated", "not_required", "required"]
    intended_next_stage: Literal["none", "legacy_submit_handoff"]
    evaluated_at: datetime
    evidence_fingerprint: str


def _path_for_key(path: str, key: str) -> str:
    if path == "$":
        return key
    return path + "." + key


def _copy_raw_value(
    value,
    path: str,
    field_name: str,
    depth: int,
    remaining_budget: int,
    ancestors: tuple,
):
    value_type = type(value)
    if depth > MAX_RAW_DEPTH:
        return None, remaining_budget, (("schema", path),)
    if remaining_budget <= 0:
        return None, remaining_budget, (("schema", path),)
    remaining_budget = remaining_budget - 1
    if value is None:
        return None, remaining_budget, ()
    if path in TIMESTAMP_PATHS and value_type is not datetime:
        return None, remaining_budget, (("timestamp", path),)
    if value_type is bool:
        return value, remaining_budget, ()
    if value_type is int:
        if value < -MAX_RAW_ABS_INT or value > MAX_RAW_ABS_INT:
            return None, remaining_budget, (("schema", path),)
        if field_name in NONNEGATIVE_INT_KEYS and value < 0:
            return None, remaining_budget, (("schema", path),)
        return value, remaining_budget, ()
    if value_type is str:
        if len(value) > MAX_RAW_TEXT_CHARS or (value.strip() == "" and path not in BLANK_ALLOWED_PATHS):
            return None, remaining_budget, (("schema", path),)
        return value, remaining_budget, ()
    if value_type is bytes:
        if len(value) > MAX_RAW_BYTES:
            return None, remaining_budget, (("schema", path),)
        return value, remaining_budget, ()
    if value_type is Decimal:
        if not value.is_finite():
            return None, remaining_budget, (("schema", path),)
        decimal_parts = value.as_tuple()
        decimal_exponent = decimal_parts.exponent
        if len(decimal_parts.digits) > MAX_DECIMAL_DIGITS:
            return None, remaining_budget, (("schema", path),)
        if decimal_exponent < -MAX_DECIMAL_ABS_EXPONENT or decimal_exponent > MAX_DECIMAL_ABS_EXPONENT:
            return None, remaining_budget, (("schema", path),)
        return value, remaining_budget, ()
    if value_type is datetime:
        value_timezone = value.tzinfo
        if type(value_timezone) is not timezone:
            return None, remaining_budget, (("timestamp", path),)
        if value.utcoffset() is None:
            return None, remaining_budget, (("timestamp", path),)
        return value, remaining_budget, ()
    if value_type is dict:
        cycle_found = False
        for ancestor in ancestors:
            if value is ancestor:
                cycle_found = True
        if cycle_found:
            return None, remaining_budget, (("schema", path),)
        if len(value) > MAX_RAW_CONTAINER_ITEMS:
            return None, remaining_budget, (("schema", path),)
        child_ancestors = ancestors + (value,)
        copied_pairs = ()
        findings = ()
        for raw_key, raw_item in value.items():
            raw_key_type = type(raw_key)
            if raw_key_type is not str:
                findings = findings + (("schema", "$extra"),)
            else:
                if raw_key not in KNOWN_RAW_KEYS:
                    findings = findings + (("schema", "$extra"),)
                else:
                    if path == "$":
                        child_path = raw_key
                    else:
                        child_path = path + "." + raw_key
                    try:
                        copied_item, remaining_budget, child_findings = _copy_raw_value(
                            raw_item,
                            child_path,
                            raw_key,
                            depth + 1,
                            remaining_budget,
                            child_ancestors,
                        )
                    except RecursionError:
                        copied_item = None
                        remaining_budget = 0
                        child_findings = (("schema", child_path),)
                    copied_pairs = copied_pairs + ((raw_key, copied_item),)
                    findings = findings + child_findings
        return {key: item for key, item in copied_pairs}, remaining_budget, findings
    if value_type is list or value_type is tuple:
        cycle_found = False
        for ancestor in ancestors:
            if value is ancestor:
                cycle_found = True
        if cycle_found:
            return None, remaining_budget, (("schema", path),)
        if len(value) > MAX_RAW_CONTAINER_ITEMS:
            return None, remaining_budget, (("schema", path),)
        child_ancestors = ancestors + (value,)
        copied_items = ()
        findings = ()
        for raw_item in value:
            try:
                copied_item, remaining_budget, child_findings = _copy_raw_value(
                    raw_item,
                    path + "[]",
                    field_name,
                    depth + 1,
                    remaining_budget,
                    child_ancestors,
                )
            except RecursionError:
                copied_item = None
                remaining_budget = 0
                child_findings = (("schema", path + "[]"),)
            copied_items = copied_items + (copied_item,)
            findings = findings + child_findings
        return copied_items, remaining_budget, findings
    return None, remaining_budget, (("schema", path),)


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    ordered = tuple(sorted(values))
    result = ()
    previous = None
    for value in ordered:
        if value != previous:
            result = result + (value,)
            previous = value
    return result


def _finding_paths(findings: tuple[tuple[str, str], ...], kind: str) -> tuple[str, ...]:
    paths = ()
    for finding_kind, finding_path in findings:
        if finding_kind == kind:
            paths = paths + (finding_path,)
    return _sorted_unique(paths)


def _sanitize_location(location: tuple[str | int, ...]) -> str:
    path = ""
    valid = True
    for component in location:
        component_type = type(component)
        if component_type is int:
            if path == "":
                path = "[]"
            else:
                path = path + "[]"
        else:
            if component_type is str and component in KNOWN_RAW_KEYS:
                if path == "":
                    path = str(component)
                else:
                    path = path + "." + component
            else:
                valid = False
    if not valid or path == "":
        return "$extra"
    return path


def _timestamp_findings(value: datetime | None, path: str):
    if value is None:
        return ()
    if type(value) is not datetime:
        return (("timestamp", path),)
    value_timezone = value.tzinfo
    if type(value_timezone) is not timezone or value.utcoffset() is None:
        return (("timestamp", path),)
    return ()


def _validated_timestamp_findings(evidence: KernelEvaluationInputV1 | None):
    if evidence is None:
        return ()
    findings = _timestamp_findings(evidence.evaluated_at, "evaluated_at")
    findings = findings + _timestamp_findings(evidence.candidate.intent.quote_time, "candidate.intent.quote_time")
    findings = findings + _timestamp_findings(evidence.candidate.risk_check_expires_at, "candidate.risk_check_expires_at")
    findings = findings + _timestamp_findings(evidence.candidate.order_expires_at, "candidate.order_expires_at")
    findings = findings + _timestamp_findings(evidence.authorization.evaluated_at, "authorization.evaluated_at")
    findings = findings + _timestamp_findings(evidence.authorization.approval_transition_at, "authorization.approval_transition_at")
    findings = findings + _timestamp_findings(evidence.authorization.requested_at, "authorization.requested_at")
    findings = findings + _timestamp_findings(evidence.authorization.approved_at, "authorization.approved_at")
    findings = findings + _timestamp_findings(evidence.authorization.expires_at, "authorization.expires_at")
    findings = findings + _timestamp_findings(evidence.single_risk.created_at, "single_risk.created_at")
    findings = findings + _timestamp_findings(evidence.single_risk.expires_at, "single_risk.expires_at")
    findings = findings + _timestamp_findings(evidence.single_risk.snapshot_captured_at, "single_risk.snapshot_captured_at")
    findings = findings + _timestamp_findings(evidence.single_risk.submit_market_quote_as_of, "single_risk.submit_market_quote_as_of")
    findings = findings + _timestamp_findings(evidence.final_safety.captured_at, "final_safety.captured_at")
    findings = findings + _timestamp_findings(evidence.paper.snapshot_captured_at, "paper.snapshot_captured_at")
    findings = findings + _timestamp_findings(evidence.paper.snapshot_deadline, "paper.snapshot_deadline")
    findings = findings + _timestamp_findings(evidence.paper.quote_as_of, "paper.quote_as_of")
    findings = findings + _timestamp_findings(evidence.paper.quote_deadline, "paper.quote_deadline")
    findings = findings + _timestamp_findings(evidence.paper.session_lease_deadline, "paper.session_lease_deadline")
    return findings


def validate_kernel_input_v1(raw_snapshot):
    """Detach hostile input and return strict frozen evidence or one safe error."""

    detached, remaining_budget, preflight_findings = _copy_raw_value(
        raw_snapshot,
        "$",
        "$",
        0,
        MAX_RAW_NODES,
        (),
    )
    if type(detached) is dict:
        validation_tree = detached
    else:
        validation_tree = {}
        preflight_findings = preflight_findings + (("schema", "$"),)
    timestamp_paths = _finding_paths(preflight_findings, "timestamp")
    validated = None
    pydantic_findings = ()
    try:
        validated = KernelEvaluationInputV1.model_validate(validation_tree)
    except ValidationError as validation_error:
        error_rows = validation_error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        for error_row in error_rows:
            location = error_row["loc"]
            if error_row["type"] == "extra_forbidden":
                path = "$extra"
            else:
                path = _sanitize_location(location)
            if path not in timestamp_paths:
                pydantic_findings = pydantic_findings + (("schema", path),)
    timestamp_findings = _validated_timestamp_findings(validated)
    all_findings = preflight_findings + pydantic_findings + timestamp_findings
    if len(all_findings) > 0:
        all_paths = ()
        all_timestamp = True
        for finding_kind, finding_path in all_findings:
            all_paths = all_paths + (finding_path,)
            if finding_kind != "timestamp":
                all_timestamp = False
        if all_timestamp:
            error_code = "naive_or_invalid_timestamp"
        else:
            error_code = "invalid_evidence_schema"
        raise KernelEvidenceValidationError(error_code, _sorted_unique(all_paths)) from None
    return validated


def _canonical_decimal(value: Decimal) -> str:
    parts = value.as_tuple()
    digits = parts.digits
    exponent = parts.exponent
    last_nonzero = -1
    for index, digit in enumerate(digits):
        if digit != 0:
            last_nonzero = index
    if last_nonzero < 0:
        return "0"
    removed = len(digits) - last_nonzero - 1
    canonical_digits = digits[: last_nonzero + 1]
    exponent = exponent + removed
    digit_text = "".join(str(digit) for digit in canonical_digits)
    if exponent >= 0:
        result = digit_text + "0" * exponent
    else:
        decimal_position = len(digit_text) + exponent
        if decimal_position <= 0:
            result = "0." + "0" * -decimal_position + digit_text
        else:
            result = digit_text[:decimal_position] + "." + digit_text[decimal_position:]
    if parts.sign == 1:
        result = "-" + result
    return result


def _canonical_value(value):
    value_type = type(value)
    if value is None or value_type is bool or value_type is int or value_type is str:
        return value
    if value_type is Decimal:
        return _canonical_decimal(value)
    if value_type is datetime:
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00",
            "Z",
        )
    if value_type is tuple:
        return tuple(_canonical_value(item) for item in value)
    if value_type is dict:
        return {key: _canonical_value(item) for key, item in value.items()}
    return None


def _canonical_sha256(value) -> str:
    canonical = _canonical_value(value)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded)
    return "sha256:" + digest.hexdigest()


def _evidence_fingerprint(evidence: KernelEvaluationInputV1) -> str:
    dumped = evidence.model_dump(mode="python")
    return _canonical_sha256(dumped)


def _valid_sha256(value: str | None) -> bool:
    if value is None or len(value) != 71 or value[:7] != "sha256:":
        return False
    valid = True
    for character in value[7:]:
        if character not in "0123456789abcdef":
            valid = False
    return valid


def _paper_run_fingerprint(paper: PaperSubmissionEvidenceV1) -> str:
    projection = {
        "paper_run_id": paper.paper_run_id,
        "run_user_id": paper.run_user_id,
        "run_policy_id": paper.run_policy_id,
        "run_policy_version": paper.run_policy_version,
        "run_mode": paper.run_mode,
        "data_mode": paper.data_mode,
        "checkpoint_status": paper.checkpoint_status,
    }
    return _canonical_sha256(projection)


def _versions_match(left: str, right: str) -> bool:
    left_trimmed = left.strip()
    right_trimmed = right.strip()
    left_parts = tuple(left_trimmed.split("."))
    right_parts = tuple(right_trimmed.split("."))
    left_numeric_path = len(left_parts) > 0 and all(part.isdigit() for part in left_parts)
    right_numeric_path = len(right_parts) > 0 and all(part.isdigit() for part in right_parts)
    if not left_numeric_path or not right_numeric_path:
        return left_trimmed == right_trimmed
    left_numbers = ()
    right_numbers = ()
    conversion_valid = True
    try:
        for part in left_parts:
            converted = ""
            for character in part:
                converted = converted + str(int(character))
            first_nonzero = len(converted)
            for index, character in enumerate(converted):
                if first_nonzero == len(converted) and character != "0":
                    first_nonzero = index
            if first_nonzero == len(converted):
                converted = "0"
            else:
                converted = converted[first_nonzero:]
            left_numbers = left_numbers + (converted,)
        for part in right_parts:
            converted = ""
            for character in part:
                converted = converted + str(int(character))
            first_nonzero = len(converted)
            for index, character in enumerate(converted):
                if first_nonzero == len(converted) and character != "0":
                    first_nonzero = index
            if first_nonzero == len(converted):
                converted = "0"
            else:
                converted = converted[first_nonzero:]
            right_numbers = right_numbers + (converted,)
    except ValueError:
        conversion_valid = False
    if not conversion_valid:
        return False
    left_last_nonzero = -1
    right_last_nonzero = -1
    for index, number in enumerate(left_numbers):
        if number != "0":
            left_last_nonzero = index
    for index, number in enumerate(right_numbers):
        if number != "0":
            right_last_nonzero = index
    return left_numbers[: left_last_nonzero + 1] == right_numbers[: right_last_nonzero + 1]


def _prefix_valid(evidence: KernelEvaluationInputV1) -> bool:
    phase = evidence.observation_phase
    authorization_state = evidence.authorization.evaluation_state
    paper_state = evidence.paper.evaluation_state
    single_state = evidence.single_risk.evaluation_state
    batch_state = evidence.batch_risk.evaluation_state
    final_state = evidence.final_safety.evaluation_state
    capability_state = evidence.capability.evaluation_state
    suffix_payloads_valid = _unevaluated_payloads_absent(evidence)
    if not suffix_payloads_valid:
        return False
    if phase == "candidate_failure" and len(_candidate_reasons(evidence)) == 0:
        return False
    if phase == "final_safety_failure":
        expected_failed = ()
        if evidence.final_safety.policy_snapshot_current is not True:
            expected_failed = expected_failed + ("policy_version_match",)
        if evidence.final_safety.policy_kill_switch_engaged is not False:
            expected_failed = expected_failed + ("kill_switch_not_engaged",)
        if evidence.final_safety.live_trading_enabled is not False:
            expected_failed = expected_failed + ("live_trading_disabled",)
        if evidence.final_safety.operator_kill_switch_engaged is not False:
            expected_failed = expected_failed + ("operator_kill_switch_not_engaged",)
        if evidence.final_safety.autopilot_paused is not False:
            expected_failed = expected_failed + ("operator_not_paused",)
        if evidence.final_safety.broker_healthy is not True:
            expected_failed = expected_failed + ("broker_health",)
        timestamp_failed = evidence.final_safety.captured_at is None or evidence.final_safety.captured_at > evidence.evaluated_at
        actual_failure = timestamp_failed or len(expected_failed) > 0
        if not actual_failure or evidence.final_safety.failed_checks != expected_failed:
            return False
    paper_passed = paper_state == "passed" or paper_state == "not_applicable"
    if phase == "authorization_failure":
        return authorization_state == "failed" and paper_state == "not_evaluated" and single_state == "not_evaluated" and batch_state == "not_evaluated" and final_state == "not_evaluated" and capability_state == "not_evaluated"
    if phase == "external_paper_input_failure":
        return authorization_state == "passed" and paper_state == "failed" and single_state == "not_evaluated" and batch_state == "not_evaluated" and final_state == "not_evaluated" and capability_state == "not_evaluated"
    if phase == "candidate_failure":
        return authorization_state == "passed" and paper_passed and single_state == "not_evaluated" and batch_state == "not_evaluated" and final_state == "not_evaluated" and capability_state == "not_evaluated"
    if phase == "single_risk_failure":
        return authorization_state == "passed" and paper_passed and single_state == "failed" and batch_state == "not_evaluated" and final_state == "not_evaluated" and capability_state == "not_evaluated"
    if phase == "batch_risk_failure":
        return authorization_state == "passed" and paper_passed and single_state == "passed" and batch_state == "failed" and final_state == "not_evaluated" and capability_state == "not_evaluated"
    if phase == "final_safety_failure":
        return authorization_state == "passed" and paper_passed and single_state == "passed" and batch_state == "passed" and final_state == "failed" and capability_state == "not_evaluated"
    if phase == "capability_failure":
        return authorization_state == "passed" and paper_passed and single_state == "passed" and batch_state == "passed" and final_state == "passed" and capability_state == "failed"
    return authorization_state == "passed" and paper_passed and single_state == "passed" and batch_state == "passed" and final_state == "passed" and capability_state == "passed"


def _expected_kind(run_mode: str) -> str:
    if run_mode == "level_1_2_mock":
        return "simulated_level_1_2"
    if run_mode == "level_3_direct":
        return "human_direct_level_3"
    if run_mode == "level_3_ticket":
        return "human_ticket_level_3"
    if run_mode == "guarded_level_4":
        return "guarded_level_4"
    if run_mode == "professional_risk_reduction":
        return "professional_risk_reduction"
    return "automated_level_5"


def _expected_checks(kind: str) -> tuple[str, ...]:
    if kind == "simulated_level_1_2":
        return SIMULATED_CHECKS
    if kind == "human_direct_level_3":
        return DIRECT_LEVEL3_CHECKS
    if kind == "human_ticket_level_3":
        return TICKET_LEVEL3_CHECKS
    if kind == "guarded_level_4":
        return GUARDED_LEVEL4_CHECKS
    return AUTOMATED_LEVEL5_CHECKS


def _authorization_checks_valid(authorization: AuthorizationEvidenceV1) -> bool:
    expected = _expected_checks(authorization.kind)
    names = tuple(check.name for check in authorization.checks)
    details_valid = all(check.detail_code == check.name for check in authorization.checks)
    if authorization.authority_algorithm_version != 1 or not details_valid:
        return False
    if authorization.evaluation_state == "passed":
        return authorization.authorized is True and names == expected and all(check.passed for check in authorization.checks) and authorization.first_failed_check is None
    if authorization.evaluation_state == "failed":
        if authorization.authorized is not False or len(authorization.checks) == 0:
            return False
        prefix_valid = names == expected[: len(names)]
        preceding_valid = all(check.passed for check in authorization.checks[:-1])
        last_check = authorization.checks[-1]
        return prefix_valid and preceding_valid and not last_check.passed and authorization.first_failed_check == last_check.name
    return authorization.authorized is None and len(authorization.checks) == 0 and authorization.first_failed_check is None


def _authorization_payload_valid(authorization: AuthorizationEvidenceV1) -> bool:
    simulation_present = authorization.simulation_reference is not None
    direct_present = authorization.approval_transition_source is not None or authorization.approval_transition_at is not None
    ticket_present = authorization.ticket_id is not None or authorization.ticket_user_id is not None or authorization.ticket_policy_id is not None or authorization.ticket_policy_version is not None or authorization.ticket_order_plan_id is not None or authorization.ticket_data_mode is not None or authorization.ticket_status is not None or authorization.requested_at is not None or authorization.approved_at is not None or authorization.expires_at is not None or authorization.approved_by_label is not None or authorization.authentication_assurance is not None
    level4_present = authorization.promotion_status is not None or authorization.allowed_execution_levels is not None
    level5_present = authorization.operator_run_id is not None or authorization.registry_strategy_id is not None or authorization.registry_version is not None or authorization.registry_spec_hash is not None or authorization.registry_status is not None or authorization.registry_allowed_execution_levels is not None or authorization.registry_min_policy_version is not None or authorization.registry_max_policy_version is not None or authorization.lifecycle_strategy_id is not None or authorization.lifecycle_version is not None or authorization.lifecycle_status is not None or authorization.lifecycle_spec_hash is not None
    recipe_present = authorization.recipe_strategy_id is not None or authorization.recipe_version is not None
    if authorization.kind == "simulated_level_1_2":
        return simulation_present and not direct_present and not ticket_present and not recipe_present and not level4_present and not level5_present and authorization.authenticated_subject_id is None and authorization.authentication_reference is None
    if authorization.kind == "human_direct_level_3":
        return not simulation_present and authorization.approval_transition_source is not None and authorization.approval_transition_at is not None and not ticket_present and not recipe_present and not level4_present and not level5_present and authorization.authenticated_subject_id is None and authorization.authentication_reference is None
    if authorization.kind == "human_ticket_level_3":
        return not simulation_present and not direct_present and ticket_present and not recipe_present and not level4_present and not level5_present
    if authorization.kind == "guarded_level_4":
        return not simulation_present and not direct_present and not ticket_present and authorization.recipe_strategy_id is not None and authorization.recipe_version is not None and authorization.promotion_status is not None and authorization.allowed_execution_levels is not None and not level5_present and authorization.authenticated_subject_id is None and authorization.authentication_reference is None
    return not simulation_present and not direct_present and not ticket_present and authorization.recipe_strategy_id is not None and authorization.recipe_version is not None and not level4_present and level5_present and authorization.authenticated_subject_id is None and authorization.authentication_reference is None


def _single_payload_absent(single: SingleRiskEvidenceV1) -> bool:
    return single.risk_check_id is None and single.order_plan_id is None and single.passed is None and single.policy_id is None and single.policy_version is None and single.policy_user_id is None and single.snapshot_user_id is None and single.idempotency_key is None and single.created_at is None and single.expires_at is None and single.passed_checks is None and single.failed_checks is None and single.snapshot_id is None and single.snapshot_captured_at is None and single.snapshot_fingerprint_schema_version is None and single.snapshot_fingerprint is None and single.submit_market_quote_symbol is None and single.submit_market_quote_as_of is None and single.submit_market_quote_fingerprint_schema_version is None and single.submit_market_quote_fingerprint is None and single.guardrail_fingerprint_schema_version is None and single.guardrail_fingerprint is None and single.reservation_state is None


def _batch_payload_absent(batch: BatchRiskEvidenceV1) -> bool:
    return batch.passed is None and batch.mode is None and batch.policy_version is None and batch.accepted_order_plan_ids is None and batch.failed_checks is None


def _final_payload_absent(final: FinalSafetyEvidenceV1) -> bool:
    return final.captured_at is None and final.policy_snapshot_current is None and final.policy_kill_switch_engaged is None and final.live_trading_enabled is None and final.operator_kill_switch_engaged is None and final.autopilot_paused is None and final.broker_healthy is None and final.failed_checks is None


def _paper_payload_absent(paper: PaperSubmissionEvidenceV1) -> bool:
    return paper.paper_run_id is None and paper.paper_run_fingerprint_schema_version is None and paper.paper_run_fingerprint is None and paper.run_user_id is None and paper.run_policy_id is None and paper.run_policy_version is None and paper.run_mode is None and paper.data_mode is None and paper.checkpoint_status is None and paper.snapshot_id is None and paper.snapshot_captured_at is None and paper.snapshot_fingerprint_schema_version is None and paper.snapshot_fingerprint is None and paper.snapshot_deadline is None and paper.quote_symbol is None and paper.quote_as_of is None and paper.quote_fingerprint_schema_version is None and paper.quote_fingerprint is None and paper.quote_deadline is None and paper.entry_atr14 is None and paper.store_id is None and paper.account_scope_fingerprint is None and paper.session_id is None and paper.fencing_token is None and paper.session_revision is None and paper.session_status is None and paper.session_lease_deadline is None


def _unevaluated_payloads_absent(evidence: KernelEvaluationInputV1) -> bool:
    valid = True
    if evidence.single_risk.evaluation_state == "not_evaluated" and not _single_payload_absent(evidence.single_risk):
        valid = False
    if evidence.batch_risk.evaluation_state == "not_evaluated" and not _batch_payload_absent(evidence.batch_risk):
        valid = False
    if evidence.final_safety.evaluation_state == "not_evaluated" and not _final_payload_absent(evidence.final_safety):
        valid = False
    if evidence.paper.evaluation_state == "not_evaluated" and not _paper_payload_absent(evidence.paper):
        valid = False
    if evidence.capability.evaluation_state == "not_evaluated" and evidence.capability.profile_id is not None:
        valid = False
    return valid


def _identity_reasons(evidence: KernelEvaluationInputV1) -> tuple[str, ...]:
    reasons = ()
    if evidence.candidate.policy_id != evidence.context.current_policy_id:
        reasons = reasons + ("policy_identity_mismatch",)
    if evidence.candidate.policy_version != evidence.context.current_policy_version:
        reasons = reasons + ("policy_version_mismatch",)
    return _sorted_unique(reasons)


def _strategy_binding_reasons(evidence: KernelEvaluationInputV1) -> tuple[str, ...]:
    reasons = ()
    binding = evidence.candidate.strategy_binding
    authorization = evidence.authorization
    if binding is not None:
        if binding.symbol != evidence.candidate.intent.symbol or binding.side != evidence.candidate.intent.side or binding.policy_version != evidence.candidate.policy_version:
            reasons = reasons + ("strategy_binding_mismatch",)
    if authorization.kind == "guarded_level_4" or authorization.kind == "automated_level_5":
        if binding is not None:
            if authorization.recipe_strategy_id != binding.strategy_id or authorization.recipe_version is None or not _versions_match(binding.strategy_version, authorization.recipe_version):
                reasons = reasons + ("strategy_binding_mismatch",)
    if authorization.kind == "guarded_level_4":
        allowed = authorization.allowed_execution_levels
        level_allowed = allowed is not None and ("level_4" in allowed or "guarded_autopilot" in allowed)
        if authorization.promotion_status not in ("approved", "validated_l4") or not level_allowed:
            reasons = reasons + ("strategy_authority_mismatch",)
    if authorization.kind == "automated_level_5":
        required_present = authorization.recipe_strategy_id is not None and authorization.recipe_version is not None and authorization.registry_strategy_id is not None and authorization.registry_version is not None and authorization.registry_spec_hash is not None and authorization.registry_status is not None and authorization.registry_allowed_execution_levels is not None and authorization.lifecycle_strategy_id is not None and authorization.lifecycle_version is not None and authorization.lifecycle_status is not None and authorization.lifecycle_spec_hash is not None
        if not required_present:
            reasons = reasons + ("strategy_authority_mismatch",)
        else:
            level_allowed = "level_5" in authorization.registry_allowed_execution_levels or "fully_automated" in authorization.registry_allowed_execution_levels
            minimum_allowed = authorization.registry_min_policy_version is None or authorization.registry_min_policy_version <= evidence.candidate.policy_version
            maximum_allowed = authorization.registry_max_policy_version is None or evidence.candidate.policy_version <= authorization.registry_max_policy_version
            policy_allowed = minimum_allowed and maximum_allowed
            recipe_matches = authorization.recipe_strategy_id == authorization.registry_strategy_id and authorization.recipe_version is not None and authorization.registry_version is not None and _versions_match(authorization.recipe_version, authorization.registry_version)
            if authorization.registry_status != "validated_l5" or not level_allowed or not policy_allowed or not recipe_matches:
                reasons = reasons + ("strategy_authority_mismatch",)
            known_levels_only = all(level in ("level_3", "level_4", "guarded_autopilot", "level_5", "fully_automated") for level in authorization.registry_allowed_execution_levels)
            if known_levels_only:
                lifecycle_status_valid = authorization.lifecycle_status in ("paper_validated", "live_candidate")
            else:
                lifecycle_status_valid = authorization.lifecycle_status == "live_candidate"
            lifecycle_matches = authorization.registry_strategy_id == authorization.lifecycle_strategy_id and authorization.registry_version == authorization.lifecycle_version and authorization.registry_spec_hash == authorization.lifecycle_spec_hash and lifecycle_status_valid
            if not lifecycle_matches:
                reasons = reasons + ("lifecycle_binding_mismatch",)
    return _sorted_unique(reasons)


def _authorization_reasons(evidence: KernelEvaluationInputV1) -> tuple[str, ...]:
    authorization = evidence.authorization
    reasons = ()
    if authorization.kind != _expected_kind(evidence.context.run_mode):
        reasons = reasons + ("authorization_kind_mismatch",)
    if authorization.policy_id != evidence.candidate.policy_id or authorization.policy_user_id != evidence.context.policy_user_id:
        reasons = reasons + ("authorization_evidence_mismatch",)
    if authorization.policy_version != evidence.candidate.policy_version:
        reasons = reasons + ("authorization_evidence_mismatch",)
    if authorization.evaluated_at > evidence.evaluated_at:
        reasons = reasons + ("future_evidence_timestamp",)
    if authorization.evaluation_state == "failed" or authorization.authorized is False:
        reasons = reasons + ("authorization_denied",)
    if not _authorization_checks_valid(authorization):
        reasons = reasons + ("authorization_evidence_mismatch",)
    if not _authorization_payload_valid(authorization):
        reasons = reasons + ("authorization_evidence_mismatch",)
    if authorization.kind == "simulated_level_1_2":
        if authorization.source != "simulated_harness" or authorization.assurance != "simulated" or authorization.simulation_reference is None:
            reasons = reasons + ("authorization_evidence_mismatch",)
    if authorization.kind == "human_direct_level_3":
        if authorization.source != "level3_direct_transition" or authorization.assurance != "unverified_local" or authorization.approval_transition_source is None or authorization.approval_transition_at is None or authorization.authenticated_subject_id is not None or authorization.authentication_reference is not None:
            reasons = reasons + ("authorization_evidence_mismatch",)
        else:
            if authorization.approval_transition_at > authorization.evaluated_at:
                reasons = reasons + ("future_evidence_timestamp",)
    if authorization.kind == "human_ticket_level_3":
        ticket_present = authorization.source == "level3_ticket" and authorization.assurance == "unverified_local" and authorization.ticket_id is not None and authorization.ticket_user_id is not None and authorization.ticket_policy_id is not None and authorization.ticket_policy_version is not None and authorization.ticket_order_plan_id is not None and authorization.ticket_data_mode is not None and authorization.ticket_status is not None and authorization.requested_at is not None and authorization.approved_at is not None and authorization.expires_at is not None and authorization.approved_by_label is not None
        if not ticket_present:
            reasons = reasons + ("authorization_evidence_mismatch",)
        else:
            ticket_identity = authorization.ticket_user_id == evidence.context.policy_user_id and authorization.ticket_policy_id == evidence.candidate.policy_id and authorization.ticket_policy_version == evidence.candidate.policy_version and authorization.ticket_order_plan_id == evidence.candidate.order_plan_id and authorization.ticket_data_mode == evidence.context.data_mode
            if not ticket_identity:
                reasons = reasons + ("authorization_evidence_mismatch",)
            if authorization.requested_at is not None and authorization.approved_at is not None and authorization.expires_at is not None and (authorization.ticket_status != "approved" or authorization.requested_at > authorization.approved_at or authorization.approved_at > authorization.evaluated_at or authorization.approved_at > evidence.evaluated_at or evidence.evaluated_at >= authorization.expires_at):
                reasons = reasons + ("ticket_expired",)
    if evidence.context.external_paper_enabled and authorization.kind in ("human_direct_level_3", "human_ticket_level_3"):
        reasons = reasons + ("actor_assurance_missing",)
    if authorization.kind == "guarded_level_4":
        if authorization.source != "guarded_authority_v1" or authorization.assurance != "policy_authorized":
            reasons = reasons + ("authorization_evidence_mismatch",)
    if authorization.kind == "automated_level_5":
        if authorization.source != "level5_authority_v1" or authorization.assurance != "operator_authorized":
            reasons = reasons + ("authorization_evidence_mismatch",)
        if authorization.operator_run_id is None or authorization.operator_run_id != evidence.context.operator_run_id:
            reasons = reasons + ("operator_run_mismatch",)
        if evidence.single_risk.created_at is not None and (authorization.evaluated_at != evidence.single_risk.created_at or authorization.evaluated_at != evidence.evaluated_at):
            reasons = reasons + ("authorization_evidence_mismatch",)
    if authorization.kind == "professional_risk_reduction":
        reasons = reasons + ("professional_binding_not_supported",)
    reasons = reasons + _strategy_binding_reasons(evidence)
    if evidence.context.run_mode in ("operator_mock_submit", "operator_paper_submit"):
        if evidence.context.operator_run_id is None:
            reasons = reasons + ("operator_run_mismatch",)
    else:
        if evidence.context.operator_run_id is not None:
            reasons = reasons + ("operator_run_mismatch",)
    return _sorted_unique(reasons)


def _paper_reasons(evidence: KernelEvaluationInputV1) -> tuple[str, ...]:
    paper = evidence.paper
    reasons = ()
    if not evidence.context.external_paper_enabled:
        if paper.evaluation_state != "not_applicable" or not _paper_payload_absent(paper):
            reasons = reasons + ("paper_evidence_mismatch",)
        return _sorted_unique(reasons)
    if evidence.candidate.strategy_binding is None:
        reasons = reasons + ("paper_strategy_binding_missing",)
    if paper.evaluation_state != "passed":
        reasons = reasons + ("paper_evidence_mismatch",)
    required_present = paper.paper_run_id is not None and paper.paper_run_fingerprint_schema_version == 1 and paper.paper_run_fingerprint is not None and paper.run_user_id is not None and paper.run_policy_id is not None and paper.run_policy_version is not None and paper.run_mode is not None and paper.data_mode is not None and paper.checkpoint_status is not None and paper.snapshot_id is not None and paper.snapshot_captured_at is not None and paper.snapshot_fingerprint_schema_version == 1 and paper.snapshot_fingerprint is not None and paper.snapshot_deadline is not None and paper.quote_symbol is not None and paper.quote_as_of is not None and paper.quote_fingerprint_schema_version == 1 and paper.quote_fingerprint is not None and paper.quote_deadline is not None and paper.entry_atr14 is not None and paper.store_id is not None and paper.account_scope_fingerprint is not None and paper.session_id is not None and paper.fencing_token is not None and paper.session_revision is not None and paper.session_status is not None and paper.session_lease_deadline is not None
    if not required_present:
        reasons = reasons + ("paper_evidence_mismatch",)
    if paper.store_id is None or paper.account_scope_fingerprint is None:
        reasons = reasons + ("account_provenance_missing",)
    if paper.session_id is None or paper.fencing_token is None or paper.session_revision is None:
        reasons = reasons + ("paper_session_fence_missing",)
    if paper.checkpoint_status != "started":
        reasons = reasons + ("checkpoint_status_invalid",)
    if paper.session_status != "active" or paper.session_lease_deadline is None or evidence.evaluated_at >= paper.session_lease_deadline:
        reasons = reasons + ("paper_session_status_invalid",)
    if required_present:
        fingerprints_valid = _valid_sha256(paper.paper_run_fingerprint) and _valid_sha256(paper.snapshot_fingerprint) and _valid_sha256(paper.quote_fingerprint) and _valid_sha256(paper.account_scope_fingerprint)
        if not fingerprints_valid or paper.paper_run_fingerprint != _paper_run_fingerprint(paper):
            reasons = reasons + ("paper_evidence_mismatch",)
        if not _valid_sha256(paper.account_scope_fingerprint):
            reasons = reasons + ("account_provenance_missing",)
        run_matches = paper.paper_run_id == evidence.context.operator_run_id and paper.paper_run_id == evidence.authorization.operator_run_id and paper.run_user_id == evidence.context.policy_user_id and paper.run_policy_id == evidence.candidate.policy_id and paper.run_policy_version == evidence.candidate.policy_version and paper.run_mode == evidence.context.run_mode and paper.data_mode == evidence.context.data_mode
        snapshot_matches = evidence.single_risk.evaluation_state == "not_evaluated" or (paper.snapshot_id == evidence.single_risk.snapshot_id and paper.snapshot_captured_at == evidence.single_risk.snapshot_captured_at and paper.snapshot_fingerprint == evidence.single_risk.snapshot_fingerprint and paper.snapshot_fingerprint_schema_version == evidence.single_risk.snapshot_fingerprint_schema_version)
        quote_matches = evidence.single_risk.evaluation_state == "not_evaluated" or (paper.quote_symbol == evidence.single_risk.submit_market_quote_symbol and paper.quote_as_of == evidence.single_risk.submit_market_quote_as_of and paper.quote_fingerprint == evidence.single_risk.submit_market_quote_fingerprint and paper.quote_fingerprint_schema_version == evidence.single_risk.submit_market_quote_fingerprint_schema_version)
        freshness = paper.snapshot_deadline is not None and paper.quote_deadline is not None and evidence.evaluated_at < paper.snapshot_deadline and evidence.evaluated_at < paper.quote_deadline
        if not run_matches or not snapshot_matches or not quote_matches or not freshness:
            reasons = reasons + ("paper_evidence_mismatch",)
    return _sorted_unique(reasons)


def _candidate_reasons(evidence: KernelEvaluationInputV1) -> tuple[str, ...]:
    candidate = evidence.candidate
    reasons = ()
    if candidate.status != "user_approved":
        reasons = reasons + ("order_not_user_approved",)
    if candidate.risk_check_id is None:
        reasons = reasons + ("risk_check_missing",)
    if candidate.risk_check_expires_at is not None and evidence.evaluated_at >= candidate.risk_check_expires_at:
        reasons = reasons + ("prior_risk_check_expired",)
    if candidate.order_expires_at is None:
        reasons = reasons + ("order_expiry_missing",)
    else:
        if evidence.evaluated_at >= candidate.order_expires_at:
            reasons = reasons + ("order_expired",)
    return _sorted_unique(reasons)


def _risk_reasons(evidence: KernelEvaluationInputV1) -> tuple[str, ...]:
    single = evidence.single_risk
    batch = evidence.batch_risk
    candidate = evidence.candidate
    reasons = ()
    if single.evaluation_state == "not_evaluated":
        reasons = reasons + ("risk_evidence_not_evaluated",)
        return _sorted_unique(reasons)
    required_single = single.risk_check_id is not None and single.order_plan_id is not None and single.passed is not None and single.policy_id is not None and single.policy_version is not None and single.policy_user_id is not None and single.snapshot_user_id is not None and single.idempotency_key is not None and single.created_at is not None and single.expires_at is not None and single.passed_checks is not None and single.failed_checks is not None and single.snapshot_id is not None and single.snapshot_captured_at is not None and single.snapshot_fingerprint_schema_version == 1 and single.snapshot_fingerprint is not None and single.guardrail_fingerprint_schema_version == 1 and single.guardrail_fingerprint is not None and single.reservation_state is not None
    if not required_single:
        reasons = reasons + ("risk_check_mismatch",)
    else:
        binding_matches = single.risk_check_id == candidate.risk_check_id and single.order_plan_id == candidate.order_plan_id and single.policy_id == candidate.policy_id and single.policy_version == candidate.policy_version and single.policy_user_id == evidence.context.policy_user_id and single.snapshot_user_id == evidence.context.policy_user_id and single.idempotency_key == candidate.idempotency_key
        if not binding_matches:
            reasons = reasons + ("risk_check_mismatch",)
        if single.created_at is not None and single.snapshot_captured_at is not None and (single.created_at < evidence.authorization.evaluated_at or single.created_at > evidence.evaluated_at or single.snapshot_captured_at > evidence.evaluated_at or candidate.intent.quote_time > evidence.evaluated_at):
            reasons = reasons + ("future_evidence_timestamp",)
        if single.expires_at is not None and evidence.evaluated_at >= single.expires_at:
            reasons = reasons + ("risk_check_expired",)
        expected_reservation = "required_not_prepared" if evidence.context.external_paper_enabled else "none"
        if single.reservation_state != expected_reservation:
            reasons = reasons + ("risk_check_mismatch",)
        quote_complete = single.submit_market_quote_symbol is None and single.submit_market_quote_as_of is None and single.submit_market_quote_fingerprint_schema_version is None and single.submit_market_quote_fingerprint is None
        quote_present = single.submit_market_quote_symbol is not None and single.submit_market_quote_as_of is not None and single.submit_market_quote_fingerprint_schema_version == 1 and single.submit_market_quote_fingerprint is not None
        if not quote_complete and not quote_present:
            reasons = reasons + ("risk_quote_mismatch",)
        if evidence.context.external_paper_enabled and not quote_present:
            reasons = reasons + ("risk_quote_mismatch",)
        if not _valid_sha256(single.snapshot_fingerprint) or not _valid_sha256(single.guardrail_fingerprint):
            reasons = reasons + ("risk_check_mismatch",)
        if quote_present:
            if not _valid_sha256(single.submit_market_quote_fingerprint) or single.submit_market_quote_symbol != candidate.intent.symbol:
                reasons = reasons + ("risk_quote_mismatch",)
            if single.submit_market_quote_as_of is not None and single.submit_market_quote_as_of > evidence.evaluated_at:
                reasons = reasons + ("future_evidence_timestamp",)
    if single.evaluation_state == "failed" or single.passed is not True or single.failed_checks is None or len(single.failed_checks) > 0:
        reasons = reasons + ("single_order_risk_failed",)
    if single.evaluation_state == "failed":
        return _sorted_unique(reasons)
    if batch.evaluation_state == "not_evaluated":
        reasons = reasons + ("risk_evidence_not_evaluated",)
        return _sorted_unique(reasons)
    if batch.passed is not True or batch.evaluation_state == "failed" or batch.failed_checks is None or len(batch.failed_checks) > 0:
        reasons = reasons + ("batch_risk_failed",)
    if batch.mode == "partial_batch":
        reasons = reasons + ("partial_batch_not_allowed_at_submit",)
    if batch.mode != "full_batch" and batch.mode != "partial_batch":
        reasons = reasons + ("batch_risk_failed",)
    if batch.policy_version != candidate.policy_version:
        reasons = reasons + ("risk_check_mismatch",)
    if batch.accepted_order_plan_ids is None or candidate.order_plan_id not in batch.accepted_order_plan_ids:
        reasons = reasons + ("batch_order_not_accepted",)
    return _sorted_unique(reasons)


def _final_safety_reasons(evidence: KernelEvaluationInputV1) -> tuple[str, ...]:
    final = evidence.final_safety
    reasons = ()
    expected_failed = ()
    if final.captured_at is None or final.captured_at > evidence.evaluated_at:
        reasons = reasons + ("future_evidence_timestamp",)
    if final.policy_snapshot_current is not True:
        reasons = reasons + ("policy_snapshot_changed",)
        expected_failed = expected_failed + ("policy_version_match",)
    if final.policy_kill_switch_engaged is not False:
        reasons = reasons + ("policy_kill_switch_engaged",)
        expected_failed = expected_failed + ("kill_switch_not_engaged",)
    if final.live_trading_enabled is not False:
        reasons = reasons + ("live_trading_enabled",)
        expected_failed = expected_failed + ("live_trading_disabled",)
    if final.operator_kill_switch_engaged is not False:
        reasons = reasons + ("operator_kill_switch_engaged",)
        expected_failed = expected_failed + ("operator_kill_switch_not_engaged",)
    if final.autopilot_paused is not False:
        reasons = reasons + ("autopilot_paused",)
        expected_failed = expected_failed + ("operator_not_paused",)
    if final.broker_healthy is not True:
        reasons = reasons + ("broker_unhealthy",)
        expected_failed = expected_failed + ("broker_health",)
    if final.failed_checks != expected_failed:
        reasons = reasons + ("policy_snapshot_changed",)
    return _sorted_unique(reasons)


def _decimal_is_positive_integer(value: Decimal | None) -> bool:
    if value is None or value <= 0:
        return False
    parts = value.as_tuple()
    exponent = parts.exponent
    if exponent >= 0:
        return True
    fractional_count = -exponent
    if fractional_count > len(parts.digits):
        return False
    fractional_digits = parts.digits[len(parts.digits) - fractional_count :]
    return all(digit == 0 for digit in fractional_digits)


def _profile_matrix_valid(evidence: KernelEvaluationInputV1) -> bool:
    profile = evidence.capability.profile_id
    data_mode = evidence.context.data_mode
    run_mode = evidence.context.run_mode
    kind = evidence.authorization.kind
    external = evidence.context.external_paper_enabled
    paper_state = evidence.paper.evaluation_state
    if profile == "mock_v1":
        return not external and paper_state == "not_applicable" and data_mode == "fixture" and ((run_mode == "level_1_2_mock" and kind == "simulated_level_1_2") or (run_mode == "level_3_direct" and kind == "human_direct_level_3") or (run_mode == "level_3_ticket" and kind == "human_ticket_level_3") or (run_mode == "guarded_level_4" and kind == "guarded_level_4") or (run_mode == "operator_mock_submit" and kind == "automated_level_5"))
    if profile == "simulated_paper_v1":
        return not external and paper_state == "not_applicable" and ((data_mode == "fixture" and run_mode == "level_3_direct" and kind == "human_direct_level_3") or (data_mode == "paper_trading" and run_mode == "level_3_ticket" and kind == "human_ticket_level_3") or (data_mode == "fixture" and run_mode == "guarded_level_4" and kind == "guarded_level_4") or (data_mode == "fixture" and run_mode == "operator_paper_submit" and kind == "automated_level_5"))
    if profile == "kis_paper_v1":
        return external and paper_state == "passed" and data_mode == "paper_trading" and run_mode == "operator_paper_submit" and kind == "automated_level_5"
    return False


def _capability_reasons(evidence: KernelEvaluationInputV1) -> tuple[str, ...]:
    reasons = ()
    profile = evidence.capability.profile_id
    intent = evidence.candidate.intent
    if evidence.capability.evaluation_state == "failed":
        reasons = reasons + ("broker_capability_mismatch",)
    if not _profile_matrix_valid(evidence):
        reasons = reasons + ("broker_capability_mismatch",)
    if profile == "kis_paper_v1" and evidence.context.data_mode != "paper_trading":
        reasons = reasons + ("data_mode_mismatch",)
    if profile == "kis_paper_v1" and not evidence.context.external_paper_enabled:
        reasons = reasons + ("broker_environment_mismatch",)
    if profile != "kis_paper_v1" and evidence.context.external_paper_enabled:
        reasons = reasons + ("broker_environment_mismatch",)
    if intent.order_type == "market" or evidence.context.market_orders_enabled:
        reasons = reasons + ("market_order_disabled",)
    if intent.quantity <= 0:
        reasons = reasons + ("quantity_step_mismatch",)
    if intent.order_type == "limit":
        if intent.limit_price is None or intent.limit_price <= 0:
            reasons = reasons + ("price_step_mismatch",)
    if profile == "kis_paper_v1":
        if not _decimal_is_positive_integer(intent.quantity):
            reasons = reasons + ("quantity_step_mismatch",)
        if not _decimal_is_positive_integer(intent.limit_price):
            reasons = reasons + ("price_step_mismatch",)
    return _sorted_unique(reasons)


def _blocked_decision(
    evidence: KernelEvaluationInputV1,
    stage: str,
    reasons: tuple[str, ...],
    fingerprint: str,
    capability_reached: bool,
) -> KernelDecisionV1:
    if capability_reached:
        if evidence.capability.profile_id == "kis_paper_v1":
            durable_requirement = "required"
            reservation_requirement = "required"
        else:
            durable_requirement = "not_required"
            reservation_requirement = "not_required"
    else:
        durable_requirement = "not_evaluated"
        reservation_requirement = "not_evaluated"
    return KernelDecisionV1(
        schema_version=1,
        order_plan_id=evidence.candidate.order_plan_id,
        verdict="blocked",
        blocked_stage=stage,
        reason_codes=_sorted_unique(reasons),
        durable_prepare_requirement=durable_requirement,
        atomic_reservation_requirement=reservation_requirement,
        intended_next_stage="none",
        evaluated_at=evidence.evaluated_at,
        evidence_fingerprint=fingerprint,
    )


def evaluate_execution(evidence: KernelEvaluationInputV1) -> KernelDecisionV1:
    """Evaluate frozen evidence without performing or authorizing a command."""

    fingerprint = _evidence_fingerprint(evidence)
    reasons = _identity_reasons(evidence)
    if len(reasons) > 0:
        return _blocked_decision(evidence, "identity", reasons, fingerprint, False)
    if not _prefix_valid(evidence):
        return _blocked_decision(
            evidence,
            "authorization",
            ("evidence_prefix_mismatch",),
            fingerprint,
            False,
        )
    reasons = _authorization_reasons(evidence)
    if len(reasons) > 0:
        return _blocked_decision(evidence, "authorization", reasons, fingerprint, False)
    reasons = _paper_reasons(evidence)
    if len(reasons) > 0:
        return _blocked_decision(evidence, "paper_evidence", reasons, fingerprint, False)
    reasons = _candidate_reasons(evidence)
    if len(reasons) > 0:
        return _blocked_decision(evidence, "candidate", reasons, fingerprint, False)
    reasons = _risk_reasons(evidence)
    if len(reasons) > 0:
        return _blocked_decision(evidence, "risk", reasons, fingerprint, False)
    reasons = _final_safety_reasons(evidence)
    if len(reasons) > 0:
        return _blocked_decision(evidence, "final_safety", reasons, fingerprint, False)
    reasons = _capability_reasons(evidence)
    if len(reasons) > 0:
        return _blocked_decision(evidence, "capability", reasons, fingerprint, True)
    if evidence.capability.profile_id == "kis_paper_v1":
        durable_requirement = "required"
        reservation_requirement = "required"
    else:
        durable_requirement = "not_required"
        reservation_requirement = "not_required"
    return KernelDecisionV1(
        schema_version=1,
        order_plan_id=evidence.candidate.order_plan_id,
        verdict="eligible_for_legacy_submit",
        blocked_stage="none",
        reason_codes=(),
        durable_prepare_requirement=durable_requirement,
        atomic_reservation_requirement=reservation_requirement,
        intended_next_stage="legacy_submit_handoff",
        evaluated_at=evidence.evaluated_at,
        evidence_fingerprint=fingerprint,
    )
