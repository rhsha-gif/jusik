"""Restart-safe professional position-risk, retirement, and cadence orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import floor, isclose
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import Field

from quantpilot.packages.core.execution.state_machine import (
    ApprovalRequired,
    RiskCheckRequired,
    authorize_level5,
    transition_order_plan,
)
from quantpilot.packages.core.execution.paper_submission import (
    PaperSubmissionRejected,
)
from quantpilot.packages.core.harness_service import HarnessService
from quantpilot.packages.core.marketdata.types import Quote
from quantpilot.packages.core.operator.position_ledger import (
    ManagedPositionBinding,
    ManagedPositionState,
    OperatorCycleClaim,
    PendingLiquidationCheckpoint,
    OperatorSafetyState,
    PaperRunCheckpoint,
    StateStoreProvenance,
    StrategyOperatorState,
)
from quantpilot.packages.core.operator.retirement import (
    MarketableLimitLiquidationInput,
    build_marketable_limit_liquidation_decision,
)
from quantpilot.packages.core.risk.position_exit import (
    PositionRiskDecision,
    PositionRiskInput,
    evaluate_position_risk,
)
from quantpilot.packages.core.schemas import (
    BrokerMode,
    Fill,
    HarnessModel,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    ProcessedFillRecord,
    ProposalExplanation,
    StrategyRecipe,
    UserPolicy,
)
from quantpilot.packages.core.strategies.performance_review import (
    StrategyHealthDecision,
    StrategyHealthInput,
    evaluate_strategy_health,
)
from quantpilot.packages.core.strategies.registry import (
    StrategyRegistry,
    StrategyRegistryEntry,
)


KST = ZoneInfo("Asia/Seoul")
RISK_CADENCE_SECONDS = 60
WEEKLY_REBALANCE_LEASE_SECONDS = 300
OPEN_PENDING_STATUSES = {
    "prepared",
    "submitted",
    "accepted",
    "partially_filled",
    "outcome_unknown",
}


class ProfessionalStateStore(Protocol):
    @property
    def provenance(self) -> StateStoreProvenance: ...

    def insert_run_checkpoint(
        self,
        checkpoint: PaperRunCheckpoint,
    ) -> PaperRunCheckpoint: ...
    def update_run_checkpoint(
        self,
        checkpoint: PaperRunCheckpoint,
    ) -> PaperRunCheckpoint: ...
    def find_run_checkpoint_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> PaperRunCheckpoint | None: ...
    def save_operator_safety_state(
        self,
        state: OperatorSafetyState,
    ) -> OperatorSafetyState: ...
    def load_operator_safety_state(
        self,
        policy_id: str,
    ) -> OperatorSafetyState | None: ...
    def save_position(self, position: ManagedPositionState) -> ManagedPositionState: ...
    def load_position(
        self,
        policy_id: str,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
    ) -> ManagedPositionState | None: ...
    def list_positions(self) -> list[ManagedPositionState]: ...
    def delete_position(
        self,
        policy_id: str,
        strategy_id: str,
        strategy_version: str,
        symbol: str,
    ) -> bool: ...
    def save_strategy_operator_state(
        self,
        state: StrategyOperatorState,
    ) -> StrategyOperatorState: ...
    def load_strategy_operator_state(
        self,
        policy_id: str,
        strategy_id: str,
        strategy_version: str,
    ) -> StrategyOperatorState | None: ...
    def insert_pending_liquidation(
        self,
        checkpoint: PendingLiquidationCheckpoint,
    ) -> PendingLiquidationCheckpoint: ...
    def update_pending_liquidation(
        self,
        checkpoint: PendingLiquidationCheckpoint,
    ) -> PendingLiquidationCheckpoint: ...
    def load_pending_liquidation(
        self,
        order_plan_id: str,
    ) -> PendingLiquidationCheckpoint | None: ...
    def list_pending_liquidations(
        self,
        *,
        include_reconciled: bool = False,
    ) -> list[PendingLiquidationCheckpoint]: ...
    def claim_operator_cycle(self, claim: OperatorCycleClaim) -> bool: ...
    def release_operator_cycle_claim(self, claim: OperatorCycleClaim) -> bool: ...
    def complete_operator_cycle_claim(
        self,
        claim: OperatorCycleClaim,
        *,
        completed_at: datetime,
    ) -> OperatorCycleClaim: ...
    def list_operator_cycle_claims(self) -> list[OperatorCycleClaim]: ...
    def load_processed_fill(self, fill_id: str) -> ProcessedFillRecord | None: ...
    def apply_fill_reconciliation(
        self,
        *,
        records: list[ProcessedFillRecord],
        expected_position: ManagedPositionState | None,
        next_position: ManagedPositionState | None,
        reconciled_account_quantity: float,
    ) -> ManagedPositionState | None: ...


class StrategyHealthReviewResult(HarnessModel):
    decision: StrategyHealthDecision
    state: StrategyOperatorState
    registry_status: str


class WeeklyRebalanceClaim(HarnessModel):
    claimed: bool
    bucket: str | None = None
    reason_code: str
    state: StrategyOperatorState | None = None
    claim: OperatorCycleClaim | None = None


class ProfessionalPositionCycleResult(HarnessModel):
    status: Literal[
        "submitted",
        "no_action",
        "not_due",
        "duplicate_cycle",
        "awaiting_reconciliation",
        "blocked",
        "reconciled",
    ]
    state: StrategyOperatorState
    position_decisions: list[PositionRiskDecision] = Field(default_factory=list)
    created_order_plan_ids: list[str] = Field(default_factory=list)
    submitted_order_plan_ids: list[str] = Field(default_factory=list)
    blocked_order_plan_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def risk_minute_bucket(value: datetime) -> str:
    if not _is_aware(value):
        raise ValueError("risk cycle time must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def rebalance_week_bucket(value: datetime) -> str:
    if not _is_aware(value):
        raise ValueError("rebalance cycle time must be timezone-aware")
    iso = value.astimezone(KST).isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def risk_evaluation_due(
    last_evaluated_at: datetime | None,
    evaluated_at: datetime,
) -> tuple[bool, str]:
    if not _is_aware(evaluated_at):
        return False, "evaluation_timestamp_naive"
    if last_evaluated_at is None:
        return True, "risk_evaluation_due"
    if not _is_aware(last_evaluated_at):
        return False, "persisted_risk_timestamp_naive"
    age = (evaluated_at - last_evaluated_at).total_seconds()
    if age < 0:
        return False, "clock_regression"
    if age < RISK_CADENCE_SECONDS:
        return False, "risk_evaluation_not_due"
    return True, "risk_evaluation_due"


class ProfessionalOperatorCoordinator:
    def __init__(
        self,
        *,
        harness: HarnessService,
        registry: StrategyRegistry,
        state_store: ProfessionalStateStore,
    ) -> None:
        self.harness = harness
        self.registry = registry
        self.state_store = state_store
        self.harness.pending_liquidation_provider = state_store
        self.harness.operator_safety_state_provider = state_store

    def _require_authoritative_policy(self, policy: UserPolicy) -> None:
        if self.harness.repositories.policies.get(policy.policy_id) != policy:
            raise ValueError("policy is not present unchanged in the policy journal")

    def _require_authoritative_registry_entry(
        self,
        entry: StrategyRegistryEntry,
    ) -> None:
        if self.registry.get(entry.strategy_id) != entry:
            raise ValueError(
                "strategy entry is not present unchanged in the strategy registry"
            )

    def _load_state(
        self,
        *,
        policy_id: str,
        strategy_id: str,
        strategy_version: str,
    ) -> StrategyOperatorState | None:
        return self.state_store.load_strategy_operator_state(
            policy_id,
            strategy_id,
            strategy_version,
        )

    def _persist_state(
        self,
        previous: StrategyOperatorState | None,
        *,
        at: datetime,
        **updates: object,
    ) -> StrategyOperatorState:
        if not _is_aware(at):
            raise ValueError("strategy state writes require an aware timestamp")
        if previous is None:
            state = StrategyOperatorState(updated_at=at, **updates)
        else:
            write_at = max(at, previous.updated_at + timedelta(microseconds=1))
            state = previous.model_copy(
                update={
                    **updates,
                    "updated_at": write_at,
                    "revision": previous.revision + 1,
                }
            )
            state = StrategyOperatorState.model_validate(state.model_dump())
        return self.state_store.save_strategy_operator_state(state)

    def _positions(
        self,
        *,
        policy_id: str,
        strategy_id: str,
        strategy_version: str,
    ) -> list[ManagedPositionState]:
        return sorted(
            [
                item
                for item in self.state_store.list_positions()
                if item.policy_id == policy_id
                and item.strategy_id == strategy_id
                and item.strategy_version == strategy_version
            ],
            key=lambda item: item.symbol,
        )

    def _positions_from_other_strategy_versions(
        self,
        *,
        policy_id: str,
        strategy_id: str,
        strategy_version: str,
    ) -> list[ManagedPositionState]:
        return sorted(
            [
                item
                for item in self.state_store.list_positions()
                if item.policy_id == policy_id
                and item.strategy_id == strategy_id
                and item.strategy_version != strategy_version
            ],
            key=lambda item: (item.strategy_version, item.symbol),
        )

    def _mark_attribution_conflict(
        self,
        position: ManagedPositionState,
        *,
        observed_at: datetime,
        reason: str,
    ) -> ManagedPositionState:
        if position.attribution_status == "conflicted":
            return position
        write_at = max(
            observed_at,
            position.updated_at + timedelta(microseconds=1),
        )
        conflicted = position.model_copy(
            update={
                "attribution_status": "conflicted",
                "attribution_conflict_reason": reason,
                "attribution_conflicted_at": write_at,
                "updated_at": write_at,
                "revision": position.revision + 1,
            }
        )
        return self.state_store.save_position(
            ManagedPositionState.model_validate(conflicted.model_dump())
        )

    @staticmethod
    def _snapshot_position_evidence(
        snapshot: PortfolioSnapshot,
        symbol: str,
    ) -> tuple[tuple[float, float, float] | None, str | None]:
        normalized_symbol = symbol.strip().upper()
        matches = [
            item
            for item in snapshot.positions
            if item.symbol.strip().upper() == normalized_symbol
        ]
        if not matches:
            return None, f"reconciled_position_missing:{normalized_symbol}"
        market_price = matches[0].market_price
        if any(
            not isclose(item.market_price, market_price, abs_tol=0.000001)
            for item in matches[1:]
        ):
            return None, f"reconciled_position_price_conflict:{normalized_symbol}"
        return (
            (
                sum(item.quantity for item in matches),
                sum(item.effective_orderable_quantity for item in matches),
                market_price,
            ),
            None,
        )

    def review_strategy_health(
        self,
        *,
        policy: UserPolicy,
        registry_entry: StrategyRegistryEntry,
        evidence: StrategyHealthInput,
        performance_record_id: str,
        evaluated_at: datetime,
        reapproved: bool = False,
    ) -> StrategyHealthReviewResult:
        self._require_authoritative_policy(policy)
        self._require_authoritative_registry_entry(registry_entry)
        if (
            evidence.strategy_id != registry_entry.strategy_id
            or evidence.strategy_version != registry_entry.version
        ):
            raise ValueError("health evidence must match the exact registry version")
        previous = self._load_state(
            policy_id=policy.policy_id,
            strategy_id=registry_entry.strategy_id,
            strategy_version=registry_entry.version,
        )
        if previous is not None and evaluated_at <= previous.updated_at:
            raise ValueError("strategy health evidence is not newer than persisted state")
        decision = evaluate_strategy_health(evidence)
        status = decision.status
        reasons = list(decision.reason_codes)
        if previous is not None and previous.health_status == "disabled":
            status = "disabled"
            reasons = sorted(set([*previous.reason_codes, "disabled_version_is_sticky"]))
        elif (
            previous is not None
            and previous.health_status == "paused_reapproval"
            and not reapproved
            and status != "disabled"
        ):
            status = "paused_reapproval"
            reasons = sorted(set([*previous.reason_codes, "explicit_reapproval_required"]))

        if status != decision.status or reasons != decision.reason_codes:
            decision = decision.model_copy(
                update={
                    "status": status,
                    "block_new_buys": status != "active",
                    "start_retirement": status == "disabled",
                    "reason_codes": reasons,
                    "reason": "persisted strategy state requires explicit reapproval",
                }
            )

        phase = previous.retirement_phase if previous is not None else "none"
        if status == "disabled" and phase in {"none", "complete"}:
            phase = "risk_first" if phase != "complete" else "complete"
        state = self._persist_state(
            previous,
            at=evaluated_at,
            policy_id=policy.policy_id,
            strategy_id=registry_entry.strategy_id,
            strategy_version=registry_entry.version,
            health_status=status,
            reason_codes=reasons,
            performance_record_id=performance_record_id,
            retirement_phase=phase,
            pending_order_plan_ids=(
                previous.pending_order_plan_ids if previous is not None else []
            ),
            last_risk_evaluated_at=(
                previous.last_risk_evaluated_at if previous is not None else None
            ),
            last_rebalance_session=(
                previous.last_rebalance_session if previous is not None else None
            ),
        )
        if status == "disabled":
            self.registry.disable(
                registry_entry.strategy_id,
                reason="professional_health_disable_threshold_reached",
            )
        self.harness.audit.emit(
            user_id=policy.user_id,
            entity_type="strategy_operator_state",
            entity_id=f"{policy.policy_id}:{registry_entry.strategy_id}:{registry_entry.version}",
            action="strategy_health_reviewed",
            before_state=previous,
            after_state={"decision": decision, "persisted_status": status},
            source="professional_operator",
        )
        return StrategyHealthReviewResult(
            decision=decision,
            state=state,
            registry_status=self.registry.require(registry_entry.strategy_id).status,
        )

    def claim_weekly_rebalance(
        self,
        *,
        policy: UserPolicy,
        strategy_id: str,
        strategy_version: str,
        evaluated_at: datetime,
        acquire: bool = True,
    ) -> WeeklyRebalanceClaim:
        self._require_authoritative_policy(policy)
        current_entry = self.registry.get(strategy_id)
        if current_entry is None or current_entry.version != strategy_version:
            return WeeklyRebalanceClaim(
                claimed=False,
                reason_code="strategy_registry_mismatch",
            )
        if self._positions_from_other_strategy_versions(
            policy_id=policy.policy_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        ):
            return WeeklyRebalanceClaim(
                claimed=False,
                reason_code="strategy_version_migration_required",
            )
        state = self._load_state(
            policy_id=policy.policy_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        )
        if state is None:
            return WeeklyRebalanceClaim(
                claimed=False,
                reason_code="strategy_state_missing",
            )
        if state.health_status != "active":
            return WeeklyRebalanceClaim(
                claimed=False,
                reason_code="strategy_health_blocks_rebalance",
                state=state,
            )
        if state.retirement_phase != "none" or state.pending_order_plan_ids:
            return WeeklyRebalanceClaim(
                claimed=False,
                reason_code="liquidation_precedes_rebalance",
                state=state,
            )
        risk_due, risk_reason = risk_evaluation_due(
            state.last_risk_evaluated_at,
            evaluated_at,
        )
        if risk_due or risk_reason != "risk_evaluation_not_due":
            return WeeklyRebalanceClaim(
                claimed=False,
                reason_code="protective_risk_evaluation_required",
                state=state,
            )
        try:
            bucket = rebalance_week_bucket(evaluated_at)
        except ValueError:
            return WeeklyRebalanceClaim(
                claimed=False,
                reason_code="evaluation_timestamp_naive",
                state=state,
            )
        if not acquire:
            return WeeklyRebalanceClaim(
                claimed=True,
                bucket=bucket,
                reason_code="weekly_rebalance_eligible",
                state=state,
            )
        claim = OperatorCycleClaim(
            policy_id=policy.policy_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            cycle_kind="weekly_rebalance",
            bucket=bucket,
            claimed_at=evaluated_at,
            lease_expires_at=(
                evaluated_at
                + timedelta(seconds=WEEKLY_REBALANCE_LEASE_SECONDS)
            ),
        )
        if not self.state_store.claim_operator_cycle(claim):
            return WeeklyRebalanceClaim(
                claimed=False,
                bucket=bucket,
                reason_code="weekly_rebalance_already_claimed",
                state=state,
            )
        return WeeklyRebalanceClaim(
            claimed=True,
            bucket=bucket,
            reason_code="weekly_rebalance_claimed",
            state=state,
            claim=claim,
        )

    def release_weekly_rebalance(
        self,
        *,
        claim: OperatorCycleClaim,
    ) -> bool:
        if claim.cycle_kind != "weekly_rebalance":
            raise ValueError("only a weekly rebalance claim can be released")
        return self.state_store.release_operator_cycle_claim(claim)

    def require_weekly_rebalance_fence(
        self,
        claim: OperatorCycleClaim,
        *,
        checked_at: datetime,
    ) -> OperatorCycleClaim:
        """Require the exact current lease, or its permanently committed token."""

        if not _is_aware(checked_at):
            raise RiskCheckRequired("weekly rebalance fence time must be timezone-aware")
        current = next(
            (
                item
                for item in self.state_store.list_operator_cycle_claims()
                if item.storage_key == claim.storage_key
            ),
            None,
        )
        if current is None or current != claim:
            raise RiskCheckRequired("weekly rebalance lease ownership changed")
        if (
            current.completed_at is None
            and (
                current.lease_expires_at is None
                or checked_at >= current.lease_expires_at
            )
        ):
            raise RiskCheckRequired("weekly rebalance lease expired")
        return current

    def commit_weekly_rebalance_submission(
        self,
        *,
        claim: OperatorCycleClaim,
        committed_at: datetime,
    ) -> OperatorCycleClaim:
        """Permanently fence this weekly bucket before the first broker side effect."""

        self.require_weekly_rebalance_fence(claim, checked_at=committed_at)
        try:
            return self.state_store.complete_operator_cycle_claim(
                claim,
                completed_at=committed_at,
            )
        except Exception as exc:
            raise RiskCheckRequired(
                "weekly rebalance submission fence could not be committed"
            ) from exc

    def complete_weekly_rebalance(
        self,
        *,
        policy: UserPolicy,
        claim: OperatorCycleClaim,
        completed_at: datetime,
    ) -> StrategyOperatorState:
        if claim.policy_id != policy.policy_id or claim.completed_at is None:
            raise ValueError(
                "weekly rebalance completion requires the committed policy claim"
            )
        self.require_weekly_rebalance_fence(claim, checked_at=completed_at)
        state = self._load_state(
            policy_id=policy.policy_id,
            strategy_id=claim.strategy_id,
            strategy_version=claim.strategy_version,
        )
        if state is None:
            raise ValueError("weekly rebalance completion requires strategy state")
        if state.last_rebalance_session == claim.bucket:
            return state
        return self._persist_state(
            state,
            at=completed_at,
            last_rebalance_session=claim.bucket,
        )

    def _refresh_position_bindings(
        self,
        *,
        positions: list[ManagedPositionState],
        snapshot: PortfolioSnapshot,
    ) -> tuple[list[ManagedPositionState], list[str]]:
        refreshed: list[ManagedPositionState] = []
        blocked: list[str] = []
        account_quantities: dict[str, float] = {}
        for item in snapshot.positions:
            symbol = item.symbol.strip().upper()
            account_quantities[symbol] = (
                account_quantities.get(symbol, 0.0) + item.quantity
            )
        conflict_reasons: dict[str, str] = {}
        if positions:
            policy_id = positions[0].policy_id
            policy_positions = [
                item
                for item in self.state_store.list_positions()
                if item.policy_id == policy_id
            ]
            managed_totals: dict[str, float] = {}
            for item in policy_positions:
                managed_totals[item.symbol] = (
                    managed_totals.get(item.symbol, 0.0) + item.quantity
                )
                if item.attribution_status == "conflicted":
                    conflict_reasons.setdefault(
                        item.symbol,
                        "related_attribution_conflict",
                    )
            for symbol, total in managed_totals.items():
                account_quantity = account_quantities.get(symbol, 0.0)
                if total > account_quantity + 0.000001:
                    reason = (
                        "managed_quantity_exceeds_account"
                        if any(
                            item.symbol == symbol
                            and item.quantity > account_quantity + 0.000001
                            for item in policy_positions
                        )
                        else "aggregate_managed_quantity_exceeds_account"
                    )
                    conflict_reasons[symbol] = reason
            for position in policy_positions:
                reason = conflict_reasons.get(position.symbol)
                if reason is not None and position.attribution_status == "active":
                    self._mark_attribution_conflict(
                        position,
                        observed_at=snapshot.captured_at,
                        reason=reason,
                    )
        for position in positions:
            account_quantity = account_quantities.get(position.symbol, 0.0)
            conflict_reason = conflict_reasons.get(position.symbol)
            if position.attribution_status == "conflicted" or conflict_reason is not None:
                code = (
                    "attribution_conflict_sticky"
                    if position.attribution_status == "conflicted"
                    else conflict_reason
                )
                blocked.append(f"{code}:{position.symbol}")
                continue
            if account_quantity + 0.000001 < position.quantity:
                self._mark_attribution_conflict(
                    position,
                    observed_at=snapshot.captured_at,
                    reason="managed_quantity_exceeds_account",
                )
                blocked.append(f"managed_quantity_exceeds_account:{position.symbol}")
                continue
            if (
                position.reconciled_snapshot_id == snapshot.snapshot_id
                and position.reconciled_at == snapshot.captured_at
            ):
                refreshed.append(position)
                continue
            if snapshot.captured_at <= position.reconciled_at:
                blocked.append(f"snapshot_not_newer_than_ledger:{position.symbol}")
                continue
            candidate = position.model_copy(
                update={
                    "updated_at": snapshot.captured_at,
                    "reconciled_snapshot_id": snapshot.snapshot_id,
                    "reconciled_at": snapshot.captured_at,
                    "revision": position.revision + 1,
                }
            )
            candidate = ManagedPositionState.model_validate(candidate.model_dump())
            refreshed.append(self.state_store.save_position(candidate))
        return refreshed, blocked

    def _recover_pending(
        self,
        *,
        state: StrategyOperatorState,
        snapshot: PortfolioSnapshot,
        evaluated_at: datetime,
    ) -> tuple[StrategyOperatorState, list[str], bool]:
        active_checkpoints = [
            item
            for item in self.state_store.list_pending_liquidations()
            if item.policy_id == state.policy_id
            and item.strategy_id == state.strategy_id
            and item.strategy_version == state.strategy_version
        ]
        checkpoints_by_id = {item.order_plan_id: item for item in active_checkpoints}
        missing_checkpoint_ids: list[str] = []
        for order_plan_id in state.pending_order_plan_ids:
            checkpoint = self.state_store.load_pending_liquidation(order_plan_id)
            if checkpoint is None:
                missing_checkpoint_ids.append(order_plan_id)
            else:
                checkpoints_by_id[order_plan_id] = checkpoint
        checkpoints = sorted(checkpoints_by_id.values(), key=lambda item: item.order_plan_id)
        if missing_checkpoint_ids:
            return (
                state,
                [
                    *[
                        f"pending_checkpoint_missing:{order_plan_id}"
                        for order_plan_id in sorted(missing_checkpoint_ids)
                    ],
                    "pending_liquidation_requires_reconciliation",
                ],
                True,
            )
        pending_ids = sorted({item.order_plan_id for item in checkpoints})
        if pending_ids and sorted(state.pending_order_plan_ids) != pending_ids:
            state = self._persist_state(
                state,
                at=evaluated_at,
                retirement_phase="awaiting_reconciliation",
                pending_order_plan_ids=pending_ids,
                reason_codes=sorted(
                    set([*state.reason_codes, "pending_recovered_from_durable_store"])
                ),
            )
        if not checkpoints:
            return state, [], False

        account_quantities: dict[str, float] = {}
        for item in snapshot.positions:
            symbol = item.symbol.strip().upper()
            account_quantities[symbol] = (
                account_quantities.get(symbol, 0.0) + item.quantity
            )
        unresolved: list[PendingLiquidationCheckpoint] = []
        reconciled_codes: list[str] = []
        completed_purposes: list[str] = []
        for checkpoint in checkpoints:
            if checkpoint.status == "reconciled":
                completed_purposes.append(checkpoint.purpose)
                reconciled_codes.append(
                    f"pending_recovered_as_reconciled:{checkpoint.symbol}"
                )
                continue
            pre_dispatch_recovery_reason = (
                self._pre_dispatch_recovery_reason(checkpoint)
            )
            if (
                checkpoint.status == "prepared"
                or pre_dispatch_recovery_reason is not None
            ):
                recovery_reason = (
                    pre_dispatch_recovery_reason
                    or "prepared_without_submission_attempt"
                )
                journal_order = self.harness.repositories.order_plans.get(
                    checkpoint.order_plan_id
                )
                if journal_order is not None:
                    explanation = journal_order.explanation
                    exact_journal_match = (
                        journal_order.order_plan_id == checkpoint.order_plan_id
                        and journal_order.policy_id == checkpoint.policy_id
                        and journal_order.policy_version == checkpoint.policy_version
                        and journal_order.purpose == checkpoint.purpose
                        and journal_order.idempotency_key
                        == checkpoint.idempotency_key
                        and journal_order.intent.side == "sell"
                        and journal_order.intent.symbol.strip().upper()
                        == checkpoint.symbol
                        and isclose(
                            journal_order.intent.quantity,
                            checkpoint.quantity_requested,
                            abs_tol=0.000001,
                        )
                        and journal_order.intent.limit_price is not None
                        and isclose(
                            journal_order.intent.limit_price,
                            checkpoint.limit_price,
                            abs_tol=0.000001,
                        )
                        and journal_order.intent.quote_time
                        == checkpoint.quote_as_of
                        and explanation is not None
                        and explanation.strategy_id == checkpoint.strategy_id
                        and explanation.strategy_version
                        == checkpoint.strategy_version
                    )
                    recoverable_prebroker_statuses = {
                        OrderStatus.draft,
                        OrderStatus.risk_checked,
                        OrderStatus.proposed,
                        OrderStatus.user_approved,
                        OrderStatus.submitted,
                        OrderStatus.failed,
                    }
                    if (
                        not exact_journal_match
                        or journal_order.status
                        not in recoverable_prebroker_statuses
                    ):
                        unresolved.append(checkpoint)
                        reconciled_codes.append(
                            f"prepared_order_journal_conflict:{checkpoint.symbol}"
                        )
                        continue
                    if journal_order.status != OrderStatus.failed:
                        journal_order.blocked_reason = recovery_reason
                        transition_order_plan(
                            order_plan=journal_order,
                            new_status=OrderStatus.failed,
                            audit=self.harness.audit,
                            user_id=snapshot.user_id,
                            source="professional_operator_recovery",
                            action="order_failed",
                        )
                        self.harness.repositories.order_plans.update(
                            journal_order
                        )
                abandoned = checkpoint.model_copy(
                    update={
                        "status": "failed",
                        "last_error_code": recovery_reason,
                        "updated_at": max(
                            evaluated_at,
                            checkpoint.updated_at + timedelta(microseconds=1),
                        ),
                        "revision": checkpoint.revision + 1,
                    }
                )
                abandoned = self.state_store.update_pending_liquidation(
                    PendingLiquidationCheckpoint.model_validate(
                        abandoned.model_dump()
                    )
                )
                reconciled = abandoned.model_copy(
                    update={
                        "status": "reconciled",
                        "updated_at": (
                            abandoned.updated_at + timedelta(microseconds=1)
                        ),
                        "revision": abandoned.revision + 1,
                    }
                )
                self.state_store.update_pending_liquidation(
                    PendingLiquidationCheckpoint.model_validate(
                        reconciled.model_dump()
                    )
                )
                reconciled_codes.append(
                    (
                        f"{pre_dispatch_recovery_reason}:{checkpoint.symbol}"
                        if pre_dispatch_recovery_reason is not None
                        else "prepared_abandoned_without_submission:"
                        f"{checkpoint.symbol}"
                    )
                )
                continue
            if checkpoint.status in OPEN_PENDING_STATUSES:
                unresolved.append(checkpoint)
                continue
            if snapshot.captured_at <= checkpoint.updated_at:
                unresolved.append(checkpoint)
                continue
            actual_filled = checkpoint.cumulative_filled_quantity
            if actual_filled > 0:
                account_quantity = account_quantities.get(checkpoint.symbol, 0.0)
                expected_account_after_fill = (
                    checkpoint.account_quantity_before - actual_filled
                )
                if account_quantity > expected_account_after_fill + 0.000001:
                    unresolved.append(checkpoint)
                    continue
                managed = self.state_store.load_position(
                    checkpoint.policy_id,
                    checkpoint.strategy_id,
                    checkpoint.strategy_version,
                    checkpoint.symbol,
                )
                expected_managed_after_fill = (
                    checkpoint.quantity_before - actual_filled
                )
                if account_quantity + 0.000001 < expected_managed_after_fill:
                    if managed is not None:
                        self._mark_attribution_conflict(
                            managed,
                            observed_at=snapshot.captured_at,
                            reason="managed_remainder_exceeds_account",
                        )
                    unresolved.append(checkpoint)
                    continue
                if managed is not None and managed.attribution_status == "conflicted":
                    unresolved.append(checkpoint)
                    continue
                checkpoint_fills = set(checkpoint.fill_ids)
                records = [
                    ProcessedFillRecord(
                        fill_id=fill.fill_id,
                        broker_order_id=fill.broker_order_id,
                        order_plan_id=fill.order_plan_id,
                        policy_id=checkpoint.policy_id,
                        policy_version=checkpoint.policy_version,
                        user_id=snapshot.user_id,
                        strategy_id=checkpoint.strategy_id,
                        strategy_version=checkpoint.strategy_version,
                        symbol=checkpoint.symbol,
                        side="sell",
                        quantity=fill.quantity,
                        price=fill.price,
                        notional=fill.notional,
                        filled_at=fill.filled_at,
                        recorded_at=fill.filled_at,
                    )
                    for fill in checkpoint.fill_evidence
                ]
                persisted_records = {
                    record.fill_id: persisted
                    for record in records
                    if (
                        persisted := self.state_store.load_processed_fill(
                            record.fill_id
                        )
                    )
                    is not None
                }
                expected_records = {record.fill_id: record for record in records}
                if persisted_records:
                    if persisted_records != expected_records:
                        unresolved.append(checkpoint)
                        continue
                    expected_remaining = checkpoint.quantity_before - actual_filled
                    if expected_remaining <= 0.000001:
                        if managed is not None:
                            unresolved.append(checkpoint)
                            continue
                    elif (
                        managed is None
                        or not isclose(
                            managed.quantity,
                            expected_remaining,
                            abs_tol=0.000001,
                        )
                        or not checkpoint_fills.issubset(
                            set(managed.processed_fill_ids)
                        )
                    ):
                        unresolved.append(checkpoint)
                        continue
                    completed_purposes.append(checkpoint.purpose)
                    terminal = checkpoint.model_copy(
                        update={
                            "status": "reconciled",
                            "updated_at": max(
                                evaluated_at,
                                checkpoint.updated_at + timedelta(microseconds=1),
                            ),
                            "revision": checkpoint.revision + 1,
                        }
                    )
                    self.state_store.update_pending_liquidation(
                        PendingLiquidationCheckpoint.model_validate(
                            terminal.model_dump()
                        )
                    )
                    reconciled_codes.append(
                        f"pending_reconciled:{checkpoint.symbol}"
                    )
                    continue
                if managed is None:
                    unresolved.append(checkpoint)
                    continue
                processed = set(managed.processed_fill_ids)
                if processed.intersection(checkpoint_fills):
                    unresolved.append(checkpoint)
                    continue
                if not isclose(
                    managed.quantity,
                    checkpoint.quantity_before,
                    abs_tol=0.000001,
                ):
                    unresolved.append(checkpoint)
                    continue
                remaining_quantity = checkpoint.quantity_before - actual_filled
                if remaining_quantity <= 0.000001:
                    next_position: ManagedPositionState | None = None
                else:
                    updated = managed.model_copy(
                        update={
                            "quantity": remaining_quantity,
                            "updated_at": snapshot.captured_at,
                            "reconciled_snapshot_id": snapshot.snapshot_id,
                            "reconciled_at": snapshot.captured_at,
                            "processed_fill_ids": sorted(
                                processed.union(checkpoint_fills)
                            ),
                            "revision": managed.revision + 1,
                        }
                    )
                    next_position = ManagedPositionState.model_validate(
                        updated.model_dump()
                    )
                try:
                    self.state_store.apply_fill_reconciliation(
                        records=records,
                        expected_position=managed,
                        next_position=next_position,
                        reconciled_account_quantity=account_quantity,
                    )
                except (RuntimeError, ValueError):
                    unresolved.append(checkpoint)
                    continue
            completed_purposes.append(checkpoint.purpose)
            terminal = checkpoint.model_copy(
                update={
                    "status": "reconciled",
                    "updated_at": max(
                        evaluated_at,
                        checkpoint.updated_at + timedelta(microseconds=1),
                    ),
                    "revision": checkpoint.revision + 1,
                }
            )
            self.state_store.update_pending_liquidation(
                PendingLiquidationCheckpoint.model_validate(terminal.model_dump())
            )
            reconciled_codes.append(f"pending_reconciled:{checkpoint.symbol}")

        if unresolved:
            unresolved_ids = sorted(item.order_plan_id for item in unresolved)
            if state.pending_order_plan_ids != unresolved_ids:
                state = self._persist_state(
                    state,
                    at=evaluated_at,
                    retirement_phase="awaiting_reconciliation",
                    pending_order_plan_ids=unresolved_ids,
                )
            return state, ["pending_liquidation_requires_reconciliation"], True

        remaining = self._positions(
            policy_id=state.policy_id,
            strategy_id=state.strategy_id,
            strategy_version=state.strategy_version,
        )
        if state.health_status == "disabled":
            if "strategy_retirement" in completed_purposes and not remaining:
                phase = "complete"
            elif "strategy_retirement" in completed_purposes:
                phase = "remaining"
            else:
                phase = "risk_first"
        else:
            phase = "none"
        state = self._persist_state(
            state,
            at=evaluated_at,
            retirement_phase=phase,
            pending_order_plan_ids=[],
            reason_codes=sorted(set([*state.reason_codes, *reconciled_codes])),
        )
        return state, reconciled_codes, False

    def _pre_dispatch_recovery_reason(
        self,
        checkpoint: PendingLiquidationCheckpoint,
    ) -> str | None:
        """Prove that a callback marker never reached the one-attempt POST fence."""

        if (
            checkpoint.status != "submitted"
            or not checkpoint.broker_submission_attempted
        ):
            return None
        provider = self.harness.paper_dispatch_provider
        if provider is None or not hasattr(
            provider,
            "load_paper_order_dispatch",
        ):
            return None
        try:
            dispatch = provider.load_paper_order_dispatch(
                checkpoint.order_plan_id
            )
        except Exception:
            return None
        if dispatch is None:
            return "durable_dispatch_missing_before_claim"
        try:
            identity_matches = (
                dispatch.order_plan_id == checkpoint.order_plan_id
                and dispatch.policy_id == checkpoint.policy_id
                and dispatch.policy_version == checkpoint.policy_version
                and dispatch.strategy_id == checkpoint.strategy_id
                and dispatch.strategy_version == checkpoint.strategy_version
                and dispatch.symbol == checkpoint.symbol
                and dispatch.side == "sell"
                and dispatch.purpose == checkpoint.purpose
                and dispatch.idempotency_key == checkpoint.idempotency_key
                and isclose(
                    dispatch.quantity,
                    checkpoint.quantity_requested,
                    abs_tol=0.000001,
                )
                and isclose(
                    dispatch.limit_price,
                    checkpoint.limit_price,
                    abs_tol=0.000001,
                )
                and dispatch.quote_as_of == checkpoint.quote_as_of
                and dispatch.risk_check_id == checkpoint.risk_check_id
                and dispatch.reconciled_snapshot_id
                == checkpoint.reconciled_snapshot_id
            )
            if not identity_matches:
                return None
            if dispatch.attempt_count != 0 or dispatch.status not in {
                "prepared",
                "expired_pre_dispatch",
                "failed_pre_dispatch",
            }:
                return None
        except (AttributeError, TypeError, ValueError):
            return None
        return "durable_dispatch_unclaimed_before_post"

    def _materialize_order(
        self,
        *,
        policy: UserPolicy,
        registry_entry: StrategyRegistryEntry,
        strategy: StrategyRecipe,
        snapshot: PortfolioSnapshot,
        managed: ManagedPositionState,
        quote: Quote,
        purpose: Literal["protective_exit", "strategy_retirement"],
        requested_quantity: int,
        reason_code: str,
        evaluated_at: datetime,
    ) -> tuple[OrderPlan | None, list[str]]:
        position_evidence, position_block = self._snapshot_position_evidence(
            snapshot,
            managed.symbol,
        )
        if position_evidence is None:
            return None, [position_block or "reconciled_position_invalid"]
        (
            account_quantity,
            account_orderable_quantity,
            market_price,
        ) = position_evidence
        if account_quantity + 0.000001 < managed.quantity:
            return None, [f"managed_quantity_exceeds_account:{managed.symbol}"]
        requested_quantity = min(
            requested_quantity,
            floor(account_orderable_quantity + 0.000001),
        )
        if requested_quantity <= 0:
            return None, [
                f"reconciled_orderable_quantity_unavailable:{managed.symbol}"
            ]
        current_weight = (
            managed.quantity * market_price / snapshot.equity
        )
        decision = build_marketable_limit_liquidation_decision(
            MarketableLimitLiquidationInput(
                purpose=purpose,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                strategy_id=managed.strategy_id,
                strategy_version=managed.strategy_version,
                symbol=managed.symbol,
                quantity_held=int(managed.quantity),
                quantity_requested=requested_quantity,
                current_weight=current_weight,
                best_bid=quote.bid,
                quote_as_of=quote.as_of,
                evaluated_at=evaluated_at,
                max_quote_age_seconds=policy.stale_quote_max_age_seconds,
                managed_position_updated_at=managed.updated_at,
                reconciled_snapshot_id=managed.reconciled_snapshot_id,
                reconciled_at=managed.reconciled_at,
                reason_code=reason_code,
            )
        )
        if decision.status != "ready" or decision.limit_price is None:
            return None, list(decision.reason_codes)
        intent = OrderIntent(
            symbol=managed.symbol,
            side="sell",
            order_type=OrderType.limit,
            quantity=decision.quantity_to_sell,
            limit_price=decision.limit_price,
            notional=decision.notional,
            target_weight=decision.target_weight,
            reason=reason_code,
            quote_time=quote.as_of,
        )
        order = OrderPlan(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            intent=intent,
            purpose=purpose,
            idempotency_key=decision.idempotency_key,
            auto_order_reference_price=decision.limit_price,
            explanation=ProposalExplanation(
                symbol=managed.symbol,
                action="sell",
                quantity=decision.quantity_to_sell,
                target_weight_delta=round(decision.target_weight - current_weight, 6),
                reference_price=decision.limit_price,
                estimated_cash_impact=-decision.notional,
                strategy_id=managed.strategy_id,
                strategy_version=managed.strategy_version,
                signal_reason=reason_code,
                reason_codes=decision.reason_codes,
                current_weight=round(current_weight, 6),
                target_weight=decision.target_weight,
                weight_delta=round(decision.target_weight - current_weight, 6),
                quote_price=decision.limit_price,
                quote_age_seconds=decision.quote_age_seconds or 0.0,
                limit_price=decision.limit_price,
                estimated_notional=decision.notional,
                idempotency_key=decision.idempotency_key,
                policy_version=policy.version,
            ),
        )
        self.harness.repositories.order_plans.add(order)
        binding = ManagedPositionBinding.from_position(managed)
        risk = self.harness.apply_risk_check(
            order.order_plan_id,
            snapshot=snapshot,
            position_binding=binding,
            market_quote=quote,
            now=evaluated_at,
        )
        order = self.harness.repositories.order_plans.require(order.order_plan_id)
        if not risk.passed:
            order.blocked_reason = "risk_check_failed"
            transition_order_plan(
                order_plan=order,
                new_status=OrderStatus.failed,
                audit=self.harness.audit,
                user_id=policy.user_id,
                source="professional_operator",
            )
            self.harness.repositories.order_plans.update(order)
            return None, list(risk.failed_checks)
        if order.explanation is not None:
            order.explanation = order.explanation.model_copy(
                update={
                    "risk_checks_passed": risk.passed_checks,
                    "risk_checks_failed": risk.failed_checks,
                    "risk_check_id": risk.risk_check_id,
                    "risk_check_expires_at": risk.expires_at,
                }
            )
        transition_order_plan(
            order_plan=order,
            new_status=OrderStatus.proposed,
            audit=self.harness.audit,
            user_id=policy.user_id,
            source="professional_operator",
            action="risk_liquidation_proposed",
        )
        self.harness.repositories.order_plans.update(order)
        authority = authorize_level5(
            order_plan=order,
            policy=policy,
            registry_entry=registry_entry,
            strategy=strategy,
            snapshot=snapshot,
            state=self.harness._guardrail_state(
                policy=policy,
                strategy_id=managed.strategy_id,
                exclude_order_plan_id=order.order_plan_id,
                now=evaluated_at,
            ),
            seen_idempotency_keys=self.harness._seen_idempotency_keys(
                exclude_order_plan_id=order.order_plan_id,
                submitted_only=True,
            ),
            now=evaluated_at,
            position_binding=binding,
            market_quote=quote,
        )
        if not authority.authorized:
            order.blocked_reason = authority.first_failed_check
            transition_order_plan(
                order_plan=order,
                new_status=OrderStatus.failed,
                audit=self.harness.audit,
                user_id=policy.user_id,
                source="professional_operator",
            )
            self.harness.repositories.order_plans.update(order)
            return None, [authority.first_failed_check or "authority_failed"]
        return order, []

    def run_position_cycle(
        self,
        *,
        policy: UserPolicy,
        registry_entry: StrategyRegistryEntry,
        strategy: StrategyRecipe,
        snapshot: PortfolioSnapshot,
        risk_inputs: dict[str, PositionRiskInput],
        quotes: dict[str, Quote],
        evaluated_at: datetime,
    ) -> ProfessionalPositionCycleResult:
        self._require_authoritative_policy(policy)
        self._require_authoritative_registry_entry(registry_entry)
        other_version_positions = self._positions_from_other_strategy_versions(
            policy_id=policy.policy_id,
            strategy_id=registry_entry.strategy_id,
            strategy_version=registry_entry.version,
        )
        operating_strategy_version = (
            other_version_positions[0].strategy_version
            if other_version_positions
            else registry_entry.version
        )
        state = self._load_state(
            policy_id=policy.policy_id,
            strategy_id=registry_entry.strategy_id,
            strategy_version=operating_strategy_version,
        )
        if state is None:
            state = self._persist_state(
                None,
                at=evaluated_at,
                policy_id=policy.policy_id,
                strategy_id=registry_entry.strategy_id,
                strategy_version=operating_strategy_version,
                health_status=(
                    "disabled" if other_version_positions else "review_unavailable"
                ),
                reason_codes=(
                    ["strategy_version_superseded_retirement"]
                    if other_version_positions
                    else ["strategy_health_evidence_missing"]
                ),
                retirement_phase=(
                    "risk_first" if other_version_positions else "none"
                ),
            )
        elif other_version_positions and (
            state.health_status != "disabled"
            or state.retirement_phase == "complete"
        ):
            state = self._persist_state(
                state,
                at=evaluated_at,
                health_status="disabled",
                retirement_phase="risk_first",
                reason_codes=sorted(
                    set(
                        [
                            *state.reason_codes,
                            "strategy_version_superseded_retirement",
                        ]
                    )
                ),
            )

        snapshot_age = (
            (evaluated_at - snapshot.captured_at).total_seconds()
            if _is_aware(evaluated_at) and _is_aware(snapshot.captured_at)
            else None
        )
        if (
            snapshot.user_id != policy.user_id
            or snapshot_age is None
            or not 0 <= snapshot_age <= 900
        ):
            return ProfessionalPositionCycleResult(
                status="blocked",
                state=state,
                reason_codes=["reconciled_snapshot_invalid"],
            )

        state, recovery_codes, awaiting = self._recover_pending(
            state=state,
            snapshot=snapshot,
            evaluated_at=evaluated_at,
        )
        if awaiting:
            return ProfessionalPositionCycleResult(
                status="awaiting_reconciliation",
                state=state,
                reason_codes=recovery_codes,
            )

        if (
            state.health_status == "disabled"
            and state.retirement_phase == "complete"
            and self._positions(
                policy_id=policy.policy_id,
                strategy_id=state.strategy_id,
                strategy_version=state.strategy_version,
            )
        ):
            state = self._persist_state(
                state,
                at=evaluated_at,
                retirement_phase="risk_first",
                reason_codes=sorted(
                    set(
                        [
                            *state.reason_codes,
                            "post_retirement_position_reappeared",
                        ]
                    )
                ),
            )

        due, due_reason = risk_evaluation_due(
            state.last_risk_evaluated_at,
            evaluated_at,
        )
        if not due:
            return ProfessionalPositionCycleResult(
                status="blocked" if due_reason != "risk_evaluation_not_due" else "not_due",
                state=state,
                reason_codes=[due_reason],
            )
        claim = OperatorCycleClaim(
            policy_id=policy.policy_id,
            strategy_id=registry_entry.strategy_id,
            strategy_version=state.strategy_version,
            cycle_kind="risk_evaluation",
            bucket=risk_minute_bucket(evaluated_at),
            claimed_at=evaluated_at,
        )
        if not self.state_store.claim_operator_cycle(claim):
            return ProfessionalPositionCycleResult(
                status="duplicate_cycle",
                state=state,
                reason_codes=["risk_cycle_bucket_already_claimed"],
            )

        durable_positions = self._positions(
            policy_id=policy.policy_id,
            strategy_id=registry_entry.strategy_id,
            strategy_version=state.strategy_version,
        )
        positions, binding_blocks = self._refresh_position_bindings(
            positions=durable_positions,
            snapshot=snapshot,
        )
        decisions: list[PositionRiskDecision] = []
        input_blocks: list[str] = list(binding_blocks)
        managed_by_symbol = {item.symbol: item for item in positions}
        for symbol, managed in managed_by_symbol.items():
            request = risk_inputs.get(symbol)
            if request is None:
                input_blocks.append(f"position_risk_input_missing:{symbol}")
                continue
            trusted_quote = quotes.get(symbol)
            if (
                trusted_quote is None
                or trusted_quote.symbol.strip().upper() != symbol
                or trusted_quote.as_of != request.quote_as_of
                or request.evaluated_at != evaluated_at
                or not isclose(
                    trusted_quote.last,
                    request.current_price,
                    abs_tol=0.000001,
                )
            ):
                input_blocks.append(f"position_quote_evidence_mismatch:{symbol}")
                continue
            if (
                request.strategy_id != managed.strategy_id
                or request.strategy_version != managed.strategy_version
                or request.symbol.strip().upper() != managed.symbol
                or not isclose(request.quantity, managed.quantity, abs_tol=0.000001)
                or not isclose(
                    request.average_entry_price,
                    managed.average_entry_price,
                    abs_tol=0.000001,
                )
                or not isclose(request.atr14, managed.atr14, abs_tol=0.000001)
            ):
                input_blocks.append(f"position_risk_input_mismatch:{symbol}")
                continue
            decision = evaluate_position_risk(request)
            if decision.action.value == "blocked":
                input_blocks.extend(
                    f"position_decision_blocked:{symbol}:{code}"
                    for code in decision.reason_codes
                )
            if (
                decision.action.value not in {"exit", "blocked"}
                and request.current_price <= managed.active_stop
            ):
                decision = decision.model_copy(
                    update={
                        "action": "exit",
                        "quantity_to_exit": managed.quantity,
                        "exit_fraction": 1.0,
                        "reason_codes": ["persisted_active_stop_triggered"],
                        "reason": "current quote is at or below the persisted active stop",
                    }
                )
            decisions.append(decision)

        phase = state.retirement_phase
        if state.health_status == "disabled" and phase == "none":
            phase = "risk_first"
        risk_candidates = [
            item for item in decisions if item.action.value in {"exit", "trim"}
        ]
        risk_candidates.sort(
            key=lambda item: (
                0 if item.action.value == "exit" else 1,
                item.symbol,
            )
        )
        selected_managed: ManagedPositionState | None = None
        selected_quantity = 0
        purpose: Literal["protective_exit", "strategy_retirement"] = "protective_exit"
        reason_code = ""
        if risk_candidates:
            selected = risk_candidates[0]
            selected_managed = managed_by_symbol[selected.symbol]
            selected_quantity = floor(selected.quantity_to_exit + 0.000001)
            reason_code = selected.reason_codes[0]
        elif state.health_status == "disabled" and phase in {"risk_first", "remaining"}:
            if input_blocks:
                selected_managed = None
            elif positions:
                phase = "remaining"
                selected_managed = positions[0]
                selected_quantity = floor(selected_managed.quantity + 0.000001)
                purpose = "strategy_retirement"
                reason_code = "strategy_disabled_remaining_liquidation"
            elif durable_positions:
                phase = "remaining"
                input_blocks.append("attribution_resolution_required")
            else:
                phase = "complete"

        state = self._persist_state(
            state,
            at=evaluated_at,
            last_risk_evaluated_at=evaluated_at,
            retirement_phase=phase,
            reason_codes=sorted(set([*state.reason_codes, *input_blocks])),
        )
        if selected_managed is None or selected_quantity <= 0:
            return ProfessionalPositionCycleResult(
                status="reconciled" if recovery_codes else "no_action",
                state=state,
                position_decisions=decisions,
                reason_codes=[*recovery_codes, *input_blocks, "no_liquidation_order_due"],
            )

        quote = quotes.get(selected_managed.symbol)
        if quote is None or quote.bid is None:
            return ProfessionalPositionCycleResult(
                status="blocked",
                state=state,
                position_decisions=decisions,
                reason_codes=[f"best_bid_missing:{selected_managed.symbol}"],
            )
        guardrail = self.harness._guardrail_state(
            policy=policy,
            strategy_id=selected_managed.strategy_id,
            now=evaluated_at,
        )
        available_turnover = max(
            0.0,
            policy.max_daily_turnover - guardrail.daily_turnover_used,
        )
        max_notional = min(policy.single_order_cash_limit, available_turnover)
        max_quantity = floor(max_notional / quote.bid)
        selected_quantity = min(selected_quantity, max_quantity)
        if guardrail.daily_order_count >= policy.max_daily_orders or selected_quantity <= 0:
            return ProfessionalPositionCycleResult(
                status="blocked",
                state=state,
                position_decisions=decisions,
                reason_codes=["risk_liquidation_requires_next_tranche"],
            )

        order, order_blocks = self._materialize_order(
            policy=policy,
            registry_entry=registry_entry,
            strategy=strategy,
            snapshot=snapshot,
            managed=selected_managed,
            quote=quote,
            purpose=purpose,
            requested_quantity=selected_quantity,
            reason_code=reason_code,
            evaluated_at=evaluated_at,
        )
        if order is None:
            return ProfessionalPositionCycleResult(
                status="blocked",
                state=state,
                position_decisions=decisions,
                reason_codes=order_blocks,
            )

        account_quantity = sum(
            item.quantity
            for item in snapshot.positions
            if item.symbol.strip().upper() == selected_managed.symbol
        )
        checkpoint = PendingLiquidationCheckpoint(
            order_plan_id=order.order_plan_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            strategy_id=selected_managed.strategy_id,
            strategy_version=selected_managed.strategy_version,
            symbol=selected_managed.symbol,
            purpose=purpose,
            idempotency_key=order.idempotency_key,
            quantity_before=selected_managed.quantity,
            quantity_requested=order.intent.quantity,
            expected_quantity_after=(
                selected_managed.quantity - order.intent.quantity
            ),
            account_quantity_before=account_quantity,
            expected_account_quantity_after=(
                account_quantity - order.intent.quantity
            ),
            limit_price=order.intent.limit_price or 0,
            quote_as_of=quote.as_of,
            reconciled_snapshot_id=snapshot.snapshot_id,
            status="prepared",
            risk_check_id=None,
            created_at=evaluated_at,
            updated_at=evaluated_at,
        )
        self.state_store.insert_pending_liquidation(checkpoint)
        active_checkpoint = checkpoint
        state = self._persist_state(
            state,
            at=evaluated_at,
            retirement_phase="awaiting_reconciliation",
            pending_order_plan_ids=[order.order_plan_id],
        )
        transition_order_plan(
            order_plan=order,
            new_status=OrderStatus.user_approved,
            audit=self.harness.audit,
            user_id=policy.user_id,
            source="professional_operator",
            action="risk_liquidation_authorized",
        )
        self.harness.repositories.order_plans.update(order)
        binding = ManagedPositionBinding.from_position(selected_managed)

        def mark_submission_attempt(submission_order: OrderPlan) -> None:
            nonlocal active_checkpoint
            if submission_order.risk_check_id is None:
                raise ValueError(
                    "broker submission attempt requires the final risk check ID"
                )
            attempted = active_checkpoint.model_copy(
                update={
                    "status": "submitted",
                    "broker_submission_attempted": True,
                    "risk_check_id": submission_order.risk_check_id,
                    "updated_at": max(
                        evaluated_at + timedelta(microseconds=1),
                        active_checkpoint.updated_at + timedelta(microseconds=1),
                    ),
                    "revision": active_checkpoint.revision + 1,
                }
            )
            active_checkpoint = self.state_store.update_pending_liquidation(
                PendingLiquidationCheckpoint.model_validate(
                    attempted.model_dump()
                )
            )

        try:
            submitted, broker_order, fills = self.harness.submit_order_plan(
                order.order_plan_id,
                snapshot=snapshot,
                position_binding=binding,
                market_quote=quote,
                paper_run_id=(
                    f"risk:{claim.policy_id}:{claim.strategy_id}:"
                    f"{claim.strategy_version}:{claim.bucket}"
                ),
                entry_atr14=selected_managed.atr14,
                now=evaluated_at,
                before_broker_submit=mark_submission_attempt,
            )
        except PaperSubmissionRejected as exc:
            journal_order = self.harness.repositories.order_plans.require(
                order.order_plan_id
            )
            if journal_order.status == OrderStatus.submitted:
                transition_order_plan(
                    order_plan=journal_order,
                    new_status=OrderStatus.rejected,
                    audit=self.harness.audit,
                    user_id=policy.user_id,
                    source="professional_operator",
                    action="paper_order_rejected",
                )
                self.harness.repositories.order_plans.update(journal_order)
            rejected = active_checkpoint.model_copy(
                update={
                    "status": "rejected",
                    "broker_submission_attempted": True,
                    "risk_check_id": journal_order.risk_check_id,
                    "broker_order_id": exc.dispatch.broker_order_id,
                    "last_error_code": (
                        exc.dispatch.last_error_code
                        or "broker_business_rejected"
                    ),
                    "updated_at": max(
                        exc.dispatch.updated_at,
                        active_checkpoint.updated_at
                        + timedelta(microseconds=1),
                    ),
                    "revision": active_checkpoint.revision + 1,
                }
            )
            self.state_store.update_pending_liquidation(
                PendingLiquidationCheckpoint.model_validate(
                    rejected.model_dump()
                )
            )
            return ProfessionalPositionCycleResult(
                status="blocked",
                state=state,
                position_decisions=decisions,
                created_order_plan_ids=[order.order_plan_id],
                blocked_order_plan_ids=[order.order_plan_id],
                reason_codes=["paper_submission_rejected"],
            )
        except (RiskCheckRequired, ApprovalRequired) as exc:
            failed = active_checkpoint.model_copy(
                update={
                    "status": "failed",
                    "last_error_code": type(exc).__name__,
                    "updated_at": max(
                        evaluated_at + timedelta(microseconds=1),
                        active_checkpoint.updated_at + timedelta(microseconds=1),
                    ),
                    "revision": active_checkpoint.revision + 1,
                }
            )
            self.state_store.update_pending_liquidation(
                PendingLiquidationCheckpoint.model_validate(failed.model_dump())
            )
            return ProfessionalPositionCycleResult(
                status="blocked",
                state=state,
                position_decisions=decisions,
                created_order_plan_ids=[order.order_plan_id],
                blocked_order_plan_ids=[order.order_plan_id],
                reason_codes=["final_submission_gate_failed"],
            )
        except Exception:
            journal_order = self.harness.repositories.order_plans.require(
                order.order_plan_id
            )
            if (
                journal_order.status
                not in {
                    OrderStatus.submitted,
                    OrderStatus.accepted,
                    OrderStatus.partially_filled,
                    OrderStatus.filled,
                }
                or journal_order.risk_check_id is None
            ):
                failed = active_checkpoint.model_copy(
                    update={
                        "status": "failed",
                        "last_error_code": "prebroker_submission_failed",
                        "updated_at": max(
                            evaluated_at + timedelta(microseconds=1),
                            active_checkpoint.updated_at
                            + timedelta(microseconds=1),
                        ),
                        "revision": active_checkpoint.revision + 1,
                    }
                )
                self.state_store.update_pending_liquidation(
                    PendingLiquidationCheckpoint.model_validate(failed.model_dump())
                )
                return ProfessionalPositionCycleResult(
                    status="blocked",
                    state=state,
                    position_decisions=decisions,
                    created_order_plan_ids=[order.order_plan_id],
                    blocked_order_plan_ids=[order.order_plan_id],
                    reason_codes=["prebroker_submission_failed"],
                )
            unknown = active_checkpoint.model_copy(
                update={
                    "status": "outcome_unknown",
                    "broker_submission_attempted": True,
                    "risk_check_id": journal_order.risk_check_id,
                    "last_error_code": "broker_submission_outcome_unknown",
                    "updated_at": max(
                        evaluated_at + timedelta(microseconds=1),
                        active_checkpoint.updated_at + timedelta(microseconds=1),
                    ),
                    "revision": active_checkpoint.revision + 1,
                }
            )
            self.state_store.update_pending_liquidation(
                PendingLiquidationCheckpoint.model_validate(unknown.model_dump())
            )
            self.harness.record_broker_health(
                policy_id=policy.policy_id,
                healthy=False,
                reason="broker_submission_outcome_unknown",
            )
            return ProfessionalPositionCycleResult(
                status="awaiting_reconciliation",
                state=state,
                position_decisions=decisions,
                created_order_plan_ids=[order.order_plan_id],
                reason_codes=["broker_submission_outcome_unknown"],
            )
        filled = active_checkpoint.model_copy(
            update={
                "status": (
                    "filled"
                    if submitted.status == OrderStatus.filled
                    else (
                        "partially_filled"
                        if submitted.status == OrderStatus.partially_filled
                        else "accepted"
                    )
                ),
                "broker_submission_attempted": True,
                "risk_check_id": submitted.risk_check_id,
                "broker_order_id": broker_order.broker_order_id,
                "cumulative_filled_quantity": sum(fill.quantity for fill in fills),
                "fill_ids": [fill.fill_id for fill in fills],
                "fill_evidence": fills,
                "updated_at": max(
                    evaluated_at + timedelta(microseconds=1),
                    active_checkpoint.updated_at + timedelta(microseconds=1),
                ),
                "revision": active_checkpoint.revision + 1,
            }
        )
        self.state_store.update_pending_liquidation(
            PendingLiquidationCheckpoint.model_validate(filled.model_dump())
        )
        return ProfessionalPositionCycleResult(
            status="submitted",
            state=state,
            position_decisions=decisions,
            created_order_plan_ids=[order.order_plan_id],
            submitted_order_plan_ids=[order.order_plan_id],
            reason_codes=["risk_liquidation_submitted"],
        )

    def record_reconciled_fills(
        self,
        *,
        policy: UserPolicy,
        order: OrderPlan,
        fills: list[Fill],
        snapshot: PortfolioSnapshot,
        entry_atr14: float | None = None,
    ) -> ManagedPositionState | None:
        """Create/update the attributed ledger only from reconciled broker fills."""

        if order.explanation is None or not fills:
            raise ValueError("attributed order explanation and fills are required")
        self._require_authoritative_policy(policy)
        stored_order = self.harness.repositories.order_plans.get(
            order.order_plan_id
        )
        if stored_order != order:
            raise ValueError("attributed order is not present unchanged in the order journal")
        if (
            order.policy_id != policy.policy_id
            or order.policy_version > policy.version
        ):
            raise ValueError("order policy identity does not match current policy")
        if order.status not in {
            OrderStatus.accepted,
            OrderStatus.partially_filled,
            OrderStatus.filled,
            OrderStatus.cancelled,
        }:
            raise ValueError("order must be broker-accepted before fill reconciliation")
        if snapshot.user_id != policy.user_id:
            raise ValueError("reconciled snapshot user does not match policy")
        if not _is_aware(snapshot.captured_at):
            raise ValueError("reconciled snapshot timestamp must be aware")
        for fill in fills:
            if (
                fill.order_plan_id != order.order_plan_id
                or fill.symbol.strip().upper() != order.intent.symbol.strip().upper()
                or not _is_aware(fill.filled_at)
                or fill.filled_at > snapshot.captured_at
                or not isclose(
                    fill.notional,
                    fill.quantity * fill.price,
                    abs_tol=0.01,
                )
            ):
                raise ValueError("fill evidence does not match the reconciled order")
            stored_fill = self.harness.repositories.fills.get(fill.fill_id)
            if stored_fill != fill:
                raise ValueError("fill evidence is not present in the broker journal")
        quantity = sum(item.quantity for item in fills)
        if quantity > order.intent.quantity + 0.000001:
            raise ValueError("aggregate fills exceed the attributed order quantity")
        journal_quantity = sum(
            item.quantity
            for item in self.harness.repositories.fills.list()
            if item.order_plan_id == order.order_plan_id
        )
        if journal_quantity > order.intent.quantity + 0.000001:
            raise ValueError("broker journal fills exceed the order quantity")
        fill_ids = [item.fill_id for item in fills]
        if len(fill_ids) != len(set(fill_ids)):
            raise ValueError("fill evidence contains duplicate fill IDs")
        broker_order_ids = {item.broker_order_id for item in fills}
        if len(broker_order_ids) != 1:
            raise ValueError("fill evidence must belong to one broker order")
        broker_order = self.harness.repositories.broker_orders.get(
            next(iter(broker_order_ids))
        )
        if (
            broker_order is None
            or broker_order.order_plan_id != order.order_plan_id
            or broker_order.broker_mode not in {BrokerMode.mock, BrokerMode.paper}
            or broker_order.status
            not in {
                OrderStatus.accepted,
                OrderStatus.partially_filled,
                OrderStatus.filled,
                OrderStatus.cancelled,
            }
        ):
            raise ValueError("broker order linkage is missing or mismatched")
        if order.purpose in {"protective_exit", "strategy_retirement"}:
            checkpoint = self.state_store.load_pending_liquidation(
                order.order_plan_id
            )
            checkpoint_evidence = (
                {}
                if checkpoint is None
                else {item.fill_id: item for item in checkpoint.fill_evidence}
            )
            if (
                checkpoint is None
                or checkpoint.order_plan_id != order.order_plan_id
                or checkpoint.policy_id != order.policy_id
                or checkpoint.policy_version != order.policy_version
                or checkpoint.strategy_id != order.explanation.strategy_id
                or checkpoint.strategy_version != order.explanation.strategy_version
                or checkpoint.symbol != order.intent.symbol.strip().upper()
                or checkpoint.purpose != order.purpose
                or checkpoint.idempotency_key != order.idempotency_key
                or not isclose(
                    checkpoint.quantity_requested,
                    order.intent.quantity,
                    abs_tol=0.000001,
                )
                or order.intent.limit_price is None
                or not isclose(
                    checkpoint.limit_price,
                    order.intent.limit_price,
                    abs_tol=0.000001,
                )
                or checkpoint.quote_as_of != order.intent.quote_time
                or order.risk_check_id is None
                or checkpoint.risk_check_id != order.risk_check_id
                or not checkpoint.broker_submission_attempted
                or checkpoint.broker_order_id != broker_order.broker_order_id
                or any(
                    checkpoint_evidence.get(fill.fill_id) != fill
                    for fill in fills
                )
            ):
                raise ValueError(
                    "risk-reducing fill evidence does not match its durable submission checkpoint"
                )
        notional = sum(item.notional for item in fills)
        key = (
            policy.policy_id,
            order.explanation.strategy_id,
            order.explanation.strategy_version,
            order.intent.symbol,
        )
        existing = self.state_store.load_position(*key)
        if existing is not None and existing.attribution_status == "conflicted":
            raise ValueError("conflicted attribution requires explicit resolution")
        if (
            existing is not None
            and order.intent.side == "buy"
            and existing.policy_version != order.policy_version
        ):
            raise ValueError(
                "fill policy version does not match the managed position attribution"
            )
        if (
            existing is not None
            and order.intent.side == "sell"
            and existing.policy_version > order.policy_version
        ):
            raise ValueError(
                "sell fill policy version is older than the managed position attribution"
            )
        records = [
            ProcessedFillRecord(
                fill_id=fill.fill_id,
                broker_order_id=fill.broker_order_id,
                order_plan_id=fill.order_plan_id,
                policy_id=policy.policy_id,
                policy_version=order.policy_version,
                user_id=policy.user_id,
                strategy_id=order.explanation.strategy_id,
                strategy_version=order.explanation.strategy_version,
                symbol=order.intent.symbol,
                side=order.intent.side,
                quantity=fill.quantity,
                price=fill.price,
                notional=fill.notional,
                filled_at=fill.filled_at,
                recorded_at=fill.filled_at,
            )
            for fill in fills
        ]
        persisted_records = {
            record.fill_id: persisted
            for record in records
            if (persisted := self.state_store.load_processed_fill(record.fill_id))
            is not None
        }
        if persisted_records:
            expected_records = {record.fill_id: record for record in records}
            if persisted_records != expected_records:
                raise ValueError("fill replay mixes processed, new, or mismatched fill evidence")
            return existing
        account_quantity = sum(
            item.quantity
            for item in snapshot.positions
            if item.symbol.strip().upper() == order.intent.symbol.strip().upper()
        )
        sibling_positions = [
            item
            for item in self.state_store.list_positions()
            if item.policy_id == policy.policy_id
            and item.symbol == order.intent.symbol.strip().upper()
            and item.storage_key != key
        ]
        if any(
            item.attribution_status == "conflicted" for item in sibling_positions
        ):
            raise ValueError("related attribution conflict requires explicit resolution")
        sibling_quantity = sum(item.quantity for item in sibling_positions)
        if order.intent.side == "buy":
            if entry_atr14 is None or entry_atr14 < 0:
                raise ValueError("entry ATR14 is required for a managed buy fill")
            previous_quantity = existing.quantity if existing is not None else 0.0
            previous_notional = (
                previous_quantity * existing.average_entry_price
                if existing is not None
                else 0.0
            )
            total_quantity = previous_quantity + quantity
            if sibling_quantity + total_quantity > account_quantity + 0.000001:
                raise ValueError("attributed buy quantity exceeds reconciled account quantity")
            average_entry = (previous_notional + notional) / total_quantity
            initial_stop = max(average_entry * 0.92, average_entry - 2 * entry_atr14)
            active_stop = max(
                initial_stop,
                existing.active_stop if existing is not None else initial_stop,
            )
            position = ManagedPositionState(
                policy_id=policy.policy_id,
                strategy_id=order.explanation.strategy_id,
                strategy_version=order.explanation.strategy_version,
                symbol=order.intent.symbol,
                quantity=total_quantity,
                average_entry_price=average_entry,
                atr14=(existing.atr14 if existing is not None else entry_atr14),
                active_stop=active_stop,
                policy_version=order.policy_version,
                opened_at=(existing.opened_at if existing is not None else fills[0].filled_at),
                updated_at=snapshot.captured_at,
                reconciled_snapshot_id=snapshot.snapshot_id,
                reconciled_at=snapshot.captured_at,
                processed_fill_ids=sorted(
                    set(existing.processed_fill_ids if existing is not None else []).union(
                        fill_ids
                    )
                ),
                revision=(existing.revision + 1 if existing is not None else 0),
            )
            return self.state_store.apply_fill_reconciliation(
                records=records,
                expected_position=existing,
                next_position=position,
                reconciled_account_quantity=account_quantity,
            )

        if existing is None:
            raise ValueError("sell fill has no attributed managed position")
        remaining = existing.quantity - quantity
        if remaining < -0.000001:
            raise ValueError("sell fills exceed attributed managed quantity")
        if remaining <= 0.000001:
            if sibling_quantity > account_quantity + 0.000001:
                raise ValueError(
                    "remaining attributed positions exceed reconciled account quantity"
                )
            return self.state_store.apply_fill_reconciliation(
                records=records,
                expected_position=existing,
                next_position=None,
                reconciled_account_quantity=account_quantity,
            )
        if sibling_quantity + remaining > account_quantity + 0.000001:
            raise ValueError("attributed sell remainder exceeds reconciled account quantity")
        updated = existing.model_copy(
            update={
                "quantity": remaining,
                "updated_at": snapshot.captured_at,
                "reconciled_snapshot_id": snapshot.snapshot_id,
                "reconciled_at": snapshot.captured_at,
                "processed_fill_ids": sorted(
                    set(existing.processed_fill_ids).union(fill_ids)
                ),
                "revision": existing.revision + 1,
            }
        )
        return self.state_store.apply_fill_reconciliation(
            records=records,
            expected_position=existing,
            next_position=ManagedPositionState.model_validate(updated.model_dump()),
            reconciled_account_quantity=account_quantity,
        )
