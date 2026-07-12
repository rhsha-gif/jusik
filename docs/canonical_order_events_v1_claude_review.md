# Canonical Execution Events v1 — Independent Claude Code Initial Decomposition Review

Required counterpart review for `QP-EXEC-EVENTS-V1`, task `QP-EVT-010`.
Documentation-only: this review corrects the binding contract and records the
decomposition; it implements no schema v11, event model, reducer, SQLite write,
test, broker, or runtime behavior.

## 1. Reviewer, model, and reviewed scope

| Field | Value |
|---|---|
| Reviewer | Claude Code (independent counterpart, non-implementer) |
| Exact model | `claude-fable-5` (CLI alias `fable`, resolved to `claude-fable-5`) |
| Branch/worktree | `claude/qp-exec-events-v1-review` / `주식트레이더-claude-exec-events-review` |
| Reviewed HEAD | `e08ef986f69c529125e0657c68f1c99047e87ec1` (clean tree verified before any edit) |
| Scope | `docs/contracts/canonical_order_events_v1.md` draft vs. actual schema-v10 code; QP-EVT-020/030/040 decomposition |
| Date | 2026-07-11 KST |

Precondition verified: `git status --short --branch` reported
`## claude/qp-exec-events-v1-review` with a clean tree; `git rev-parse HEAD`
returned the expected commit. No branch switch, fetch, merge, rebase, reset, or
stash was performed. No network, KIS, credential, or package installer was used.

## 2. Repository evidence inspected

All decisions below are grounded in direct reads of:

- `quantpilot/packages/db/sqlite_repositories.py` — transition maps (`:68-138`),
  error family (`:36-61`), `_initialize_schema`/migration transaction
  (`:260-626`), `_serialize` canonical JSON (`:672-693`),
  `reserve_and_insert_paper_order_dispatch` (`:1691`), `claim_dispatch_attempt`
  (`:1987`), `takeover_prepared_paper_order_dispatch` (`:2108`, including the
  same-session no-write return `:2135-2139`), `_dispatch_immutable_identity`
  (`:2245`), `_release_reservation_for_terminal_dispatch` (`:2292`, including
  the already-released no-write return `:2307-2308`),
  `update_paper_order_dispatch` (`:2352`, exact-equality return `:2367-2368`,
  reconciliation map `:2451-2455`), `recover_interrupted_dispatches` (`:2485`,
  multi-row single transaction), cancel create/claim/update (`:2859-3061`,
  identical-create return `:2909-2915`, exact-equality return `:3021-3022`).
- `quantpilot/packages/core/operator/position_ledger.py` —
  `PaperDispatchFillEvidence` (`:416-465`, per-fill `time_basis`),
  `PaperOrderDispatch` (`:468-543`, broker identity fields `:526-536`),
  `PaperCancelRequest` (`:335-361`).
- `quantpilot/packages/core/execution/paper_submission.py` — claim/POST ordering
  and guard rejections (`:560-653`), `_record_acceptance` (`:655-688`),
  `_definitive_rejection` (`:690`), `_outcome_unknown` (`:711`),
  `_terminal_pre_dispatch` (`:729`), `expire_stale_prepared_dispatches` (`:171`),
  `terminalize_prepared_dispatches_for_kill` (`:210`).
- `quantpilot/packages/core/execution/paper_reconciliation.py` —
  `reconcile_dispatch` single-revision combined mutation (`:150-257`),
  `_blocked` (`:298-326`), `_status_from_row` (`:382`), `_merge_fill_evidence`
  and the `kisagg-*` reference derivation (`:399-442`).
- `quantpilot/packages/core/execution/paper_kill.py` — cancel journal drivers
  (`:100-183`, `:324-378` `_submit_claimed_cancel`, `:380-439`
  `_synchronize_cancel_requests`/`_persist_cancel_state`).
- `quantpilot/packages/db/audit.py` — secret-field conventions (`:12-27`,
  `:133-144`, the `_token` suffix rule motivating the single `fencing_token`
  exception).
- Governing docs: `AGENTS.md`, collaboration protocol, roadmap acceptance
  matrix/workboard, Gate 1 contract/workboard/audit, event workboard.

## 3. Answers to the mandatory review questions

1. **Stream separation** — correct. The three aggregates map 1:1 to
   independently CAS-revisioned tables; `aggregate_version` is a store-assigned
   contiguous sequence distinct from row `revision` (an import may seed any
   legacy revision at version 1; one legacy transaction can advance two
   streams via the terminal-release pairing).
