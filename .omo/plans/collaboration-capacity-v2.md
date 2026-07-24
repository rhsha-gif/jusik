# collaboration-capacity-v2 - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** A revised collaboration protocol that still assigns work to the demonstrably stronger agent, while using Claude's larger subscription capacity to schedule near-ties, parallel research, and independent review without exhausting Codex capacity.

**Why this approach:** Capability ownership is decided before capacity is considered. Capacity only breaks an existing near-tie or controls timing, lanes, and availability fallback.

**What it will NOT do:** It will not assign work to Claude just to consume quota, treat subscription price as skill, enable paid overage, or weaken trading safety and independent-review gates.

**Effort:** Medium
**Risk:** Medium - the policy is cross-cutting and must stay identical across both agent adapters.
**Decisions to sanity-check:** Near-ties use capacity first; Claude runs two lanes and Codex one; paid overage stays disabled.

Your next move: Execute the approved plan through isolated worktrees and independent review. Full execution detail follows below.

---

> TL;DR (machine): Medium documentation-policy change across core protocol, evidence schema, workboard, and agent adapters; no runtime behavior changes.

## Scope
### Must have
- Preserve the 30/25/25/10/10 capability formula and evidence-based task-class preferences.
- Define <=0.5 as the near-tie band; choose the better capacity state, then the initial recipient when states match.
- Define capacity states, Claude 2 + Codex 1 maximum lane ceilings, a shared Claude subscription pool, subscription-only operation, capacity_wait, bounded fallback, cross-vendor merge blocks, and model-switch evidence handling.
- Add a capacity snapshot and separate fit owner, scheduled executor, active time, wait time, rate-limit, fallback, and actual-model fields to new mission workboards.
- Synchronize AGENTS.md, CLAUDE.md, Codex/Claude entrypoint documentation, and current workflow documentation.
- Produce and integrate an independent Claude counterpart review artifact.
### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do not alter QuantPilot runtime code, trading flags, broker behavior, tests, secrets, or external systems.
- Do not modify completed historical workboards or the user-owned CLAUDE.md.20260705.bak file.
- Do not put subscription price, nominal multiplier, or availability failures into R1-R5 or preference ratings.
- Do not use API credits, PAYG, or auto top-up.
- Do not weaken non-self-review, lead-only integration, disjoint worktree, or safety rules.
- Do not treat lane ceilings as quotas or schedule a lower-fit agent merely to keep a lane busy.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: no product tests; documentation-contract verification with git diff checks, local-link resolution, required-clause assertions, and tabletop scenarios.
- Evidence: <attemptDir>/task-<N>-collaboration-capacity-v2.<ext> (attemptDir = currentAttemptDir from 'omo ulw-loop status --json', .omo/evidence/ulw/<session>/<goalId>/a<attempt>; outside ulw-loop use .omo/evidence/)

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- Wave 1: create isolated worktrees, then create and commit the canonical active workboard before any worker edits.
- Wave 2: propagate the workboard baseline, then run Claude counterpart review and Codex core drafting in parallel.
- Wave 3: synchronize adapters and current-workflow documentation after the core wording stabilizes.
- Wave 4: integrate the Claude artifact, resolve findings, run contract checks, and commit the mission mainline candidate.
- Wave 5: independent final verification; leave the dirty root untouched and hand off the verified integration branch.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 2 | none |
| 2 | 1 | 3, 4 | none |
| 3 | 2 | 6 | 4 |
| 4 | 2 | 5, 6 | 3 |
| 5 | 4 | 6 | none (single Codex lane) |
| 6 | 3, 4, 5 | final verification | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Bootstrap isolated mission state and worktrees
  What to do / Must NOT do: Create codex/collab-capacity-v2-core and claude/collab-capacity-v2-review worktrees from f8162e2; record exact paths and preserve the dirty main workspace. Do not move, stage, stash, or edit CLAUDE.md.20260705.bak.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 2, 3
  References (executor has NO interview context - be exhaustive): AGENTS.md:5-17; docs/agent_collaboration_protocol.md:127-150; git status at f8162e2
  Acceptance criteria (agent-executable): git worktree list --porcelain shows both branches at f8162e2; main git status still lists only the pre-existing backup plus .omo execution state. Completed 2026-07-13 KST.
  QA scenarios (name the exact tool + invocation): Git Bash `git -C <worktree> status --short --branch` for both; failure probe verifies no target path resolves inside the main worktree. Evidence .omo/evidence/task-1-collaboration-capacity-v2.txt
  Commit: N | orchestration only

