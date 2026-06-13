# Level 5 Operator Completion Report

Date: 2026-06-13
Implementer: Fable5 (Level 5 implementation lead)
Plan: `docs/level_5_fable5_implementation_plan.md`
Spec: `docs/fable5_level5_implementation_spec.md` + `docs/contracts/operator_contracts.md`

## 1. Summary of implemented Level 5 behavior

A fully automated operator run-once cycle (`OperatorService.run_once`) that is **disabled by default** and only ever reaches MockBroker/PaperBroker. One run: gate chain → deterministic strategy selection from an approved registry → broker snapshot sync → signals from the selected recipe only → Level 3 proposal path → per-order `authorize_level5` (including a fresh deterministic risk check) → submission through the existing order state machine → `OperatorReport` + audit events. Every blocked branch maps to a deterministic fallback. Idempotent run keys replay results without duplicating orders (and re-block if a kill switch engaged since).

With default flags, an operator run is a no-op: `status=blocked`, `fallback=level5_flag_disabled`.

## 2. Files changed

New:
- `quantpilot/packages/core/operator/` (`schemas.py`, `service.py`, `reporting.py`)
- `quantpilot/packages/core/strategies/registry.py`
- `quantpilot/packages/core/policy/versioning.py`
- `quantpilot/packages/core/execution/fallback_manager.py`
- `quantpilot/services/api/routers/operator.py`
- `quantpilot/tests/unit/test_level5_authority.py`
- Docs: operator runbook, fallback matrix, safety checklist, live-trading enablement checklist, strategy promotion policy, implementation plan, this report, `docs/agent_memory/fable5_level5_lessons.md`

Modified (narrow):
- `quantpilot/packages/core/schemas.py` — `UserPolicy.authority_level` upper bound 4→5; new `fully_automated_operator_enabled: bool = False`
- `quantpilot/packages/core/risk/gatekeeper.py` — `allowed_execution_modes()` admits `fully_automated` only when the Level 5 flag (env or policy field) is on; defaults unchanged
- `quantpilot/packages/core/execution/state_machine.py` — `authorize_level5`, `fully_automated_operator_flag_enabled`, `operator_kill_switch_engaged`, `live_trading_flag_enabled`
- `quantpilot/packages/db/audit.py` — new whitelisted operator/versioning audit actions
- `quantpilot/services/api/dependencies.py`, `main.py` — operator service singleton + router
- `quantpilot/jobs/run_smoke.py` — default-blocked operator dry run appended to smoke output
- Tests: 4 pending xfail Level 5 test files implemented and extended; `quantpilot/tests/fixtures/operator_fallback_cases.json` expanded 3→19 cases

## 3. Operator loop behavior

Gate order in `run_once`: (0) idempotent replay (kill-switch-aware) → (1) Level 5 flag → (2) policy exists → (3) `LIVE_TRADING_ENABLED` must be false → (4) policy + env kill switches → (5) broker mode mock/paper + run-mode/broker match → (6) policy version match (`PolicyVersionGuard`) → (7) promotion (`authority_level=5` and `execution_mode=fully_automated`) → strategy selection → (8) monthly loss stop after broker snapshot sync → planning → per-order `authorize_level5` → submission via `HarnessService.submit_order_plan` (which runs a second fresh risk check). `dry_run` evaluates everything but submits nothing. Broker exceptions fail the order, pause the harness, and end the run with `broker_failure`.

## 4. Strategy registry and promotion behavior

`StrategyRegistryEntry` is the authority record (statuses `draft/validated_l3/validated_l4/validated_l5/disabled/revoked` + `allowed_execution_levels`). Level 5 selection requires status `validated_l5` **and** a `level_5`/`fully_automated` execution level; lowest priority wins deterministically; optional policy-version bounds. The default registry contains no `validated_l5` entry, so a default run cannot submit even with flags forced on. Deterministic underperformance rules demote (l5→l4→l3→disabled, stripping earned levels) or disable. Details: `docs/operator_strategy_promotion_policy.md`.

## 5. Fallback matrix

19 deterministic reason codes in `FALLBACK_MATRIX`, unknown codes degrade to no-op, `order_submission_enabled=false` on every row (asserted by test). Conditional spec row implemented: no L5 strategy → level 4 if a guarded-ready strategy exists, else level 2 (`no_approved_strategy_available`). Full table + mapping notes: `docs/operator_fallback_matrix.md`.

## 6. Safety controls and feature flag defaults

