# Step 05 Portfolio Optimizer Report

Date: 2026-06-14

## Scope Completed

- Added deterministic portfolio optimizer DTOs:
  - `ExpectedReturnRiskProxy`
  - `OptimizationConstraints`
  - `OptimizationInput`
  - `TargetWeight`
  - `OptimizationResult`
- Added `DeterministicPortfolioOptimizer` for long-only constrained target weights.
- Added a backward-compatible planner adapter through `build_portfolio_plan`.
- Added uncalibrated signal-derived expected return and volatility proxy metadata.
- Added explicit fail-closed/no-trade behavior for missing proxies, exceptions, and infeasible constraints.

## Constraints Implemented

- Max position weight
- Max sector weight
- Minimum cash buffer
- Max turnover weight
- Rebalance band
- Optional max order weight for direct optimizer callers

The default planner path keeps per-order cash limiting in the legacy adapter so existing public behavior and tests remain compatible.

## Data Assumptions

- Fixture data remains the default validation path.
- Planner-generated proxies are uncalibrated and marked with proxy metadata.
- Sector metadata is explicit input when available; existing portfolio position sectors are used as fallback; otherwise `unknown` is used.
- No covariance model, factor model, probabilistic optimizer, or ML sizing was added.

## Safety Invariants

- Live trading enabled: no
- Broker mode used for validation: mock
- Market orders enabled by this change: no
- Broker credentials added: no
- Real broker integration added: no
- Actual order transmission paths added: no
- Order submission enabled by optimizer results: no

## Tests

- `python -m pytest quantpilot\tests\unit\test_portfolio_optimizer.py -q`
  - Result: 10 passed
- `python -m pytest quantpilot\tests`
  - Result: 242 passed, 1 skipped
  - Note: pytest temp directory was pointed inside the workspace to avoid a Windows temp permission issue.
- `python -m quantpilot.jobs.run_smoke`
  - Result: passed
  - Observed `broker=mock`
  - Observed `live_trading_enabled=false`
  - Observed Level 5 operator fallback `level5_flag_disabled`

## Known Limitations

- Expected return and volatility proxies are deterministic placeholders derived from signal metadata unless explicit proxies are supplied.
- The optimizer is long-only and does not implement covariance, factors, or batch risk.
- `PortfolioPlan` does not yet expose optimizer status or constraint evidence directly; those remain available from `OptimizationResult` for direct callers.

## Next Recommended Step

- Calibrate expected return and volatility proxy generation from validated historical features while preserving fixture-first tests and fail-closed behavior.
