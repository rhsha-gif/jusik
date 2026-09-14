---
name: codex-handoff-writer
description: "Write a complete Codex task handoff document when capability-based mission routing selected Codex. Produces a self-contained task specification with acceptance criteria, data schemas, performance constraints, and test stubs — ready for Codex to implement deterministically."
---

# codex-handoff-writer

Write a complete Codex task handoff document when capability-based mission routing selected Codex. Produces a self-contained task specification with acceptance criteria, data schemas, performance constraints, and test stubs — ready for Codex to implement deterministically.

This definition requires anthropic or openai. Before execution, the lead must select capabilityIds: [codex-handoff-writer] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:codex-handoff-writer; mode=bridge; edit .agents/aorch/definitions.json -->
