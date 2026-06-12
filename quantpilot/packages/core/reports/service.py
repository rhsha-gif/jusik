from __future__ import annotations

from quantpilot.packages.core.schemas import Fill, OperationReport, OrderPlan, UserPolicy
from quantpilot.packages.db.repositories import RepositoryRegistry


def build_operation_report(
    *,
    user_id: str,
    policy: UserPolicy,
    orders: list[OrderPlan],
    fills: list[Fill],
    repositories: RepositoryRegistry,
) -> OperationReport:
    summary = {
        "orders_total": len(orders),
        "fills_total": len(fills),
        "filled_notional": round(sum(fill.notional for fill in fills), 2),
        "broker": policy.broker.value,
        "execution_mode": policy.execution_mode.value,
    }
    return OperationReport(
        user_id=user_id,
        policy_id=policy.policy_id,
        summary=summary,
        order_plan_ids=[order.order_plan_id for order in orders],
        fill_ids=[fill.fill_id for fill in fills],
        audit_event_count=len(repositories.audit_logs.list()),
        live_trading_enabled=False,
    )
