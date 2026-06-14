from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence, cast

from quantpilot.packages.core.learning.datasets import CalibrationDatasetBuilder
from quantpilot.packages.core.learning.promotion import PromotionCandidateBuilder
from quantpilot.packages.core.learning.types import (
    CalibrationDataset,
    LearningDataMode,
    LearningSource,
    OfflineLearningReport,
    OutcomeStatus,
    PredictionOutcomeRecord,
    SignalOutcomeLog,
)
from quantpilot.packages.core.ledger.types import LedgerEntry, LedgerEventType
from quantpilot.packages.core.normalization import first_not_none, symbol_key, unique_text
from quantpilot.packages.core.reports.metrics_types import PaperTrialMetrics
from quantpilot.packages.core.schemas import Signal, SignalAction
from quantpilot.packages.core.signals.types import CalibratedSignal, CalibratedSignalSet


SAFE_LEDGER_SOURCES = {"mock", "paper"}
SAFE_LEDGER_DATA_MODES = {"fixture", "paper_trading"}


def validate_mock_paper_sources(ledger_entries: Sequence[LedgerEntry]) -> None:
    for entry in ledger_entries:
        source = str(entry.source)
        data_mode = str(entry.data_mode)
        if source not in SAFE_LEDGER_SOURCES or data_mode not in SAFE_LEDGER_DATA_MODES:
            raise ValueError("offline learning supports mock/paper ledger sources only")


