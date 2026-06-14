# Step 03 Snapshot Threading Report

## Status

Completed. Runtime mock/paper portfolio snapshots now carry source and staleness
metadata into the operator, harness, and final risk gate. The final submit path
no longer depends on `fixture_portfolio_snapshot()`.

## Implemented

- Added additive `PortfolioSnapshot` metadata:
  - `as_of`
  - `generated_at`
  - `is_fixture`
  - `is_stale`
  - `stale_reason`
- Added risk-check snapshot evidence:
  - `snapshot_id`
  - `snapshot_source`
  - `snapshot_as_of`
  - `snapshot_is_stale`
- Added runtime snapshot helpers in `quantpilot/packages/core/portfolio/snapshot.py`.
- Added a fixture-free static snapshot provider in `quantpilot/packages/core/portfolio/snapshot_provider.py`.
- Updated `MockBroker` and inherited paper broker behavior to return runtime snapshots from the provider instead of planner fixtures.
- Updated `HarnessService.submit_order_plan()` to accept a threaded `snapshot` and to fetch a safe mock/paper broker snapshot when omitted.
- Added fail-closed validation for missing, fixture, stale, or source-less runtime snapshots.
- Updated Level 5 operator reporting safety flags to include portfolio snapshot metadata.
- Added deterministic Level 5 fallback reasons for missing, fixture, and stale portfolio snapshots.
- Updated guarded autopilot and smoke paths to use runtime broker snapshots for submit-time risk.

## Safety Invariants

- Live trading enabled: no.
- Broker mode used for validation: mock.
- `LIVE_TRADING_ENABLED=false` preserved.
- `BROKER_MODE=mock` preserved in smoke validation.
- Market order enablement was not changed.
- No broker credentials, credential UI, real broker integration, or live order path was added.
- Fixture snapshots remain usable for unit tests and Level 1/2-style fixture workflows, but are rejected on the runtime submission path.

## Tests And Validation

- `python -m pytest quantpilot/tests/unit/test_snapshot_threading.py -q -p no:cacheprovider --basetemp=.pytest_tmp`
  - 7 passed.
- `python -m pytest quantpilot/tests/integration/test_level5_operator_run_once.py -q -p no:cacheprovider --basetemp=.pytest_tmp`
  - 27 passed.
- `python -m pytest quantpilot/tests/unit/test_level5_fallback_manager.py -q -p no:cacheprovider --basetemp=.pytest_tmp`
  - 3 passed.
- `python -m pytest quantpilot/tests/unit/test_level3_proposals.py quantpilot/tests/integration/test_level3_flow.py -q -p no:cacheprovider --basetemp=.pytest_tmp`
  - 9 passed.
- `python -m pytest quantpilot/tests/unit/test_risk_matrix.py quantpilot/tests/unit/test_pre_harness.py -q -p no:cacheprovider --basetemp=.pytest_tmp`
  - 23 passed.
- `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
  - 269 passed, 1 skipped.
- `python -m quantpilot.jobs.run_smoke`
  - Passed with `broker: mock`, `live_trading_enabled: false`, and Level 5 blocked by default.
- `cd quantpilot/apps/web && npm.cmd run test`
  - 5 files passed, 18 tests passed.
- `cd quantpilot/apps/web && npm.cmd run build`
  - Passed.

## Data Assumptions

- Mock and paper snapshots are still deterministic and local.
- The default runtime snapshot provider mirrors the old fixture cash, equity, and positions so existing behavior remains compatible while the runtime source is no longer a fixture.
- Staleness is evaluated from `PortfolioSnapshot.as_of` against the policy's `stale_quote_max_age_seconds`.

## Known Limitations

- The runtime provider is still static mock/paper state, not a real broker state adapter.
- Level 1/2 and direct unit-test helpers can still use fixture snapshots by design.
- Existing public harness methods that are not submission paths may still default to fixtures for backward compatibility.

## Next Recommended Stage

Add a real paper-state repository boundary or fake-client paper adapter state store, then reconcile submitted fills back into the runtime snapshot provider without enabling live trading.
