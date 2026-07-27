# Stage 02-04 Preparation Handoff

Date: 2026-06-13

This handoff records the rails now in place before implementing local historical data workflows, backtest validation, and the strategy registry promotion path.

## Stage 01 Closed

- `DATA_MODE` defaults to `fixture` only when unset.
- Unsupported `DATA_MODE` values now fail closed instead of silently falling back to fixtures.
- `/api/health` reports `status=blocked`, the raw data mode, and `data_mode_error` for invalid configuration.
- `live_trading` data mode is surfaced as unsafe and blocked.

## Stage 02 Ready Rails

- `DATA_MODE=local_historical` requires `LOCAL_DATA_DIR`.
- Local data must provide:
  - `securities.csv`
  - `ohlcv.csv`
- The API dependency and smoke job can now construct `HarnessService.from_environment()`.
- Fixture defaults remain unchanged for direct `HarnessService()` construction and unset `DATA_MODE`.
- Future data modes (`external_historical`, `realtime_market_data`, `paper_trading`, `live_trading`) are intentionally not routed to fixtures.

## Stage 03 Preparation

Backtest implementation should start from the existing provider boundary and should not call broker adapters. Minimum first slice:

- Backtest service reads `MarketDataProvider.get_price_history()`.
- Execution model uses next-bar fills only.
- Cost and slippage parameters are nonzero by default in tests.
- Metrics include turnover, max drawdown, exposure, trade count, and cash/equity path.
- Add a no-lookahead regression test before implementing signal replay.

## Stage 04 Preparation

Strategy promotion must remain registry-driven:

- `StrategyRecipe` cannot self-promote to `validated_l5`.
- `StrategyRegistryEntry` remains the authority record for Level 5 eligibility.
- Promotion from `draft` to `validated_l3` should require Stage 03 backtest evidence.
- Disabled and revoked strategies must stay ineligible across Levels 3-5.

## Human Inputs Needed Later

- Real local historical CSV/parquet data path for Stage 02 beyond test fixtures.
- Transaction cost/slippage assumptions for Stage 03.
- Walk-forward window policy for Stage 03.
- Evidence format and approver identity policy for Stage 04 promotions.

