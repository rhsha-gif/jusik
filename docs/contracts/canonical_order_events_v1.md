# Canonical Execution Events v1 Contract (schema v11)

Decision-complete draft for `QP-EXEC-EVENTS-V1`. This gate adds a durable,
append-only **shadow journal** around the existing KIS paper execution state.
The schema-v10 `PaperOrderDispatch`, `PaperRiskReservation`, and
`PaperCancelRequest` rows remain authoritative throughout Gate 2. Events may
describe those rows, but MUST NOT grant new broker authority, widen a state
transition, or become a second submission path.

This draft is repository-grounded and awaits the required Claude Code initial
decomposition review before runtime implementation begins. `MUST` and
`MUST NOT` are safety requirements.

## 0. Exact scope and exclusions

In scope:

- KIS paper only, with the existing whole-share KRW limit-order contract.
- Canonical event models and a pure deterministic reducer.
- SQLite schema v11 and append-only event persistence.
- Same-transaction dual-write from every existing durable paper mutation.
- Truthful v6-v10 import anchors, replay, and read-only shadow parity checks.
- Order-dispatch, risk-reservation, and cancel-request streams.

Out of scope:

- Live trading, live credentials, real KIS calls, or automatic network tests.
- Market orders, margin, short selling, derivatives, multi-currency, or flatten.
- Promoting events to source of truth; that is a later ledger/cutover gate.
- Level 3/4/5 kernel unification; that is `QP-KERNEL-V2`.
- Position/cash accounting, fees, tax, corporate actions, or NAV projection.
- Postgres, Kafka, a message bus, or a mutable event projection table.
- Replace, restate, trade correction, or trade bust behavior. These names are
  not accepted by the v1 reducer until their durable legacy semantics exist.
- A `PaperKillOperation` event stream. Gate 2 journals its managed cancel
  requests, while account-level kill lifecycle remains in the existing durable
  table and kill contract.
- `OrderSubmitted`: the current system can prove `DispatchClaimed` before a
  possible POST, but cannot prove that bytes reached KIS after a crash.
- Reconstructing true executions from KIS cumulative daily-order observations.

The following defaults remain fixed:

```text
LIVE_TRADING_ENABLED=false
GUARDED_AUTOPILOT_ENABLED=false
FULLY_AUTOMATED_OPERATOR_ENABLED=false
MARKET_ORDERS_ENABLED=false
BROKER_MODE=mock
```

## 1. Authority and aggregate boundaries

The authoritative rows and their existing CAS revisions are independent:

| Aggregate type | Aggregate ID | Authoritative model | Correlation |
|---|---|---|---|
| `order_dispatch` | `order_plan_id` | `PaperOrderDispatch` | `order_plan_id` |
| `risk_reservation` | `reservation_id` | `PaperRiskReservation` | paired `order_plan_id` |
| `cancel_request` | `cancel_id` | `PaperCancelRequest` | target `order_plan_id` |

They MUST remain separate event streams. A terminal order update and its
reservation release are correlated events committed in one SQLite transaction,
not one synthetic aggregate. This preserves the independent legacy revisions
and makes the atomic boundary explicit.

`aggregate_version` is the event stream's contiguous local sequence starting at
1. It MUST NOT be equated with a legacy row `revision`: one legacy transaction
can write multiple aggregates, and an imported row may begin at any legacy
revision while its event stream begins at version 1.

`correlation_id` is the stable `order_plan_id`. `causation_id` is store-derived
and never caller-supplied, by this closed rule:

- Import events: null.
- `RiskReserved` and `CancelPrepared`: null. No durable command journal exists
  in v1, and inventing a command ID would be untruthful provenance.
- `OrderPrepared`: the `event_id` of the `RiskReserved` event committed in the
  same transaction.
- `RiskReservationFenceRebound`: the `event_id` of the same transaction's
  `DispatchFenceRebound`.
- `RiskReservationReleased`: the `event_id` of the same transaction's terminal
  dispatch event (the correlated terminal batch).
- Every other event: the `event_id` of the same aggregate's immediately
  preceding event, i.e. the event currently holding the last
  `aggregate_version`.

`causation_id` never authorizes a broker call.

## 2. Canonical envelope

Add a secret-free `PaperExecutionEvent` model under
`quantpilot/packages/core/execution/events.py` with this envelope:

