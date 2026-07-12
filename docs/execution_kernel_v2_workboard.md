# Execution Kernel v2 Workboard

## Document edit lease

- Lease status: `free`
- Document editor: `none`
- Mission/task ID: `QP-KERNEL-V2`
- Acquired at: `none`

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-KERNEL-V2` |
| Received by | Codex |
| Mission lead | Codex |
| Lead model/version | GPT-5 Codex desktop |
| Goal | Make Level 3, Level 4, Level 5, professional risk-reduction, and KIS paper use one typed execution-kernel handoff without adding broker authority or weakening the accepted reservation/event contracts. |
| In scope | Repository-grounded contract, pure decision model, default-off shadow runner, normalized parity, gated level-by-level cutover, KIS paper last, independent audit, completion report. |
| Out of scope | Live trading, real KIS automatic tests, market-order enablement, partial-batch redesign, outbox/worker, authoritative accounting ledger, continuous runtime, flatten, Postgres/Kafka, multi-account, multi-broker, UI changes. |
| Safety constraints | Safe defaults stay false/mock; fake clients only; shadow has no broker/store/repository/audit/clock/env authority; KIS order POST remains coordinator-only; ambiguous POST is never retried; schema-v10 reservation and schema-v11 dual-write/provenance/fencing remain unchanged. |
| Completion criteria | All staged gates and audits in this board are accepted, Level 3/4/5/professional/KIS use the common handoff, no dual POST path exists, full backend/smoke pass, P0/P1=0, original dirty main is untouched. |

## Counterpart plan review

- Reviewer/model: Claude Code, first attempted with `claude-fable-5`, then
  `sonnet`; exact accepted model will be recorded with its commit.
- Review status: `pending`; Claude Code reported a session limit resetting at
  `2026-07-12 12:20 KST` after two background transport failures and one
  foreground timeout. Runtime implementation remains held.
- Codex draft findings:
  - all submission paths already converge on
    `HarnessService.submit_order_plan()`;
  - approval evidence and failure translation remain level-specific;
  - the first kernel boundary must consume already-computed authorization and
    risk evidence because current risk/authority functions read environment
    state;
  - shadow must run before the first transition, reservation, event, audit, or
    broker mutation; and
  - external-paper shadow cannot call buying power or durable preparation.
- Required substantive counterpart role: independently inspect the current
  call graph, revise the binding contract/workboard, commit the two-document
  artifact in `claude/qp-kernel-v2-review`, and identify any P0/P1 contract
  defect before `QP-KER-010` begins.

## Routing assessment

Score formula:
`domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`.

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `QP-KER-000A` repository draft | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Mission recipient with direct Gate 1/2 and repository integration context. |
| `QP-KER-000B` binding review | Claude Code `claude-fable-5` or available exact model | 5 | 4 | 5 | 3 | 5 | 4.55 | Gate 2 contract review was accepted first pass; cross-model review is mandatory and disjoint. |
| `QP-KER-010` pure model | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Cross-cutting contracts and stateful integration are the best evidenced class; the slice is pure and bounded. |
| `QP-KER-010` pure model | Claude Code candidate | 4 | 4 | 3 | 3 | 4 | 3.65 | Capable challenger but lower current repository continuity; assigned review rather than overlapping implementation. |
| `QP-KER-020+` integration | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Existing scorecard strongly favors Codex for repository-wide stateful integration and release verification. |

## Work queue

Statuses: `proposed`, `ready`, `in_progress`, `review`, `integrated`, `done`,
`blocked`.

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-KER-000A` | GPT-5 Codex lead | Claude Code | Gate 2 | `주식트레이더-kernel-v2-contract` / `codex/qp-kernel-v2-contract` | `docs/execution_kernel_v2_contract.md`, `docs/execution_kernel_v2_workboard.md` | review | Repository inventory and decision-complete draft; no runtime edits; original main untouched. | draft `d3023a2` |
| `QP-KER-000B` | Claude Code exact model to be recorded | GPT-5 Codex lead | `QP-KER-000A` | `주식트레이더-claude-kernel-v2-review` / `claude/qp-kernel-v2-review` | the same two docs in an isolated review branch only | ready | Independent decomposition/revision committed; P0/P1 contract defects zero after lead cross-check; only two docs changed. | session reset required at 12:20 KST |
| `QP-KER-010` | GPT-5 Codex lead | Claude Code + independent read-only audit | accepted `QP-KER-000B` | new `주식트레이더-kernel-v2-pure` / `codex/qp-kernel-v2-pure` | `quantpilot/packages/core/execution/kernel.py`, `quantpilot/tests/unit/test_execution_kernel_v2.py` | proposed | Frozen pure input/output, closed decision order, deterministic fingerprint, forbidden-import and zero-side-effect tests. | pending |
| `QP-KER-020` | GPT-5 Codex lead | independent audit | `QP-KER-010` | new isolated worktree / `codex/qp-kernel-v2-shadow` | `quantpilot/packages/core/execution/kernel_shadow.py`, focused tests, minimal composition/config boundary | proposed | `off` default; unknown mode fails closed; shadow itself has no authoritative mutation; current-order expiry mismatch is fixed fail-closed rather than ignored; no second broker callable. | pending |
| `QP-KER-030` | best-fit test owner | independent auditor | `QP-KER-020` | separate parity worktree | kernel parity tests only | proposed | L1-2, L3 direct/ticket, L4 blocked/success, L5 dry/blocked/success, professional risk reduction, and fake KIS evidence parity; all side-effect counters zero. | pending |
| `QP-KER-035` | GPT-5 Codex lead | independent safety audit | `QP-KER-030` | separate facade worktree / `codex/qp-kernel-v2-facade` | `quantpilot/packages/core/execution/kernel_service.py`, focused facade tests, minimal dependency injection | proposed | One authoritative facade preserves snapshot/quote/binding/run/ATR/fence arguments, invokes the fence exactly once in legacy order, and reaches only the existing single submit boundary. | pending |
| `QP-KER-040` | GPT-5 Codex lead | Claude Code/read-only audit | `QP-KER-035` | separate Level 3 worktree | Level 3 adapters and tests | proposed | Direct and ticket paths use the common handoff; direct approval gains explicit persisted actor evidence or remains fixture mock/simulated-paper `unverified_local`; ticket actor binding is retained; no broker expansion. | pending |
| `QP-KER-050` | GPT-5 Codex lead | independent audit | `QP-KER-040` | separate Level 4 worktree | Level 4 adapter and tests | proposed | Existing `authorize_level4()` evidence is adapted once; default disabled and kill behavior unchanged. | pending |
| `QP-KER-060A` | GPT-5 Codex lead | independent audit | `QP-KER-050` | separate Level 5 worktree | ordinary operator adapter and tests | proposed | Existing Level 5 fallback/reporting preserved; no LLM/RL authority; common handoff used. | pending |
| `QP-KER-060B` | GPT-5 Codex lead | independent audit | `QP-KER-060A` | separate professional worktree | professional protective/retirement adapter and tests | proposed | Position-binding and reduce-only evidence preserved; no exposure-increasing regression. | pending |
| `QP-KER-070` | GPT-5 Codex lead | Claude Code + safety audit | `QP-KER-060B` | separate KIS-paper worktree | KIS composition adapter and fake-only tests | proposed | KIS remains coordinator-only; reservation/events/provenance/fencing/no-rePOST unchanged; no real network. | pending |
| `QP-KER-080` | mission lead | three independent audits where available | `QP-KER-070` | integration worktree | duplicate legacy orchestration only after proof | proposed | No alternate broker POST path; full parity/race/crash/restart/reconciliation suite; P0/P1=0; completion report. | pending |