- [x] 2. Create and commit the canonical active workboard
  What to do / Must NOT do: In the Codex integration worktree, create docs/collaboration_capacity_v2_workboard.md before any other product-document edit. Record f8162e2, data_mode=fixture, mission-mainline definition, C1/C2/G1 ceilings and shared pool, capacity snapshot, Metis gap disposition, exact worktrees, dirty-root boundary, routing, queue, handoffs, and verification. Only the mission lead may edit this canonical board.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3, 4
  References (executor has NO interview context - be exhaustive): docs/agent_workboard_template.md; .omo/drafts/collaboration-capacity-v2.md; Metis findings in the current conversation; AGENTS.md:5-17
  Acceptance criteria (agent-executable): the new workboard is the only changed path, includes all required fields and conditional-GO corrections, and is committed on codex/collab-capacity-v2-core. Completed through c43dc13 + arithmetic correction c91d2ca; independent re-verification confirmed.
  QA scenarios (name the exact tool + invocation): Git Bash `git diff --check`, `git diff-tree --no-commit-id --name-only -r HEAD`, and required-field `rg`; failure probe rejects any second changed path. Evidence .omo/evidence/task-2-collaboration-capacity-v2.txt
  Commit: Y | docs(collab): start capacity v2 workboard

- [x] 3. Obtain a substantive Claude counterpart artifact
  What to do / Must NOT do: Fast-forward the clean Claude worktree to the committed workboard baseline, invoke Claude Code 2.1.205 to review the approved v2 decomposition and current governance, then write docs/collaboration_capacity_v2_claude_review.md with blocking findings, exact clauses, tabletop gaps, and verdict; commit only that file. Do not edit the canonical workboard or core protocol files.
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 6
  References (executor has NO interview context - be exhaustive): docs/collaboration_capacity_v2_workboard.md; AGENTS.md; docs/agent_collaboration_protocol.md; docs/agent_capability_scorecard.md; docs/agent_workboard_template.md; CLAUDE.md; .claude/commands/start-collaboration.md
  Acceptance criteria (agent-executable): artifact identifies exact resolved model when exposed or alias plus exact_model_unavailable=true; covers capability/capacity separation, ceilings-not-quotas, shared pool, both-red state, no-overage, fallback/cross-vendor gate, mixed-model evidence, dirty root, and at least fourteen tabletop checks; commit changes exactly one allowed path. Completed through final Claude artifact commit 8899333; independent final verification confirmed.
  QA scenarios (name the exact tool + invocation): Git Bash `git show --stat --oneline HEAD` and `git diff-tree --no-commit-id --name-only -r HEAD`; failure probe rejects any runtime, adapter, or canonical-board edit. Evidence .omo/evidence/task-3-collaboration-capacity-v2.txt
  Commit: Y | docs(collab): review capacity-aware protocol

- [ ] 4. Update the core collaboration contract
  What to do / Must NOT do: In the Codex worktree, update docs/agent_collaboration_protocol.md, docs/agent_capability_scorecard.md, and docs/agent_workboard_template.md with canonical ordering, lane ceilings, shared pool, capacity state machine, fallback, cross-vendor completion, evidence isolation, model-ID fallback, and dynamic file ownership. Do not change the five score weights, historical evidence rows, or QuantPilot safety rules.
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 5, 6
  References (executor has NO interview context - be exhaustive): approved user choices and Metis corrections in .omo/drafts/collaboration-capacity-v2.md; docs/collaboration_capacity_v2_workboard.md; docs/agent_collaboration_protocol.md:1-202; docs/agent_capability_scorecard.md:1-286; docs/agent_workboard_template.md:1-88
  Acceptance criteria (agent-executable): gap>0.5 selects capability winner; <=0.5 uses green>yellow>unknown>red, same non-red recipient tie, both-red capacity_wait; Claude caps 2/1/0 by state with shared pool, Codex 1/1/0; same-vendor fallback cannot complete mandatory counterpart; scorecard owner is dynamic; exact-model-unavailable and mixed-model rules are explicit.
  QA scenarios (name the exact tool + invocation): Git Bash required-clause matrix plus negative searches for stale unconditional recipient-first, fixed Claude file ownership, and availability inside R1-R5. Evidence .omo/evidence/task-4-collaboration-capacity-v2.txt
  Commit: N | committed with todo 6 after counterpart integration