- `LIVE_TRADING_ENABLED=false` (if ever true, the operator **refuses to run**: `live_trading_flag_engaged`)
- `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock` (documented in `.env.example`, guarded by `test_level5_safety_flags.py`)
- `OPERATOR_KILL_SWITCH` env switch blocks all runs including idempotent replays; policy kill switch likewise
- Single submission path: only `HarnessService.submit_order_plan` calls `broker.submit_order`
- No LLM/RL output reaches a broker; reports render deterministically without an LLM

## 7. Policy versioning behavior

`PolicyVersionGuard.require_current_version` blocks runs on version drift (`policy_review_required`). `PolicyVersioningService.propose_update/confirm_update`: every update creates a `PolicyVersionChange` with version+1; material fields (risk limits, broker, execution mode, authority, flags, order caps) stay **pending** until the exact confirmation phrase `"confirm policy update"`; non-material changes apply immediately with a version bump; merged policies are re-validated through `UserPolicy` validators; before/after states and `execution_mode_updated` are audit-logged.

## 8. Tests added or updated

108 tests total (baseline 64, all 4 pending Level 5 xfails implemented, ~44 new/extended). Coverage maps to the 21 required scenarios: Mock/Paper run-once, registry refusals (draft/disabled/revoked/guarded-only), policy versioning (new version, explicit confirmation, execution-mode audit), monthly loss pause (blocks new buys; sells still allowed) and stop, both kill switches, broker failure fallback + pause, stale data, deterministic no-LLM reports (byte-identical re-render), underperformance demote/disable, risk-check expiry on the L5 path, missing/duplicate idempotency keys, duplicate run keys, market-order block, live-trading default-off (doc + runtime refusal + API), full Level 0-4 regression. Post-audit additions: kill-switch-aware replay test, empty-registry → level 2 test, fixture-wired tests (`operator_policy.json`, `operator_portfolio_snapshot.json`, `operator_strategy_registry.json`), 19-case fallback fixture, unknown-code default test.

## 9. Commands run and results

- `python -m pytest quantpilot/tests` → **108 passed** (final run 2026-06-13, ~1.3s; baseline before implementation: 64 passed with 4 xfail)
- `python -m quantpilot.jobs.run_smoke` → PASS; operator section: `{"status": "blocked", "fallback": "level5_flag_disabled", "submitted_order_plan_ids": [], "live_trading_enabled": false}`
- Subagent audits: risk-gate audit (no high-severity findings; low findings fixed or documented below) and test audit (3 weak spots closed; fixture gaps closed)

## 10. Known limitations

- `submit_order_plan`'s fresh risk check uses `fixture_portfolio_snapshot()`, not the broker-synced snapshot the operator gated on. Identical for mock/paper brokers today; must be threaded through before any real broker adapter exists.
- The operator's broker-exception handler catches broad `Exception` and labels it `broker_failure` (fail-safe: pauses harness, stops the run), which can mask non-broker programming errors in the label.
- A failure after broker `accepted` leaves the order plan in a non-terminal state (no resubmission possible; idempotency keys cover `accepted`).
- `operator_market_regime.json` is reserved: no regime-aware selection is implemented yet (selection inputs are status, levels, priority, policy-version bounds).
- Market-order blocking is covered at the authority-check unit level only; the planner emits limit orders exclusively, so no end-to-end market-order run exists.
- The NL policy parser maps any text containing "fully"/"automated" to `execution_mode=fully_automated`; fail-closed (authority/flag gates still block) but worth tightening.
- `OperatorReport.live_trading_enabled` is structurally hardcoded `false` (no live path exists); the runtime guard is the `live_trading_flag_engaged` refusal.

## 11. Live trading status

**Disabled. Not implemented. No live broker mode exists in the codebase.** `LIVE_TRADING_ENABLED=true` causes the operator to refuse to run. See `docs/live_trading_enablement_checklist.md` — all items unchecked and human-gated.

## 12. Human review checklist before any real broker integration

1. Read `docs/live_trading_enablement_checklist.md` end to end; every item needs a named human owner.
2. Re-audit the single-submission-path invariant (`broker.submit_order` call sites).
3. Thread the authorization snapshot into `submit_order_plan` (limitation #1).
4. Narrow the operator's broker-exception handling (limitation #2) and add terminal-state recovery for post-`accepted` failures (limitation #3).
5. Replace fixture OHLCV/quotes with a real data source carrying staleness guarantees; revisit `stale_quote_max_age_seconds`.
6. Replace the simplified KRX window check with an exchange calendar (holidays, halts).
7. Design secret management before writing any credentialed adapter; verify nothing reaches logs/reports/audit events.
8. Re-run both subagent audits (risk-gate, test) and the full suite; require a human sign-off recorded in this repo.
