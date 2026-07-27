# Fable5 Level 5 Readiness Report

Date: 2026-06-12

## 1. Repository Status

QuantPilot is a Python FastAPI/Pydantic fixture harness with existing Level 0-4 reports and tests. The package code is under `quantpilot/`, so the prompt's root package paths were adapted to `quantpilot/packages/...`.

The worktree already had uncommitted Level 3/4 changes before this prep pass. They were not reverted.

## 2. Stage Report Availability

Present:

- `docs/environment_verification_report.md`
- `docs/pre_harness_report.md`
- `docs/level_1_2_report.md`
- `docs/quant_recipes/fable5_level_3_4_autopilot_recipe.md`
- `docs/level_3_4_implementation_report.md`

No stage-report blocker remains.

## 3. Existing Level 0-4 Contracts Discovered

- Pydantic schemas: `quantpilot/packages/core/schemas.py`
- Harness orchestration: `quantpilot/packages/core/harness_service.py`
- Strategy loading: `quantpilot/packages/core/strategies/loader.py`
- Policy parsing: `quantpilot/packages/core/policy/parser.py`
- Portfolio planning: `quantpilot/packages/core/portfolio/planner.py`
- Risk gates: `quantpilot/packages/core/risk/gatekeeper.py`
- Level 4 authorization and state transitions: `quantpilot/packages/core/execution/state_machine.py`
- Mock and paper brokers: `quantpilot/packages/brokers/`
- API routers: `quantpilot/services/api/routers/`

## 4. Files Created Or Updated

Created:

- `AGENTS.md`
- `docs/fable5_level5_implementation_spec.md`
- `docs/contracts/operator_contracts.md`
- `quantpilot/tests/fixtures/operator_policy.json`
- `quantpilot/tests/fixtures/operator_portfolio_snapshot.json`
- `quantpilot/tests/fixtures/operator_strategy_registry.json`
- `quantpilot/tests/fixtures/operator_market_regime.json`
- `quantpilot/tests/fixtures/operator_fallback_cases.json`
- `quantpilot/tests/integration/test_level5_operator_run_once.py`
- `quantpilot/tests/unit/test_level5_strategy_registry.py`
- `quantpilot/tests/unit/test_level5_fallback_manager.py`
- `quantpilot/tests/unit/test_level5_policy_versioning.py`
- `quantpilot/tests/unit/test_level5_safety_flags.py`
- `.claude/agents/risk-gate-auditor.md`
- `.claude/agents/test-auditor.md`
- `.claude/agents/operator-runbook-reviewer.md`
- `.codex/README.md`
- `.codex/setup.sh`

Updated:

- `CLAUDE.md`
- `.env.example`
- `docs/fable5_level5_readiness_report.md`

## 5. Safety Defaults Verified

Documented defaults:

```text
LIVE_TRADING_ENABLED=false
GUARDED_AUTOPILOT_ENABLED=false
FULLY_AUTOMATED_OPERATOR_ENABLED=false
MARKET_ORDERS_ENABLED=false
BROKER_MODE=mock
```

`.env.example` contains these defaults. No live broker credentials were added.

## 6. Test Commands Discovered

From `README.md`, `Makefile`, and `pyproject.toml`:

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
python -m uvicorn quantpilot.services.api.main:app --reload
```

Optional where `make` is available:

```powershell
make test
make smoke
make api
```

No lint or typecheck command is configured in project metadata.

## 7. Tests Added Or Prepared

Prepared Level 5 tests:

- integration run-once disabled-flag test
- strategy registry eligibility test
- fallback manager matrix test
- policy version drift test
- safety defaults test

Implementation-dependent tests are marked `xfail` because Level 5 modules do not exist yet. The safety defaults test is expected to pass now.

## 8. Remaining Blockers For Fable5

- Implement Level 5 operator models and service.
- Implement Level 5 strategy registry or adapter.
- Implement fallback manager.
- Implement policy versioning guard.
- Add API route if required: `POST /api/operator/run-once`.
- Convert prepared `xfail` tests into passing tests once implementation exists.
- Keep all live-trading paths disabled by default.

## 9. Exact Next Prompt For Fable5

Give Fable5:

`01_FABLE5_LEVEL_5_IMPLEMENTATION_PROMPT_OFFICIAL_DOC_OPTIMIZED.md`

## 10. Verification Results

```powershell
python -m pytest quantpilot/tests
```

Result: PASS. `59 passed, 4 xfailed in 3.25s`.

The 4 xfailed tests are Level 5 implementation targets for modules that do not exist yet.

```powershell
python -m quantpilot.jobs.run_smoke
```

Result: PASS. Summary included `broker=mock`, `execution_mode=approval_required`, `signals=7`, `fills=3`, `audit_events=36`, and `live_trading_enabled=false`.

No lint or typecheck command is configured in project metadata.
