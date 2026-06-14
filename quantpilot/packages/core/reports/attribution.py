from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType
from quantpilot.packages.core.normalization import first_not_none, symbol_key, unique_text
from quantpilot.packages.core.reports.metrics_types import PaperTrialMetrics
from quantpilot.packages.core.reports.report_types import (
    AttributionReport,
    DecisionExplanationType,
    PolicyIntentSummary,
    PositionAttribution,
    RejectedTrimmedExplanation,
    ReviewFlag,
    RiskBudgetAttribution,
    SectorAttribution,
    SignalContribution,
    ThemeAttribution,
)
from quantpilot.packages.core.risk.types import BatchRiskDecision
from quantpilot.packages.core.schemas import OrderPlan, PortfolioPlan, PortfolioSnapshot, Signal, UserPolicy


@dataclass(frozen=True)
class _SelectedAttributionInputs:
    entries: list[LedgerEntry]
    signals: list[Signal]
    orders: list[OrderPlan]
    risk_decisions: list[BatchRiskDecision]
    latest_plan: PortfolioPlan | None
    intended_by_symbol: dict[str, float]
    filled_by_symbol: dict[str, float]
    rejected_by_symbol: dict[str, float]
    touched_symbols: list[str]


@dataclass
class _RejectedTrimmedCollector:
    explanations: list[RejectedTrimmedExplanation] = field(default_factory=list)
    seen: set[tuple[str, str | None, str | None, str]] = field(default_factory=set)

    def add(
        self,
        *,
        decision_type: DecisionExplanationType,
        order_plan_id: str | None,
        intent_id: str | None,
        symbol: str | None,
        reason_codes: Iterable[str],
        notional: float,
        source: str,
    ) -> None:
        normalized_reasons = unique_text(reason_codes) or ["unknown"]
        key = (decision_type, order_plan_id, intent_id, source)
        if key in self.seen:
            return
        self.seen.add(key)
        detail = ", ".join(normalized_reasons)
        self.explanations.append(
            RejectedTrimmedExplanation(
                order_plan_id=order_plan_id,
                intent_id=intent_id,
                symbol=symbol_key(symbol) if symbol else None,
                decision_type=decision_type,
                reason_codes=normalized_reasons,
                notional=round(notional, 2),
                source=source,
                explanation=f"{decision_type} {order_plan_id or intent_id or 'unknown'} because {detail}.",
            )
        )


@dataclass(frozen=True)
class _PositionAttributionContext:
    entries_by_key: dict[str, list[LedgerEntry]]
    orders_by_key: dict[str, OrderPlan]
    signals_by_symbol: dict[str, Signal]
    risk_reasons_by_key: dict[str, list[str]]
    trimmed_keys: set[str]
    rejected_keys: set[str]
    keys: list[str]


