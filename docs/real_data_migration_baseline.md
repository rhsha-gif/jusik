# QuantPilot — Real Data Migration Baseline

> Stage: fixture/mock baseline audit  
> Date: 2026-06-13  
> Author: Claude Sonnet 4.6 (safety audit pass)  
> Test run: 113 passed / 0 failed  

---

## 1. Purpose

This document establishes an auditable baseline of the current fixture/mock behavior, call graph, feature flags, and safety invariants **before** any live data or real broker integration is introduced. It satisfies the implementation requirement for a written baseline report (stage 1 of `docs/fable5_level5_implementation_spec.md`).

---

## 2. Call Graph — API → HarnessService → Risk → Broker

### 2.1 API route surface (registered in `quantpilot/services/api/main.py`)

| Router file | Prefix | Key endpoints |
|---|---|---|
| `routers/harness.py` | `/api` | `GET /api/health`, `POST /api/harness/run-smoke` |
| `routers/policies.py` | `/api` | `POST /api/policies/parse`, `/preview`, `/confirm` |
| `routers/level_1_2.py` | `/api` | `POST /api/level-1-2/run` |
| `routers/signals.py` | `/api` | `POST /api/signals/run`, `GET /api/signals/board` |
| `routers/portfolio.py` | `/api` | `POST /api/portfolio/plan` |
| `routers/orders.py` | `/api` | `POST /api/orders/plan`, `/generate-proposals`, `/{id}/approve`, `/{id}/reject`, `/{id}/modify`, `/{id}/submit`, `GET /{id}/status` |
| `routers/autopilot.py` | `/api` | `POST /api/autopilot/pause`, `/resume`, `/kill`, `/release-kill-switch`, `/run-once`, `GET /status` |
| `routers/operator.py` | `/api` | `POST /api/operator/run-once` |
| `routers/reports.py` | `/api` | `POST /api/reports/daily` |

### 2.2 Dependency injection

`quantpilot/services/api/dependencies.py` constructs **one** `RepositoryRegistry` and **one** `HarnessService` at module import time. `OperatorService` wraps `HarnessService`. All routes receive these singletons via FastAPI `Depends`.

### 2.3 HarnessService internal call graph

```
HarnessService.run_smoke()
  └── parse_policy() → AuditRecorder.emit(policy_created)
  └── confirm_policy() → AuditRecorder.emit(policy_confirmed)
  └── run_signals() → load_default_strategy() → generate_signals(fixture_ohlcv)
  └── create_portfolio_plan() → build_portfolio_plan()
  └── create_order_plans()
        └── run_risk_check()          ← gatekeeper.py
        └── transition_order_plan()   ← state_machine.py (VALID_TRANSITIONS)
  └── approve_order_plan() → transition_order_plan(user_approved)
  └── submit_order_plan()
        └── run_risk_check()          ← fresh check
        └── _broker_for_policy()      ← returns MockBroker or PaperBroker; raises for live_disabled
        └── broker.submit_order()
  └── create_daily_report()

HarnessService.run_guarded_autopilot_once()
  └── run_signals()
  └── create_portfolio_plan()
  └── generate_order_proposals()
        └── run_risk_check()
  └── for each proposal: authorize_level4()  ← state_machine.py
  └── submit_order_plan()

OperatorService.run_once()
  └── FallbackManager.for_reason()     ← fallback_manager.py
  └── authorize_level5()               ← state_machine.py
  └── HarnessService.*
```

### 2.4 Risk gate chain (per order, at every submit path)

```
run_risk_check()
  1. kill_switch_not_engaged
  2. policy_version_match
  3. execution_mode_allowed  ← uses allowed_execution_modes(policy)
  4. broker_mode_not_live    ← fails if BrokerMode.live_disabled
  5. available_cash
  6. min_cash_after_order
  7. max_position_weight_after_fill
  8. max_sector_weight_after_fill
  9. single_order_cash_limit
 10. max_daily_orders
 11. max_daily_turnover
 12. order_type_allowed       ← fails if market order and MARKET_ORDERS_ENABLED != true
 13. idempotency_key_not_seen
 14. quote_not_stale
 15. unfilled_conflicting_order (if strategy_id supplied)
 16. daily_loss_limit_not_triggered
 17. monthly_loss_pause_not_triggered
 18. monthly_loss_stop_not_triggered
 19. monthly_loss_stop_all_autotrading
 20. risk_reducing_sell (conditional)
```

