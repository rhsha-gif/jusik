from __future__ import annotations

from typing import Any

from quantpilot.packages.core.learning.types import CalibrationDataset, PredictionOutcomeRecord, SignalOutcomeLog


FEATURE_SCHEMA = [
    "signal_id",
    "symbol",
    "predicted_action",
    "calibrated_action",
    "predicted_strength",
    "predicted_confidence",
    "predicted_expected_return",
    "predicted_risk",
    "predicted_risk_adjusted_return",
    "target_weight_hint",
    "realized_outcome",
    "realized_return",
    "intended_notional",
    "submitted_notional",
    "filled_notional",
    "rejected_notional",
    "fill_ratio",
    "paper_status",
    "paper_turnover_notional",
    "paper_fill_ratio",
    "paper_submitted_fill_ratio",
    "paper_average_slippage_bps",
    "paper_orders_rejected",
    "validation_acceptance_passed",
]


class CalibrationDatasetBuilder:
    def build(self, outcome_log: SignalOutcomeLog) -> CalibrationDataset:
        if not outcome_log.records:
            return CalibrationDataset(
                status="unavailable",
                unavailable_reason=outcome_log.unavailable_reason or "empty_dataset",
                source_log_id=outcome_log.outcome_log_id,
            )
        if outcome_log.ledger_event_count == 0:
            return CalibrationDataset(
                status="unavailable",
                unavailable_reason=outcome_log.unavailable_reason or "missing_ledger",
                source_log_id=outcome_log.outcome_log_id,
                records=outcome_log.records,
            )

        rows = [self._feature_row(record) for record in outcome_log.records]
        return CalibrationDataset(
            status="available",
            source_log_id=outcome_log.outcome_log_id,
            records=outcome_log.records,
            feature_rows=rows,
            feature_schema=list(FEATURE_SCHEMA),
        )

    def _feature_row(self, record: PredictionOutcomeRecord) -> dict[str, Any]:
        paper = record.paper_metric_features
        validation = record.validation_evidence
        validation_global = validation.get("global", {}) if isinstance(validation.get("global", {}), dict) else {}
        validation_symbol = validation.get("symbol", {}) if isinstance(validation.get("symbol", {}), dict) else {}
        return {
            "signal_id": record.signal_id,
            "symbol": record.symbol,
            "predicted_action": record.predicted_action,
            "calibrated_action": record.calibrated_action,
            "predicted_strength": record.predicted_strength,
            "predicted_confidence": record.predicted_confidence,
            "predicted_expected_return": record.predicted_expected_return,
            "predicted_risk": record.predicted_risk,
            "predicted_risk_adjusted_return": record.predicted_risk_adjusted_return,
            "target_weight_hint": record.target_weight_hint,
            "realized_outcome": record.realized_outcome,
            "realized_return": record.realized_return,
            "intended_notional": record.intended_notional,
            "submitted_notional": record.submitted_notional,
            "filled_notional": record.filled_notional,
            "rejected_notional": record.rejected_notional,
            "fill_ratio": record.fill_ratio,
            "paper_status": paper.get("status"),
            "paper_turnover_notional": paper.get("turnover_notional"),
            "paper_fill_ratio": paper.get("execution_fill_ratio"),
            "paper_submitted_fill_ratio": paper.get("execution_submitted_fill_ratio"),
            "paper_average_slippage_bps": paper.get("execution_average_slippage_bps"),
            "paper_orders_rejected": paper.get("execution_orders_rejected"),
            "validation_acceptance_passed": validation_symbol.get(
                "acceptance_passed",
                validation_global.get("acceptance_passed"),
            ),
        }
