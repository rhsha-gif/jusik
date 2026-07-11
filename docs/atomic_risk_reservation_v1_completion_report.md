# Atomic Risk Reservation v1 — Development Completion Report

Status: **Gate 1 fake-only development acceptance complete** on 2026-07-11 KST.
This is not KIS paper operational readiness; Gate P/manual validation remains
pending.

## Delivered outcome

`QP-RISK-RES-V1` adds a schema-v10, KIS-paper-only durable reservation paired
1:1 with every prepared paper dispatch. The supported scope remains a single
KRW cash account, long-only, whole-share limit orders.

- Buy orders atomically reserve integer cash and incremental long gross.
- Sell orders atomically reserve integer orderable quantity by symbol.
- Reservation and prepared dispatch commit or roll back together under
  `BEGIN IMMEDIATE`.
- Dispatch and reservation capacity evidence are exactly bound, including the
  broker bases and rounded `minimum_cash_reserve_krw`; callers cannot forge a
  larger capacity envelope.
- `outcome_unknown`, accepted, and partially-filled orders retain their entire
  reservation.
- Filled, cancelled, rejected, and pre-dispatch terminal orders release the
  reservation by CAS in the same dispatch transaction.
- Prepared takeover re-fences both records together.
- The paper guardrail reads held sell reservations from the durable store.
- Schema v9 migration backfills open buy/sell work, excludes terminal work, and
  rolls back metadata/table creation on unsafe legacy evidence.

## Safety invariants retained

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- smoke broker remains `mock`
- ambiguous broker POST outcomes are never retried automatically
- no credential, account number, secret, real KIS request, or network-dependent
  unit test was added
- `DurablePaperSubmissionCoordinator` remains the only low-level KIS order POST
  authority

## Adversarial evidence

The test suite now covers:

- concurrent cash admission with two SQLite connections
- aggregate gross and sell-quantity exhaustion
- forged broker cash, buy quantity, sell quantity, minimum reserve, and gross
  limit evidence
- forced dispatch-insert rollback and forced terminal-release rollback
- competing terminal updates and exactly-once release
- restart, takeover, uncertain outcome, partial fill, kill, reconciliation, and
  capacity reopening
- realistic v9 JSON without the schema-v10 dispatch reserve field
- open buy + open sell + terminal migration, invalid-backfill rollback, and
  future-schema refusal
- fractional legacy sell audit evidence fails with the contract-level
  `PaperStateMigrationRequired` and rolls the whole migration back
- conservative fractional cash/gross rounding and fractional share rejection
- durable reservation-to-guardrail projection and secret-free persistence

An independent Codex audit agent found two P1 defects during implementation:
forged reservation bases and migrated-open idempotent reprepare reserve drift.
Both were fixed and now have regression tests. Claude Code `claude-fable-5`
then completed an independent implementation audit (`c021a50`) and reported
ACCEPT with residual P0=0/P1=0. Its reproduced QP-RES-A1 P2 was fixed in
`40071c7` and independently closed by follow-up `b280bef`. The remaining
QP-RES-A2 P2 is conservative over-blocking only; QP-RES-A3 is a diagnostic P3.

## Verification snapshot — 2026-07-11 KST

```text
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp_gate1
885 passed, 2 skipped

python -m quantpilot.jobs.run_smoke
broker=mock
live_trading_enabled=false
operator.status=blocked
operator.fallback=level5_flag_disabled

python -m quantpilot.jobs.run_kis_paper_kill engage
status=blocked
reason_code=paper_kill_disabled

git diff --check
passed
```

The accepted implementation and audit evidence were fast-forwarded into the
clean roadmap baseline through `5eb70a9` before this snapshot was taken.

## Claude Code collaboration and audit

Claude Code authored the binding acceptance matrix, reservation contract, and
mission workboard (`215a4b9`, integrated and hardened through `5dff17a`). A
first read-only implementation-audit attempt with model selector `fable`
resolved to `claude-fable-5` but returned `429 rate_limit_error` at 11:53 KST;
that historical attempt remains recorded. The later retry succeeded and created
`docs/atomic_risk_reservation_v1_claude_audit.md` (`c021a50`). A bounded
follow-up reviewed only the QP-RES-A1 fix and regression test, closed the
finding, and committed the result as `b280bef`. Claude changed no runtime code
and received no broker/network authority.

## Manual-only limitations

- Real KIS paper buying-power and cancel TR semantics remain unverified by
  automatic tests.
- `VTTC0084R` and real cancel POST verification require explicit user-provided
  credentials and manual authorization.
- Live trading, market orders, flattening, margin, derivatives, multi-currency,
  Postgres, and Kafka remain out of scope and disabled.

These limitations block Gate P or later roadmap expansion; they do not undo the
completed fake-only Gate 1 development acceptance.
