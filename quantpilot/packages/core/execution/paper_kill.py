from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from math import isclose
from typing import Callable
from zoneinfo import ZoneInfo

from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperBrokerReconciler,
    PaperReconciliationUnavailable,
)
from quantpilot.packages.core.execution.paper_submission import (
    DurablePaperSubmissionCoordinator,
)
from quantpilot.packages.core.kis_paper import (
    KisCancelableOrder,
    KisPaperBusinessError,
    KisPaperCancelOutcomeUnknown,
    KisPaperClient,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperCancelRequest,
    PaperExecutionSession,
    PaperKillOperation,
    PaperOrderDispatch,
)
from quantpilot.packages.core.schemas import utc_now
from quantpilot.packages.db.sqlite_repositories import PaperStateStore


_WORKING_DISPATCH_STATUSES = {
    "dispatch_claimed",
    "outcome_unknown",
    "accepted",
    "partially_filled",
}
_TERMINAL_CANCEL_STATUSES = {
    "reconciled_cancelled",
    "reconciled_filled",
}
KST = ZoneInfo("Asia/Seoul")


class PaperKillError(RuntimeError):
    pass


@dataclass(frozen=True)
class PaperKillResult:
    kill_id: str
    status: str
    expired_prepared_count: int
    cancel_request_count: int
    cancel_post_count: int
    reconciled_cancelled_count: int
    reconciled_filled_count: int
    unresolved_reason_codes: tuple[str, ...]


