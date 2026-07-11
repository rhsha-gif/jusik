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

- Reviewer/model: Claude Code Opus alias (exact resolved ID not exposed), with `claude-fable-5` finalization
- Review status: `completed and integrated`; Codex review corrected gross-exposure scope, integer arithmetic, and atomic store ownership before implementation
- Decomposition findings: existing KIS dispatch journal must be extended rather than duplicated; reserve+prepare and terminal dispatch+release are paired SQLite transactions; schema v10 backfills every open v9 dispatch conservatively
- Required substantive counterpart role: delivered the roadmap acceptance matrix, binding risk-reservation contract, and Mission 1 workboard in commit `215a4b9`.

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
| `QP-RISK-RES-V1` | GPT-5 Codex lead | Claude Code independent audit | `QP-RM-00`, `QP-RM-00A` | `주식트레이더-risk-reservation-v1` / `codex/qp-risk-reservation-v1-core` | risk reservation model/store/integration/tests/report | review | Schema v10 atomic cash/sell-quantity/gross-exposure reservation passes concurrency, crash, migration, full-suite, and smoke gates; integration awaits Claude audit retry. | `ce075bf`; `884 passed, 2 skipped`; smoke mock/live=false; internal audit P0/P1=0; Claude `429` retry pending |
| `QP-EXEC-EVENTS-V1` | best-fit routed per scorecard | independent auditor | `QP-RISK-RES-V1` contract stability | separate worktree | canonical events/reducer/shadow parity | proposed | Replay is deterministic; duplicates/out-of-order events cannot corrupt projections. | pending |
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
| `QP-RM-00` | baseline integration | GPT-5 Codex | yes | 0 | 0 | 0 | 0 | 819 tests + smoke + disabled CLI | pending | 5 |

- Routing decision quality: `pending`
- Capability scorecard update: `pending`
- User-owned changes preserved: original worktree remains untouched; its pre-existing modified and untracked paths were not staged or copied
- Remaining limitations: manual KIS paper validation and all post-baseline missions remain pending
