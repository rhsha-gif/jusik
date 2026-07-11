from quantpilot.packages.core.execution import transitions as domain_transitions
from quantpilot.packages.db import sqlite_repositories


EXPECTED_DISPATCH_TRANSITIONS = {
    "prepared": {"expired_pre_dispatch", "failed_pre_dispatch"},
    "dispatch_claimed": {
        "outcome_unknown",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
    },
    "outcome_unknown": {
        "outcome_unknown",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
        "cancelled",
    },
    "accepted": {"accepted", "partially_filled", "filled", "rejected", "cancelled"},
    "partially_filled": {"partially_filled", "filled", "cancelled"},
    "filled": {"filled"},
    "rejected": {"rejected"},
    "cancelled": {"cancelled"},
    "expired_pre_dispatch": {"expired_pre_dispatch"},
    "failed_pre_dispatch": {"failed_pre_dispatch"},
}

EXPECTED_RECONCILIATION_TRANSITIONS = {
    "pending": {"pending", "blocked", "reconciled"},
    "blocked": {"blocked", "reconciled"},
    "reconciled": {"reconciled"},
}

EXPECTED_CANCEL_TRANSITIONS = {
    "prepared": {"reconciled_cancelled", "reconciled_filled"},
    "cancel_claimed": {
        "cancel_accepted",
        "cancel_outcome_unknown",
        "reconciled_cancelled",
        "reconciled_filled",
        "rejected",
    },
    "cancel_accepted": {
        "cancel_accepted",
        "reconciled_cancelled",
        "reconciled_filled",
    },
    "cancel_outcome_unknown": {
        "cancel_outcome_unknown",
        "reconciled_cancelled",
        "reconciled_filled",
    },
    "reconciled_cancelled": {"reconciled_cancelled"},
    "reconciled_filled": {"reconciled_filled"},
    "rejected": {"rejected", "reconciled_cancelled", "reconciled_filled"},
}

EXPECTED_RESERVATION_TERMINALS = {
    "released_filled",
    "released_cancelled",
    "released_rejected",
    "released_expired",
}

EXPECTED_RESERVATION_RELEASE_BY_DISPATCH = {
    "filled": ("released_filled", "filled"),
    "cancelled": ("released_cancelled", "cancelled"),
    "rejected": ("released_rejected", "rejected"),
    "expired_pre_dispatch": ("released_expired", "expired_pre_dispatch"),
    "failed_pre_dispatch": ("released_expired", "failed_pre_dispatch"),
}


def test_sqlite_reexports_the_pure_transition_objects() -> None:
    names = (
        "PAPER_DISPATCH_TRANSITIONS",
        "PAPER_DISPATCH_RECONCILIATION_TRANSITIONS",
        "PAPER_CANCEL_TRANSITIONS",
        "PAPER_RESERVATION_TERMINALS",
        "PAPER_RESERVATION_RELEASE_BY_DISPATCH",
    )

    for name in names:
        assert getattr(sqlite_repositories, name) is getattr(domain_transitions, name)


def test_shared_transition_definitions_preserve_the_schema_v10_surface() -> None:
    assert domain_transitions.PAPER_DISPATCH_TRANSITIONS == EXPECTED_DISPATCH_TRANSITIONS
    assert (
        domain_transitions.PAPER_DISPATCH_RECONCILIATION_TRANSITIONS
        == EXPECTED_RECONCILIATION_TRANSITIONS
    )
    assert domain_transitions.PAPER_CANCEL_TRANSITIONS == EXPECTED_CANCEL_TRANSITIONS
    assert (
        domain_transitions.PAPER_RESERVATION_TERMINALS
        == EXPECTED_RESERVATION_TERMINALS
    )
    assert (
        domain_transitions.PAPER_RESERVATION_RELEASE_BY_DISPATCH
        == EXPECTED_RESERVATION_RELEASE_BY_DISPATCH
    )
