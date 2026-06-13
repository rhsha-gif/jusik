from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from quantpilot.packages.core.schemas import HarnessModel


StrategyRegistryStatus = Literal["draft", "validated_l3", "validated_l4", "validated_l5", "disabled", "revoked"]
ExecutionLevel = Literal["level_3", "level_4", "level_5", "guarded_autopilot", "fully_automated"]

LEVEL5_EXECUTION_LEVELS = {"level_5", "fully_automated"}
LEVEL4_EXECUTION_LEVELS = {"level_4", "guarded_autopilot"}

# The execution levels each registry *status* earns. This is the single source
# of truth for status->levels questions, replacing status literals scattered
# across selection and availability checks. A strategy is only ever eligible
# when both its status earns the level AND its allowed_execution_levels list it.
REGISTRY_STATUS_LEVELS: dict[str, frozenset[str]] = {
    "draft": frozenset(),
    "validated_l3": frozenset({"level_3"}),
    "validated_l4": frozenset({"level_3", "level_4", "guarded_autopilot"}),
    "validated_l5": frozenset({"level_3", "level_4", "guarded_autopilot", "level_5", "fully_automated"}),
    "disabled": frozenset(),
    "revoked": frozenset(),
}


def levels_for_status(status: str) -> frozenset[str]:
    return REGISTRY_STATUS_LEVELS.get(status, frozenset())

# Demotion ladder: a validated strategy loses exactly one validation level per demotion.
DEMOTION_LADDER: dict[str, str] = {
    "validated_l5": "validated_l4",
    "validated_l4": "validated_l3",
    "validated_l3": "disabled",
}

# Deterministic underperformance thresholds. Breaching the disable thresholds removes the
# strategy from automatic selection entirely; breaching only the demote threshold lowers
# its validation level by one.
UNDERPERFORMANCE_DISABLE_MAX_DRAWDOWN = -0.20
UNDERPERFORMANCE_DISABLE_EXCESS_RETURN = -0.10
UNDERPERFORMANCE_DEMOTE_EXCESS_RETURN = -0.05


class StrategyRegistryEntry(HarnessModel):
    strategy_id: str
    version: str
    spec_hash: str | None = None
    status: StrategyRegistryStatus
    allowed_execution_levels: list[ExecutionLevel]
    priority: int = 100
    max_policy_version: int | None = None
    min_policy_version: int | None = None
    disabled_reason: str | None = None


class StrategySelectionDecision(HarnessModel):
    selected_strategy_id: str | None
    selected_version: str | None
    eligible_strategy_ids: list[str]
    rejected: dict[str, str]
    reason: str


