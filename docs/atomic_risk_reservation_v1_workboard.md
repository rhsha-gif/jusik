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
| Goal | Durable, atomic KIS-paper cash + sell-quantity + incremental long-gross reservation (schema v10) that admits concurrent long-only KRW whole-share limit orders up to — never beyond — evidenced capacity, and survives crash conservatively. |
| In scope | Integer-KRW/whole-share `PaperRiskReservation` model + table, one-transaction reserve+prepare, conservative release on definitive terminals, idempotency/provenance/revision/fencing, v9→v10 migration/backfill, concurrency/crash/migration tests. |
| Out of scope | Live trading, market orders, margin/short/derivatives, multi-currency, position flatten/cancel-all, Postgres/Kafka, any second broker POST path. |
| Safety constraints | `LIVE_TRADING_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock`; fake-client automatic tests only; no ambiguous-outcome capacity release; `DurablePaperSubmissionCoordinator` stays the sole POST authority. |
| Completion criteria | `docs/contracts/atomic_risk_reservation_v1.md` §10 tests present and green within `python -m pytest quantpilot/tests`; `python -m quantpilot.jobs.run_smoke` prints `broker=mock`/`live_trading_enabled=false`/operator blocked; migration preserves v9 state; independent audit reports zero P0/P1; `git diff --check` clean. |

## Counterpart plan review

- Reviewer/model: Claude Code using the Opus alias (exact resolved model not
  exposed) drafted the binding contract and acceptance matrix; `claude-fable-5`
  finalized the committed artifact and later acted as an instance distinct from
  the implementer for the independent audit (the implementer did not self-approve
  this safety-critical change — protocol §6).
- Review status: `accepted`; the contract is bound and Claude Code
  `claude-fable-5` completed the independent implementation audit plus the
  QP-RES-A1 follow-up with residual P0=0/P1=0.
- Decomposition findings: reservation aggregate identity must be stable before
  `QP-EXEC-EVENTS-V1` dual-write (`roadmap_execution_workboard.md` integration
  requests); the reserve+prepare atomic transaction and the migration/backfill are
  the two highest-risk slices and each need dedicated tests
  (`atomic_risk_reservation_v1.md` §5, §9, §10).
- Required substantive counterpart role: Claude Code delivered (a) the binding
  decision-complete contract + acceptance matrix under `QP-RM-00A`, and (b) the
  independent adversarial implementation audit `c021a50` plus A1 follow-up
  `b280bef` before Gate 1 acceptance.

## Routing assessment

점수는 1~5이며 총점은 `domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`이다.
`track=3` = 비교 가능한 실적 없음(중립). Bounded seed 규칙은 stateful integration에는 적용하지 않는다
(격리·가역 조건 미충족, scorecard §3).

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `QP-RISK-RES-V1` impl | Codex GPT-5 lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Stateful SQLite/broker integration is `codex-gpt5x`'s best-sampled class (scorecard §6, n=5, ratings 4–5). |
| `QP-RISK-RES-V1` impl | Claude Code Opus alias | 4 | 4 | 3 | 3 | 3 | 3.55 | Strong on contracts/audit; less-sampled on this store-integration class; owns the binding design instead. |
| `QP-RISK-RES-V1-contract` | Claude Code Opus alias + `claude-fable-5` finalizer | 5 | 5 | 4 | 5 | 5 | 4.85 | Long-form safety contract + acceptance authoring is the Claude priority class; delivered and Codex-reviewed. |
| `QP-RISK-RES-V1-audit` | Claude Code (exact model recorded at audit) | 5 | 5 | 4 | 4 | 5 | 4.75 | Independent adversarial audit is a Claude priority class and must be a non-implementer (protocol §6). |

## Work queue

