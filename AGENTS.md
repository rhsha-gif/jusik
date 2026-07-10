# QuantPilot Agent Workflow

QuantPilot is a safe, fixture-first trading operator harness. Live trading must remain disabled by default.

## QuantPilot Stage 03+ Working Agreements

- Treat QuantPilot as a safety-critical trading-system harness.
- Never enable live trading by default.
- Never add broker credentials, API keys, account IDs, secrets, or personal trading information to the repository.
- Any external API connector must have fake-client unit tests and skipped/manual integration tests only.
- Unit tests must not require internet access.
- Preserve fixture determinism.
- Preserve the existing mock default and market-order-disabled default.
- Use explicit data mode labels: `fixture`, `local_historical`, `external_historical`, `realtime_market_data`, `paper_trading`, `live_trading_candidate`, `live_canary`, or `live_scaled`.
- Any live-trading-related change must preserve pre-trade risk checks, kill switches, idempotency, order-state-machine checks, audit logging, and reconciliation.
- Prefer small, reviewable stages. Do not combine multiple stages in one task.
- Always run `python -m pytest quantpilot/tests` after backend changes.
- Always run `python -m quantpilot.jobs.run_smoke` when smoke behavior or orchestration changes.
- Frontend changes under `quantpilot/apps/web` require `npm run build` and `npm run test` from that directory when available.
- If a test fails, fix the root cause. Do not weaken safety tests to pass.

## Current Level 5 Workflow

- Codex prepares Level 5 rails, contracts, fixtures, and tests.
- Fable5 implements the Level 5 fully automated portfolio operator inside those rails.
- Codex or a human reviews Fable5 diffs before merge.
- Live trading remains disabled by default.

## Codex + Claude Code Workboard

- Read `docs/professional_operator_workboard.md` before claiming or changing professional-operator work.
- The workboard is the canonical source for task ownership, dependencies, acceptance criteria, evidence, and handoff state.
- Codex and Claude Code may work concurrently only on disjoint paths recorded in the workboard.
- Each agent may own at most one `in_progress` task.
- Claude Code is limited to its claimed pure technical/signal/position-risk modules and focused tests/fixtures.
- Codex owns cross-cutting contracts, repositories, operator integration, broker adapters, API/UI, review, and all Git staging/commits.
- Both agents update the workboard at claim, handoff, review, and blocker checkpoints using its document-edit lease.
- Do not mark a task `done` without exact verification output recorded in the workboard.

### Stuck-task escalation (workboard rule 9)

- If the same test or defect survives two or more fix attempts, stop looping: record the blocker in the
  workboard (failing command, error summary) and wait for a Fable5 (Claude Code) read-only diagnostic before
  further attempts.
- Fable5 posts a root-cause diagnosis to the checkpoint log; it never edits Codex-owned paths. The fix stays
  with the path owner.

### Strength-based routing (workboard rule 10)

- Route to Fable5, executed end-to-end in Claude Code: research synthesis, backtest forensics (lookahead,
  overfitting, data snooping, survivorship), quant recipe and risk-matrix design, and independent
  contract/evidence review. Fable5 hands only the finished artifact to Codex for integration.
- Codex keeps all stateful integration regardless of workload: DB, broker, API, scheduler, UI, and Git.

## Required Commands

Use PowerShell equivalents on Windows:

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
```

Use `make test` and `make smoke` only where `make` is available.

## Safety Invariants

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`

Do not add live broker credentials, enable real broker access, or create tests that submit live orders.

## Level 5 References

Read [docs/fable5_level5_implementation_spec.md](docs/fable5_level5_implementation_spec.md) and [docs/contracts/operator_contracts.md](docs/contracts/operator_contracts.md) before implementation.
Also read [docs/professional_operator_workboard.md](docs/professional_operator_workboard.md) for the active execution stage and file ownership.