class StrategyRegistry:
    def __init__(
        self,
        entries: list[StrategyRegistryEntry] | None = None,
        *,
        lifecycle_records: Iterable[object] | None = None,
    ) -> None:
        self._entries: dict[str, StrategyRegistryEntry] = {}
        self._lifecycle_records = list(lifecycle_records or [])
        for entry in entries or []:
            self.add(entry)

    def add(self, entry: StrategyRegistryEntry) -> StrategyRegistryEntry:
        if entry.strategy_id in self._entries:
            raise ValueError(f"duplicate registry entry: {entry.strategy_id}")
        self._entries[entry.strategy_id] = entry
        return entry

    def get(self, strategy_id: str) -> StrategyRegistryEntry | None:
        return self._entries.get(strategy_id)

    def require(self, strategy_id: str) -> StrategyRegistryEntry:
        entry = self.get(strategy_id)
        if entry is None:
            raise KeyError(f"missing registry entry: {strategy_id}")
        return entry

    def entries(self) -> list[StrategyRegistryEntry]:
        return list(self._entries.values())

    def set_lifecycle_records(self, records: Iterable[object]) -> None:
        self._lifecycle_records = list(records)

    def _lifecycle_rejection_reason(self, entry: StrategyRegistryEntry) -> str | None:
        from quantpilot.packages.core.strategies.lifecycle_binding import (
            bind_registry_entries_to_lifecycle,
        )

        decision = bind_registry_entries_to_lifecycle([entry], self._lifecycle_records)[entry.strategy_id]
        if decision.allowed:
            return None
        return decision.reason_code

    def _rejection_reason_for_level5(self, entry: StrategyRegistryEntry, *, policy_version: int) -> str | None:
        if not LEVEL5_EXECUTION_LEVELS.intersection(levels_for_status(entry.status)):
            return "status_not_level5_eligible"
        if not LEVEL5_EXECUTION_LEVELS.intersection(entry.allowed_execution_levels):
            return "execution_level_not_allowed"
        if entry.min_policy_version is not None and policy_version < entry.min_policy_version:
            return "policy_version_out_of_range"
        if entry.max_policy_version is not None and policy_version > entry.max_policy_version:
            return "policy_version_out_of_range"
        return self._lifecycle_rejection_reason(entry)

    def select_for_level5(self, *, policy_version: int) -> StrategySelectionDecision:
        eligible: list[StrategyRegistryEntry] = []
        rejected: dict[str, str] = {}
        for entry in self._entries.values():
            reason = self._rejection_reason_for_level5(entry, policy_version=policy_version)
            if reason is None:
                eligible.append(entry)
            else:
                rejected[entry.strategy_id] = reason

        if not eligible:
            return StrategySelectionDecision(
                selected_strategy_id=None,
                selected_version=None,
                eligible_strategy_ids=[],
                rejected=rejected,
                reason="no_level5_strategy_eligible",
            )

        ranked = sorted(eligible, key=lambda entry: (entry.priority, entry.strategy_id))
        selected = ranked[0]
        return StrategySelectionDecision(
            selected_strategy_id=selected.strategy_id,
            selected_version=selected.version,
            eligible_strategy_ids=[entry.strategy_id for entry in ranked],
            rejected=rejected,
            reason="selected_lowest_priority_eligible_strategy",
        )

    def level4_available(self) -> bool:
        return any(
            LEVEL4_EXECUTION_LEVELS.intersection(levels_for_status(entry.status))
            and LEVEL4_EXECUTION_LEVELS.intersection(entry.allowed_execution_levels)
            and self._lifecycle_rejection_reason(entry) is None
            for entry in self._entries.values()
        )

    def demote(self, strategy_id: str, *, reason: str) -> StrategyRegistryEntry:
        entry = self.require(strategy_id)
        next_status = DEMOTION_LADDER.get(entry.status)
        if next_status is None:
            return self.disable(strategy_id, reason=reason)
        allowed = list(entry.allowed_execution_levels)
        if next_status != "validated_l5":
            allowed = [level for level in allowed if level not in LEVEL5_EXECUTION_LEVELS]
        if next_status not in {"validated_l5", "validated_l4"}:
            allowed = [level for level in allowed if level not in LEVEL4_EXECUTION_LEVELS]
        updated = entry.model_copy(update={"status": next_status, "allowed_execution_levels": allowed, "disabled_reason": reason if next_status == "disabled" else entry.disabled_reason})
        self._entries[strategy_id] = updated
        return updated

    def disable(self, strategy_id: str, *, reason: str) -> StrategyRegistryEntry:
        entry = self.require(strategy_id)
        updated = entry.model_copy(update={"status": "disabled", "allowed_execution_levels": [], "disabled_reason": reason})
        self._entries[strategy_id] = updated
        return updated

    def apply_performance_review(
        self,
        strategy_id: str,
        *,
        excess_return: float,
        max_drawdown: float,
    ) -> Literal["disabled", "demoted", "unchanged"]:
        if max_drawdown <= UNDERPERFORMANCE_DISABLE_MAX_DRAWDOWN or excess_return <= UNDERPERFORMANCE_DISABLE_EXCESS_RETURN:
            self.disable(strategy_id, reason="underperformance_disable_threshold_breached")
            return "disabled"
        if excess_return <= UNDERPERFORMANCE_DEMOTE_EXCESS_RETURN:
            self.demote(strategy_id, reason="underperformance_demote_threshold_breached")
            return "demoted"
        return "unchanged"


def default_strategy_registry() -> StrategyRegistry:
    from quantpilot.packages.core.strategies.promotion import load_lifecycle_fixture

    # The default registry deliberately contains no validated_l5 entry: even with every
    # feature flag forced on, a default operator run must fall back instead of submitting.
    return StrategyRegistry(
        [
            StrategyRegistryEntry(
                strategy_id="pullback_trend_v1",
                version="1.0.0",
                spec_hash="sha256:fixture-pullback-trend-v1-validated-snapshot",
                status="validated_l4",
                allowed_execution_levels=["level_3", "level_4", "guarded_autopilot"],
                priority=20,
            ),
            StrategyRegistryEntry(
                strategy_id="pullback_trend_v2",
                version="2.0",
                spec_hash="sha256:fixture-pullback-trend-v2-draft-snapshot",
                status="draft",
                allowed_execution_levels=[],
                priority=30,
            ),
        ],
        lifecycle_records=load_lifecycle_fixture(),
    )
