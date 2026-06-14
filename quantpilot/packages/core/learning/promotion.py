from __future__ import annotations

from collections import Counter
from statistics import mean

from quantpilot.packages.core.learning.types import CalibrationDataset, PromotionCandidate


class PromotionCandidateBuilder:
    def build(self, dataset: CalibrationDataset) -> PromotionCandidate:
        if dataset.status != "available" or not dataset.records:
            raise ValueError("promotion candidate requires an available offline calibration dataset")

        outcomes = Counter(record.realized_outcome for record in dataset.records)
        expected_returns = [
            record.predicted_expected_return
            for record in dataset.records
            if record.predicted_expected_return is not None
        ]
        realized_returns = [
            record.realized_return
            for record in dataset.records
            if record.realized_return is not None
        ]
        validation_runs = sorted(
            {
                str(record.validation_evidence.get("global", {}).get("validation_run_id"))
                for record in dataset.records
                if record.validation_evidence.get("global", {}).get("validation_run_id")
            }
        )
        return PromotionCandidate(
            dataset_id=dataset.dataset_id,
            record_count=len(dataset.records),
            evidence_summary={
                "outcome_counts": dict(sorted(outcomes.items())),
                "source": "offline_calibration_dataset",
                "mock_paper_only": True,
            },
            metrics_summary={
                "average_predicted_expected_return": round(mean(expected_returns), 6) if expected_returns else None,
                "average_realized_return": round(mean(realized_returns), 6) if realized_returns else None,
            },
            validation_summary={
                "validation_run_ids": validation_runs,
                "human_review_required": True,
                "live_auto_update": False,
            },
        )
