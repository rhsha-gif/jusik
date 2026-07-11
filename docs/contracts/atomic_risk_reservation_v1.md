# Atomic Risk Reservation v1 Contract (schema v10)

Decision-complete contract for `QP-RISK-RES-V1`. It binds a durable, atomic
capacity reservation that closes the gap between the in-memory
`GuardrailState.reserved_sell_quantities` (`core/schemas.py:504`) and the durable
`PaperOrderDispatch` journal (`operator/position_ledger.py:460`). `MUST` and
`MUST NOT` are safety requirements.

Scope is **exactly**: KIS paper, long-only, cash-account, KRW, whole-share limit
orders. The implementer (routed to the mission lead per
`roadmap_execution_workboard.md`) implements to this document; an independent
auditor blocks merge on any P0/P1.

## 0. Explicit exclusions (out of scope, MUST NOT be implemented here)

- Live trading of any kind (`LIVE_TRADING_ENABLED` stays `false`).
- Market orders (`MARKET_ORDERS_ENABLED` stays `false`; limit-only).
- Margin, short selling, borrowing, or any derivative instrument.
- Multi-currency: KRW only. No FX conversion, no non-KRW cash legs.
- Position flattening / cancel-all (owned by the kill contract
  `docs/contracts/kis_paper_kill_contract.md`, not this reservation).
- Postgres, Kafka, or any non-SQLite store or message bus.
- Any change to broker POST authority: `DurablePaperSubmissionCoordinator`
  remains the sole POST path (`execution/paper_submission.py:107`).

## 1. Problem and current baseline

Today, buy capacity is checked only at `prepare_order` time against a single
broker buying-power probe (`execution/paper_submission.py:295-318`):

```
raw_no_receivable_cash = min(orderable_cash, no_receivable_buy_amount)
broker_cash            = max(0.0, raw_no_receivable_cash - minimum_cash_reserve)
broker_quantity        = no_receivable_buy_quantity
reject if quantity      > broker_quantity + 1e-6
reject if quantity*price > broker_cash    + 0.01
```

Between that probe and the fill, two concurrent buys can each individually pass
while jointly overspending cash. The only current defenses are coarse: the
in-memory `unresolved_paper_buy_order` guardrail (`risk/gatekeeper.py:281-283`)
and the `claim_dispatch_attempt` rule that blocks a new external attempt while any
`dispatch_claimed`/`outcome_unknown` dispatch exists
(`db/sqlite_repositories.py:1493-1523`). Sells are reserved only in memory
(`risk/gatekeeper.py:139-146`, `risk/batch.py:303-313`), lost on restart.

This contract makes the reservation **durable, per-order, and atomic** so that
capacity accounting survives a crash and admits concurrent independent orders up
to — never beyond — evidenced capacity.

## 2. Schema v10 constants

Mirror the existing versioning block (`db/sqlite_repositories.py:58-60`):

```
PAPER_STATE_SCHEMA_VERSION            = 10
PAPER_STATE_PREVIOUS_SCHEMA_VERSION   = 9
PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS = frozenset({6, 7, 8, 9})
```

## 3. Reservation model

New Pydantic model `PaperRiskReservation` in
`operator/position_ledger.py`, following the field/validator conventions of
`PaperOrderDispatch`. Secret-free: no credentials, no account numbers, only the
opaque `sha256:` account fingerprint.

| Field | Type / constraint | Meaning |
|---|---|---|
| `reservation_id` | `str` default `new_id("presv")` | Surrogate identity. |
| `order_plan_id` | `str` | 1:1 with the prepared `PaperOrderDispatch`. |
| `idempotency_key` | `str` | Equals the dispatch/order-plan idempotency key. |
| `kind` | `Literal["cash_buy","sell_quantity"]` | Reservation dimension. |
| `symbol` | `str` `^[A-Z0-9]{6}$` | KRX six-digit symbol. |
| `side` | `Literal["buy","sell"]` | Must agree with `kind`. |
| `reserved_cash` | `float \| None` `ge=0` | KRW held; set iff `kind=cash_buy`. |
| `reserved_quantity` | `float \| None` `gt=0` | Whole shares held; set iff `kind=sell_quantity`. |
| `broker_orderable_cash_basis` | `float \| None` `ge=0` | The `broker_cash` evidence the buy was admitted against. |
| `broker_orderable_quantity_basis` | `float \| None` `ge=0` | The `no_receivable_buy_quantity` basis for buys. |
| `snapshot_orderable_quantity_basis` | `float \| None` `ge=0` | The snapshot orderable qty basis for sells. |
| `store_id` | `str` | Provenance store. |
| `session_id` | `str` | Owning session (fencing). |
| `fencing_token` | `int` `ge=1` | Session fence. |
| `account_scope_fingerprint` | `str` `^sha256:[0-9a-f]{64}$` | Opaque account bind. |
| `data_mode` | `Literal["paper_trading"]` | Fixed. |
| `broker_environment` | `Literal["kis_paper"]` | Fixed. |
| `status` | `PaperReservationStatus` (see §5) | Lifecycle. |
| `release_reason` | `str \| None` `^[a-z0-9_]{1,64}$` | Why released. |
| `created_at` / `updated_at` | aware `datetime` | `updated_at ≥ created_at`. |
| `released_at` | `datetime \| None` | Set iff terminal (§5). |
| `revision` | `int` `ge=0` | CAS revision (starts 0). |

