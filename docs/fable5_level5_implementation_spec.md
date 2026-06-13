# Fable5 Level 5 Implementation Spec

## Target

Level 5 is a fully automated portfolio operator that can run one bounded decision cycle without user approval. It must select an eligible strategy, evaluate policy and market conditions, decide whether to submit mock or paper-safe orders, record fallbacks, and produce an auditable operator report.

Level 5 is not live trading. It remains disabled by default.

## Target Loop

1. Accept an `OperatorRunRequest`.
2. Load the current policy, portfolio snapshot, market regime, and strategy registry.
3. Refuse to run unless `FULLY_AUTOMATED_OPERATOR_ENABLED=true` and every lower safety gate is satisfied.
4. Select one eligible strategy or fall back to Level 4, Level 3, Level 2, or no-op.
5. Generate candidate orders through existing Level 3/4 planning paths.
6. Re-run deterministic risk checks and policy-version checks immediately before any submission.
7. Submit only through mock or paper-safe broker adapters.
8. Persist decisions, fallback reasons, policy version changes, and report data.
9. Return an `OperatorRunResult`.

## Required Interfaces

Use `docs/contracts/operator_contracts.md` as the stable source for:

- `OperatorRunRequest`
- `OperatorRunResult`
- `StrategyRegistryEntry`
- `StrategySelectionDecision`
- `OperatorDecision`
- `FallbackDecision`
- `PolicyReviewRequest`
- `PolicyVersionChange`
- `OperatorReport`

Prefer Pydantic models under `quantpilot/packages/core/` and keep API DTOs compatible with those models.

## Expected Files

Fable5 may add or update narrowly scoped files such as:

- `quantpilot/packages/core/operator/`
- `quantpilot/packages/core/strategies/registry.py`
- `quantpilot/packages/core/policy/versioning.py`
- `quantpilot/packages/core/execution/fallback_manager.py`
- `quantpilot/services/api/routers/operator.py`
- `quantpilot/tests/unit/test_level5_*.py`
- `quantpilot/tests/integration/test_level5_operator_run_once.py`

Do not move existing Level 0-4 code unless a small adapter is clearly necessary.

## Feature Flags

Defaults must remain:

```text
LIVE_TRADING_ENABLED=false
GUARDED_AUTOPILOT_ENABLED=false
FULLY_AUTOMATED_OPERATOR_ENABLED=false
MARKET_ORDERS_ENABLED=false
BROKER_MODE=mock
```

Level 5 must additionally require:

- policy authority level explicitly promoted to Level 5 or an equivalent explicit policy field
- safe broker mode: `mock` or paper-only
- kill switch off
- no stale quotes
- no conflicting unfilled orders
- matching policy version
- passing fresh risk checks

## Strategy Registry Statuses

Use stable statuses:

- `draft`: never eligible for Level 5
- `validated_l3`: eligible for Level 3 only
- `validated_l4`: eligible for guarded autopilot only
- `validated_l5`: eligible for Level 5 candidate selection
- `disabled`: not eligible
- `revoked`: not eligible

A strategy must also declare allowed execution levels. Status alone is not authority.

## Fallback Matrix

| Condition | Required fallback |
|---|---|
| Level 5 flag disabled | no-op with `level5_flag_disabled` |
| policy not promoted | Level 4 if available, else Level 3 |
| no Level 5 strategy eligible | Level 4 if available, else Level 2 suggestions |
| policy version mismatch | no submission and `policy_review_required` |
| stale quote or broker unhealthy | no submission and Level 3 proposal only |
| risk check failure | no submission and blocked decision |
| kill switch engaged | no-op with `kill_switch_engaged` |
| market order requested while disabled | no submission and blocked decision |

## Policy Versioning

- Every operator run must bind to a policy version.
- Any material policy change must create a `PolicyVersionChange`.
- Existing plans from older policy versions must be blocked from automatic submission.
- Reports must include the policy version used and any pending review requirement.

## Reporting Requirements

Every run must produce an `OperatorReport` with:

- run id and timestamps
- policy id and version
- selected strategy or fallback
- decisions made
- order plan ids and broker order ids
- risk check ids
- safety flags observed
- live trading state
- audit event count

## API/UI Expectations

If an API is added, use FastAPI patterns already present under `quantpilot/services/api/routers/`.

Minimum expected route:

- `POST /api/operator/run-once`

If a UI is later added, it must show disabled/default state, fallback reason, policy version, strategy selection, and report details. No frontend currently exists in this repository.

## Out Of Scope

- live broker trading
- credential storage
- secret handling
- real exchange calendar integration
- guaranteed performance claims
- broad architecture rewrites
- new production dependencies unless justified

## Acceptance Tests

The Level 5 implementation is acceptable only when:

- new Level 5 tests are no longer xfail/pending
- disabled feature flags block operator submission
- safe mock run-once can produce an auditable no-op or mock/paper-safe submission
- strategy registry refuses draft, disabled, and unapproved strategies
- fallback manager returns deterministic fallback reasons
- policy version mismatch blocks automatic submission
- safety flags remain false by default
- `python -m pytest quantpilot/tests` passes
- `python -m quantpilot.jobs.run_smoke` passes with `live_trading_enabled=false`
