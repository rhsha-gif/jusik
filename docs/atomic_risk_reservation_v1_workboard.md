# QuantPilot Atomic Risk Reservation v1 Workboard

Mission workboard for `QP-RISK-RES-V1`, the schema-v10 atomic risk reservation
gate of the roadmap (`docs/roadmap_execution_workboard.md`, Gate 1 of
`docs/roadmap_acceptance_matrix.md`). Governing rules: root `AGENTS.md`,
`docs/agent_collaboration_protocol.md`, `docs/agent_capability_scorecard.md`.

## Document edit lease

- Lease status: `free`
- Document editor: `none`
- Mission/task ID: `QP-RISK-RES-V1`
- Acquired at: `none`

한 번에 한 에이전트만 이 문서를 수정한다. 편집자는 lease를 잡고 문서를 다시 읽은 뒤 최소 변경만 적용하고
즉시 해제한다.

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-RISK-RES-V1` |
| Received by | Codex (roadmap mission lead) |
| Mission lead | GPT-5 Codex (desktop) |
| Lead model/version | GPT-5 Codex (exact point release not exposed by harness) |
| Goal | Durable, atomic KIS-paper cash + sell-quantity reservation (schema v10) that admits concurrent long-only KRW whole-share limit orders up to — never beyond — evidenced capacity, and survives crash conservatively. |
| In scope | `PaperRiskReservation` model + table, one-transaction reserve+prepare, conservative release on definitive terminals, idempotency/provenance/revision/fencing, v9→v10 migration/backfill, concurrency/crash/migration tests. |
| Out of scope | Live trading, market orders, margin/short/derivatives, multi-currency, position flatten/cancel-all, Postgres/Kafka, any second broker POST path. |
| Safety constraints | `LIVE_TRADING_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock`; fake-client automatic tests only; no ambiguous-outcome capacity release; `DurablePaperSubmissionCoordinator` stays the sole POST authority. |
| Completion criteria | `docs/contracts/atomic_risk_reservation_v1.md` §10 tests present and green within `python -m pytest quantpilot/tests`; `python -m quantpilot.jobs.run_smoke` prints `broker=mock`/`live_trading_enabled=false`/operator blocked; migration preserves v9 state; independent audit reports zero P0/P1; `git diff --check` clean. |

## Counterpart plan review

- Reviewer/model: Claude Code (`claude-opus-4-8`, this session) authored the
  binding contract and acceptance matrix; independent audit reviewer to be an
  instance distinct from the implementer (implementer MUST NOT self-approve this
  safety-critical change — protocol §6).
- Review status: `contract-complete` (design bound); implementation review
  `pending`.
- Decomposition findings: reservation aggregate identity must be stable before
  `QP-EXEC-EVENTS-V1` dual-write (`roadmap_execution_workboard.md` integration
  requests); the reserve+prepare atomic transaction and the migration/backfill are
  the two highest-risk slices and each need dedicated tests
  (`atomic_risk_reservation_v1.md` §5, §9, §10).
- Required substantive counterpart role: Claude Code owns (a) the binding
  decision-complete contract + acceptance matrix (delivered under
  `QP-RM-00A`/this branch), and (b) the independent adversarial audit of the
  reservation implementation before mainline integration.

## Routing assessment

점수는 1~5이며 총점은 `domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`이다.
`track=3` = 비교 가능한 실적 없음(중립). Bounded seed 규칙은 stateful integration에는 적용하지 않는다
(격리·가역 조건 미충족, scorecard §3).

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `QP-RISK-RES-V1` impl | Codex GPT-5 lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Stateful SQLite/broker integration is `codex-gpt5x`'s best-sampled class (scorecard §6, n=5, ratings 4–5). |
| `QP-RISK-RES-V1` impl | Claude `claude-opus-4-8` | 4 | 4 | 3 | 3 | 3 | 3.55 | Strong on contracts/audit; less-sampled on this store-integration class; owns the binding design instead. |
| `QP-RISK-RES-V1-contract` | Claude `claude-opus-4-8` | 5 | 5 | 4 | 5 | 5 | 4.85 | Long-form safety contract + acceptance authoring is the Claude priority class; done on this branch. |
| `QP-RISK-RES-V1-audit` | Claude `claude-opus-4-8` | 5 | 5 | 4 | 4 | 5 | 4.75 | Independent adversarial audit is a Claude priority class and must be a non-implementer (protocol §6). |

## Work queue

상태: `proposed`, `ready`, `in_progress`, `review`, `integrated`, `done`, `blocked`.

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-RISK-RES-V1-contract` | Claude `claude-opus-4-8` | GPT-5 Codex lead | `QP-RM-00`, `QP-RM-00A` | `주식트레이더-claude-roadmap-contracts` / `claude/qp-roadmap-contracts` | `docs/roadmap_acceptance_matrix.md`, `docs/contracts/atomic_risk_reservation_v1.md`, `docs/atomic_risk_reservation_v1_workboard.md` | review | Decision-complete v10 model/arithmetic/transaction/migration/tests; internally consistent with current code; `git diff --check` clean. | this branch commit (see handoff) |
| `QP-RISK-RES-V1-impl` | GPT-5 Codex lead | Claude `claude-opus-4-8` (independent audit) | `QP-RISK-RES-V1-contract` | new sibling worktree / `codex/qp-risk-reservation-v1-core` | `operator/position_ledger.py` (reservation model), `db/sqlite_repositories.py` (table/migration/CAS methods), `execution/paper_submission.py` + `execution/paper_reconciliation_apply.py` (reserve/release hooks), `risk/*` (durable `H_qty` read), reservation tests | proposed | Contract §10 tests green in full suite; smoke safe-default; migration preserves v9; zero P0/P1. | pending |
| `QP-RISK-RES-V1-audit` | Claude `claude-opus-4-8` | GPT-5 Codex lead | `QP-RISK-RES-V1-impl` | read-only over impl branch | audit report only (no code) | proposed | Adversarial review of atomicity, conservative release, fencing, and migration; blocks merge on any P0/P1. | pending |

