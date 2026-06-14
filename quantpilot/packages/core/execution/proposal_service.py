from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from quantpilot.packages.core.execution.state_machine import RiskCheckRequired, transition_order_plan
from quantpilot.packages.core.ledger.service import ReconciliationLedgerService
from quantpilot.packages.core.portfolio.planner import current_weight, fixture_portfolio_snapshot, proposal_idempotency_key
from quantpilot.packages.core.risk.batch import run_batch_risk_gate
from quantpilot.packages.core.risk.types import BatchRiskConfig, BatchRiskDecision
from quantpilot.packages.core.schemas import (
    GuardrailState,
    OrderIntent,
    OrderPlan,
    OrderStatus,
    PortfolioPlan,
    PortfolioSnapshot,
    ProposalExplanation,
    RiskCheck,
    Signal,
    StrategyRecipe,
    UserPolicy,
    new_id,
)
from quantpilot.packages.core.execution.order_context import quotes_for_intents
from quantpilot.packages.db.audit import AuditRecorder
from quantpilot.packages.db.repositories import RepositoryRegistry


@dataclass(frozen=True)
class ProposalCandidate:
    order_plan: OrderPlan
    signal: Signal | None
    strategy_id: str
    strategy_version: str


class ProposalService:
    def __init__(
        self,
        *,
        repositories: RepositoryRegistry,
        audit: AuditRecorder,
        ledger: ReconciliationLedgerService,
        load_strategy: Callable[[], StrategyRecipe],
        seen_idempotency_keys: Callable[..., set[str]],
        guardrail_state: Callable[..., GuardrailState],
        risk_check: Callable[..., RiskCheck],
        now: Callable[[], datetime],
    ) -> None:
        self.repositories = repositories
        self.audit = audit
        self.ledger = ledger
        self.load_strategy = load_strategy
        self.seen_idempotency_keys = seen_idempotency_keys
        self.guardrail_state = guardrail_state
        self.risk_check = risk_check
        self.now = now

    def generate_order_proposals(
        self,
        *,
        portfolio_plan_id: str,
        snapshot: PortfolioSnapshot | None = None,
        partial_allow: bool = False,
    ) -> list[OrderPlan]:
        portfolio_plan = self.repositories.portfolio_plans.require(portfolio_plan_id)
        policy = self.repositories.policies.require(portfolio_plan.policy_id)
        strategy = self.load_strategy()
        portfolio_snapshot = snapshot or fixture_portfolio_snapshot()
        now = self.now()

        if policy.kill_switch_engaged:
            self._emit_portfolio_blocked(policy=policy, plan=portfolio_plan, reason="kill_switch_not_engaged")
            return []

        ordered_intents = self._ordered_intents(portfolio_plan=portfolio_plan, snapshot=portfolio_snapshot)
        if not ordered_intents:
            self._emit_portfolio_blocked(policy=policy, plan=portfolio_plan, reason="no_order_intents")
            return []

        existing_seen_keys = self.seen_idempotency_keys()
        candidates = self._build_candidates(
            policy=policy,
            strategy=strategy,
            intents=ordered_intents,
            existing_seen_keys=existing_seen_keys,
            portfolio_plan=portfolio_plan,
            now=now,
        )
        if not candidates:
            return []

        batch_decision = self._run_proposal_batch_risk(
            policy=policy,
            portfolio_plan=portfolio_plan,
            snapshot=portfolio_snapshot,
            candidates=candidates,
            strategy=strategy,
            partial_allow=partial_allow,
            seen_idempotency_keys=existing_seen_keys,
            now=now,
        )
        if not batch_decision.passed:
            self._emit_batch_risk_audit(policy=policy, portfolio_plan=portfolio_plan, batch_decision=batch_decision)
            return []
        if batch_decision.mode == "partial_batch":
            self._emit_batch_risk_audit(policy=policy, portfolio_plan=portfolio_plan, batch_decision=batch_decision)

        return self._persist_accepted_candidates(
            policy=policy,
            candidates=candidates,
            portfolio_plan=portfolio_plan,
            portfolio_snapshot=portfolio_snapshot,
            accepted_order_ids=set(batch_decision.accepted_order_plan_ids),
            rejected_reasons=batch_decision.rejected_reasons,
            seen_keys=set(existing_seen_keys),
            now=now,
        )

    def _run_proposal_batch_risk(
        self,
        *,
        policy: UserPolicy,
        portfolio_plan: PortfolioPlan,
        snapshot: PortfolioSnapshot,
        candidates: list[ProposalCandidate],
        strategy: StrategyRecipe,
        partial_allow: bool,
        seen_idempotency_keys: set[str],
        now: datetime,
    ) -> BatchRiskDecision:
        return run_batch_risk_gate(
            policy=policy,
            portfolio_plan=portfolio_plan,
            snapshot=snapshot,
            quotes=quotes_for_intents([candidate.order_plan.intent for candidate in candidates]),
            order_plans=[candidate.order_plan for candidate in candidates],
            config=BatchRiskConfig(
                partial_allow=partial_allow,
                quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
            ),
            guardrail_state=self.guardrail_state(policy=policy, strategy_id=strategy.strategy_id),
            seen_idempotency_keys=seen_idempotency_keys,
            now=now,
        )

    def _emit_batch_risk_audit(
        self,
        *,
        policy: UserPolicy,
        portfolio_plan: PortfolioPlan,
        batch_decision: BatchRiskDecision,
    ) -> None:
        action = "batch_risk_partial_allowed" if batch_decision.mode == "partial_batch" else "batch_risk_rejected"
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="portfolio_plan",
            entity_id=portfolio_plan.plan_id,
            action=action,
            after_state=batch_decision,
            source="batch_risk_gate",
        )

    def modify_order_plan(self, order_plan_id: str, *, quantity: float, limit_price: float | None) -> OrderPlan:
        original = self.repositories.order_plans.require(order_plan_id)
        policy = self.repositories.policies.require(original.policy_id)
        self._validate_modification(original=original, quantity=quantity, limit_price=limit_price)
        new_order = self._build_modified_order(policy=policy, original=original, quantity=quantity, limit_price=limit_price)
        risk_check = self._risk_check_modified_order(policy=policy, original=original, new_order=new_order)
        if not risk_check.passed:
            self._emit_modified_risk_failure(policy=policy, original=original, risk_check=risk_check)
            raise RiskCheckRequired("modified proposal failed risk check")

        new_order.risk_check_id = risk_check.risk_check_id
        new_order.risk_check_expires_at = risk_check.expires_at
        self._copy_modified_explanation(original=original, new_order=new_order, risk_check=risk_check)
        return self._record_modified_order(policy=policy, original=original, new_order=new_order)

    def _build_modified_order(
        self,
        *,
        policy: UserPolicy,
        original: OrderPlan,
        quantity: float,
        limit_price: float | None,
    ) -> OrderPlan:
        modified_intent = OrderIntent(
            symbol=original.intent.symbol,
            side=original.intent.side,
            order_type=original.intent.order_type,
            quantity=quantity,
            limit_price=limit_price,
            notional=round(quantity * (limit_price or original.intent.limit_price or 0), 2),
            target_weight=original.intent.target_weight,
            reason=original.intent.reason,
            quote_time=self.now(),
        )
        return OrderPlan(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            intent=modified_intent,
            idempotency_key=f"{original.idempotency_key}:mod:{new_id('mod')}",
            auto_order_reference_price=original.auto_order_reference_price,
            replaces_order_plan_id=original.order_plan_id,
            expires_at=self.now() + timedelta(minutes=policy.order_expiry_minutes),
        )

    def _risk_check_modified_order(self, *, policy: UserPolicy, original: OrderPlan, new_order: OrderPlan) -> RiskCheck:
        strategy_id = original.explanation.strategy_id if original.explanation else "unknown_strategy"
        return self.risk_check(
            policy=policy,
            order_plan=new_order,
            snapshot=fixture_portfolio_snapshot(),
            seen_idempotency_keys=self.seen_idempotency_keys(),
            guardrail_state=self.guardrail_state(
                policy=policy,
                strategy_id=strategy_id,
                exclude_order_plan_id=original.order_plan_id,
            ),
            quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
            strategy_id=strategy_id,
        )

    def _emit_modified_risk_failure(self, *, policy: UserPolicy, original: OrderPlan, risk_check: RiskCheck) -> None:
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="order_plan",
            entity_id=original.order_plan_id,
            action="proposal_blocked",
            after_state={"reason": "modified_proposal_failed_risk", "failed_checks": risk_check.failed_checks},
            source="user_modification",
        )

    def _copy_modified_explanation(self, *, original: OrderPlan, new_order: OrderPlan, risk_check: RiskCheck) -> None:
        if original.explanation is not None:
            new_order.explanation = original.explanation.model_copy(
                update={
                    "quantity": new_order.intent.quantity,
                    "limit_price": new_order.intent.limit_price,
                    "estimated_notional": new_order.intent.notional,
                    "estimated_cash_impact": new_order.intent.notional if new_order.intent.side == "buy" else -new_order.intent.notional,
                    "risk_checks_passed": risk_check.passed_checks,
                    "risk_checks_failed": risk_check.failed_checks,
                    "risk_check_id": risk_check.risk_check_id,
                    "risk_check_expires_at": risk_check.expires_at,
                    "idempotency_key": new_order.idempotency_key,
                }
            )

    def _record_modified_order(self, *, policy: UserPolicy, original: OrderPlan, new_order: OrderPlan) -> OrderPlan:
        transition_order_plan(
            order_plan=original,
            new_status=OrderStatus.modified,
            audit=self.audit,
            user_id=policy.user_id,
            source="user_modification",
            action="proposal_modified",
        )
        self.repositories.order_plans.update(original)
        self.repositories.order_plans.add(new_order)
        self.ledger.record_order_intent(
            policy=policy,
            order_plan=new_order,
            metadata={"replaces_order_plan_id": original.order_plan_id, "source": "user_modification"},
        )
        transition_order_plan(
            order_plan=new_order,
            new_status=OrderStatus.risk_checked,
            audit=self.audit,
            user_id=policy.user_id,
            source="risk_gatekeeper",
            action="risk_check_passed",
        )
        transition_order_plan(
            order_plan=new_order,
            new_status=OrderStatus.proposed,
            audit=self.audit,
            user_id=policy.user_id,
            source="user_modification",
            action="proposal_created",
        )
        return self.repositories.order_plans.update(new_order)

    def _ordered_intents(self, *, portfolio_plan: PortfolioPlan, snapshot: PortfolioSnapshot) -> list[OrderIntent]:
        return sorted(
            portfolio_plan.order_intents,
            key=lambda intent: abs(intent.target_weight - current_weight(snapshot, intent.symbol)),
            reverse=True,
        )

    def _build_candidates(
        self,
        *,
        policy: UserPolicy,
        strategy: StrategyRecipe,
        intents: list[OrderIntent],
        existing_seen_keys: set[str],
        portfolio_plan: PortfolioPlan,
        now: datetime,
    ) -> list[ProposalCandidate]:
        signals_by_symbol = {signal.symbol: signal for signal in self.repositories.signals.list()}
        candidates: list[ProposalCandidate] = []
        for intent in intents:
            signal = signals_by_symbol.get(intent.symbol)
            strategy_id = signal.strategy_id if signal else strategy.strategy_id
            strategy_version = signal.recipe_version if signal else strategy.version
            trading_date = signal.signal_date if signal else now.date()
            key = proposal_idempotency_key(
                policy=policy,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                symbol=intent.symbol,
                side=intent.side,
                trading_date=trading_date,
            )
            if key in existing_seen_keys:
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="portfolio_plan",
                    entity_id=portfolio_plan.plan_id,
                    action="proposal_blocked",
                    after_state={"reason": "duplicate_order_blocked", "idempotency_key": key},
                    source="level3_proposal_service",
                )
                continue
            candidates.append(
                ProposalCandidate(
                    order_plan=OrderPlan(
                        policy_id=policy.policy_id,
                        policy_version=policy.version,
                        intent=intent,
                        idempotency_key=key,
                        auto_order_reference_price=intent.limit_price,
                        expires_at=now + timedelta(minutes=policy.order_expiry_minutes),
                    ),
                    signal=signal,
                    strategy_id=strategy_id,
                    strategy_version=strategy_version,
                )
            )
        return candidates

    def _persist_accepted_candidates(
        self,
        *,
        policy: UserPolicy,
        candidates: list[ProposalCandidate],
        portfolio_plan: PortfolioPlan,
        portfolio_snapshot: PortfolioSnapshot,
        accepted_order_ids: set[str],
        rejected_reasons: dict[str, list[str]],
        seen_keys: set[str],
        now: datetime,
    ) -> list[OrderPlan]:
        created: list[OrderPlan] = []
        for candidate in candidates:
            order_plan = candidate.order_plan
            if order_plan.order_plan_id not in accepted_order_ids:
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="order_plan",
                    entity_id=order_plan.order_plan_id,
                    action="proposal_blocked",
                    after_state={
                        "reason": "batch_risk_rejected",
                        "batch_reasons": rejected_reasons.get(order_plan.order_plan_id, []),
                    },
                    source="batch_risk_gate",
                )
                continue

            risk_check = self.risk_check(
                policy=policy,
                order_plan=order_plan,
                snapshot=portfolio_snapshot,
                seen_idempotency_keys=seen_keys,
                guardrail_state=self.guardrail_state(
                    policy=policy,
                    strategy_id=candidate.strategy_id,
                    exclude_order_plan_id=order_plan.order_plan_id,
                ),
                quote_max_age_seconds=policy.human_review_quote_max_age_seconds,
                strategy_id=candidate.strategy_id,
            )
            if not risk_check.passed:
                self.audit.emit(
                    user_id=policy.user_id,
                    entity_type="order_plan",
                    entity_id=order_plan.order_plan_id,
                    action="proposal_blocked",
                    after_state={"failed_checks": risk_check.failed_checks, "idempotency_key": order_plan.idempotency_key},
                    source="level3_proposal_service",
                )
                continue

            order_plan.risk_check_id = risk_check.risk_check_id
            order_plan.risk_check_expires_at = risk_check.expires_at
            order_plan.explanation = self._proposal_explanation(
                policy=policy,
                order_plan=order_plan,
                signal=candidate.signal,
                strategy_id=candidate.strategy_id,
                strategy_version=candidate.strategy_version,
                snapshot=portfolio_snapshot,
                risk_check=risk_check,
                now=now,
            )
            self._record_proposal(policy=policy, portfolio_plan=portfolio_plan, candidate=candidate)
            created.append(self.repositories.order_plans.update(order_plan))
            seen_keys.add(order_plan.idempotency_key)
        return created

    def _record_proposal(
        self,
        *,
        policy: UserPolicy,
        portfolio_plan: PortfolioPlan,
        candidate: ProposalCandidate,
    ) -> None:
        order_plan = candidate.order_plan
        self.repositories.order_plans.add(order_plan)
        self.ledger.record_order_intent(
            policy=policy,
            order_plan=order_plan,
            metadata={
                "portfolio_plan_id": portfolio_plan.plan_id,
                "strategy_id": candidate.strategy_id,
                "strategy_version": candidate.strategy_version,
                "source": "level3_proposal_service",
            },
        )
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.risk_checked,
            audit=self.audit,
            user_id=policy.user_id,
            source="risk_gatekeeper",
            action="risk_check_passed",
        )
        transition_order_plan(
            order_plan=order_plan,
            new_status=OrderStatus.proposed,
            audit=self.audit,
            user_id=policy.user_id,
            source="level3_proposal_service",
            action="proposal_created",
        )

    def _proposal_explanation(
        self,
        *,
        policy: UserPolicy,
        order_plan: OrderPlan,
        signal: Signal | None,
        strategy_id: str,
        strategy_version: str,
        snapshot: PortfolioSnapshot,
        risk_check: RiskCheck,
        now: datetime,
    ) -> ProposalExplanation:
        intent = order_plan.intent
        current = current_weight(snapshot, intent.symbol)
        quote_age = (now - intent.quote_time).total_seconds()
        warnings = []
        if quote_age > policy.stale_quote_max_age_seconds:
            warnings.append("stale_quote_warning")
        return ProposalExplanation(
            symbol=intent.symbol,
            action=intent.side,
            quantity=intent.quantity,
            target_weight_delta=round(intent.target_weight - current, 6),
            reference_price=float(intent.limit_price or 0),
            estimated_cash_impact=round(intent.notional if intent.side == "buy" else -intent.notional, 2),
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            signal_reason=signal.reason if signal else intent.reason,
            reason_codes=signal.reason_codes if signal else [],
            current_weight=round(current, 6),
            target_weight=intent.target_weight,
            weight_delta=round(intent.target_weight - current, 6),
            quote_price=float(intent.limit_price or 0),
            quote_age_seconds=round(max(0.0, quote_age), 3),
            limit_price=intent.limit_price,
            estimated_notional=intent.notional,
            stop_price_hint=signal.stop_price_hint if signal else None,
            take_profit_hint=signal.take_profit_hint if signal else None,
            risk_checks_passed=risk_check.passed_checks,
            risk_checks_failed=risk_check.failed_checks,
            risk_check_id=risk_check.risk_check_id,
            risk_check_expires_at=risk_check.expires_at,
            idempotency_key=order_plan.idempotency_key,
            policy_version=policy.version,
            warnings=warnings,
        )

    def _validate_modification(self, *, original: OrderPlan, quantity: float, limit_price: float | None) -> None:
        if original.status != OrderStatus.proposed:
            raise RuntimeError("only proposed orders can be modified")
        if quantity <= 0 or quantity > original.intent.quantity:
            raise RuntimeError("quantity can only be reduced")
        if original.intent.limit_price is None or limit_price is None:
            return
        lower = original.intent.limit_price * 0.98
        upper = original.intent.limit_price * 1.02
        if not lower <= limit_price <= upper:
            raise RuntimeError("limit_price modification must stay within 2 percent")
        if original.intent.side == "buy" and original.auto_order_reference_price is not None and limit_price > original.auto_order_reference_price:
            raise RuntimeError("buy limit price cannot chase above the reference price")

    def _emit_portfolio_blocked(self, *, policy: UserPolicy, plan: PortfolioPlan, reason: str) -> None:
        self.audit.emit(
            user_id=policy.user_id,
            entity_type="portfolio_plan",
            entity_id=plan.plan_id,
            action="proposal_blocked",
            after_state={"reason": reason},
            source="level3_proposal_service",
        )
