from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from quantpilot.packages.core.schemas import HarnessModel, UserPolicy, utc_now
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


POLICY_UPDATE_CONFIRMATION = "confirm policy update"

# Material fields change risk exposure, broker access, or authority. Updating any of
# them must never silently mutate an active policy: the update stays pending until the
# user repeats the explicit confirmation phrase.
MATERIAL_POLICY_FIELDS = {
    "daily_loss_limit",
    "monthly_loss_limit",
    "monthly_loss_pause_new_buys",
    "monthly_loss_stop_all_autotrading",
    "max_positions",
    "max_position_weight",
    "max_sector_weight",
    "min_cash_weight",
    "max_daily_orders",
    "max_daily_turnover",
    "single_order_cash_limit",
    "stale_quote_max_age_seconds",
    "human_review_quote_max_age_seconds",
    "allowed_order_types",
    "broker",
    "execution_mode",
    "authority_level",
    "guarded_autopilot_enabled",
    "fully_automated_operator_enabled",
    "kill_switch_engaged",
}


class PolicyReviewRequest(HarnessModel):
    policy_id: str
    current_version: int
    requested_version: int
    reason: str
    blocks_automatic_submission: bool = True


class PolicyVersionChange(HarnessModel):
    policy_id: str
    previous_version: int
    next_version: int
    changed_fields: list[str]
    changed_at: datetime = Field(default_factory=utc_now)
    changed_by: str
    requires_review: bool


class PolicyVersionGuard:
    def require_current_version(self, *, policy_id: str, current_version: int, requested_version: int) -> PolicyReviewRequest:
        if current_version == requested_version:
            return PolicyReviewRequest(
                policy_id=policy_id,
                current_version=current_version,
                requested_version=requested_version,
                reason="policy_version_current",
                blocks_automatic_submission=False,
            )
        return PolicyReviewRequest(
            policy_id=policy_id,
            current_version=current_version,
            requested_version=requested_version,
            reason="policy_version_mismatch",
            blocks_automatic_submission=True,
        )


class PolicyUpdateConfirmationRequired(RuntimeError):
    pass


class PolicyVersioningService:
    def __init__(self, repositories: RepositoryRegistry, audit: AuditRecorder) -> None:
        self.repositories = repositories
        self.audit = audit
        self._pending: dict[str, tuple[dict[str, Any], PolicyVersionChange]] = {}

    def pending_change(self, policy_id: str) -> PolicyVersionChange | None:
        pending = self._pending.get(policy_id)
        return pending[1] if pending else None

    def propose_update(self, *, policy_id: str, changes: dict[str, Any], changed_by: str) -> PolicyVersionChange:
        policy = self.repositories.policies.require(policy_id)
        changed_fields = sorted(changes)
        if not changed_fields:
            raise ValueError("policy update requires at least one changed field")
        requires_review = bool(MATERIAL_POLICY_FIELDS.intersection(changed_fields))
        change = PolicyVersionChange(
            policy_id=policy_id,
            previous_version=policy.version,
            next_version=policy.version + 1,
            changed_fields=changed_fields,
            changed_by=changed_by,
            requires_review=requires_review,
        )
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="policy",
            entity_id=policy_id,
            action="policy_version_proposed",
            before_state=policy,
            after_state=change,
            source="policy_versioning_service",
        )
        if requires_review:
            self._pending[policy_id] = (dict(changes), change)
            return change
        self._apply(policy=policy, changes=dict(changes), change=change)
        return change

    def confirm_update(self, *, policy_id: str, confirmation: str) -> UserPolicy:
        pending = self._pending.get(policy_id)
        if pending is None:
            raise PolicyUpdateConfirmationRequired("no pending policy update to confirm")
        if confirmation != POLICY_UPDATE_CONFIRMATION:
            raise PolicyUpdateConfirmationRequired(
                f"explicit confirmation '{POLICY_UPDATE_CONFIRMATION}' is required for material policy changes"
            )
        changes, change = self._pending.pop(policy_id)
        policy = self.repositories.policies.require(policy_id)
        return self._apply(policy=policy, changes=changes, change=change)

    def _apply(self, *, policy: UserPolicy, changes: dict[str, Any], change: PolicyVersionChange) -> UserPolicy:
        before = policy.model_copy(deep=True)
        updated = policy.model_copy(update={**changes, "version": change.next_version})
        # Re-validate the merged policy so an update cannot smuggle in values the
        # UserPolicy validators would reject at creation time.
        updated = UserPolicy.model_validate(updated.model_dump())
        self.repositories.policies.update(updated)
        self.audit.emit(
            user_id=updated.user_id,
            entity_type="policy",
            entity_id=updated.policy_id,
            action="policy_version_applied",
            before_state=before,
            after_state=updated,
            source="policy_versioning_service",
        )
        if "execution_mode" in changes:
            self.audit.emit(
                user_id=updated.user_id,
                entity_type="policy",
                entity_id=updated.policy_id,
                action="execution_mode_updated",
                before_state={"execution_mode": before.execution_mode.value},
                after_state={"execution_mode": updated.execution_mode.value},
                source="policy_versioning_service",
            )
        return updated
