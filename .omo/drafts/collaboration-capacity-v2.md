---
slug: collaboration-capacity-v2
status: approved-for-execution
intent: clear
pending-action: write .omo/plans/collaboration-capacity-v2.md
approach: Preserve capability-first routing, then apply a separate capacity scheduler with a capacity-first near-tie rule, Claude 2 + Codex 1 lanes, and subscription-only fallback behavior.
---

# Draft: collaboration-capacity-v2

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
1 | Capability routing remains evidence-first and plan-neutral | active | docs/agent_capability_scorecard.md
2 | Capacity scheduler governs near-ties, lanes, waits, and fallback | active | docs/agent_collaboration_protocol.md
3 | Workboards capture capacity and actual-executor evidence | active | docs/agent_workboard_template.md
4 | Codex and Claude adapters enforce the same v2 contract | active | AGENTS.md, CLAUDE.md, .codex/README.md, .claude/commands/start-collaboration.md
5 | Tabletop and independent counterpart review prove the contract | active | docs/agent_collaboration_protocol.md, new mission workboard/review artifact

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
Capacity ordering | green > yellow > unknown > red | conservative observable scheduler state | yes
Clear fit winner unavailable | wait through the next reset before bounded fallback | preserves best-fit ownership | yes
Mixed-model work | exclude from preference updates unless contributions are attributable | prevents evidence contamination | yes

## Findings (cited - path:lines)

- Existing routing uses five fixed capability dimensions and a <=0.5 recipient-side tie rule: docs/agent_collaboration_protocol.md:50-80 and docs/agent_capability_scorecard.md:56-96.
- Existing WIP is one in-progress task per agent and lacks capacity fields: docs/agent_collaboration_protocol.md:88-109 and docs/agent_workboard_template.md:36-55.
- Historical 429/session-limit events caused same-family fallback and critical-path delay but were manually separated from quality evidence: docs/agent_capability_scorecard.md:241-271.
- User-owned dirty state is limited to CLAUDE.md.20260705.bak and must remain untouched.

## Decisions (with rationale)

- <=0.5 is an operational equivalence band; capacity state breaks the tie, then initial recipient breaks equal capacity.
- Claude has two lanes: one mutating/design lane and one read-only research/audit lane. Codex has one active lane total.
- Additional credits, PAYG, and auto top-up are forbidden; verified limits become capacity_wait.
- Capacity telemetry never changes R1-R5 or task-class preference evidence.
- Mission lead, independent counterpart artifact, disjoint worktrees, lead-only integration, and QuantPilot safety invariants remain unchanged.
- Claude 2 + Codex 1 are maximum concurrency ceilings, never quotas; unused lanes stay idle and Claude C1/C2 share one subscription pool.
- Capacity is sampled at dispatch and immediately before claim. Effective caps are Claude green=2, yellow/unknown=1, red=0; Codex non-red=1, red=0.
- Both pools red yields capacity_wait with no scheduled executor. Same-state non-red near-ties fall back to the initial recipient.
- Exact model IDs are recorded when exposed; otherwise the harness alias is recorded with exact_model_unavailable=true. Versions are never inferred.
- Same-vendor fallback is progress only and cannot satisfy the mandatory cross-vendor completion gate without explicit user waiver.

## Scope IN

- Core collaboration protocol, capability scorecard, workboard template, Codex/Claude adapters, current workflow reference, a new active mission workboard, and a substantive Claude review artifact.

## Scope OUT (Must NOT have)

- No product runtime, trading, broker, database, frontend, or test-behavior changes.
- No API credits, secrets, external connector calls, live trading, or modification of historical completed workboards.
- No quota-filling workload split and no price/plan field in capability scores.

## Open questions

- None. User approved 1A, 2A, and 3A, then explicitly authorized implementation.

## Approval gate
status: approved
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
