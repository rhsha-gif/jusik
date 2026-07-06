# Step 13 Calibrated Planning Wiring Report

## Summary

Step 13 wires the Step 12 calibrated proxy adapter into the harness planning
path behind an opt-in flag. `HarnessService.create_portfolio_plan` now accepts
an optional `CalibratedSignalSet`, and the Level 5 operator forwards the
provider-bound signal set's calibrated signals into planning. When the
`CALIBRATED_PLANNING_ENABLED` flag is unset (default), the calibrated set is
dropped and planning stays byte-identical to the uncalibrated path.

## Implemented

- `calibration_adapter.calibrated_planning_flag_enabled()`:
  reads `CALIBRATED_PLANNING_ENABLED` (default `false`).
- `PortfolioPlan.proxy_metadata: dict[str, Any] | None` (additive optional
  field) so downstream consumers can see whether a plan was built from
  calibrated proxies and why symbols were excluded.
- `planner.build_portfolio_plan` now stamps `optimization_result.proxy_metadata`
  onto the returned plan.
- `HarnessService.create_portfolio_plan(..., calibrated_signal_set=None)`:
  passes the calibrated set to the planner only when the flag is enabled.
- `OperatorService` forwards `signal_set.calibrated_signal_set` into
  `create_portfolio_plan`; the flag gate keeps default runs unchanged.

## Safety Invariants

- Live trading enabled: no.
- Broker mode used for validation: `mock`.
- `CALIBRATED_PLANNING_ENABLED` defaults to `false`; with it unset, planning
  behavior and outputs are unchanged (298 tests pass without setting it).
- Calibration never enables order submission, never changes risk gates, and
  can only replace an optimizer proxy — excluded/fail-closed symbols fall back
  to the conservative uncalibrated proxy (inherited from Step 12).
- Provider-unavailable / unusable-data calibrated sets fail closed at the set
  level; planning proceeds with uncalibrated proxies rather than blocking.

## Validation

- `python -m pytest quantpilot/tests/unit/test_harness_calibrated_planning.py`
  - Result: `4 passed`
- `python -m pytest quantpilot/tests`
  - Result: `298 passed, 1 skipped`
- `python -m quantpilot.jobs.run_smoke`
  - Result: passed; operator section stays `blocked` /
    `level5_flag_disabled`, `live_trading_enabled=false`.

## Known Limitations

- The operator only *feeds* the calibrated set into planning; it does not yet
  surface `proxy_metadata` in the operator run report payload. Exposing it in
  the report and the frontend is a follow-up.
- `CALIBRATED_PLANNING_ENABLED` is a runtime env flag; there is no per-policy
  field for it yet.

## Next Recommended Step

Step 14 — surface the Step 09 `PromotionEvidenceReport` through the strategy
lifecycle registry so promotion review consumes walk-forward evidence
(promotion stays human-gated with `promotion_allowed=false`).
