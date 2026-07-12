# Execution Kernel v2 Workboard

## Document edit lease

- Lease status: `held`
- Document editor: `Codex mission lead`
- Mission/task ID: `QP-KER-000C`
- Acquired at: `2026-07-12 22:08 KST`

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
- Review status: `in progress but not accepted`. Claude Code made a partial,
  uncommitted one-file revision in its isolated worktree, then reported an
  account reset at `2026-07-13 00:30 KST`. That worktree is preserved exactly;
  the partial status wording is not integrated. Runtime implementation remains
  held.
- Codex draft findings:
  - all submission paths already converge on
    `HarnessService.submit_order_plan()`;
  - approval evidence and failure translation remain level-specific;
  - the first kernel boundary must consume already-computed authorization and
    risk evidence because current risk/authority functions read environment
    state;
  - each stage-local shadow observation must run immediately before the
    specific legacy mutation or blocked return it compares, while the ready
    hook remains before the first submission mutation; and
  - external-paper shadow cannot call buying power or durable preparation.
- Required substantive counterpart role: independently inspect the current
  call graph and hardening revision, revise the binding contract/workboard,
  commit the two-document artifact in a fresh/clean Claude review branch, and
  close every P1 before `QP-KER-010` begins.

Three independent Codex read-only audits completed while Claude was capacity
limited:

- authorization/binding: P0=0, P1=6;
- purity/decision/parity: P0=0, P1=7, P2=2; and
- KIS/durable cutover: P0=0, P1=4, P2=1.

Overlapping findings are consolidated into `QP-KER-000C`; audit counts are not
added as though they were independent defects.

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
| `QP-KER-000B` | Claude Code exact model to be recorded | GPT-5 Codex lead | `QP-KER-000A` | `주식트레이더-claude-kernel-v2-review` / `claude/qp-kernel-v2-review` | the same two docs in an isolated review branch only | blocked | Preserve partial file; do not claim completion or integrate without a reviewed commit. | one uncommitted contract file; account reset `2026-07-13 00:30 KST` |
| `QP-KER-000C` | GPT-5 Codex lead | three read-only Codex audits | `QP-KER-000A`, Gate 2 mainline | `주식트레이더-kernel-v2-contract-hardening` / `codex/qp-kernel-v2-contract-hardening` | the same two docs only | in_progress | Rebind to `70219d4`; close decision/auth/purity/durable/KIS findings; no runtime edits. | pending commit |
| `QP-KER-000D` | Claude Code exact model to be recorded | GPT-5 Codex lead | committed `QP-KER-000C` | fresh isolated Claude review worktree/branch | the same two docs only | proposed | Independent final review/revision committed; only two docs changed; lead cross-check P0/P1=0. | becomes ready only after 000C commit and `00:30 KST` reset |
| `QP-KER-010` | GPT-5 Codex lead | Claude Code + independent read-only audit | accepted `QP-KER-000D` | new `주식트레이더-kernel-v2-pure` / `codex/qp-kernel-v2-pure` | `quantpilot/packages/core/execution/kernel.py`, `quantpilot/tests/unit/test_execution_kernel_v2.py` | proposed | Strict deeply frozen pure model, import allowlist, closed decision order, deterministic fingerprint, zero side effects. | pending |
| `QP-KER-015` | GPT-5 Codex lead | independent safety audit | `QP-KER-010` | separate expiry-hardening worktree | legacy submit/coordinator/store temporal checks and focused tests only | proposed | Use existing v11 order payload (no migration), strict expiry parse/effective deadline/reopen test; both safety fences, preclaim/restart/pre-POST; no ambiguous retry/release. | pending |
| `QP-KER-020` | GPT-5 Codex lead | independent audit | `QP-KER-015` | new isolated worktree / `codex/qp-kernel-v2-shadow` | `quantpilot/packages/core/execution/kernel_shadow.py`, focused tests, minimal stage-local adapters/config boundary | proposed | `off` default; unknown mode fails closed; real blocked-path evidence prefixes; zero authoritative mutation; no second broker callable. | pending |
| `QP-KER-030` | best-fit test owner | independent auditor | `QP-KER-020` | separate parity worktree | kernel parity tests only | proposed | L1-2, mock/sim L3, L4 blocked/success, L5 blocked/success, dry-run zero-call; professional closed; all shadow counters zero. | pending |
| `QP-KER-035` | GPT-5 Codex lead | independent safety audit | `QP-KER-030` | separate facade worktree / `codex/qp-kernel-v2-facade` | `quantpilot/packages/core/execution/kernel_service.py`, focused facade tests, minimal dependency injection | proposed | One authoritative facade preserves snapshot/quote/binding/run/ATR/fence arguments, invokes the fence exactly once in legacy order, and reaches only the existing single submit boundary. | pending |
| `QP-KER-040` | GPT-5 Codex lead | Claude Code/read-only audit | `QP-KER-035` | separate Level 3 worktree | Level 3 adapters and tests | proposed | Direct/ticket use common handoff only for mock/simulated paper; caller label is not actor assurance; KIS L3 stays blocked until separately authenticated subject binding. | pending |
| `QP-KER-050` | GPT-5 Codex lead | independent audit | `QP-KER-040` | separate Level 4 worktree | Level 4 adapter and tests | proposed | Existing `authorize_level4()` evidence is adapted once; default disabled and kill behavior unchanged. | pending |
| `QP-KER-060A` | GPT-5 Codex lead | independent audit | `QP-KER-050` | separate Level 5 worktree | ordinary operator adapter and tests | proposed | Existing Level 5 fallback/reporting preserved; no LLM/RL authority; common handoff used. | pending |
| `QP-KER-060B` | GPT-5 Codex lead | independent audit | `QP-KER-060A` | separate professional worktree | professional protective/retirement adapter and tests | proposed | Full frozen position/quote/reserved-quantity evidence lets kernel recompute current reduce-only predicate; opaque fingerprint never authorizes; no exposure-increasing regression. | pending |
| `QP-KER-065` | best-fit test owner | Claude Code + safety audit | `QP-KER-060B` | separate fake-KIS rehearsal worktree | KIS composition adapter in shadow plus fake-only tests | proposed | Actual SQLite/coordinator composition; fake client; zero shadow mutation/client queries; expiry/restart/claim/unknown/kill/reconciliation corpus; P0/P1=0. | pending |
| `QP-KER-070` | GPT-5 Codex lead | Claude Code + safety audit | `QP-KER-065` | separate KIS-paper worktree | KIS cutover adapter and fake-only tests | proposed | Separately default-off/reversible; coordinator-only POST; reservation/events/provenance/fencing/no-rePOST unchanged; no real network. | pending |
| `QP-KER-080` | mission lead | three independent audits where available | `QP-KER-070` | integration worktree | duplicate legacy orchestration only after proof | proposed | No alternate broker POST path; full parity/race/crash/restart/reconciliation suite; P0/P1=0; completion report. | pending |

