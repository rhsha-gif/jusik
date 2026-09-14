---
name: quant-source-synthesis
description: "Gather, rank, and summarize academic and practitioner sources relevant to a quantitative strategy hypothesis. Outputs a ranked source list with relevance notes and key claims for use in recipe authoring."
---

# quant-source-synthesis

Gather, rank, and summarize academic and practitioner sources relevant to a quantitative strategy hypothesis. Outputs a ranked source list with relevance notes and key claims for use in recipe authoring.

This definition requires anthropic or openai. Before execution, the lead must select capabilityIds: [quant-source-synthesis] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:quant-source-synthesis; mode=bridge; edit .agents/aorch/definitions.json -->
