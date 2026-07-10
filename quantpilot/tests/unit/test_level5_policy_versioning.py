from __future__ import annotations

import pytest

from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.policy.versioning import (
    POLICY_UPDATE_CONFIRMATION,
    PolicyUpdateConfirmationRequired,
    PolicyVersionGuard,
    PolicyVersioningService,
)
from quantpilot.packages.core.schemas import BrokerMode, ExecutionMode, OrderType


def test_policy_version_mismatch_blocks_automatic_submission() -> None:
    guard = PolicyVersionGuard()

    review = guard.require_current_version(policy_id="pol_level5_fixture", current_version=6, requested_version=5)

    assert review.blocks_automatic_submission is True
    assert review.reason == "policy_version_mismatch"


def test_matching_policy_version_does_not_block() -> None:
    guard = PolicyVersionGuard()

    review = guard.require_current_version(policy_id="pol_level5_fixture", current_version=5, requested_version=5)

    assert review.blocks_automatic_submission is False
    assert review.reason == "policy_version_current"


def _versioning_service() -> tuple[HarnessService, PolicyVersioningService, str]:
    harness = HarnessService()
    policy = harness.parse_policy()
    service = PolicyVersioningService(harness.repositories, harness.audit)
    return harness, service, policy.policy_id


def test_non_material_policy_update_creates_a_new_version() -> None:
    harness, service, policy_id = _versioning_service()
    before = harness.repositories.policies.require(policy_id)

    change = service.propose_update(
        policy_id=policy_id,
        changes={"preferred_themes": ["ai", "semiconductor"]},
        changed_by="fixture-user",
    )

    after = harness.repositories.policies.require(policy_id)
    assert change.requires_review is False
    assert change.previous_version == before.version
    assert after.version == before.version + 1
    assert after.preferred_themes == ["ai", "semiconductor"]


def test_risk_limit_update_requires_explicit_confirmation() -> None:
    harness, service, policy_id = _versioning_service()
    before = harness.repositories.policies.require(policy_id)

    change = service.propose_update(
        policy_id=policy_id,
        changes={"daily_loss_limit": -0.02},
        changed_by="fixture-user",
    )

    # The active policy must not silently mutate.
    untouched = harness.repositories.policies.require(policy_id)
    assert change.requires_review is True
    assert untouched.version == before.version
    assert untouched.daily_loss_limit == before.daily_loss_limit

    with pytest.raises(PolicyUpdateConfirmationRequired):
        service.confirm_update(policy_id=policy_id, confirmation="yes please")

    updated = service.confirm_update(policy_id=policy_id, confirmation=POLICY_UPDATE_CONFIRMATION)
    assert updated.version == before.version + 1
    assert updated.daily_loss_limit == -0.02


def test_pending_policy_review_snapshots_changes_and_change_record() -> None:
    harness, service, policy_id = _versioning_service()
    before = harness.repositories.policies.require(policy_id)
    allowed_types = [OrderType.limit]

    proposed = service.propose_update(
        policy_id=policy_id,
        changes={"allowed_order_types": allowed_types},
        changed_by="fixture-user",
    )
    allowed_types.append(OrderType.market)
    proposed.next_version = 99
    returned_pending = service.pending_change(policy_id)
    assert returned_pending is not None
    returned_pending.next_version = 100

    updated = service.confirm_update(
        policy_id=policy_id,
        confirmation=POLICY_UPDATE_CONFIRMATION,
    )

    assert updated.version == before.version + 1
    assert updated.allowed_order_types == [OrderType.limit]


def test_competing_policy_confirmations_use_version_compare_and_swap() -> None:
    harness, first, policy_id = _versioning_service()
    second = PolicyVersioningService(harness.repositories, harness.audit)
    first.propose_update(
        policy_id=policy_id,
        changes={"broker": BrokerMode.paper},
        changed_by="fixture-user",
    )
    second.propose_update(
        policy_id=policy_id,
        changes={"max_daily_orders": 4},
        changed_by="fixture-user",
    )

    applied = first.confirm_update(
        policy_id=policy_id,
        confirmation=POLICY_UPDATE_CONFIRMATION,
    )
    with pytest.raises(PolicyUpdateConfirmationRequired, match="stale"):
        second.confirm_update(
            policy_id=policy_id,
            confirmation=POLICY_UPDATE_CONFIRMATION,
        )

    current = harness.repositories.policies.require(policy_id)
    assert current == applied
    assert current.version == 2
    assert current.broker == BrokerMode.paper
    assert current.max_daily_orders == 3


def test_execution_mode_update_is_audit_logged() -> None:
    harness, service, policy_id = _versioning_service()

    service.propose_update(
        policy_id=policy_id,
        changes={"execution_mode": ExecutionMode.guarded_autopilot, "authority_level": 4},
        changed_by="fixture-user",
    )
    service.confirm_update(policy_id=policy_id, confirmation=POLICY_UPDATE_CONFIRMATION)

    actions = [event.action for event in harness.repositories.audit_logs.list()]
    assert "policy_version_proposed" in actions
    assert "policy_version_applied" in actions
    assert "execution_mode_updated" in actions
    updated = harness.repositories.policies.require(policy_id)
    assert updated.execution_mode == ExecutionMode.guarded_autopilot


def test_confirming_without_pending_update_raises() -> None:
    _, service, policy_id = _versioning_service()

    with pytest.raises(PolicyUpdateConfirmationRequired):
        service.confirm_update(policy_id=policy_id, confirmation=POLICY_UPDATE_CONFIRMATION)


@pytest.mark.parametrize("field", ["policy_id", "user_id", "version", "created_at"])
def test_policy_identity_fields_cannot_change_across_versions(field: str) -> None:
    harness, service, policy_id = _versioning_service()
    policy = harness.repositories.policies.require(policy_id)
    replacement = {
        "policy_id": "other-policy",
        "user_id": "other-user",
        "version": policy.version + 10,
        "created_at": policy.created_at,
    }[field]

    with pytest.raises(ValueError, match="identity fields are immutable"):
        service.propose_update(
            policy_id=policy_id,
            changes={field: replacement},
            changed_by="fixture-user",
        )

    assert harness.repositories.policies.require(policy_id) == policy
