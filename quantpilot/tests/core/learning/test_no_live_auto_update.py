from __future__ import annotations

import pytest
from pydantic import ValidationError

from quantpilot.packages.core.learning.offline import SignalOutcomeLogger, build_offline_learning_report
from quantpilot.packages.core.learning.promotion import PromotionCandidateBuilder
from quantpilot.packages.core.learning.types import PromotionCandidate
from quantpilot.packages.core.ledger.types import LedgerEventType
from quantpilot.packages.core.schemas import SignalAction

from quantpilot.tests.core.learning.test_signal_outcome_logging import _calibrated_signal, _entry


def test_non_mock_or_paper_sources_are_rejected() -> None:
    unsafe_entry = _entry("AAA", LedgerEventType.order_intent).model_copy(
        update={"source": "live", "data_mode": "live_trading"}
    )

    with pytest.raises(ValueError, match="mock/paper"):
        SignalOutcomeLogger().build_from_calibrated_signal_set(
            calibrated_signals=[_calibrated_signal("AAA", action=SignalAction.buy_ready)],
            ledger_entries=[unsafe_entry],
        )


def test_promotion_candidate_rejects_live_auto_update() -> None:
    with pytest.raises(ValidationError):
        PromotionCandidate(
            dataset_id="cds_fixture",
            record_count=1,
            status="pending_review",
            human_review_required=True,
            live_auto_update=True,
        )


def test_empty_dataset_is_gracefully_unavailable_without_promotion_candidate() -> None:
    report = build_offline_learning_report(
        calibrated_signals=[],
        ledger_entries=[],
        paper_metrics=None,
        validation_metadata={},
    )

    assert report.status == "unavailable"
    assert report.calibration_dataset.status == "unavailable"
    assert report.calibration_dataset.records == []
    assert report.promotion_candidate is None
    assert "empty_dataset" in report.review_flags
    assert report.live_auto_update is False


def test_promotion_builder_never_applies_model_config_or_broker_updates() -> None:
    report = build_offline_learning_report(
        calibrated_signals=[_calibrated_signal("AAA", action=SignalAction.buy_ready)],
        ledger_entries=[_entry("AAA", LedgerEventType.fill)],
        paper_metrics=None,
        validation_metadata={},
    )

    candidate = PromotionCandidateBuilder().build(report.calibration_dataset)

    assert candidate.status == "pending_review"
    assert candidate.human_review_required is True
    assert candidate.model_update_allowed is False
    assert candidate.config_update_allowed is False
    assert candidate.broker_update_allowed is False
