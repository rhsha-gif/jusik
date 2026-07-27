# Step 06 Batch Risk Gate Report

## Summary

Step 06 adds a deterministic batch-level risk gate before order creation and before broker submission. The gate evaluates the after-batch portfolio state instead of only checking each order independently.

## Implemented

- Added `BatchRiskInput`, `BatchRiskConfig`, `BatchPortfolioExposure`, and `BatchRiskDecision`.
- Added `run_batch_risk_gate` and `run_batch_risk_gate_from_input`.
- Calculates after-batch cash, cash weight, position values/weights, and sector values/weights.
- Rejects stale portfolio snapshots and stale quote timestamps.
- Enforces after-batch cash buffer, sector cap, concentration cap, short-sell prevention, daily order count, daily turnover, order type, idempotency, kill switch, and daily/monthly loss-stop checks.
- Preserves backward compatibility by failing concentration and sector checks only when a batch creates or worsens a cap breach. Existing fixture positions can remain over a default cap if untouched.
- Defaults to full-batch rejection. Partial batches are only allowed when `partial_allow=true`.
- Wires the batch gate into:
  - `HarnessService.create_order_plans`
  - `HarnessService.generate_order_proposals`
  - `HarnessService.submit_order_plan`
- Adds `partial_allow` to existing order/proposal API request DTOs, defaulting to `false`.
- Regenerated `openapi.json` and frontend OpenAPI types.

## Safety Invariants

- Live trading enabled: no.
- Broker mode used for validation: `mock`.
- `LIVE_TRADING_ENABLED=false` preserved.
- `GUARDED_AUTOPILOT_ENABLED=false` default preserved.
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false` default preserved.
- `MARKET_ORDERS_ENABLED=false` default preserved.
- No broker credentials, credential UI, live broker connector, live order path, or market-order enablement was added.
- Batch rejection occurs before mock/paper broker submission.

## Validation

- `python -m pytest quantpilot/tests/unit/test_batch_risk_gate.py quantpilot/tests/unit/test_batch_risk_partial_allow.py quantpilot/tests/unit/test_harness_batch_risk.py -q`
  - 9 passed.
- `python -m pytest quantpilot/tests/unit/test_level3_proposals.py quantpilot/tests/integration/test_level4_guarded_flow.py quantpilot/tests/integration/test_smoke.py -q`
  - 16 passed.
- `python -m pytest quantpilot/tests`
  - Hit the known Windows temp-directory permission issue before one fixture setup.
- `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
  - 251 passed, 1 skipped.
- `python -m quantpilot.jobs.run_smoke`
  - Passed; broker `mock`, `live_trading_enabled=false`, 3 filled mock orders.
- `npm run generate:api`
  - Passed.
- `npm run build`
  - Passed; Vite emitted the existing large chunk warning.
- `npm run test`
  - 3 files passed, 11 tests passed.

## Known Limitations

- The batch gate uses deterministic notional-based exposure and does not model VaR, CVaR, factor risk, correlation, execution slippage, or fees.
- New-buy sector defaults to `unknown` when no existing position sector metadata is available.
- Submit-time batch grouping is conservative and includes same-policy proposed, approved, submitted, accepted, partially filled, and filled orders because the fixture snapshot may not reflect recent fills.

## Next Recommended Step

Add explicit portfolio-plan batch identifiers or execution-cycle identifiers so submit-time grouping can distinguish current-cycle orders from older same-policy filled orders without relying on conservative status grouping.
