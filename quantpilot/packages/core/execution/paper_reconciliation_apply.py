from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperReconciliationResult,
)
from quantpilot.packages.core.execution.state_machine import (
    VALID_TRANSITIONS,
    transition_order_plan,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperDispatchFillEvidence,
    PaperOrderDispatch,
)
from quantpilot.packages.core.schemas import (
    BrokerMode,
    BrokerOrder,
    Fill,
    OrderPlan,
    OrderStatus,
    OrderType,
)
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


KST = ZoneInfo("Asia/Seoul")
_APPLICABLE_STATUSES = {
    "accepted",
    "partially_filled",
    "filled",
    "rejected",
    "cancelled",
}
_PENDING_STATUSES = {"prepared", "dispatch_claimed", "outcome_unknown"}


@dataclass(frozen=True)
class PaperReconciliationApplyResult:
    """Local journal outcome; position attribution is intentionally excluded.

    A caller must rebuild managed-position attribution from an authoritative,
    post-reconciliation broker snapshot after this result is committed.
    """

    applied_order_plan_ids: tuple[str, ...]
    missing_order_plan_ids: tuple[str, ...]
    blocked_order_plan_ids: tuple[str, ...]
    pending_order_plan_ids: tuple[str, ...]
    new_fill_ids: tuple[str, ...]
    blocked_reasons: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _PreparedApplication:
    order_plan: OrderPlan
    broker_order: BrokerOrder
    broker_order_exists: bool
    transition_path: tuple[OrderStatus, ...]
    new_fills: tuple[Fill, ...]


