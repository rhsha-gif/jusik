# QuantPilot Roadmap Execution Workboard

## Document edit lease

- Lease status: `free`
- Document editor: `none`
- Mission/task ID: `QP-ROADMAP-EXECUTION`
- Acquired at: `none`

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-ROADMAP-EXECUTION` |
| Received by | Codex |
| Mission lead | Codex |
| Lead model/version | GPT-5 Codex (desktop) |
| Goal | Execute the approved staged roadmap from the verified KIS paper kill baseline through atomic reservation, canonical events, kernel cutover, authoritative ledger, continuous runtime, and paper-readiness gates. |
| In scope | Sequential gated missions, isolated worktrees, safety contracts, implementation, tests, independent audit, reports, and integration branches. |
| Out of scope | Live trading activation, global market-order enablement, secrets, account-wide/manual-order cancellation, automatic flattening before ledger readiness, and speculative infrastructure such as Kafka. |
| Safety constraints | `LIVE_TRADING_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock`; fake-client automatic tests only; no ambiguous broker POST retry. |
| Completion criteria | Every roadmap gate has its own accepted workboard and report; required backend/smoke/frontend checks pass; P0/P1 audit findings are zero; paper-readiness evidence is complete; live remains disabled. |

## Counterpart plan review

- Reviewer/model: Claude Code Opus alias (exact resolved ID not exposed), with
  `claude-fable-5` contract finalization and independent Gate 1 audit
- Review status: `completed and integrated`; Codex review corrected gross-exposure scope, integer arithmetic, and atomic store ownership before implementation
- Decomposition findings: existing KIS dispatch journal must be extended rather than duplicated; reserve+prepare and terminal dispatch+release are paired SQLite transactions; schema v10 backfills every open v9 dispatch conservatively
- Required substantive counterpart role: delivered the roadmap acceptance
  matrix, binding risk-reservation contract, and Mission 1 workboard in
  `215a4b9`, then independently audited the implementation in `c021a50` and
  closed QP-RES-A1 in follow-up `b280bef`.

## Routing assessment

Score: `domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`.

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `QP-RM-00` baseline integration | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 5 | 5.00 | Mission recipient with direct repository and integration context. |
| `QP-RM-00A` acceptance matrix | Codex sub-agent | 4 | 5 | 3 | 3 | 4 | 3.95 | Bounded independent artifact in a disjoint worktree; neutral comparable track record. |
| `QP-RISK-RES-V1` implementation | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Stateful SQLite/broker integration is the lead's strongest evidenced task class. |

## Work queue

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-RM-00` | GPT-5 Codex lead | Claude counterpart | none | `주식트레이더-roadmap-baseline` / `codex/qp-roadmap-baseline-v9` | baseline integration, this workboard, baseline report | done | Kill v1 is fast-forwarded from clean main, full backend and smoke pass, user dirty tree unchanged. | `216ff22`; `819 passed, 2 skipped`; smoke mock/live=false; CLI disabled |
| `QP-RM-00A` | Claude Code Opus alias + `claude-fable-5` | GPT-5 Codex lead | `QP-RM-00` baseline | sibling worktree / `claude/qp-roadmap-contracts` | acceptance matrix and reservation contract/workboard | integrated | Repository-grounded gate dependencies, invariants, and exact acceptance evidence are decision-complete after Codex correction. | Claude `215a4b9`; integrated `36d9a8a`; Codex hardening `5dff17a` |
| `QP-RISK-RES-V1` | GPT-5 Codex lead | Claude Code `claude-fable-5` | `QP-RM-00`, `QP-RM-00A` | `주식트레이더-risk-reservation-v1` / `codex/qp-risk-reservation-v1-core` | risk reservation model/store/integration/tests/report | done | Schema v10 atomic cash/sell-quantity/gross-exposure reservation passed concurrency, crash, migration, independent audit, baseline integration, full-suite, smoke, and default-blocked kill gates. | implementation/audit `5eb70a9`; Claude audit `c021a50` + follow-up `b280bef`; QP-RES-A1 closed; residual P0=0/P1=0; `885 passed, 2 skipped`; smoke mock/live=false/blocked; kill CLI `paper_kill_disabled` |
| `QP-EXEC-EVENTS-V1` | GPT-5 Codex lead; Claude initial contract reviewer pending | independent auditor | `QP-RISK-RES-V1` acceptance | `주식트레이더-exec-events-v1` / `codex/qp-exec-events-v1-contract` | canonical events/reducer/shadow parity | contract_review_ready | Replay is deterministic; duplicates/out-of-order events cannot corrupt projections; no implementation before Claude review. | repository inventory + adversarial test design; corrected internal contract/schema re-audits P0/P1=0; Gate 1 accepted; Claude review remains pending |
| `QP-KERNEL-V2` | best-fit routed per scorecard | independent auditor | reservation + events | separate worktree | shadow then gated cutover | proposed | Level 3/4/5 use one kernel, no shadow side effects, and no dual broker POST path. | pending |
| `QP-LEDGER-RUNTIME` | best-fit routed per scorecard | independent auditor | kernel cutover | separate missions/worktrees | ledger, reconciliation, continuous runtime | proposed | Projection replay, broker parity, continuous protection, and paper-readiness gates pass. | pending |

