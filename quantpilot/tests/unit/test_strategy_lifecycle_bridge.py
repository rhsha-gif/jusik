"""The lifecycle gate must keep unvalidated strategies out of paper/live paths.

These tests exercise the bridge between the human-review lifecycle
(``promotion.py``) and the execution-level authority (``registry.py``) without
touching the live operator run path: an un-promoted lifecycle status justifies
no registry execution levels, so a strategy built from it can never be selected
for paper or live submission.
"""

from __future__ import annotations

import pytest

from quantpilot.packages.core.strategies.promotion import (
    StrategyLifecycleStatus,
    eligibility_for,
)
from quantpilot.packages.core.strategies.registry import (
    StrategyRegistry,
    StrategyRegistryEntry,
    levels_for_status,
)


UNVALIDATED = [
    StrategyLifecycleStatus.draft,
    StrategyLifecycleStatus.backtested,
    StrategyLifecycleStatus.disabled,
    StrategyLifecycleStatus.revoked,
]


@pytest.mark.parametrize("status", UNVALIDATED)
def test_unvalidated_lifecycle_status_justifies_no_execution_levels(status: StrategyLifecycleStatus) -> None:
    eligibility = eligibility_for(status)
    assert eligibility.can_submit_orders is False
    assert eligibility.justified_registry_levels == ()


def test_registry_entry_built_from_draft_lifecycle_is_not_level5_eligible() -> None:
    # A strategy still in the human-review lifecycle (not yet live_candidate) only
    # justifies levels up to its lifecycle stage. Even if an entry were
    # mistakenly granted level_5 levels, the registry STATUS must still gate it.
    # draft justifies no execution levels at all.
    assert eligibility_for(StrategyLifecycleStatus.draft).justified_registry_levels == ()
    registry = StrategyRegistry(
        [
            StrategyRegistryEntry(
                strategy_id="unvalidated_candidate",
                version="0.1.0",
                status="draft",
                allowed_execution_levels=[],
            )
        ]
    )

    decision = registry.select_for_level5(policy_version=5)

    assert decision.selected_strategy_id is None
    assert decision.rejected["unvalidated_candidate"] == "status_not_level5_eligible"


def test_lifecycle_to_registry_level_bridge_is_monotonic() -> None:
    # paper_candidate -> level_3 only; paper_validated -> guarded; live_candidate -> level_5 candidacy.
    assert eligibility_for(StrategyLifecycleStatus.paper_candidate).justified_registry_levels == ("level_3",)
    assert "guarded_autopilot" in eligibility_for(StrategyLifecycleStatus.paper_validated).justified_registry_levels
    assert "level_5" not in eligibility_for(StrategyLifecycleStatus.paper_validated).justified_registry_levels
    assert "level_5" in eligibility_for(StrategyLifecycleStatus.live_candidate).justified_registry_levels


def test_registry_status_levels_helper_matches_selection_rules() -> None:
    # The centralized helper is the same authority the registry selection uses.
    assert levels_for_status("validated_l5") >= {"level_5", "fully_automated"}
    assert levels_for_status("validated_l4") == {"level_3", "level_4", "guarded_autopilot"}
    assert levels_for_status("draft") == frozenset()
    assert levels_for_status("revoked") == frozenset()