Model invariants (`@model_validator(mode="after")`), enforced exactly like
`PaperOrderDispatch` (`operator/position_ledger.py:636`):

- `kind=cash_buy` ⇒ `side=buy`, `reserved_cash` set (`> 0`),
  `reserved_quantity is None`, `broker_orderable_cash_basis` and
  `broker_orderable_quantity_basis` set, `snapshot_orderable_quantity_basis is
  None`, and `reserved_cash ≤ broker_orderable_cash_basis + 0.01`.
- `kind=sell_quantity` ⇒ `side=sell`, `reserved_quantity` set and a positive
  whole number, `reserved_cash is None`, `snapshot_orderable_quantity_basis` set,
  buy bases `None`, and `reserved_quantity ≤ snapshot_orderable_quantity_basis +
  1e-6`.
- Terminal `status` ⇒ `released_at` set and `≥ updated_at`-consistent and
  `release_reason` set; non-terminal ⇒ both `None`.
- All timestamps aware (`_require_aware_timestamp`).

## 4. Availability arithmetic (exact)

Tolerances match the codebase: cash `abs_tol = 0.01`, quantity `abs_tol = 1e-6`
(`execution/paper_submission.py:315-318`, `risk/gatekeeper.py:146`).

Let `H_cash` = Σ `reserved_cash` over reservations for the store with
`status="held"` and `kind="cash_buy"`; let `H_qty(sym)` = Σ `reserved_quantity`
over `status="held"`, `kind="sell_quantity"`, `symbol=sym`.

**Buy admission** (computed inside the reservation transaction, §5):

```
broker_cash = max(0.0, min(orderable_cash, no_receivable_buy_amount)
                        - minimum_cash_reserve)          # unchanged basis
request_cash = quantity * limit_price                    # whole shares * whole KRW
ADMIT buy iff  request_cash + H_cash <= broker_cash + 0.01
          AND  quantity <= no_receivable_buy_quantity + 1e-6
```

The second clause reuses the existing per-order broker-quantity check; the first
adds the durable cross-order cash sum `H_cash`. Both `quantity` and `limit_price`
are positive whole numbers (`execution/paper_submission.py:280-284`,
`_whole_positive_number`).

**Sell admission:**

```
orderable = snapshot orderable quantity for symbol      # existing basis
ADMIT sell iff  quantity + H_qty(symbol) <= orderable + 1e-6
```