| Field | Constraint | Meaning |
|---|---|---|
| `event_id` | nonblank string | Globally unique event identity; store-generated per Section 8, deterministic for imports per Section 10, never caller-supplied. |
| `event_schema_version` | literal `1` | Payload/envelope contract version, not DB schema. |
| `aggregate_type` | one of the three types in Section 1 | Stream family. |
| `aggregate_id` | nonblank string | ID in the aggregate table above. |
| `aggregate_version` | integer `>=1` | Store-assigned contiguous replay order. |
| `event_type` | Section 4 allowlist | Semantic fact. |
| `store_id` | nonblank string | Exact `PaperStateStore` provenance. |
| `account_scope_fingerprint` | `sha256:<64 lowercase hex>` | Opaque account bind; never an account ID. |
| `data_mode` | literal `paper_trading` | Fixed. |
| `broker_environment` | literal `kis_paper` | Fixed. |
| `source` | Section 3 allowlist | Fact origin. |
| `occurred_at` | aware datetime | Best evidenced source time; derived by the closed Section 3 rule, never caller-selected. |
| `received_at` | aware datetime | Local durable-observation time; derived by the closed Section 3 rule. |
| `correlation_id` | nonblank string | `order_plan_id`. |
| `causation_id` | optional nonblank string | Command/event that caused this append. |
| `idempotency_key` | optional nonblank string | Paired request key when known. |
| `local_broker_order_id` | optional string | Existing locally generated `PaperOrderDispatch.broker_order_id`; not a KIS ID. |
| `broker_order_id` | optional string | Actual KIS `broker_order_reference`. |
| `original_client_order_id` | optional string | Reserved for a future replace contract; null in v1. |
| `venue_order_id` | optional string | Null unless KIS exposes independent venue identity. |
| `identity_keys` | list of typed keys | Zero or more newly introduced fill/observation identities; Section 5. |
| `broker_sequence` | optional integer `>=0` | Persist only when the broker actually supplies it. |
| `source_revision` | integer `>=0` | Authoritative row revision after this fact. |
| `payload` | typed, secret-free object | Section 5 after-state payload. |
| `payload_hash` | `sha256:<64 lowercase hex>` | Hash of canonical payload JSON. |

The event model MUST use an event-specific recursive secret validator derived
from the repository's audit conventions. It MUST reject credentials, access/
refresh/bearer tokens, passwords, API/app keys and secrets, and raw account
numbers. The sole `_token` structural-key exception is the typed model field
`fencing_token`; it is safe only inside a validated dispatch/reservation snapshot
and must be an integer session fence. Payloads are rejected rather than redacted
because redaction would break exact replay/parity. Identifiers are normalized
but never silently rewritten across semantic fields.

Identifier mapping is deliberately explicit:

```text
order_plan_id                       -> aggregate/correlation order identity
PaperOrderDispatch.broker_order_id -> local_broker_order_id
broker_order_reference             -> broker_order_id (actual KIS order number)
broker_fill_reference/kisagg-*     -> identity key `broker_cumulative_delta`
true broker execution reference    -> identity key `venue_execution`
```

## 3. Source, mutation origin, and time semantics

Allowed sources are:

```text
local_prepare
local_dispatch_claim
local_session_takeover
local_submission_result
broker_acceptance
broker_reconciliation
process_recovery
kill_cancel
schema_migration
```

`local_submission_result` is a review correction: pre-dispatch expiry/failure
and post-claim local guard rejections never touched the broker, and the draft's
original allowlist gave them no truthful source.

`source` is never inferred from a before/after row diff and never caller
free-form. The generic mutators cannot distinguish acceptance, reconciliation,
and guard/kill/recovery writes from state alone — `dispatch_claimed ->
rejected` is produced both by the submission coordinator
(`_definitive_rejection`) and, for an unresolved claim, by broker
reconciliation — so `update_paper_order_dispatch` and
`update_paper_cancel_request` gain one required keyword-only
`mutation_origin` argument typed as a closed literal. Each production call
site passes exactly one token; the store maps the token to `source` and
validates it against that token's allowlisted delta shape before append. Any
mismatch fails closed with no row or event write. Callers never choose event
types or envelopes; the specialized store methods carry an implicit origin.

| Mutation origin | Exact production call sites | `source` | Allowed delta |
|---|---|---|---|
| implicit: `reserve_and_insert_paper_order_dispatch` | `prepare_order` (`paper_submission.py`) | `local_prepare` | create pair, both revision 0 |
| implicit: `claim_dispatch_attempt` | submission claim path | `local_dispatch_claim` | `prepared/attempt 0 -> dispatch_claimed/attempt 1` |
| implicit: `takeover_prepared_paper_order_dispatch` | `expire_stale_prepared_dispatches`, `terminalize_prepared_dispatches_for_kill`, submission replay takeover | `local_session_takeover` | fence rebind only (dispatch + paired reservation) |
| implicit: `recover_interrupted_dispatches` | session recovery | `process_recovery` | `dispatch_claimed -> outcome_unknown` with `last_error_code="process_interrupted"` |
| `broker_post_result` | `_record_acceptance`; `_definitive_rejection` with `broker_business_rejected`; `_outcome_unknown` for POST-attempt ambiguity (`broker_response_ambiguous`, `broker_exception_after_claim`, `broker_acceptance_mismatch`, `broker_business_date_unverified`, `acceptance_persistence_failed`) | `broker_acceptance` | `dispatch_claimed -> accepted \| rejected \| outcome_unknown` |
| `local_submission_guard` | `_terminal_pre_dispatch` (expiry, kill terminalization of prepared rows, pre-claim session-closed failure); `_definitive_rejection` for post-claim local guards (`paper_kill_engaged_after_claim`, `paper_session_closed_after_claim`, `local_configuration_error`) | `local_submission_result` | `prepared -> expired_pre_dispatch \| failed_pre_dispatch`; `dispatch_claimed -> rejected` with a local guard error code |
| `broker_reconciliation` | `reconcile_dispatch` and `_blocked` (`paper_reconciliation.py`) | `broker_reconciliation` | the Section 6 reconciliation surface, including `-> blocked` writes |
| `kill_cancel_journal` | every cancel write in `paper_kill.py` (`create`/`claim`/`_persist_cancel_state`/`_synchronize_cancel_requests`) | `kill_cancel` | cancel-stream transitions only |
| implicit: migration importer | `_initialize_schema` | `schema_migration` | version-1 import anchors only |