class SignalOutcomeLogger:
    def build_from_calibrated_signal_set(
        self,
        *,
        calibrated_signal_set: CalibratedSignalSet | None = None,
        calibrated_signals: Sequence[CalibratedSignal] | None = None,
        ledger_entries: Sequence[LedgerEntry],
        paper_metrics: PaperTrialMetrics | None = None,
        validation_metadata: Mapping[str, Any] | None = None,
    ) -> SignalOutcomeLog:
        selected_signals = list(calibrated_signals if calibrated_signals is not None else (calibrated_signal_set.signals if calibrated_signal_set else []))
        entries = list(ledger_entries)
        validate_mock_paper_sources(entries)
        entries_by_symbol = self._entries_by_symbol(entries)
        paper_features = self._paper_metric_features(paper_metrics)
        records = [
            self._record_from_calibrated_signal(
                signal=signal,
                ledger_entries=entries_by_symbol.get(self._symbol(signal.symbol), []),
                paper_metric_features=paper_features,
                validation_metadata=validation_metadata or {},
            )
            for signal in selected_signals
        ]
        return self._log(records=records, ledger_entries=entries)

    def build_from_signals(
        self,
        *,
        signals: Sequence[Signal],
        ledger_entries: Sequence[LedgerEntry],
        paper_metrics: PaperTrialMetrics | None = None,
        validation_metadata: Mapping[str, Any] | None = None,
    ) -> SignalOutcomeLog:
        entries = list(ledger_entries)
        validate_mock_paper_sources(entries)
        entries_by_symbol = self._entries_by_symbol(entries)
        paper_features = self._paper_metric_features(paper_metrics)
        records = [
            self._record_from_signal(
                signal=signal,
                ledger_entries=entries_by_symbol.get(self._symbol(signal.symbol), []),
                paper_metric_features=paper_features,
                validation_metadata=validation_metadata or {},
            )
            for signal in signals
        ]
        return self._log(records=records, ledger_entries=entries)

    def _log(self, *, records: list[PredictionOutcomeRecord], ledger_entries: Sequence[LedgerEntry]) -> SignalOutcomeLog:
        data_modes = self._safe_data_modes(ledger_entries)
        if not records:
            status = "unavailable"
            unavailable_reason = "empty_signal_input"
        elif not ledger_entries:
            status = "unavailable"
            unavailable_reason = "missing_ledger"
        else:
            status = "available"
            unavailable_reason = None
        return SignalOutcomeLog(
            status=status,
            unavailable_reason=unavailable_reason,
            records=records,
            ledger_event_count=len(ledger_entries),
            data_modes=data_modes,
        )

    def _record_from_calibrated_signal(
        self,
        *,
        signal: CalibratedSignal,
        ledger_entries: Sequence[LedgerEntry],
        paper_metric_features: dict[str, Any],
        validation_metadata: Mapping[str, Any],
    ) -> PredictionOutcomeRecord:
        proxy = signal.expected_return_risk
        evidence = self._validation_evidence(validation_metadata, signal.symbol)
        return self._record(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            strategy_id=None,
            recipe_version=None,
            predicted_action=signal.calibrated_action.value,
            calibrated_action=signal.calibrated_action.value,
            prediction_source=signal.source,
            predicted_strength=signal.strength,
            predicted_confidence=signal.confidence,
            predicted_expected_return=proxy.expected_return,
            predicted_risk=proxy.risk,
            predicted_risk_adjusted_return=proxy.risk_adjusted_return,
            target_weight_hint=signal.target_weight_hint,
            reason_codes=signal.reason_codes,
            signal_generated_at=signal.generated_at,
            ledger_entries=ledger_entries,
            paper_metric_features=paper_metric_features,
            validation_evidence=evidence,
        )

    def _record_from_signal(
        self,
        *,
        signal: Signal,
        ledger_entries: Sequence[LedgerEntry],
        paper_metric_features: dict[str, Any],
        validation_metadata: Mapping[str, Any],
    ) -> PredictionOutcomeRecord:
        evidence = self._validation_evidence(validation_metadata, signal.symbol)
        return self._record(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            strategy_id=signal.strategy_id,
            recipe_version=signal.recipe_version,
            predicted_action=signal.action.value,
            calibrated_action=None,
            prediction_source=signal.source,
            predicted_strength=signal.strength,
            predicted_confidence=None,
            predicted_expected_return=None,
            predicted_risk=None,
            predicted_risk_adjusted_return=None,
            target_weight_hint=signal.target_weight_hint,
            reason_codes=signal.reason_codes,
            signal_generated_at=signal.generated_at,
            ledger_entries=ledger_entries,
            paper_metric_features=paper_metric_features,
            validation_evidence=evidence,
        )

    def _record(
        self,
        *,
        signal_id: str | None,
        symbol: str,
        strategy_id: str | None,
        recipe_version: str | None,
        predicted_action: str,
        calibrated_action: str | None,
        prediction_source: str,
        predicted_strength: float | None,
        predicted_confidence: float | None,
        predicted_expected_return: float | None,
        predicted_risk: float | None,
        predicted_risk_adjusted_return: float | None,
        target_weight_hint: float | None,
        reason_codes: Sequence[str],
        signal_generated_at: datetime | None,
        ledger_entries: Sequence[LedgerEntry],
        paper_metric_features: dict[str, Any],
        validation_evidence: dict[str, Any],
    ) -> PredictionOutcomeRecord:
        intended_notional = self._notional_sum(ledger_entries, {LedgerEventType.order_intent})
        submitted_notional = self._notional_sum(ledger_entries, {LedgerEventType.submitted})
        filled_notional = self._notional_sum(ledger_entries, {LedgerEventType.fill, LedgerEventType.partial_fill})
        rejected_notional = self._notional_sum(ledger_entries, {LedgerEventType.reject})
        fill_ratio = round(filled_notional / intended_notional, 6) if intended_notional > 0 else None
        return PredictionOutcomeRecord(
            signal_id=signal_id,
            symbol=self._symbol(symbol),
            strategy_id=strategy_id,
            recipe_version=recipe_version,
            predicted_action=predicted_action,
            calibrated_action=calibrated_action,
            prediction_source=prediction_source,
            predicted_strength=predicted_strength,
            predicted_confidence=predicted_confidence,
            predicted_expected_return=predicted_expected_return,
            predicted_risk=predicted_risk,
            predicted_risk_adjusted_return=predicted_risk_adjusted_return,
            target_weight_hint=target_weight_hint,
            reason_codes=self._unique(reason_codes),
            signal_generated_at=signal_generated_at,
            realized_outcome=self._outcome(predicted_action=predicted_action, ledger_entries=ledger_entries),
            realized_return=self._realized_return(validation_evidence),
            realized_side=self._first_not_none(entry.side for entry in ledger_entries),
            intended_notional=intended_notional,
            submitted_notional=submitted_notional,
            filled_notional=filled_notional,
            rejected_notional=rejected_notional,
            fill_ratio=fill_ratio,
            order_plan_ids=self._unique(entry.order_plan_id for entry in ledger_entries),
            ledger_entry_ids=self._unique(entry.ledger_entry_id for entry in ledger_entries),
            broker_order_ids=self._unique(entry.broker_order_id for entry in ledger_entries),
            fill_ids=self._unique(entry.fill_id for entry in ledger_entries),
            rejection_reasons=self._rejection_reasons(ledger_entries),
            source_modes=self._safe_sources(ledger_entries),
            data_modes=self._safe_data_modes(ledger_entries),
            paper_metric_features=paper_metric_features,
            validation_evidence=validation_evidence,
            observed_at=max((entry.occurred_at for entry in ledger_entries), default=signal_generated_at),
        )

    def _entries_by_symbol(self, entries: Sequence[LedgerEntry]) -> dict[str, list[LedgerEntry]]:
        grouped: dict[str, list[LedgerEntry]] = defaultdict(list)
        for entry in entries:
            if entry.symbol is None:
                continue
            grouped[self._symbol(entry.symbol)].append(entry)
        return grouped

    def _outcome(self, *, predicted_action: str, ledger_entries: Sequence[LedgerEntry]) -> OutcomeStatus:
        event_types = {entry.event_type for entry in ledger_entries}
        if not ledger_entries:
            return "no_action"
        if LedgerEventType.reject in event_types and not event_types.intersection({LedgerEventType.fill, LedgerEventType.partial_fill}):
            return "rejected"
        if predicted_action == SignalAction.trim.value and LedgerEventType.reject not in event_types:
            return "trimmed"
        if LedgerEventType.fill in event_types:
            return "filled"
        if LedgerEventType.partial_fill in event_types:
            return "partial_fill"
        if LedgerEventType.reject in event_types:
            return "rejected"
        if LedgerEventType.submitted in event_types:
            return "submitted"
        if LedgerEventType.order_intent in event_types:
            return "intent"
        return "no_action"

    def _notional_sum(self, entries: Sequence[LedgerEntry], event_types: set[LedgerEventType]) -> float:
        return round(sum(self._notional(entry) for entry in entries if entry.event_type in event_types), 2)

    def _notional(self, entry: LedgerEntry) -> float:
        if entry.notional is not None:
            return float(entry.notional)
        if entry.quantity is not None and entry.price is not None:
            return round(float(entry.quantity) * float(entry.price), 2)
        return 0.0

    def _paper_metric_features(self, paper_metrics: PaperTrialMetrics | None) -> dict[str, Any]:
        if paper_metrics is None:
            return {}
        execution = paper_metrics.execution_quality
        return {
            "status": paper_metrics.status,
            "turnover_notional": paper_metrics.turnover_notional,
            "turnover_weight": paper_metrics.turnover_weight,
            "execution_orders_intended": execution.orders_intended,
            "execution_orders_submitted": execution.orders_submitted,
            "execution_orders_filled": execution.orders_filled,
            "execution_orders_rejected": execution.orders_rejected,
            "execution_fill_ratio": execution.fill_ratio,
            "execution_submitted_fill_ratio": execution.submitted_fill_ratio,
            "execution_average_slippage_bps": execution.average_slippage_bps,
            "risk_turnover_used": paper_metrics.risk_budget_usage.turnover_used,
            "live_trading_enabled": paper_metrics.live_trading_enabled,
        }

    def _validation_evidence(self, metadata: Mapping[str, Any], symbol: str) -> dict[str, Any]:
        raw = metadata.model_dump(mode="json") if hasattr(metadata, "model_dump") else dict(metadata)
        symbols = raw.get("symbols", {})
        symbol_key = self._symbol(symbol)
        symbol_evidence = {}
        if isinstance(symbols, Mapping):
            symbol_evidence = symbols.get(symbol) or symbols.get(symbol_key) or {}
        global_evidence = {key: value for key, value in raw.items() if key != "symbols"}
        return {
            "global": global_evidence,
            "symbol": symbol_evidence if isinstance(symbol_evidence, dict) else {"value": symbol_evidence},
        }

    def _realized_return(self, validation_evidence: Mapping[str, Any]) -> float | None:
        symbol_evidence = validation_evidence.get("symbol", {})
        if not isinstance(symbol_evidence, Mapping):
            return None
        for key in ("realized_return", "forward_return", "actual_return"):
            value = symbol_evidence.get(key)
            if isinstance(value, int | float):
                return float(value)
        return None

    def _rejection_reasons(self, entries: Sequence[LedgerEntry]) -> list[str]:
        reasons = []
        for entry in entries:
            if entry.event_type != LedgerEventType.reject:
                continue
            reasons.append(str(entry.metadata.get("reason") or entry.order_status or "unknown"))
        return self._unique(reasons)

    def _safe_sources(self, entries: Sequence[LedgerEntry]) -> list[LearningSource]:
        return [cast(LearningSource, source) for source in sorted({entry.source for entry in entries})]

    def _safe_data_modes(self, entries: Sequence[LedgerEntry]) -> list[LearningDataMode]:
        return [cast(LearningDataMode, data_mode) for data_mode in sorted({entry.data_mode for entry in entries})]

    def _symbol(self, value: str) -> str:
        return symbol_key(value)

    def _first_not_none(self, values: Iterable[str | None]) -> str | None:
        return first_not_none(values)

    def _unique(self, values: Iterable[str | None]) -> list[str]:
        return unique_text(values)


