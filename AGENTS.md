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