### 2.5 Level 4 authority chain (`authorize_level4`)

```
1. guarded_autopilot_enabled     (GUARDED_AUTOPILOT_ENABLED env or policy field)
2. kill_switch_not_engaged
3. autopilot_not_paused
4. broker_mode_safe              (mock or paper only)
5. authority_level_4             (policy.authority_level == 4, execution_mode == guarded_autopilot)
6. policy_version_match
7. broker_health
8. quote_not_stale
9. strategy_promotion_approved
10. strategy_level_allowed
11. krx_auto_order_window
12. order_type_allowed
13. monthly_loss_stop_not_triggered
14. monthly_loss_pause_allows_order
15. no_unfilled_conflicting_order
16. idempotency_key_new
17. fresh_risk_check_passed      (calls run_risk_check)
```

### 2.6 Level 5 authority chain (`authorize_level5`)

```
1. fully_automated_operator_enabled   (FULLY_AUTOMATED_OPERATOR_ENABLED env or policy field)
2. live_trading_disabled              (LIVE_TRADING_ENABLED must be false)
3. kill_switch_not_engaged            (policy + state + OPERATOR_KILL_SWITCH env)
4. operator_not_paused
5. broker_mode_safe                   (mock or paper only)
6. authority_level_5                  (policy.authority_level == 5, execution_mode == fully_automated)
7. policy_version_match
8. broker_health
9. quote_not_stale
10. strategy_registry_validated_l5    (registry entry must have status = validated_l5)
11. strategy_level_allowed
12. strategy_recipe_matches_registry
13. krx_auto_order_window
14. order_type_allowed
15. monthly_loss_stop_not_triggered
16. monthly_loss_pause_allows_order
17. no_unfilled_conflicting_order
18. idempotency_key_new
19. fresh_risk_check_passed
```

---

## 3. Feature Flag Defaults

| Flag | Read from env | Default value | Code path |
|---|---|---|---|
| `LIVE_TRADING_ENABLED` | `state_machine.live_trading_flag_enabled()` | `false` | `authorize_level5` check 2 |
| `GUARDED_AUTOPILOT_ENABLED` | `state_machine.guarded_autopilot_flag_enabled()` | `false` | `authorize_level4` check 1 |
| `FULLY_AUTOMATED_OPERATOR_ENABLED` | `state_machine.fully_automated_operator_flag_enabled()` | `false` | `authorize_level5` check 1; `allowed_execution_modes()` |
| `MARKET_ORDERS_ENABLED` | `gatekeeper.market_orders_enabled()` and `state_machine._market_orders_enabled()` | `false` | risk check 12, authority checks |
| `BROKER_MODE` | `UserPolicy.broker` default | `mock` | `_broker_for_policy()` |
| `OPERATOR_KILL_SWITCH` | `state_machine.operator_kill_switch_engaged()` | `false` | `authorize_level5` check 3 |

All defaults are documented in `.env.example` and verified by `test_level5_safety_flags.py`.

### 3.1 Broker routing

`HarnessService._broker_for_policy()` (harness_service.py:649):
- `BrokerMode.paper` → `PaperBroker()` (no network calls; `live_api_calls == 0`)
- `BrokerMode.mock` → `MockBroker()` (in-memory fixture fills)
- `BrokerMode.live_disabled` → `raise RuntimeError("live broker mode is disabled in the pre-harness")`

There is no code path that can reach a live broker.

---

## 4. Safety Invariant Status

