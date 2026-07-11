# Canonical Execution Events v1 Workboard

## Document edit lease

- Lease status: `free`
- Document editor: `none`
- Mission/task ID: `QP-EXEC-EVENTS-V1`
- Acquired at: `none`

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-EXEC-EVENTS-V1` |
| Received by | Codex mission lead |
| Mission lead | GPT-5 Codex (desktop) |
| Goal | Add a deterministic, append-only canonical execution-event shadow journal without changing broker authority or the schema-v10 state machines. |
| In scope | Contract, pure reducer, schema v11, truthful import anchors, exhaustive same-transaction dual-write, replay/parity/fault tests, audit, and completion report. |
| Out of scope | Live/network calls, source-of-truth cutover, Kernel v2, ledger/accounting, replace/correction/bust, Postgres/Kafka, UI, and market orders. |
| Safety constraints | All live/autonomy/market flags remain false; mock broker; fake-only automatic tests; no ambiguous POST retry; original dirty worktree untouched. |
| Completion criteria | Gate 1 accepted; Claude initial review integrated; replay and shadow parity pass; all writes are atomic; full suite/smoke pass; P0/P1 zero. |

## Counterpart plan review

- Reviewer/model: Claude Code, exact model `claude-fable-5` (CLI alias `fable`).
- Review status: `accepted_and_integrated` — the initial decomposition review
  is committed as
  `docs/canonical_order_events_v1_claude_review.md` plus a corrected contract
  with residual P0=0/P1=0 (nine P1 and three P2 findings resolved, including
  decisions A-H), and the mission lead integrated and cross-checked it with two
  read-only audits. Historical `fable`/`sonnet` attempts were rate-limited
  without an artifact.
- Requested substantive counterpart artifact: review and correct
  `docs/contracts/canonical_order_events_v1.md`, including aggregate boundaries,
  event identity, migration truthfulness, duplicate/order semantics, and the
  adversarial acceptance matrix.
- Mission lead acceptance duty: completed; no legacy-transition widening was
  accepted, six P2 precision corrections were added, and QP-EVT-020A is
  authorized on isolated pure-domain paths.

## Accepted repository-grounded decisions

1. Gate 2 is a same-transaction shadow journal; schema-v10 rows remain source of
   truth and no cutover occurs.
2. Order, reservation, and cancel are separate streams with independent event
   versions and one `order_plan_id` correlation.
3. Current KIS aggregate fill hashes are `evidence_id`, not true `execution_id`.
4. Import creates one truthful deterministic snapshot event per existing row and
   never fabricates historical transitions.
5. Pure replay does not sort timestamps, query a broker, write a projection, or
   repair either side of a parity mismatch.
6. All authoritative mutation sites, including claim/takeover/recovery/cancel
   paths that bypass the generic updater, must append inside the same transaction.

## Routing assessment

Score formula: `domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`.

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Repository inventory and first contract draft | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 5 | 5.00 | Owns roadmap integration and schema-v10 context. |
| Initial decomposition and contract challenge | Claude Code | 5 | 4 | 5 | 4 | 5 | 4.65 | Required independent counterpart; produces a reviewable contract commit, not broker code. |
| Pure model/reducer | Claude Code `claude-fable-5` | 5 | 5 | 4 | 5 | 5 | 4.65 | Authored the accepted decision-complete review and has continuity on the pure contract; receives only new pure-domain paths. |
| SQLite dual-write/migration | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Existing store transaction/fencing work has verified continuity. |
| Independent final audit | separate read-only reviewer | 5 | 5 | 4 | 2 | 4 | 4.35 | Must not own the paths it audits. |

## Work queue

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-EVT-000` | GPT-5 Codex lead + read-only inventory/test/audit agents | Claude Code | Gate 1 accepted | `주식트레이더-exec-events-v1` / `codex/qp-exec-events-v1-contract` | repository inventory, contract draft, workboard | done | Every mutation path, identifier meaning, and migration risk is grounded in code and incorporated into the accepted contract. | inventory + test design; internal re-audits P0/P1=0; Gate 1 merged; Claude review integrated |
| `QP-EVT-010` | Claude Code `claude-fable-5` | GPT-5 Codex lead | `QP-EVT-000` | `주식트레이더-claude-exec-events-review` / `claude/qp-exec-events-v1-review` | contract corrections + adversarial acceptance artifact | integrated | Initial decomposition review is substantive, decision-complete, committed, and contains no runtime/network changes. | `80e05d5`; findings R1-R12/decisions A-H closed; lead cross-check P0=0/P1=0; P2 precision corrections integrated |
| `QP-EVT-020A` | GPT-5 Codex fallback implementer | independent reviewer + mission lead | `QP-EVT-010`, Gate 1 accepted | `주식트레이더-exec-events-domain-fallback` / `codex/qp-exec-events-v1-domain-fallback` | new `transitions.py`, `events.py`, `reducer.py`; new event-model/reducer tests | integrated | Deterministic pure replay; strict duplicate/gap/hash/provenance/precedence behavior; no DB or broker imports. | implementation `64cffd6`; audit repairs `cb10b15`, `7e9bd53`; final independent re-audit P0/P1/P2/P3=0; `37 passed`; full `922 passed, 2 skipped`; safe smoke |
| `QP-EVT-020B` | GPT-5 Codex lead | independent read-only reviewer | `QP-EVT-020A` merged | event integration worktree | narrow SQLite transition import/re-export shim + equality regression only | in_progress | SQLite and pure transition definitions are identical without concurrent ownership overlap. | exact five-symbol shim mapped; full equality/identity regression pending |
| `QP-EVT-030` | GPT-5 Codex lead | independent auditor | `QP-EVT-020B` | implementation worktree | schema v11, append helpers, exhaustive dual-write, migration, mutation-origin call sites | proposed | Authoritative row/event batch commits or rolls back together at every mutation site. | pending |
| `QP-EVT-040` | GPT-5 Codex lead | Claude/internally independent reviewer | `QP-EVT-030` | integration worktree | parity corpus, race/fault/restart tests, report | proposed | Replay equals all authoritative observable fields; no broker side effects; full suite/smoke green. | pending |
| `QP-EVT-050` | independent read-only auditor | GPT-5 Codex lead | `QP-EVT-040` | no-write audit | complete diff and safety invariants | proposed | P0/P1 zero; no missing dual-write path or widened broker authority. | pending |

