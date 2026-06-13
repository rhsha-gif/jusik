# Authoring and Promoting a Strategy

How a human-defined strategy is encoded, backtested, and walked through the
promotion lifecycle so it can move from `draft` toward paper trading **only
after validation gates pass**. Nothing here enables live trading: the lifecycle
tops out at `live_candidate`, a *candidate* status that still requires a separate
human-reviewed live-trading spec with its own fail-closed flags.

## Three orthogonal axes (don't conflate them)

| Axis | Where | What it answers |
|---|---|---|
| **Spec** | `StrategyRecipe.promotion_status` ([schemas.py](../quantpilot/packages/core/schemas.py)) | What does the strategy *do*? Immutable recipe; cannot express Level 5. |
| **Lifecycle** | `StrategyLifecycleRecord` ([promotion.py](../quantpilot/packages/core/strategies/promotion.py)) | How far through *human review and evidence* has this version progressed? |
| **Execution authority** | `StrategyRegistry` ([registry.py](../quantpilot/packages/core/strategies/registry.py)) | Which strategy may the operator select *right now*? |

This document covers the **lifecycle** axis and its bridge to execution
authority. For the execution-level rules see
[operator_strategy_promotion_policy.md](operator_strategy_promotion_policy.md).
The lifecycle axis is evidence, not selection. The registry axis is selection,
not evidence. Any registry entry that carries proposal, paper, guarded, Level 5,
or future live/canary authority must be bound to a matching lifecycle record for
the same `strategy_id`, `version`, and `spec_hash` by
[`lifecycle_binding.py`](../quantpilot/packages/core/strategies/lifecycle_binding.py).

## Step 1 — Encode the spec

Author the recipe as YAML under `quantpilot/docs/strategy_specs/<strategy_id>.yaml`
(see [`pullback_trend_v2.yaml`](../quantpilot/docs/strategy_specs/pullback_trend_v2.yaml)
for a full template). It is loaded and validated by
`load_strategy_recipe(...)`; a recipe whose `allowed_execution_levels` exceed its
`promotion_status` is rejected at load. New specs start `promotion_status: draft`
with `allowed_execution_levels: []`.

## Step 2 — Register a lifecycle record

```python
from quantpilot.packages.core.strategies.loader import load_strategy_recipe
from quantpilot.packages.core.strategies.promotion import (
    StrategyPromotionService, compute_spec_hash,
)

recipe = load_strategy_recipe("my_strategy")
service = StrategyPromotionService()
service.register_draft(
    strategy_id=recipe.strategy_id,
    version=recipe.version,
    spec_hash=compute_spec_hash(recipe),  # content-addressed immutability lock
)
```

The `spec_hash` binds the record to an immutable snapshot of the recipe. A
registry entry with execution authority must carry the same hash before the
operator-selection axis can rely on the lifecycle evidence. A `draft` strategy
**can be backtested but can never submit orders**
(`eligibility_for(draft).can_submit_orders is False`).

## Step 3 — Walk the promotion ladder

Each step advances exactly one stage and requires **recorded evidence** *and* an
**explicit human confirmation marker**:

```
draft → backtested → paper_candidate → paper_validated → live_candidate
```

| Target status | Required evidence kinds |
|---|---|
| `backtested` | `backtest_result` |
| `paper_candidate` | `backtest_result` |
| `paper_validated` | `paper_track_record` |
| `live_candidate` | `paper_track_record`, `risk_review` |

Attach evidence, then promote with the confirmation phrase and a human
attribution:

```python
from quantpilot.packages.core.strategies.promotion import (
    PROMOTION_CONFIRMATION, PromotionEvidence,
)

service.attach_evidence(
    strategy_id="my_strategy", version="1.0",
    evidence=PromotionEvidence(
        kind="backtest_result",
        reference="docs/stage_03_backtest_validation_report.md",
        summary="cleared the backtest validation protocol",
        metrics={"excess_return": 0.04, "max_drawdown": -0.08},
        recorded_by="human-reviewer",
    ),
)
service.promote(
    strategy_id="my_strategy", version="1.0",
    confirmation=PROMOTION_CONFIRMATION,   # exact phrase: "confirm strategy promotion"
    confirmed_by="your-name",              # non-empty human attribution
)
```

**Why this blocks LLM/RL self-promotion:** a promotion needs both the exact
`PROMOTION_CONFIRMATION` phrase *and* a non-empty `confirmed_by`. Model output is
not permitted to synthesize a human confirmation, so an agent can *prepare*
evidence but never *approve* a stage. This mirrors the policy-update
confirmation in [versioning.py](../quantpilot/packages/core/policy/versioning.py).

### Failure modes (all deterministic, typed)

| Situation | Raised |
|---|---|
| Wrong/empty confirmation marker | `PromotionConfirmationRequired` |
| Required evidence kind missing | `MissingPromotionEvidence` |
| Promotion names the wrong version | `StrategyVersionMismatch` |
| Supplied `spec_hash` ≠ locked snapshot | `ImmutableStrategyVersion` |
| Skipping a ladder step / promoting a `disabled`/`revoked` record | `InvalidPromotionTransition` |

## Immutability and versioning

A version is **immutable once promoted past `draft`**. To change the spec,
author a **new version** that starts again at `draft` — never mutate a promoted
record. `revoked` is terminal; re-entry begins a fresh `draft`.

## Bridge to execution authority

Lifecycle evidence is necessary but not sufficient for registry authority. The
fail-closed binding layer checks that a registry entry's effective authority
levels are supported by a matching lifecycle record:

| Registry authority carried | Minimum lifecycle evidence |
|---|---|
| Research/signal-only, no execution authority | no lifecycle record required |
| Level 3 / proposal authority | `backtested` |
| Paper, guarded autopilot, or Level 5 candidate authority | `paper_validated` |
| Future live/canary authority labels | `live_candidate` |
| `disabled` or `revoked` lifecycle | no execution authority |

The binding also blocks missing lifecycle records and `spec_hash` mismatches. A
registry entry is only selectable when **both** its registry status earns the
level **and** its `allowed_execution_levels` list it, and every order is
independently re-checked by `authorize_level5`.

`live_candidate` is still not live permission. It is evidence for a future
live/canary review path only; live trading remains blocked by the separate
enablement checklist and fail-closed runtime flags.

## Durable, versioned representation

Lifecycle records persist as JSON (see
[`tests/fixtures/strategy_lifecycle_records.json`](../quantpilot/tests/fixtures/strategy_lifecycle_records.json)),
loadable with `load_lifecycle_fixture(...)`. Each record is keyed by
`strategy_id` + `version` + `spec_hash` and carries its full evidence and
promotion history.
