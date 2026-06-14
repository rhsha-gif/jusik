from __future__ import annotations

from quantpilot.packages.core.learning.offline import build_offline_learning_report
from quantpilot.packages.core.ledger.types import LedgerEventType
from quantpilot.packages.core.reports.paper_metrics import PaperTrialMetricsCalculator
from quantpilot.packages.core.schemas import SignalAction

from quantpilot.tests.core.learning.test_signal_outcome_logging import _calibrated_signal, _entry


def test_promotion_candidate_requires_human_review_and_pending_review_status() -> None:
    signal = _calibrated_signal("AAA", action=SignalAction.buy_ready)
    entries = [
        _entry("AAA", LedgerEventType.order_intent, notional=1_000),
        _entry("AAA", LedgerEventType.fill, notional=1_000),
    ]
    paper_metrics = PaperTrialMetricsCalculator().calculate(ledger_entries=entries)

    report = build_offline_learning_report(
        calibrated_signals=[signal],
        ledger_entries=entries,
        paper_metrics=paper_metrics,
        validation_metadata={"validation_run_id": "validation_fixture"},
    )

    candidate = report.promotion_candidate
    assert candidate is not None
    assert candidate.status == "pending_review"
    assert candidate.human_review_required is True
    assert candidate.live_auto_update is False
    assert candidate.model_update_allowed is False
    assert candidate.config_update_allowed is False
    assert candidate.broker_update_allowed is False
    assert candidate.record_count == 1
    assert report.model_update_applied is False
    assert report.config_update_applied is False
    assert report.broker_update_applied is False
