from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from quantpilot.packages.core.execution.order_context import build_submit_batch_context
from quantpilot.packages.core.execution.state_machine import ApprovalRequired, RiskCheckRequired, transition_order_plan
from quantpilot.packages.core.ledger.service import ReconciliationLedgerService
from quantpilot.packages.core.portfolio.planner import fixture_portfolio_snapshot
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.gatekeeper import run_risk_check
from quantpilot.packages.core.risk.types import BatchRiskConfig, BatchRiskDecision
from quantpilot.packages.core.schemas import (
    BrokerOrder,
    Fill,
    GuardrailState,
    OrderPlan,
    OrderStatus,
    PortfolioPlan,
    PortfolioSnapshot,
    UserPolicy,
)
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


class SubmissionService:
    def __init__(
        self,
        *,
        repositories: RepositoryRegistry,
        audit: AuditRecorder,
        ledger: ReconciliationLedgerService,
        broker_for_policy: Callable[[UserPolicy], Any],
        guardrail_state: Callable[..., GuardrailState],
        autopilot_paused: Callable[[], bool],
        last_blocked_reason: Callable[[], str | None],
        now: Callable[[], datetime],
    ) -> None:
        self.repositories = repositories
        self.audit = audit
        self.ledger = ledger
        self.broker_for_policy = broker_for_policy
        self.guardrail_state = guardrail_state
        self.autopilot_paused = autopilot_paused
        self.last_blocked_reason = last_blocked_reason
        self.now = now

    def submit_order_plan(self, order_plan_id: str) -> tuple[OrderPlan, BrokerOrder, list[Fill]]:
        order_plan = self.repositories.order_plans.require(order_plan_id)
        policy = self.repositories.policies.require(order_plan.policy_id)

        self._require_submittable(order_plan=order_plan, policy=policy)
        strategy_id = self._strategy_id_for_order(order_plan)
        snapshot = fixture_portfolio_snapshot()
        fresh_risk = self._fresh_submission_risk_check(
            policy=policy,
            order_plan=order_plan,
            snapshot=snapshot,
            strategy_id=strategy_id,
        )
        if not fresh_risk.passed:
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=order_plan.order_plan_id,
                action="risk_check_failed",
                before_state=order_plan,
                after_state={"failed_checks": fresh_risk.failed_checks},
                source="execution_service",
            )
            raise RiskCheckRequired(f"fresh risk check failed: {fresh_risk.failed_checks}")
        order_plan.risk_check_id = fresh_risk.risk_check_id
        order_plan.risk_check_expires_at = fresh_risk.expires_at

        batch_decision = self._submit_batch_risk_decision(
            policy=policy,
            order_plan=order_plan,
            snapshot=snapshot,
            strategy_id=strategy_id,
        )
        if not batch_decision.passed or order_plan.order_plan_id not in set(batch_decision.accepted_order_plan_ids):
            before_blocked = order_plan.model_copy(deep=True)
            order_plan.blocked_reason = "batch_risk_rejected"
            self.repositories.order_plans.update(order_plan)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="order_plan",
                entity_id=order_plan.order_plan_id,
                action="batch_risk_rejected",
                before_state=before_blocked,
                after_state=batch_decision,
                source="execution_service",
            )
            raise RiskCheckRequired(f"batch risk check failed: {batch_decision.failed_checks}")

        return self._submit_to_broker(policy=policy, order_plan=order_plan)

    def _require_submittable(self, *, order_plan: OrderPlan, policy: UserPolicy) -> None:
        if order_plan.risk_check_id is None or order_plan.status == OrderStatus.draft:
            raise RiskCheckRequired("risk_checked is required before submission")
        if order_plan.risk_check_expires_at is not None and order_plan.risk_check_expires_at <= self.now():
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.expired,
                audit=self.audit,
                user_id=policy.user_id,
                source="execution_service",
                action="risk_check_expired",
            )
            self.repositories.order_plans.update(order_plan)
            raise RiskCheckRequired("fresh risk check is required before submission")
        if policy.execution_mode.value == "approval_required" and order_plan.status != OrderStatus.user_approved:
            raise ApprovalRequired("explicit user approval is required before submission")

    def _strategy_id_for_order(self, order_plan: OrderPlan) -> str:
        return order_plan.explanation.strategy_id if order_plan.explanation else "unknown_strategy"

    def _fresh_submission_risk_check(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        snapshot: PortfolioSnapshot,
        strategy_id: str,
    ):
        return run_risk_check(
            policy=policy,
            order_plan=order_plan,
            snapshot=snapshot,
            seen_idempotency_keys=self._seen_submitted_keys(exclude_order_plan_id=order_plan.order_plan_id),
            guardrail_state=self.guardrail_state(policy=policy, strategy_id=strategy_id, exclude_order_plan_id=order_plan.order_plan_id),
            quote_max_age_seconds=policy.stale_quote_max_age_seconds,
            strategy_id=strategy_id,
        )

    def _submit_batch_risk_decision(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        snapshot: PortfolioSnapshot,
        strategy_id: str,
    ) -> BatchRiskDecision:
        context = build_submit_batch_context(
            order_plans=self.repositories.order_plans.list(),
            order_plan=order_plan,
            policy=policy,
            strategy_id=strategy_id,
            autopilot_paused=self.autopilot_paused(),
            last_blocked_reason=self.last_blocked_reason(),
        )
        return run_batch_risk_gate(
            policy=policy,
            portfolio_plan=self._portfolio_plan_for_order_batch(policy=policy, order_plans=context.batch_orders),
            snapshot=snapshot,
            quotes=context.quotes,
            order_plans=context.batch_orders,
            config=BatchRiskConfig(quote_max_age_seconds=policy.stale_quote_max_age_seconds),
            guardrail_state=context.guardrail_state,
            seen_idempotency_keys=context.seen_idempotency_keys,
        )

    def _portfolio_plan_for_order_batch(self, *, policy: UserPolicy, order_plans: list[OrderPlan]) -> PortfolioPlan:
        return PortfolioPlan(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            target_weights={},
            cash_target_weight=0.0,
            order_intents=[order.intent for order in order_plans],
        )

    def _submit_to_broker(self, *, policy: UserPolicy, order_plan: OrderPlan) -> tuple[OrderPlan, BrokerOrder, list[Fill]]:
        broker = self.broker_for_policy(policy)
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.submitted,
            audit=self.audit,
            user_id=policy.user_id,
            source="execution_service",
        )
        broker_order, fills = broker.submit_order(order_plan)
        self.repositories.broker_orders.add(broker_order)
        self.ledger.record_submitted(policy=policy, order_plan=order_plan, broker_order=broker_order)
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.accepted,
            audit=self.audit,
            user_id=policy.user_id,
            source="broker_adapter",
        )
        for index, fill in enumerate(fills):
            self.repositories.fills.add(fill)
            self.audit.emit(
                user_id=policy.user_id,
                entity_type="fill",
                entity_id=fill.fill_id,
                action="fill_recorded",
                after_state=fill,
                source="broker_adapter",
            )
            self.ledger.record_fill(
                policy=policy,
                order_plan=order_plan,
                broker_order=broker_order,
                fill=fill,
                partial=len(fills) > 1 and index < len(fills) - 1,
            )
        if len(fills) > 1:
            transition_order_plan(
                order_plan=order_plan,
                new_status=OrderStatus.partially_filled,
                audit=self.audit,
                user_id=policy.user_id,
                source="broker_adapter",
            )
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.filled,
            audit=self.audit,
            user_id=policy.user_id,
            source="broker_adapter",
        )
        if fills:
            self.ledger.record_position_update(policy=policy, order_plan=order_plan, broker_order=broker_order, fills=fills)
        self.repositories.order_plans.update(order_plan)
        return order_plan, broker_order, fills

    def _seen_submitted_keys(self, *, exclude_order_plan_id: str) -> set[str]:
        return {
            order.idempotency_key
            for order in self.repositories.order_plans.list()
            if order.order_plan_id != exclude_order_plan_id
            and order.status in {
                OrderStatus.submitted,
                OrderStatus.accepted,
                OrderStatus.partially_filled,
                OrderStatus.filled,
            }
        }