2. **Determinism/fail-closed identity machinery** — sound after corrections:
   canonical bytes match the store's `_serialize` convention; secret rejection
   mirrors `audit.py` with the sole `fencing_token` structural exception; exact
   replay vs divergent reuse is split correctly. Runtime `event_id` generation
   and `occurred_at` derivation were unspecified (findings R2/R7, fixed).
3. **Cumulative vs true executions** — the draft correctly refuses to promote
   `kisagg-*` observations to executions, but its scope-hash wording was
   ambiguous and, read strictly, unsatisfiable (finding R5, fixed): the
   `kisagg-*` raw reference covers only order number + cumulative totals, so
   cross-local-order reuse protection depends entirely on the exact scope-hash
   field list now pinned in contract §5.
4. **Reducer surface subset** — yes with the corrected precedence: legacy
   generic maps plus the exact special store-method transitions;
   fill-before-acceptance (`dispatch_claimed -> partially_filled/filled`) is in
   `PAPER_DISPATCH_TRANSITIONS`; late acceptance cannot regress; terminal
   contradiction raises with no mutation; gaps fail closed; no speculative
   release (the release map contains exactly the five definitive terminals).
5. **Same-transaction rule per mutation** — complete after the §9 additions:
   prepare, claim, takeover (incl. its no-write return), interrupted recovery
   (multi-aggregate), generic dispatch updates, terminal release (incl. the
   already-released no-write return), cancel create/claim/update, and every
   idempotent no-op are now individually enumerated.
6. **Migration truthfulness** — yes: one deterministic version-1 snapshot
   import per existing row after reservation backfill, explicit `persisted`
   provenance (no runtime accessor), all-or-nothing with schema metadata and
   `PRAGMA user_version`, idempotent reopen. Matches the observed
   `_initialize_schema` single-transaction structure and the Gate 1 QP-RES-A1
   error-contract precedent.
7. **Shadow parity** — read-only, all observable authoritative fields, zero
   broker calls/writes, no repair, no cutover. Preserved unchanged.
8. **Decomposition** — see §5; path-disjoint and dependency-ordered.
9. **Scope creep** — none found: kernel v2, ledger, correction/bust/replace,
   live, market orders, and cutover remain excluded; `OrderSubmitted` remains
   correctly rejected as unprovable after a crash.

## 4. Findings

Severity: P0 = safety/authority defect; P1 = blocks a correct implementation or
permits untruthful/ambiguous journal facts; P2 = clarity/robustness.

