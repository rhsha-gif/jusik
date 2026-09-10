---
name: quantpilot-staged-safety-harness
description: Use when implementing, extending, auditing, or debugging QuantPilot Operator or similar staged trading-assistant systems with levels such as research copilot, signal assistant, approval-based autopilot, guarded autopilot, or deployment readiness. Applies to pre-harness work, Korean policy parsing, universe/signal/rebalance flows, order proposal and approval gates, mock/paper broker safety, live-trading defaults, risk matrices, audit logs, strategy promotion, RL output contracts, OpenAPI/frontend scaffolds, and staged completion reports.
---

# QuantPilot Staged Safety Harness

## Purpose

Implement staged trading automation without weakening safety invariants. Keep research, signal generation, proposal creation, approval, guarded execution, and deployment readiness separated by explicit contracts.

## Workflow

1. Read the stage contract first.
   - Inspect the referenced prompt, `AGENTS.md`, existing reports, README, env examples, schemas, repositories, broker adapters, API routes, strategy specs, jobs, and tests.
   - Identify the current level and the requested next level before editing.
   - Preserve user-modified prompt files and generated reports unless the task explicitly asks to update them.

2. State the safety invariants before implementation.
   - `LIVE_TRADING_ENABLED` must be false by default.
   - Broker mode must default to mock or paper, never live.
   - Do not print secrets or call live broker order endpoints.
   - Level 1-2 may produce research, analyst summaries, signals, target weights, stop/take-profit hints, and rebalance suggestions, but must not submit orders.
   - Level 3 may create proposals and submit only after explicit approval plus fresh risk checks.
   - Level 4 guarded automation must be disabled by default and fail closed behind kill switches, windows, risk budgets, stale quote checks, and strategy authorization.
   - RL or strategy-selection outputs must never directly approve or submit orders.

3. Map the pre-harness contracts.
   - Reuse existing objects such as `UserPolicy`, `StrategyRecipe`, `Signal`, `PortfolioPlan`, `AuditLogEvent`, `OperationReport`, `OrderIntent`, `RiskCheck`, `OrderPlan`, and mock/paper brokers.
   - Keep schema changes additive and defaulted where possible.
   - Prefer pure helpers for risk, authority, state transitions, strategy validation, and RL validation so tests can exercise them without FastAPI or brokers.
   - Keep API routes thin and service/repository rules centralized.

4. Write stage-specific tests first for behavior changes.
   - Environment/pre-harness: runtime readiness, safe env defaults, smoke command, and no live credentials exposure.
   - Level 1-2: policy parsing, universe blocklists, liquidity/data readiness, analyst reports not overriding signal action, look-ahead-safe technical indicators, action precedence, rebalance limits, and no broker submission.
   - Level 3: proposal generation, approval/rejection/modify states, idempotency keys, fresh risk checks at submit time, audit events, and explicit API states.
   - Level 4: kill switch, guarded run blocked by default, authority windows, strategy promotion, monthly/daily risk budgets, stale quote blocks, and fail-closed audit behavior.
   - RL/strategy: reject outputs that resemble orders, approvals, or broker authority.

5. Implement the smallest stage-complete slice.
   - For Level 1-2, keep data fixture-first or provider-boundary-first; do not invent market values.
   - For Level 3, separate executable-shaped proposals from approved submissions. Re-run risk checks immediately before submission.
   - For Level 4, add explicit autopilot status, kill switch, guarded run endpoint/job, and blocked reasons before any automation path can submit.
   - For frontend work, expose only the screen/routes required by the stage. Disable or omit controls for later levels.
   - For generated OpenAPI/hooks/frontend scaffolds, validate dependency health before continuing feature work.

6. Validate safety and compatibility.
   - Run targeted tests for the new level, then the full suite.
   - Run the documented smoke job and confirm mock/paper broker mode plus live trading disabled.
   - Regenerate OpenAPI or frontend clients only when contracts changed.
   - If the project is inside OneDrive or another synced folder and dependencies look corrupt, move validation to a clean non-synced working copy before blaming individual packages.

7. Write the required report.
   - Create or update the exact level report requested by the prompt.
   - Include implemented features, routes/screens, data assumptions, safety invariants preserved, tests and results, known limitations, and the next recommended prompt or stage.
   - Call out any validation that could not be run and why.

## Output

End with the stage status in the requested format when provided. Always include:

- Live trading enabled: yes/no.
- Broker mode used for validation.
- Stage safety invariant status.
- Tests and smoke results.
- Report path.
- Remaining blockers or next stage.

Stop when the requested level is complete, tests and smoke checks reflect the final code state, and no later-stage capability is silently enabled.
