# Pre-Stage06 Hardening Report

Date: 2026-06-14

## Scope

This pass inspected the current repository before Stage06 realtime ingestion and
made only narrow hardening changes. It did not add realtime WebSockets, broker
account/order APIs, live trading, credentials, network-required unit tests, or a
paper/live broker integration.

Preserved defaults:

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`
- `DATA_MODE=fixture`

## Actual Diagnosis

The repo was already in a broad dirty state before this pass, including Level 3,
Level 4, Level 5, local/external historical data, frontend, and documentation
changes. Baseline backend verification before edits passed:

- `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
  - `221 passed, 1 skipped`
- `python -m quantpilot.jobs.run_smoke`
  - PASS; `broker=mock`, `live_trading_enabled=false`, operator `status=blocked`,
    fallback `level5_flag_disabled`, no submitted Level 5 orders.

Stages verified as complete in this repo:

- Level 1-2 research/signal/rebalance rails: covered by `test_level_1_2.py`,
  `test_pre_harness.py`, and smoke.
- Stage03 backtest validation: covered by `test_backtest_engine.py`; backtests
  are research-only and do not call broker adapters.
- Stage04 strategy promotion and lifecycle evidence: covered by
  `test_strategy_promotion.py`, `test_strategy_lifecycle_bridge.py`, and
  `test_strategy_lifecycle_registry_binding.py`.
- Level 3 proposals and approval-required submission: covered by
  `test_level3_proposals.py` and `integration/test_level3_flow.py`.
- Level 4 guarded autopilot rails: covered by `test_authority_checks.py`,
  `test_risk_matrix.py`, and `integration/test_level4_guarded_flow.py`.
- Level 5 operator rails: covered by `test_level5_*.py` and
  `integration/test_level5_operator_run_once.py`; disabled by default.
- Stage05.5 external historical quality gates: covered by
  `test_data_mode.py`, `test_providers.py`, `test_external_historical_provider.py`,
  and `test_historical_data_quality.py`.

Incomplete by design:

- Stage06 realtime ingestion is not implemented.
- No exchange-grade realtime freshness/session feed exists.
- No live broker/account/order API exists.
- KIS historical integration remains manual and skipped by default.
- No production secret-management workflow exists.

## Strategy Chosen

Because all baseline checks passed, the safe cleanup strategy was to avoid broad
rewrites and only close high-confidence false-authority and provenance gaps:

- Make lifecycle evidence binding part of registry selection rather than only a
  standalone helper.
- Declare registry `spec_hash` in the contract/schema so lifecycle binding is
  explicit and fixture-backed.
- Add audit redaction for secret-shaped fields before Stage06 introduces more
  provider configuration.
- Update README safety defaults and Windows temp guidance.
- Create this report instead of rewriting old historical reports.

## Fixes Made

1. Registry/lifecycle authority hardening

- `StrategyRegistryEntry` now has optional `spec_hash`.
- `StrategyRegistry.select_for_level5()` now rejects otherwise eligible Level 5
  entries when lifecycle evidence is missing, mismatched, disabled, or revoked.
- `StrategyRegistry.level4_available()` also respects lifecycle binding.
- Default registry entries carry fixture `spec_hash` values and load fixture
  lifecycle evidence.
- Tests now prove a promoted policy plus `validated_l5` registry entry still
  cannot submit without matching lifecycle evidence.

2. Audit secret redaction

- `AuditRecorder` now redacts secret-shaped keys recursively before persisting
  audit `before_state`/`after_state`.
- Redacted examples include `authorization`, `access_token`, `appsecret`,
  `KIS_APP_KEY`, and common `*_secret`/`*_token` suffixes.
- Non-secret safety keys such as `idempotency_key` are preserved.

3. Docs/config hardening

- README safe defaults now list guarded autopilot, fully automated operator, and
  `DATA_MODE=fixture`.
- README documents the Windows-safe pytest command using
  `--basetemp=.pytest_tmp`.
- `docs/contracts/operator_contracts.md` now includes registry `spec_hash` and
  states that missing lifecycle evidence fails closed.

## Unchanged Behavior

- Fixtures remain the default data source.
- External historical providers still require explicit injection or env config.
- Manual KIS integration still skips unless `RUN_KIS_MANUAL_INTEGRATION=1`.
- Level 5 remains blocked by default.
- Broker mode remains mock by default.
- Market orders remain disabled by default.
- Live trading remains disabled and not implemented.
- Risk checks, kill switches, idempotency, order-state-machine checks, fallback
  matrix behavior, audit logging, and reconciliation paths were not weakened.

## Stale Docs And Debt

- Older stage reports contain historical test counts such as 58, 108, 113, and
  166 tests; the current suite is 224 passed / 1 skipped after this pass.
- `docs/level_3_4_implementation_report.md` says no frontend exists, but the
  repo now contains `quantpilot/apps/web`.
- `docs/stage_03_backtest_validation_report.md` records an older Windows temp
  permission workaround; the verified command now uses `.pytest_tmp`.
- `real_data_migration_baseline.md` still describes an older call graph and
  should be refreshed before any Stage06 merge that changes ingestion/runtime
  dependencies.
- `OperatorService._record_signals()` still loads fixture OHLCV directly through
  `load_fixture_ohlcv()`; this is acceptable for default-disabled Level 5 smoke,
  but Stage06 should route any realtime or external-market run through the
  provider/data-mode boundary.
- `HarnessService.submit_order_plan()` still uses `fixture_portfolio_snapshot()`
  for the final fresh risk check. This is safe for mock defaults, but must be
  threaded through before any broker adapter with real account state exists.

## Stage06 Readiness Checklist

- [x] Full backend tests pass with workspace-local pytest temp dir.
- [x] Smoke passes with `broker=mock`.
- [x] Smoke reports `live_trading_enabled=false`.
- [x] Smoke reports Level 5 `status=blocked` by default.
- [x] Provider factory defaults to fixture and external historical fails closed.
- [x] Historical data quality blocks unsafe consumption.
- [x] Backtest summaries carry external provider provenance and quality.
- [x] Manual KIS integration is opt-in and skipped by default.
- [x] Lifecycle binding blocks execution authority without evidence.
- [x] Audit logs redact secret-shaped fields.
- [ ] Stage06 realtime provider contract is designed.
- [ ] Exchange/session calendar source is reviewed.
- [ ] Realtime freshness/staleness policy is specified.
- [ ] Secret-management workflow is specified before credentialed connectors.
- [ ] Final risk-check snapshot threading is resolved before any real broker.

## Go / No-Go

Go for Stage06 implementation planning and disabled-by-default realtime market
data ingestion rails.

No-go for live trading, paper broker expansion, account/order APIs, credentialed
connectors in unit tests, or any realtime path that can bypass provider quality,
risk checks, lifecycle binding, kill switches, idempotency, audit redaction, or
the mock/default-disabled safety invariants.

## Final Verification

- `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
  - `224 passed, 1 skipped in 2.44s`
- `python -m quantpilot.jobs.run_smoke`
  - PASS; `broker=mock`, `execution_mode=approval_required`,
    `live_trading_enabled=false`, operator `status=blocked`,
    fallback `level5_flag_disabled`, `submitted_order_plan_ids=[]`.
- `git diff --check`
  - Passed with no whitespace errors.
