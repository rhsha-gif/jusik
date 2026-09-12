"""Operator-gated resolution of ambiguous broker states.

Neither command talks to the order endpoints. `release_cancel_claim` only clears the
local cancel claim after fresh daily evidence proves the original order is still
working and no cancellation was recorded; the trader re-sends the cancellation on
its next cycle. `resolve_unknown_dispatch` closes an outcome_unknown row as rejected
only after a fresh reconciliation pass found no broker row and the account holding
matches the ledger, and records the human reason under its own event source.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from quantpilot.paper.calendar import KST
from quantpilot.paper.order_evidence import daily_identity_matches
from quantpilot.packages.core.execution.paper_reconciliation import (
    PaperBrokerReconciler,
)
from quantpilot.packages.core.kis_paper import is_original_order
from quantpilot.packages.core.operator.position_ledger import PaperOrderDispatch
from quantpilot.packages.db.sqlite_repositories import PaperStateStore

UNKNOWN_RESOLUTION_MIN_AGE_SECONDS = 600


def open_kernel(store, client):
    return PaperStateStore(
        store.path.with_name("broker.sqlite3"),
        data_mode="paper_trading",
        broker_environment="kis_paper",
        account_scope_fingerprint=client.account_scope_fingerprint,
    )


def check_profile(store, client):
    if store.policy.data_mode != "paper_trading":
        raise ValueError("operator_requires_paper_profile")
    if store.get("control") != "paused":
        raise ValueError("operator_requires_paused_profile")
    if store.get("account_binding") != client.account_scope_fingerprint:
        raise ValueError("operator_account_mismatch")


def _local_order(store, order_id):
    order = next((o for o in store.orders() if o["id"] == order_id), None)
    if order is None:
        raise ValueError("unknown_local_order")
    return order


def release_cancel_claim(store, client, order_id, now, *, kernel=None):
    check_profile(store, client)
    _local_order(store, order_id)
    if not store.get("cancel_claim:" + order_id):
        raise ValueError("no_cancel_claim")
    owned = kernel is None
    kernel = kernel or open_kernel(store, client)
    try:
        dispatch = kernel.load_paper_order_dispatch(order_id)
    finally:
        if owned:
            kernel.close()
    if dispatch is None or dispatch.status not in {"accepted", "partially_filled"}:
        raise ValueError("dispatch_not_working")
    business_date = now.astimezone(KST).date()
    rows = client.get_daily_orders_and_fills(
        business_date, business_date, exchange="KRX", as_of_date=business_date
    ).rows
    original = [r for r in rows if daily_identity_matches(dispatch, r, business_date)]
    if len(original) != 1:
        raise ValueError("daily_evidence_unmatched")
    if original[0].remaining_quantity <= 0:
        raise ValueError("order_no_longer_working")
    if any(
        not is_original_order(r.original_order_number)
        and r.original_order_number == dispatch.broker_order_reference
        for r in rows
    ):
        raise ValueError("cancel_request_already_recorded")
    with store.transaction():
        store.db.execute(
            "DELETE FROM settings WHERE key=?", ("cancel_claim:" + order_id,)
        )
        store.audit(
            "cancel_claim_released",
            {
                "order_id": order_id,
                "remaining_quantity": original[0].remaining_quantity,
            },
            now,
        )
    return {
        "status": "cancel_claim_released",
        "order_id": order_id,
        "remaining_quantity": original[0].remaining_quantity,
        "next": "trader re-sends the cancellation on its next cycle",
    }


def resolve_unknown_dispatch(store, client, order_id, reason, now, *, kernel=None):
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        raise ValueError("resolution_reason_required")
    check_profile(store, client)
    order = _local_order(store, order_id)
    if order["state"] != "outcome_unknown":
        raise ValueError("order_not_outcome_unknown")
    owned = kernel is None
    kernel = kernel or open_kernel(store, client)
    try:
        dispatch = kernel.load_paper_order_dispatch(order_id)
        if dispatch is None or dispatch.status != "outcome_unknown":
            raise ValueError("dispatch_not_outcome_unknown")
        if dispatch.cumulative_filled_quantity > 0 or dispatch.broker_order_reference:
            raise ValueError("broker_evidence_exists")
        if (now - dispatch.updated_at).total_seconds() < UNKNOWN_RESOLUTION_MIN_AGE_SECONDS:
            raise ValueError("resolution_window_not_elapsed")
        # Exactly the trader's own query-only pass: if the broker now shows the order,
        # this reconciliation records it and the row is no longer unknown.
        result = PaperBrokerReconciler(
            store=kernel, client=client, clock=lambda: now
        ).reconcile_unresolved()
        dispatch = kernel.load_paper_order_dispatch(order_id)
        if dispatch.status != "outcome_unknown":
            raise ValueError("broker_evidence_found")
        matched = [
            d["matched_rows"]
            for d in result.diagnostics
            if d.get("order_plan_id") == order_id
        ]
        if matched != [0]:
            raise ValueError("broker_evidence_ambiguous")
        # The reconciler only matches an unknown row inside a short time window
        # around the claim. A same-day row with this exact symbol, side, quantity and
        # price that no other dispatch owns is still this order as far as anyone can
        # prove, however late its timestamp; closing it here would leave a live broker
        # order the ledger no longer tracks.
        business_date = dispatch.broker_business_date or (
            dispatch.dispatch_claimed_at.astimezone(KST).date()
        )
        end_date = now.astimezone(KST).date()
        rows = client.get_daily_orders_and_fills(
            business_date, end_date, exchange="KRX", as_of_date=end_date
        ).rows
        owned_references = {
            d.broker_order_reference
            for d in kernel.list_paper_order_dispatches()
            if d.broker_order_reference
        }
        if any(
            r.order_number not in owned_references
            and is_original_order(r.original_order_number)
            and r.symbol == dispatch.symbol
            and r.side == dispatch.side
            and r.order_quantity == int(dispatch.quantity)
            and Decimal(str(r.order_price)) == Decimal(str(int(dispatch.limit_price)))
            for r in rows
        ):
            raise ValueError("broker_evidence_ambiguous")
        remote = {
            p.symbol: p.holding_quantity
            for p in result.broker_balance.positions
            if p.holding_quantity
        }
        local = {p["symbol"]: p["quantity"] for p in store.positions()}
        if remote.get(dispatch.symbol, 0) != local.get(dispatch.symbol, 0):
            raise ValueError("account_holding_mismatch")
        updated_at = max(now, dispatch.updated_at + timedelta(microseconds=1))
        resolved = PaperOrderDispatch.model_validate(
            dispatch.model_copy(
                update={
                    "status": "rejected",
                    "reconciliation_status": "reconciled",
                    "last_error_code": "operator_resolved_no_broker_evidence",
                    "updated_at": updated_at,
                    "reconciled_at": updated_at,
                    "revision": dispatch.revision + 1,
                }
            ).model_dump()
        )
        kernel.update_paper_order_dispatch(resolved, mutation_origin="operator_resolution")
    finally:
        if owned:
            kernel.close()
    with store.transaction():
        store.update_order(order_id, "rejected", order["filled"], order["amount"], now)
        store.audit(
            "unknown_dispatch_resolved",
            {"order_id": order_id, "reason": reason.strip(), "matched_rows": 0},
            now,
        )
    return {
        "status": "resolved_as_rejected",
        "order_id": order_id,
        "matched_rows": 0,
        "next": "trader reconciles and clears the incident on its next cycle",
    }