상태: `proposed`, `ready`, `in_progress`, `review`, `integrated`, `done`, `blocked`.

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-RISK-RES-V1-contract` | Claude Code Opus alias + `claude-fable-5` finalizer | GPT-5 Codex lead | `QP-RM-00`, `QP-RM-00A` | `주식트레이더-claude-roadmap-contracts` / `claude/qp-roadmap-contracts` | `docs/roadmap_acceptance_matrix.md`, `docs/contracts/atomic_risk_reservation_v1.md`, `docs/atomic_risk_reservation_v1_workboard.md` | integrated | Decision-complete v10 model/arithmetic/transaction/migration/tests; Codex review added integer gross reservation and corrected atomic store ownership. | Claude `215a4b9`, integrated `36d9a8a`; Codex hardening `5dff17a` |
| `QP-RISK-RES-V1-impl` | GPT-5 Codex lead | Claude Code `claude-fable-5` | `QP-RISK-RES-V1-contract` | `주식트레이더-risk-reservation-v1` / `codex/qp-risk-reservation-v1-core` | `operator/position_ledger.py` (reservation model), `db/sqlite_repositories.py` (table/migration/CAS methods), `execution/paper_submission.py` (reserve boundary), `harness_service.py` (durable guardrail projection), reservation tests | done | Contract §10 tests green; schema v10 migration preserves v9; baseline integration and independent audit accepted. | implementation/audit head `5eb70a9`; `885 passed, 2 skipped`; smoke mock/live=false/blocked; kill CLI `paper_kill_disabled` |
| `QP-RISK-RES-V1-audit` | Claude Code `claude-fable-5` | GPT-5 Codex lead | `QP-RISK-RES-V1-impl` | `주식트레이더-claude-risk-reservation-audit` / `claude/qp-risk-reservation-v1-audit` | audit report and audit fields only (no runtime code) | done | Adversarial audit accepted with zero residual P0/P1; QP-RES-A1 follow-up closed the reproduced migration error-contract defect. | audit `c021a50`; follow-up `b280bef`; QP-RES-A1 CLOSED; residual P0=0/P1=0/P2=1/P3=1 |

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

- `2026-07-11 KST` — Claude Code Opus alias (exact resolved ID not exposed),
  finalized by `claude-fable-5` — authored `QP-RM-00A` acceptance
  matrix, the v10 reservation contract, and this workboard from repository evidence
  (`operator/position_ledger.py`, `db/sqlite_repositories.py`,
  `execution/paper_submission.py`, `risk/gatekeeper.py`, `risk/batch.py`,
  `docs/contracts/kis_paper_kill_contract.md`); docs-only, no runtime change;
  `git diff --check` clean.
- `2026-07-11 KST` — GPT-5 Codex lead — implemented schema v10 reservation,
  exact dispatch/reservation capacity-evidence binding, one-transaction
  reserve+prepare, terminal release CAS, takeover re-fencing, durable sell
  guardrail projection, and v9 backfill/rollback handling.
- `2026-07-11 KST` — independent Codex audit agent — reproduced a forged
  reservation-basis P1 and a migrated-open reprepare P1; both were fixed with
  adversarial regression tests. Final internal audit: P0=0, P1=0.
- `2026-07-11 KST` — verification — `884 passed, 2 skipped`; smoke remained
  `broker=mock`, `live_trading_enabled=false`, Level 5 blocked; `git diff --check`
  clean; no network or real KIS POST.
- `2026-07-11 11:53 KST` — Claude Code final implementation audit attempt —
  resolved model `claude-fable-5`; request failed with account
  `429 rate_limit_error`. Contract/acceptance authorship remains the substantive
  Claude deliverable; implementation integration waits for audit retry.
- `2026-07-11 KST` — Claude Code `claude-fable-5` — audit retry succeeded;
  independent read-only final audit of `5dff17a..58715bd` completed and recorded
  in `docs/atomic_risk_reservation_v1_claude_audit.md`. Verified one-transaction
  reserve+prepare with fault-injected rollback both ways, exact integer
  admission, two-connection concurrency, capacity-evidence binding, conservative
  `outcome_unknown`/partial hold, same-transaction terminal release, takeover
  re-fencing, v9→v10 backfill/rollback, kill blocking, and durable sell
  guardrail projection. Residual P0=0, P1=0; two P2 (fail-closed sell-backfill
  error type; conservative non-policy-scoped guardrail projection) and one P3
  reported as non-blocking follow-ups. Recommendation: ACCEPT. Evidence:
  `884 passed, 2 skipped`; smoke `broker=mock`/`live_trading_enabled=false`/
  operator blocked; kill CLI `paper_kill_disabled`; `git diff --check` clean;
  no network or authority change.
- `2026-07-11 KST` — Claude Code `claude-fable-5` (CLI alias `fable`) —
  independent follow-up review of the QP-RES-A1 fix, delta `c021a50..a892210`
  at HEAD `a892210` on `claude/qp-risk-reservation-v1-audit` (clean tree
  verified first). Confirmed the fractional-legacy-sell backfill failure now
  raises `PaperStateMigrationRequired` (catch scoped to synthesized
  `PaperRiskReservation` validation only, re-raise fail-closed, no evidence
  weakened, no invalid reservation admitted) and that the whole v9→v10
  migration transaction still rolls back metadata, `user_version`, and the
  reservation table. Focused check:
  `python -m pytest quantpilot/tests/unit/test_paper_dispatch_persistence.py`
  → `41 passed`; `git diff --check` clean. **QP-RES-A1 CLOSED**; QP-RES-A2/A3
  remain non-blocking residuals; residual P0=0, P1=0 → final **ACCEPT** for
  Gate 1 development readiness at `a892210` (audit doc §9). This checkpoint
  does not claim mainline/baseline integration, Gate P/manual KIS validation,
  or full roadmap completion. Docs-only edit; document lease released (free).
- `2026-07-11 KST` — GPT-5 Codex lead — fast-forwarded the accepted
  implementation and Claude audit evidence into the clean roadmap baseline at
  `5eb70a9`, then re-ran the authoritative integration checks: `885 passed,
  2 skipped`; smoke `broker=mock`, `live_trading_enabled=false`,
  `operator.status=blocked`, `operator.fallback=level5_flag_disabled`; kill CLI
  `paper_kill_disabled`; `git diff --check` clean. Gate 1 development acceptance
  is complete; Gate P/manual KIS validation remains separate and pending.

## Handoff record

```text
task_id: QP-RISK-RES-V1-contract
agent_and_model: Claude Code / Opus alias draft (exact ID unexposed), claude-fable-5 finalizer
commit: 215a4b9 on claude/qp-roadmap-contracts; cherry-picked as 36d9a8a
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