A terminal `RiskReservationReleased` event inherits the `source` of the same
transaction's terminal dispatch event; kill terminalization of a prepared
dispatch is `local_submission_result` (no broker call occurred; the payload's
`last_error_code="paper_kill_engaged"` preserves the kill context truthfully).

Replay order is **only** `(aggregate_type, aggregate_id, aggregate_version)`.
Neither timestamp is an ordering key. Broker clocks, daily queries, and process
restarts can make `occurred_at` appear earlier than a previously received fact.

`occurred_at` and `received_at` are derived by the store from the validated
after-state; callers cannot select event times:

- `received_at` for every non-import event is `payload.after.updated_at`, the
  same transaction's durable write time.
- `occurred_at` for `OrderAccepted` is the KST datetime combined from the
  verified `after.broker_business_date` and `after.broker_order_time` when both
  are present; otherwise `after.updated_at`.
- `occurred_at` for every other non-import event — rejection, ambiguity,
  pre-dispatch expiry/failure, interrupted recovery, fence rebinds,
  reconciliation outcomes, reservation release, and every cancel mutation — is
  `after.updated_at`. KIS cumulative daily-order evidence has no execution
  timestamp in the current adapter; its first-observed times stay on the
  per-fill `fill_evidence[*].evidence_at` items with their per-fill
  `time_basis="broker_daily_aggregate_first_observed"` literal and are never
  promoted to an aggregate event value.
- Import anchors use the legacy row's `updated_at` as `occurred_at` and one
  migration clock reading captured once per migration transaction as
  `received_at`.

No code may invent a `broker_sequence`, `execution_id`, or execution timestamp.

## 4. Event type allowlist

### `order_dispatch`

```text
LegacyOrderDispatchImported
OrderPrepared
DispatchFenceRebound
DispatchClaimed
OutcomeUnknown
OrderAccepted
DispatchEvidenceObserved
OrderPartiallyFilled
OrderFilled
OrderRejected
OrderCancelled
OrderExpiredPreDispatch
OrderFailedPreDispatch
ReconciliationBlocked
DispatchReconciled
```

`DispatchEvidenceObserved` represents a legal same-status enrichment such as
accepted-to-accepted branch evidence without falsely asserting a second
acceptance or terminalization. `OrderPartiallyFilled` may repeat only when fill
evidence grows monotonically. `ReconciliationBlocked` may also carry the current
dispatch becoming `outcome_unknown`; its full after-state remains the projection
authority for the shadow stream. `DispatchReconciled` is used only when a legal
same-status update advances reconciliation from pending/blocked to reconciled;
if lifecycle status also advances, the matching lifecycle event is used instead.

Event-type selection first recognizes the Section 6 special create, claim, and
fence-rebind shapes (`OrderPrepared`, `DispatchClaimed`,
`DispatchFenceRebound`, and the paired reservation/cancel special events).
Those shapes MUST NOT fall through to generic enrichment classification. For a
generic `update_paper_order_dispatch` before/after pair, selection is then a
total deterministic function. When one authoritative revision changes
lifecycle status, fill evidence, and/or reconciliation status together (as
`reconcile_dispatch` legally does in a single revision), exactly one event type
is chosen by this precedence:

1. `after.reconciliation_status == "blocked"` -> `ReconciliationBlocked`. This
   covers `pending -> blocked`, a changed-error `blocked -> blocked` rewrite,
   and the combined `dispatch_claimed -> outcome_unknown` plus blocked write
   from `_blocked`.
2. Else, lifecycle status changed -> the after-status lifecycle event:
   `accepted -> OrderAccepted`, `outcome_unknown -> OutcomeUnknown`,
   `partially_filled -> OrderPartiallyFilled`, `filled -> OrderFilled`,
   `rejected -> OrderRejected`, `cancelled -> OrderCancelled`,
   `expired_pre_dispatch -> OrderExpiredPreDispatch`,
   `failed_pre_dispatch -> OrderFailedPreDispatch`. Newly introduced fill
   identities ride this event as child keys and a simultaneous reconciliation
   advance rides the after-state; neither spawns a second dispatch event.
3. Else, reconciliation advanced pending/blocked -> reconciled ->
   `DispatchReconciled`.
4. Else, new fill evidence was introduced -> `OrderPartiallyFilled`, which
   additionally requires `after.status == "partially_filled"`; any other
   same-status fill growth is corruption, because `_status_from_row` cannot
   produce it.
5. Else -> `DispatchEvidenceObserved` (legal same-status enrichment, such as
   accepted-to-accepted branch/time evidence).

Both the store append helper and the pure reducer recompute this function and
reject any event whose type disagrees with it.

### `risk_reservation`

```text
LegacyRiskReservationImported
RiskReserved
RiskReservationFenceRebound
RiskReservationReleased
```

`RiskReservationReleased` reflects, but never independently causes, the existing
same-transaction terminal release. It is forbidden for `dispatch_claimed`,
`outcome_unknown`, `accepted`, or `partially_filled` orders.

### `cancel_request`

```text
LegacyCancelRequestImported
CancelPrepared
CancelClaimed
CancelAccepted
CancelOutcomeUnknown
CancelRejected
CancelReconciledCancelled
CancelReconciledFilled
```

