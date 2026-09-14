# Bounded refactoring

Identify the behavior to preserve and the relevant existing verification before editing. Remove unnecessary code only within the authorized scope. Do not weaken tests to make a refactor pass. Report public API or behavior changes as decisions rather than silently including them.

Small refactors can be completed and reviewed by the current agent. Use an independent reviewer for risky or uncertain changes. `examples/plan-refactor.json` is optional; a removal assessment does not require a separate worker. Preserve aorch’s write-isolation rules and the project’s dependency and shared-process safeguards.
