# Fable5 Level 5 Implementation Lessons

Durable, non-obvious lessons from the 2026-06-12/13 Level 5 implementation run. (No secrets; nothing here duplicates README/AGENTS.)

## Repository conventions that matter

- The audit recorder whitelists actions (`quantpilot/packages/db/audit.py::AUDIT_EVENT_ACTIONS`) and **raises** on unknown actions. Add every new action to the whitelist *before* emitting it, or runs fail at runtime (fail-closed, caught as a broker_failure fallback in the operator).
- Pending-feature tests are committed as `xfail(strict=False)` with exact import paths — they are executable specs. Implement the module at that path, then delete the marker.
- `HarnessModel` (`extra="forbid"`) is the base for all Pydantic models; fixtures in `quantpilot/tests/fixtures/*.json` are written to construct these models directly.
- The Bash tool on this machine runs POSIX sh, not PowerShell, despite the env saying PowerShell — use `tail`/`grep`, not `Select-Object`.
- `.env.example` is read-blocked by permission settings; its content is verifiable indirectly via `test_level5_safety_flags.py`.

## Safety invariants that were easy to violate

- `UserPolicy.authority_level` was capped `le=4` and the risk gatekeeper's allowed execution modes excluded `fully_automated` — naively setting `execution_mode=fully_automated` makes **every** risk check fail. The fix is an env/policy-flag-gated widening (`allowed_execution_modes()`), keeping defaults byte-identical.
- Passing the run's start time as the authorization `now` makes every quote look stale (negative age), because proposals are created *after* the run starts. Authorization must use decision-time wall clock; tests inject `now` only to simulate staleness.
- Idempotent run replay can mask a kill switch engaged after the cached run — replay must re-check kill switches first.
- `harness_service.submit_order_plan` re-risk-checks against `fixture_portfolio_snapshot()`, not the snapshot the operator gated on. Identical for mock/paper brokers today; a real broker adapter would need the snapshot threaded through.

## Architecture decisions confirmed by implementation

- Registry-as-authority works: `StrategyRegistryEntry` (separate from `StrategyRecipe`) carries Level 5 eligibility; `StrategyRecipe.promotion_status` cannot express `validated_l5`, so a recipe can never self-promote. `authorize_level5` cross-checks recipe id == registry id.
- Default-deny composition: default registry has no `validated_l5` entry, so even with all flags forced on, a default run falls back instead of submitting. Tests rely on this (`test_level5_default_registry_has_no_eligible_strategy`).
- Fallback policy as a data table (`FALLBACK_MATRIX`) with an unknown-code → no-op default kept the test fixture and docs trivially in sync.

## Test commands

- `python -m pytest quantpilot/tests` (108 tests, ~2s) and `python -m quantpilot.jobs.run_smoke` are the only verification commands; the smoke output's `operator` section must stay `blocked/level5_flag_disabled` by default.
- KRX auto-order window is wall-clock dependent; integration tests monkeypatch `quantpilot.packages.core.execution.state_machine.is_krx_auto_order_window`. Risk-check expiry is testable by monkeypatching `quantpilot.packages.core.harness_service.utc_now` forward (+20min) — only the submission-time clock shifts.