class PaperReconciliationApplier:
    """Apply durable paper reconciliation to the in-memory execution journals.

    This class never creates a missing ``OrderPlan`` and never updates managed
    positions. All durable/local identity and cumulative fill evidence is
    preflighted before the first local write for an order.
    """

    def __init__(
        self,
        *,
        repositories: RepositoryRegistry,
        audit: AuditRecorder | None = None,
    ) -> None:
        self._repositories = repositories
        self._audit = audit or AuditRecorder(repositories.audit_logs)

    def apply(
        self,
        reconciliation: (
            PaperReconciliationResult
            | PaperOrderDispatch
            | Iterable[PaperOrderDispatch]
        ),
    ) -> PaperReconciliationApplyResult:
        dispatches = _coerce_dispatches(reconciliation)
        unique, batch_blocked = _deduplicate_dispatches(dispatches)

        applied: set[str] = set()
        missing: set[str] = set()
        pending: set[str] = set()
        blocked: dict[str, str] = dict(batch_blocked)
        new_fill_ids: set[str] = set()

        for dispatch in unique:
            order_plan_id = dispatch.order_plan_id
            if order_plan_id in blocked:
                continue
            if dispatch.reconciliation_status == "blocked":
                blocked[order_plan_id] = "broker_reconciliation_blocked"
                continue
            if dispatch.status in _PENDING_STATUSES:
                pending.add(order_plan_id)
                continue
            if dispatch.status not in _APPLICABLE_STATUSES:
                blocked[order_plan_id] = "unsupported_dispatch_status"
                continue

            order_plan = self._repositories.order_plans.get(order_plan_id)
            if order_plan is None:
                missing.add(order_plan_id)
                continue

            prepared, reason = self._preflight(dispatch, order_plan)
            if prepared is None:
                blocked[order_plan_id] = reason
                continue

            self._apply_prepared(prepared, dispatch.user_id)
            applied.add(order_plan_id)
            new_fill_ids.update(fill.fill_id for fill in prepared.new_fills)

        return PaperReconciliationApplyResult(
            applied_order_plan_ids=tuple(sorted(applied)),
            missing_order_plan_ids=tuple(sorted(missing)),
            blocked_order_plan_ids=tuple(sorted(blocked)),
            pending_order_plan_ids=tuple(sorted(pending)),
            new_fill_ids=tuple(sorted(new_fill_ids)),
            blocked_reasons=tuple(sorted(blocked.items())),
        )

    def _preflight(
        self,
        dispatch: PaperOrderDispatch,
        order_plan: OrderPlan,
    ) -> tuple[_PreparedApplication | None, str]:
        if not self._order_identity_matches(order_plan, dispatch):
            return None, "order_identity_mismatch"

        policy = self._repositories.policies.get(dispatch.policy_id)
        if policy is None:
            return None, "policy_evidence_missing"
        if (
            policy.policy_id != dispatch.policy_id
            or policy.user_id != dispatch.user_id
            or policy.version != dispatch.policy_version
            or policy.broker != BrokerMode.paper
        ):
            return None, "policy_evidence_mismatch"

        target_status = OrderStatus(dispatch.status)
        transition_path = _transition_path(
            order_plan.status,
            target_status,
            cumulative_filled_quantity=dispatch.cumulative_filled_quantity,
            requested_quantity=dispatch.quantity,
        )
        if transition_path is None:
            return None, "order_state_conflict"

        try:
            accepted_at = _broker_evidence_time(dispatch)
        except ValueError:
            return None, "broker_order_identity_incomplete"
        expected_broker_order = BrokerOrder(
            broker_order_id=dispatch.broker_order_id,
            order_plan_id=dispatch.order_plan_id,
            broker_mode=BrokerMode.paper,
            status=target_status,
            accepted_at=accepted_at,
            broker_reference=dispatch.broker_order_reference,
        )

        broker_orders = self._repositories.broker_orders.list()
        if any(
            item.order_plan_id == dispatch.order_plan_id
            and item.broker_order_id != dispatch.broker_order_id
            for item in broker_orders
        ):
            return None, "broker_order_identity_mismatch"
        existing_broker_order = self._repositories.broker_orders.get(
            dispatch.broker_order_id
        )
        if existing_broker_order is not None:
            if not _broker_identity_matches(
                existing_broker_order,
                expected_broker_order,
            ):
                return None, "broker_order_identity_mismatch"
            if _transition_path(
                existing_broker_order.status,
                target_status,
                cumulative_filled_quantity=dispatch.cumulative_filled_quantity,
                requested_quantity=dispatch.quantity,
            ) is None:
                return None, "broker_order_state_conflict"

        fills = tuple(_fill_from_evidence(dispatch, item) for item in dispatch.fill_evidence)
        if not _cumulative_fill_evidence_matches(dispatch, fills):
            return None, "cumulative_fill_evidence_mismatch"

        expected_by_id = {fill.fill_id: fill for fill in fills}
        repository_fills = self._repositories.fills.list()
        for existing in repository_fills:
            belongs_to_order = existing.order_plan_id == dispatch.order_plan_id
            belongs_to_broker = existing.broker_order_id == dispatch.broker_order_id
            if not (belongs_to_order or belongs_to_broker):
                continue
            expected = expected_by_id.get(existing.fill_id)
            if expected is None or existing != expected:
                return None, "fill_evidence_mismatch"

        new_fills: list[Fill] = []
        for fill in fills:
            existing = self._repositories.fills.get(fill.fill_id)
            if existing is None:
                new_fills.append(fill)
            elif existing != fill:
                return None, "fill_evidence_mismatch"

        return (
            _PreparedApplication(
                order_plan=order_plan,
                broker_order=expected_broker_order,
                broker_order_exists=existing_broker_order is not None,
                transition_path=transition_path,
                new_fills=tuple(new_fills),
            ),
            "",
        )

    def _order_identity_matches(
        self,
        order_plan: OrderPlan,
        dispatch: PaperOrderDispatch,
    ) -> bool:
        intent = order_plan.intent
        explanation = order_plan.explanation
        if (
            order_plan.order_plan_id != dispatch.order_plan_id
            or order_plan.policy_id != dispatch.policy_id
            or order_plan.policy_version != dispatch.policy_version
            or order_plan.purpose != dispatch.purpose
            or order_plan.idempotency_key != dispatch.idempotency_key
            or order_plan.risk_check_id != dispatch.risk_check_id
            or order_plan.risk_check_expires_at != dispatch.risk_check_expires_at
            or intent.symbol.strip().upper() != dispatch.symbol
            or intent.side != dispatch.side
            or intent.order_type != OrderType.limit
            or dispatch.order_type != "limit"
            or not _decimal_equal(intent.quantity, dispatch.quantity)
            or intent.limit_price is None
            or not _decimal_equal(intent.limit_price, dispatch.limit_price)
            or not _decimal_equal(
                intent.notional,
                Decimal(str(dispatch.quantity)) * Decimal(str(dispatch.limit_price)),
            )
            or explanation is None
        ):
            return False
        return (
            explanation.symbol.strip().upper() == dispatch.symbol
            and explanation.action == dispatch.side
            and _decimal_equal(explanation.quantity, dispatch.quantity)
            and explanation.limit_price is not None
            and _decimal_equal(explanation.limit_price, dispatch.limit_price)
            and _decimal_equal(
                explanation.estimated_notional,
                Decimal(str(dispatch.quantity)) * Decimal(str(dispatch.limit_price)),
            )
            and explanation.strategy_id == dispatch.strategy_id
            and explanation.strategy_version == dispatch.strategy_version
            and explanation.idempotency_key == dispatch.idempotency_key
            and explanation.policy_version == dispatch.policy_version
        )

    def _apply_prepared(
        self,
        prepared: _PreparedApplication,
        user_id: str,
    ) -> None:
        if prepared.broker_order_exists:
            current_broker = self._repositories.broker_orders.require(
                prepared.broker_order.broker_order_id
            )
            if current_broker != prepared.broker_order:
                self._repositories.broker_orders.update(prepared.broker_order)
        else:
            self._repositories.broker_orders.add(prepared.broker_order)

        order_plan = prepared.order_plan
        remaining_path = list(prepared.transition_path)
        while remaining_path and remaining_path[0] == OrderStatus.accepted:
            next_status = remaining_path.pop(0)
            transition_order_plan(
                order_plan=order_plan,
                new_status=next_status,
                audit=self._audit,
                user_id=user_id,
                source="paper_reconciliation_applier",
            )

        for fill in prepared.new_fills:
            self._repositories.fills.add(fill)
            self._audit.emit(
                user_id=user_id,
                entity_type="fill",
                entity_id=fill.fill_id,
                action="fill_recorded",
                after_state=fill,
                source="paper_reconciliation_applier",
            )

        for next_status in remaining_path:
            transition_order_plan(
                order_plan=order_plan,
                new_status=next_status,
                audit=self._audit,
                user_id=user_id,
                source="paper_reconciliation_applier",
                action=(
                    "order_partially_filled"
                    if next_status == OrderStatus.partially_filled
                    else None
                ),
            )
        self._repositories.order_plans.update(order_plan)


