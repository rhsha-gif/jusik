# KIS Paper Cancel-All Kill v1 Workboard

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-PAPER-KILL-V1` |
| Received by / lead | Codex GPT-5.4 |
| Goal | A durable, one-attempt KIS paper kill command cancels QuantPilot-managed working orders and blocks all new paper submission until verified release. |
| In scope | Paper-only cancel inquiry/POST, durable kill and cancel state, crash-safe reconciliation, CLI engage/release, tests and completion report. |
| Out of scope | Live trading, account-wide/manual-order cancellation, position flattening, frontend/API control, Postgres/event sourcing, atomic cash risk reservations. |
| Safety constraints | Live and market orders remain disabled; paper origin only; no secrets persisted or printed; no ambiguous cancel retry; fake-client automatic tests only. |
| Completion criteria | Targeted tests, full `python -m pytest quantpilot/tests`, and `python -m quantpilot.jobs.run_smoke` pass with live=false and mock smoke broker. |

## Counterpart plan review

- Reviewer/model: Claude Code 2.1.205 attempted; Codex GPT-5.4 same-family fallback delivered contract and independent audit
- Review status: complete — initial audit P1 findings fixed; scoped re-audit found no remaining P0/P1
- Required substantive counterpart role: independently author a durable cancel/kill contract and adversarial test matrix, committed in a separate worktree.

## Routing assessment

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Core implementation | Codex GPT-5.4 | 5 | 5 | 5 | 5 | 5 | 5.00 | Strong repository integration and release-verification record. |
| Contract/test audit | Claude Code | 5 | 4 | 4 | 4 | 4 | 4.25 | Independent safety-contract review and adversarial test design. |

## Work queue

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| QP-KILL-01 | Codex GPT-5.4 fallback | Codex GPT-5.4 lead | none | sibling worktree / `codex/qp-paper-kill-v1-contract` | `docs/contracts/kis_paper_kill_contract.md` | done | State machine, failure semantics, and executable test matrix cover ambiguous POST and reconciliation. | `745b166`, integrated as `1266a0b` |
| QP-KILL-02 | Codex GPT-5.4 | independent Codex GPT-5.4 audit | QP-KILL-01 | this worktree / `codex/qp-paper-kill-v1-core` | KIS client, paper persistence/execution, CLI, tests, report | done | Managed working orders cancel once; crash recovery never reposts; release is fail closed. | `d20acaa`, `65d7ff7`, `b82e2cd` |
| QP-KILL-03 | Codex GPT-5.4 | independent Codex GPT-5.4 audit | QP-KILL-02 | this worktree | integrated tree | done | Required backend and smoke checks pass; no live/network automatic path exists. | `819 passed, 2 skipped`; smoke mock/live=false |

## Integration requests

- QP-KILL-01 -> QP-KILL-02: contract must define which ambiguous outcomes force `RECOVERY_REQUIRED` and how release proves no unresolved work.

## Checkpoint log

- 2026-07-11 KST — Codex — mission created from clean `15eb33d`; dirty main workspace left untouched.
- 2026-07-11 KST — Claude Code 2.1.205 — contract task blocked before output by session limit; same-family fallback authorized and recorded.
- 2026-07-11 KST — Codex fallback — binding contract committed as `745b166`.
- 2026-07-11 KST — independent audit — no P0; three P1 findings fixed, re-audit confirmed no remaining P0/P1; final P2 transition/test fixed.
- 2026-07-11 KST — Codex lead — full backend `819 passed, 2 skipped`; smoke broker mock/live=false; kill CLI disabled by default.

## Blockers and authority requests

- Real KIS credentials/network tests remain skipped/manual. Paper cancelable inquiry TR `VTTC0084R` requires manual broker confirmation before operational use.

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---:|
| QP-KILL-01 | safety contract | Codex GPT-5.4 fallback | yes | 0 | 0 | 0 | 0 | diff checks | 5 |
| QP-KILL-02 | trading persistence/orchestration | Codex GPT-5.4 | no | 0 | 3 | 2 | 2 | 819 tests + smoke | 2 |
| QP-KILL-03 | independent audit | Codex GPT-5.4 | yes | 0 | 0 | 0 | 0 | 2 scoped audits | 5 |

- Routing decision quality: fallback was necessary; cross-vendor independence was unavailable.
- User-owned changes preserved: dirty main workspace remains untouched; all implementation occurred in the sibling worktree.
- Remaining limitation: real KIS paper cancellation semantics are not automatically validated.
