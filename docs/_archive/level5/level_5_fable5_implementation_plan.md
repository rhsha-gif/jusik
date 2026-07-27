# Level 5 Fable5 Implementation Plan

Date: 2026-06-12
Owner: Fable5 (Level 5 implementation lead)
Spec: `docs/fable5_level5_implementation_spec.md`, `docs/contracts/operator_contracts.md`

## Baseline (verified in this session)

- `python -m pytest quantpilot/tests` -> 64 tests, all green, 4 XFAIL pending Level 5 targets:
  - `test_level5_operator_run_once.py::test_level5_run_once_blocks_when_feature_flag_is_disabled`
  - `test_level5_fallback_manager.py::test_fallback_manager_maps_known_level5_blockers`
  - `test_level5_policy_versioning.py::test_policy_version_mismatch_blocks_automatic_submission`
  - `test_level5_strategy_registry.py::test_strategy_registry_selects_only_validated_level5_entries`
- `.env.example` already documents all five disabled safety flags (verified by passing `test_level5_safety_flags.py`).
- Level 3/4 rails: `HarnessService` (proposals, guarded run-once, kill switch), `authorize_level4`, `run_risk_check`, order state machine, audit whitelist.

## Files to add

| File | Purpose |
|---|---|
| `quantpilot/packages/core/strategies/registry.py` | `StrategyRegistryEntry`, `StrategySelectionDecision`, `StrategyRegistry` (Level 5 selection, demotion/disable rules), `default_strategy_registry()` (no `validated_l5` entry by default) |
| `quantpilot/packages/core/policy/versioning.py` | `PolicyReviewRequest`, `PolicyVersionChange`, `PolicyVersionGuard`, `PolicyVersioningService` (propose/confirm flow; material changes require explicit confirmation) |
| `quantpilot/packages/core/execution/fallback_manager.py` | `FallbackDecision`, `FallbackManager` with the deterministic reason-code matrix |
| `quantpilot/packages/core/operator/__init__.py`, `schemas.py`, `service.py`, `reporting.py` | `OperatorRunRequest/Result`, `OperatorDecision`, `OperatorReport`, `OperatorService.run_once`, deterministic plain-text report renderer |
| `quantpilot/services/api/routers/operator.py` | `POST /api/operator/run-once`, `GET /api/operator/status` |

## Files to modify (narrow diffs)

| File | Change |
|---|---|
| `quantpilot/packages/core/schemas.py` | `UserPolicy.authority_level` upper bound 4 -> 5; add `fully_automated_operator_enabled: bool = False` |
| `quantpilot/packages/core/risk/gatekeeper.py` | allow `ExecutionMode.fully_automated` in `execution_mode_allowed` only when `FULLY_AUTOMATED_OPERATOR_ENABLED=true` |
| `quantpilot/packages/core/execution/state_machine.py` | add `fully_automated_operator_flag_enabled`, `operator_kill_switch_engaged`, `authorize_level5` (mirrors `authorize_level4`, plus registry-entry authority checks) |
| `quantpilot/packages/db/audit.py` | add operator/policy-versioning audit actions to the whitelist |
| `quantpilot/services/api/dependencies.py`, `main.py` | operator service singleton + router registration |
| `quantpilot/jobs/run_smoke.py` | append default-blocked operator dry-run section to smoke output (existing keys unchanged) |
| 4 pending Level 5 test files | remove `xfail` markers; extend coverage |

## Safety gates (unchanged invariants)

- All five env flags default false / mock; Level 5 refuses to run unless `FULLY_AUTOMATED_OPERATOR_ENABLED=true` **and** policy field/authority promoted **and** kill switch off.
- `OPERATOR_KILL_SWITCH=true` (env) or `policy.kill_switch_engaged` blocks all automatic trading.
- Submission only via `HarnessService.submit_order_plan` (state machine + fresh deterministic risk check + idempotency). No new order path.
- RL/LLM outputs never reach a broker; registry + deterministic selector constrain strategy choice.
- Default registry has **no** `validated_l5` entry, so even with flags forced on, a default run falls back (no submission).

## Operator run-once pipeline

1. Idempotent run-key dedup (same key -> cached result, no duplicate orders).
2. Gate chain (each failure -> audit + deterministic fallback): operator flag -> policy exists -> live-trading env must be false -> kill switch -> broker mode safe -> run-mode/broker consistency -> policy version match (`PolicyVersionGuard`) -> policy promoted (authority 5 + `fully_automated`) -> registry selection.
3. Signals from the **selected registry strategy's recipe only**; portfolio snapshot synced from Mock/Paper broker; Level 3 proposal path reused.
4. Per proposal: `authorize_level5` (includes fresh risk check) -> dry-run records decisions only; mock/paper submit via existing `submit_order_plan`; broker exception -> `broker_failure` fallback + pause.
5. `OperatorReport` per contract + deterministic text rendering (works with no LLM); audit events for every material branch.

## Tests to add (mapped to prompt requirements 1-21)

Unit: registry selection/refusals/demotion (3,4,5,15), versioning propose/confirm/audit (6,7,8), fallback matrix (deterministic codes), `authorize_level5` ordering incl. market-order block (19), risk-check expiry (16), missing idempotency key (17, if not already covered).
Integration: run-once with MockBroker (1) and PaperBroker (2), flag-disabled default (20), kill switch (11), monthly pause/stop via monkeypatched broker snapshot (9,10), stale data (13), broker failure fallback (12), duplicate run key (18), deterministic report without LLM (14), full regression (21).

## Verification commands

- `python -m pytest quantpilot/tests`
- `python -m quantpilot.jobs.run_smoke`

## Risks

- KRX auto-order window check is wall-clock dependent: integration tests monkeypatch `state_machine.is_krx_auto_order_window` for the submission happy path (same seam `authorize_level4` exposes via `now`).
- Audit whitelist raises on unknown actions; every new emit must be added there first.
