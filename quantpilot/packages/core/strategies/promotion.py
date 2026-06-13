"""Human-review promotion lifecycle for strategy specifications.

This module adds a third, orthogonal axis to the two strategy vocabularies that
already exist in the harness:

- ``StrategyRecipe.promotion_status`` (``schemas.py``) is the immutable *spec*
  literal. It deliberately cannot express Level 5, so a recipe can never
  self-promote.
- ``StrategyRegistry`` status (``registry.py``) is the *execution-level authority
  record* consulted by ``select_for_level5``/``authorize_level5`` per order.

Neither captures the *human review lifecycle*: a strategy moving
``draft -> backtested -> paper_candidate -> paper_validated -> live_candidate``
only after validation evidence is recorded and a human confirms the step. That
lifecycle lives here.

Design invariants (see ``docs/strategy_authoring_and_promotion.md``):

- Promotion is deterministic and typed. No LLM/RL output can advance a strategy:
  every promotion requires both recorded ``PromotionEvidence`` *and* an explicit
  human confirmation marker (``PROMOTION_CONFIRMATION`` plus a ``confirmed_by``).
- A strategy version is immutable once promoted past ``draft`` -- the record is
  bound to a ``spec_hash`` snapshot and a differing hash is rejected. Changing a
  spec means authoring a *new* version that starts again at ``draft``.
- ``live_candidate`` is a *candidate* status only. It justifies registry Level 5
  candidacy but never enables live trading; that remains a separate,
  human-reviewed live-trading spec with its own fail-closed flags.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field

from quantpilot.packages.core.schemas import (
    HarnessModel,
    StrategyRecipe,
    new_id,
    utc_now,
)
from quantpilot.packages.db.audit import AuditRecorder


# The exact human confirmation marker required to promote a strategy. Mirrors
# ``POLICY_UPDATE_CONFIRMATION`` in ``policy/versioning.py``: a fixed phrase that
# model output is not permitted to synthesize on a human's behalf.
PROMOTION_CONFIRMATION = "confirm strategy promotion"


class StrategyLifecycleStatus(str, Enum):
    draft = "draft"
    backtested = "backtested"
    paper_candidate = "paper_candidate"
    paper_validated = "paper_validated"
    live_candidate = "live_candidate"
    disabled = "disabled"
    revoked = "revoked"


# Forward promotion ladder: exactly one step per promotion. Statuses absent as
# keys (disabled/revoked/live_candidate) have no forward transition.
PROMOTION_LADDER: dict[StrategyLifecycleStatus, StrategyLifecycleStatus] = {
    StrategyLifecycleStatus.draft: StrategyLifecycleStatus.backtested,
    StrategyLifecycleStatus.backtested: StrategyLifecycleStatus.paper_candidate,
    StrategyLifecycleStatus.paper_candidate: StrategyLifecycleStatus.paper_validated,
    StrategyLifecycleStatus.paper_validated: StrategyLifecycleStatus.live_candidate,
}

# Evidence kinds that must be present (attached) before a strategy may enter the
# target status. Promotion is blocked until every required kind is recorded.
REQUIRED_EVIDENCE: dict[StrategyLifecycleStatus, frozenset[str]] = {
    StrategyLifecycleStatus.backtested: frozenset({"backtest_result"}),
    StrategyLifecycleStatus.paper_candidate: frozenset({"backtest_result"}),
    StrategyLifecycleStatus.paper_validated: frozenset({"paper_track_record"}),
    StrategyLifecycleStatus.live_candidate: frozenset({"paper_track_record", "risk_review"}),
}

# Statuses from which automatic order submission is never eligible.
NON_SUBMITTING_STATUSES: frozenset[StrategyLifecycleStatus] = frozenset(
    {
        StrategyLifecycleStatus.draft,
        StrategyLifecycleStatus.backtested,
        StrategyLifecycleStatus.disabled,
        StrategyLifecycleStatus.revoked,
    }
)

# Terminal status: cannot be left. Re-entry must author a new version at draft.
TERMINAL_STATUSES: frozenset[StrategyLifecycleStatus] = frozenset({StrategyLifecycleStatus.revoked})

# The registry execution levels a lifecycle status *justifies*. This is the
# single bridge between the human lifecycle and the execution-level authority in
# ``registry.py``; downstream feature flags and per-order gates still apply.
_JUSTIFIED_REGISTRY_LEVELS: dict[StrategyLifecycleStatus, tuple[str, ...]] = {
    StrategyLifecycleStatus.draft: (),
    StrategyLifecycleStatus.backtested: (),
    StrategyLifecycleStatus.paper_candidate: ("level_3",),
    StrategyLifecycleStatus.paper_validated: ("level_3", "level_4", "guarded_autopilot"),
    StrategyLifecycleStatus.live_candidate: ("level_3", "level_4", "guarded_autopilot", "level_5", "fully_automated"),
    StrategyLifecycleStatus.disabled: (),
    StrategyLifecycleStatus.revoked: (),
}


class StrategyPromotionError(RuntimeError):
    """Base class for deterministic promotion failures."""


class PromotionConfirmationRequired(StrategyPromotionError):
    pass


class MissingPromotionEvidence(StrategyPromotionError):
    pass


class StrategyVersionMismatch(StrategyPromotionError):
    pass


class ImmutableStrategyVersion(StrategyPromotionError):
    pass


class InvalidPromotionTransition(StrategyPromotionError):
    pass


class PromotionEvidence(HarnessModel):
    evidence_id: str = Field(default_factory=lambda: new_id("evid"))
    kind: str
    reference: str
    summary: str
    metrics: dict[str, float] = Field(default_factory=dict)
    recorded_by: str
    recorded_at: datetime = Field(default_factory=utc_now)


class PromotionRecord(HarnessModel):
    """One immutable entry in a strategy's promotion audit trail."""

    from_status: StrategyLifecycleStatus
    to_status: StrategyLifecycleStatus
    evidence_ids: list[str] = Field(default_factory=list)
    confirmed_by: str
    confirmation: str
    promoted_at: datetime = Field(default_factory=utc_now)


