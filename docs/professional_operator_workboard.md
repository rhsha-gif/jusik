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

- Codex: `idle/review` — QP-020 complete; waiting for QP-110 handoff before QP-030
- Claude Code: `QP-110 ready` — executable pullback signal engine
- Next integration gate: `GATE-1` — QP-010 contract accepted; QP-020 and QP-110 ready in parallel

## Work queue

| Task ID | Owner | Depends on | Owned paths | Status | Acceptance | Evidence / handoff |
|---|---|---|---|---|---|---|
| QP-000 | codex | none | workboard, `AGENTS.md`, `CLAUDE.md` | done | Both agents have read-first and ownership rules; fresh baseline recorded | backend 324/1; smoke mock/default-blocked; frontend 20/build passed |
| QP-010 | codex | QP-000 | operator decision contract, contract tests, fixtures | done | Exact pure-function inputs/outputs and fail-closed tests exist without implementing the engine | targeted: 3 passed, 16 xfailed; full suite after initial rails: 325 passed, 1 skipped, 8 xfailed |
| QP-020 | codex | QP-010 | repository and managed-position persistence modules/tests | done | Paper-mode state survives restart; idempotency remains unique; fixture defaults unchanged | 8 focused tests passed; SQLite WAL/full-sync, restart recovery, stale-write and secret-field guards verified |
| QP-110 | claude | QP-010 | technical indicators, pure pullback signal module, focused tests/fixtures | ready | Selected strategy emits deterministic, no-lookahead actions using the locked thresholds | contract and xfail targets ready |
| QP-030 | codex | QP-020, QP-110 | schemas, signal adapter, portfolio optimizer/planner, operator integration | blocked | Selected strategy alone drives plans; max positions and existing safety gates are enforced | pending |
| QP-120 | claude | QP-110 | pure position-risk evaluator and focused tests | blocked | 8%/2ATR stop and technical exit/trim precedence are deterministic and broker-free | pending |
| QP-040 | codex | QP-030, QP-120 | strategy performance, retirement, liquidation, rebalance orchestration | blocked | Retirement creates no buys; risk-reducing sells remain auditable and idempotent | pending |
| QP-130 | claude | QP-040 | signal/risk fixtures, replay validation, fixes limited to Claude-owned modules | blocked | Entry/exit/stop behavior is replayed without look-ahead and documented with exact evidence | pending |
| QP-050 | codex | QP-040 | KIS paper adapters, reconciliation, session job, fake-client tests | blocked | No production host accepted; fake tests pass; manual paper test stays opt-in | pending |
| QP-060 | codex | QP-050, QP-130 | operator status/report UI and final hardening | blocked | Safety state, position risk, strategy health, rebalance, and reconciliation are visible | pending |
| QP-900 | codex | QP-060 | completion report and final verification only | blocked | Full acceptance audit passes; all required evidence is current | pending |

## Integration requests

- QP-030: provider-bound signal flow must pass real completed history and actual `Quote.as_of`; never reuse
  `OrderIntent.quote_time` as market-data freshness evidence.
- QP-030: enforce `UserPolicy.max_positions`, replace the professional path's missing-quote `100.0` fallback with
  fail-closed behavior, and use the locked 0.01 rebalance band instead of the legacy 0.001 default.
- QP-040: risk-reducing liquidation sells must not be rejected together with failed buys or blocked solely by a
  monthly buy/automation pause; they still require fresh quotes, idempotency, state-machine, and audit checks.
- QP-050: thread the reconciled broker snapshot through the final submission-time risk check instead of silently
  substituting `fixture_portfolio_snapshot()`.

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
