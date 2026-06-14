"""Offline learning loop contracts and builders."""

from quantpilot.packages.core.learning.datasets import CalibrationDatasetBuilder
from quantpilot.packages.core.learning.offline import (
    SignalOutcomeLogger,
    build_offline_learning_report,
    unavailable_offline_learning_report,
    validate_mock_paper_sources,
)
from quantpilot.packages.core.learning.promotion import PromotionCandidateBuilder
from quantpilot.packages.core.learning.types import (
    CalibrationDataset,
    OfflineLearningReport,
    PredictionOutcomeRecord,
    PromotionCandidate,
    SignalOutcomeLog,
)

__all__ = [
    "CalibrationDataset",
    "CalibrationDatasetBuilder",
    "OfflineLearningReport",
    "PredictionOutcomeRecord",
    "PromotionCandidate",
    "PromotionCandidateBuilder",
    "SignalOutcomeLog",
    "SignalOutcomeLogger",
    "build_offline_learning_report",
    "unavailable_offline_learning_report",
    "validate_mock_paper_sources",
]