## Ownership map

| Path | Owner after routing | Rule |
|---|---|---|
| `docs/contracts/canonical_order_events_v1.md` | Claude review branch, then mission lead integration | Counterpart proposes corrections; lead resolves and integrates. |
| `quantpilot/packages/core/execution/transitions.py` | `QP-EVT-020A` Codex fallback owner, now integrated | New pure transition/classifier definitions; no DB/client imports. |
| `quantpilot/packages/core/execution/events.py` | `QP-EVT-020A` Codex fallback owner, now integrated | Pure domain only; no DB/client imports. |
| `quantpilot/packages/core/execution/reducer.py` | `QP-EVT-020A` Codex fallback owner, now integrated | Pure deterministic reducer only. |
| `quantpilot/packages/db/sqlite_repositories.py` | GPT-5 Codex lead | 020B owns only transition imports/re-exports after 020A merges; 030 then owns schema/migration/dual-write. Never concurrent with 020A. |
| execution submission/reconciliation/kill modules | GPT-5 Codex lead only if store API requires a narrow call-site change | Broker authority and no-rePOST behavior cannot change. |
| event-focused tests/fixtures | split by task with explicit non-overlap | No network or secrets. |
| original main worktree | user | Never edit, stage, clean, or overwrite. |

## Hard gates

1. **Contract gate:** Claude initial review commit integrated; Gate 1 accepted.
2. **Pure-domain gate:** reducer tests pass without database/broker imports.
3. **Atomicity gate:** injected failure proves every state/event batch rolls back.
4. **Parity gate:** full lifecycle corpus replays exactly after each transition.
5. **Audit gate:** independent P0/P1 count is zero.
6. **Integration gate:** backend, smoke, and diff checks pass with mock/live=false.

