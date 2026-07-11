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

- Reviewer/model: Codex sub-agent (same runtime; cross-vendor reviewer unavailable in this harness)
- Review status: `in_progress`
- Decomposition findings: `pending`
- Required substantive counterpart role: independently author the roadmap acceptance matrix and audit the Mission 0/1 gate boundaries from repository evidence.

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
| `QP-RM-00` | GPT-5 Codex lead | counterpart | none | `주식트레이더-roadmap-baseline` / `codex/qp-roadmap-baseline-v9` | baseline integration, this workboard | in_progress | Kill v1 is fast-forwarded from clean main, full backend and smoke pass, user dirty tree unchanged. | `216ff22`; checks pending |
| `QP-RM-00A` | Codex sub-agent | GPT-5 Codex lead | `QP-RM-00` baseline | separate sibling worktree / `codex/qp-roadmap-acceptance-audit` | `docs/roadmap_acceptance_matrix.md` | proposed | Repository-grounded gate dependencies, invariants, and exact acceptance evidence are decision-complete. | pending |
| `QP-RISK-RES-V1` | GPT-5 Codex lead | independent auditor | `QP-RM-00`, `QP-RM-00A` | new sibling worktree / `codex/qp-risk-reservation-v1-core` | risk reservation contract/model/store/integration/tests/report | proposed | Schema v10 atomic cash/sell-quantity/gross-exposure reservation passes concurrency, crash, migration, full-suite, and smoke gates. | pending |
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
| `QP-RM-00` | baseline integration | GPT-5 Codex | pending | 0 | 0 | 0 | 0 | pending | pending | 0 |

- Routing decision quality: `pending`
- Capability scorecard update: `pending`
- User-owned changes preserved: original worktree remains untouched; final status evidence pending
- Remaining limitations: manual KIS paper validation and all post-baseline missions remain pending
