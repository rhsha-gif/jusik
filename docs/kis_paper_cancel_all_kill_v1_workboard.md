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

- Reviewer/model: Claude Code 2.1.205 (requested; exact resolved model recorded at handoff)
- Review status: pending
- Required substantive counterpart role: independently author a durable cancel/kill contract and adversarial test matrix, committed in a separate worktree.

## Routing assessment

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Core implementation | Codex GPT-5.4 | 5 | 5 | 5 | 5 | 5 | 5.00 | Strong repository integration and release-verification record. |
| Contract/test audit | Claude Code | 5 | 4 | 4 | 4 | 4 | 4.25 | Independent safety-contract review and adversarial test design. |

## Work queue

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| QP-KILL-01 | Claude Code | Codex GPT-5.4 | none | sibling worktree / `claude/qp-paper-kill-v1-contract` | `docs/contracts/kis_paper_kill_contract.md` | ready | State machine, failure semantics, and executable test matrix cover ambiguous POST and reconciliation. | pending |
| QP-KILL-02 | Codex GPT-5.4 | Claude Code | QP-KILL-01 review input | this worktree / `codex/qp-paper-kill-v1-core` | KIS client, paper persistence/execution, CLI, tests, report | in_progress | Managed working orders cancel once; crash recovery never reposts; release is fail closed. | pending |
| QP-KILL-03 | Codex GPT-5.4 | Claude Code | QP-KILL-02 | this worktree | integrated tree | proposed | Required backend and smoke checks pass; no live/network automatic path exists. | pending |

## Integration requests

- QP-KILL-01 -> QP-KILL-02: contract must define which ambiguous outcomes force `RECOVERY_REQUIRED` and how release proves no unresolved work.

## Checkpoint log

- 2026-07-11 KST — Codex — mission created from clean `15eb33d`; dirty main workspace left untouched.

## Blockers and authority requests

- None. Real KIS credentials and network tests are explicitly unnecessary and forbidden for automatic verification.