class StrategyLifecycleRecord(HarnessModel):
    strategy_id: str
    version: str
    spec_hash: str
    status: StrategyLifecycleStatus = StrategyLifecycleStatus.draft
    evidence: list[PromotionEvidence] = Field(default_factory=list)
    history: list[PromotionRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    disabled_reason: str | None = None

    def evidence_kinds(self) -> set[str]:
        return {item.kind for item in self.evidence}


class StrategyEligibility(HarnessModel):
    status: StrategyLifecycleStatus
    can_backtest: bool
    can_submit_orders: bool
    justified_registry_levels: tuple[str, ...]
    reason: str


def eligibility_for(status: StrategyLifecycleStatus) -> StrategyEligibility:
    """Single source of truth mapping a lifecycle status to capabilities.

    Centralizing this here removes the duplicated status checks the stage's
    optimization scope calls out: callers ask one question instead of
    re-deriving "may this status backtest / submit / run at level N".
    """

    # Draft and backtested may be researched/backtested but never submit. Disabled
    # and revoked may do nothing at all.
    can_submit = status not in NON_SUBMITTING_STATUSES
    can_backtest = status not in {StrategyLifecycleStatus.disabled, StrategyLifecycleStatus.revoked}
    levels = _JUSTIFIED_REGISTRY_LEVELS[status]
    if status in {StrategyLifecycleStatus.disabled, StrategyLifecycleStatus.revoked}:
        reason = f"{status.value}_strategy_is_never_eligible"
    elif not can_submit:
        reason = f"{status.value}_may_backtest_but_not_submit"
    else:
        reason = f"{status.value}_eligible_for_{'_'.join(levels)}"
    return StrategyEligibility(
        status=status,
        can_backtest=can_backtest,
        can_submit_orders=can_submit,
        justified_registry_levels=levels,
        reason=reason,
    )


def compute_spec_hash(recipe: StrategyRecipe) -> str:
    """Deterministic content hash binding a lifecycle record to a spec snapshot.

    Canonical JSON with sorted keys so the same recipe always hashes the same
    value regardless of field ordering. No clock or randomness is involved, so
    the hash is stable across processes and runs.
    """

    canonical = json.dumps(recipe.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StrategyPromotionService:
    """Deterministic promotion gate over the strategy lifecycle.

    Holds lifecycle records in memory (mirroring ``InMemoryRepository``) and,
    optionally, emits audit events. The service is the only sanctioned way to
    advance a strategy's lifecycle status.
    """

    def __init__(
        self,
        records: list[StrategyLifecycleRecord] | None = None,
        *,
        audit: AuditRecorder | None = None,
        user_id: str = "fixture-user",
    ) -> None:
        self._records: dict[str, StrategyLifecycleRecord] = {}
        self._audit = audit
        self._user_id = user_id
        for record in records or []:
            self._records[record.strategy_id] = record

    # -- read -------------------------------------------------------------
    def get(self, strategy_id: str) -> StrategyLifecycleRecord | None:
        return self._records.get(strategy_id)

    def require(self, strategy_id: str) -> StrategyLifecycleRecord:
        record = self._records.get(strategy_id)
        if record is None:
            raise StrategyPromotionError(f"no lifecycle record for strategy: {strategy_id}")
        return record

    def records(self) -> list[StrategyLifecycleRecord]:
        return list(self._records.values())

    def eligibility(self, strategy_id: str) -> StrategyEligibility:
        return eligibility_for(self.require(strategy_id).status)

    # -- write ------------------------------------------------------------
    def register_draft(self, *, strategy_id: str, version: str, spec_hash: str) -> StrategyLifecycleRecord:
        if strategy_id in self._records:
            raise StrategyPromotionError(f"lifecycle record already exists: {strategy_id}")
        record = StrategyLifecycleRecord(strategy_id=strategy_id, version=version, spec_hash=spec_hash)
        self._records[strategy_id] = record
        self._emit(record, action="strategy_lifecycle_registered", before=None)
        return record

    def attach_evidence(self, *, strategy_id: str, version: str, evidence: PromotionEvidence) -> StrategyLifecycleRecord:
        record = self.require(strategy_id)
        self._assert_version(record, version)
        before = record.model_copy(deep=True)
        updated = record.model_copy(
            update={"evidence": [*record.evidence, evidence], "updated_at": utc_now()}
        )
        self._records[strategy_id] = updated
        self._emit(updated, action="strategy_evidence_attached", before=before)
        return updated

    def promote(
        self,
        *,
        strategy_id: str,
        version: str,
        confirmation: str,
        confirmed_by: str,
        target: StrategyLifecycleStatus | None = None,
        spec_hash: str | None = None,
    ) -> StrategyLifecycleRecord:
        record = self.require(strategy_id)
        # 1. The promotion must name the exact version on record.
        self._assert_version(record, version)
        # 2. Immutability: a supplied spec hash must match the locked snapshot.
        if spec_hash is not None and spec_hash != record.spec_hash:
            raise ImmutableStrategyVersion(
                f"strategy {strategy_id} v{version} is locked to a different spec; author a new version"
            )
        # 3. Determine and validate the single ladder step.
        expected = PROMOTION_LADDER.get(record.status)
        if expected is None:
            raise InvalidPromotionTransition(
                f"strategy {strategy_id} in status '{record.status.value}' has no forward promotion"
            )
        resolved_target = target or expected
        if resolved_target != expected:
            raise InvalidPromotionTransition(
                f"strategy {strategy_id} may only promote {record.status.value} -> {expected.value}, "
                f"not {resolved_target.value}"
            )
        # 4. Required evidence must be recorded before the transition.
        required = REQUIRED_EVIDENCE.get(resolved_target, frozenset())
        missing = required - record.evidence_kinds()
        if missing:
            raise MissingPromotionEvidence(
                f"promotion to {resolved_target.value} requires evidence kinds {sorted(missing)}"
            )
        # 5. Human confirmation marker. This is what keeps LLM/RL output from
        #    ever advancing a strategy: approval needs the exact phrase and a
        #    non-empty human attribution.
        if confirmation != PROMOTION_CONFIRMATION or not confirmed_by.strip():
            raise PromotionConfirmationRequired(
                f"promotion requires confirmation '{PROMOTION_CONFIRMATION}' and a human confirmed_by"
            )

        evidence_ids = [item.evidence_id for item in record.evidence if item.kind in required]
        transition = PromotionRecord(
            from_status=record.status,
            to_status=resolved_target,
            evidence_ids=evidence_ids,
            confirmed_by=confirmed_by,
            confirmation=confirmation,
        )
        before = record.model_copy(deep=True)
        updated = record.model_copy(
            update={
                "status": resolved_target,
                "history": [*record.history, transition],
                "updated_at": utc_now(),
            }
        )
        self._records[strategy_id] = updated
        self._emit(updated, action="strategy_promoted", before=before)
        return updated

    def revoke(self, *, strategy_id: str, reason: str) -> StrategyLifecycleRecord:
        return self._terminal(strategy_id, StrategyLifecycleStatus.revoked, reason, "strategy_revoked")

    def disable(self, *, strategy_id: str, reason: str) -> StrategyLifecycleRecord:
        return self._terminal(strategy_id, StrategyLifecycleStatus.disabled, reason, "strategy_lifecycle_disabled")

    # -- internals --------------------------------------------------------
    def _terminal(
        self,
        strategy_id: str,
        status: StrategyLifecycleStatus,
        reason: str,
        action: str,
    ) -> StrategyLifecycleRecord:
        record = self.require(strategy_id)
        if record.status in TERMINAL_STATUSES:
            raise InvalidPromotionTransition(f"strategy {strategy_id} is already {record.status.value}")
        before = record.model_copy(deep=True)
        updated = record.model_copy(update={"status": status, "disabled_reason": reason, "updated_at": utc_now()})
        self._records[strategy_id] = updated
        self._emit(updated, action=action, before=before)
        return updated

    @staticmethod
    def _assert_version(record: StrategyLifecycleRecord, version: str) -> None:
        if version != record.version:
            raise StrategyVersionMismatch(
                f"strategy {record.strategy_id} is version {record.version}, not {version}"
            )

    def _emit(self, record: StrategyLifecycleRecord, *, action: str, before: StrategyLifecycleRecord | None) -> None:
        if self._audit is None:
            return
        self._audit.emit(
            user_id=self._user_id,
            entity_type="strategy_lifecycle",
            entity_id=record.strategy_id,
            action=action,
            before_state=before,
            after_state=record,
            source="strategy_promotion_service",
        )


def default_lifecycle_fixture_path() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "strategy_lifecycle_records.json"


def load_lifecycle_fixture(path: Path | None = None) -> list[StrategyLifecycleRecord]:
    fixture_path = path or default_lifecycle_fixture_path()
    with fixture_path.open("r", encoding="utf-8") as handle:
        raw: list[dict[str, Any]] = json.load(handle)
    return [StrategyLifecycleRecord.model_validate(item) for item in raw]
