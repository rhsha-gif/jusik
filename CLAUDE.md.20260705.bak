# QuantPilot Fable5 Level 5 Handoff

QuantPilot is a safe, fixture-first portfolio operator harness. Fable5 may implement Level 5 only inside the contracts, flags, and tests prepared for this repository.

## Read First

- `AGENTS.md`
- `README.md`
- `docs/fable5_level5_implementation_spec.md`
- `docs/contracts/operator_contracts.md`
- `docs/level_3_4_implementation_report.md`
- `docs/quant_recipes/fable5_level_3_4_autopilot_recipe.md`

## Commands

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
- Level 5 tests must not call live brokers or require secrets.
- Fully automated runs must be blocked unless all feature flags, policy gates, fallback checks, and risk gates pass.

## Forbidden Actions

- Do not add live broker credentials or read secret files.
- Do not enable live trading defaults.
- Do not bypass the order state machine or risk gates.
- Do not emit raw broker orders from LLM/RL outputs.
- Do not broaden refactors outside Level 5 surfaces.

## Working Rules

- Preserve existing user changes in the worktree.
- Ground progress claims in actual command output.
- Keep diffs narrow and consistent with existing Pydantic/FastAPI patterns.
- Use subagents for risk review and test review when available.
- Treat pending Level 5 tests as implementation targets, not proof that Level 5 exists.
