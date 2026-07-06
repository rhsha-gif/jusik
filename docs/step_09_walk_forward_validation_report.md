# Step 09 Walk-Forward Validation Report

## Summary

Implemented an additive validation evidence layer so deterministic backtest results
are not treated as direct promotion approval. The new package builds walk-forward
splits with purge/embargo metadata, runs fixture-safe validation windows, sweeps
slippage assumptions, compares strategy returns against a benchmark series, and
produces a conservative `PromotionEvidenceReport`.

The report is evidence only: `promotion_allowed=false`,
`human_review_required=true`, `research_only=true`, and
`live_trading_approval=false`.

## Files Added

- `quantpilot/packages/core/validation/__init__.py`
- `quantpilot/packages/core/validation/types.py`
- `quantpilot/packages/core/validation/walk_forward.py`
- `quantpilot/packages/core/validation/report.py`
- `quantpilot/tests/core/validation/test_walk_forward.py`
- `quantpilot/tests/core/validation/test_slippage_sensitivity.py`
- `quantpilot/tests/core/validation/test_promotion_evidence_report.py`
- `docs/step_09_walk_forward_validation_report.md`

## Implemented Features

- `WalkForwardSplit` with `PurgeEmbargoMetadata`.
- `ValidationRunResult` for completed or fail-closed unavailable validation runs.
- `SlippageSensitivityResult` and scenario-level summaries.
- `BenchmarkRelativeAttribution` over aligned benchmark dates.
- `ExtensionPointStatus` for survivorship-bias and corporate-action review.
- `DiagnosticPlaceholder` schema for PBO and DSR.
- `PromotionEvidenceReport` with deterministic report IDs and JSON serialization.
- Deterministic-only promotion blocker with `promotion_allowed=false`.

## Safety Impact

- Live trading remains disabled by default.
- Broker mode remains mock for validation.
- Market orders remain disabled by default.
- No broker credential UI, broker credential storage, real broker integration, or
  live order submission path was added.
- Validation uses fixture/injected historical data and the existing deterministic
  research-only backtest engine.

## Test Results

- `python -m pytest quantpilot/tests/core/validation -q`
  - Passed: `5 passed`
- `python -m pytest quantpilot/tests/unit/test_backtest_engine.py -q`
  - Passed: `9 passed`
- `python -m pytest quantpilot/tests -q`
  - Environment failure: Windows `PermissionError` scanning
    `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan`.
  - This is the known pytest temp-directory issue, not a validation failure.
- `python -m pytest quantpilot/tests -q --basetemp .pytest_tmp_step09`
  - Passed.
- `python -m quantpilot.jobs.run_smoke`
  - Passed.
  - Smoke output reported `broker="mock"`, `live_trading_enabled=false`,
    Level 5 operator `status="blocked"`, fallback `level5_flag_disabled`, and
    `submitted_order_plan_ids=[]`.

## Known Limitations

- PBO and DSR are schema placeholders only in this step.
- Survivorship-bias and corporate-action handling are explicit extension points
  and are not integrated with an external database.
- Walk-forward validation does not optimize or retrain a strategy per window; it
  evaluates the provided deterministic signal/request contract over test windows.
- No API route, frontend screen, registry promotion, or live/paper submission path
  was added.

## Stage Status

- Live trading enabled: no.
- Broker mode used for validation: mock.
- Stage safety invariant status: preserved.
- Promotion status: blocked by design for deterministic-only evidence.
- Remaining blocker for future promotion: human review plus full non-placeholder
  statistical and data-quality evidence.
