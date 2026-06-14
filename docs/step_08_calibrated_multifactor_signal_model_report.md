# Step 08 Calibrated Multi-Factor Signal Model Report

## Implemented

- Added strict serializable signal calibration contracts:
  - `CalibratedSignal`
  - `CalibratedSignalSet`
  - `MultiFactorScore`
  - `ExpectedReturnRiskProxy`
  - `EnsembleVote`
  - `CalibrationGuardResult`
- Added deterministic multi-factor scoring for:
  - momentum
  - trend
  - volume
  - volatility
  - data quality
- Added deterministic regime inference with regime-specific factor weights.
- Added confidence calibration, signal decay, horizon-scaled expected-return/risk proxies, and ensemble voting.
- Added an action-compatibility guard that preserves risk-reducing actions and prevents calibration from upgrading `watch` or `buy_wait` into `buy_ready`.
- Added provider quality guards so unavailable or stale provider paths produce no calibrated `buy_ready` action.
- Preserved the legacy `SignalSet.signals` public API and added `calibrated_signal_set` as an additive optional field.

## Data Assumptions

- Calibration is fixture/local-provider deterministic and does not train, fetch, or learn from external data.
- Legacy fixture OHLCV snapshot bars provide trend, volume, and volatility proxies.
- Candidate ranking is consumed only as an optional deterministic data-quality input.
- Missing bar metadata falls back to existing signal scores rather than failing open.

## Safety Invariants

- Live trading enabled: no.
- Broker mode used for validation: `mock`.
- `LIVE_TRADING_ENABLED=false` preserved.
- `GUARDED_AUTOPILOT_ENABLED=false` default preserved.
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false` default preserved.
- `MARKET_ORDERS_ENABLED=false` default preserved.
- No broker credentials, credential UI, real broker connector, live order path, market-order enablement, optimizer submission path, or live/online learning was added.
- Calibrated outputs are advisory signal metadata only; order submission remains disabled on signal sets.

## Validation

- `python -m pytest quantpilot/tests/core/signals -q`
  - Result: `7 passed`
- `python -m pytest quantpilot/tests/unit/test_marketdata_provider_bound_signals.py -q`
  - Result: `6 passed`
- `python -m pytest quantpilot/tests`
  - Initial run hit the known Windows temp-directory permission issue under `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan`.
  - Rerun with workspace-local `TMP`/`TEMP`: `264 passed, 1 skipped`
- `python -m quantpilot.jobs.run_smoke`
  - Result: passed
  - Broker: `mock`
  - Execution mode: `approval_required`
  - Live trading enabled: `false`
  - Operator fallback: `level5_flag_disabled`

## Known Limitations

- Regime and expected-return/risk outputs are deterministic proxies, not ML estimates or performance guarantees.
- Horizon scaling is a bounded heuristic and does not invoke an optimizer or backtest.
- Calibration does not change order planning authority and does not submit, approve, or validate orders.

## Next Recommended Step

- Keep the next stage separate: feed calibrated proxies into the portfolio optimizer only through an explicit adapter and tests that prove provider failures, stale data, and low-confidence signals fail closed.