Only one task per agent may be `in_progress`. No runtime task becomes
`in_progress` before `QP-KER-000B` is integrated.

## Dependency graph

```text
Gate 2 accepted
  -> QP-KER-000A Codex draft
  -> QP-KER-000B Claude binding review
  -> QP-KER-010 pure model
  -> QP-KER-020 off/shadow runner
  -> QP-KER-030 exhaustive parity
  -> QP-KER-035 common authoritative facade
  -> QP-KER-040 Level 3 cutover
  -> QP-KER-050 Level 4 cutover
  -> QP-KER-060A Level 5 ordinary cutover
  -> QP-KER-060B professional risk-reduction cutover
  -> QP-KER-070 KIS paper last
  -> QP-KER-080 legacy removal/final audit
  -> QP-DURABLE-OUTBOX (next roadmap mission)
```

## Integration requests

- `QP-KER-000B -> QP-KER-010`: approve or revise the exact evidence fields,
  stage order, purity/import boundary, mismatch policy, and cutover sequence.
- `QP-KER-010 -> QP-KER-020`: expose only frozen value objects and a pure
  evaluator; do not introduce ports that can write.
- `QP-KER-020 -> QP-KER-030`: return structured in-memory comparison evidence;
  do not persist a shadow result.
- `QP-KER-030 -> QP-KER-040+`: each level needs its own accepted parity corpus
  before authority switches.
