# QuantPilot Operator Level 1-2 Report

## 1. Implemented features

- Extended `UserPolicy` with fixture-safe Level 1-2 research controls: preferred themes, preferred sectors, blocklist, and minimum average daily traded value.
- Extended `Signal` compatibly with Level 2 fields: ticker, signal date, technical score, quant score, target weight hint, stop price hint, take profit hint, valid-until date, policy version, and reason codes.
- Added deterministic Korean policy preview parsing for market, risk tone, cash target, max position weight, preferred themes, sectors, rebalance frequency, and blocklisted tickers.
- Added fixture-first candidate universe builder with ticker, name, market, sector, theme match, liquidity pass, data readiness, block reason, and analyst-required flag.
- Added deterministic technical indicator calculations with moving averages, returns, volatility, RSI-like indicator, volume ratio, momentum score, technical score, liquidity score, and defensive score.
- Added template-based analyst reports with optional adapter injection. Analyst ratings do not modify or override `Signal.action`.
- Added Level 2 signal enrichment for all supported actions: `buy_ready`, `buy_wait`, `hold`, `trim`, `exit`, `watch`, `blocked`.
- Added non-executable rebalance suggestions using `PortfolioPlan` while clearing `order_intents` in the Level 1-2 report path.
- Added `HarnessService.run_level_1_2()` orchestration that stores signals, a portfolio plan, audit events, and an `OperationReport` without creating `OrderPlan`, broker orders, or fills.

## 2. Screens/routes added

No real frontend app exists in `quantpilot/apps/web`; only a placeholder file is present. API routes were added for the requested flows:

- `POST /api/policies/preview`
- `POST /api/research/universe`
- `POST /api/research/analyst`
- `POST /api/signals/board`
- `POST /api/portfolio/rebalance-suggestions`
- `POST /api/reports/research-signal-daily`
- `POST /api/level-1-2/run`

## 3. Data assumptions

- Candidate metadata is local fixture data only.
- Technical price history is generated deterministic local fixture data.
- Existing `ohlcv.json` fixture data remains the compatibility source for pre-harness smoke signals.
- Financial statement and valuation fields in analyst reports are marked unavailable when no local source exists.
- No LLM adapter is configured by default. The analyst report path accepts an optional adapter for later integration.

## 4. Safety invariants preserved

- Level 1-2 does not submit orders.
- Level 1-2 does not create executable `OrderPlan` objects.
- Level 1-2 does not call `MockBroker` or `PaperBroker`.
- Existing `OrderIntent`, `RiskCheck`, `OrderPlan`, `MockBroker`, and `PaperBroker` compatibility is preserved.
- Existing pre-harness smoke mode still creates mock orders and fills only through the explicit smoke/order endpoints.
- `live_trading_enabled` remains false.
- Market orders remain blocked by the existing risk tests unless explicitly allowed and still gated.
- Technical calculations filter rows at or before the signal date to prevent look-ahead leakage.

## 5. Tests run and results

- `python -m pytest quantpilot\tests\unit\test_level_1_2.py -q` -> PASS, 10 tests.
- `python -m pytest quantpilot\tests -q` -> PASS, 28 tests.
- `python -m quantpilot.jobs.run_smoke` -> PASS. Output reported `live_trading_enabled: false`, `broker: mock`, 7 signals, 3 filled mock orders, and 3 fills.
- Manual API smoke:
  - `POST /api/level-1-2/run` -> HTTP 200.
  - Response reported 7 candidates, 7 signals, and `order_submission_enabled: false`.

## 6. Known limitations

- Universe, technical history, and analyst reports are fixture/template-based.
- Analyst reports do not cite external sources yet.
- No frontend screens were added because no frontend implementation exists in this repository.
- Rebalance suggestions are weight guidance only and intentionally do not produce executable order plans.
- Policy parsing is deterministic and keyword-based, so Korean free-form phrasing coverage is limited.

## 7. Next recommended step

Run Fable5 Level 3-4 recipe prompt:

`04_FABLE5_LEVEL_3_4_RECIPE_PROMPT.md`