## Integration requests

- `QP-RM-00A -> QP-RISK-RES-V1`: acceptance matrix must bind the reservation transaction, release evidence, crash points, and schema migration gate.
- `QP-RISK-RES-V1 -> QP-EXEC-EVENTS-V1`: reservation lifecycle events must have stable aggregate identities before event dual-write begins.

## Blockers and authority requests

- Real KIS paper `VTTC0084R` validation requires user credentials and explicit manual authority; it blocks operational kill use, not fake-client development.
- The original `main` worktree has user-owned uncommitted changes. It will not be modified, staged, or used as the integration worktree.

## Checkpoint log

- `2026-07-11 KST` — GPT-5 Codex lead — created clean sibling worktree from `main` and fast-forwarded verified kill v1 through `216ff22`; original dirty worktree untouched.
- `2026-07-11 KST` — GPT-5 Codex lead — baseline verification passed: backend `819 passed, 2 skipped`; smoke `broker=mock`, `live=false`, Level 5 blocked; kill CLI default `paper_kill_disabled`.
- `2026-07-11 KST` — Claude Code — delivered acceptance matrix, schema v10 reservation contract, and Mission 1 workboard as `215a4b9`; no runtime or network changes.
- `2026-07-11 KST` — GPT-5 Codex lead — integrated Claude artifact as `36d9a8a`; review found a P1 omission/atomicity issue (gross exposure missing, float persistence, release assigned to in-memory applier) and corrected the contract before implementation. Claude follow-up was unavailable due session limit until 15:40 KST; deviation recorded.
- `2026-07-11 KST` — GPT-5 Codex lead — completed the Mission 1 implementation candidate and adversarial repair cycle; full backend `884 passed, 2 skipped`, smoke stayed mock/live=false, and internal independent audit closed at P0/P1 zero.
- `2026-07-11 11:53 KST` — Claude Code `claude-fable-5` final implementation audit was attempted read-only but account rate limiting returned `429`; integration remains in review and the audit will be retried after reset.
- `2026-07-11 KST` — Claude Code `claude-fable-5` completed the independent
  implementation audit (`c021a50`) with ACCEPT and residual P0=0/P1=0. Codex
  fixed the reproduced QP-RES-A1 migration error-contract P2 (`40071c7`), and
  Claude independently closed it in follow-up `b280bef`; residual P2=1/P3=1.
- `2026-07-11 KST` — GPT-5 Codex lead fast-forwarded Gate 1 into the clean
  roadmap baseline at `5eb70a9`. Authoritative integration verification:
  `885 passed, 2 skipped`; smoke `broker=mock`, live=false, operator blocked,
  fallback=`level5_flag_disabled`; kill CLI `paper_kill_disabled`;
  `git diff --check` clean. Gate 1 development status is `done`; Gate P remains
  manual and pending.
- `2026-07-11 KST` — Gate 2 repository inventory and adversarial test design
  completed in a clean worktree. A same-transaction schema-v11 shadow-journal
  contract was drafted; Gate 1 is now accepted, while runtime implementation
  remains held for Claude's required initial decomposition review.
- `2026-07-11 12:55 KST` — Claude Code `sonnet` Gate 2 review preparation was
  rate-limited with zero output. The isolated review worktree remains available;
  no runtime work started and the later successful Gate 1 audit does not count
  as the separate Gate 2 contract review.

## Handoff record

```text
task_id: QP-RISK-RES-V1
agent_and_model: GPT-5 Codex lead + Claude Code claude-fable-5 auditor
commit: implementation/audit integrated through 5eb70a9 on
  codex/qp-roadmap-baseline-v9
owned_paths: schema-v10 reservation model/store/coordinator/guardrail tests and
  Gate 1 contract, audit, workboard, acceptance, and completion documents
acceptance_met: yes for fake-only Gate 1 development; residual P0=0/P1=0
exact_checks: 885 passed, 2 skipped; smoke mock/live=false/operator blocked;
  kill CLI paper_kill_disabled; git diff --check clean
known_limits: real KIS buying-power/cancel semantics remain Gate P manual
  evidence; QP-RES-A2 P2 and QP-RES-A3 P3 remain non-blocking
integration_requests: preserve reservation aggregate identity while starting
  QP-EXEC-EVENTS-V1; do not treat Gate 1 as paper-operational readiness
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-RM-00` | baseline integration | GPT-5 Codex | yes | 0 | 0 | 0 | 0 | 819 tests + smoke + disabled CLI | pending | 5 |
| `QP-RISK-RES-V1` | atomic reservation + independent audit | GPT-5 Codex + Claude Code `claude-fable-5` | no | 0 | 2 | 2 | 5 | 885 tests + smoke + disabled kill CLI + audit | completed 2026-07-11 KST | 4 |

- Routing decision quality: contract authorship and independent audit were
  correctly separated; the audit reproduced one fail-closed P2 and verified its
  closure without receiving runtime write authority.
- Capability scorecard update: Claude-owned evidence update is handled as a
  separate bounded documentation task.
- User-owned changes preserved: original worktree remains untouched; its pre-existing modified and untracked paths were not staged or copied
- Remaining limitations: manual KIS paper validation and all post-Gate-1
  missions remain pending; one conservative P2 and one diagnostic P3 are open.
