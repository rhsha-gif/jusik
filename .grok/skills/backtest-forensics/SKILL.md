---
name: backtest-forensics
description: "Audit a backtest result for common failure modes: look-ahead bias, data snooping, overfitting, survivorship bias, unrealistic fill assumptions, and regime sensitivity. Outputs a forensics report with severity ratings and remediation steps."
---

# backtest-forensics

Audit a backtest result for common failure modes: look-ahead bias, data snooping, overfitting, survivorship bias, unrealistic fill assumptions, and regime sensitivity. Outputs a forensics report with severity ratings and remediation steps.

This definition requires anthropic or openai. Before execution, the lead must select capabilityIds: [backtest-forensics] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:backtest-forensics; mode=bridge; edit .agents/aorch/definitions.json -->