No later gate can be waived because an earlier test count is high.

## Required adversarial evidence

- Exact duplicate no-op versus divergent event/version/hash corruption.
- Concurrent expected-version append has one winner.
- Fill-before-ack, late ack, cumulative-fill regression, and identity conflict.
- Cancel/fill races in both arrival orders with no repost or speculative release.
- Terminal order and reservation release all-or-nothing.
- v6-v10 truthful import, deterministic reopen, and migration rollback.
- Cross-account/provenance/fence rejection.
- Replay/restart performs zero broker calls and zero authoritative writes.
- Closed causation chains, mutation-origin/source mapping, store-derived event
  times, all five generic precedence branches, and the special-transition-first
  classifier each have executable vectors.
- Nonzero-revision cancel create, same-status cancel mutation, raw unknown
  schema/type, canonical preimage hashes, and all idempotent no-write paths fail
  or return without a partial row/event write.

## Blockers and authority requests

- Gate 1 is accepted and no longer blocks Gate 2.
- The contract/decomposition review, QP-EVT-020A pure domain, and its independent
  audit repairs are accepted; QP-EVT-020B may apply the narrow transition shim.
- Real KIS paper validation remains manual and is not needed for this fake-only
  development gate.
- No request for live, secrets, network access, or user-owned dirty-tree changes
  exists.

## Checkpoint log

- `2026-07-11 KST` — clean event-contract worktree created from the schema-v10
  risk-reservation candidate (`58715bd`); original dirty main stayed untouched.
- `2026-07-11 KST` — two independent read-only investigations mapped every
  dispatch/reservation/cancel mutation and produced an adversarial test matrix.
- `2026-07-11 KST` — mission lead drafted the schema-v11 shadow-journal contract;
  runtime implementation intentionally held for Claude initial review and Gate 1
  acceptance.
- `2026-07-11 KST` — two independent read-only contract/schema audits found and
  closed event-version, special-transition, secret-field, multi-fill identity,
  duplicate, terminal-conflict, conditional-release, and migration-provenance
  P1s; final internal re-audits report P0/P1 zero.
- `2026-07-11 12:55 KST` — a clean Claude review worktree/branch was created at
  `c0a625e`. A safe-mode Claude Code `sonnet` audit retry returned HTTP 429 with
  zero token/cost usage and an explicit `15:40 KST` reset; no files changed.
- `2026-07-11 KST` — accepted Gate 1 baseline `0a9b644` was merged into the
  event mission while retaining the event contract inventory and review queue.
  Gate 1 dependency is satisfied; no event runtime code has started.
- `2026-07-11 KST` — Claude Code `claude-fable-5` (CLI alias `fable`) completed
  the QP-EVT-010 initial decomposition review at reviewed HEAD `e08ef98`
  (clean tree verified first). Docs-only: corrected the contract (mutation-origin
  source map with new `local_submission_result`, runtime `event_id` rule, exact
  `causation_id`, payload `time_basis` removal, exact cumulative identity scope
  excluding the acceptance-only forwarding-org field, total event-type
  precedence, closed `occurred_at`/`received_at` derivation, pure reducer
  exception family and SQLite translation, typed per-transaction mutation batch
  guard, fail-closed cancel same-status exclusion) and recorded the review
  report with findings R1-R12 (9 P1, 3 P2) all resolved; residual P0=0/P1=0.
  Decomposition confirmed for QP-EVT-020/030/040: 020 owns the pure domain plus
  the transition-map relocation first slice; 030 (lead) owns schema
  v11/append/guard/migration and the narrow mutation-origin call-site change;
  040 owns parity/race/fault tests. `git diff --check` clean; changed paths are
  exactly the three owned docs; no runtime, network, or safety-flag change.
  Mission-lead inspection/integration remains the implementation gate.
  Document lease released (free).
