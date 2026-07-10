from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from math import isclose
from typing import Protocol
from zoneinfo import ZoneInfo

from quantpilot.packages.core.kis_paper import (
    KisBalanceResult,
    KisDailyOrderFill,
    KisDailyOrdersResult,
    KisPaperClient,
    kis_recent_three_month_start,
)
from quantpilot.packages.core.operator.position_ledger import (
    PaperDispatchFillEvidence,
    PaperOrderDispatch,
)
from quantpilot.packages.core.schemas import utc_now


KST = ZoneInfo("Asia/Seoul")


class PaperReconciliationUnavailable(RuntimeError):
    pass


class PaperReconciliationStore(Protocol):
    @property
    def provenance(self): ...

    def list_unresolved_paper_order_dispatches(self) -> list[PaperOrderDispatch]: ...

    def update_paper_order_dispatch(
        self,
        dispatch: PaperOrderDispatch,
    ) -> PaperOrderDispatch: ...


@dataclass(frozen=True)
class PaperReconciliationResult:
    updated_dispatches: tuple[PaperOrderDispatch, ...]
    pending_order_plan_ids: tuple[str, ...]
    blocked_order_plan_ids: tuple[str, ...]
    broker_balance: KisBalanceResult
    reconciled_at: datetime


class PaperBrokerReconciler:
    """Query-only recovery for claimed/unknown/accepted KIS paper orders."""

    def __init__(
        self,
        *,
        store: PaperReconciliationStore,
        client: KisPaperClient,
        clock=utc_now,
        unknown_match_window_seconds: int = 90,
    ) -> None:
        provenance = store.provenance
        if (
            provenance.data_mode != "paper_trading"
            or provenance.broker_environment != "kis_paper"
            or provenance.account_scope_fingerprint
            != client.account_scope_fingerprint
        ):
            raise ValueError("paper reconciler store and client provenance must match")
        if (
            isinstance(unknown_match_window_seconds, bool)
            or unknown_match_window_seconds <= 0
        ):
            raise ValueError("paper reconciliation match window must be positive")
        self._store = store
        self._client = client
        self._clock = clock
        self._match_window_seconds = unknown_match_window_seconds

    def reconcile_unresolved(self) -> PaperReconciliationResult:
        now = self._now()
        unresolved = [
            item
            for item in self._store.list_unresolved_paper_order_dispatches()
            if item.attempt_count == 1
            and item.status
            in {
                "dispatch_claimed",
                "outcome_unknown",
                "accepted",
                "partially_filled",
            }
        ]
        end_date = now.astimezone(KST).date()
        earliest_queryable_date = kis_recent_three_month_start(end_date)
        history_expired = [
            item
            for item in unresolved
            if _dispatch_business_date(item) < earliest_queryable_date
        ]
        queryable = [item for item in unresolved if item not in history_expired]
        try:
            balance = self._client.get_balance(exchange="KRX")
            if queryable:
                start_date = min(_dispatch_business_date(item) for item in queryable)
                query = self._client.get_daily_orders_and_fills(
                    start_date,
                    end_date,
                    exchange="KRX",
                    as_of_date=end_date,
                )
            else:
                query = KisDailyOrdersResult(rows=(), pages_fetched=0)
        except Exception:
            raise PaperReconciliationUnavailable(
                "KIS paper reconciliation query is unavailable"
            ) from None

        expired_ids = {item.order_plan_id for item in history_expired}
        updated = tuple(
            (
                self._blocked(
                    item,
                    observed_at=now,
                    error_code="broker_history_window_manual_resolution_required",
                )
                if item.order_plan_id in expired_ids
                else self.reconcile_dispatch(item, query.rows, reconciled_at=now)
            )
            for item in unresolved
        )
        return PaperReconciliationResult(
            updated_dispatches=updated,
            pending_order_plan_ids=tuple(
                item.order_plan_id
                for item in updated
                if item.reconciliation_status == "pending"
            ),
            blocked_order_plan_ids=tuple(
                item.order_plan_id
                for item in updated
                if item.reconciliation_status == "blocked"
            ),
            broker_balance=balance,
            reconciled_at=now,
        )

    def reconcile_dispatch(
        self,
        dispatch: PaperOrderDispatch,
        rows: tuple[KisDailyOrderFill, ...],
        *,
        reconciled_at: datetime | None = None,
    ) -> PaperOrderDispatch:
        observed_at = reconciled_at or self._now()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("paper reconciliation requires an aware timestamp")
        if dispatch.attempt_count != 1:
            return dispatch
        candidates = [row for row in rows if self._matches(dispatch, row)]
        if len(candidates) == 0:
            if (
                dispatch.broker_order_branch_number is not None
                and any(
                    self._matches(
                        dispatch,
                        row,
                        compare_known_branch=False,
                    )
                    and row.order_branch_number
                    != dispatch.broker_order_branch_number
                    for row in rows
                )
            ):
                return self._blocked(
                    dispatch,
                    observed_at=observed_at,
                    error_code="broker_order_branch_mismatch",
                )
            return dispatch
        if len(candidates) > 1:
            return self._blocked(
                dispatch,
                observed_at=observed_at,
                error_code="broker_match_ambiguous",
            )
        row = candidates[0]
        error = _row_consistency_error(dispatch, row)
        if error is not None:
            return self._blocked(
                dispatch,
                observed_at=observed_at,
                error_code=error,
            )
        status = _status_from_row(row)
        if status is None:
            return self._blocked(
                dispatch,
                observed_at=observed_at,
                error_code="broker_state_inconsistent",
            )
        try:
            fills = _merge_fill_evidence(
                dispatch,
                row,
                observed_at=observed_at,
            )
        except ValueError:
            return self._blocked(
                dispatch,
                observed_at=observed_at,
                error_code="broker_fill_evidence_regressed",
            )

        broker_date = _parse_business_date(row.order_date)
        terminal = status in {"filled", "rejected", "cancelled"}
        if (
            dispatch.status == status
            and dispatch.broker_business_date == broker_date
            and dispatch.broker_order_reference == row.order_number
            and dispatch.broker_order_branch_number == row.order_branch_number
            and dispatch.broker_order_time == row.order_time
            and isclose(
                dispatch.cumulative_filled_quantity,
                float(row.total_filled_quantity),
                abs_tol=0.000001,
            )
            and dispatch.fill_evidence == fills
            and dispatch.reconciliation_status
            == ("reconciled" if terminal else "pending")
        ):
            return dispatch

        write_at = max(observed_at, dispatch.updated_at + timedelta(microseconds=1))
        updated = PaperOrderDispatch.model_validate(
            dispatch.model_copy(
                update={
                    "status": status,
                    "reconciliation_status": "reconciled" if terminal else "pending",
                    "broker_business_date": broker_date,
                    "broker_order_reference": row.order_number,
                    "broker_order_branch_number": row.order_branch_number,
                    "broker_order_time": row.order_time,
                    "cumulative_filled_quantity": float(row.total_filled_quantity),
                    "fill_evidence": fills,
                    "last_error_code": None,
                    "updated_at": write_at,
                    "reconciled_at": write_at if terminal else None,
                    "revision": dispatch.revision + 1,
                }
            ).model_dump()
        )
        if updated == dispatch:
            return dispatch
        return self._store.update_paper_order_dispatch(updated)

    def _matches(
        self,
        dispatch: PaperOrderDispatch,
        row: KisDailyOrderFill,
        *,
        compare_known_branch: bool = True,
    ) -> bool:
        expected_date = _dispatch_business_date(dispatch).strftime("%Y%m%d")
        exact_identity = (
            row.order_date == expected_date
            and row.symbol == dispatch.symbol
            and row.side == dispatch.side
            and row.order_quantity == int(dispatch.quantity)
            and Decimal(str(row.order_price)) == Decimal(str(int(dispatch.limit_price)))
            and row.original_order_number in {"", "0"}
        )
        if not exact_identity:
            return False
        if dispatch.broker_order_reference is not None:
            return (
                row.order_number == dispatch.broker_order_reference
                and (
                    not compare_known_branch
                    or dispatch.broker_order_branch_number is None
                    or row.order_branch_number
                    == dispatch.broker_order_branch_number
                )
                and (
                    dispatch.broker_order_time is None
                    or row.order_time == dispatch.broker_order_time
                )
            )
        if dispatch.dispatch_claimed_at is None:
            return False
        row_time = _row_order_datetime(row)
        claimed_at = dispatch.dispatch_claimed_at.astimezone(KST)
        delta = (row_time - claimed_at).total_seconds()
        return -5 <= delta <= self._match_window_seconds

    def _blocked(
        self,
        dispatch: PaperOrderDispatch,
        *,
        observed_at: datetime,
        error_code: str,
    ) -> PaperOrderDispatch:
        if (
            dispatch.reconciliation_status == "blocked"
            and dispatch.last_error_code == error_code
        ):
            return dispatch
        write_at = max(observed_at, dispatch.updated_at + timedelta(microseconds=1))
        blocked = PaperOrderDispatch.model_validate(
            dispatch.model_copy(
                update={
                    "status": (
                        "outcome_unknown"
                        if dispatch.status == "dispatch_claimed"
                        else dispatch.status
                    ),
                    "reconciliation_status": "blocked",
                    "last_error_code": error_code,
                    "updated_at": write_at,
                    "revision": dispatch.revision + 1,
                }
            ).model_dump()
        )
        return self._store.update_paper_order_dispatch(blocked)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("paper reconciler clock must include a UTC offset")
        return value


