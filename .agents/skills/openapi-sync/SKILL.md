---
name: openapi-sync
description: Regenerate and verify the retained backend openapi.json after API contract changes.
---

# OpenAPI Sync

The web client was retired with user approval on 2026-09-10. Do not recreate its TypeScript artifact or run npm frontend checks.

After API contract changes, serialize quantpilot.services.api.main.app.openapi() to openapi.json using json.dumps(indent=2, ensure_ascii=False), UTF-8 and a trailing newline. Inspect the diff for intended changes, report path count, regenerate again to confirm byte-identical output, and run backend tests. Preserve historical evidence. Never create commits without an explicit request.

<!-- aorch-generated: skill:openapi-sync; mode=native; edit .agents/aorch/definitions.json -->
