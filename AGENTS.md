# QuantPilot Agent Workflow

QuantPilot is an intent-first personal AI quant operations webapp and a safe,
fixture-first trading operator harness. The target product flow is:

1. The user enters an investment direction, sector/theme, symbol set, or risk appetite.
2. AI and deterministic quant services produce candidate analysis, signals, target weights,
   rebalance suggestions, pre-trade risk review, mock/dry-run or paper-safe execution outputs,
   and operation reports.
3. The harness keeps every execution path safe by default. Live trading must remain disabled
   unless a separate human-reviewed live enablement stage is created in the future.

## QuantPilot Stage 03+ Working Agreements

- Treat QuantPilot as a safety-critical trading-system harness.
- Never enable live trading by default.
- Never add broker credentials, API keys, account IDs, secrets, or personal trading information to the repository.
- Do not add credential UI, live-trading APIs, or real broker order submission paths.
- Interpret "order execution" as dry-run, mock broker, or paper-safe execution only.
- Any external API connector must have fake-client unit tests and skipped/manual integration tests only.
- Unit tests must not require internet access.
- Preserve fixture determinism.
- Preserve the existing mock default and market-order-disabled default.
- Use explicit data mode labels: `fixture`, `local_historical`, `external_historical`, `realtime_market_data`, `paper_trading`, `live_trading_candidate`, `live_canary`, or `live_scaled`.
- Any live-trading-related change must preserve pre-trade risk checks, kill switches, idempotency, order-state-machine checks, audit logging, and reconciliation.
- Keep public API paths stable unless the task explicitly requires a migration: `/api/intent/run`, `/api/level-1-2/run`, `/api/operator/run-once`, `/api/operator/status`, and live-readiness evaluation.
- Prefer typed DTOs and explicit contracts over loose dictionaries when touching service boundaries.
- Prefer small, reviewable stages. Do not combine multiple stages in one task.
- Always run `python -m pytest quantpilot/tests` after backend changes.
- Always run `python -m quantpilot.jobs.run_smoke` when smoke behavior or orchestration changes.
- Frontend changes under `quantpilot/apps/web` require `npm run build` and `npm run test` from that directory when available.
- If a test fails, fix the root cause. Do not weaken safety tests to pass.

## Intent-First Product Surfaces

- Preserve the intent-first UI as the primary experience, not the older simple overview.
- User input should remain plain-language direction plus risk tolerance; generated policy text
  must stay mock/paper-safe and limit-order-only by default.
- Candidate analysis, signals, target weights, rebalance suggestions, risk checks, operator dry-runs,
  readiness reports, and operation reports should share one run snapshot where practical.
- Readiness modules must fail closed and may evaluate configuration or evidence only; they must not
  submit orders or make network calls in unit tests.

## Current Level 5 Workflow

- Codex prepares Level 5 rails, contracts, fixtures, and tests.
- Fable5 implements the Level 5 fully automated portfolio operator inside those rails.
- Codex or a human reviews Fable5 diffs before merge.
- Live trading remains disabled by default.

## Required Commands

Use PowerShell equivalents on Windows:

```powershell
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp
python -m quantpilot.jobs.run_smoke
cd quantpilot/apps/web
npm.cmd run test
npm.cmd run build
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
