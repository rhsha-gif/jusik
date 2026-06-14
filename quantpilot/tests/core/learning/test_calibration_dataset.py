from __future__ import annotations

import json

from quantpilot.packages.core.learning.offline import build_offline_learning_report
from quantpilot.packages.core.ledger.types import LedgerEventType
from quantpilot.packages.core.reports.paper_metrics import PaperTrialMetricsCalculator
from quantpilot.packages.core.schemas import SignalAction

from quantpilot.tests.core.learning.test_signal_outcome_logging import _calibrated_signal, _entry


def test_calibration_dataset_includes_prediction_outcome_metrics_and_validation_evidence() -> None:
    signal_set = _calibrated_signal("AAA", action=SignalAction.buy_ready)
    entries = [
        _entry("AAA", LedgerEventType.order_intent, notional=1_000),
        _entry("AAA", LedgerEventType.fill, notional=950),
    ]
    paper_metrics = PaperTrialMetricsCalculator().calculate(ledger_entries=entries)
    validation_metadata = {
        "validation_run_id": "validation_fixture",
        "acceptance_passed": True,
        "symbols": {
            "AAA": {
                "realized_return": 0.041,
                "forward_window": "5d",
            }
        },
    }

    report = build_offline_learning_report(
        calibrated_signals=[signal_set],
        ledger_entries=entries,
        paper_metrics=paper_metrics,
        validation_metadata=validation_metadata,
    )

    dataset = report.calibration_dataset
    row = dataset.feature_rows[0]
    record = dataset.records[0]
    assert report.status == "available"
    assert dataset.status == "available"
    assert row["signal_id"] == signal_set.signal_id
    assert row["predicted_action"] == "buy_ready"
    assert row["realized_outcome"] == "filled"
    assert row["predicted_expected_return"] == 0.035
    assert row["paper_fill_ratio"] == paper_metrics.execution_quality.fill_ratio
    assert row["validation_acceptance_passed"] is True
    assert record.validation_evidence["symbol"]["realized_return"] == 0.041
    assert record.paper_metric_features["execution_fill_ratio"] == paper_metrics.execution_quality.fill_ratio
    assert dataset.live_auto_update is False
    json.dumps(report.model_dump(mode="json"))
