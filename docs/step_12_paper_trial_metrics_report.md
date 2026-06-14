# Step 12 Paper Trial Metrics Report

## Implemented

- Added serializable paper trial metric contracts:
  - `PaperTrialMetrics`
  - `ExecutionQualityMetrics`
  - `RejectedReasonSummary`
  - `RiskBudgetUsage`
- Added `PaperTrialMetricsCalculator` for ledger-based paper/mock execution metrics.
- Integrated optional paper trial metrics into `OperationReport.summary["paper_trial_metrics"]`.
- Preserved existing report fields and call signatures.

## Metrics Covered

- Turnover notional and turnover weight.
- Intended-fill ratio and submitted-fill ratio.
- Side-aware weighted slippage estimate in basis points.
- Exposure drift versus target weights when a snapshot and target weights are available.
- Cash drag versus target cash weight when available.
- Rejected reason counts from ledger reject events.
- Risk budget usage for daily turnover and single-order notional limits.
- Batch risk failed-check counts when batch risk audit events are available.
- Signal-to-fill latency when signal timestamps are available.
- Unavailable-safe output when ledger entries are missing.

## Data Assumptions

- Metrics are calculated from reconciliation ledger entries and optional local context already present in repositories.
- Report integration scopes ledger entries to the requested policy.
- Report integration uses the latest portfolio plan for target weights and the fixture snapshot because snapshots are not currently persisted in repositories.
- Missing optional context leaves related metrics as `null`; missing ledger entries return `status="unavailable"` with `unavailable_reason="missing_ledger"`.

## Safety Invariants Preserved

- `LIVE_TRADING_ENABLED=false`.
- `BROKER_MODE=mock` remains the validation default.
- Market orders remain disabled by default.
- No live broker connector, credential UI, credential storage, account IDs, or live order path was added.
- Metrics are read-only and do not submit, approve, modify, or reconcile orders.
- Tests use fixture/mock/paper-safe paths only and do not require internet access.

## Validation

- `python -m pytest quantpilot/tests/core/reports -q`
  - `4 passed`
- `python -m pytest quantpilot/tests`
  - Initial run hit a Windows temp-directory permission error at `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan`.
  - Reran with `TMP` and `TEMP` pointed to a workspace-local temp directory.
  - `286 passed, 1 skipped`
- `python -m quantpilot.jobs.run_smoke`
  - Passed.
  - Broker: `mock`.
  - Live trading enabled: `false`.
  - Operator remained blocked by `level5_flag_disabled`.

## Known Limitations

- Exposure drift and cash drag in operation reports use the fixture snapshot until snapshots are persisted.
- Risk budget failed-check counts depend on available batch-risk audit events.
- Metrics are paper/mock execution-quality metrics only, not attribution or offline learning metrics.

## Stage Status

- Step 12 status: complete.
- Live trading enabled: no.
- Broker mode used for validation: mock.
- Stage safety invariant status: preserved.