| Invariant | Status | How enforced | Test |
|---|---|---|---|
| `LIVE_TRADING_ENABLED=false` | ✅ | Hardcoded `False` in `run_smoke`, `run_level_1_2`, `autopilot_status`; `authorize_level5` blocks if env is `true` | `test_level5_refuses_to_run_if_live_trading_env_is_enabled`, `test_api_smoke_route_passes`, `test_safety_invariant_baseline.py` |
| `GUARDED_AUTOPILOT_ENABLED=false` | ✅ | `guarded_autopilot_flag_enabled()` defaults false; `authorize_level4` check 1 | `test_guarded_autopilot_default_disabled` |
| `FULLY_AUTOMATED_OPERATOR_ENABLED=false` | ✅ | `fully_automated_operator_flag_enabled()` defaults false; `authorize_level5` check 1; `allowed_execution_modes` excludes `fully_automated` | `test_level5_run_once_blocks_when_feature_flag_is_disabled`, new baseline tests |
| `MARKET_ORDERS_ENABLED=false` | ✅ | `market_orders_enabled()` defaults false; risk check 12; `UserPolicy.allowed_order_types` defaults to `[limit]` | `test_market_orders_are_blocked_by_default`, `test_level5_authority_blocks_market_orders_when_flag_disabled` |
| `BROKER_MODE=mock` | ✅ | `UserPolicy.broker = BrokerMode.mock` (schema default); `_broker_for_policy` raises for `live_disabled` | `test_mock_broker_completes_account_order_fill_flow`, `test_live_broker_mode_raises_in_broker_for_policy` (new) |
| Kill switch blocks all orders | ✅ | Risk check 1; `authorize_level4` check 2; `authorize_level5` check 3 | `test_kill_switch_blocks_risk_check`, `test_authority_check_short_circuits_on_kill_switch_before_later_checks`, `test_level5_policy_kill_switch_blocks_run` |
| Approval required before submission | ✅ | `submit_order_plan` raises `ApprovalRequired` when `execution_mode == approval_required` and status != `user_approved` | `test_approval_is_required_before_submit_in_approval_required_mode` |
| Risk check required before submission | ✅ | `submit_order_plan` raises `RiskCheckRequired` when `risk_check_id is None` or expired | `test_risk_check_is_required_before_submit` |
| Order state machine enforced | ✅ | `transition_order_plan` validates against `VALID_TRANSITIONS`; raises `InvalidOrderTransition` | `test_audit_logs_are_emitted_on_state_transitions`, integration tests |
| Idempotency deduplication | ✅ | Risk check 13 (`idempotency_key_not_seen`); operator run-once deduplication | `test_duplicate_idempotency_key_is_rejected`, `test_level5_duplicate_run_key_does_not_duplicate_orders` |
| Audit events on all state changes | ✅ | `AuditRecorder.emit()` validates action against `AUDIT_EVENT_ACTIONS` allowlist | `test_audit_logs_are_emitted_on_state_transitions` |
| LLM/Fable5 cannot directly submit orders | ✅ | `submit_order_from_fable5_recipe` raises `DirectOrderSubmissionBlocked` | `test_fable5_recipe_cannot_directly_submit_an_order` |
| Fallback never enables order submission | ✅ | `FallbackDecision.order_submission_enabled = False` hardcoded | `test_no_fallback_ever_enables_order_submission` |
| Monthly loss stops/pauses | ✅ | Risk checks 17–20; `authorize_level4/5` checks | `test_monthly_loss_pause_blocks_new_buys`, `test_monthly_loss_stop_disables_automatic_trading` |
| Stale quotes rejected | ✅ | Risk check 14; authority check `quote_not_stale` | `test_stale_quotes_are_rejected`, `test_level5_stale_market_data_blocks_submission` |

---

## 5. Test Suite Summary

### 5.1 Full suite results

```
python -m pytest quantpilot/tests -v

113 passed in 2.29s
```

Test distribution:

| File | Tests | Coverage area |
|---|---|---|
| `integration/test_smoke.py` | 6 | End-to-end API smoke + error guidance |
| `integration/test_level3_flow.py` | 2 | Level 3 proposal → submit flow |
| `integration/test_level4_guarded_flow.py` | 2 | Level 4 guarded autopilot flow |
| `integration/test_level5_operator_run_once.py` | 24 | Level 5 operator all scenarios |
| `unit/test_authority_checks.py` | 4 | `authorize_level4`, KRX window |
| `unit/test_level3_proposals.py` | 7 | `generate_order_proposals` |
| `unit/test_level5_authority.py` | 9 | `authorize_level5` all checks |
| `unit/test_level5_fallback_manager.py` | 3 | `FallbackManager` matrix |
| `unit/test_level5_policy_versioning.py` | 6 | Policy version drift |
| `unit/test_level5_safety_flags.py` | 1 | `.env.example` has all defaults |
| `unit/test_level5_strategy_registry.py` | 7 | Strategy registry validation |
| `unit/test_level_1_2.py` | 10 | Level 1-2 pipeline + API |
| `unit/test_pre_harness.py` | 16 | Core risk, broker, state machine |
| `unit/test_risk_matrix.py` | 7 | Risk gate checks |
| `unit/test_rl_contract.py` | 2 | RL output typing |
| `unit/test_safety_invariant_baseline.py` | 5 | **New** — baseline invariant gaps |
| `unit/test_strategy_loader_v2.py` | 2 | Strategy YAML loader |

