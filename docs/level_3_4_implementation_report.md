# QuantPilot Operator Level 3-4 Implementation Report

Date: 2026-06-12

## 1. Fable5 recipe path used

- `docs/quant_recipes/fable5_level_3_4_autopilot_recipe.md`

## 2. Baseline confirmed

- Pre-harness safety baseline remains in place: live trading is disabled, default broker mode is mock, market orders remain blocked by default, and order submission still requires risk checks.
- Level 1-2 no-order behavior remains covered by `test_level_1_2_research_flow_cannot_submit_broker_orders`.
- Fable5 remains a recipe source only. It has no runtime order submission or approval authority.

## 3. Implemented Level 3 features

- Added proposal-first order flow through `HarnessService.generate_order_proposals`.
- Proposals are created only after a passing risk check.
- Proposed orders persist with explanation payloads, deterministic idempotency keys, risk check IDs, and risk check expiry timestamps.
- Approval, rejection, and modification paths are explicit:
  - approval: `proposal_approved`
  - rejection: `proposal_rejected`, then later submission is blocked
  - modification: original proposal becomes `modified`, revised proposal receives a new idempotency key and a new risk check
- Submission re-runs a fresh risk check and blocks expired risk checks.

## 4. Implemented Level 4 features

- Added guarded run-once flow through `HarnessService.run_guarded_autopilot_once`.
- Level 4 uses the same Level 3 proposal generation path before authority checks.
- Guarded autopilot is disabled by default.
- Automatic submission remains limited to MockBroker and PaperBroker-safe policy states.
- Added pause, resume, status, and kill-switch API support.

## 5. Level 4 authority sequence implemented

Implemented deterministic checks in `authorize_level4`:

1. guarded autopilot flag enabled
2. kill switch off
3. autopilot not paused
4. broker mode mock or paper
5. authority level 4 with guarded autopilot execution mode
6. policy version match
7. broker health state
8. quote freshness
9. strategy promotion approved or validated for Level 4
10. strategy allowed execution level includes Level 4 or guarded autopilot
11. KRX automatic-order window excludes opening and closing auction windows
12. order type allowed, with market orders blocked unless explicitly enabled
13. monthly loss pause/stop rules
14. no unfilled conflicting order
15. idempotency key is new
16. fresh risk check passes

## 6. Safety feature flags and defaults

- `LIVE_TRADING_ENABLED`: false
- `MARKET_ORDERS_ENABLED`: false
- `guarded_autopilot_enabled`: false
- `broker`: mock by default
- `authority_level`: 2 by default
- `kill_switch_engaged`: false by default
- `max_daily_orders`: 3
- `max_daily_turnover`: 3,000,000 KRW
- `stale_quote_max_age_seconds`: 30
- `human_review_quote_max_age_seconds`: 120
- `order_expiry_minutes`: 30
- `monthly_loss_pause_new_buys`: -0.05
- `monthly_loss_stop_all_autotrading`: -0.10

## 7. Schema fields reused vs added

Reused:

- `max_position_weight`
- `min_cash_weight`
- `max_sector_weight`
- `daily_loss_limit`
- `monthly_loss_limit`
- `RiskCheck.expires_at`
- `ExecutionMode`
- `BrokerMode`
- `OrderType`
- `OrderStatus`

Added:

- `UserPolicy.max_daily_orders`
- `UserPolicy.max_daily_turnover`
- `UserPolicy.monthly_loss_pause_new_buys`
- `UserPolicy.monthly_loss_stop_all_autotrading`
- `UserPolicy.stale_quote_max_age_seconds`
- `UserPolicy.human_review_quote_max_age_seconds`
- `UserPolicy.order_expiry_minutes`
- `UserPolicy.authority_level`
- `UserPolicy.kill_switch_engaged`
- `UserPolicy.guarded_autopilot_enabled`
- `ProposalExplanation`
- `AuthorityCheckStep`
- `AuthorityCheckResult`
- `GuardrailState`
- `OrderPlan.risk_check_expires_at`
- `OrderPlan.explanation`
- `OrderPlan.auto_order_reference_price`
- `OrderPlan.replaces_order_plan_id`
- `OrderPlan.blocked_reason`
- `OrderPlan.approved_by`
- `OrderPlan.expires_at`
- `OrderStatus.modified`
- `OrderStatus.failed`
- `StrategyRecipe.promotion_status`
- `StrategyRecipe.allowed_execution_levels`
- additive strategy metadata fields for v2 strategy specs

## 8. API routes added or changed

Added:

- `POST /api/orders/generate-proposals`
- `POST /api/orders/{order_plan_id}/reject`
- `POST /api/orders/{order_plan_id}/modify`
- `POST /api/autopilot/guarded/run-once`
- `POST /api/autopilot/guarded/pause`
- `POST /api/autopilot/guarded/resume`
- `POST /api/autopilot/kill-switch`
- `POST /api/autopilot/kill-switch/release`
- `GET /api/autopilot/status`

Existing route retained:

- `GET /api/orders/proposed`
- `POST /api/orders/{order_plan_id}/approve`
- `POST /api/orders/{order_plan_id}/submit`

## 9. UI changes

No frontend implementation exists in this repository. API responses now include explicit proposal state, explanation payloads, blocked reasons, kill-switch state, feature flags, broker mode, live trading state, guarded autopilot state, and monthly loss thresholds for a future UI.

## 10. Tests added and command results

Added tests:

- `quantpilot/tests/unit/test_level3_proposals.py`
- `quantpilot/tests/unit/test_risk_matrix.py`
- `quantpilot/tests/unit/test_authority_checks.py`
- `quantpilot/tests/unit/test_strategy_loader_v2.py`
- `quantpilot/tests/unit/test_rl_contract.py`
- `quantpilot/tests/integration/test_level3_flow.py`
- `quantpilot/tests/integration/test_level4_guarded_flow.py`

Commands run:

```powershell
python -m pytest quantpilot/tests
```

Result: `58 passed in 1.34s`

```powershell
python -m quantpilot.jobs.run_smoke
```

Result: PASS. Summary included `broker=mock`, `execution_mode=approval_required`, `signals=7`, `fills=3`, `audit_events=36`, all three smoke orders `filled`, and `live_trading_enabled=false`.

## 11. Broker modes tested

- MockBroker: covered by the smoke command and existing/unit integration tests.
- PaperBroker: covered by existing unit test that verifies simulated-only fills and zero live API calls.
- Live broker execution: not implemented and not enabled.

## 12. Live trading state

Live trading remains disabled. No live broker adapter was added.

## 13. Remaining limitations

- Repositories are still in-memory only.
- Guarded autopilot can authorize only strategies that have explicit promoted status and allowed execution levels; the new `pullback_trend_v2` strategy is intentionally draft-only.
- KRX session blocking uses configurable local-time defaults rather than exchange calendar data.
- No frontend screens were added because the repository has no frontend app.
- Backtest, walk-forward validation reports, and Level 5 deployment are not implemented in this step.

## 14. Next recommended step

Proceed to Level 5 Operator completion:

`07_FINAL_CODEX_LEVEL_5_DEPLOYMENT_PROMPT.md`
