# Operator Strategy Promotion Policy

How a strategy earns — and loses — the right to run at each automation level. The registry (`quantpilot/packages/core/strategies/registry.py`) is the **operator-selection record**; a strategy recipe can never self-promote (its `promotion_status` literal cannot even express Level 5).

> **Upstream of this record:** the human-review *lifecycle*
> (`draft → backtested → paper_candidate → paper_validated → live_candidate`) in
> [strategy_authoring_and_promotion.md](strategy_authoring_and_promotion.md) is
> what *justifies* a registry status. Each lifecycle step requires recorded
> evidence plus an explicit human confirmation marker, so the registry statuses
> below are never granted by LLM/RL output. Registry authority also requires a
> fail-closed lifecycle binding with matching `strategy_id`, `version`, and
> `spec_hash`. `live_candidate` is not live permission — live trading remains a
> separate, fail-closed spec.

## Statuses and what they permit

| Registry status | May run at | Notes |
|---|---|---|
| `draft` | nothing automatic | authoring/backtesting only |
| `validated_l3` | Level 3 (approval-based proposals) | every order needs explicit user approval |
| `validated_l4` | Levels 3-4 (guarded autopilot) | **not sufficient for Level 5** |
| `validated_l5` | Levels 3-5 (fully automated candidate) | still constrained by allowed_execution_levels |
| `disabled` | nothing automatic | `disabled_reason` must be recorded |
| `revoked` | nothing automatic | terminal; re-entry starts from `draft` |

Selection requires **both**: status `validated_l5` *and* `level_5`/`fully_automated` in `allowed_execution_levels`. Status alone is not authority.

## Lifecycle binding requirements

The lifecycle is the human/evidence axis; the registry is the operator-selection
axis. A registry entry with execution authority must pass
`validate_registry_entry_lifecycle(...)` before that authority is trusted:

| Registry authority carried | Required lifecycle state |
|---|---|
| Research/signal-only | no lifecycle record required |
| Level 3 / proposal | at least `backtested` |
| Paper, guarded autopilot, or Level 5 candidate | at least `paper_validated` |
| Future live/canary authority | `live_candidate` |

Missing lifecycle records, missing registry-side spec hashes, `spec_hash`
mismatches, and disabled/revoked lifecycle states block authority. A
`live_candidate` lifecycle record does not enable live trading; the runtime still
requires `LIVE_TRADING_ENABLED=false` and the live enablement checklist remains
unsatisfied by design.

## Promotion ladder (each step needs recorded evidence and a human decision)

1. `draft → validated_l3`: backtest protocol passed (see strategy spec validation block); sources verified; risk rules consistent with policy limits.
2. `validated_l3 → validated_l4`: paper/approval-mode track record over a predefined window; no risk-gate violations attributable to the strategy; guarded-autopilot dry runs reviewed.
3. `validated_l4 → validated_l5`: guarded-autopilot track record over a predefined window; fallback/loss-limit behavior observed at least once in paper; human sign-off recorded in the registry entry change.

Deterministic selection among eligible `validated_l5` entries: lowest `priority` number wins, ties broken by `strategy_id`. Optional `min_policy_version`/`max_policy_version` pin a strategy to the policy versions it was approved against.

## Demotion and disablement (deterministic, in code)

`StrategyRegistry.apply_performance_review(strategy_id, excess_return, max_drawdown)`:

- `max_drawdown <= -0.20` **or** `excess_return <= -0.10` → **disabled** (`underperformance_disable_threshold_breached`).
- `excess_return <= -0.05` → **demoted** one level (`validated_l5 → validated_l4 → validated_l3 → disabled`); demotion strips the execution levels the new status no longer earns.
- Otherwise unchanged.

Demoted or disabled strategies are never selected automatically; re-promotion requires walking the ladder again with new evidence.

## LLM / RL suggestions

Fable5, another LLM, or an RL policy may *suggest* a `strategy_selection`. The suggestion is only an input: final selection is always `StrategyRegistry.select_for_level5` under the rules above, and `authorize_level5` independently re-verifies registry status and recipe identity per order.
