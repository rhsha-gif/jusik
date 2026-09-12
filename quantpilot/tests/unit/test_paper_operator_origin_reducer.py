"""The operator_resolution event source has a closed delta (2026-09-12 audit review).

Only a bare outcome_unknown row (no fill, no broker identifier) may be closed as
rejected under this source, and no other source may write that delta.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from quantpilot.packages.core.execution.events import (
    PaperEventStreamCorruption,
    build_paper_execution_event,
)
from quantpilot.packages.core.execution.reducer import reduce_paper_execution_event
from quantpilot.packages.core.operator.position_ledger import PaperOrderDispatch
from quantpilot.tests.unit.test_paper_execution_reducer import _claimed_with_payload


def _operator_unknown():
    projection, claimed = _claimed_with_payload()
    recovered = PaperOrderDispatch.model_validate(
        claimed.model_copy(
            update={
                "status": "outcome_unknown",
                "last_error_code": "process_interrupted",
                "updated_at": claimed.updated_at + timedelta(seconds=1),
                "revision": claimed.revision + 1,
            }
        ).model_dump()
    )
    event = build_paper_execution_event(
        event_id="pevt-recovered",
        aggregate_version=3,
        event_type="OutcomeUnknown",
        source="process_recovery",
        after=recovered,
        before=claimed,
        causation_id="pevt-claimed",
    )
    return reduce_paper_execution_event(projection, event), recovered


def _operator_resolution(unknown, **overrides):
    at = unknown.updated_at + timedelta(minutes=11)
    updates = {
        "status": "rejected",
        "reconciliation_status": "reconciled",
        "last_error_code": "operator_resolved_no_broker_evidence",
        "updated_at": at,
        "reconciled_at": at,
        "revision": unknown.revision + 1,
    }
    updates.update(overrides)
    return PaperOrderDispatch.model_validate(
        unknown.model_copy(update=updates).model_dump()
    )


def test_operator_resolution_closes_only_a_bare_unknown_row() -> None:
    projection, unknown = _operator_unknown()
    resolved = _operator_resolution(unknown)
    event = build_paper_execution_event(
        event_id="pevt-operator",
        aggregate_version=4,
        event_type="OrderRejected",
        source="operator_resolution",
        after=resolved,
        before=unknown,
        causation_id="pevt-recovered",
    )
    assert reduce_paper_execution_event(projection, event).after == resolved


@pytest.mark.parametrize(
    "overrides, source, match",
    [
        ({"last_error_code": "broker_business_rejected"}, "operator_resolution", "operator resolution"),
        ({"reconciliation_status": "pending", "reconciled_at": None}, "operator_resolution", "operator resolution"),
        ({"order_plan_payload": None}, "operator_resolution", "operator resolution"),
        ({}, "local_submission_result", "local submission guard"),
        ({}, "broker_reconciliation", "broker reconciliation"),
    ],
)
def test_operator_resolution_rejects_foreign_deltas_and_origins(overrides, source, match) -> None:
    projection, unknown = _operator_unknown()
    after = _operator_resolution(unknown, **overrides)
    event = build_paper_execution_event(
        event_id="pevt-operator-bad",
        aggregate_version=4,
        event_type="OrderRejected",
        source=source,
        after=after,
        before=unknown,
        causation_id="pevt-recovered",
    )
    with pytest.raises(PaperEventStreamCorruption, match=match):
        reduce_paper_execution_event(projection, event)
