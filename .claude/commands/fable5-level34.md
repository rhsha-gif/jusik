# /fable5-level34

Design a complete Level 3 or Level 4 QuantPilot recipe.

## Usage

```
/fable5-level34 [hypothesis] [--level 3|4] [--asset-class equities|futures|crypto|fx] [--horizon daily|weekly|intraday]
```

## What This Command Does

Invokes the `quant-recipe-architect` agent to run the full recipe design pipeline:

1. **Source synthesis** — uses `quant-source-synthesis` skill to find peer-reviewed and practitioner backing for the hypothesis
2. **Signal design** — defines alpha signals with formulas and citations
3. **Risk matrix** — uses `risk-matrix-designer` skill to produce risk parameters
4. **Backtest protocol** — defines walk-forward, OOS splits, transaction cost assumptions
5. **RL contract** (Level 4 only) — uses `rl-contract-designer` skill
6. **YAML assembly** — produces complete recipe conforming to CLAUDE.md schema
7. **Codex handoff stub** — produces initial task spec block

## Invocation Instructions

When this command is invoked:

1. If no hypothesis is provided, ask the user: "Describe the strategy hypothesis (signal, asset class, and time horizon)."
2. Ask the user to confirm the desired recipe level (3 = approval-required, 4 = guarded autopilot with RL).
3. Invoke the `quant-recipe-architect` agent with the collected inputs.
4. The agent will report progress through each pipeline stage.
5. Write the final recipe to `docs/quant_recipes/<recipe-id>.yaml`.
6. Report: recipe ID, overall confidence rating, and next steps.

## Safety

- No broker API calls
- No executable orders
- No live trading code
- No secrets access
- Recipe is a design artifact only — Codex implements

## Output

File: `docs/quant_recipes/<recipe-id>.yaml`
