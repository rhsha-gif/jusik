# Stage 03 Backtest Validation Report

## 1. Summary

Implemented a deterministic, research-only backtest validation layer for QuantPilot strategies. The engine consumes provider-backed or injected local historical OHLCV rows, simulates next-bar limit-touch fills, accounts for cash, positions, fees, slippage, sell tax assumptions, blocked trades, turnover, exposure, drawdown, volatility, and simplified Sharpe, and returns auditable `BacktestResult` objects.

The implementation does not submit orders, does not call broker adapters, does not connect to external data, and does not promote strategies.

## 2. Files changed

- `quantpilot/packages/core/backtest/__init__.py`
- `quantpilot/packages/core/backtest/schemas.py`
- `quantpilot/packages/core/backtest/engine.py`
- `quantpilot/packages/core/backtest/metrics.py`
- `quantpilot/packages/core/backtest/validation.py`
- `quantpilot/tests/unit/test_backtest_engine.py`
- `docs/stage_03_backtest_validation_report.md`

## 3. Architecture / boundaries changed

- Added a new `quantpilot.packages.core.backtest` package with pure Pydantic models and deterministic helpers.
- `run_backtest()` reads historical bars through `MarketDataProvider.get_price_history()` or explicit injected local rows. It never reads fixture files directly.
- Backtest signal inputs accept `BacktestSignal` and existing `Signal`-shaped objects.
- Validation helpers provide train/test and walk-forward windows over actual trading dates, plus research-only acceptance threshold evaluation.
- No FastAPI routes, OpenAPI changes, frontend changes, repositories, operator services, or broker paths were added.

## 4. Safety impact

- Live trading remains disabled by default.
- Broker validation remained `mock`.
- `MARKET_ORDERS_ENABLED=false` and the existing order state machine were not changed.
- The backtest result is explicitly `research_only=True` and `live_trading_approval=False`.
- Tests monkeypatch broker submit methods to raise if called; the backtest still passes, confirming no broker submission path is used.

## 5. Tests run with command output summary

- `python -m pytest quantpilot/tests/unit/test_backtest_engine.py --basetemp .pytest-tmp`
  - Passed: `9 passed in 0.40s`
- `python -m pytest quantpilot/tests --basetemp .pytest-tmp`
  - Passed: `166 passed in 1.84s`
- `python -m quantpilot.jobs.run_smoke`
  - Passed: output reported `broker="mock"`, `live_trading_enabled=false`, Level 5 operator `status="blocked"`, fallback `level5_flag_disabled`, and `submitted_order_plan_ids=[]`.
- `python -m pytest quantpilot/tests`
  - Environment failure remains: pytest cannot scan `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan` due `PermissionError: [WinError 5] 액세스가 거부되었습니다`.
  - Before the permission error, the run showed `164 passed, 1 error`; the error is in pytest temp setup for a `tmp_path` fixture, not in Stage 03 code.

## 6. Known limitations

- The fill model is intentionally simplified and deterministic: `next_open_limit_touch`.
- No automatic strategy promotion, DSR, PBO, trial database, or registry update is implemented.
- No API route or UI is exposed for backtest execution in this stage.
- The sell-tax rate is configurable but defaults to zero and emits a warning when omitted.
- Backtest results are research evidence only and are not live-trading approval.

## 7. Next recommended stage

Use Stage 04 to connect backtest evidence to a human-reviewed strategy promotion workflow. Keep promotion registry-driven, require explicit evidence references, and preserve the existing default-disabled live and automated-trading flags.
