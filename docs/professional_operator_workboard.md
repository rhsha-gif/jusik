# QuantPilot Professional Operator Workboard

> Canonical execution board for the Codex + Claude Code collaboration.
> Both agents must read this file before claiming work and update it at handoff checkpoints.
> Detailed historical reports stay in their existing files; `docs/STATUS.md` is updated only after a stage closes.

## Document edit lease

- Lease status: `free`
- Document editor: `none`
- Task ID: `none`
- Lease acquired at (KST): `none`

Only one agent may edit this document at a time. Claim the lease, re-read the current file, make the smallest
status/evidence/log update, and release it immediately. Codex alone may recover a lease older than 15 minutes
after checking that Claude Code is no longer editing.

## Objective

Build a fixture-first, single-strategy/multi-symbol professional portfolio operator that performs deterministic
technical selection, entry timing, protective exits, strategy retirement, and weekly rebalancing, then validates
the same decisions against KIS paper trading. Live trading remains out of scope and disabled by default.

## Non-negotiable safety invariants

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`
- No secrets, account identifiers, or live broker endpoints in the repository.
- External connectors use fake-client unit tests and explicitly skipped/manual integration tests only.
- LLM/RL/Claude output never creates, approves, or submits broker orders directly.
- Every submission continues through the existing risk gate, order state machine, audit log, idempotency checks,
  kill switches, and reconciliation path.

## Locked operating decisions

- Scope: one approved strategy operating multiple symbols; multi-strategy allocation is deferred.
- Candidate path: policy/blocklist/liquidity/data-quality filters, rank at most 20 candidates, hold no more than
  `UserPolicy.max_positions` (default 8).
- Indicators: SMA20, SMA120, RSI14, ATR14, 20-session volume ratio, and the existing multi-factor score.
- Entry: completed close above SMA120; prior RSI below 35 and current RSI at least 35; volume ratio at least 1.05;
  multi-factor score at least 68; realtime premium no more than 0.5%; quote age no more than 30 seconds.
- Initial protective stop: `max(average_entry_price * 0.92, average_entry_price - 2 * ATR14)`.
- Technical exit: full exit at completed close at or below 94% of SMA20; trim 50% at RSI at least 72 or
  close at least 120% of SMA20.
- Strategy review: pause and require reapproval above 1.5x backtest MDD; disable at 20% MDD or -10% benchmark
  excess return.
- Cadence: protective-risk evaluation every minute during a paper session; ordinary rebalance weekly with a
  1 percentage-point no-trade band.
- Retirement: block new buys immediately, liquidate risk exits first, then liquidate remaining attributed
  positions with marketable limit orders. Never fall back to market orders.
- Runtime target: local Windows; final external boundary is KIS paper trading, never live trading.

## Collaboration rules

1. Read `AGENTS.md`, `CLAUDE.md`, this workboard, and `git status` before starting.
2. Each agent may own at most one `in_progress` task.
3. A path may belong to only one active task. Do not edit another task's paths.
4. Claude Code edits only pure technical/signal/position-risk modules plus their focused tests and fixtures.
5. If Claude needs a schema, repository, operator, broker, API, job, UI, or documentation change, add an
   integration request here instead of editing that path.
6. Codex owns contracts, integration, review, Git staging, commits, and merges. Claude Code must not stage,
   commit, reset, clean, or move files.
7. Move a task to `review` only after recording exact targeted-test output. Codex moves it to `done` only after
   reviewing the diff and running the required broader checks.
8. Existing user changes are never overwritten or included in a task without explicit ownership.

## Current baseline

- Baseline timestamp (KST): `2026-07-10 06:03`
- Backend: `324 passed, 1 skipped in 5.02s`
- Smoke: `passed; broker=mock; operator=blocked/level5_flag_disabled; live_trading_enabled=false`
- Frontend: `20 passed; production build passed`
- Live trading enabled: `no`
- Validation broker: `mock`
- Existing uncommitted user paths at kickoff:
  - `.env.example`
  - `docs/STATUS.md`
  - `quantpilot/apps/web/.gitignore`
  - `quantpilot/services/api/main.py`
  - `quantpilot/services/api/routers/execution.py`
  - `CLAUDE.md.20260705.bak`
  - `quantpilot/services/api/routers/notifications.py`
  - `quantpilot/services/api/routers/strategy_studio.py`
  - `quantpilot/services/api/routers/strategy_tickets.py`
  - `quantpilot/tests/unit/test_api_cors.py`

These paths remain unowned unless a later task explicitly adopts them after Codex reviews the overlap.

## Active focus

- Codex: `QP-060 in_progress` — professional operator status/report UI and final hardening
- Claude Code: `idle/done` — QP-110 and QP-120 reviewed; no additional Claude task is necessary
- Next integration gate: `GATE-4` — durable operator visibility without exposing secrets or enabling live trading

## Work queue

| Task ID | Owner | Depends on | Owned paths | Status | Acceptance | Evidence / handoff |
|---|---|---|---|---|---|---|
| QP-000 | codex | none | workboard, `AGENTS.md`, `CLAUDE.md` | done | Both agents have read-first and ownership rules; fresh baseline recorded | backend 324/1; smoke mock/default-blocked; frontend 20/build passed |
| QP-010 | codex | QP-000 | operator decision contract, contract tests, fixtures | done | Exact pure-function inputs/outputs and fail-closed tests exist without implementing the engine | targeted: 3 passed, 16 xfailed; full suite after initial rails: 325 passed, 1 skipped, 8 xfailed |
| QP-020 | codex | QP-010 | repository and managed-position persistence modules/tests | done | Paper-mode state survives restart; idempotency remains unique; fixture defaults unchanged | 8 focused tests passed; SQLite WAL/full-sync, restart recovery, stale-write and secret-field guards verified |
| QP-110 | claude | QP-010 | technical indicators, pure pullback signal module, focused tests/fixtures | done | Selected strategy emits deterministic, no-lookahead actions using the locked thresholds | Codex-reviewed targeted suite: 12 passed; canonical rules shared with v2 typed schema |
| QP-030 | codex | QP-020, QP-110 | schemas, signal adapter, portfolio optimizer/planner, operator integration | done | Selected strategy alone drives plans; max positions and existing safety gates are enforced | post-review backend 379 passed/1 skipped/7 QP-120 xfailed; smoke mock/default-blocked; selected-v2 integration and blocked-no-sell proofs passed |
| QP-120 | claude | QP-110 | pure position-risk evaluator and focused tests | done | 8%/2ATR stop and technical exit/trim precedence are deterministic and broker-free | Codex-reviewed targeted: 10 passed; completed-close contract and raw-boundary comparison added; backend 388 passed/1 manual skip |
| QP-040 | codex | QP-030, QP-120 | strategy performance, retirement, liquidation, rebalance orchestration | done | Retirement creates no buys; risk-reducing sells remain auditable and idempotent | independent audits found no P0/P1; backend 568 passed/1 skipped; smoke mock/default-blocked/live=false |
| QP-050 | codex | QP-040 | KIS paper adapters, reconciliation, session job, fake-client tests | done | Paper dispatch has a process-kill-safe journal and broker-query reconciliation; fixture/paper provenance is durable and cross-mode reuse fails closed; no production host is accepted; fake tests pass; manual paper test stays opt-in | independent final audits found no P0/P1; backend 757 passed/2 skipped; smoke mock/default-blocked/live=false; paper job default-disabled/no-network |
| QP-060 | codex | QP-050 | operator status/report UI and final hardening | in_progress | Safety state, position risk, strategy health, rebalance, and reconciliation are visible | claimed after QP-050 gate; clean existing operator API/page paths selected |
| QP-900 | codex | QP-060 | completion report and final verification only | blocked | Full acceptance audit passes; all required evidence is current | pending |

## Integration requests

- QP-030: provider-bound signal flow must pass real completed history and actual `Quote.as_of`; never reuse
  `OrderIntent.quote_time` as market-data freshness evidence.
- QP-030: pass the reconciled broker snapshot into signal evaluation so current weights are real; the selected v2
  recipe must fail closed when typed rules, completed history, or a per-symbol quote is missing and must never fall
  back to the legacy classifier.
- QP-030: build the provisional multi-factor score before the final pullback decision, expose only the final
  decision to planning, and ignore future/incomplete bars.
- QP-030: enforce `UserPolicy.max_positions`, replace the professional path's missing-quote `100.0` fallback with
  fail-closed behavior, and use the locked 0.01 rebalance band instead of the legacy 0.001 default.
- QP-040: risk-reducing liquidation sells must not be rejected together with failed buys or blocked solely by a
  monthly buy/automation pause; they still require fresh quotes, idempotency, state-machine, and audit checks.
- QP-040: normalize drawdown as a positive ratio and use a pure health decision: disable at MDD `>= 0.20` or
  excess return `<= -0.10`; pause only when MDD is strictly above `1.5 * backtest MDD`; missing benchmark blocks
  buys without forcing liquidation.
- QP-040: add an auditable order purpose and independently verify that a claimed protective/retirement order is a
  sell that cannot exceed the reconciled long position. Isolate such orders from ordinary buy batches; terminal
  order states must never re-enter a submit batch.
- QP-040: retirement uses fresh best-bid marketable limit orders only, advances only after reconciliation, evaluates
  protective risk at one-minute cadence, and runs ordinary rebalance at most once per ISO week.
- QP-050: thread the reconciled broker snapshot through the final submission-time risk check instead of silently
  substituting `fixture_portfolio_snapshot()`.
- QP-050: persist a general paper-order submission journal before any external POST, record dispatch/accepted/
  `outcome_unknown`, reconcile by broker query after restart, and never blindly resend an uncertain order.
- QP-050: persist fixture/paper provenance at database, session, and relevant row boundaries; reject cross-mode
  reopen or reuse before a KIS paper adapter can read state or submit an order.
- QP-060: expose a secret-free, read-only professional status projection from the existing operator API route;
  the dashboard must never migrate or mutate the paper database and missing/stale evidence must not render green.
- QP-060: surface unresolved paper dispatches and local recovery gaps; retain explicit manual resolution for
  orders older than the broker's historical query window instead of allowing one old row to hide current status.

## Blockers and human inputs

- KIS paper credentials are intentionally absent. They are needed only for the manual integration/soak phase,
  never for unit tests or implementation of the fake-client boundary.
- A local Windows machine must remain awake for an actual paper-session worker.

## Verification policy

- Backend change: targeted tests, then `python -m pytest quantpilot/tests`.
- Smoke/orchestration change: `python -m quantpilot.jobs.run_smoke`.
- Frontend change: `npm run test` and `npm run build` from `quantpilot/apps/web`.
- KIS external check: manual and skipped unless `RUN_KIS_MANUAL_INTEGRATION=1` is explicitly set.
- A failed check is recorded with its root cause; safety tests are never weakened to obtain green output.

## Checkpoint log

- 2026-07-10 KST — Codex — QP-000 started. Preserving the pre-existing dirty worktree and installing the
  shared workboard before any feature implementation.
- 2026-07-10 06:04 KST — Codex — QP-000 done. Verified backend `324 passed, 1 skipped`, smoke with
  `broker=mock` and Level 5 default-blocked, frontend `20 passed`, and production build success. QP-010 claimed.
- 2026-07-10 KST — Codex — QP-010 done. Added broker-free pullback and position-risk contracts, Wilder
  indicator definitions, deterministic fixtures, import-boundary checks, and 16 executable xfail targets.
  Independent audit integration findings were recorded. QP-020 claimed and QP-110 released to Claude Code.
- 2026-07-10 KST — Codex — QP-020 done in parallel with Claude QP-110. Added opt-in SQLite paper-state
  persistence and a secret-free managed-position/run-checkpoint ledger. Focused verification: `8 passed`.
- 2026-07-10 06:40 KST — Codex + Claude Code — QP-030 independent slices started while Claude implements QP-110
  in `.claude/worktrees/qp110-pullback-signal`. Integration audit fixed the required data path: completed history,
  broker current weights, actual quote price/time, pre-decision multi-factor score, max-position cap, and explicit
  no-fabricated-quote behavior. Codex will review and copy only the allowed QP-110 diff into the main workspace.
- 2026-07-10 07:05 KST — Codex — QP-110 and QP-030 done. Integrated and reviewed Claude's Wilder
  SMA/RSI/ATR/volume engine, added an exact first-session ATR seed proof, typed and locked the v2 rules, preserved
  the v1 spec hash, normalized completed history, carried actual broker weights and quote timestamps through the
  selected-v2 signal and plan path, enforced the 20-candidate/8-position limits, and made missing professional
  quotes fail closed. Verification: `364 passed, 1 skipped, 7 xfailed` (all remaining xfails belong to QP-120),
  smoke passed with mock broker and Level 5 default-blocked. QP-120 started in Claude session `ac78fee1`.
- 2026-07-10 07:05 KST — Codex — Removed the optional Claude replay task QP-130. Its replay assertions are folded
  into Codex-owned QP-040/QP-900 verification, leaving Claude only the two pure modules where separate work adds
  material value: QP-110 signals and QP-120 position risk.
- 2026-07-10 07:15 KST — Claude Code — QP-120 moved to review (resumed and completed the `ac78fee1` worktree
  session). Pure evaluator in `quantpilot/packages/core/risk/position_exit.py`: protective stop
  `max(entry * 0.92, entry - 2 * ATR14)` rounded to 6dp; fail-closed `blocked` on naive/future/stale (>30s)
  quotes with zero exit quantity; full `exit` at stop breach or close <= SMA20 * 0.94 with stop-breach
  precedence over the `trim` branch; 50% `trim` at RSI >= 72 or close >= SMA20 * 1.20; deterministic, no
  clock/env/broker/order imports (import-boundary test kept). All 7 QP-120 xfail markers removed and passing:
  targeted `8 passed in 0.47s`; worktree full suite `349 tests, 0 failures` (10 skipped = 1 manual KIS +
  9 QP-110 xfails inherent to the worktree base commit); smoke `broker=mock`, `live_trading_enabled=false`,
  operator default-blocked. No Git staging performed. Codex review focus: 6dp rounding of stop/quantities and
  `>=` boundary semantics on the trim thresholds, then copy-back and QP-040 unblock.
- 2026-07-10 07:20 KST — Codex — QP-030 final safety review found and fixed two merge blockers: a provider/data
  `blocked` signal can no longer become a liquidation sell for an existing holding, and an evaluation-date daily
  bar is conservatively excluded unless a later session confirms completion. Added held-position stale-quote and
  same-day-forming-bar regressions. Verification after the fixes: `379 passed, 1 skipped, 7 xfailed`; smoke passed
  with `broker=mock`, `live_trading_enabled=false`, and Level 5 default-blocked.
- 2026-07-10 07:25 KST — Codex — QP-120 done after reviewing Claude's pure implementation. Corrected the contract
  so the realtime quote drives only the protective stop while the completed daily close drives SMA20 exit/trim;
  exposed both fixed-fraction and ATR stop components; and compare unrounded thresholds before rounding outputs.
  Exact stop, technical-exit, RSI/extension trim, stale/future/naive quote, precedence, and determinism proofs pass.
  Verification: QP-120 targeted `10 passed`; strategy-health targeted `13 passed`; backend `388 passed, 1 skipped`.
- 2026-07-10 14:10 KST — Claude Code — Read-only verification pass during Codex QP-040 WIP; no Claude-owned task
  is ready and no code was edited. Pure quant targeted suite `44 passed` (position-exit + pullback contracts,
  including the working-tree NaN/inf and symbol-normalization hardening). Full backend snapshot: `559 tests,
  2 failures`, both in Codex-owned paths: `test_level5_duplicate_run_key_does_not_duplicate_orders` and
  `test_level5_kill_switch_engaged_after_run_is_not_masked_by_duplicate_key` hit
  `ValueError: operator idempotency key is bound to a different request` at `operator/service.py:209`.
  Diagnostic hint for QP-040: the new run fingerprint appears to include a per-request field (e.g. `requested_at`),
  so a same-key replay no longer matches its stored fingerprint and raises instead of returning the cached result
  or surfacing the kill switch. Observation only — mid-task snapshot, not a review verdict.
- 2026-07-10 17:54 KST — Codex — QP-040 done after adversarial restart/concurrency review. Added durable managed
  position attribution, protective/retirement order isolation, best-bid limit liquidation, strategy health and
  retirement recovery, policy-portfolio weekly fencing, exact run replay binding, atomic safety-state patches,
  prepared-order recovery, and daily attempted-order accounting. The earlier duplicate-run failures were contract
  fixtures constructing different `requested_at` values for one idempotency key; they now reuse the exact request.
  One full-run attempt also hit an inaccessible system pytest temp directory; rerunning the required command with
  workspace-local `TEMP` passed `568 passed, 1 skipped in 10.26s`. Smoke passed with `broker=mock`, operator
  default-blocked by `level5_flag_disabled`, and live trading false. Two independent final audits found no P0/P1.
  QP-050 claimed with durable dispatch-journal, outcome-unknown reconciliation, and fixture/paper provenance gates.
- 2026-07-10 20:09 KST — Claude Code — Read-only verification snapshot during Codex QP-050 WIP; no Claude edits.
  Pure quant contracts remain green: `44 passed` (position-exit + pullback). Full backend snapshot: `728 tests,
  8 failures`, all in Codex-owned paths. Six failures share one root cause: the newly added
  `entry_atr14=decision.atr14` at `signals/service.py:341` sits in the provider fail-closed branch where no
  `decision` variable exists, raising `NameError` (that branch builds a `blocked` Signal without a pullback
  decision, so the field presumably wants `None`). The remaining two: `test_kis_paper_session_job`
  `runtime.policy == _policy()` fails on `TzInfo(0)` vs `timezone.utc` representation after a persistence round
  trip, and the related level5 provider-stale path failure shares the service.py NameError. Observation only —
  mid-task snapshot, not a review verdict.
- 2026-07-10 23:16 KST — Codex — QP-050 done after independent process-kill and boundary audits. Added strict
  paper-only KIS transport, explicit session authority, durable single-attempt dispatch/provenance, query-only
  reconciliation, restart hydration/application, persistent loss baselines, orderable-quantity controls, and the
  default-disabled one-shot paper job. Audit fixes separated KIS forwarding-org from daily order-branch evidence,
  preserved risk-reducing sells behind uncertain buys, recovered pre-claim liquidation checkpoints, revalidated
  the full human promotion ladder, rejected mixed balance pages, and rechecked the session after the dispatch CAS
  before POST. Full backend verification passed `757 passed, 2 skipped`; smoke remained `broker=mock`, operator
  default-blocked by `level5_flag_disabled`, and live trading false. The paper job default exited with
  `paper_session_disabled` and no network. QP-060 claimed on clean existing operator API/page paths; Claude Code
  remains idle because its completed pure-signal/risk modules need no additional task.
