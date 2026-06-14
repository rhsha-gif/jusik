from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Mapping, Sequence

from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType
from quantpilot.packages.core.reports.metrics_types import (
    ExecutionQualityMetrics,
    PaperTrialMetrics,
    RejectedReasonSummary,
    RiskBudgetUsage,
)
from quantpilot.packages.core.risk.types import BatchRiskDecision
from quantpilot.packages.core.schemas import PortfolioSnapshot


class PaperTrialMetricsCalculator:
    def calculate(
        self,
        *,
        ledger_entries: Sequence[LedgerEntry] | None,
        target_weights: Mapping[str, float] | None = None,
        target_cash_weight: float | None = None,
        snapshot: PortfolioSnapshot | None = None,
        batch_risk_decisions: Sequence[BatchRiskDecision] | None = None,
        signal_timestamps: Mapping[str, datetime] | None = None,
        max_daily_turnover: float | None = None,
        single_order_cash_limit: float | None = None,
    ) -> PaperTrialMetrics:
        entries = list(ledger_entries or [])
        if not entries:
            return self._unavailable("missing_ledger")

        intents = self._entries_by_type(entries, LedgerEventType.order_intent)
        submitted = self._entries_by_type(entries, LedgerEventType.submitted)
        fills = [
            entry
            for entry in entries
            if entry.event_type in {LedgerEventType.fill, LedgerEventType.partial_fill}
        ]
        rejects = self._entries_by_type(entries, LedgerEventType.reject)

        intended_notional = self._notional_sum(intents) or self._notional_sum(submitted)
        submitted_notional = self._notional_sum(submitted)
        filled_notional = self._notional_sum(fills)
        turnover_notional = filled_notional

        execution_quality = ExecutionQualityMetrics(
            orders_intended=len(self._order_ids(intents)),
            orders_submitted=len(self._order_ids(submitted)),
            orders_filled=len(self._order_ids(fills)),
            orders_rejected=len(self._order_ids(rejects)),
            intended_notional=round(intended_notional, 2),
            submitted_notional=round(submitted_notional, 2),
            filled_notional=round(filled_notional, 2),
            fill_ratio=self._ratio(filled_notional, intended_notional),
            submitted_fill_ratio=self._ratio(filled_notional, submitted_notional),
            average_slippage_bps=self._average_slippage_bps(fills=fills, intents=intents, submitted=submitted),
            **self._latency_fields(fills=fills, signal_timestamps=signal_timestamps or {}),
        )

        return PaperTrialMetrics(
            status="available",
            ledger_event_count=len(entries),
            ledger_sources=sorted({entry.source for entry in entries}),
            data_modes=sorted({entry.data_mode for entry in entries}),
            turnover_notional=round(turnover_notional, 2),
            turnover_weight=self._ratio(turnover_notional, snapshot.equity if snapshot else None),
            exposure_drift=self._exposure_drift(
                fills=fills,
                snapshot=snapshot,
                target_weights=target_weights,
            ),
            cash_drag=self._cash_drag(
                fills=fills,
                snapshot=snapshot,
                target_cash_weight=target_cash_weight,
            ),
            execution_quality=execution_quality,
            rejected_reasons=self._rejected_reasons(rejects),
            risk_budget_usage=self._risk_budget_usage(
                intended_notional=intended_notional,
                intents=intents,
                batch_risk_decisions=batch_risk_decisions or [],
                max_daily_turnover=max_daily_turnover,
                single_order_cash_limit=single_order_cash_limit,
            ),
            live_trading_enabled=False,
        )

    def _unavailable(self, reason: str) -> PaperTrialMetrics:
        return PaperTrialMetrics(
            status="unavailable",
            unavailable_reason=reason,
            live_trading_enabled=False,
        )

    def _entries_by_type(self, entries: Sequence[LedgerEntry], event_type: LedgerEventType) -> list[LedgerEntry]:
        return [entry for entry in entries if entry.event_type == event_type]

    def _order_ids(self, entries: Sequence[LedgerEntry]) -> set[str]:
        return {entry.order_plan_id or entry.intent_id or entry.ledger_entry_id for entry in entries}

    def _notional(self, entry: LedgerEntry) -> float:
        if entry.notional is not None:
            return float(entry.notional)
        if entry.quantity is not None and entry.price is not None:
            return round(float(entry.quantity) * float(entry.price), 2)
        return 0.0

    def _notional_sum(self, entries: Sequence[LedgerEntry]) -> float:
        return round(sum(self._notional(entry) for entry in entries), 2)

    def _ratio(self, numerator: float, denominator: float | None) -> float | None:
        if denominator is None or denominator <= 0:
            return None
        return round(numerator / denominator, 6)

    def _entry_by_order(self, entries: Sequence[LedgerEntry]) -> dict[str, LedgerEntry]:
        by_order: dict[str, LedgerEntry] = {}
        for entry in entries:
            if entry.order_plan_id and entry.order_plan_id not in by_order:
                by_order[entry.order_plan_id] = entry
        return by_order

    def _average_slippage_bps(
        self,
        *,
        fills: Sequence[LedgerEntry],
        intents: Sequence[LedgerEntry],
        submitted: Sequence[LedgerEntry],
    ) -> float | None:
        reference_by_order = {**self._entry_by_order(submitted), **self._entry_by_order(intents)}
        weighted_bps = 0.0
        weight = 0.0
        for fill in fills:
            if fill.order_plan_id is None or fill.price is None:
                continue
            reference = reference_by_order.get(fill.order_plan_id)
            if reference is None or reference.price is None or reference.price <= 0:
                continue
            side = fill.side or reference.side
            if side == "buy":
                bps = (fill.price - reference.price) / reference.price * 10_000
            elif side == "sell":
                bps = (reference.price - fill.price) / reference.price * 10_000
            else:
                continue
            fill_notional = self._notional(fill)
            weighted_bps += bps * fill_notional
            weight += fill_notional
        if weight <= 0:
            return None
        return round(weighted_bps / weight, 6)

    def _latency_fields(
        self,
        *,
        fills: Sequence[LedgerEntry],
        signal_timestamps: Mapping[str, datetime],
    ) -> dict[str, object]:
        latencies: list[float] = []
        for fill in fills:
            started_at = self._signal_timestamp_for_fill(fill, signal_timestamps)
            if started_at is None:
                continue
            latency = (fill.occurred_at - started_at).total_seconds()
            if latency >= 0:
                latencies.append(latency)
        if not latencies:
            return {
                "signal_to_fill_latency_seconds": None,
                "latency_status": "unavailable",
                "latency_sample_count": 0,
            }
        return {
            "signal_to_fill_latency_seconds": round(sum(latencies) / len(latencies), 6),
            "latency_status": "available",
            "latency_sample_count": len(latencies),
        }

    def _signal_timestamp_for_fill(
        self,
        fill: LedgerEntry,
        signal_timestamps: Mapping[str, datetime],
    ) -> datetime | None:
        for key in (fill.order_plan_id, fill.intent_id, fill.symbol):
            if key is not None and key in signal_timestamps:
                return signal_timestamps[key]
        return None

    def _position_values(self, snapshot: PortfolioSnapshot) -> dict[str, float]:
        values: dict[str, float] = {}
        for position in snapshot.positions:
            symbol = self._symbol(position.symbol)
            values[symbol] = round(values.get(symbol, 0.0) + position.market_value, 2)
        return values

    def _apply_fills(
        self,
        *,
        fills: Sequence[LedgerEntry],
        snapshot: PortfolioSnapshot,
    ) -> tuple[dict[str, float], float]:
        values = self._position_values(snapshot)
        cash = snapshot.cash
        for fill in fills:
            if fill.symbol is None:
                continue
            symbol = self._symbol(fill.symbol)
            notional = self._notional(fill)
            if fill.side == "buy":
                values[symbol] = round(values.get(symbol, 0.0) + notional, 2)
                cash = round(cash - notional, 2)
            elif fill.side == "sell":
                values[symbol] = round(max(0.0, values.get(symbol, 0.0) - notional), 2)
                cash = round(cash + notional, 2)
        return values, cash

    def _exposure_drift(
        self,
        *,
        fills: Sequence[LedgerEntry],
        snapshot: PortfolioSnapshot | None,
        target_weights: Mapping[str, float] | None,
    ) -> float | None:
        if snapshot is None or not target_weights:
            return None
        values, _ = self._apply_fills(fills=fills, snapshot=snapshot)
        drift = 0.0
        for symbol, target_weight in target_weights.items():
            actual_weight = values.get(self._symbol(symbol), 0.0) / snapshot.equity
            drift += abs(actual_weight - target_weight)
        return round(drift, 6)

    def _cash_drag(
        self,
        *,
        fills: Sequence[LedgerEntry],
        snapshot: PortfolioSnapshot | None,
        target_cash_weight: float | None,
    ) -> float | None:
        if snapshot is None or target_cash_weight is None:
            return None
        _, cash = self._apply_fills(fills=fills, snapshot=snapshot)
        cash_weight = cash / snapshot.equity
        return round(max(0.0, cash_weight - target_cash_weight), 6)

    def _rejected_reasons(self, rejects: Sequence[LedgerEntry]) -> RejectedReasonSummary:
        reasons = Counter()
        for entry in rejects:
            reason = str(entry.metadata.get("reason") or "unknown")
            reasons[reason] += 1
        return RejectedReasonSummary(
            rejected_count=len(rejects),
            reasons=dict(sorted(reasons.items())),
        )

    def _risk_budget_usage(
        self,
        *,
        intended_notional: float,
        intents: Sequence[LedgerEntry],
        batch_risk_decisions: Sequence[BatchRiskDecision],
        max_daily_turnover: float | None,
        single_order_cash_limit: float | None,
    ) -> RiskBudgetUsage:
        failed_check_counts = Counter()
        for decision in batch_risk_decisions:
            checks = set(decision.failed_checks)
            for reasons in decision.rejected_reasons.values():
                checks.update(reasons)
            failed_check_counts.update(checks)

        largest_order_notional = max((self._notional(entry) for entry in intents), default=0.0)
        return RiskBudgetUsage(
            turnover_used=round(intended_notional, 2),
            max_daily_turnover=max_daily_turnover,
            daily_turnover_usage=self._ratio(intended_notional, max_daily_turnover),
            largest_order_notional=round(largest_order_notional, 2),
            single_order_cash_limit=single_order_cash_limit,
            largest_order_usage=self._ratio(largest_order_notional, single_order_cash_limit),
            failed_check_counts=dict(sorted(failed_check_counts.items())),
        )

    def _symbol(self, value: str) -> str:
        return value.strip().upper()