def _dispatch_business_date(dispatch: PaperOrderDispatch) -> date:
    if dispatch.broker_business_date is not None:
        return dispatch.broker_business_date
    if dispatch.dispatch_claimed_at is None:
        raise ValueError("paper dispatch has no broker business-date evidence")
    return dispatch.dispatch_claimed_at.astimezone(KST).date()


def _parse_business_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _row_order_datetime(row: KisDailyOrderFill) -> datetime:
    return datetime.combine(
        _parse_business_date(row.order_date),
        datetime.strptime(row.order_time, "%H%M%S").time(),
        tzinfo=KST,
    )


def _row_consistency_error(
    dispatch: PaperOrderDispatch,
    row: KisDailyOrderFill,
) -> str | None:
    quantity = int(dispatch.quantity)
    filled = row.total_filled_quantity
    remaining = row.remaining_quantity
    rejected = row.rejected_quantity
    cancelled = row.confirmed_cancel_quantity
    if min(filled, remaining, rejected, cancelled) < 0:
        return "broker_quantity_negative"
    if filled > quantity or filled + remaining + rejected + cancelled > quantity:
        return "broker_quantity_inconsistent"
    if row.total_filled_amount < 0 or row.average_fill_price < 0:
        return "broker_amount_negative"
    if filled == 0 and row.total_filled_amount != 0:
        return "broker_fill_amount_inconsistent"
    if filled > 0 and not isclose(
        float(row.total_filled_amount),
        float(row.average_fill_price * filled),
        rel_tol=0,
        abs_tol=0.01,
    ):
        return "broker_fill_amount_inconsistent"
    return None