- `2026-07-11 KST` — GPT-5 Codex mission lead fast-forwarded `80e05d5`,
  verified its exact three-document scope, and ran two independent read-only
  cross-checks. Residual P0=0/P1=0. The lead corrected six nonblocking P2
  precision points: existing `broker_execution` evidence is supported in v1;
  special transitions precede generic classification; ordinary reopen does not
  retry imports; canonical hash preimages are typed JSON; raw unknown
  schema/type maps before strict parsing; explicit causation/origin/time/
  precedence/no-write tests are required. QP-EVT-020 was split into 020A pure
  new paths and 020B lead-owned SQLite re-export integration to eliminate the
  ownership overlap. Runtime remains unchanged at this checkpoint.
- `2026-07-11 KST` — Claude Code `claude-fable-5` was first-routed to
  QP-EVT-020A in isolated branch `claude/qp-exec-events-v1-domain`. The
  invocation ended with account `429 session limit` (reset 01:40 KST) before
  creating or modifying any file; the branch remained clean at `0e9bab9`.
  Per the availability fallback rule, the mission lead rerouted the same exact
  five-path pure-domain scope to a separate Codex fallback worktree. This is an
  availability deviation, not Claude quality evidence; Claude's substantive
  contract review `80e05d5` remains integrated.
- `2026-07-11 KST` — Codex fallback implemented QP-EVT-020A in the exact five
  authorized new paths. Independent audit reproduced two provenance/binding
  P1s and later a reservation-release provenance P1; fixes landed separately
  and transparently as `cb10b15` and `7e9bd53`. Final re-audit exercised the
  complete release matrix and reported P0/P1/P2/P3 zero. Mission-lead checks on
  the integrated commits: focused `37 passed`, full backend
  `922 passed, 2 skipped`, smoke mock/live=false/operator blocked, no DB or
  broker imports, and clean diff/worktree. QP-EVT-020B is now unblocked.

## Handoff record

```text
task_id: QP-EVT-010
agent_and_model: Claude Code / claude-fable-5 (CLI alias fable)
commit: 80e05d5da5d36e545d8dabb2f196956651985022 on
  claude/qp-exec-events-v1-review, fast-forwarded and lead-reviewed
owned_paths:
  - docs/contracts/canonical_order_events_v1.md
  - docs/canonical_order_events_v1_claude_review.md
  - docs/canonical_order_events_v1_workboard.md
acceptance_met: yes — decision-complete corrected contract (decisions A-H
  closed) plus substantive review report; residual P0=0/P1=0
exact_checks: git diff --check clean; git diff --name-only limited to the three
  owned docs; docs-only task, no backend/smoke run per acceptance-matrix
  documentation-gate rule (lead re-runs on the clean integration commit)
known_limits: real KIS TR semantics remain Gate P manual evidence; existing
  schema-v10 broker_execution evidence maps to v1 venue_execution, while the
  current KIS production adapter has no known producer of that evidence shape
integration_requests:
  - QP-EVT-020A may start on new pure-domain paths only
  - land mutation_origin store-signature and call-site changes in one commit
  - merge 020A before lead-owned 020B transition re-export shim; start 030 only
    after 020B
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-EVT-000` | inventory/contract | GPT-5 Codex + read-only subagents | rework-required | 0 | 9 | 3 | 3 contract correction cycles | docs allowlist/diff checks + Claude/lead cross-check | completed 2026-07-11 KST | 2 |
| `QP-EVT-010` | independent decomposition/contract review | Claude Code `claude-fable-5` | yes | 0 | 0 | 0 | 0 | three-doc allowlist + `git diff --check`; lead cross-check P0/P1 zero | ~18.5m | 5 |
| `QP-EVT-020A` | pure canonical event model/reducer | GPT-5 Codex fallback implementer | rework-required | 0 | 3 | 3 | 2 audited repair cycles | `37 passed`; full `922 passed, 2 skipped`; safe smoke; final P0/P1/P2/P3 zero | completed 2026-07-11 KST | 3 |