```text
task_id: QP-RISK-RES-V1-impl
agent_and_model: GPT-5 Codex lead; independent Claude Code claude-fable-5 audit
commit: implementation/audit integrated through 5eb70a9 on
  codex/qp-roadmap-baseline-v9
owned_paths:
  - quantpilot/packages/core/operator/position_ledger.py
  - quantpilot/packages/core/execution/paper_submission.py
  - quantpilot/packages/core/harness_service.py
  - quantpilot/packages/db/sqlite_repositories.py
  - quantpilot/tests/unit/test_paper_*.py
  - docs/contracts/atomic_risk_reservation_v1.md
  - docs/atomic_risk_reservation_v1_workboard.md
  - docs/atomic_risk_reservation_v1_completion_report.md
acceptance_met: yes for Gate 1 fake-only development; baseline integration and
  independent Claude audit completed with residual P0=0/P1=0
exact_checks: 885 passed, 2 skipped; smoke broker=mock/live=false/operator
  blocked/fallback=level5_flag_disabled; kill CLI paper_kill_disabled;
  git diff --check clean
known_limits: real KIS calls remain manual Gate P evidence; QP-RES-A2 is a
  conservative cross-policy over-blocking P2 and QP-RES-A3 is a diagnostic P3
integration_requests:
  - preserve reservation aggregate identity for QP-EXEC-EVENTS-V1
```

```text
task_id: QP-RISK-RES-V1-audit
agent_and_model: Claude Code / claude-fable-5 (CLI alias fable)
commit: c021a50 audit plus b280bef follow-up on
  claude/qp-risk-reservation-v1-audit
owned_paths:
  - docs/atomic_risk_reservation_v1_claude_audit.md
  - docs/atomic_risk_reservation_v1_workboard.md (Claude-audit fields only)
acceptance_met: independent adversarial audit and A1 follow-up complete;
  residual P0=0, P1=0; recommendation ACCEPT for Gate 1 development readiness
exact_checks: pytest JUnit tests=886 failures=0 errors=0 skipped=2
  (884 passed, 2 skipped); smoke broker=mock, live_trading_enabled=false,
  operator.status=blocked, operator.fallback=level5_flag_disabled; kill CLI
  {"status":"blocked","reason_code":"paper_kill_disabled"}; git diff --check
  clean; QP-RES-A1 reproduced and then closed by a focused 41-test follow-up
known_limits: real KIS TR semantics (VTTC0084R, buying-power fields, session
  calendar) remain manual Gate P evidence; offline concurrency evidence uses
  two SQLite connections and fault injection, not multi-process crash timing
integration_requests:
  - retain QP-RES-A2 policy-scope hardening and QP-RES-A3 diagnostic cleanup as
    non-blocking follow-ups
  - preserve reservation aggregate identity before QP-EXEC-EVENTS-V1
  - Claude-owned scorecard evidence is recorded separately at mission close
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-RISK-RES-V1-contract` | safety contract + acceptance authoring | Claude Code Opus alias + `claude-fable-5` | no | 0 | 1 | 0 | 1 | `git diff --check` clean; docs-only | recorded in Claude runs | 2 |
| `QP-RISK-RES-V1-impl` | SQLite risk reservation + integration | GPT-5 Codex + Claude Code `claude-fable-5` audit | no | 0 | 2 | 2 | 5 | 885 tests + smoke + kill CLI + migration/concurrency/fault tests | completed 2026-07-11 KST | 4 |
| `QP-RISK-RES-V1-audit` | independent safety audit + follow-up | Claude Code `claude-fable-5` | no | 0 | 0 | 2 | 1 | full suite + focused 41-test follow-up + smoke + kill CLI | completed 2026-07-11 KST | 5 |

- Routing decision quality: correct for independent contract authorship; Codex review found and repaired one P1 scope/atomicity defect before runtime implementation.
- Capability scorecard update: append one record for the contract/acceptance class
  on completion; preference unchanged pending ≥3 comparable samples (scorecard §7).
- User-owned changes preserved: original `main` worktree untouched; only the three
  owned docs staged on `claude/qp-roadmap-contracts`.
- Remaining limitations: manual KIS validation remains deferred to Gate P;
  QP-RES-A2 and QP-RES-A3 are non-blocking residual hardening items.
