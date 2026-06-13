from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from quantpilot.packages.core.schemas import HarnessModel
from quantpilot.packages.core.strategies.promotion import (
    StrategyLifecycleRecord,
    StrategyLifecycleStatus,
)
from quantpilot.packages.core.strategies.registry import StrategyRegistryEntry, levels_for_status


_LIFECYCLE_STATUS_RANK: dict[StrategyLifecycleStatus, int] = {
    StrategyLifecycleStatus.draft: 0,
    StrategyLifecycleStatus.backtested: 1,
    StrategyLifecycleStatus.paper_candidate: 2,
    StrategyLifecycleStatus.paper_validated: 3,
    StrategyLifecycleStatus.live_candidate: 4,
}

_RESEARCH_ONLY_LEVELS = {
    "",
    "backtest",
    "backtest_only",
    "draft",
    "fixture",
    "local_historical",
    "research",
    "research_only",
    "signal",
    "signal_only",
}
_LEVEL3_LEVELS = {"approval_required", "level_3", "proposal", "proposals"}
_PAPER_OR_LEVEL5_LEVELS = {
    "fully_automated",
    "guarded_autopilot",
    "level_4",
    "level_5",
    "paper_trading",
}
_LIVE_AUTHORITY_LEVELS = {
    "live",
    "live_canary",
    "live_scaled",
    "live_trading",
    "live_trading_candidate",
    "realtime_market_data",
}


class LifecycleRegistryBindingDecision(HarnessModel):
    strategy_id: str
    version: str
    allowed: bool
    reason_code: str
    registry_status: str
    authority_levels: tuple[str, ...] = Field(default_factory=tuple)
    required_lifecycle_status: StrategyLifecycleStatus | None = None
    lifecycle_status: StrategyLifecycleStatus | None = None
    registry_spec_hash: str | None = None
    lifecycle_spec_hash: str | None = None


def _normalize_levels(levels: Iterable[str]) -> set[str]:
    return {str(level).strip().lower() for level in levels}


def required_lifecycle_status_for_levels(
    levels: Iterable[str],
) -> StrategyLifecycleStatus | None:
    """Return the minimum lifecycle status needed for a registry authority set.

    Research and signal-only labels do not require a lifecycle record. Unknown
    non-empty labels fail closed by requiring ``live_candidate``.
    """

    normalized = _normalize_levels(levels)
    execution_levels = normalized - _RESEARCH_ONLY_LEVELS
    if not execution_levels:
        return None
    if execution_levels.intersection(_LIVE_AUTHORITY_LEVELS):
        return StrategyLifecycleStatus.live_candidate
    if execution_levels.intersection(_PAPER_OR_LEVEL5_LEVELS):
        return StrategyLifecycleStatus.paper_validated
    if execution_levels.intersection(_LEVEL3_LEVELS):
        return StrategyLifecycleStatus.backtested
    return StrategyLifecycleStatus.live_candidate


def validate_registry_entry_lifecycle(
    entry: StrategyRegistryEntry,
    lifecycle_record: StrategyLifecycleRecord | None,
) -> LifecycleRegistryBindingDecision:
    """Fail-closed lifecycle binding for one registry entry.

    The binding treats registry execution authority as the intersection of the
    registry status levels and the entry's allowed execution levels. Execution
    authority also needs a registry-side ``spec_hash`` attribute that matches the
    lifecycle record.
    """

    registry_spec_hash = entry.spec_hash
    allowed_levels = _normalize_levels(entry.allowed_execution_levels)
    earned_levels = set(levels_for_status(entry.status))
    authority_levels = tuple(sorted(allowed_levels.intersection(earned_levels)))

    base = {
        "strategy_id": entry.strategy_id,
        "version": entry.version,
        "registry_status": entry.status,
        "authority_levels": authority_levels,
        "registry_spec_hash": registry_spec_hash,
        "lifecycle_status": lifecycle_record.status if lifecycle_record else None,
        "lifecycle_spec_hash": lifecycle_record.spec_hash if lifecycle_record else None,
    }

    if entry.status in {"disabled", "revoked"}:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=False,
            reason_code=f"registry_status_{entry.status}",
        )

    if allowed_levels and not authority_levels:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=False,
            reason_code="registry_status_does_not_earn_allowed_levels",
        )

    required_status = required_lifecycle_status_for_levels(authority_levels)
    if required_status is None:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=True,
            reason_code="no_execution_authority",
        )

    if lifecycle_record is None:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=False,
            required_lifecycle_status=required_status,
            reason_code="lifecycle_record_missing",
        )

    if lifecycle_record.strategy_id != entry.strategy_id:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=False,
            required_lifecycle_status=required_status,
            reason_code="strategy_id_mismatch",
        )

    if lifecycle_record.version != entry.version:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=False,
            required_lifecycle_status=required_status,
            reason_code="strategy_version_mismatch",
        )

    if lifecycle_record.status in {StrategyLifecycleStatus.disabled, StrategyLifecycleStatus.revoked}:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=False,
            required_lifecycle_status=required_status,
            reason_code=f"lifecycle_status_{lifecycle_record.status.value}",
        )

    if registry_spec_hash is None:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=False,
            required_lifecycle_status=required_status,
            reason_code="registry_spec_hash_missing",
        )

    if registry_spec_hash != lifecycle_record.spec_hash:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=False,
            required_lifecycle_status=required_status,
            reason_code="spec_hash_mismatch",
        )

    lifecycle_rank = _LIFECYCLE_STATUS_RANK.get(lifecycle_record.status, -1)
    required_rank = _LIFECYCLE_STATUS_RANK[required_status]
    if lifecycle_rank < required_rank:
        return LifecycleRegistryBindingDecision(
            **base,
            allowed=False,
            required_lifecycle_status=required_status,
            reason_code="lifecycle_status_insufficient",
        )

    return LifecycleRegistryBindingDecision(
        **base,
        allowed=True,
        required_lifecycle_status=required_status,
        reason_code="lifecycle_bound",
    )


def bind_registry_entries_to_lifecycle(
    entries: Iterable[StrategyRegistryEntry],
    records: Iterable[StrategyLifecycleRecord],
) -> dict[str, LifecycleRegistryBindingDecision]:
    """Bind registry entries to lifecycle records keyed by strategy/version/hash."""

    records_by_version: dict[tuple[str, str], StrategyLifecycleRecord] = {}
    records_by_hash: dict[tuple[str, str, str], StrategyLifecycleRecord] = {}
    for record in records:
        records_by_version.setdefault((record.strategy_id, record.version), record)
        records_by_hash[(record.strategy_id, record.version, record.spec_hash)] = record

    decisions: dict[str, LifecycleRegistryBindingDecision] = {}
    for entry in entries:
        registry_spec_hash = entry.spec_hash
        record = None
        if registry_spec_hash is not None:
            record = records_by_hash.get((entry.strategy_id, entry.version, registry_spec_hash))
        if record is None:
            record = records_by_version.get((entry.strategy_id, entry.version))
        decisions[entry.strategy_id] = validate_registry_entry_lifecycle(entry, record)
    return decisions
