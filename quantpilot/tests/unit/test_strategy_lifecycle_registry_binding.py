from __future__ import annotations

import json

import pytest

from quantpilot.jobs import run_smoke
from quantpilot.packages.core.execution.state_machine import live_trading_flag_enabled
from quantpilot.packages.core.strategies.lifecycle_binding import (
    bind_registry_entries_to_lifecycle,
    required_lifecycle_status_for_levels,
    validate_registry_entry_lifecycle,
)
from quantpilot.packages.core.strategies.promotion import (
    StrategyLifecycleRecord,
    StrategyLifecycleStatus,
)
from quantpilot.packages.core.strategies.registry import StrategyRegistryEntry


SPEC_HASH = "sha256:fixture-bound-spec"


def _entry(
    *,
    strategy_id: str = "level5_candidate",
    version: str = "1.0",
    status: str = "validated_l5",
    levels: list[str] | None = None,
    spec_hash: str | None = SPEC_HASH,
) -> StrategyRegistryEntry:
    entry = StrategyRegistryEntry(
        strategy_id=strategy_id,
        version=version,
        status=status,  # type: ignore[arg-type]
        allowed_execution_levels=levels or ["level_5", "fully_automated"],  # type: ignore[arg-type]
    )
    if spec_hash is None:
        return entry
    # The binding hash is intentionally not added to the registry schema in this
    # stage; the validation layer reads it defensively when a caller supplies it.
    return entry.model_copy(update={"spec_hash": spec_hash})


def _record(
    status: StrategyLifecycleStatus,
    *,
    strategy_id: str = "level5_candidate",
    version: str = "1.0",
    spec_hash: str = SPEC_HASH,
) -> StrategyLifecycleRecord:
    return StrategyLifecycleRecord(
        strategy_id=strategy_id,
        version=version,
        spec_hash=spec_hash,
        status=status,
    )


def test_validated_l5_without_lifecycle_blocks() -> None:
    decision = validate_registry_entry_lifecycle(_entry(), None)

    assert decision.allowed is False
    assert decision.reason_code == "lifecycle_record_missing"
    assert decision.required_lifecycle_status is StrategyLifecycleStatus.paper_validated


@pytest.mark.parametrize(
    "status",
    [StrategyLifecycleStatus.draft, StrategyLifecycleStatus.backtested],
)
def test_validated_l5_with_unvalidated_lifecycle_blocks(status: StrategyLifecycleStatus) -> None:
    decision = validate_registry_entry_lifecycle(_entry(), _record(status))

    assert decision.allowed is False
    assert decision.reason_code == "lifecycle_status_insufficient"
    assert decision.lifecycle_status is status


def test_validated_l5_with_paper_validated_matching_spec_hash_allows_level5_candidate() -> None:
    decision = validate_registry_entry_lifecycle(
        _entry(),
        _record(StrategyLifecycleStatus.paper_validated),
    )

    assert decision.allowed is True
    assert decision.reason_code == "lifecycle_bound"
    assert decision.required_lifecycle_status is StrategyLifecycleStatus.paper_validated
    assert decision.authority_levels == ("fully_automated", "level_5")


def test_execution_authority_without_registry_spec_hash_blocks() -> None:
    decision = validate_registry_entry_lifecycle(
        _entry(spec_hash=None),
        _record(StrategyLifecycleStatus.paper_validated),
    )

    assert decision.allowed is False
    assert decision.reason_code == "registry_spec_hash_missing"


def test_spec_hash_mismatch_blocks() -> None:
    decisions = bind_registry_entries_to_lifecycle(
        [_entry(spec_hash="sha256:registry-spec")],
        [_record(StrategyLifecycleStatus.paper_validated, spec_hash="sha256:lifecycle-spec")],
    )

    decision = decisions["level5_candidate"]
    assert decision.allowed is False
    assert decision.reason_code == "spec_hash_mismatch"


@pytest.mark.parametrize(
    "status",
    [StrategyLifecycleStatus.disabled, StrategyLifecycleStatus.revoked],
)
def test_disabled_or_revoked_lifecycle_blocks(status: StrategyLifecycleStatus) -> None:
    decision = validate_registry_entry_lifecycle(_entry(), _record(status))

    assert decision.allowed is False
    assert decision.reason_code == f"lifecycle_status_{status.value}"


@pytest.mark.parametrize("status", ["disabled", "revoked"])
def test_disabled_or_revoked_registry_status_blocks(status: str) -> None:
    decision = validate_registry_entry_lifecycle(
        _entry(status=status),
        _record(StrategyLifecycleStatus.live_candidate),
    )

    assert decision.allowed is False
    assert decision.reason_code == f"registry_status_{status}"


def test_live_or_canary_authority_requires_live_candidate() -> None:
    assert required_lifecycle_status_for_levels(["live_canary"]) is StrategyLifecycleStatus.live_candidate
    assert required_lifecycle_status_for_levels(["live_trading_candidate"]) is StrategyLifecycleStatus.live_candidate
    assert required_lifecycle_status_for_levels(["level_5"]) is StrategyLifecycleStatus.paper_validated


def test_live_candidate_alone_does_not_enable_live_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)

    decision = validate_registry_entry_lifecycle(
        _entry(),
        _record(StrategyLifecycleStatus.live_candidate),
    )

    assert decision.allowed is True
    assert live_trading_flag_enabled() is False


def test_default_smoke_remains_level5_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (
        "BROKER_MODE",
        "FULLY_AUTOMATED_OPERATOR_ENABLED",
        "GUARDED_AUTOPILOT_ENABLED",
        "LIVE_TRADING_ENABLED",
        "MARKET_ORDERS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    exit_code = run_smoke.main()
    summary = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert summary["broker"] == "mock"
    assert summary["live_trading_enabled"] is False
    assert summary["operator"]["fallback"] == "level5_flag_disabled"
    assert summary["operator"]["submitted_order_plan_ids"] == []
