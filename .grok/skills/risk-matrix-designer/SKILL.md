---
name: risk-matrix-designer
description: "Design a quantitative risk matrix for a trading strategy: position sizing, drawdown limits, correlation budgets, stop-loss levels, and Kelly-based allocation formulas. Outputs a structured YAML risk block."
---

# risk-matrix-designer

Design a quantitative risk matrix for a trading strategy: position sizing, drawdown limits, correlation budgets, stop-loss levels, and Kelly-based allocation formulas. Outputs a structured YAML risk block.

This definition requires anthropic or openai. Before execution, the lead must select capabilityIds: [risk-matrix-designer] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:risk-matrix-designer; mode=bridge; edit .agents/aorch/definitions.json -->