Only one task per agent may be `in_progress`. No runtime task becomes
`in_progress` before `QP-KER-000D` is integrated.

## Dependency graph

```text
main `70219d4` accepted
  -> QP-KER-000A Codex draft
  -> QP-KER-000B partial Claude review (preserved, not accepted)
  -> QP-KER-000C Codex audit hardening
  -> QP-KER-000D Claude final binding review
  -> QP-KER-010 pure model
  -> QP-KER-015 temporal/durable expiry hardening
  -> QP-KER-020 off/shadow runner
  -> QP-KER-030 exhaustive parity
  -> QP-KER-035 common authoritative facade
  -> QP-KER-040 Level 3 cutover
  -> QP-KER-050 Level 4 cutover
  -> QP-KER-060A Level 5 ordinary cutover
  -> QP-KER-060B professional risk-reduction cutover
  -> QP-KER-065 fake-KIS shadow rehearsal
  -> QP-KER-070 KIS paper last
  -> QP-KER-080 legacy removal/final audit
  -> QP-DURABLE-OUTBOX (next roadmap mission)
```

## Integration requests

- `QP-KER-000D -> QP-KER-010`: approve or revise the exact evidence fields,
  stage order, purity/import boundary, mismatch policy, and cutover sequence.
- `QP-KER-010 -> QP-KER-015`: expose only frozen value objects and a pure
  evaluator; do not introduce ports that can write.
- `QP-KER-015 -> QP-KER-020`: close every order/risk/quote/snapshot durable
  expiry TOCTOU path before parity can treat ready evidence as safe.
- `QP-KER-020 -> QP-KER-030`: return structured in-memory comparison evidence;
  do not persist a shadow result.
- `QP-KER-030 -> QP-KER-040+`: each level needs its own accepted parity corpus
  before authority switches.
- `QP-KER-030 -> QP-KER-035`: introduce one facade that preserves every
  existing submission argument and fence ordering; it may delegate only to the
  accepted legacy submit boundary and may not receive a broker client directly.
