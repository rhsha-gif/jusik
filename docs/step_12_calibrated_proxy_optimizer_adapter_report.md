# Step 12 Calibrated Proxy → Optimizer Adapter Report

## Summary

Step 12 implements the Step 08 recommendation: calibrated expected-return/risk
proxies now reach the portfolio optimizer only through an explicit,
fail-closed adapter. The adapter translates a `CalibratedSignalSet` (Step 08)
into optimizer `ExpectedReturnRiskProxy` entries; anything it rejects falls
back to the pre-existing conservative uncalibrated planner proxy, so
calibration can only replace a proxy, never remove one or widen authority.

## Implemented

- `quantpilot/packages/core/portfolio/calibration_adapter.py`:
  - `CalibrationAdapterConfig` (`min_confidence=0.35`, `max_age_seconds=900`,
    `require_provider_available=True`).
  - `build_calibrated_proxies(...) -> CalibratedProxyAdapterResult` with
    `status` in `applied | partial | fail_closed`, accepted proxies, and
    per-symbol exclusion reasons.
  - Confidence-weighted shrinkage: adapter expected return is
    `raw_expected_return * confidence`, recorded in proxy metadata.
- `planner.build_optimization_input` / `build_portfolio_plan` gained additive
  optional parameters `calibrated_signal_set`, `calibration_config`, and
  `calibration_now` (decision-time clock is injectable for staleness tests,
  never the run start time). Explicitly passed
  `expected_return_risk_proxies` still take precedence; defaults are
  byte-identical to the previous behavior.

## Fail-Closed Rules

Set-level (reject everything):

- any provider status not `available` (`provider_<name>_<state>`)
- `data_quality.usable` false (`data_quality_not_usable`)
- `order_submission_enabled` unexpectedly true
  (`order_submission_flag_unexpected`)
- missing calibrated set (`calibrated_signal_set_missing`)

Per-symbol (excluded, falls back to uncalibrated proxy):

- calibration guard not passed / not `available`
  (`calibration_guard_<status>`)
- confidence below `min_confidence` (`low_confidence`)
- `generated_at` older than `max_age_seconds` (`stale_calibration`)
- requested symbol with no calibrated signal (`calibrated_signal_missing`)

## Safety Invariants

- Live trading enabled: no.
- Broker mode used for validation: `mock`.
- All feature-flag defaults preserved; no order submission, approval, or risk
  gate authority changes. The adapter produces advisory optimizer inputs only.
- The optimizer's own fail-closed behavior (missing proxies, infeasible
  constraints) is unchanged.

## Validation

- `python -m pytest quantpilot/tests/unit/test_calibration_optimizer_adapter.py`
  - Result: `11 passed`
- `python -m pytest quantpilot/tests`
  - Result: `294 passed, 1 skipped`
- `python -m quantpilot.jobs.run_smoke`
  - Result: passed; operator section stays `blocked` /
    `level5_flag_disabled`, `live_trading_enabled=false`.

## Known Limitations

- Harness runtime flows do not yet pass a `CalibratedSignalSet` into
  `build_portfolio_plan`; wiring the provider-bound signal path's calibrated
  set through `HarnessService.create_portfolio_plan` is a separate, explicit
  step so its fail-closed behavior can be reviewed in isolation.
- Confidence shrinkage is a bounded heuristic, not a statistical estimator.

## Next Recommended Step

Surface the Step 09 `PromotionEvidenceReport` through the strategy lifecycle
registry so promotion review consumes walk-forward evidence (promotion stays
human-gated with `promotion_allowed=false`).
