# Step 14 Offline Learning Loop Report

Date: 2026-06-14
Implementer: Codex
Step brief: `C:\Users\goyan\Downloads\step_14_offline_learning_loop.md`
Level 5 references: `docs/fable5_level5_implementation_spec.md` and `docs/contracts/operator_contracts.md`

## Implemented

- Added serializable offline learning contracts:
  - `SignalOutcomeLog`
  - `PredictionOutcomeRecord`
  - `CalibrationDataset`
  - `PromotionCandidate`
  - `OfflineLearningReport`
- Added `SignalOutcomeLogger` to link calibrated or legacy signal predictions with reconciliation ledger outcomes.
- Added mock/paper-only source validation for offline learning ledger inputs.
- Added calibration dataset feature rows for prediction fields, realized outcomes, paper metric features, and validation evidence metadata.
- Added review-gated promotion candidates with `human_review_required=True`, `status="pending_review"`, and `live_auto_update=False`.
- Integrated the offline learning report into the existing operation report summary and machine payload as an additive field.

## Learning Coverage

- Fill, partial fill, reject, submitted, intent, trim, and no-action outcomes are represented without submitting or mutating orders.
- Prediction rows include action, strength, confidence, expected return, risk proxy, target weight hint, realized outcome, notional outcomes, fill ratio, rejection reasons, ledger IDs, and validation evidence.
- Paper metric features include execution fill ratios, rejected order counts, turnover, risk turnover usage, and live-trading-disabled evidence.
- Missing signals or missing ledger inputs return unavailable-safe datasets instead of raising during report generation.
- Non mock/paper ledger sources are rejected before dataset generation.

## Files Changed

- Added `quantpilot/packages/core/learning/`.
- Updated `quantpilot/packages/core/reports/service.py`.
- Added learning tests under `quantpilot/tests/core/learning/`.
- Updated `quantpilot/tests/core/reports/test_report_machine_readable_payload.py`.
- Added this report.

## Data Assumptions

- Learning data is derived only from in-memory fixture/mock/paper-safe evidence already present in repositories.
- Report integration uses legacy persisted signals because calibrated signal sets are not currently persisted by `HarnessService.run_signals()`.
- Validation evidence is accepted as optional metadata and is copied into records; missing evidence leaves related fields null.
- Promotion candidates are advisory review packets only and cannot approve, promote, retrain, update configs, or change broker state.

## Safety Invariants Preserved

- `LIVE_TRADING_ENABLED=false`.
- `BROKER_MODE=mock` remains the validation default.
- `GUARDED_AUTOPILOT_ENABLED=false` default preserved.
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false` default preserved.
- `MARKET_ORDERS_ENABLED=false` default preserved.
- No broker credentials, credential UI, account IDs, live broker connector, live order transport, or market-order enablement was added.
- Offline learning is read-only and does not submit, approve, modify, reconcile, retrain, promote, or update broker/config/model state.
- Tests use fixture/mock/paper-safe paths only and do not require internet access.

## Validation

- `python -m pytest quantpilot/tests/core/learning quantpilot/tests/core/reports -q`
  - Result: `17 passed`
- `python -m pytest quantpilot/tests`
  - Initial run hit the known Windows temp-directory permission issue at `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan`.
  - Before the temp error: `298 passed, 1 skipped, 1 error`.
- `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
  - Result: `299 passed, 1 skipped`
- `python -m pytest quantpilot/tests/core/learning/test_no_live_auto_update.py -q`
  - Result: `4 passed`
- `python -m quantpilot.jobs.run_smoke`
  - Result: passed
  - Broker: `mock`
  - Execution mode: `approval_required`
  - Live trading enabled: `false`
  - Operator fallback: `level5_flag_disabled`

## Known Limitations

- Calibrated signal sets are supported by the learning builders, but the existing harness report integration currently uses legacy persisted signals because calibrated sets are not persisted.
- Realized returns require validation metadata; ledger-only records leave `realized_return` null.
- The promotion candidate is a human-review packet only; there is no automated promotion workflow in this step.

## Stage Status

- Step 14 status: complete.
- Live trading enabled: no.
- Broker mode used for validation: mock.
- Stage safety invariant status: preserved.