- `QP-KER-035 -> QP-KER-040+`: level adapters call the facade, not a new broker
  port; each cutover proves one facade call and at most one existing submit.
- `QP-KER-060B -> QP-KER-065`: rehearse actual KIS-paper composition with a
  fake client, real SQLite/coordinator, and zero shadow mutations.
- `QP-KER-065 -> QP-KER-070`: KIS cutover may compose only with the existing
  coordinator and store provenance; it cannot call the client directly.
- `QP-KER-080 -> QP-DURABLE-OUTBOX`: preserve the accepted typed handoff and
  coordinator no-rePOST semantics when command dispatch becomes durable.

## Blockers and authority requests

- Claude Code account capacity resets at `2026-07-13 00:30 KST`. This blocks
  `QP-KER-000D` and therefore runtime implementation, but not the docs-only
  Codex hardening. Do not retry repeatedly before the reset and do not edit or
  reset Claude's partial worktree.
- Real KIS paper validation requires user credentials and explicit manual
  authority. It remains Gate P and does not block fake-only contract/model work.
- Reproduced P1 pre-cutover finding: current Level 3 legacy submission can fill
  an order whose `OrderPlan.expires_at` is already past while its risk evidence
  remains fresh. The full fix includes both safety fences, durable deadline,
  preclaim, restart, and final pre-POST handling; one pre-submit check is not
  accepted.
- Main is clean except the user-owned untracked
  `CLAUDE.md.20260705.bak`. It is never read, edited, staged, committed, reset,
  or cleaned.

## Verification matrix

### Contract task

```powershell
git diff --check
git status --short
git diff --name-only 1bb6be4..HEAD
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
- `2026-07-12 KST` — Mainline integration advanced to `70219d4`: Drift and
  Gate 2 were accepted; backend `1046 passed, 2 skipped`; frontend `23 passed`
  plus build; smoke remained mock/live=false/operator blocked; OpenAPI stayed
  byte-exact. The user-owned backup remained untouched.
- `2026-07-12 KST` — Claude review produced a useful but incomplete one-file
  edit (current-order structural gap and unauthenticated ticket-label caveat),
  then hit the `2026-07-13 00:30 KST` account limit. No reviewed commit exists;
  its worktree remains preserved.
- `2026-07-12 KST` — Three read-only Codex audits found P0=0 and overlapping
  contract P1s in authorization binding, blocked-path parity, purity, deep
  immutability, decision precedence, professional reduce-only evidence,
  durable expiry/TOCTOU, KIS provenance, and sole-POST ownership.
- `2026-07-12 KST` — `QP-KER-000C` hardening began on a separate docs-only
  worktree. The contract was rebound to `70219d4`, KIS rehearsal Gate 065 and
  temporal Gate 015 were added, and all runtime work remained held for Claude
  final review.

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

```text
task_id: QP-KER-000C
agent_and_model: GPT-5 Codex desktop with three independent read-only audits
base: 1bb6be4 contract branch; governing repository main 70219d4
owned_paths:
  - docs/execution_kernel_v2_contract.md
  - docs/execution_kernel_v2_workboard.md
acceptance_met: pending final diff/audit/commit
known_limits: Claude final acceptance waits for 2026-07-13 00:30 KST reset; no runtime work authorized
integration_requests: fresh Claude review must commit P0/P1=0 acceptance before QP-KER-010
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-KER-000A` | inventory/contract draft | GPT-5 Codex desktop | rework required | 0 | 11 consolidated | 3 consolidated | 1 | two-doc allowlist + diff check | complete draft | pending |
| `QP-KER-000B` | independent binding review | Claude Code exact model pending | incomplete | 0 | not fully assessed | not fully assessed | 0 | reviewed two-doc commit | account reset pending | 0 |
| `QP-KER-000C` | contract hardening | GPT-5 Codex desktop + three read-only audits | pending audit | 0 | pending | pending | 1 | two-doc allowlist + diff check | in progress | 0 |
| `QP-KER-000D` | final independent binding review | Claude Code exact model pending | not started | 0 | 0 required | 0 required | 0 | reviewed two-doc commit | waits for reset | 0 |

- Routing decision quality: pending counterpart and implementation evidence.
- Capability scorecard update: append only after an accepted task artifact.
- User-owned changes preserved: original main status remains exactly outside
  this worktree and was not staged, copied, reset, or cleaned.
- Remaining limitations: all runtime/cutover gates and manual KIS Gate P remain
  pending.
