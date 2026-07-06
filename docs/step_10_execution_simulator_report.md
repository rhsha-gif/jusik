# Step 10 Execution Simulator Report

## Summary

Step 10 adds a simulator-only execution layer for approved order plans. It models
TWAP, VWAP, and POV slicing; deterministic fill probability; partial-fill event
streams; simulated cancel/replace events; queue estimates; adverse selection;
and slippage estimates without adding any live broker integration.

Existing mock and paper broker submission behavior is unchanged. The simulator
is an opt-in core module and reports `broker_order_sent=false`.

## Implemented Files

- `quantpilot/packages/core/execution/types.py`
- `quantpilot/packages/core/execution/slicing.py`
- `quantpilot/packages/core/execution/simulator.py`
- `quantpilot/packages/core/execution/__init__.py`
- `quantpilot/tests/core/execution/test_execution_slicing.py`
- `quantpilot/tests/core/execution/test_execution_simulator.py`
- `quantpilot/tests/core/execution/test_execution_simulator_safety.py`

## Implemented Behavior

- `ExecutionSimulationRequest`, `SliceSchedule`, `ExecutionEvent`, and
  `ExecutionSimulationResult` Pydantic models.
- TWAP slicing with fixed intervals and even quantity allocation.
- VWAP slicing from a deterministic volume curve.
- POV slicing that respects a configured max participation cap and leaves
  excess quantity unscheduled.
- Simulator-only partial fill lifecycle events.
- Simulator-only cancel/replace events with no broker call path.
- Deterministic queue estimate from an L2 provider when available, with a
  deterministic fallback when no L2 provider is supplied.
- Deterministic adverse selection and slippage estimates.
- Fail-closed results for non-approved orders, unavailable quotes, and market
  orders.
- JSON/dict serialization through Pydantic `model_dump(mode="json")`.

## Data Assumptions

- Default data mode is `fixture`.
- Quote data is supplied through the existing `QuoteProvider` protocol.
- L2 data is optional and supplied through the existing `L2Provider` protocol.
- Missing or unavailable quotes return an `unavailable` no-trade result.
- No internet, realtime market data, broker credentials, or account identifiers
  are required.

## Safety Invariants

- Live trading enabled: no.
- Broker mode used for validation: mock.
- Market orders remain disabled in the simulator.
- No credential UI was added.
- No real broker integration was added.
- No actual submit, cancel, or replace order path was added.
- Simulator cancel/replace emits events only.
- Existing `MockBroker.submit_order` immediate fill behavior remains covered by
  regression tests.

## Validation

- `python -m pytest quantpilot/tests/core/execution -q`
  - Result: `9 passed`
- `python -m pytest quantpilot/tests`
  - Initial result: failed during setup because Windows denied access to
    `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan`.
  - Rerun with `TEMP` and `TMP` pointed at a workspace temp directory.
  - Final result: `278 passed, 1 skipped`
- `python -m quantpilot.jobs.run_smoke`
  - Result: passed.
  - Smoke summary reported `broker="mock"` and `live_trading_enabled=false`.

## Known Limitations

- The simulator is not wired into `submit_order_plan`; it is intentionally
  opt-in and simulator-only.
- Queue and adverse-selection estimates are deterministic proxies, not exchange
  microstructure models.
- POV schedules do not force completion when configured participation limits and
  expected volumes cannot cover the full order quantity.
- `docs/lob_spread_pricing_report.md` was referenced by the prompt but is not
  present in this checkout.

## Next Recommended Step

If needed, add an explicit API or harness-service preview endpoint that returns
`ExecutionSimulationResult` for already approved orders while preserving the
existing mock broker submission path.
