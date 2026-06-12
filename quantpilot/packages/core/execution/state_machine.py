from __future__ import annotations

from quantpilot.packages.core.schemas import OrderPlan, OrderStatus, utc_now
from quantpilot.packages.db.audit import AuditRecorder


class InvalidOrderTransition(RuntimeError):
    pass


class RiskCheckRequired(RuntimeError):
    pass


class ApprovalRequired(RuntimeError):
    pass


TERMINAL_STATES = {
    OrderStatus.cancelled,
    OrderStatus.rejected,
    OrderStatus.expired,
}

VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.draft: {OrderStatus.risk_checked, *TERMINAL_STATES},
    OrderStatus.risk_checked: {OrderStatus.proposed, *TERMINAL_STATES},
    OrderStatus.proposed: {OrderStatus.user_approved, OrderStatus.submitted, *TERMINAL_STATES},
    OrderStatus.user_approved: {OrderStatus.submitted, *TERMINAL_STATES},
    OrderStatus.submitted: {OrderStatus.accepted, *TERMINAL_STATES},
    OrderStatus.accepted: {OrderStatus.partially_filled, OrderStatus.filled, *TERMINAL_STATES},
    OrderStatus.partially_filled: {OrderStatus.filled, *TERMINAL_STATES},
    OrderStatus.filled: set(),
    OrderStatus.cancelled: set(),
    OrderStatus.rejected: set(),
    OrderStatus.expired: set(),
}

ACTION_BY_STATUS = {
    OrderStatus.risk_checked: "risk_check_passed",
    OrderStatus.proposed: "order_proposed",
    OrderStatus.user_approved: "order_approved",
    OrderStatus.submitted: "order_submitted",
    OrderStatus.accepted: "broker_order_accepted",
    OrderStatus.partially_filled: "fill_recorded",
    OrderStatus.filled: "order_filled",
    OrderStatus.cancelled: "order_cancelled",
    OrderStatus.rejected: "order_rejected",
    OrderStatus.expired: "order_expired",
}


def transition_order_plan(
    *,
    order_plan: OrderPlan,
    new_status: OrderStatus,
    audit: AuditRecorder,
    user_id: str,
    source: str,
    action: str | None = None,
) -> OrderPlan:
    if new_status not in VALID_TRANSITIONS[order_plan.status]:
        raise InvalidOrderTransition(f"invalid order transition: {order_plan.status.value} -> {new_status.value}")

    before = order_plan.model_copy(deep=True)
    order_plan.status = new_status
    order_plan.updated_at = utc_now()
    audit.emit(
        user_id=user_id,
        entity_type="order_plan",
        entity_id=order_plan.order_plan_id,
        action=action or ACTION_BY_STATUS[new_status],
        before_state=before,
        after_state=order_plan,
        source=source,
    )
    return order_plan
