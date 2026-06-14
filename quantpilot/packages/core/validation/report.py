from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from quantpilot.packages.core.validation.types import (
    BenchmarkRelativeAttribution,
    DataModeLabel,
    DiagnosticPlaceholder,
    ExtensionPointStatus,
    PromotionEvidenceReport,
    SlippageSensitivityResult,
    ValidationRunResult,
)


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _infer_strategy_id(validation_runs: list[ValidationRunResult], fallback: str | None) -> str:
    if fallback:
        return fallback
    for run in validation_runs:
        if run.backtest_result is not None:
            return run.backtest_result.strategy_id
    return "unknown_strategy"


def _infer_recipe_version(validation_runs: list[ValidationRunResult], fallback: str | None) -> str:
    if fallback:
        return fallback
    for run in validation_runs:
        if run.backtest_result is not None:
            return run.backtest_result.recipe_version
    return "unknown_version"


def build_promotion_evidence_report(
    *,
    validation_runs: list[ValidationRunResult],
    slippage_sensitivity: SlippageSensitivityResult | None = None,
    benchmark_attribution: BenchmarkRelativeAttribution | None = None,
    strategy_id: str | None = None,
    recipe_version: str | None = None,
    data_mode: DataModeLabel = "fixture",
) -> PromotionEvidenceReport:
    inferred_strategy_id = _infer_strategy_id(validation_runs, strategy_id)
    inferred_recipe_version = _infer_recipe_version(validation_runs, recipe_version)
    survivorship_status = ExtensionPointStatus(
        name="survivorship_bias",
        status="not_configured",
        implemented=False,
        required_for_promotion=True,
        detail="Survivorship-bias controls are an explicit extension point and are not configured in fixture validation.",
    )
    corporate_action_status = ExtensionPointStatus(
        name="corporate_actions",
        status="not_configured",
        implemented=False,
        required_for_promotion=True,
        detail="Corporate-action adjustments are an explicit extension point and are not integrated in fixture validation.",
    )
    pbo = DiagnosticPlaceholder(
        name="pbo",
        detail="PBO is schema-only in this stage; full probability-of-backtest-overfitting calculation is not implemented.",
    )
    dsr = DiagnosticPlaceholder(
        name="dsr",
        detail="DSR is schema-only in this stage; full deflated-Sharpe-ratio calculation is not implemented.",
    )

    blockers = [
        "deterministic_validation_cannot_promote",
        "human_review_required",
        "survivorship_bias_review_not_configured",
        "corporate_action_review_not_configured",
        "pbo_placeholder_not_implemented",
        "dsr_placeholder_not_implemented",
    ]
    if not any(run.status == "completed" for run in validation_runs):
        blockers.append("walk_forward_validation_unavailable")
    if slippage_sensitivity is None or slippage_sensitivity.status != "completed":
        blockers.append("slippage_sensitivity_incomplete")
    if benchmark_attribution is None or benchmark_attribution.status != "completed":
        blockers.append("benchmark_relative_attribution_unavailable")

    warnings = sorted(
        set(
            [
                "promotion_allowed_false_by_design",
                "live_trading_approval_false_by_design",
                "deterministic_backtest_is_research_only",
            ]
            + [warning for run in validation_runs for warning in run.warnings]
            + (slippage_sensitivity.warnings if slippage_sensitivity is not None else [])
            + (benchmark_attribution.warnings if benchmark_attribution is not None else [])
        )
    )

    payload = {
        "strategy_id": inferred_strategy_id,
        "recipe_version": inferred_recipe_version,
        "data_mode": data_mode,
        "validation_runs": validation_runs,
        "slippage_sensitivity": slippage_sensitivity,
        "benchmark_attribution": benchmark_attribution,
        "blockers": blockers,
        "warnings": warnings,
    }
    return PromotionEvidenceReport(
        report_id=f"pe_{_stable_hash(payload)[:24]}",
        strategy_id=inferred_strategy_id,
        recipe_version=inferred_recipe_version,
        data_mode=data_mode,
        validation_runs=validation_runs,
        slippage_sensitivity=slippage_sensitivity,
        benchmark_attribution=benchmark_attribution,
        survivorship_status=survivorship_status,
        corporate_action_status=corporate_action_status,
        pbo=pbo,
        dsr=dsr,
        blockers=blockers,
        warnings=warnings,
    )
