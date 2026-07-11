# Atomic Risk Reservation v1 — Completion Candidate

Status: implementation and local acceptance complete; branch integration waits
for the pending Claude Code implementation-audit retry.

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
- conservative fractional cash/gross rounding and fractional share rejection
- durable reservation-to-guardrail projection and secret-free persistence

An independent Codex audit agent found two P1 defects during implementation:
forged reservation bases and migrated-open idempotent reprepare reserve drift.
Both were fixed and now have regression tests. Its final verdict is P0=0,
P1=0.

## Verification snapshot — 2026-07-11 KST

```text
python -m pytest quantpilot/tests -p no:cacheprovider
884 passed, 2 skipped

python -m quantpilot.jobs.run_smoke
broker=mock
live_trading_enabled=false
operator.status=blocked
operator.fallback=level5_flag_disabled

git diff --check
passed
```

## Claude Code collaboration and remaining gate

Claude Code authored the binding acceptance matrix, reservation contract, and
mission workboard (`215a4b9`, integrated and hardened through `5dff17a`). A
read-only final implementation audit was invoked with model selector `fable`;
the CLI resolved it to `claude-fable-5` but the account returned
`429 rate_limit_error` at 2026-07-11 11:53 KST. No implementation finding was
returned. The implementation stays in `review` until this audit is retried or an
equivalent independent human/different-model review is recorded.

## Manual-only limitations

- Real KIS paper buying-power and cancel TR semantics remain unverified by
  automatic tests.
- `VTTC0084R` and real cancel POST verification require explicit user-provided
  credentials and manual authorization.
- Live trading, market orders, flattening, margin, derivatives, multi-currency,
  Postgres, and Kafka remain out of scope and disabled.
