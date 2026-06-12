---
name: fable5-level34-recipe
description: >
  Author a complete Level 3 (Approval-Based Autopilot) or Level 4 (Guarded Autopilot)
  quantitative trading recipe in structured YAML. Synthesizes signals, entry/exit rules,
  risk matrix, backtest protocol, and Codex handoff stub from cited sources.
triggers:
  - "design a strategy"
  - "write a recipe"
  - "level 3 recipe"
  - "level 4 recipe"
  - "approval autopilot"
  - "guarded autopilot"
model: claude-fable-5
---

# Fable5 Level 3–4 Recipe Skill

## Purpose

Produce a structured, cite-backed YAML recipe for a quantitative trading strategy at Level 3 (human-approval-required) or Level 4 (fully guarded autopilot with RL contract).

## Safety Constraints

- No broker API calls.
- No executable orders.
- No live trading code.
- No secrets access.
- Output is a design artifact only — Codex must implement.

## Inputs Required

1. Strategy hypothesis (plain English)
2. Target asset class and universe
3. Desired recipe level (3 or 4)
4. Time horizon (intraday / daily / weekly)
5. Risk tolerance parameters (optional; defaults applied if omitted)

## Process

1. **Source check** — invoke `quant-source-synthesis` skill to identify 2+ peer-reviewed and 1+ practitioner sources supporting the hypothesis.
2. **Signal design** — enumerate alpha signals with formula, lookback, and source citation.
3. **Rule construction** — define entry conditions, exit conditions, position sizing formula.
4. **Risk matrix** — invoke `risk-matrix-designer` skill to set drawdown limits, correlation budget, stop-loss.
5. **Backtest protocol** — define in-sample / out-of-sample splits, walk-forward windows, transaction costs.
6. **RL contract** (Level 4 only) — invoke `rl-contract-designer` skill.
7. **YAML assembly** — output compliant with schema defined in CLAUDE.md.
8. **Codex handoff stub** — include minimal handoff block for downstream `codex-handoff-writer`.

## Output Format

```yaml
recipe:
  id: <uuid>
  level: 3 | 4
  name: <string>
  hypothesis: <string>
  signals: []
  entry_rules: []
  exit_rules: []
  risk_matrix: {}
  backtest_protocol: {}
  rl_contract: {}  # Level 4 only
  sources: []
  codex_handoff: {}
```

## Quality Gates

- Every signal must have a source citation.
- Risk matrix must include `max_position_pct`, `max_portfolio_drawdown_pct`, `stop_loss_pct`.
- Backtest protocol must include out-of-sample period.
- Level 4 must include `rl_contract` block with reward function and safety bounds.
