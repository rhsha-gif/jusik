# /write-codex-handoff

Write a complete Codex implementation task handoff document from a finalized recipe.

## Usage

```
/write-codex-handoff [recipe-id or path] [--priority critical|high|medium|low]
```

## What This Command Does

Invokes the `codex-handoff-writer` skill to convert a finalized and reviewed recipe into a Codex-ready implementation task specification.

## Prerequisites

The recipe must have passed `/review-quant-recipe` before handoff is written. If no review file exists at `docs/quant_recipes/<recipe-id>-review.yaml`, run the review first.

## Invocation Instructions

When this command is invoked:

1. If no recipe ID is provided, ask: "Which recipe should I write a Codex handoff for?"
2. Check that `docs/quant_recipes/<recipe-id>-review.yaml` exists and has `decision: approved`.
3. If review is missing or not approved, report to user and suggest running `/review-quant-recipe` first.
4. Read the recipe YAML.
5. Invoke `codex-handoff-writer` skill with the recipe content.
6. The skill produces a complete handoff document including:
   - Goal statement
   - Input/output specifications with data schemas
   - Acceptance criteria (GIVEN/WHEN/THEN format)
   - Test stubs (function signatures only)
   - Out-of-scope list (must include: live trading, order execution, secret reading)
7. Write handoff to `docs/quant_recipes/<recipe-id>-codex-handoff.yaml`.
8. Report: task ID, complexity estimate, and top 3 acceptance criteria.

## Safety

- No broker API calls
- No executable orders
- No live trading code
- No secrets access
- Handoff is a specification — Codex writes the code

## Output

File: `docs/quant_recipes/<recipe-id>-codex-handoff.yaml`
