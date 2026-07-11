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

- Reviewer/model: Claude Code, exact resolved model to be recorded at handoff.
- Review status: `blocked_pending_retry` — Claude Code `fable` and `sonnet`
  attempts received account-level HTTP 429 with reset at
  `2026-07-11 15:40 KST`. No runtime implementation starts before this initial
  decomposition review succeeds.
- Requested substantive counterpart artifact: review and correct
  `docs/contracts/canonical_order_events_v1.md`, including aggregate boundaries,
  event identity, migration truthfulness, duplicate/order semantics, and the
  adversarial acceptance matrix.
- Mission lead acceptance duty: inspect the Claude commit, correct any widening
  of legacy transitions, run contract consistency checks, then authorize code.

## Repository-grounded decisions awaiting counterpart review

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
| Pure model/reducer | route after Claude review | 5 | 5 | 4 | 4 | 4 | 4.55 | Assignment waits for corrected contract and capability evidence. |
| SQLite dual-write/migration | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Existing store transaction/fencing work has verified continuity. |
| Independent final audit | separate read-only reviewer | 5 | 5 | 4 | 2 | 4 | 4.35 | Must not own the paths it audits. |

## Work queue

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-EVT-000` | GPT-5 Codex lead + read-only inventory/test/audit agents | Claude Code | Gate 1 candidate | `주식트레이더-exec-events-v1` / `codex/qp-exec-events-v1-contract` | repository inventory, contract draft, workboard | review_ready | Every mutation path, identifier meaning, and migration risk is grounded in code. | inventory + test design; two corrected re-audits P0/P1=0; draft pending Claude review |
| `QP-EVT-010` | Claude Code | GPT-5 Codex lead | `QP-EVT-000` | `주식트레이더-claude-exec-events-review` / `claude/qp-exec-events-v1-review` | contract corrections + adversarial acceptance artifact | blocked | Initial decomposition review is substantive, decision-complete, committed, and contains no runtime/network changes. | clean worktree created at `c0a625e`; retry after 15:40 KST reset |
| `QP-EVT-020` | routed after review | counterpart reviewer | `QP-EVT-010`, Gate 1 accepted | separate implementation branch | event model, canonical hash, pure reducer, focused tests | proposed | Deterministic replay; strict duplicate/gap/hash/provenance behavior; no side effects. | pending |
| `QP-EVT-030` | GPT-5 Codex lead | independent auditor | `QP-EVT-020` | implementation worktree | schema v11, append helpers, exhaustive dual-write, migration | proposed | Authoritative row/event batch commits or rolls back together at every mutation site. | pending |
| `QP-EVT-040` | GPT-5 Codex lead | Claude/internally independent reviewer | `QP-EVT-030` | integration worktree | parity corpus, race/fault/restart tests, report | proposed | Replay equals all authoritative observable fields; no broker side effects; full suite/smoke green. | pending |
| `QP-EVT-050` | independent read-only auditor | GPT-5 Codex lead | `QP-EVT-040` | no-write audit | complete diff and safety invariants | proposed | P0/P1 zero; no missing dual-write path or widened broker authority. | pending |

## Ownership map

| Path | Owner after routing | Rule |
|---|---|---|
| `docs/contracts/canonical_order_events_v1.md` | Claude review branch, then mission lead integration | Counterpart proposes corrections; lead resolves and integrates. |
| `quantpilot/packages/core/execution/events.py` | `QP-EVT-020` owner | Pure domain only; no DB/client imports. |
| `quantpilot/packages/core/execution/reducer.py` | `QP-EVT-020` owner | Pure deterministic reducer only. |
| `quantpilot/packages/db/sqlite_repositories.py` | GPT-5 Codex lead | Schema, migration, and same-transaction append integration. |
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

## Blockers and authority requests

- Gate 1 remains in review until Claude's final implementation audit can be
  retried after the account-level 429 reset. Gate 2 contract work may proceed,
  but implementation cannot.
- Claude's Gate 2 initial review is likewise pending rate-limit recovery.
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

## Handoff record

```text
task_id:
agent_and_model:
commit:
owned_paths:
acceptance_met:
exact_checks:
known_limits:
integration_requests:
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-EVT-000` | inventory/contract | GPT-5 Codex + read-only subagents | pending Claude review | 0 | 0 | pending | 2 contract correction cycles | docs/diff check | pending | pending |