- `QP-KER-030 -> QP-KER-035`: introduce one facade that preserves every
  existing submission argument and fence ordering; it may delegate only to the
  accepted legacy submit boundary and may not receive a broker client directly.
- `QP-KER-035 -> QP-KER-040+`: level adapters call the facade, not a new broker
  port; each cutover proves one facade call and at most one existing submit.
- `QP-KER-060B -> QP-KER-070`: KIS cutover may compose only with the existing
  coordinator and store provenance; it cannot call the client directly.
- `QP-KER-080 -> QP-DURABLE-OUTBOX`: preserve the accepted typed handoff and
  coordinator no-rePOST semantics when command dispatch becomes durable.

## Blockers and authority requests

- Claude Code session capacity resets at `2026-07-12 12:20 KST`. This blocks
  the required counterpart artifact and therefore runtime implementation, but
  does not block Codex inventory/contract drafting. A hidden, docs-only Claude
  review process is scheduled for `12:21 KST`; it has no runtime ownership.
- Real KIS paper validation requires user credentials and explicit manual
  authority. It remains Gate P and does not block fake-only contract/model work.
- Reproduced P1 pre-cutover finding: current Level 3 legacy submission can fill
  an order whose `OrderPlan.expires_at` is already past while its risk evidence
  remains fresh. This does not block the pure model, but it blocks shadow parity
  acceptance and every level cutover until a fail-closed fix and independent
  regression audit land.
- The original main worktree contains user-owned modified and untracked files.
  It is not used for implementation, staging, or integration.

## Verification matrix

### Contract task

```powershell
git diff --check
git status --short
git diff --name-only 8eaf15a..HEAD
```

Only the two Kernel v2 documents may change in each contract/review branch.

### Pure-model task

```powershell
python -m pytest quantpilot/tests/unit/test_execution_kernel_v2.py `
  -p no:cacheprovider --basetemp=.pytest_tmp_kernel
python -m pytest quantpilot/tests `
  -p no:cacheprovider --basetemp=.pytest_tmp_kernel_full
python -m quantpilot.jobs.run_smoke
git diff --check
```

### Shadow and cutover tasks

In addition to the pure/full checks:

```powershell
python -m pytest `
  quantpilot/tests/integration/test_level3_flow.py `
  quantpilot/tests/integration/test_level4_guarded_flow.py `
  quantpilot/tests/integration/test_level5_operator_run_once.py `
  quantpilot/tests/unit/test_approval_tickets.py `
  quantpilot/tests/unit/test_harness_batch_risk.py `
  quantpilot/tests/unit/test_external_paper_harness_integration.py `
  quantpilot/tests/unit/test_paper_submission_coordinator.py `
  quantpilot/tests/unit/test_paper_execution_shadow_parity.py `
  -p no:cacheprovider --basetemp=.pytest_tmp_kernel_focused
