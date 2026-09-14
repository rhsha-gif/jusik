---
name: openapi-sync
description: "Regenerate and verify the retained backend openapi.json after API contract changes. Do not recreate the retired web TypeScript artifact."
---

# openapi-sync

Regenerate and verify the retained backend openapi.json after API contract changes. Do not recreate the retired web TypeScript artifact.

This definition requires anthropic or openai. Before execution, the lead must select capabilityIds: [openapi-sync] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:openapi-sync; mode=bridge; edit .agents/aorch/definitions.json -->