| ID | Sev | Evidence | Resolution | Residual |
|---|---|---|---|---|
| R1 | P1 | Decision A: `source` not derivable from before/after — `dispatch_claimed -> rejected` is produced by both `_definitive_rejection` (`paper_submission.py:690`) and reconciliation; the draft allowlist had **no** source at all for pre-dispatch terminals and local guard rejections (`_terminal_pre_dispatch` `paper_submission.py:729`, kill/session guards `:569-597`) | Closed typed `mutation_origin` tokens supplied by exact production call sites, mapped to `source`, validated per-origin against allowlisted delta shapes; added `local_submission_result` source (contract §3) | closed |
| R2 | P1 | Decision B: only import `event_id`s were specified; runtime IDs undefined | Store-generated opaque `new_id("pevt")` created only after the row-level idempotency paths prove a row change; duplicate candidates suppressed before creation (§8) | closed |
| R3 | P1 | Decision C: `causation_id` said only "command or immediately preceding event" | Exact closed rule: null for imports and `RiskReserved`/`CancelPrepared`; same-batch `RiskReserved` for `OrderPrepared`; cross-stream terminal-dispatch causation for `RiskReservationReleased`; fence-rebind pairing; same-stream predecessor otherwise; always store-derived (§1) | closed |
| R4 | P1 | Decision D: payload-level `time_basis` cannot be aggregated — `fill_evidence` items each carry their own `time_basis` literal (`position_ledger.py:428-431`) and one snapshot may mix values | Payload-level field removed; time basis lives only per-fill and per-identity-key (§5) | closed |
| R5 | P1 | Decision E: "broker branch/organization identity" ambiguous; `broker_forwarding_order_org_number` is acceptance-only (`paper_submission.py:678`) and legally null on reconciliation-only lifecycles, while reconciliation writes `broker_order_branch_number` in the same revision as fills (`paper_reconciliation.py:244`) | Exact component list: environment, account fingerprint, kind, business date, KIS order number, branch number, evidence ID; forwarding org excluded with rationale; null component fails closed; no local-ID fallback (§5) | closed |
| R6 | P1 | Decision F: `reconcile_dispatch` changes lifecycle, fills, and reconciliation in one revision (`paper_reconciliation.py:236-257`); draft had partial per-event rules but no total precedence | Five-step total deterministic event-type function recomputed by both store and reducer (§4) | closed |
| R7 | P1 | Decision G: `occurred_at` undefined for rejection, ambiguity, pre-dispatch failure/expiry, recovery, and cancel mutations — callers could select times | Closed derivation: `received_at = after.updated_at` for all non-import events; `OrderAccepted` combines verified KIS business date + order time else `after.updated_at`; everything else `after.updated_at`; imports use legacy `updated_at` + one migration clock reading (§3) | closed |
| R8 | P1 | Decision H: reducer exception surface and SQLite mapping undefined; the reducer must not import store errors | Pure family `PaperEventStreamConflict`/`PaperEventStreamCorruption`/`PaperEventSchemaUnsupported` with exact one-way translation to `PaperStateConflictError`/`PaperStateCorruptionError`/`PaperStateMigrationRequired`; migration failures surface as `PaperStateMigrationRequired` (§6) | closed |
| R9 | P1 | Cancel stream had no rule for same-status changed-field writes: `PAPER_CANCEL_TRANSITIONS` permits self-loops (`sqlite_repositories.py:101-123`) and `update_paper_cancel_request` accepts them, but no draft event type existed — the one-to-one invariant would fail undefined | Decided fail-closed: closed per-status cancel event map; same-status changed-field cancel writes are excluded from v1 and roll back; production inspection shows no such caller (§4) | closed |
| R10 | P2 | One-to-one row/event invariant relied on informal caller discipline | Typed internal per-transaction mutation batch guard with commit-time assertion (§8) | closed |
| R11 | P2 | Idempotent no-write paths not enumerated (same-session takeover `:2135-2139`, identical cancel re-create `:2909-2915`, already-released reservation `:2307-2308`, exact-equality updates, reconciler in-memory equality) | Enumerated in §9; each emits no event and advances no stream | closed |
| R12 | P2 | `evidence_payload_hash` bytes not pinned; `CancelPrepared` revision-0 constraint is a tightening the store does not itself assert | Pinned to the §5 canonical-bytes rule; tightening documented explicitly as fail-closed with its production-caller evidence (§5, §6) | closed |

No P0 was found: the draft never granted broker authority, never widened a
legacy transition, and preserved shadow (non-)authority throughout.

## 5. Decomposition for QP-EVT-020/030/040

Dependency order `010 -> 020 -> 030 -> 040 -> 050` is confirmed correct and
acceptance-testable. Path-disjointness decisions:

- **QP-EVT-020 (pure domain):** `events.py`, `reducer.py`, focused unit tests.
  Includes as its **first slice** the semantic-preserving relocation of the
  dispatch/cancel/reconciliation/reservation-release transition maps into the
  pure execution domain with compatibility re-exports plus an
  equality/regression test. That slice touches `sqlite_repositories.py` only
  for the re-export shim; because 030 strictly depends on 020's merged commit,
  no concurrent path overlap occurs. Acceptance: the reducer test family of
  contract §12 passes with no DB/broker import.
- **QP-EVT-030 (store/schema, mission lead):** schema v11 DDL, append helper,
  mutation batch guard, the `mutation_origin` keyword on the two generic
  mutators and its exact call-site updates in submission/reconciliation/kill
  (the narrow call-site change already authorized by the workboard ownership
  map), and the migration importer. Acceptance: the atomicity/concurrency and
  migration test families of §12.
- **QP-EVT-040 (parity/races):** parity corpus, race/fault/restart tests, and
  report; test paths disjoint from 020/030 runtime paths. Acceptance: the
  broker ordering/race and parity families of §12 plus full backend + smoke.

## 6. Adversarial acceptance mapping to concrete mutation classes