```

Required invariant evidence:

- mock smoke reports `live_trading_enabled=false` and Level 5 remains blocked
  under defaults;
- shadow broker/client/store/repository/audit counters are zero;
- KIS order POST static authority count is unchanged;
- `outcome_unknown`, restart, kill, and reconciliation never re-POST;
- schema-v10 reservation and schema-v11 event parity suites remain green; and
- no secret/account/raw payload appears in kernel evidence.

## Checkpoint log

- `2026-07-12 KST` — Gate 2 accepted at `8eaf15a`; full backend `1003 passed,
  2 skipped`; safe smoke; three final audits P0/P1/P2=0.
- `2026-07-12 KST` — Codex created clean
  `codex/qp-kernel-v2-contract` worktree at `8eaf15a`; original main remained
  untouched.
- `2026-07-12 KST` — Read-only inventory confirmed all current submission
  levels converge on `HarnessService.submit_order_plan()` and KIS POST remains
  coordinator-only. Level 4 lacks a current successful integration fixture,
  which must be added to the parity corpus.
- `2026-07-12 KST` — Claude background attempt `d2ac7d89` stopped at a project
  MCP approval prompt; safe-mode background `5e49f1b2` lost its control pipe;
  foreground Fable produced no file/output before the ten-minute timeout; the
  Sonnet retry returned the explicit `12:20 KST` session reset. All attempts
  left the Claude worktree clean.
- `2026-07-12 KST` — Codex drafted the decision-complete contract and this
  workboard as `d3023a2`. No runtime implementation started.
- `2026-07-12 KST` — Read-only feasibility review corrected two draft
  assumptions before counterpart review: mutable `OrderIntent` cannot be
  embedded in a mechanically frozen input, and the shadow hook can precede the
  first submission-phase mutation but not planning/approval/professional writes
  that already occurred before `submit_order_plan()`.
- `2026-07-12 KST` — Submit-time source inspection found that the current
  order's `OrderPlan.expires_at` is not explicitly rejected by
  `submit_order_plan()`. The contract records this as a required fail-closed
  hardening delta; parity may not conceal it.
- `2026-07-12 KST` — Direct Level 3 approval inspection found no approver input
  or persisted `approved_by`. The contract now records the baseline as
  `unverified_local`, non-external fixture evidence and requires real actor
  binding before Level 3 external-paper cutover.
- `2026-07-12 KST` — Contract-branch baseline verification after the read-only
  review passed: backend `1003 passed, 2 skipped in 22.09s`; smoke remained
  `broker=mock`, live=false, Level 5 blocked with zero submitted operator
  orders. The temporary pytest tree was removed and only the two contract docs
  remained modified.
- `2026-07-12 KST` — A read-only/in-memory Level 4 success probe showed the
  default strategy correctly blocks at `strategy_promotion_approved`; with an
  explicitly injected approved Level 4 recipe and open-window function, the
  same fixture submits and fills three mock orders with live=false. This is the
  required success-parity fixture and does not justify changing safe defaults.
- `2026-07-12 KST` — The current-order expiry gap was reproduced rather than
  inferred: a past-expiry, user-approved Level 3 mock order with a still-valid
  risk check became `filled` with one broker fill. Classified P1 for cutover;
  runtime fix remains held for counterpart contract review.
- `2026-07-12 09:00 KST` — Scheduled hidden process PID `71524` to start the
  required Claude Code Fable review at `12:21 KST` in
  `claude/qp-kernel-v2-review`. Standard output/error are redirected to
  temporary `qp-kernel-claude-review.*.log` files. The prompt restricts writes
  to the two contract docs and requires its own reviewed commit.

## Handoff record

```text
task_id: QP-KER-000A
agent_and_model: GPT-5 Codex desktop
commit: d3023a2
owned_paths:
  - docs/execution_kernel_v2_contract.md
  - docs/execution_kernel_v2_workboard.md
acceptance_met: repository-grounded draft complete; counterpart acceptance pending
exact_checks: two-file allowlist and final diff check passed; backend 1003 passed, 2 skipped; smoke mock/live=false/operator blocked
known_limits: Claude Code review unavailable until 12:20 KST; no runtime work authorized
integration_requests: Claude must revise/accept the binding contract before QP-KER-010
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-KER-000A` | inventory/contract draft | GPT-5 Codex desktop | pending review | 0 | 0 | 0 | 0 | two-doc allowlist + diff check | in progress | 0 |
| `QP-KER-000B` | independent binding review | Claude Code exact model pending | not started | 0 | 0 | 0 | 0 | two-doc allowlist + diff check | session reset pending | 0 |

- Routing decision quality: pending counterpart and implementation evidence.
- Capability scorecard update: append only after an accepted task artifact.
- User-owned changes preserved: original main status remains exactly outside
  this worktree and was not staged, copied, reset, or cleaned.
- Remaining limitations: all runtime/cutover gates and manual KIS Gate P remain
  pending.