def _coerce_dispatches(
    reconciliation: (
        PaperReconciliationResult
        | PaperOrderDispatch
        | Iterable[PaperOrderDispatch]
    ),
) -> tuple[PaperOrderDispatch, ...]:
    if isinstance(reconciliation, PaperReconciliationResult):
        values = reconciliation.updated_dispatches
    elif isinstance(reconciliation, PaperOrderDispatch):
        values = (reconciliation,)
    else:
        values = tuple(reconciliation)
    if any(not isinstance(item, PaperOrderDispatch) for item in values):
        raise TypeError("paper reconciliation input must contain dispatch records")
    return tuple(values)


def _deduplicate_dispatches(
    dispatches: tuple[PaperOrderDispatch, ...],
) -> tuple[tuple[PaperOrderDispatch, ...], dict[str, str]]:
    by_plan: dict[str, PaperOrderDispatch] = {}
    blocked: dict[str, str] = {}
    for dispatch in dispatches:
        existing = by_plan.get(dispatch.order_plan_id)
        if existing is None:
            by_plan[dispatch.order_plan_id] = dispatch
        elif existing != dispatch:
            blocked[dispatch.order_plan_id] = "divergent_dispatches_in_batch"

    broker_owners: dict[str, set[str]] = {}
    fill_owners: dict[str, set[str]] = {}
    for dispatch in by_plan.values():
        broker_owners.setdefault(dispatch.broker_order_id, set()).add(
            dispatch.order_plan_id
        )
        for fill in dispatch.fill_evidence:
            fill_owners.setdefault(fill.broker_fill_reference, set()).add(
                dispatch.order_plan_id
            )
    for owners in (*broker_owners.values(), *fill_owners.values()):
        if len(owners) > 1:
            for order_plan_id in owners:
                blocked[order_plan_id] = "cross_order_evidence_collision"

    return (
        tuple(sorted(by_plan.values(), key=lambda item: item.order_plan_id)),
        blocked,
    )


