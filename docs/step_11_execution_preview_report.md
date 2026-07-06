# Step 11 Execution Simulation Preview Report

## Summary

Step 11 wires the Step 10 simulator-only execution layer into the harness
service and API as an explicit, opt-in preview path. A new
`HarnessService.preview_order_execution` method and a
`POST /api/orders/{order_plan_id}/simulate` route return an
`ExecutionSimulationResult` for already approved order plans without touching
the existing mock/paper submission path.

## Implemented

- `HarnessService.preview_order_execution(order_plan_id, *, config, quote_provider, l2_provider)`:
  - Loads the order plan and policy from repositories (missing IDs surface as
    the existing 404 `RepositoryError` handling).
  - Runs `ExecutionSimulator` against a deep copy of the order plan so preview
    can never mutate order state.
  - Defaults to `FixtureQuoteProvider` (fixture OHLCV closes); providers are
    injectable for tests.
  - Emits `execution_simulation_previewed` or `execution_simulation_blocked`
    audit events (both added to `AUDIT_EVENT_ACTIONS` before first emission).
- `POST /api/orders/{order_plan_id}/simulate` in the orders router with an
  optional `SimulateOrderRequest` body carrying an `ExecutionSimulatorConfig`.

## Safety Invariants

- Live trading enabled: no.
- Broker mode used for validation: `mock`.
- `LIVE_TRADING_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`,
  `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`
  defaults preserved.
- Preview never submits, approves, transitions, or expires an order plan; the
  repository copy keeps its pre-preview status.
- No fills or broker orders are created; `broker_order_sent=false` always.
- Fail-closed behavior is inherited from the simulator: non-approved orders
  (`order_not_approved`), market orders (`market_order_disabled`), and
  unavailable quotes (`quote_unavailable`) return blocked/unavailable results
  with zero filled quantity.
- No broker credentials, live broker integration, or live order path added.

## Validation

- `python -m pytest quantpilot/tests/unit/test_execution_preview.py`
  - Result: `5 passed`
- `python -m pytest quantpilot/tests`
  - Result: `283 passed, 1 skipped`
- `python -m quantpilot.jobs.run_smoke`
  - Result: passed; operator section stays `blocked` / `level5_flag_disabled`,
    `live_trading_enabled=false`, `submitted_order_plan_ids=[]`.

## Known Limitations

- The preview endpoint uses fixture quotes by default; it does not consult the
  harness `DATA_MODE` local/historical providers.
- Simulation results are deterministic proxies and carry no execution
  guarantee; they are advisory metadata for human review before approval-based
  submission.

## Next Recommended Step

Feed calibrated signal proxies (Step 08) into the portfolio optimizer through
an explicit adapter with tests proving provider failures, stale data, and
low-confidence signals fail closed (the Step 08 recommendation that has not
yet been implemented).
