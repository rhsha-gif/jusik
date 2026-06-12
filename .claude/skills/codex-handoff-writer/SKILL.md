---
name: codex-handoff-writer
description: >
  Write a complete Codex task handoff document from a finalized QuantPilot recipe.
  Produces a self-contained task specification with acceptance criteria, data schemas,
  performance constraints, and test stubs — ready for Codex to implement deterministically.
triggers:
  - "write codex handoff"
  - "codex task"
  - "handoff document"
  - "implementation spec"
  - "write task for codex"
model: claude-fable-5
---

# Codex Handoff Writer Skill

## Purpose

Convert a finalized QuantPilot YAML recipe into a Codex-ready implementation task. The handoff must be unambiguous: Codex should be able to implement the strategy purely from this document without asking clarifying questions.

## Safety Constraints

- No broker API calls.
- No executable orders.
- No live trading code.
- No secrets access.
- The handoff is a specification — Codex writes the code, Claude does not.

## Handoff Document Structure

### 1. Task Header
- Task ID (derived from recipe ID)
- Priority: critical / high / medium / low
- Estimated complexity: XS / S / M / L / XL
- Depends on: (other task IDs)

### 2. Goal Statement
One paragraph: what must exist after implementation that does not exist now.

### 3. Input Specification
- Data sources and schemas (column names, types, date ranges)
- Configuration parameters with types and valid ranges
- Environment variables required (names only, not values)

### 4. Output Specification
- File paths or database tables produced
- Schema of output (column names, types, cardinality)
- Performance SLA (e.g., must complete in < 5 min on 10 years of daily data)

### 5. Acceptance Criteria
Written as testable assertions:
- `GIVEN [state] WHEN [action] THEN [observable outcome]`
- Each criterion must be verifiable by a unit or integration test.

### 6. Test Stubs
- List test function signatures and what each must assert.
- Do not implement tests — Codex writes them from the stubs.

### 7. Out of Scope
Explicit list of what Codex must NOT implement in this task.

## Output Format

```yaml
codex_handoff:
  task_id: <string>
  recipe_id: <string>
  priority: critical | high | medium | low
  complexity: XS | S | M | L | XL
  depends_on: []
  goal: <string>
  inputs:
    data_sources: []
    parameters: []
    env_vars: []
  outputs:
    artifacts: []
    performance_sla: <string>
  acceptance_criteria: []
  test_stubs: []
  out_of_scope: []
  notes: <string>
```

## Quality Gates

- Every acceptance criterion must be independently verifiable.
- No acceptance criterion may reference broker credentials or live data.
- `out_of_scope` must include: live trading, order execution, secret reading.
- `env_vars` must list names only — never values or defaults that reveal secrets.