Cancel acknowledgment is not order cancellation. Only broker reconciliation may
produce `OrderCancelled`/`CancelReconciledCancelled`; a fill may win the race and
produce `OrderFilled`/`CancelReconciledFilled` without another broker POST.

Cancel event types are the closed per-status map of the after-state:
`prepared -> CancelPrepared` (creation only), `cancel_claimed -> CancelClaimed`,
`cancel_accepted -> CancelAccepted`,
`cancel_outcome_unknown -> CancelOutcomeUnknown`, `rejected -> CancelRejected`,
`reconciled_cancelled -> CancelReconciledCancelled`, and
`reconciled_filled -> CancelReconciledFilled`. A same-status cancel write that
changes other fields has **no** v1 event type and fails closed before commit.
`PAPER_CANCEL_TRANSITIONS` permits such self-loops, but every production cancel
writer (`create_paper_cancel_request`, `claim_paper_cancel_attempt`, and the
`paper_kill.py` `_persist_cancel_state`/`_synchronize_cancel_requests` paths)
either replays a state exactly — which returns before any write — or advances
the status. V1 deliberately excludes an invented cancel-enrichment event rather
than journal an unreachable mutation class; if a future writer needs it, that is
an event-schema revision, not a silent widening.

## 5. Typed payload and canonical hash

Every v1 event contains a validated full after-state snapshot for its aggregate:

```text
order_dispatch   -> { "after": <PaperOrderDispatch JSON> }
risk_reservation -> { "after": <PaperRiskReservation JSON> }
cancel_request   -> { "after": <PaperCancelRequest JSON> }
```

The draft's payload-level `time_basis` field is removed by review decision: one
dispatch snapshot may contain multiple `fill_evidence` rows with different
per-fill `time_basis` values, so no truthful aggregate value exists and none may
be guessed. Time basis exists only on each `PaperDispatchFillEvidence` item
inside `after.fill_evidence` and, through `evidence_payload_hash`, on each
identity key.

Import events add `legacy_snapshot=true`. Full after-state payloads are an
intentional v1 safety choice: they let the reducer re-run the existing model and
transition invariants and enable exact shadow parity without guessing omitted
fields. A later schema version may introduce validated deltas, but v1 readers
MUST NOT infer them.

Canonical payload bytes are UTF-8 JSON produced with sorted object keys, compact
separators, no NaN/Infinity, stable ISO-8601 aware timestamps, and the repository's
JSON-mode model encoding. `payload_hash` is SHA-256 over those exact bytes with a
`sha256:` prefix. Hash verification happens before reducer application.

`identity_keys` is a list of `PaperExecutionIdentityKey` objects:

| Field | Constraint | Meaning |
|---|---|---|
| `kind` | `venue_execution` or `broker_cumulative_delta` | Truthful identity class. |
| `external_id` | nonblank string | True execution reference or current `kisagg-*` observation reference. |
| `scope_hash` | `sha256:<64 lowercase hex>` | Canonical broker/account/order scope. |
| `evidence_payload_hash` | `sha256:<64 lowercase hex>` | SHA-256 over the canonical JSON bytes (this section's canonical-bytes rule) of the exact newly introduced `PaperDispatchFillEvidence`. |

The list is canonically sorted by `(kind, scope_hash)` and contains no duplicate
scope. It is **exactly** the set difference between `after.fill_evidence` and the
prior order projection. An import event includes one key for every imported fill;
an event that introduces no fill has an empty list. This handles the legal case
where one authoritative revision adds multiple fill-evidence rows.

- `time_basis="broker_execution"` maps to `kind=venue_execution`; its
  `external_id` is a true broker execution reference.
- `time_basis="broker_daily_aggregate_first_observed"` maps to
  `kind=broker_cumulative_delta`; its `external_id` is the current `kisagg-*`
  reference and MUST NOT be called an execution ID.
- A venue scope hash covers exactly, in canonical field order:
  `broker_environment`, `account_scope_fingerprint`, identity kind, and the true
  external execution ID.
- A cumulative scope hash covers exactly, in canonical field order:
  `broker_environment`, `account_scope_fingerprint`, identity kind,
  `after.broker_business_date` (ISO date), `after.broker_order_reference` (the
  actual KIS order number), `after.broker_order_branch_number`, and the external
  evidence ID (`kisagg-*`). Every component MUST be non-null when the key is
  constructed; `reconcile_dispatch` durably writes each of them in the same
  revision that merges cumulative fill evidence, and a missing component fails
  closed instead of falling back to any local ID.
  `broker_forwarding_order_org_number` is deliberately **excluded**: it is
  acceptance-path evidence only (`_record_acceptance`) and is legally absent on
  a reconciliation-only lifecycle (`outcome_unknown -> accepted` via daily
  orders never sets it), so requiring it would block truthful legacy state and
  making it optional would fork the hash. The scope deliberately excludes local
  `order_plan_id`/`correlation_id`, so the same broker fact cannot be attached
  to two local orders under different hashes.
- Any second distinct event that reuses a scope is a conflict, never a no-op or
  overwrite, even when its payload matches. Later full after-state snapshots do
  not repeat old keys.

Every venue/cumulative scope-hash preimage is a typed object containing the
listed named fields and is encoded with this section's sorted-key, compact,
UTF-8 canonical JSON rule before hashing. Delimiter-concatenated strings are
forbidden. Tests pin exact preimage objects, canonical bytes, and digest vectors.

Envelope/payload binding is mandatory and is rechecked by both reducer and store:

- `source_revision == payload.after.revision`.
- Store, account fingerprint, data mode, and broker environment equal both the
  typed after-state and validated `PaperStateStore` provenance.
- Aggregate ID, correlation/order ID, idempotency key, local broker ID, and KIS
  broker ID equal their per-aggregate after-state fields; fields without a typed
  source are null rather than caller-selected.
- `identity_keys` are recomputed from the newly introduced fill evidence and may
  not be caller-forged.
- Event type matches the exact before/after state and reconciliation change.

## 6. Pure reducer contract

Add a pure reducer under `quantpilot/packages/core/execution/reducer.py`. It has
no clock, database, environment, broker client, audit recorder, or global state.
It MUST NOT mutate input events or models.

The legacy dispatch/cancel transition maps, the reconciliation transition map
currently local to `update_paper_order_dispatch`, and the reservation-release
mapping must have one pure domain definition shared by the reducer and SQLite
store. Move the definitions to the pure execution domain (with compatibility
re-exports if tests/importers require the old names); do not duplicate drifting
dictionaries and do not make the reducer import `sqlite_repositories.py`. This
relocation is semantic-preserving and receives its own equality/regression test.

For each independent stream it applies these rules in caller-supplied order:

1. Verify envelope provenance, event schema, canonical payload hash, aggregate
   type/ID, and typed after-state.
2. Require the first version to be 1 and every new version to equal
   `last_aggregate_version + 1`.
3. An already-seen `event_id` with identical envelope and hash is an exact no-op
   and does not advance version. Reuse with different bytes is corruption.
4. The same aggregate version with a different event is corruption. A gap or an
   unknown event/schema is fail-closed. The reducer never sorts or guesses.
5. Import events seed any valid current legacy revision. Non-import update events
   require `source_revision` to advance the prior authoritative revision by
   exactly one. The three create events `RiskReserved`, `OrderPrepared`, and
   `CancelPrepared` are constrained to `aggregate_version=1` and
   `source_revision=0` with no prior projection. For `CancelPrepared` this is a
   deliberate fail-closed tightening: `create_paper_cancel_request` does not
   itself assert `revision=0`, but its only production caller constructs
   revision-0 `prepared` requests, so the event layer rejects — and rolls
   back — any nonzero-revision create instead of inventing an event for it.
6. Event type must match the after-state and the before-to-after transition.
7. For order streams, transition and reconciliation surfaces MUST be subsets of
   the union of `PAPER_DISPATCH_TRANSITIONS`, the current reconciliation map, and
   the exact special store-method transitions below. Attempt count, claim
   evidence, broker identity, fills, and cumulative quantity remain monotonic
   exactly as the legacy store requires.
8. Reservation and cancel streams use the same rule: their generic transition
   tables plus the exact special store-method transitions below. No event may
   invent a transition merely because its after-state model validates.
9. Exact `event_id`+bytes retry is the only reducer no-op. Reuse of a true
   execution ID or cumulative evidence scope under a different event ID is a
   conflict, whether its payload is identical or divergent, and cannot consume
   an aggregate version. The authoritative legacy idempotency path suppresses a
   repeated broker observation before constructing a new event.

Special store-method transitions are an explicit closed allowlist:

```text
order_dispatch:  no row -> prepared revision 0
order_dispatch:  prepared/attempt 0 -> dispatch_claimed/attempt 1
order_dispatch:  prepared/attempt 0 -> prepared/attempt 0 with only the
                 successor session/fence, update time, and revision changed
risk_reservation:no row -> held revision 0
risk_reservation:held -> held with only the paired successor session/fence,
                 update time, and revision changed
risk_reservation:held -> exactly one matching released_* terminal through
                 `PAPER_RESERVATION_RELEASE_BY_DISPATCH`; no cross-terminal or
                 reopening transition
cancel_request:  no row -> prepared revision 0
cancel_request:  prepared/attempt 0 -> cancel_claimed/attempt 1
```

The reducer validates the field-level shape of these transitions. Per-stream
identity sets handle replay duplicates; cross-order execution/evidence reuse is
enforced by the store's scoped unique indexes before commit. The existing
store methods remain responsible for live lease expiry, exact active-session,
kill-state, and external-attempt authority checks; events do not duplicate or
widen that authority.

The reducer raises a pure exception family defined beside it with no database
import, and the SQLite store translates it exactly at its boundary with
`raise ... from exc`:

```text
PaperEventStreamConflict    -> PaperStateConflictError
    expected-version mismatch, source-revision mismatch, illegal transition,
    identity-scope reuse (identical or divergent payload)
PaperEventStreamCorruption  -> PaperStateCorruptionError
    payload-hash mismatch, divergent event_id reuse, same-version divergent
    event, sequence gap, envelope/provenance/after-state binding mismatch
PaperEventSchemaUnsupported -> PaperStateMigrationRequired
    unknown event_type or event_schema_version
```

Any failure raised inside the migration importer surfaces as
`PaperStateMigrationRequired`, matching the Gate 1 backfill error contract
(QP-RES-A1). The reducer never raises a store error type, and the store never
downgrades a reducer error to a warning or partial apply.

Raw event decoding performs a minimal envelope discriminator check before typed
Pydantic construction. An unknown `event_schema_version` or `event_type` is
classified as `PaperEventSchemaUnsupported` even if strict typed parsing would
otherwise fail first; malformed data for a known schema/type remains
`PaperEventStreamCorruption`.

The reducer returns a typed `PaperExecutionProjection` for one stream containing
the after-state, aggregate version, source revision, and identity sets needed for
duplicate validation. A helper joins correlated order/reservation/cancel
projections only for read-only parity diagnostics.

Late evidence is semantic, not last-write-wins:

- A fill after `DispatchClaimed` may advance directly to partial/filled before a
  local acceptance record, because the current transition table permits it.
- A later acceptance cannot regress partial/filled state.
- A lower cumulative fill observation cannot replace a higher one.
- Cancel requested/accepted remains nonterminal; fills still apply.
- Full fill wins a cancel race and the cancel journal reconciles filled.
- Contradictory evidence detected while an order is unresolved may use the
  existing reconciliation-blocked transition. Evidence that contradicts an
  already terminal order cannot change that terminal aggregate in schema v10:
  it raises conflict/corruption, commits no row/event/reservation change, and is
  surfaced to operator diagnostics outside this event stream. It never reopens
  capacity speculatively.

## 7. SQLite schema v11

Version constants become:

```text
PAPER_STATE_SCHEMA_VERSION = 11
PAPER_STATE_PREVIOUS_SCHEMA_VERSION = 10
PAPER_STATE_MIGRATABLE_SCHEMA_VERSIONS = frozenset({6, 7, 8, 9, 10})
```

Add one generic append-only table:

```sql
CREATE TABLE paper_execution_events (
    event_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    account_scope_fingerprint TEXT NOT NULL,
    data_mode TEXT NOT NULL,
    broker_environment TEXT NOT NULL,
    source TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    idempotency_key TEXT,
    local_broker_order_id TEXT,
    broker_order_id TEXT,
    original_client_order_id TEXT,
    venue_order_id TEXT,
    broker_sequence INTEGER,
    source_revision INTEGER NOT NULL,
    event_schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    UNIQUE (store_id, aggregate_type, aggregate_id, aggregate_version),
    FOREIGN KEY (store_id) REFERENCES state_store_metadata(store_id)
) WITHOUT ROWID;

CREATE TABLE paper_execution_event_identity_keys (
    event_id TEXT NOT NULL,
    identity_kind TEXT NOT NULL,
    identity_scope_hash TEXT NOT NULL,
    external_id TEXT NOT NULL,
    evidence_payload_hash TEXT NOT NULL,
    PRIMARY KEY (event_id, identity_kind, identity_scope_hash),
    UNIQUE (identity_kind, identity_scope_hash),
    FOREIGN KEY (event_id) REFERENCES paper_execution_events(event_id)
) WITHOUT ROWID;
```

Required indexes support `(store_id, aggregate_type, aggregate_id,
aggregate_version)` replay. The normalized child table stores one row for every
new/imported fill identity, so one event can atomically index multiple fills.
Its scoped unique constraint protects both true execution and cumulative KIS
observation identities. The typed key validator enforces the converse as well:
every child row has a known kind, external ID, correctly recomputed scope hash,
and matching evidence-payload hash; no nullable discriminator can bypass the
unique key. Cumulative observations are not promoted to venue executions.

There is no mutable shadow projection table in v1. Projection is rebuilt by the
pure reducer, making replay the tested behavior instead of testing a cache.

## 8. Append and duplicate semantics

Only `PaperStateStore` internal mutation paths may append canonical events. No
API, UI, LLM, RL output, or broker adapter receives general event-append
authority.

`PaperStateStore` constructs candidate events from the validated before/after
models plus the closed Section 3 mutation-origin token; callers do not supply
arbitrary envelopes, event types, sources, or times. Candidate events are
constructed only after the existing row-level idempotency paths have proven a
row change: every legacy exact-replay early return — identical re-prepare pair,
same-session takeover, exact-equality dispatch/cancel update, already-released
reservation on a terminal replay, identical cancel re-create, and the
reconciler's in-memory equality returns — exits before any candidate exists, so
duplicate candidates are suppressed before creation. Runtime `event_id`s are
then store-generated opaque IDs following the repository convention
(`new_id("pevt")`); the deterministic Section 10 derivation applies only to
import anchors. The step-1 exact-`event_id` no-op arm below is therefore
defensive reducer/append semantics, not a normal reopen path: reopening a
schema-v11 database runs no importer and creates no candidate. Tests exercise
exact duplicates directly and through fault/retry harnesses without claiming
that ordinary reopen appends an import event.

For each normal runtime transaction the set of changed authoritative aggregate
rows and the set of advancing events must be one-to-one. This invariant is
enforced by a typed internal per-transaction mutation batch guard (e.g.
`_PaperEventMutationGuard`), not by informal caller discipline: every store
mutation method registers each changed aggregate row and its candidate event in
the guard, and the guard's commit-time assertion verifies the one-to-one
property inside the existing transaction. A registered change without an event,
an event without a registered change, or an unknown mutation-origin token each
fail closed and roll back the whole transaction. A terminal dispatch plus a changed
reservation therefore has one event in each stream; multiple new fill identities
remain child keys of the single dispatch event. Truthful schema-migration import
anchors are the sole explicit exception because they seed unchanged legacy rows.

An internal append helper takes an event batch and an expected previous version
per aggregate. Inside the caller's existing `BEGIN IMMEDIATE` transaction it:

1. Checks `event_id` first. Exact persisted retry is a no-op only when no
   authoritative row changed in this transaction and the current row still
   equals that persisted event's typed after-state. A changed row cannot use an
   old event to satisfy dual-write. Divergent reuse raises conflict/corruption.
2. Loads each stream's last version and requires the caller's expected value.
3. Validates all candidate events through the pure reducer against current
   replay state, then requires each changed row's exact serialized JSON and
   revision to equal its candidate `payload.after` and `source_revision`.
4. Inserts every event and all of its normalized identity-key rows. Any failure
   rolls back the authoritative mutation, reservation release, cancel mutation,
   events, and identity keys together.

The event helper never opens or commits its own nested transaction. Immediately
before a normal runtime commit, the store asserts that every changed aggregate
has exactly one advancing event and that no unchanged aggregate received one;
the separate migration importer applies the Section 10 seed-event rule.

## 9. Exhaustive same-transaction dual-write map

Every authoritative mutation must append its event(s) before the existing
transaction commits:

| Existing mutation | Required event batch |
|---|---|
| atomic reservation + prepared dispatch | `RiskReserved`, `OrderPrepared` |
| prepared session takeover | `DispatchFenceRebound`, `RiskReservationFenceRebound` |
| one-attempt claim | `DispatchClaimed` |
| accepted or same-status durable evidence enrichment | `OrderAccepted` or `DispatchEvidenceObserved` |
| ambiguous outcome / interrupted recovery | `OutcomeUnknown` |
| partial/complete fill | `OrderPartiallyFilled` or `OrderFilled`; terminal batch emits `RiskReservationReleased` only when the row actually changes held-to-released |
| definitive rejection/cancel/pre-dispatch expiry/failure | matching order terminal event; add `RiskReservationReleased` only for an actual held-to-released transition |
| reconciliation block | `ReconciliationBlocked` |
| same-status pending/blocked to reconciled | `DispatchReconciled` |
| cancel journal prepare/claim/ack/unknown/reject/reconcile | matching cancel event |

Every row of this table binds to exactly one Section 3 mutation-origin token.
`expire_stale_prepared_dispatches` and `terminalize_prepared_dispatches_for_kill`
may perform two separate transactions per dispatch (fence rebind, then
pre-dispatch terminal); each transaction carries its own one-to-one event batch.
`recover_interrupted_dispatches` may recover multiple dispatches in one
transaction; each recovered aggregate gets exactly one `OutcomeUnknown` event.

The idempotent no-write paths emit no event and advance no stream: identical
re-prepare pair return, same-session takeover return, exact-equality
dispatch/cancel update return, already-released reservation on terminal replay,
identical cancel re-create return, and the reconciler's in-memory equality
returns.

The implementation MUST cover write paths that bypass
`update_paper_order_dispatch`: prepare, claim, prepared takeover, interrupted
recovery, and cancel-journal CAS. Hooking only the generic update method is a P1
event-gap defect.

The existing ordering remains unchanged: claim is committed before a possible
broker POST; ambiguous outcomes are never automatically reposted; terminal
reservation release stays in the exact order-terminal transaction.
Legal terminal self/enrichment writes that find an already released reservation
MUST NOT emit a second release event or advance the reservation stream.

## 10. Migration and truthful import

Schema v11 supports direct migration from v6-v10 in the current migration
transaction. For pre-v10 databases, reservation backfill runs first. Event import
then reads the completed authoritative rows.

It MUST NOT fabricate historical `OrderAccepted`, fill, cancel, or claim events
from a current snapshot. Instead it inserts one deterministic version-1 import
event for every existing aggregate row:

```text
PaperOrderDispatch    -> LegacyOrderDispatchImported
PaperRiskReservation -> LegacyRiskReservationImported
PaperCancelRequest    -> LegacyCancelRequestImported
```

The deterministic import `event_id` is derived from a typed preimage object with
the named fields `event_schema_version`, `store_id`, `aggregate_type`,
`aggregate_id`, `source_revision`, and `payload_hash`, encoded with the Section
5 sorted-key compact UTF-8 canonical JSON rule before hashing. Positional or
delimiter-concatenated preimages are forbidden. The event contains the full
current after-state with `legacy_snapshot=true`. A terminal legacy order may
truthfully have no reservation aggregate. Each imported order event also inserts
one normalized identity-key row for every imported fill-evidence item.

During `_initialize_schema`, runtime `self.provenance` is not yet installed.
Migration import builders and validators MUST receive the already decoded and
validated local `persisted` provenance explicitly; they MUST NOT call
`self.provenance`, `_require_paper_store()`, or another runtime-only accessor
before the migration transaction commits.

DDL, all import events, metadata JSON, `schema_version`, and `PRAGMA user_version`
commit or roll back together. Reopening a migrated DB creates no new events and
does not change event IDs, versions, or hashes. Future schema and provenance
mismatches continue to fail closed.

## 11. Shadow parity and authority rule

Gate 2 shadow parity is read-only:

```text
load canonical events
  -> verify hash/version/provenance
  -> pure replay per aggregate
  -> join by order_plan_id
  -> compare every observable legacy field
```

Parity comparison includes dispatch status/reconciliation/attempt/identity/fill
evidence, reservation status/capacity/fence, and cancel status/evidence. It
excludes only event metadata that has no legacy counterpart. Event replay MUST
make zero broker queries, zero broker POSTs, and zero legacy writes.

A mismatch is diagnostic evidence and blocks Gate 2 acceptance. V1 does not
silently repair the authoritative row from events or the event stream from the
row. Cutover remains blocked until the later gate explicitly changes authority.

## 12. Required executable tests

All tests are deterministic, fake-only, and secret-free.

### Reducer

- Replaying the same stream repeatedly produces byte-identical projection JSON
  and does not mutate inputs.
- Replay uses aggregate version, not timestamps.
- Gap, duplicate version with divergent bytes, hash corruption, unknown event,
  unknown schema, cross-account event, and illegal transition fail unchanged.
- Exact duplicate event ID/hash is a no-op; divergent reuse is blocked.
- Reducer transition surface is a subset of the legacy generic maps plus the
  explicit special store-method allowlist in Section 6.
- Special create/claim/fence classifiers run before the generic five-step
  dispatch precedence, whose five branches each have an executable vector.
- Pure exceptions distinguish conflict, corruption, and unsupported schema/type,
  including the pre-Pydantic raw-decode boundary.
- Fill evidence remains canonically ordered, additive, and non-regressing.

### Store atomicity and concurrency

- Event insert failure rolls back prepare+reservation, claim, takeover,
  interrupted recovery, terminal release, and cancel mutations.
- Terminal order plus an actually changed reservation-release event either all
  commit or all roll back; already released terminal enrichment emits no second
  release event.
- Two connections appending the same expected version have one winner.
- Idempotent prepare/reconciliation/retry emits no duplicate event.
- A changed row paired with an old exact event is rejected and rolled back; an
  exact-event no-op is legal only when the authoritative row is unchanged and
  still equals that event's after-state.
- Stale/future expected version leaves event stream and authoritative state
  unchanged.
- Session fence and account provenance mismatch fail before append.
- A mutation-origin token whose allowed delta does not match the observed
  before/after change fails closed with no row or event write.
- Every event's `occurred_at`/`received_at` equals the closed Section 3
  derivation; a caller-supplied time is impossible by construction.
- A same-status cancel write with changed fields (no v1 event type) fails
  closed and rolls back.
- The multi-fill dispatch payload carries no aggregate `time_basis`; per-fill
  bases survive replay byte-exactly.
- Store-derived causation is tested for null import/create roots, paired
  prepare/takeover/terminal batches, and normal same-stream predecessor chains.
- All import events in one migration share the single transaction-level
  `received_at` clock reading; import-ID and identity-scope canonical preimages
  have pinned digest vectors.
- Nonzero-revision cancel creation, same-status changed-field cancel writes,
  and every enumerated idempotent no-write path fail/return without consuming a
  runtime event ID or advancing a stream.

### Broker ordering and cancel/fill races

- Partial/full fill before acceptance advances legally; late acceptance cannot
  regress it.
- Fill before dispatch claim is rejected.
- Cumulative evidence 1 replay is a no-op, 1-to-2 creates one new fact, and
  2-to-1 blocks unchanged.
- A repeated broker observation is suppressed before event creation; a distinct
  event reusing a true execution/evidence scope (identical or divergent) blocks
  without consuming a version, and accounts scope real execution identity.
- Multi-fill updates and import anchors create one normalized identity-key row
  per newly introduced fill.
- Cancel requested/accepted is nonterminal and permits a later fill.
- Partial fill then confirmed cancel preserves fills and releases once.
- Full fill wins cancel race and cancel reconciles filled with no repost.
- Cancel rejection keeps order working and reservation held.
- Contradictory unresolved evidence may block reconciliation; contradictory
  post-terminal evidence raises with no row/event/reservation mutation and never
  reopens capacity speculatively.

### Migration and parity

- Representative v10 corpus covers all dispatch, held/released reservation, and
  cancel states; each imports once and replays exactly.
- Direct pre-v10 migration backfills reservations before import events.
- Import IDs/hashes are deterministic and reopen is idempotent.
- Injected migration failure rolls back schema metadata and all events.
- After every mutation path in Section 9, event replay equals authoritative rows.
- Restart rebuild performs zero broker calls and no writes.
- Shadow mode never adds a broker POST or changes live/autonomy defaults.

Acceptance commands:

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
git diff --check
```

Expected safety evidence remains `broker=mock`, `live_trading_enabled=false`,
Level 5 blocked, and all real KIS integration tests skipped/manual.

## 13. Gate acceptance

`QP-EXEC-EVENTS-V1` is accepted only when:

- Gate 1 is accepted and its aggregate identities are unchanged.
- Claude Code's initial decomposition review and substantive contract artifact
  are integrated and reviewed by the mission lead.
- All mutation paths dual-write atomically and no event gap exists.
- Reducer replay is deterministic under duplicate, gap, corruption, late
  evidence, and restart tests.
- Shadow projections equal authoritative rows on the complete fixture corpus.
- Full backend, smoke, and `git diff --check` pass.
- Independent audit has zero P0/P1 findings.
- No live/network/market-order/autonomy authority was added.
