from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quantpilot.packages.core.strategies.promotion import (
    StrategyLifecycleRecord,
    StrategyLifecycleStatus,
    load_lifecycle_fixture,
)
from quantpilot.packages.core.strategies.registry import StrategyRegistry, StrategyRegistryEntry


SPEC_HASH = "sha256:test-level5-candidate"


def _record(strategy_id: str, *, version: str = "1.0", spec_hash: str = SPEC_HASH) -> StrategyLifecycleRecord:
    return StrategyLifecycleRecord(
        strategy_id=strategy_id,
        version=version,
        spec_hash=spec_hash,
        status=StrategyLifecycleStatus.paper_validated,
    )


def test_strategy_registry_selects_only_validated_level5_entries() -> None:

    registry = StrategyRegistry(
        [
            StrategyRegistryEntry(strategy_id="draft", version="1", status="draft", allowed_execution_levels=[]),
            StrategyRegistryEntry(
                strategy_id="candidate",
                version="1",
                spec_hash="sha256:candidate",
                status="validated_l5",
                allowed_execution_levels=["level_5", "fully_automated"],
                priority=1,
            ),
        ],
        lifecycle_records=[_record("candidate", version="1", spec_hash="sha256:candidate")],
    )

    decision = registry.select_for_level5(policy_version=5)

    assert decision.selected_strategy_id == "candidate"
    assert decision.rejected["draft"] == "status_not_level5_eligible"


def _entry(strategy_id: str, **updates: Any) -> StrategyRegistryEntry:
    values: dict[str, Any] = {
        "strategy_id": strategy_id,
        "version": "1.0",
        "spec_hash": SPEC_HASH,
        "status": "validated_l5",
        "allowed_execution_levels": ["level_5", "fully_automated"],
        "priority": 50,
    }
    values.update(updates)
    return StrategyRegistryEntry(**values)


def test_strategy_registry_blocks_level5_entry_without_lifecycle_evidence() -> None:
    registry = StrategyRegistry([_entry("candidate_without_evidence")])

    decision = registry.select_for_level5(policy_version=5)

    assert decision.selected_strategy_id is None
    assert decision.rejected["candidate_without_evidence"] == "lifecycle_record_missing"


def test_strategy_registry_refuses_disabled_revoked_and_guarded_only_entries() -> None:
    registry = StrategyRegistry(
        [
            _entry("disabled_strategy", status="disabled"),
            _entry("revoked_strategy", status="revoked"),
            _entry("guarded_only", status="validated_l4", allowed_execution_levels=["level_4", "guarded_autopilot"]),
            _entry("l3_only", status="validated_l3", allowed_execution_levels=["level_3"]),
            _entry("status_ok_level_missing", allowed_execution_levels=["level_4"]),
        ]
    )

    decision = registry.select_for_level5(policy_version=5)

    assert decision.selected_strategy_id is None
    assert decision.reason == "no_level5_strategy_eligible"
    assert decision.rejected["disabled_strategy"] == "status_not_level5_eligible"
    assert decision.rejected["revoked_strategy"] == "status_not_level5_eligible"
    assert decision.rejected["guarded_only"] == "status_not_level5_eligible"
    assert decision.rejected["l3_only"] == "status_not_level5_eligible"
    assert decision.rejected["status_ok_level_missing"] == "execution_level_not_allowed"


def test_strategy_registry_selects_lowest_priority_and_respects_version_bounds() -> None:
    registry = StrategyRegistry(
        [
            _entry("second", priority=20),
            _entry("first", priority=10),
            _entry("version_bound", priority=1, min_policy_version=10),
        ],
        lifecycle_records=[_record("first"), _record("second")],
    )

    decision = registry.select_for_level5(policy_version=5)

    assert decision.selected_strategy_id == "first"
    assert decision.eligible_strategy_ids == ["first", "second"]
    assert decision.rejected["version_bound"] == "policy_version_out_of_range"


def test_underperformance_demotes_strategy_out_of_level5_selection() -> None:
    registry = StrategyRegistry([_entry("fading_alpha")])

    action = registry.apply_performance_review("fading_alpha", excess_return=-0.06, max_drawdown=-0.10)

    assert action == "demoted"
    entry = registry.require("fading_alpha")
    assert entry.status == "validated_l4"
    assert "level_5" not in entry.allowed_execution_levels
    assert "fully_automated" not in entry.allowed_execution_levels
    assert registry.select_for_level5(policy_version=5).selected_strategy_id is None


def test_severe_underperformance_disables_strategy() -> None:
    registry = StrategyRegistry([_entry("blown_up")])

    action = registry.apply_performance_review("blown_up", excess_return=-0.02, max_drawdown=-0.25)

    assert action == "disabled"
    entry = registry.require("blown_up")
    assert entry.status == "disabled"
    assert entry.allowed_execution_levels == []
    assert entry.disabled_reason == "underperformance_disable_threshold_breached"
    assert registry.select_for_level5(policy_version=5).selected_strategy_id is None


def test_acceptable_performance_leaves_strategy_unchanged() -> None:
    registry = StrategyRegistry([_entry("steady")])

    action = registry.apply_performance_review("steady", excess_return=0.01, max_drawdown=-0.05)

    assert action == "unchanged"
    assert registry.require("steady").status == "validated_l5"


def test_fixture_registry_selects_only_the_level5_candidate() -> None:
    raw_entries = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "operator_strategy_registry.json").read_text(encoding="utf-8")
    )
    registry = StrategyRegistry(
        [StrategyRegistryEntry(**raw) for raw in raw_entries],
        lifecycle_records=load_lifecycle_fixture(),
    )

    decision = registry.select_for_level5(policy_version=5)

    assert decision.selected_strategy_id == "level5_candidate_fixture"
    assert decision.rejected["pullback_trend_v1"] == "status_not_level5_eligible"
    assert decision.rejected["draft_not_eligible"] == "status_not_level5_eligible"
    assert registry.level4_available() is True