def build_offline_learning_report(
    *,
    calibrated_signal_set: CalibratedSignalSet | None = None,
    calibrated_signals: Sequence[CalibratedSignal] | None = None,
    signals: Sequence[Signal] | None = None,
    ledger_entries: Sequence[LedgerEntry],
    paper_metrics: PaperTrialMetrics | None = None,
    validation_metadata: Mapping[str, Any] | None = None,
) -> OfflineLearningReport:
    logger = SignalOutcomeLogger()
    if calibrated_signal_set is not None or calibrated_signals is not None:
        outcome_log = logger.build_from_calibrated_signal_set(
            calibrated_signal_set=calibrated_signal_set,
            calibrated_signals=calibrated_signals,
            ledger_entries=ledger_entries,
            paper_metrics=paper_metrics,
            validation_metadata=validation_metadata or {},
        )
    else:
        outcome_log = logger.build_from_signals(
            signals=list(signals or []),
            ledger_entries=ledger_entries,
            paper_metrics=paper_metrics,
            validation_metadata=validation_metadata or {},
        )
    dataset = CalibrationDatasetBuilder().build(outcome_log)
    review_flags = _review_flags(outcome_log=outcome_log, dataset=dataset)
    candidate = None
    if dataset.status == "available" and dataset.records:
        candidate = PromotionCandidateBuilder().build(dataset)
    status = "available" if dataset.status == "available" else "unavailable"
    return OfflineLearningReport(
        status=status,
        unavailable_reason=dataset.unavailable_reason,
        signal_outcome_log=outcome_log,
        calibration_dataset=dataset,
        promotion_candidate=candidate,
        review_flags=review_flags,
        data_modes=outcome_log.data_modes,
    )


def unavailable_offline_learning_report(reason: str) -> OfflineLearningReport:
    outcome_log = SignalOutcomeLog(
        status="unavailable",
        unavailable_reason=reason,
        records=[],
        ledger_event_count=0,
    )
    dataset = CalibrationDataset(
        status="unavailable",
        unavailable_reason=reason,
        source_log_id=outcome_log.outcome_log_id,
    )
    return OfflineLearningReport(
        status="unavailable",
        unavailable_reason=reason,
        signal_outcome_log=outcome_log,
        calibration_dataset=dataset,
        review_flags=[reason],
    )


def _review_flags(*, outcome_log: SignalOutcomeLog, dataset: CalibrationDataset) -> list[str]:
    flags: list[str] = []
    if outcome_log.unavailable_reason:
        flags.append(outcome_log.unavailable_reason)
    if dataset.unavailable_reason and dataset.unavailable_reason not in flags:
        flags.append(dataset.unavailable_reason)
    if dataset.status == "unavailable" and "empty_dataset" not in flags:
        flags.append("empty_dataset")
    flags.append("human_review_required")
    flags.append("live_auto_update_forbidden")
    return flags
