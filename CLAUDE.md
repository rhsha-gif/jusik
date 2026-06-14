# QuantPilot Fable5 Level 5 Handoff

QuantPilot is an intent-first personal AI quant operations webapp backed by a safe,
fixture-first portfolio operator harness. The product goal is for a user to enter an
investment direction, sector/theme, symbol set, or risk appetite, then have QuantPilot
produce candidate analysis, signals, target weights, rebalance suggestions, risk review,
mock/dry-run or paper-safe execution outputs, and operation reports.

Fable5 may implement Level 5 only inside the contracts, flags, and tests prepared for
this repository. Live trading remains out of scope by default.

## Read First

- `AGENTS.md`
- `README.md`
- `docs/fable5_level5_implementation_spec.md`
- `docs/contracts/operator_contracts.md`
- `docs/level_3_4_implementation_report.md`
- `docs/quant_recipes/fable5_level_3_4_autopilot_recipe.md`

## Commands

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
- Data mode labels must be explicit: `fixture`, `local_historical`, `external_historical`, `realtime_market_data`, `paper_trading`, `live_trading_candidate`, `live_canary`, or `live_scaled`.
- Level 5 tests must not call live brokers or require secrets.
- Fully automated runs must be blocked unless all feature flags, policy gates, fallback checks, and risk gates pass.
- "Order execution" means dry-run, mock broker, or paper-safe output unless a separate human-reviewed live enablement stage exists.

## Forbidden Actions

- Do not add live broker credentials or read secret files.
- Do not enable live trading defaults.
- Do not add credential UI, live-trading APIs, or real broker order submission paths.
- Do not bypass the order state machine or risk gates.
- Do not emit raw broker orders from LLM/RL outputs.
- Do not broaden refactors outside Level 5 surfaces.

## Working Rules

- Preserve existing user changes in the worktree.
- Ground progress claims in actual command output.
- Keep diffs narrow and consistent with existing Pydantic/FastAPI patterns.
- Preserve the intent-first UI and public API paths such as `/api/intent/run`, `/api/level-1-2/run`, `/api/operator/run-once`, `/api/operator/status`, and live-readiness evaluation.
- Prefer typed DTOs and explicit contracts over loose dictionaries when touching service boundaries.
- Readiness modules must fail closed and avoid network-required unit tests.
- Use subagents for risk review and test review when available.
- Treat pending Level 5 tests as implementation targets, not proof that Level 5 exists.
