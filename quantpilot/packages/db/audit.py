from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from quantpilot.packages.core.schemas import AuditLogEvent
from quantpilot.packages.db.repositories import InMemoryRepository


REDACTED = "[REDACTED]"
SECRET_FIELD_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "app_key",
    "appkey",
    "app_secret",
    "appsecret",
    "authorization",
    "bearer_token",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "token",
}

AUDIT_EVENT_ACTIONS = {
    "policy_created",
    "policy_confirmed",
    "strategy_loaded",
    "signal_generated",
    "level_2_signal_generated",
    "portfolio_plan_created",
    "level_2_rebalance_suggestion_created",
    "level_1_2_daily_report_generated",
    "order_plan_created",
    "order_proposed",
    "proposal_created",
    "proposal_blocked",
    "proposal_approved",
    "proposal_rejected",
    "proposal_modified",
    "proposal_expired",
    "risk_check_passed",
    "risk_check_failed",
    "risk_check_expired",
    "order_approved",
    "order_submitted",
    "broker_order_accepted",
    "fill_recorded",
    "order_filled",
    "order_partially_filled",
    "order_cancelled",
    "order_rejected",
    "order_expired",
    "order_failed",
    "duplicate_order_blocked",
    "stale_quote_blocked",
    "autopilot_run_started",
    "autopilot_order_authorized",
    "autopilot_order_blocked",
    "autopilot_order_submitted",
    "autopilot_paused",
    "autopilot_resumed",
    "kill_switch_engaged",
    "kill_switch_released",
    "loss_pause_engaged",
    "loss_stop_engaged",
    "authority_demoted_l4_to_l3",
    "authority_demoted_to_l2",
    "broker_health_failed",
    "policy_version_mismatch",
    "operation_report_generated",
    "policy_version_proposed",
    "policy_version_applied",
    "execution_mode_updated",
    "operator_run_started",
    "operator_run_completed",
    "operator_run_blocked",
    "operator_duplicate_run_ignored",
    "operator_fallback_engaged",
    "operator_strategy_selected",
    "operator_order_authorized",
    "operator_order_blocked",
    "operator_order_submitted",
    "operator_report_generated",
    "strategy_demoted",
    "strategy_disabled",
    "strategy_lifecycle_registered",
    "strategy_evidence_attached",
    "strategy_promoted",
    "strategy_revoked",
    "strategy_lifecycle_disabled",
}


def _is_secret_field(key: object) -> bool:
    normalized = str(key).replace("-", "_").lower()
    return (
        normalized in SECRET_FIELD_NAMES
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
        or normalized.endswith("_app_key")
        or normalized.endswith("_app_secret")
        or normalized.endswith("_access_token")
        or normalized.endswith("_password")
        or normalized.endswith("_api_key")
    )


def _redact_state(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _redact_state(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _is_secret_field(key) else _redact_state(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_state(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_state(item) for item in value]
    return value


def _safe_state(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return _redact_state(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return _redact_state(value)
    return {"value": str(_redact_state(value))}


class AuditRecorder:
    def __init__(self, repository: InMemoryRepository[AuditLogEvent]) -> None:
        self._repository = repository

    def emit(
        self,
        *,
        user_id: str,
        entity_type: str,
        entity_id: str,
        action: str,
        before_state: Any = None,
        after_state: Any = None,
        source: str,
    ) -> AuditLogEvent:
        if action not in AUDIT_EVENT_ACTIONS:
            raise ValueError(f"unknown audit action: {action}")
        event = AuditLogEvent(
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_state=_safe_state(before_state),
            after_state=_safe_state(after_state),
            source=source,
        )
        return self._repository.add(event)
