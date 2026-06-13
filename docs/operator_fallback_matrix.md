# Operator Fallback Matrix (Level 5)

Source of truth in code: `quantpilot/packages/core/execution/fallback_manager.py` (`FALLBACK_MATRIX`).
Fixture mirror used by tests: `quantpilot/tests/fixtures/operator_fallback_cases.json`.

Levels: `0` = no-op, `2` = suggestions/reports only, `3` = approval-based proposals only, `4` = guarded autopilot remains available. `order_submission_enabled` is **always false** — a fallback can lower authority, never grant it.

| reason_code | to_level | Triggered by |
|---|---|---|
| `level5_flag_disabled` | 0 | `FULLY_AUTOMATED_OPERATOR_ENABLED` false and policy field false (default state) |
| `kill_switch_engaged` | 0 | `policy.kill_switch_engaged` |
| `operator_kill_switch_engaged` | 0 | `OPERATOR_KILL_SWITCH=true` env switch |
| `live_trading_flag_engaged` | 0 | `LIVE_TRADING_ENABLED=true` — operator refuses to run at all |
| `policy_review_required` | 0 | requested policy version != active policy version |
| `policy_not_found` | 0 | no active policy for the requested id |
| `monthly_loss_stop_engaged` | 0 | snapshot monthly loss <= `monthly_loss_stop_all_autotrading` |
| `broker_mode_unsafe` | 0 | policy broker is not mock/paper |
| `run_mode_broker_mismatch` | 0 | `mock_submit` with non-mock broker, or `paper_submit` with non-paper broker |
| `policy_not_promoted` | 4 | authority_level != 5 or execution_mode != fully_automated |
| `no_level5_strategy_eligible` | 4 | no `validated_l5` registry entry, but a guarded-ready (L4) strategy exists |
| `no_approved_strategy_available` | 2 | no `validated_l5` entry **and** no guarded-ready strategy either |
| `monthly_loss_pause_engaged` | 3 | monthly loss pause blocks a new automatic buy |
| `stale_market_data` | 3 | quote older than `stale_quote_max_age_seconds` at authorization |
| `broker_unhealthy` | 3 | broker heartbeat unhealthy in guardrail state |
| `broker_failure` | 3 | broker adapter raised during submission; harness paused |
| `market_orders_disabled` | 3 | market order requested while `MARKET_ORDERS_ENABLED=false` |
| `risk_check_failed` | 2 | deterministic risk gate failure (proposal stage, fresh authorize-stage check, or expired risk check at submission) |
| `llm_unavailable` | 2 | LLM unavailable; deterministic template reports continue |
| *(unknown code)* | 0 | any unmapped blocker degrades to the safest outcome: full no-op |

## Mapping notes vs the spec matrix

- Spec rows "risk check failure" and "market order requested while disabled" call for "no submission and blocked decision". The operator records the per-order **block decision** in the report *and* the matrix assigns a degradation level (2 and 3 respectively) so callers know which capability tier remains trustworthy. Submission stays disabled in both cases.
- Spec row "no Level 5 strategy eligible → Level 4 if available, else Level 2 suggestions" is implemented conditionally in `OperatorService.run_once` using `StrategyRegistry.level4_available()` (codes `no_level5_strategy_eligible` vs `no_approved_strategy_available`).
- Run status mapping: `to_level == 0` → result status `blocked`; `to_level > 0` → result status `fallback`. A run that submitted at least one order reports `completed` even if later orders were individually blocked (the report keeps every block decision).
