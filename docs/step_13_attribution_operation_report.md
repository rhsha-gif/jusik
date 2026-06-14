# Step 13 Attribution-Rich Operation Report

Date: 2026-06-14
Implementer: Codex
Step brief: `C:\Users\goyan\Downloads\step_13_attribution_operation_report.md`
Level 5 references: `docs/fable5_level5_implementation_spec.md` and `docs/contracts/operator_contracts.md`

## Implemented

- Added serializable attribution report contracts:
  - `AttributionReport`
  - `AttributionOperationReport`
  - `SignalContribution`
  - `RiskBudgetAttribution`
  - `SectorAttribution`
  - `ThemeAttribution`
  - `PositionAttribution`
  - `RejectedTrimmedExplanation`
  - `ReviewFlag`
- Added `AttributionReportBuilder` for read-only report attribution from existing repository evidence.
- Added deterministic markdown rendering for operation reports.
- Integrated the rich report into the existing legacy `OperationReport.summary` without changing the public return type.
- Added machine-readable payload output under `summary["machine_payload"]`.
- Preserved the simple report fallback by returning unavailable attribution data instead of raising out of report generation.

## Attribution Coverage

- Ledger remains the primary source for order intent, submitted, fill, partial fill, reject, and position update evidence.
- Paper trial metrics are embedded in the attribution report.
- Policy intent summary captures execution mode, broker mode, limits, allowed order types, sectors, and themes.
- Signal contribution summarizes action, strength, planned target weight, intended notional, filled notional, and reason codes.
- Risk budget attribution summarizes turnover usage, largest order usage, batch decisions, rejected order ids, stale inputs, and failed checks.
- Sector attribution summarizes intended, filled, and rejected notional by sector using persisted context plus fixture snapshot metadata.
- Theme attribution uses preferred policy themes when available and otherwise marks symbol-level theme metadata unavailable.
- Position attribution explains each touched order or position as intent, filled, partial fill, rejected, or trimmed.
- Rejected and trimmed decision explanations are derived from ledger reject events, batch risk decisions, and blocked order plans.

## Files Changed

- Added `quantpilot/packages/core/reports/report_types.py`.
- Added `quantpilot/packages/core/reports/attribution.py`.
- Added `quantpilot/packages/core/reports/markdown.py`.
- Updated `quantpilot/packages/core/reports/service.py`.
- Updated `quantpilot/packages/core/reports/__init__.py`.
- Added report tests under `quantpilot/tests/core/reports/`.

## Data Assumptions

- Reports use existing in-memory repositories and fixture-safe snapshots.
- Missing ledger entries return `status="unavailable"` with review flags instead of throwing.
- Missing theme metadata is explicitly marked unavailable and does not fabricate symbol-level themes.
- The rich report is additive and does not replace the legacy `OperationReport` API.

## Safety Invariants Preserved

- `LIVE_TRADING_ENABLED=false`.
- `BROKER_MODE=mock` remains the validation default.
- `GUARDED_AUTOPILOT_ENABLED=false` default preserved.
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false` default preserved.
- `MARKET_ORDERS_ENABLED=false` default preserved.
- No broker credentials, credential UI, account IDs, live broker connector, live order transport, or market-order enablement was added.
- Report generation is read-only and does not submit, approve, modify, or reconcile orders.
- Tests use fixture/mock/paper-safe paths only and do not require internet access.

## Validation

- `python -m pytest quantpilot/tests/core/reports -q`
  - Result: `10 passed`
- `python -m pytest quantpilot/tests`
  - Initial run hit the known Windows temp-directory permission issue at `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan`.
  - Before the temp error: `291 passed, 1 skipped, 1 error`.
- `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
  - Result: `292 passed, 1 skipped`
- `python -m quantpilot.jobs.run_smoke`
  - Result: passed
  - Broker: `mock`
  - Execution mode: `approval_required`
  - Live trading enabled: `false`
  - Operator fallback: `level5_flag_disabled`

## Known Limitations

- Symbol-level theme metadata is not persisted, so theme attribution is policy-level or unavailable.
- Operation reports still use the fixture snapshot when persisted snapshots are absent.
- Ranking explanations and calibrated signal internals are included only where they are already persisted in existing repository objects.
- This step adds reporting only; it does not add offline learning, tax/regulatory reports, live performance reports, or ledger rewrites.

## Stage Status

- Step 13 status: complete.
- Live trading enabled: no.
- Broker mode used for validation: mock.
- Stage safety invariant status: preserved.