def _broker_evidence_time(dispatch: PaperOrderDispatch) -> datetime:
    if dispatch.broker_business_date is not None and dispatch.broker_order_time is not None:
        broker_time = datetime.strptime(dispatch.broker_order_time, "%H%M%S").time()
        return datetime.combine(dispatch.broker_business_date, broker_time, tzinfo=KST)
    if dispatch.status == "rejected" and dispatch.dispatch_claimed_at is not None:
        return dispatch.dispatch_claimed_at
    raise ValueError("complete broker order time evidence is required")


def _broker_identity_matches(existing: BrokerOrder, expected: BrokerOrder) -> bool:
    return (
        existing.broker_order_id == expected.broker_order_id
        and existing.order_plan_id == expected.order_plan_id
        and existing.broker_mode == expected.broker_mode
        and existing.accepted_at == expected.accepted_at
        and existing.broker_reference == expected.broker_reference
    )


def _fill_from_evidence(
    dispatch: PaperOrderDispatch,
    evidence: PaperDispatchFillEvidence,
) -> Fill:
    return Fill(
        fill_id=evidence.broker_fill_reference,
        broker_order_id=dispatch.broker_order_id,
        order_plan_id=dispatch.order_plan_id,
        symbol=dispatch.symbol,
        quantity=evidence.quantity,
        price=evidence.price,
        notional=evidence.notional,
        filled_at=evidence.evidence_at,
    )


def _cumulative_fill_evidence_matches(
    dispatch: PaperOrderDispatch,
    fills: tuple[Fill, ...],
) -> bool:
    quantity = sum((Decimal(str(fill.quantity)) for fill in fills), Decimal("0"))
    expected_quantity = Decimal(str(dispatch.cumulative_filled_quantity))
    if quantity != expected_quantity:
        return False
    if any(
        Decimal(str(fill.notional))
        != Decimal(str(fill.quantity)) * Decimal(str(fill.price))
        for fill in fills
    ):
        return False
    requested = Decimal(str(dispatch.quantity))
    if dispatch.status in {"accepted", "rejected"}:
        return expected_quantity == 0
    if dispatch.status == "partially_filled":
        return Decimal("0") < expected_quantity < requested
    if dispatch.status == "filled":
        return expected_quantity == requested
    return Decimal("0") <= expected_quantity < requested


def _transition_path(
    current: OrderStatus,
    target: OrderStatus,
    *,
    cumulative_filled_quantity: float,
    requested_quantity: float,
) -> tuple[OrderStatus, ...] | None:
    if current == target:
        return ()
    partial_fill = (
        Decimal("0")
        < Decimal(str(cumulative_filled_quantity))
        < Decimal(str(requested_quantity))
    )
    if target == OrderStatus.accepted:
        path = (OrderStatus.accepted,)
    elif target == OrderStatus.partially_filled:
        path = (
            (OrderStatus.accepted, OrderStatus.partially_filled)
            if current == OrderStatus.submitted
            else (OrderStatus.partially_filled,)
        )
    elif target == OrderStatus.filled:
        path = (
            (OrderStatus.accepted, OrderStatus.filled)
            if current == OrderStatus.submitted
            else (OrderStatus.filled,)
        )
    elif target == OrderStatus.cancelled:
        if current == OrderStatus.submitted:
            path = (
                (
                    OrderStatus.accepted,
                    OrderStatus.partially_filled,
                    OrderStatus.cancelled,
                )
                if partial_fill
                else (OrderStatus.accepted, OrderStatus.cancelled)
            )
        elif current == OrderStatus.accepted and partial_fill:
            path = (OrderStatus.partially_filled, OrderStatus.cancelled)
        else:
            path = (OrderStatus.cancelled,)
    elif target == OrderStatus.rejected:
        path = (OrderStatus.rejected,)
    else:
        return None

    observed = current
    for next_status in path:
        if next_status not in VALID_TRANSITIONS.get(observed, set()):
            return None
        observed = next_status
    return path if observed == target else None


def _decimal_equal(left: object, right: object) -> bool:
    return Decimal(str(left)) == Decimal(str(right))