def _status_from_row(row: KisDailyOrderFill) -> str | None:
    quantity = row.order_quantity
    filled = row.total_filled_quantity
    remaining = row.remaining_quantity
    if filled == 0 and row.rejected_quantity >= quantity and remaining == 0:
        return "rejected"
    if row.cancelled and remaining == 0 and filled < quantity:
        return "cancelled"
    if filled == quantity and remaining == 0:
        return "filled"
    if 0 < filled < quantity and remaining > 0 and not row.cancelled:
        return "partially_filled"
    if filled == 0 and remaining > 0 and not row.cancelled and row.rejected_quantity == 0:
        return "accepted"
    return None


def _merge_fill_evidence(
    dispatch: PaperOrderDispatch,
    row: KisDailyOrderFill,
    *,
    observed_at: datetime,
) -> list[PaperDispatchFillEvidence]:
    existing_quantity = Decimal(str(dispatch.cumulative_filled_quantity))
    existing_amount = sum(
        (Decimal(str(item.notional)) for item in dispatch.fill_evidence),
        start=Decimal("0"),
    )
    next_quantity = Decimal(row.total_filled_quantity)
    next_amount = row.total_filled_amount
    if next_quantity < existing_quantity or next_amount < existing_amount:
        raise ValueError("aggregate broker fill evidence regressed")
    delta_quantity = next_quantity - existing_quantity
    delta_amount = next_amount - existing_amount
    if delta_quantity == 0:
        if delta_amount != 0:
            raise ValueError("fill amount changed without a quantity delta")
        return list(dispatch.fill_evidence)
    if delta_amount <= 0:
        raise ValueError("positive fill quantity requires positive amount")
    delta_price = delta_amount / delta_quantity
    raw_reference = (
        f"{row.order_number}:{row.total_filled_quantity}:"
        f"{format(row.total_filled_amount, 'f')}"
    )
    reference = "kisagg-" + hashlib.sha256(
        raw_reference.encode("utf-8")
    ).hexdigest()
    evidence = PaperDispatchFillEvidence(
        broker_fill_reference=reference,
        broker_order_id=dispatch.broker_order_id,
        broker_order_reference=row.order_number,
        symbol=dispatch.symbol,
        side=dispatch.side,
        quantity=float(delta_quantity),
        price=float(delta_price),
        notional=float(delta_amount),
        evidence_at=observed_at,
        time_basis="broker_daily_aggregate_first_observed",
    )
    return [*dispatch.fill_evidence, evidence]