- [ ] 5. Synchronize Codex and Claude entrypoints
  What to do / Must NOT do: Update AGENTS.md, CLAUDE.md, .codex/README.md, .claude/commands/start-collaboration.md, and docs/current_project_workflow.md section 12 so both sides execute the same v2 ordering and lane/fallback rules. Keep project safety commands and specialized workflows unchanged.
  Parallelization: Wave 3 | Blocked by: 4 | Blocks: 6
  References (executor has NO interview context - be exhaustive): core documents from todo 3; AGENTS.md:5-17; CLAUDE.md:1-43; .codex/README.md:1-31; .claude/commands/start-collaboration.md:1-20; docs/current_project_workflow.md:436-463
  Acceptance criteria (agent-executable): every adapter says capability before capacity, <=0.5 capacity-first, ceilings-not-quotas with shared pool, no additional credits/PAYG, and actual model recording; workflow says read-only audits may be recorded without a commit; no adapter contradicts mission-lead or safety rules.
  QA scenarios (name the exact tool + invocation): Git Bash clause matrix across all five files; failure probe searches for obsolete unconditional recipient tie, per-agent WIP=1, every-artifact-must-commit, or quota-filling wording. Evidence .omo/evidence/task-5-collaboration-capacity-v2.txt
  Commit: N | committed with todo 6

- [ ] 6. Integrate the Claude review, verify the contract, and commit mission mainline
  What to do / Must NOT do: Cherry-pick the single-file Claude review commit into the Codex worktree, inspect the diff, incorporate every confirmed blocking finding into the core/adapters, update the active workboard, and commit one cohesive protocol-v2 change. Do not silently dismiss a Claude finding; record accepted/rejected with evidence.
  Parallelization: Wave 4 | Blocked by: 3, 4, 5 | Blocks: final verification
  References (executor has NO interview context - be exhaustive): docs/collaboration_capacity_v2_claude_review.md; all files from todos 3-4; docs/agent_collaboration_protocol.md:151-177
  Acceptance criteria (agent-executable): Claude artifact is present; workboard maps every blocking finding to resolution; fourteen tabletop cases pass including both-red, ceilings-not-quotas, shared pool, and same-vendor non-substitution; commit includes only approved documentation/adapter paths and no .omo state.
  QA scenarios (name the exact tool + invocation): Git Bash `git diff --check HEAD^..HEAD`, `git show --name-status --format=fuller HEAD`, and allowlist comparison; failure probe rejects any path under quantpilot/ or the backup file. Evidence .omo/evidence/task-5-collaboration-capacity-v2.txt
  Commit: Y | docs(collab): adopt capacity-aware routing v2

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy

- Claude branch: one atomic review-artifact commit touching only docs/collaboration_capacity_v2_claude_review.md.
- Codex branch: one atomic protocol-v2 commit containing the reviewed governance, adapter, workflow, and active workboard changes.
- Mission mainline is codex/collab-capacity-v2-core. The dirty root main worktree is not patched, staged, merged, reset, stashed, or cleaned; no push.

## Success criteria

- Capability scoring remains unchanged and always precedes capacity scheduling.
- Near-ties use capacity state first and recipient continuity second.
- Claude 2 + Codex 1 lanes, no-overage, capacity_wait, bounded fallback, and cross-vendor safety gates are decision-complete.
- Lane counts are ceilings, not quotas; Claude C1/C2 share one pool, and both-red schedules nobody.
- Capacity/availability data cannot contaminate model capability evidence.
- Both agent entrypoints and the workflow reference agree with the core protocol.
- Claude provides a committed substantive counterpart artifact and every blocking finding is dispositioned.
- Documentation checks and tabletop scenarios pass; runtime files and user-owned dirty changes remain untouched.