| Mutation class (exact code path) | Required adversarial evidence (contract §12) |
|---|---|
| `reserve_and_insert_paper_order_dispatch` | pair-create batch atomic; idempotent re-prepare emits nothing; injected event failure rolls back both rows |
| `claim_dispatch_attempt` | `DispatchClaimed` committed before any POST; fill-before-claim rejected |
| `takeover_prepared_paper_order_dispatch` | two-stream rebind batch atomic; same-session return emits nothing |
| `recover_interrupted_dispatches` | one `OutcomeUnknown` per recovered aggregate in one transaction |
| `update_paper_order_dispatch` via `_record_acceptance`/`_definitive_rejection`/`_outcome_unknown`/`_terminal_pre_dispatch` | origin-token delta validation; occurred_at derivation; terminal + actually-changed release all-or-nothing; already-released replay emits no second release |
| `reconcile_dispatch` combined revision | single event by precedence rule; multi-fill child keys; cumulative 1-to-1 no-op, 1-to-2 one fact, 2-to-1 blocked; scope reuse blocked without consuming a version |
| `_blocked` | `ReconciliationBlocked` including the combined `outcome_unknown` write |
| cancel create/claim/`_persist_cancel_state`/`_synchronize_cancel_requests` | per-status cancel events; same-status changed-field write fails closed; cancel/fill races in both orders with no repost and single release |
| `_initialize_schema` migration | truthful one-import-per-row, deterministic IDs, idempotent reopen, all-or-nothing rollback, pre-v10 backfill-then-import order |

## 7. Residual severity counts

- **Residual P0: 0**
- **Residual P1: 0** (R1-R9 all resolved in the corrected contract)
- Residual P2: 0 (R10-R12 resolved); no open blockers.

## 8. Safety and no-authority-change statement

- Only the three authorized paths were modified/created:
  `docs/contracts/canonical_order_events_v1.md`,
  `docs/canonical_order_events_v1_claude_review.md`,
  `docs/canonical_order_events_v1_workboard.md`. All runtime code, tests,
  ledgers, settings, and the scorecard were read-only.
- All safety invariants are preserved verbatim: `LIVE_TRADING_ENABLED=false`,
  `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`,
  `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock`; fake-only deterministic
  automatic tests; `DurablePaperSubmissionCoordinator` remains the sole broker
  POST authority; ambiguous broker POST outcomes are never automatically
  reposted; schema-v10 rows remain authoritative through Gate 2; events cannot
  widen or repair authoritative legacy state; Gate 1 is accepted while
  Gate P/manual KIS validation stays pending.
- No network, KIS, external connector, package installer, credential, or
  account data was used. No broker/backend integration test was run for this
  docs-only task (per the acceptance-matrix documentation-gate rule); the
  integrating lead re-runs backend + smoke on the clean integration commit.
- The corrections add no authority anywhere: the `mutation_origin` keyword and
  the batch guard only constrain existing writers further (fail-closed), and
  the two deliberate tightenings (revision-0 cancel create; no same-status
  cancel enrichment event) block only production-unreachable mutations.

## 9. Blockers and mission-lead integration requests

No blocker. Requests:

1. Inspect and integrate this review commit before starting QP-EVT-020
   (contract gate); the commit SHA is supplied in the final handoff.
2. When implementing QP-EVT-030, land the `mutation_origin` call-site changes
   in the same commit as the store signature change so no caller ever runs
   against an origin-less mutator.
3. Sequence the QP-EVT-020 transition-map relocation slice as the first merged
   artifact of 020 so 030 never overlaps `sqlite_repositories.py` concurrently.
4. Existing schema-v10 `PaperDispatchFillEvidence` rows with
   `time_basis="broker_execution"` already map to v1 `venue_execution` keys and
   must be imported/replayed now. If the current KIS production adapter later
   begins producing a new per-execution reference or timestamp shape, treat that
   new shape as an event-schema revision rather than reinterpreting `kisagg-*`.
5. Claude-owned capability-scorecard evidence for this task class is recorded
   separately at mission close (the scorecard is lead/owner-managed; not
   modified here).

## 10. Mission-lead integration review

The mission lead inspected commit `80e05d5` with two independent read-only
cross-checks. Residual P0=0/P1=0; QP-EVT-020 is authorized after the following
nonblocking P2 clarifications were incorporated into the binding contract and
workboard:

- special create/claim/fence shapes are classified before generic dispatch
  precedence;
- ordinary schema-v11 reopen does not retry migration imports, while exact
  duplicate handling remains a reducer/fault-retry defense;
- import-ID and identity-scope preimages use typed canonical JSON with pinned
  digest vectors, never delimiter concatenation;
- unknown schema/type at the raw decode boundary maps to
  `PaperEventSchemaUnsupported` before strict typed parsing;
- existing `broker_execution` evidence is a v1 `venue_execution` import/replay
  case even though the current KIS production adapter has no known producer;
- causation, origin/source, event-time, cancel-tightening, no-write, and all five
  generic precedence branches receive explicit executable tests.

These are contract/test precision corrections only. They add no runtime,
broker, network, or authority change and do not alter the review's ACCEPT
conclusion.
