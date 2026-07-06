# QuantPilot Status

Last updated: 2026-07-06 (session: claude/status-md-continuation-zshmzd).
This file is committed so progress survives ephemeral session containers.
Update it at the end of every working session.

## Where we are

Fixture-first pre-harness with Levels 1–5 rails implemented and all Level 5
safety defaults intact (operator stays `blocked` / `level5_flag_disabled` in
smoke output). Real-data migration steps completed so far:

| Step | Scope | Report |
| --- | --- | --- |
| 01–03 | Real-data baseline, backtest validation | `docs/real_data_migration_baseline.md`, `docs/stage_03_backtest_validation_report.md` |
| 04 | Provider-bound signals | `docs/step_04_provider_bound_signals_report.md` |
| 05 | Portfolio optimizer (provider-bound planning) | `docs/step_05_portfolio_optimizer_report.md` |
| 06 | Batch risk gate | `docs/step_06_batch_risk_gate_report.md` |
| 07 | Ranked candidate universe | `docs/step_07_candidate_ranking_report.md` |
| 08 | Calibrated multi-factor signal model | `docs/step_08_calibrated_multifactor_signal_model_report.md` |
| 09 | Walk-forward validation evidence layer | `docs/step_09_walk_forward_validation_report.md` |
| 10 | Simulator-only execution layer (TWAP/VWAP/POV) | `docs/step_10_execution_simulator_report.md` |
| 11 | Execution simulation preview endpoint | `docs/step_11_execution_preview_report.md` |
| 12 | Calibrated proxy → optimizer adapter (fail-closed) | `docs/step_12_calibrated_proxy_optimizer_adapter_report.md` |

Frontend: Level 5 operator page and visual design pass landed (`c15f9be`).

## Verification snapshot

- `python -m pytest quantpilot/tests` → 294 passed, 1 skipped (2026-07-06).
- `python -m quantpilot.jobs.run_smoke` → passed; operator `blocked` /
  `level5_flag_disabled`, `live_trading_enabled=false`.

## Next steps (in recommended order)

1. **Step 13 — wire calibrated sets into harness planning.** Pass the
   provider-bound signal path's `CalibratedSignalSet` through
   `HarnessService.create_portfolio_plan` (adapter exists as of Step 12; only
   the plumbing plus fail-closed integration tests remain).
2. **Step 14 — promotion evidence integration.** Surface the Step 09
   `PromotionEvidenceReport` through the strategy lifecycle registry so
   promotion review consumes walk-forward evidence (promotion stays
   human-gated; `promotion_allowed=false` by design).
3. **Step 15 — replace Step 09 diagnostic placeholders.** Implement PBO and
   deflated Sharpe ratio diagnostics behind the existing
   `DiagnosticPlaceholder` schema.
4. Frontend: expose the `/api/orders/{id}/simulate` preview on the orders UI
   (advisory display only, no submission changes).

## Open items / decisions needed

- Unmerged divergent branches `origin/codex/step-03-snapshot-threading`
  (snapshot threading, policy compiler, LOB spread pricing, recovery safety)
  and `origin/codex/quantpilot-backend-core-refactor` conflict heavily with
  the step 04–08 lineage on `main`. Decide whether to cherry-pick specific
  pieces (snapshot threading is the most valuable — see
  `docs/agent_memory/fable5_level5_lessons.md` on `submit_order_plan`
  re-checking against `fixture_portfolio_snapshot()`) or abandon them.
- `origin/codex/step-10-execution-simulator` is now merged into this branch;
  it can be deleted after this branch merges to `main`.

## Session conventions

- Read `docs/agent_memory/fable5_level5_lessons.md` before touching audit
  actions, order state machine, or authorization clocks.
- Add new audit actions to `AUDIT_EVENT_ACTIONS` before emitting them.
- Ground every progress claim in `python -m pytest quantpilot/tests` and
  `python -m quantpilot.jobs.run_smoke` output; the smoke operator section
  must stay `blocked` / `level5_flag_disabled` by default.