Owned-path disjointness: the contract task owns only the three docs; the impl task
owns the runtime paths; the audit task writes no code. No two active tasks share a
writable path.

## Integration requests

- `QP-RM-00A -> QP-RISK-RES-V1`: acceptance matrix (Gate 1) binds the one-transaction
  reserve+prepare, the conservative `outcome_unknown` hold, the definitive-terminal
  release evidence, and the v9→v10 migration/backfill gate.
- `QP-RISK-RES-V1 -> QP-EXEC-EVENTS-V1`: reservation lifecycle aggregate identities
  (`reservation_id` ↔ `order_plan_id`) must be stable before event dual-write begins.
- `QP-RISK-RES-V1-impl -> risk gate`: when a paper store is present, the risk
  gate/batch MUST read held sell reservations from the durable table rather than the
  in-memory `GuardrailState.reserved_sell_quantities`
  (`risk/gatekeeper.py:139-146`, `risk/batch.py:303-313`).

## Blockers and authority requests

- Real KIS paper `VTTC0084R` and real buying-power TR field validation require user
  credentials and explicit manual authority; they gate **paper operational
  readiness (Gate P)**, not fake-client development of this reservation mission
  (`roadmap_acceptance_matrix.md` §3, Gate P).
- The original `main` worktree holds user-owned uncommitted changes; it is not
  modified, staged, or used for integration (protocol §5).

## Checkpoint log

- `2026-07-11 KST` — Claude Code `claude-opus-4-8` — authored `QP-RM-00A` acceptance
  matrix, the v10 reservation contract, and this workboard from repository evidence
  (`operator/position_ledger.py`, `db/sqlite_repositories.py`,
  `execution/paper_submission.py`, `risk/gatekeeper.py`, `risk/batch.py`,
  `docs/contracts/kis_paper_kill_contract.md`); docs-only, no runtime change;
  `git diff --check` clean.

## Handoff record

```text
task_id: QP-RISK-RES-V1-contract
agent_and_model: Claude Code / claude-opus-4-8
commit: <filled at commit time on claude/qp-roadmap-contracts>
owned_paths:
  - docs/roadmap_acceptance_matrix.md
  - docs/contracts/atomic_risk_reservation_v1.md
  - docs/atomic_risk_reservation_v1_workboard.md
acceptance_met: decision-complete v10 reservation contract + staged acceptance
  matrix + mission workboard; internally consistent with current code; docs-only
exact_checks: git diff --check clean; no runtime code edited; safety flags unchanged
known_limits: real KIS TR semantics (VTTC0084R, buying-power fields) unverified —
  manual gate for Gate P only; implementation and its tests are QP-RISK-RES-V1-impl
integration_requests:
  - Codex to implement QP-RISK-RES-V1-impl to atomic_risk_reservation_v1.md
  - reserve durable H_qty read into risk gate/batch when paper store present
  - reservation aggregate identity stable before QP-EXEC-EVENTS-V1 dual-write
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-RISK-RES-V1-contract` | safety contract + acceptance authoring | Claude `claude-opus-4-8` | pending | 0 | 0 | 0 | 0 | `git diff --check` clean; docs-only | pending | pending |

- Routing decision quality: `pending` (recorded at integration).
- Capability scorecard update: append one record for the contract/acceptance class
  on completion; preference unchanged pending ≥3 comparable samples (scorecard §7).
- User-owned changes preserved: original `main` worktree untouched; only the three
  owned docs staged on `claude/qp-roadmap-contracts`.
- Remaining limitations: implementation (`QP-RISK-RES-V1-impl`) and its independent
  audit are downstream; manual KIS validation deferred to Gate P.
