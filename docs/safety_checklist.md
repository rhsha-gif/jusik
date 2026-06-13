# Safety Checklist (Level 5 Operator)

Run through this list before enabling any operator capability, and again after any change to policy, registry, flags, or broker configuration.

## Defaults that must never drift

- [ ] `.env.example` documents: `LIVE_TRADING_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock` (guarded by `test_level5_safety_flags.py`).
- [ ] `UserPolicy` defaults: `authority_level=2`, `execution_mode=approval_required`, `broker=mock`, `kill_switch_engaged=false`, `guarded_autopilot_enabled=false`, `fully_automated_operator_enabled=false`.
- [ ] Default strategy registry contains **no** `validated_l5` entry (`default_strategy_registry()`), so a default run can never submit even with flags forced on.
- [ ] `python -m quantpilot.jobs.run_smoke` shows the operator section blocked with `level5_flag_disabled` and `"live_trading_enabled": false`.

## Before enabling FULLY_AUTOMATED_OPERATOR_ENABLED (mock/paper only)

- [ ] Full test suite green: `python -m pytest quantpilot/tests`.
- [ ] The target policy was promoted through the versioning flow (`PolicyVersioningService`) with the explicit confirmation phrase (`confirm policy update`) — never by editing fields in place.
- [ ] Active policy version is known and will be passed as `requested_policy_version`.
- [ ] The strategy intended for Level 5 is `validated_l5` in the registry with `level_5`/`fully_automated` in its allowed levels, and its recipe file exists.
- [ ] Loss limits reviewed: daily limit, monthly pause, monthly stop are at intended values.
- [ ] Kill-switch paths verified reachable: `POST /api/autopilot/kill-switch` and `OPERATOR_KILL_SWITCH=true`.
- [ ] First run is `run_mode="dry_run"`; inspect the report before any `mock_submit`.

## Invariants enforced in code (verify after any refactor)

- [ ] Only `HarnessService.submit_order_plan` calls `broker.submit_order` (single submission path, state machine + fresh risk check + idempotency).
- [ ] `authorize_level5` re-checks flag, kill switches, broker mode, promotion, version, quote freshness, registry status, order type, loss limits, conflicts, idempotency, and a fresh risk check per order.
- [ ] No LLM or RL output reaches a broker: RL contract limits outputs to `target_weight_delta`/`strategy_selection`; reports render deterministically without an LLM.
- [ ] Audit recorder whitelist rejects unknown actions (fail-closed).
- [ ] Every fallback row has `order_submission_enabled=false`.

## Out of bounds — do not do

- Do not add live broker credentials, secrets, or a live `BrokerMode` value.
- Do not widen `stale_quote_max_age_seconds`, loss limits, or order caps to "make a run pass".
- Do not bypass the policy versioning confirmation phrase for material fields.
- Do not mark a strategy `validated_l5` without the promotion evidence in `docs/operator_strategy_promotion_policy.md`.