class PaperKillService:
    """Reconciliation-first, managed-order-only KIS paper kill coordinator."""

    def __init__(
        self,
        *,
        store: PaperStateStore,
        session: PaperExecutionSession,
        client: KisPaperClient,
        submission_coordinator: DurablePaperSubmissionCoordinator,
        reconciler: PaperBrokerReconciler,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        provenance = store.provenance
        if (
            provenance.store_id != session.store_id
            or provenance.account_scope_fingerprint
            != client.account_scope_fingerprint
            or session.account_scope_fingerprint
            != client.account_scope_fingerprint
            or session.status != "active"
        ):
            raise ValueError("paper kill dependencies must share one active account fence")
        self._store = store
        self._session = session
        self._client = client
        self._submission = submission_coordinator
        self._reconciler = reconciler
        self._clock = clock

    def engage(self, *, reason: str) -> PaperKillResult:
        operation = self._store.start_paper_kill_operation(
            session=self._session,
            reason=reason,
            started_at=self._now(),
        )
        if operation.status == "killed":
            return self._result(operation, expired_count=0, post_count=0)

        expired = self._submission.terminalize_prepared_dispatches_for_kill()
        reasons: set[str] = set()
        if not self._reconcile(reasons):
            return self._finish_recovery(
                operation,
                reasons,
                expired_count=len(expired),
                post_count=0,
            )

        try:
            initial_rows = self._client.get_cancelable_orders().rows
        except Exception:
            reasons.add("cancelable_order_query_unavailable")
            return self._finish_recovery(
                operation,
                reasons,
                expired_count=len(expired),
                post_count=0,
            )

        targets, discovery_reasons = self._discover_targets(initial_rows)
        reasons.update(discovery_reasons)
        post_count = 0
        for dispatch, row in targets:
            request = self._existing_cancel_for(dispatch)
            if request is None:
                request = self._store.create_paper_cancel_request(
                    self._new_cancel(operation, dispatch, row),
                    session=self._session,
                )
            if request.status != "prepared":
                continue
            try:
                claimed = self._store.claim_paper_cancel_attempt(
                    request.cancel_id,
                    session=self._session,
                    claimed_at=self._after(request.updated_at),
                )
            except Exception:
                reasons.add("cancel_claim_failed")
                continue
            post_count += 1
            self._submit_claimed_cancel(claimed, row, reasons)

        self._reconcile(reasons)
        try:
            final_rows = self._client.get_cancelable_orders().rows
        except Exception:
            final_rows = ()
            reasons.add("cancelable_order_query_unavailable")
        self._synchronize_cancel_requests(reasons)
        _, final_discovery_reasons = self._discover_targets(final_rows)
        reasons.update(final_discovery_reasons)
        self._collect_unresolved_reasons(reasons)

        if reasons:
            return self._finish_recovery(
                operation,
                reasons,
                expired_count=len(expired),
                post_count=post_count,
            )
        killed = PaperKillOperation.model_validate(
            operation.model_copy(
                update={
                    "status": "killed",
                    "unresolved_reason_codes": [],
                    "completed_at": self._after(operation.updated_at),
                    "updated_at": self._after(operation.updated_at),
                    "revision": operation.revision + 1,
                }
            ).model_dump()
        )
        killed = self._store.update_paper_kill_operation(
            killed,
            session=self._session,
        )
        return self._result(
            killed,
            expired_count=len(expired),
            post_count=post_count,
        )

    def release(self) -> PaperKillResult:
        operation = self._store.load_active_paper_kill_operation()
        if operation is None or operation.status != "killed":
            raise PaperKillError("paper_kill_release_requires_killed_state")
        reasons: set[str] = set()
        self._reconcile(reasons)
        try:
            rows = self._client.get_cancelable_orders().rows
        except Exception:
            rows = ()
            reasons.add("cancelable_order_query_unavailable")
        self._synchronize_cancel_requests(reasons)
        _, discovery_reasons = self._discover_targets(rows)
        reasons.update(discovery_reasons)
        self._collect_unresolved_reasons(reasons)
        if reasons:
            blocked = PaperKillOperation.model_validate(
                operation.model_copy(
                    update={
                        "status": "recovery_required",
                        "unresolved_reason_codes": sorted(reasons),
                        "completed_at": None,
                        "updated_at": self._after(operation.updated_at),
                        "revision": operation.revision + 1,
                    }
                ).model_dump()
            )
            blocked = self._store.update_paper_kill_operation(
                blocked,
                session=self._session,
            )
            return self._result(blocked, expired_count=0, post_count=0)
        released_at = self._after(operation.updated_at)
        released = PaperKillOperation.model_validate(
            operation.model_copy(
                update={
                    "status": "released",
                    "released_at": released_at,
                    "updated_at": released_at,
                    "revision": operation.revision + 1,
                }
            ).model_dump()
        )
        released = self._store.update_paper_kill_operation(
            released,
            session=self._session,
        )
        return self._result(released, expired_count=0, post_count=0)

    def _reconcile(self, reasons: set[str]) -> bool:
        try:
            result = self._reconciler.reconcile_unresolved()
        except PaperReconciliationUnavailable:
            reasons.add("paper_reconciliation_unavailable")
            return False
        except Exception:
            reasons.add("paper_reconciliation_failed")
            return False
        if result.blocked_order_plan_ids:
            reasons.add("paper_reconciliation_blocked")
        return True

    def _discover_targets(
        self,
        rows: tuple[KisCancelableOrder, ...],
    ) -> tuple[list[tuple[PaperOrderDispatch, KisCancelableOrder]], set[str]]:
        managed = [
            item
            for item in self._store.list_paper_order_dispatches()
            if item.status in {"accepted", "partially_filled"}
            and item.reconciliation_status == "pending"
        ]
        matched_rows: dict[str, list[tuple[PaperOrderDispatch, KisCancelableOrder]]] = {}
        reasons: set[str] = set()
        business_date = self._now().astimezone(KST).date()
        for row in rows:
            candidates = [
                item
                for item in managed
                if _cancel_identity_matches(
                    item,
                    row,
                    business_date=business_date,
                )
            ]
            if len(candidates) != 1:
                reasons.add(
                    "external_working_order_detected"
                    if not candidates
                    else "broker_cancel_identity_ambiguous"
                )
                continue
            dispatch = candidates[0]
            matched_rows.setdefault(dispatch.order_plan_id, []).append((dispatch, row))
        targets: list[tuple[PaperOrderDispatch, KisCancelableOrder]] = []
        for candidates in matched_rows.values():
            if len(candidates) != 1:
                reasons.add("broker_cancel_identity_ambiguous")
            else:
                targets.append(candidates[0])
        return targets, reasons

    def _new_cancel(
        self,
        operation: PaperKillOperation,
        dispatch: PaperOrderDispatch,
        row: KisCancelableOrder,
    ) -> PaperCancelRequest:
        created_at = self._after(operation.updated_at, dispatch.updated_at)
        return PaperCancelRequest(
            kill_id=operation.kill_id,
            order_plan_id=dispatch.order_plan_id,
            broker_order_id=dispatch.broker_order_id,
            broker_order_reference=dispatch.broker_order_reference or "",
            broker_forwarding_order_org_number=(
                dispatch.broker_forwarding_order_org_number or ""
            ),
            symbol=dispatch.symbol,
            side=dispatch.side,
            cancelable_quantity=float(row.cancelable_quantity),
            original_limit_price=float(row.order_price),
            store_id=self._store.provenance.store_id,
            account_scope_fingerprint=self._client.account_scope_fingerprint,
            created_at=created_at,
            updated_at=created_at,
        )

    def _existing_cancel_for(
        self,
        dispatch: PaperOrderDispatch,
    ) -> PaperCancelRequest | None:
        for request in self._store.list_paper_cancel_requests():
            if (
                request.order_plan_id == dispatch.order_plan_id
                and request.broker_order_reference == dispatch.broker_order_reference
            ):
                return request
        return None

    def _submit_claimed_cancel(
        self,
        request: PaperCancelRequest,
        row: KisCancelableOrder,
        reasons: set[str],
    ) -> None:
        try:
            result = self._client.cancel_full_remaining_order(
                order_branch_number=row.order_branch_number,
                original_order_number=row.order_number,
                order_division_code=row.order_division_code,
                cancelable_quantity=row.cancelable_quantity,
                original_order_price=row.order_price,
                exchange="KRX",
            )
        except KisPaperBusinessError:
            self._persist_cancel_state(
                request,
                status="rejected",
                error_code="broker_business_rejected",
            )
            return
        except KisPaperCancelOutcomeUnknown:
            self._persist_cancel_state(
                request,
                status="cancel_outcome_unknown",
                error_code="broker_response_ambiguous",
            )
            return
        except Exception:
            self._persist_cancel_state(
                request,
                status="cancel_outcome_unknown",
                error_code="broker_exception_after_claim",
            )
            return
        try:
            if (
                result.original_order_number != row.order_number
                or result.order_branch_number != row.order_branch_number
                or result.cancelled_quantity != row.cancelable_quantity
            ):
                self._persist_cancel_state(
                    request,
                    status="cancel_outcome_unknown",
                    error_code="cancel_response_identity_mismatch",
                )
                return
            self._persist_cancel_state(
                request,
                status="cancel_accepted",
                response_order_reference=result.cancel_order_number,
            )
        except Exception:
            return

    def _synchronize_cancel_requests(self, reasons: set[str]) -> None:
        by_order = {
            item.order_plan_id: item
            for item in self._store.list_paper_order_dispatches()
        }
        for request in self._store.list_paper_cancel_requests():
            if request.status in _TERMINAL_CANCEL_STATUSES:
                continue
            dispatch = by_order.get(request.order_plan_id)
            if dispatch is None:
                reasons.add("cancel_managed_dispatch_missing")
                continue
            if dispatch.status == "cancelled":
                self._persist_cancel_state(
                    request,
                    status="reconciled_cancelled",
                    reconciled=True,
                )
            elif dispatch.status == "filled":
                self._persist_cancel_state(
                    request,
                    status="reconciled_filled",
                    reconciled=True,
                )
            elif request.status == "cancel_claimed":
                self._persist_cancel_state(
                    request,
                    status="cancel_outcome_unknown",
                    error_code="process_interrupted_after_claim",
                )
                reasons.add("cancel_submission_outcome_unknown")

    def _persist_cancel_state(
        self,
        request: PaperCancelRequest,
        *,
        status: str,
        error_code: str | None = None,
        response_order_reference: str | None = None,
        reconciled: bool = False,
    ) -> PaperCancelRequest:
        write_at = self._after(request.updated_at)
        updated = PaperCancelRequest.model_validate(
            request.model_copy(
                update={
                    "status": status,
                    "last_error_code": error_code,
                    "response_order_reference": (
                        response_order_reference or request.response_order_reference
                    ),
                    "updated_at": write_at,
                    "reconciled_at": write_at if reconciled else None,
                    "revision": request.revision + 1,
                }
            ).model_dump()
        )
        return self._store.update_paper_cancel_request(
            updated,
            session=self._session,
        )

    def _collect_unresolved_reasons(self, reasons: set[str]) -> None:
        dispatches = self._store.list_paper_order_dispatches()
        dispatch_by_order = {item.order_plan_id: item for item in dispatches}
        if any(item.status in _WORKING_DISPATCH_STATUSES for item in dispatches):
            reasons.add("managed_working_order_unresolved")
        if any(item.status == "prepared" for item in dispatches):
            reasons.add("prepared_dispatch_unresolved")
        if any(
            item.reconciliation_status == "blocked"
            for item in dispatches
        ):
            reasons.add("paper_reconciliation_blocked")
        if any(
            item.status not in {"prepared", *_WORKING_DISPATCH_STATUSES}
            and item.reconciliation_status != "reconciled"
            for item in dispatches
        ):
            reasons.add("paper_reconciliation_pending")
        if any(
            item.status not in _TERMINAL_CANCEL_STATUSES
            and not (
                item.status == "rejected"
                and (dispatch := dispatch_by_order.get(item.order_plan_id)) is not None
                and dispatch.status == "rejected"
                and dispatch.reconciliation_status == "reconciled"
            )
            for item in self._store.list_paper_cancel_requests()
        ):
            reasons.add("cancel_reconciliation_pending")

    def _finish_recovery(
        self,
        operation: PaperKillOperation,
        reasons: set[str],
        *,
        expired_count: int,
        post_count: int,
    ) -> PaperKillResult:
        reasons = reasons or {"paper_kill_recovery_required"}
        if operation.status == "recovery_required" and (
            operation.unresolved_reason_codes == sorted(reasons)
        ):
            blocked = operation
        else:
            blocked = PaperKillOperation.model_validate(
                operation.model_copy(
                    update={
                        "status": "recovery_required",
                        "unresolved_reason_codes": sorted(reasons),
                        "completed_at": None,
                        "updated_at": self._after(operation.updated_at),
                        "revision": operation.revision + 1,
                    }
                ).model_dump()
            )
            blocked = self._store.update_paper_kill_operation(
                blocked,
                session=self._session,
            )
        return self._result(
            blocked,
            expired_count=expired_count,
            post_count=post_count,
        )

    def _result(
        self,
        operation: PaperKillOperation,
        *,
        expired_count: int,
        post_count: int,
    ) -> PaperKillResult:
        requests = self._store.list_paper_cancel_requests(kill_id=operation.kill_id)
        return PaperKillResult(
            kill_id=operation.kill_id,
            status=operation.status,
            expired_prepared_count=expired_count,
            cancel_request_count=len(requests),
            cancel_post_count=post_count,
            reconciled_cancelled_count=sum(
                item.status == "reconciled_cancelled" for item in requests
            ),
            reconciled_filled_count=sum(
                item.status == "reconciled_filled" for item in requests
            ),
            unresolved_reason_codes=tuple(operation.unresolved_reason_codes),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("paper-kill clock must include a UTC offset")
        return value

    def _after(self, *values: datetime) -> datetime:
        now = self._now()
        return max([now, *(value + timedelta(microseconds=1) for value in values)])


def _cancel_identity_matches(
    dispatch: PaperOrderDispatch,
    row: KisCancelableOrder,
    *,
    business_date: date,
) -> bool:
    expected_filled_quantity = int(dispatch.cumulative_filled_quantity)
    expected_cancelable_quantity = int(
        dispatch.quantity - dispatch.cumulative_filled_quantity
    )
    expected_filled_amount = sum(
        (Decimal(str(item.notional)) for item in dispatch.fill_evidence),
        Decimal("0"),
    )
    return bool(
        dispatch.broker_order_reference
        and dispatch.broker_forwarding_order_org_number
        and dispatch.broker_order_branch_number
        and dispatch.broker_business_date == business_date
        and row.order_number == dispatch.broker_order_reference
        and row.order_branch_number == dispatch.broker_forwarding_order_org_number
        and row.order_branch_number == dispatch.broker_order_branch_number
        and (
            dispatch.broker_order_time is None
            or row.order_time == dispatch.broker_order_time
        )
        and row.original_order_number in {"", "0"}
        and row.symbol == dispatch.symbol
        and row.side == dispatch.side
        and row.order_quantity == int(dispatch.quantity)
        and isclose(dispatch.quantity, float(int(dispatch.quantity)), abs_tol=0.000001)
        and isclose(
            dispatch.cumulative_filled_quantity,
            float(expected_filled_quantity),
            abs_tol=0.000001,
        )
        and Decimal(row.order_price) == Decimal(str(int(dispatch.limit_price)))
        and row.total_filled_quantity == expected_filled_quantity
        and Decimal(row.total_filled_amount) == expected_filled_amount
        and row.cancelable_quantity == expected_cancelable_quantity > 0
        and row.exchange_id == "KRX"
    )