class AttributionReportBuilder:
    def build(
        self,
        *,
        policy: UserPolicy,
        ledger_entries: Sequence[LedgerEntry],
        paper_metrics: PaperTrialMetrics,
        signals: Sequence[Signal] | None = None,
        orders: Sequence[OrderPlan] | None = None,
        portfolio_plans: Sequence[PortfolioPlan] | None = None,
        batch_risk_decisions: Sequence[BatchRiskDecision] | None = None,
        snapshot: PortfolioSnapshot | None = None,
    ) -> AttributionReport:
        selected = self._selected_inputs(
            policy=policy,
            ledger_entries=ledger_entries,
            signals=signals,
            orders=orders,
            portfolio_plans=portfolio_plans,
            batch_risk_decisions=batch_risk_decisions,
        )
        rejected_trimmed = self._rejected_trimmed_explanations(
            entries=selected.entries,
            orders=selected.orders,
            batch_risk_decisions=selected.risk_decisions,
        )
        review_flags = self._review_flags(
            entries=selected.entries,
            paper_metrics=paper_metrics,
            signals=selected.signals,
            rejected_trimmed=rejected_trimmed,
            policy=policy,
        )
        status, unavailable_reason = self._availability(entries=selected.entries, paper_metrics=paper_metrics)
        return self._build_report(
            policy=policy,
            selected=selected,
            paper_metrics=paper_metrics,
            snapshot=snapshot,
            rejected_trimmed=rejected_trimmed,
            review_flags=review_flags,
            status=status,
            unavailable_reason=unavailable_reason,
        )

    def _build_report(
        self,
        *,
        policy: UserPolicy,
        selected: _SelectedAttributionInputs,
        paper_metrics: PaperTrialMetrics,
        snapshot: PortfolioSnapshot | None,
        rejected_trimmed: Sequence[RejectedTrimmedExplanation],
        review_flags: Sequence[ReviewFlag],
        status: str,
        unavailable_reason: str | None,
    ) -> AttributionReport:
        return AttributionReport(
            status=status,
            unavailable_reason=unavailable_reason,
            policy_intent=self._policy_intent(policy),
            ledger_event_count=len(selected.entries),
            ledger_sources=sorted({entry.source for entry in selected.entries}),
            data_modes=sorted({entry.data_mode for entry in selected.entries}),
            paper_trial_metrics=paper_metrics,
            signal_contributions=self._signal_contributions(
                signals=selected.signals,
                latest_plan=selected.latest_plan,
                intended_by_symbol=selected.intended_by_symbol,
                filled_by_symbol=selected.filled_by_symbol,
            ),
            risk_budget=self._risk_budget(
                paper_metrics=paper_metrics,
                batch_risk_decisions=selected.risk_decisions,
            ),
            sector_attribution=self._sector_attribution(
                symbols=selected.touched_symbols,
                intended_by_symbol=selected.intended_by_symbol,
                filled_by_symbol=selected.filled_by_symbol,
                rejected_by_symbol=selected.rejected_by_symbol,
                latest_plan=selected.latest_plan,
                snapshot=snapshot,
            ),
            theme_attribution=self._theme_attribution(
                policy=policy,
                symbols=selected.touched_symbols,
                signals=selected.signals,
                intended_by_symbol=selected.intended_by_symbol,
                filled_by_symbol=selected.filled_by_symbol,
            ),
            position_attribution=self._position_attribution(
                entries=selected.entries,
                orders=selected.orders,
                signals=selected.signals,
                batch_risk_decisions=selected.risk_decisions,
            ),
            rejected_trimmed_explanations=rejected_trimmed,
            review_flags=review_flags,
            live_trading_enabled=False,
        )

    def _selected_inputs(
        self,
        *,
        policy: UserPolicy,
        ledger_entries: Sequence[LedgerEntry],
        signals: Sequence[Signal] | None,
        orders: Sequence[OrderPlan] | None,
        portfolio_plans: Sequence[PortfolioPlan] | None,
        batch_risk_decisions: Sequence[BatchRiskDecision] | None,
    ) -> _SelectedAttributionInputs:
        entries = [entry for entry in ledger_entries if entry.policy_id == policy.policy_id]
        selected_signals = [
            signal
            for signal in (signals or [])
            if signal.policy_version in {None, policy.version}
        ]
        selected_orders = [order for order in (orders or []) if order.policy_id == policy.policy_id]
        selected_plans = [plan for plan in (portfolio_plans or []) if plan.policy_id == policy.policy_id]
        latest_plan = selected_plans[-1] if selected_plans else None
        intended_by_symbol = self._notional_by_symbol(entries, {LedgerEventType.order_intent})
        filled_by_symbol = self._notional_by_symbol(entries, {LedgerEventType.fill, LedgerEventType.partial_fill})
        rejected_by_symbol = self._notional_by_symbol(entries, {LedgerEventType.reject})
        return _SelectedAttributionInputs(
            entries=entries,
            signals=selected_signals,
            orders=selected_orders,
            risk_decisions=list(batch_risk_decisions or []),
            latest_plan=latest_plan,
            intended_by_symbol=intended_by_symbol,
            filled_by_symbol=filled_by_symbol,
            rejected_by_symbol=rejected_by_symbol,
            touched_symbols=self._touched_symbols(
                entries=entries,
                signals=selected_signals,
                orders=selected_orders,
                latest_plan=latest_plan,
            ),
        )

    def _availability(
        self,
        *,
        entries: Sequence[LedgerEntry],
        paper_metrics: PaperTrialMetrics,
    ) -> tuple[str, str | None]:
        if entries and paper_metrics.status == "available":
            return "available", None
        return "unavailable", paper_metrics.unavailable_reason or "missing_required_report_inputs"

    def unavailable_report(
        self,
        *,
        policy: UserPolicy,
        paper_metrics: PaperTrialMetrics,
        reason: str,
    ) -> AttributionReport:
        review_flags = [
            ReviewFlag(
                code="attribution_unavailable",
                severity="warning",
                detail=reason,
            )
        ]
        return AttributionReport(
            status="unavailable",
            unavailable_reason=reason,
            policy_intent=self._policy_intent(policy),
            paper_trial_metrics=paper_metrics,
            risk_budget=self._risk_budget(paper_metrics=paper_metrics, batch_risk_decisions=[]),
            review_flags=review_flags,
            live_trading_enabled=False,
        )

    def _policy_intent(self, policy: UserPolicy) -> PolicyIntentSummary:
        order_types = [order_type.value for order_type in policy.allowed_order_types]
        summary = (
            f"{policy.risk_profile} policy in {policy.execution_mode.value} mode with "
            f"{policy.broker.value} broker, max position {policy.max_position_weight:.2%}, "
            f"max sector {policy.max_sector_weight:.2%}, and minimum cash {policy.min_cash_weight:.2%}."
        )
        return PolicyIntentSummary(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            execution_mode=policy.execution_mode.value,
            broker=policy.broker.value,
            risk_profile=policy.risk_profile,
            authority_level=policy.authority_level,
            max_position_weight=policy.max_position_weight,
            max_sector_weight=policy.max_sector_weight,
            min_cash_weight=policy.min_cash_weight,
            max_daily_turnover=policy.max_daily_turnover,
            single_order_cash_limit=policy.single_order_cash_limit,
            allowed_order_types=order_types,
            preferred_sectors=policy.preferred_sectors,
            preferred_themes=policy.preferred_themes,
            summary=summary,
        )

    def _risk_budget(
        self,
        *,
        paper_metrics: PaperTrialMetrics,
        batch_risk_decisions: Sequence[BatchRiskDecision],
    ) -> RiskBudgetAttribution:
        usage = paper_metrics.risk_budget_usage
        accepted_ids = self._unique(
            order_id
            for decision in batch_risk_decisions
            for order_id in decision.accepted_order_plan_ids
        )
        rejected_ids = self._unique(
            order_id
            for decision in batch_risk_decisions
            for order_id in decision.rejected_order_plan_ids
        )
        stale_reasons = self._unique(
            reason
            for decision in batch_risk_decisions
            for reason in decision.stale_input_reasons
        )
        status = "available" if paper_metrics.status == "available" or batch_risk_decisions else "unavailable"
        unavailable_reason = None if status == "available" else paper_metrics.unavailable_reason
        failed_check_counts = Counter(usage.failed_check_counts)
        if not failed_check_counts:
            for decision in batch_risk_decisions:
                failed_check_counts.update(decision.failed_checks)
                for reasons in decision.rejected_reasons.values():
                    failed_check_counts.update(reasons)
        explanation = (
            f"Risk budget used {usage.turnover_used:.2f} notional"
            if status == "available"
            else f"Risk budget unavailable: {unavailable_reason or 'missing inputs'}"
        )
        if usage.daily_turnover_usage is not None:
            explanation = f"{explanation} ({usage.daily_turnover_usage:.2%} of daily turnover limit)"
        return RiskBudgetAttribution(
            status=status,
            unavailable_reason=unavailable_reason,
            turnover_used=usage.turnover_used,
            max_daily_turnover=usage.max_daily_turnover,
            daily_turnover_usage=usage.daily_turnover_usage,
            largest_order_notional=usage.largest_order_notional,
            single_order_cash_limit=usage.single_order_cash_limit,
            largest_order_usage=usage.largest_order_usage,
            failed_check_counts=dict(sorted(failed_check_counts.items())),
            batch_decision_count=len(batch_risk_decisions),
            accepted_order_plan_ids=accepted_ids,
            rejected_order_plan_ids=rejected_ids,
            stale_input_reasons=stale_reasons,
            explanation=explanation,
        )

    def _signal_contributions(
        self,
        *,
        signals: Sequence[Signal],
        latest_plan: PortfolioPlan | None,
        intended_by_symbol: dict[str, float],
        filled_by_symbol: dict[str, float],
    ) -> list[SignalContribution]:
        target_weights = self._target_weights(latest_plan)
        contributions: list[SignalContribution] = []
        for signal in sorted(signals, key=lambda item: self._symbol(item.symbol)):
            symbol = self._symbol(signal.symbol)
            planned_target = target_weights.get(symbol)
            basis = planned_target if planned_target is not None else signal.target_weight_hint or 0.0
            contribution_score = round(signal.strength * basis, 6)
            action = signal.action.value
            explanation = (
                f"{symbol} signal {action} contributed score {contribution_score:.6f} "
                f"from strength {signal.strength:.3f} and target basis {basis:.6f}."
            )
            contributions.append(
                SignalContribution(
                    symbol=symbol,
                    signal_id=signal.signal_id,
                    action=action,
                    strength=signal.strength,
                    contribution_score=contribution_score,
                    target_weight_hint=signal.target_weight_hint,
                    planned_target_weight=planned_target,
                    intended_notional=intended_by_symbol.get(symbol, 0.0),
                    filled_notional=filled_by_symbol.get(symbol, 0.0),
                    source=signal.source,
                    reason_codes=signal.reason_codes,
                    explanation=explanation,
                )
            )
        return contributions

    def _sector_attribution(
        self,
        *,
        symbols: Sequence[str],
        intended_by_symbol: dict[str, float],
        filled_by_symbol: dict[str, float],
        rejected_by_symbol: dict[str, float],
        latest_plan: PortfolioPlan | None,
        snapshot: PortfolioSnapshot | None,
    ) -> list[SectorAttribution]:
        target_weights = self._target_weights(latest_plan)
        sector_by_symbol = self._sector_by_symbol(snapshot)
        current_by_sector = self._current_weight_by_sector(snapshot)
        symbols_by_sector: dict[str, set[str]] = defaultdict(set)
        for symbol in symbols:
            symbols_by_sector[sector_by_symbol.get(symbol, "unknown")].add(symbol)
        for symbol in target_weights:
            symbols_by_sector[sector_by_symbol.get(symbol, "unknown")].add(symbol)

        sectors: list[SectorAttribution] = []
        for sector in sorted(symbols_by_sector):
            sector_symbols = sorted(symbols_by_sector[sector])
            intended = round(sum(intended_by_symbol.get(symbol, 0.0) for symbol in sector_symbols), 2)
            filled = round(sum(filled_by_symbol.get(symbol, 0.0) for symbol in sector_symbols), 2)
            rejected = round(sum(rejected_by_symbol.get(symbol, 0.0) for symbol in sector_symbols), 2)
            target = round(sum(target_weights.get(symbol, 0.0) for symbol in sector_symbols), 6)
            current = current_by_sector.get(sector)
            explanation = (
                f"{sector} sector attribution: intended {intended:.2f}, "
                f"filled {filled:.2f}, rejected {rejected:.2f}."
            )
            sectors.append(
                SectorAttribution(
                    sector=sector,
                    symbols=sector_symbols,
                    current_weight=current,
                    planned_target_weight=target if target > 0 else None,
                    intended_notional=intended,
                    filled_notional=filled,
                    rejected_notional=rejected,
                    explanation=explanation,
                )
            )
        return sectors

    def _theme_attribution(
        self,
        *,
        policy: UserPolicy,
        symbols: Sequence[str],
        signals: Sequence[Signal],
        intended_by_symbol: dict[str, float],
        filled_by_symbol: dict[str, float],
    ) -> list[ThemeAttribution]:
        touched_symbols = sorted(set(symbols))
        themes = policy.preferred_themes or ["unclassified"]
        data_status = "available" if policy.preferred_themes else "unavailable"
        signal_count = len(signals)
        intended = round(sum(intended_by_symbol.get(symbol, 0.0) for symbol in touched_symbols), 2)
        filled = round(sum(filled_by_symbol.get(symbol, 0.0) for symbol in touched_symbols), 2)
        reports: list[ThemeAttribution] = []
        for theme in themes:
            if policy.preferred_themes:
                explanation = (
                    f"Policy preferred theme {theme} is associated with the touched symbols "
                    "because symbol-level theme metadata is not persisted yet."
                )
            else:
                explanation = "Theme attribution unavailable because symbol-level theme metadata is not persisted."
            reports.append(
                ThemeAttribution(
                    theme=theme,
                    data_status=data_status,
                    symbols=touched_symbols,
                    signal_count=signal_count,
                    intended_notional=intended,
                    filled_notional=filled,
                    explanation=explanation,
                )
            )
        return reports

    def _position_attribution(
        self,
        *,
        entries: Sequence[LedgerEntry],
        orders: Sequence[OrderPlan],
        signals: Sequence[Signal],
        batch_risk_decisions: Sequence[BatchRiskDecision],
    ) -> list[PositionAttribution]:
        context = self._position_attribution_context(
            entries=entries,
            orders=orders,
            signals=signals,
            batch_risk_decisions=batch_risk_decisions,
        )
        return [
            self._build_position_attribution(key=key, context=context)
            for key in context.keys
        ]

    def _position_attribution_context(
        self,
        *,
        entries: Sequence[LedgerEntry],
        orders: Sequence[OrderPlan],
        signals: Sequence[Signal],
        batch_risk_decisions: Sequence[BatchRiskDecision],
    ) -> _PositionAttributionContext:
        entries_by_key = self._entries_by_key(entries)
        orders_by_key = {order.order_plan_id: order for order in orders}
        risk_reasons_by_key = self._risk_reasons_by_key(batch_risk_decisions)
        trimmed_keys = {
            order_id
            for decision in batch_risk_decisions
            if decision.mode == "partial_batch"
            for order_id in decision.rejected_order_plan_ids
        }
        rejected_keys = {
            order_id
            for decision in batch_risk_decisions
            for order_id in decision.rejected_order_plan_ids
        }
        return _PositionAttributionContext(
            entries_by_key=entries_by_key,
            orders_by_key=orders_by_key,
            signals_by_symbol={self._symbol(signal.symbol): signal for signal in signals},
            risk_reasons_by_key=risk_reasons_by_key,
            trimmed_keys=trimmed_keys,
            rejected_keys=rejected_keys,
            keys=sorted(set(entries_by_key) | set(orders_by_key) | set(risk_reasons_by_key)),
        )

    def _build_position_attribution(
        self,
        *,
        key: str,
        context: _PositionAttributionContext,
    ) -> PositionAttribution:
        grouped_entries = context.entries_by_key.get(key, [])
        order = context.orders_by_key.get(key)
        symbol = self._position_symbol(grouped_entries=grouped_entries, order=order)
        intended = self._position_intended_notional(grouped_entries=grouped_entries, order=order)
        filled = self._entry_notional_sum(grouped_entries, {LedgerEventType.fill, LedgerEventType.partial_fill})
        rejected = self._entry_notional_sum(grouped_entries, {LedgerEventType.reject})
        risk_reasons = context.risk_reasons_by_key.get(key, [])
        status = self._position_status(
            key=key,
            intended=intended,
            filled=filled,
            rejected=rejected,
            order=order,
            trimmed_keys=context.trimmed_keys,
            rejected_keys=context.rejected_keys,
        )
        signal = context.signals_by_symbol.get(symbol)
        signal_reason = order.intent.reason if order is not None else (signal.reason if signal is not None else None)
        return PositionAttribution(
            symbol=symbol,
            order_plan_id=order.order_plan_id if order is not None else self._order_plan_id(grouped_entries),
            intent_id=(order.intent.intent_id if order is not None else self._intent_id(grouped_entries)),
            side=(order.intent.side if order is not None else self._side(grouped_entries)),
            status=status,
            intended_notional=intended,
            filled_notional=filled,
            rejected_notional=rejected,
            target_weight=(order.intent.target_weight if order is not None else self._target_weight(grouped_entries)),
            signal_reason=signal_reason,
            risk_reasons=risk_reasons,
            ledger_event_ids=[entry.ledger_entry_id for entry in grouped_entries],
            explanation=self._position_explanation(
                symbol=symbol,
                status=status,
                intended=intended,
                filled=filled,
                rejected=rejected,
                risk_reasons=risk_reasons,
            ),
        )

    def _position_symbol(
        self,
        *,
        grouped_entries: Sequence[LedgerEntry],
        order: OrderPlan | None,
    ) -> str:
        return self._symbol(
            self._first_not_none([entry.symbol for entry in grouped_entries])
            or (order.intent.symbol if order is not None else "unknown")
        )

    def _position_intended_notional(
        self,
        *,
        grouped_entries: Sequence[LedgerEntry],
        order: OrderPlan | None,
    ) -> float:
        intended = self._entry_notional_sum(grouped_entries, {LedgerEventType.order_intent})
        if intended <= 0 and order is not None:
            return round(order.intent.notional, 2)
        return intended

    def _rejected_trimmed_explanations(
        self,
        *,
        entries: Sequence[LedgerEntry],
        orders: Sequence[OrderPlan],
        batch_risk_decisions: Sequence[BatchRiskDecision],
    ) -> list[RejectedTrimmedExplanation]:
        order_by_id = {order.order_plan_id: order for order in orders}
        entries_by_key = self._entries_by_key(entries)
        collector = _RejectedTrimmedCollector()
        self._collect_ledger_rejections(entries, collector)
        self._collect_batch_risk_rejections(
            batch_risk_decisions=batch_risk_decisions,
            order_by_id=order_by_id,
            entries_by_key=entries_by_key,
            collector=collector,
        )
        self._collect_order_plan_rejections(orders, collector)
        return collector.explanations

    def _collect_ledger_rejections(
        self,
        entries: Sequence[LedgerEntry],
        collector: _RejectedTrimmedCollector,
    ) -> None:
        for entry in entries:
            if entry.event_type != LedgerEventType.reject:
                continue
            collector.add(
                decision_type="rejected",
                order_plan_id=entry.order_plan_id,
                intent_id=entry.intent_id,
                symbol=entry.symbol,
                reason_codes=[str(entry.metadata.get("reason") or "unknown")],
                notional=self._notional(entry),
                source="reconciliation_ledger",
            )

    def _collect_batch_risk_rejections(
        self,
        *,
        batch_risk_decisions: Sequence[BatchRiskDecision],
        order_by_id: dict[str, OrderPlan],
        entries_by_key: dict[str, list[LedgerEntry]],
        collector: _RejectedTrimmedCollector,
    ) -> None:
        for decision in batch_risk_decisions:
            fallback_reasons = self._unique([*decision.failed_checks, *decision.stale_input_reasons])
            decision_type = "trimmed" if decision.mode == "partial_batch" else "rejected"
            handled = self._collect_batch_rejected_reasons(
                decision=decision,
                decision_type=decision_type,
                fallback_reasons=fallback_reasons,
                order_by_id=order_by_id,
                entries_by_key=entries_by_key,
                collector=collector,
            )
            self._collect_batch_rejected_order_ids(
                decision=decision,
                decision_type=decision_type,
                fallback_reasons=fallback_reasons,
                handled=handled,
                order_by_id=order_by_id,
                entries_by_key=entries_by_key,
                collector=collector,
            )

    def _collect_batch_rejected_reasons(
        self,
        *,
        decision: BatchRiskDecision,
        decision_type: DecisionExplanationType,
        fallback_reasons: list[str],
        order_by_id: dict[str, OrderPlan],
        entries_by_key: dict[str, list[LedgerEntry]],
        collector: _RejectedTrimmedCollector,
    ) -> set[str]:
        handled: set[str] = set()
        for order_key, reasons in decision.rejected_reasons.items():
            order = order_by_id.get(order_key)
            grouped = entries_by_key.get(order_key, [])
            self._collect_batch_order(
                decision_type=decision_type,
                order_key=order_key,
                order=order,
                grouped=grouped,
                reason_codes=reasons or fallback_reasons,
                collector=collector,
            )
            handled.add(order_key)
        return handled

    def _collect_batch_rejected_order_ids(
        self,
        *,
        decision: BatchRiskDecision,
        decision_type: DecisionExplanationType,
        fallback_reasons: list[str],
        handled: set[str],
        order_by_id: dict[str, OrderPlan],
        entries_by_key: dict[str, list[LedgerEntry]],
        collector: _RejectedTrimmedCollector,
    ) -> None:
        for order_id in decision.rejected_order_plan_ids:
            if order_id in handled:
                continue
            self._collect_batch_order(
                decision_type=decision_type,
                order_key=order_id,
                order=order_by_id.get(order_id),
                grouped=entries_by_key.get(order_id, []),
                reason_codes=fallback_reasons,
                collector=collector,
            )

    def _collect_batch_order(
        self,
        *,
        decision_type: DecisionExplanationType,
        order_key: str,
        order: OrderPlan | None,
        grouped: Sequence[LedgerEntry],
        reason_codes: Iterable[str],
        collector: _RejectedTrimmedCollector,
    ) -> None:
        collector.add(
            decision_type=decision_type,
            order_plan_id=order.order_plan_id if order is not None else order_key,
            intent_id=order.intent.intent_id if order is not None else self._intent_id(grouped),
            symbol=order.intent.symbol if order is not None else self._symbol_from_entries(grouped),
            reason_codes=reason_codes,
            notional=order.intent.notional if order is not None else self._entry_notional_sum(grouped),
            source="batch_risk_gate",
        )

    def _collect_order_plan_rejections(
        self,
        orders: Sequence[OrderPlan],
        collector: _RejectedTrimmedCollector,
    ) -> None:
        for order in orders:
            if not order.blocked_reason:
                continue
            collector.add(
                decision_type="rejected",
                order_plan_id=order.order_plan_id,
                intent_id=order.intent.intent_id,
                symbol=order.intent.symbol,
                reason_codes=[order.blocked_reason],
                notional=order.intent.notional,
                source="order_plan",
            )

    def _review_flags(
        self,
        *,
        entries: Sequence[LedgerEntry],
        paper_metrics: PaperTrialMetrics,
        signals: Sequence[Signal],
        rejected_trimmed: Sequence[RejectedTrimmedExplanation],
        policy: UserPolicy,
    ) -> list[ReviewFlag]:
        flags: list[ReviewFlag] = []
        if not entries:
            flags.append(
                ReviewFlag(
                    code="missing_ledger",
                    severity="warning",
                    detail="Attribution unavailable because reconciliation ledger entries are missing.",
                )
            )
        if paper_metrics.status == "unavailable":
            flags.append(
                ReviewFlag(
                    code="paper_metrics_unavailable",
                    severity="warning",
                    detail=paper_metrics.unavailable_reason or "Paper trial metrics are unavailable.",
                )
            )
        if not signals:
            flags.append(
                ReviewFlag(
                    code="signal_context_unavailable",
                    severity="warning",
                    detail="No persisted signal context was available for contribution attribution.",
                )
            )
        if rejected_trimmed:
            flags.append(
                ReviewFlag(
                    code="rejected_or_trimmed_orders",
                    severity="warning",
                    detail=f"{len(rejected_trimmed)} rejected or trimmed decision explanations are present.",
                )
            )
        if not policy.preferred_themes:
            flags.append(
                ReviewFlag(
                    code="theme_metadata_unavailable",
                    severity="info",
                    detail="Theme attribution falls back to unclassified because symbol theme metadata is not persisted.",
                )
            )
        return flags

    def _notional_by_symbol(
        self,
        entries: Sequence[LedgerEntry],
        event_types: set[LedgerEventType],
    ) -> dict[str, float]:
        values: dict[str, float] = defaultdict(float)
        for entry in entries:
            if entry.event_type not in event_types or entry.symbol is None:
                continue
            values[self._symbol(entry.symbol)] += self._notional(entry)
        return {symbol: round(value, 2) for symbol, value in values.items()}

    def _entry_notional_sum(
        self,
        entries: Sequence[LedgerEntry],
        event_types: set[LedgerEventType] | None = None,
    ) -> float:
        return round(
            sum(
                self._notional(entry)
                for entry in entries
                if event_types is None or entry.event_type in event_types
            ),
            2,
        )

    def _notional(self, entry: LedgerEntry) -> float:
        if entry.notional is not None:
            return round(float(entry.notional), 2)
        if entry.quantity is not None and entry.price is not None:
            return round(float(entry.quantity) * float(entry.price), 2)
        return 0.0

    def _touched_symbols(
        self,
        *,
        entries: Sequence[LedgerEntry],
        signals: Sequence[Signal],
        orders: Sequence[OrderPlan],
        latest_plan: PortfolioPlan | None,
    ) -> list[str]:
        symbols = {
            self._symbol(entry.symbol)
            for entry in entries
            if entry.symbol is not None
        }
        symbols.update(self._symbol(signal.symbol) for signal in signals)
        symbols.update(self._symbol(order.intent.symbol) for order in orders)
        if latest_plan is not None:
            symbols.update(self._symbol(symbol) for symbol in latest_plan.target_weights)
        return sorted(symbols)

    def _target_weights(self, latest_plan: PortfolioPlan | None) -> dict[str, float]:
        if latest_plan is None:
            return {}
        return {
            self._symbol(symbol): weight
            for symbol, weight in latest_plan.target_weights.items()
        }

    def _sector_by_symbol(self, snapshot: PortfolioSnapshot | None) -> dict[str, str]:
        if snapshot is None:
            return {}
        return {
            self._symbol(position.symbol): position.sector
            for position in snapshot.positions
        }

    def _current_weight_by_sector(self, snapshot: PortfolioSnapshot | None) -> dict[str, float]:
        if snapshot is None:
            return {}
        weights: dict[str, float] = defaultdict(float)
        for position in snapshot.positions:
            weights[position.sector] += position.market_value / snapshot.equity
        return {sector: round(weight, 6) for sector, weight in weights.items()}

    def _risk_reasons_by_key(
        self,
        batch_risk_decisions: Sequence[BatchRiskDecision],
    ) -> dict[str, list[str]]:
        reasons_by_key: dict[str, list[str]] = defaultdict(list)
        for decision in batch_risk_decisions:
            fallback = self._unique([*decision.failed_checks, *decision.stale_input_reasons])
            for key, reasons in decision.rejected_reasons.items():
                reasons_by_key[key].extend(reasons or fallback)
            for order_id in decision.rejected_order_plan_ids:
                if order_id not in reasons_by_key:
                    reasons_by_key[order_id].extend(fallback)
        return {
            key: self._unique(reasons)
            for key, reasons in reasons_by_key.items()
        }

    def _position_status(
        self,
        *,
        key: str,
        intended: float,
        filled: float,
        rejected: float,
        order: OrderPlan | None,
        trimmed_keys: set[str],
        rejected_keys: set[str],
    ) -> str:
        if key in trimmed_keys:
            return "trimmed"
        if rejected > 0 or key in rejected_keys or (order is not None and order.blocked_reason):
            return "rejected"
        if filled > 0 and intended > filled:
            return "partial_fill"
        if filled > 0:
            return "filled"
        return "intent"

    def _position_explanation(
        self,
        *,
        symbol: str,
        status: str,
        intended: float,
        filled: float,
        rejected: float,
        risk_reasons: Sequence[str],
    ) -> str:
        explanation = (
            f"{symbol} position status {status}: intended {intended:.2f}, "
            f"filled {filled:.2f}, rejected {rejected:.2f}."
        )
        if risk_reasons:
            explanation = f"{explanation} Risk reasons: {', '.join(risk_reasons)}."
        return explanation

    def _entry_key(self, entry: LedgerEntry) -> str:
        return entry.order_plan_id or entry.intent_id or entry.ledger_entry_id

    def _entries_by_key(self, entries: Sequence[LedgerEntry]) -> dict[str, list[LedgerEntry]]:
        entries_by_key: dict[str, list[LedgerEntry]] = defaultdict(list)
        for entry in entries:
            entries_by_key[self._entry_key(entry)].append(entry)
        return entries_by_key

    def _symbol(self, value: str) -> str:
        return symbol_key(value)

    def _unique(self, values: Iterable[str | None]) -> list[str]:
        return unique_text(values)

    def _first_not_none(self, values: Iterable[str | None]) -> str | None:
        return first_not_none(values)

    def _order_plan_id(self, entries: Sequence[LedgerEntry]) -> str | None:
        return self._first_not_none(entry.order_plan_id for entry in entries)

    def _intent_id(self, entries: Sequence[LedgerEntry]) -> str | None:
        return self._first_not_none(entry.intent_id for entry in entries)

    def _side(self, entries: Sequence[LedgerEntry]) -> str | None:
        return self._first_not_none(entry.side for entry in entries)

    def _symbol_from_entries(self, entries: Sequence[LedgerEntry]) -> str | None:
        symbol = self._first_not_none(entry.symbol for entry in entries)
        return self._symbol(symbol) if symbol else None

    def _target_weight(self, entries: Sequence[LedgerEntry]) -> float | None:
        for entry in entries:
            value = entry.metadata.get("target_weight")
            if isinstance(value, int | float):
                return float(value)
        return None