This is the durable equivalent of the batch gate's aggregate check
(`risk/batch.py:309-313`) and the gatekeeper's `available = max(0, orderable −
reserved)` (`risk/gatekeeper.py:144-146`). On admission a `sell_quantity`
reservation for `quantity` is created; the risk gate MUST read `H_qty` from these
durable reservations rather than an in-memory dict when a paper store is present.

Any failed admission MUST fail closed (raise, no reservation, no dispatch) and
MUST NOT partially reserve.

## 5. Atomic reservation + prepared dispatch (one transaction)

The reservation and its prepared dispatch MUST be created in **one**
`BEGIN IMMEDIATE` transaction so no crash can leave one without the other. This
extends the existing `insert_paper_order_dispatch` path
(`db/sqlite_repositories.py:1372-1449`, already `BEGIN IMMEDIATE` at line 1388)
into a single store method, e.g. `reserve_and_insert_paper_order_dispatch(...)`:

Inside the one transaction, in order:
1. Load and require the exact active session (existing
   `_require_exact_active_session`, `db/sqlite_repositories.py:1395`).
2. Verify dispatch↔session provenance and fencing (existing lines 1399-1407).
3. Recompute `H_cash` / `H_qty(symbol)` from `status="held"` reservation rows
   (`SELECT ... WHERE store_id=? AND status='held'`).
4. Evaluate §4 admission. If it fails, raise `PaperRiskReservationRejected`
   (subclass of the existing `PaperStateConflictError` family) — the transaction
   rolls back, nothing is written.
5. Insert the `PaperRiskReservation` row (`status="held"`, `revision=0`).
6. Insert the `PaperOrderDispatch` row (`status="prepared"`, `revision=0`) exactly
   as today.
7. Commit. The `contextmanager` `_transaction` (`db/sqlite_repositories.py:574`)
   already rolls back on any exception.

Idempotency: if a reservation/dispatch already exists for the `order_plan_id` or
`idempotency_key`, return the existing pair unchanged when identical, else raise
`PaperStateConflictError` — identical to the current dispatch idempotency handling
(`db/sqlite_repositories.py:1408-1423`) and to `prepare_order`'s early return
(`execution/paper_submission.py:250-255`).

Reservation lifecycle state machine (`PaperReservationStatus`), mirroring the
transition-table discipline of `PAPER_DISPATCH_TRANSITIONS`
(`db/sqlite_repositories.py:62-86`):

```
held -> released_filled          # dispatch reached filled
held -> released_cancelled       # dispatch/cancel reconciled cancelled
held -> released_rejected        # dispatch definitively rejected
held -> released_expired         # prepared dispatch expired/failed pre-dispatch
released_* -> released_*         # idempotent terminal replay only (no cross-terminal)
```

No transition leaves a terminal state except an idempotent replay of the same
terminal state. There is no `held -> held` capacity mutation: a reservation is
immutable in amount once created; capacity changes happen only by creating or
terminalizing whole reservations.

Every reservation transition MUST use `revision`-based CAS in a `BEGIN IMMEDIATE`
transaction (`... WHERE reservation_id=? AND status=? AND revision=?`, then assert
`rowcount==1`), exactly as `update_paper_order_dispatch`
(`db/sqlite_repositories.py:1691-1712`, the `revision != existing.revision + 1`
guard) and `_write_paper_cancel_request` (`:2310`, `:2329-2330`).

## 6. Conservative double-count behavior (the core safety rule)

A reservation MUST be released **only** on definitive terminal dispatch evidence.
While an outcome is uncertain, the reservation stays `held` and continues to
subtract capacity, so the account can never be over-allocated even if a crash hid
whether the broker committed cash.

| Dispatch/cancel evidence | Reservation action |
|---|---|
| `dispatch_claimed` (claimed, POST not confirmed) | keep `held` |
| `outcome_unknown` (crash/timeout/ambiguous) | keep `held` — MUST NOT release |
| `accepted` (broker acknowledged, no fill yet) | keep `held` |
| `partially_filled` | keep `held` for the whole reservation (no partial release) |
| `filled` | `held -> released_filled` |
| `rejected` (definitive business rejection, reconciled terminal) | `held -> released_rejected` |
| `cancelled` / cancel `reconciled_cancelled` | `held -> released_cancelled` |
| `expired_pre_dispatch` / `failed_pre_dispatch` (never POSTed) | `held -> released_expired` |

Rationale, grounded in the kill contract: an `outcome_unknown` dispatch may have
committed cash at the broker even if the local write failed, so releasing its
reservation could let a second order double-spend. This is the reservation analog
of "recovery is query-only … even when the process may have crashed before
sending bytes" (`kis_paper_kill_contract.md`, "Crash and replay contract").
A reservation for an `outcome_unknown` dispatch is released **only** once
reconciliation drives that dispatch to a definitive terminal state
(`filled`/`cancelled`/`rejected`) via daily-order evidence.

No-partial-release rule: a `partially_filled` dispatch keeps its **entire**
reservation `held` until it reaches a terminal state, because the residual is
still working and could still consume the remaining reserved cash/quantity. This
matches "A row with positive remaining quantity is unresolved regardless of cancel
acknowledgment" (`kis_paper_kill_contract.md`).

Release is performed in the **same** transaction that writes the terminalizing
dispatch update, so capacity is freed atomically with the terminal evidence and a
crash cannot free capacity without a durable terminal. Concretely, the release CAS
is added to the existing terminalizing methods:
`_terminal_pre_dispatch`/`_definitive_rejection`
(`execution/paper_submission.py:583-643`) and the reconciliation applier
(`execution/paper_reconciliation_apply.py`) that moves a dispatch to
`filled`/`cancelled`.

## 7. Idempotency, provenance, revision, fencing

- **Idempotency:** reservation is keyed 1:1 to `order_plan_id`; `idempotency_key`
  carries a `UNIQUE` constraint. Re-preparing the same key returns the existing
  reservation+dispatch pair (§5), never a second reservation.
- **Provenance:** every reservation carries `data_mode=paper_trading`,
  `broker_environment=kis_paper`, and the opaque account fingerprint; a
  `_validate_reservation_provenance` MUST reject any mismatch against the store
  provenance, exactly as `_validate_dispatch_provenance`
  (`db/sqlite_repositories.py:1379`, `:1473`).
- **Revision:** `revision` increments by exactly one per write; every update is a
  CAS on `(reservation_id, status, revision)`; `rowcount != 1` ⇒
  `PaperStateConflictError` (§5).
- **Fencing:** reservation stores `session_id` + `fencing_token`. A stale-fence
  session MUST NOT mutate a reservation; a session takeover of a prepared dispatch
  (`takeover_prepared_paper_order_dispatch`,
  `db/sqlite_repositories.py:1557-1624`) MUST, in the same transaction, re-fence
  the paired reservation to the new session/token, keeping the pair consistent.

## 8. Fill / cancel / reject / pre-dispatch / outcome-unknown terminal behavior

- **Fill:** on a monotonic fill delta that brings `cumulative_filled_quantity` to
  `quantity` (`operator/position_ledger.py:869-878`), the reservation moves to
  `released_filled` in the same transaction. Reserved cash/quantity was
  "spent" by the real fill, so releasing it prevents double-subtracting.
- **Cancel:** a cancel confirmed terminal by daily-order evidence drives the
  dispatch to `cancelled` and the reservation to `released_cancelled`. A cancel
  **acknowledgment with positive remaining** is NOT terminal and keeps the
  reservation `held` (`kis_paper_kill_contract.md`,
  `test_cancel_ack_with_remaining_quantity_is_not_terminal`).
- **Reject:** a definitive business rejection (`_definitive_rejection`,
  `execution/paper_submission.py:583`) releases to `released_rejected`. A rejection
  that does not prove the original order terminal MUST NOT release (it becomes/
  remains query-only until reconciled).
- **Pre-dispatch terminal:** `expired_pre_dispatch` / `failed_pre_dispatch` never
  crossed the broker boundary (`execution/paper_submission.py:622-643`), so the
  reservation releases to `released_expired` with no double-count risk. This
  includes the kill path `terminalize_prepared_dispatches_for_kill`
  (`execution/paper_submission.py:203-233`).
- **Outcome unknown:** keep `held` (§6). Reconciliation is the only path that can
  later release it, and only via a definitive terminal.

## 9. Migration and backfill (v9 → v10)

Supported migration only (no destructive rewrite), added to the existing
migration branch (`db/sqlite_repositories.py:511-536`):

1. Add `PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS = {6,7,8,9}` and bump
   `PAPER_STATE_SCHEMA_VERSION = 10`.
2. `CREATE TABLE IF NOT EXISTS paper_risk_reservations (...) WITHOUT ROWID` with
   `reservation_id` PK, `UNIQUE(order_plan_id)`, `UNIQUE(idempotency_key)`,
   FKs to `paper_execution_sessions(session_id, store_id, fencing_token)` and
   `state_store_metadata(store_id)`, plus an index
   `ix_paper_reservation_status ON (store_id, status, symbol)` — modeled on
   `paper_order_dispatches` (`db/sqlite_repositories.py:412-432`).
3. **Backfill (idempotent, in the migration transaction):** for every existing
   dispatch in a **non-terminal** status
   (`prepared`/`dispatch_claimed`/`outcome_unknown`/`accepted`/`partially_filled`),
   synthesize exactly one `held` reservation from that dispatch's own durable
   evidence:
   - buy dispatch ⇒ `kind=cash_buy`,
     `reserved_cash = quantity*limit_price`,
     `broker_orderable_cash_basis = broker_orderable_cash`,
     `broker_orderable_quantity_basis = broker_orderable_buy_quantity`
     (`operator/position_ledger.py:496-505`).
   - sell dispatch ⇒ `kind=sell_quantity`,
     `reserved_quantity = quantity`,
     `snapshot_orderable_quantity_basis = snapshot_symbol_orderable_quantity`
     (`operator/position_ledger.py:493`).
   Terminal dispatches (`filled`/`rejected`/`cancelled`/`expired_pre_dispatch`/
   `failed_pre_dispatch`) get **no** reservation row.
4. The migration MUST preserve all existing v9 rows unchanged (dispatch, fill,
   provenance, lease/fence) exactly as the v8→v9 test requires
   (`kis_paper_kill_contract.md`, `test_schema_v8_to_v9_preserves_state`); the
   only additions are the empty-then-backfilled reservation table and the bumped
   `schema_version`/`PRAGMA user_version`.
5. A database created by schema > 10 MUST still raise
   `PaperStateMigrationRequired` (`db/sqlite_repositories.py:213-216`).

Backfill correctness note: because it is derived from each dispatch's own admitted
evidence, the reconstructed `H_cash`/`H_qty` never exceeds the capacity those
dispatches were originally admitted against, so post-migration admission stays
conservative.

## 10. Adversarial executable test matrix

All automatic cases use a deterministic fake KIS client, fixed clocks, and an
in-memory or temp SQLite store; no network, no secrets. Each test also asserts
`LIVE_TRADING_ENABLED=false` and, where a POST could occur, the fake transport's
POST count. Add under `quantpilot/tests/unit/` alongside
`test_paper_submission_coordinator.py` and `test_batch_risk_gate.py`.

| Test | Setup / action | Required assertion |
|---|---|---|
| `test_reservation_and_dispatch_commit_atomically` | Prepare one buy | One `held` reservation and one `prepared` dispatch; both at `revision=0`. |
| `test_reservation_rollback_leaves_no_dispatch` | Force admission failure at §5 step 4 | Neither reservation nor dispatch persisted. |
| `test_concurrent_buys_cannot_exceed_broker_cash` | Two buys whose sum > `broker_cash` | First admits; second fails closed; `H_cash` never exceeds basis. |
| `test_concurrent_sells_cannot_exceed_orderable` | Two sells summing over orderable qty | Second fails closed; `H_qty(symbol)` bounded by orderable. |
| `test_outcome_unknown_keeps_reservation_held` | Buy claimed then `outcome_unknown` | Reservation stays `held`; a second same-size buy is refused. |
| `test_reconciled_fill_releases_reservation` | `outcome_unknown` → daily-order proves filled | Reservation `released_filled`; capacity freed once. |
| `test_partial_fill_keeps_full_reservation_held` | Partial fill, residual working | Whole reservation stays `held` until terminal. |
| `test_reconciled_cancel_releases_reservation` | Cancel confirmed terminal | Reservation `released_cancelled`; freed once. |
| `test_rejected_releases_reservation` | Definitive business rejection | Reservation `released_rejected`; no double free on replay. |
| `test_expired_pre_dispatch_releases_reservation` | Prepared dispatch expires (`submission_evidence_expires_at`) | Reservation `released_expired`; zero POSTs. |
| `test_kill_terminalizes_prepared_releases_reservation` | Kill fence during `prepared` | Dispatch `expired_pre_dispatch`, reservation `released_expired`, zero POSTs. |
| `test_crash_between_reservation_and_claim_is_recoverable` | Kill process after §5 commit, before claim | Restart sees paired `held`+`prepared`; no capacity leak, no double reservation. |
| `test_takeover_refences_reservation` | New session takes over a prepared dispatch | Reservation fence advances with the dispatch in one transaction. |
| `test_reservation_revision_cas_rejects_stale_write` | Concurrent update with stale `revision` | `PaperStateConflictError`; single write wins. |
| `test_idempotent_reprepare_returns_same_reservation` | Re-prepare same idempotency key | Same reservation/dispatch pair; no second row. |
| `test_migration_v9_to_v10_backfills_open_dispatches` | Open a representative v9 DB with open + terminal dispatches | Open dispatches get `held` reservations from their own evidence; terminal ones get none; all v9 rows preserved. |
| `test_migration_v9_to_v10_preserves_state` | Representative v9 DB | Dispatch, fill, provenance, lease/fence preserved; reservation table valid. |

Full acceptance run: `python -m pytest quantpilot/tests` then
`python -m quantpilot.jobs.run_smoke` (both must stay green;
`broker=mock`, `live_trading_enabled=false`). Real KIS integration remains skipped
and manual.

## 11. Non-goals restated

This contract adds durable capacity accounting only. It does not change order
routing, does not add a second POST path, does not touch live/market flags, and
does not perform reconciliation itself — it hooks the existing reconciliation and
terminalization paths to release capacity atomically with their durable terminals.