### 5.2 Smoke output

```
python -m quantpilot.jobs.run_smoke

{
  "broker": "mock",
  "execution_mode": "approval_required",
  "live_trading_enabled": false,
  "operator": {
    "fallback": "level5_flag_disabled",
    "live_trading_enabled": false,
    "status": "blocked",
    "submitted_order_plan_ids": []
  },
  "orders": [ {"status": "filled"}, {"status": "filled"}, {"status": "filled"} ],
  "signals": 7,
  "fills": 3,
  "audit_events": 36
}
```

All orders filled via `MockBroker`. Level 5 operator blocked (`level5_flag_disabled`). No live trading path reached.

---

## 6. Gaps Addressed by New Tests

The following invariants had no direct unit test before this stage:

| Gap | New test |
|---|---|
| `_broker_for_policy` raises for `BrokerMode.live_disabled` | `test_live_broker_mode_raises_in_broker_for_policy` |
| `allowed_execution_modes` excludes `fully_automated` without flag | `test_allowed_execution_modes_excludes_fully_automated_by_default` |
| risk check `execution_mode_allowed` blocks `fully_automated` without flag | `test_risk_check_execution_mode_allowed_blocks_fully_automated_without_flag` |
| `autopilot_status` hardcodes `live_trading_enabled=False` in feature_flags | `test_autopilot_status_hardcodes_live_trading_disabled` |
| `run_guarded_autopilot_once` hardcodes `live_trading_enabled=False` | `test_guarded_autopilot_run_once_hardcodes_live_trading_disabled` |

---

## 7. Remaining Risks and Observations

1. **`.env.example` requires manual sync** — `test_level5_safety_flags.py` reads the file at test time. If a flag is added to the code but not to `.env.example`, the doc check fails but the code may silently use the wrong default. Adding a code-level assertion (e.g. in `conftest.py`) would provide a stronger guard.

2. **`HarnessService` is a singleton in `dependencies.py`** — State (e.g. `autopilot_paused`, `last_blocked_reason`) is shared across requests in a running process. In tests this is fine because `HarnessService()` creates a fresh instance. In production-like multi-request scenarios, state leaks between requests. This is a pre-harness limitation, not a safety risk at current scale.

3. **`MARKET_ORDERS_ENABLED` checked in two places** — `gatekeeper.market_orders_enabled()` and `state_machine._market_orders_enabled()` are identical one-liners. If one is ever changed without the other, order-type logic could diverge. A shared utility would eliminate the duplication, but this is out of scope for this stage.

4. **`StrategyRecipe.promotion_status` Literal does not include `validated_l5`** — Level 5 gating is done via `StrategyRegistryEntry`, not `StrategyRecipe`. This is intentional (registry is the L5 gate). However, if someone adds `validated_l5` to the recipe schema without updating the guard logic, the strategy recipe validator could silently accept a broader set. The existing `validate_strategy_permissions` model validator enforces the constraint correctly.

5. **No network-level test of CORS policy** — CORS origins are restricted to `127.0.0.1:5173` and `localhost:5173`. This is code-verified but not test-verified.

---

## 8. Next Recommended Stage

**`docs/fable5_level5_implementation_spec.md` — Level 5 operator implementation**

The baseline is complete. All invariants are covered by tests. The next stage may proceed to implement Level 5 operator logic inside the rails defined by `docs/contracts/operator_contracts.md`, using the registry, policy versioning, fallback matrix, and audit logging already in place.

Before proceeding to Level 5 implementation, confirm:
- [ ] `FULLY_AUTOMATED_OPERATOR_ENABLED` env is explicitly set to `true` in the test environment only
- [ ] No real broker credentials or live endpoints are introduced
- [ ] All new operator code paths are covered by tests before merge
