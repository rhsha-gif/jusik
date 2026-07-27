# Atomic Risk Reservation v1 — Independent Claude Code Final Audit

Read-only adversarial audit of the `QP-RISK-RES-V1` schema-v10 implementation
against the binding contract `docs/contracts/atomic_risk_reservation_v1.md` and
the schema-v9 baseline. Written by the independent (non-implementer) auditor per
protocol §6; this document and the Claude-audit workboard fields are the only
artifacts modified.

## 1. Auditor, model, and reviewed range

| Field | Value |
|---|---|
| Auditor | Claude Code (independent final audit, `QP-RISK-RES-V1-audit`) |
| Exact model | `claude-fable-5` (CLI model selector alias `fable`, resolved to `claude-fable-5`) |
| Audit branch/worktree | `claude/qp-risk-reservation-v1-audit` (read-only over impl state) |
| Reviewed HEAD | `58715bd511fbcda8f19aadb54c63d31a830d5f95` |
| Reviewed range | `5dff17a..58715bd` (implementation commit `ce075bf` plus docs `0bd938f`, `58715bd`) |
| Date | 2026-07-11 KST |

Precondition verified before any read: `git status --short --branch` reported
`## claude/qp-risk-reservation-v1-audit` with a clean tree, and
`git rev-parse HEAD` returned `58715bd511fbcda8f19aadb54c63d31a830d5f95`.

## 2. Commands run and verbatim evidence

All commands local and offline; no network, no KIS, no broker endpoint, no
credentials read or printed.

```text
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp \
    --junitxml=.pytest_tmp/claude_audit.xml
# JUnit XML: tests=886, failures=0, errors=0, skipped=2  (= 884 passed, 2 skipped)

python -m quantpilot.jobs.run_smoke
# broker=mock
# live_trading_enabled=false
# operator.status=blocked
# operator.fallback=level5_flag_disabled

python -m quantpilot.jobs.run_kis_paper_kill engage
# {"status": "blocked", "reason_code": "paper_kill_disabled"}

git diff --check
# clean
```

One reproduction script (temp directory outside the repository, deterministic,
offline) was written to confirm finding QP-RES-A1; its output is quoted in §4.

## 3. What was audited and confirmed sound

Every item below was verified directly in the runtime source and its tests, not
from the completion candidate's claims.

1. **One-transaction reserve + prepared dispatch.**
   `PaperStateStore.reserve_and_insert_paper_order_dispatch`
   (`db/sqlite_repositories.py:1686`) performs kill check, exact-active-session
   check (`_require_exact_active_session` inside the transaction,
   `:1812`), idempotent-pair detection, held-capacity recomputation
   (`H_cash`/`H_qty(symbol)`/`H_gross` from `status='held'` rows), §4 admission,
   and both inserts under a single `BEGIN IMMEDIATE` (`_transaction`, `:661-669`,
   rollback on any exception). The legacy `insert_paper_order_dispatch` bypass
   now unconditionally raises (`:1676-1683`) and has no production caller
   (verified by grep; only the coordinator's single call site at
   `execution/paper_submission.py:486` inserts dispatches).
   Fault-injection tests prove both rollback directions:
   `test_dispatch_insert_failure_rolls_back_reservation` and
   `test_terminal_dispatch_and_reservation_release_roll_back_together`
   (`test_paper_dispatch_persistence.py:793`, `:822`).

2. **Exact integer arithmetic.** The coordinator proves whole-number
   quantity/limit-price at the boundary, ceils the minimum cash reserve, floors
   `min(orderable_cash, no_receivable_buy_amount) − reserve` via `Decimal`
   (`paper_submission.py:355-366`), and removed the former `1e-6`/`0.01` float
   tolerances from buy/sell admission. The store re-derives every basis from the
   paired dispatch's own evidence with `Decimal` floor/ceil and compares exact
   integers; SQLite admission uses integer arithmetic only. Conservative
   directionality confirmed: reserve rounded up, broker cash floored, current
   gross rounded up, gross limit floored
   (`test_fractional_capacity_inputs_round_conservatively`,
   `test_reservation_persists_integer...` equivalents in
   `test_paper_risk_reservation_model.py`).

3. **Concurrent admission.** Two-connection, barrier-synchronized concurrency
   test admits exactly one of two buys whose sum exceeds broker cash
   (`test_concurrent_buy_reservations_cannot_exceed_cash`); aggregate gross and
   sell-quantity exhaustion fail closed before any partial write
   (`test_aggregate_gross_reservation_fails_before_partial_write`,
   `test_aggregate_sell_reservation_fails_before_partial_write`). Buy admission
   is `request + H_cash <= floored basis`, `quantity <= broker quantity basis`,
   and `current_gross + H_gross + request <= gross_limit`, exactly per contract
   §4; sells are `quantity + H_qty(symbol) <= snapshot orderable`.

4. **Capacity-evidence binding / anti-forgery.** Reservation identity, symbol,
   side, session, fence, fingerprint-relevant bases, notional, gross basis,
   minimum cash reserve, and derived gross limit must all match the paired
   dispatch before any write (`db/sqlite_repositories.py:1740-1795`);
   `minimum_cash_reserve_krw` is persisted on the v10 dispatch, included in
   `_request_fingerprint` and `_dispatch_immutable_identity`, and re-preparing an
   idempotency key with a different reserve fails closed
   (`test_reservation_capacity_evidence_must_match_dispatch`,
   `test_cash_reserve_and_gross_limit_cannot_be_forged_together`,
   coordinator "cash-reserve evidence changed" tests).

5. **Idempotent reprepare.** Same-key reprepare returns the existing pair
   unchanged; divergent evidence conflicts; a crash between the reserve+prepare
   commit and the claim recovers to the same paired `held`+`prepared` state.
   For migrated v9 open rows the synthesized reservation is the comparison
   authority (`test_migrated_open_dispatch_reprepare_uses_backfilled_cash_reserve`).

6. **Provenance, revision CAS, fencing, takeover.**
   `_validate_reservation_provenance` mirrors the dispatch check; every
   reservation write is a CAS on `(reservation_id, status='held', revision)` with
   `rowcount==1` assertion; `takeover_prepared_paper_order_dispatch` re-fences the
   paired reservation in the same transaction
   (`db/sqlite_repositories.py:2185-2240`), asserted end-to-end by
   `test_restart_expires_prepared_record_without_any_post` (released reservation
   carries the successor session/fence).

7. **Terminal release in one transaction; conservative hold.**
   `update_paper_order_dispatch` is the only terminalization writer (submission
   `_terminal_pre_dispatch`/`_definitive_rejection`/`_outcome_unknown`, kill
   terminalization, and broker reconciliation all feed it — verified by grep) and
   calls `_release_reservation_for_terminal_dispatch` inside the same
   `BEGIN IMMEDIATE` transaction after the dispatch CAS
   (`db/sqlite_repositories.py:2477`). The release map contains exactly the five
   definitive terminals (`filled`/`cancelled`/`rejected`/`expired_pre_dispatch`/
   `failed_pre_dispatch`); `outcome_unknown`, `accepted`, and `partially_filled`
   are absent, so those states keep the whole reservation `held`
   (tests: unknown/rejected coordinator matrix, partial-fill hold and
   reconciled-fill release in `test_paper_reconciliation.py`, competing terminal
   updates release exactly once and reopen capacity). Terminal replay is
   idempotent; cross-terminal reservation change conflicts.
   `PaperReconciliationApplier` contains no reservation reference (projection
   only), per contract §6.

8. **Ambiguous POST never retried.** `claim_dispatch_attempt` still enforces
   single-attempt (`attempt_count != 0` refuses) and blocks new external attempts
   while any `dispatch_claimed`/`outcome_unknown` dispatch exists, now also
   requiring a matching `held` reservation before any claim
   (`db/sqlite_repositories.py:2012-2026`);
   `test_unknown_is_never_retried_and_business_reject_is_terminal` additionally
   asserts the reservation stays `held` on `outcome_unknown`.

9. **v9→v10 migration and backfill.** Table creation, backfill, metadata CAS
   update, and `PRAGMA user_version = 10` all execute in one `BEGIN IMMEDIATE`
   transaction (`_initialize_schema`, `:326-625`), so any backfill failure rolls
   back the whole migration. Backfill synthesizes exactly one `held` reservation
   per open dispatch from that dispatch's own durable evidence (full original
   notional/quantity — conservative), skips terminals, is idempotent on re-entry,
   and raises `PaperStateMigrationRequired` on fractional legacy quantity or
   missing buy evidence, leaving v9 intact
   (`test_migration_backfills_open_buy_and_sell_but_not_terminal`,
   `test_migration_backfill_failure_rolls_back_schema_metadata`,
   `test_schema_v9_migrates_to_v10_and_backfills_open_dispatch`). A schema >10
   database still fails closed (`test_future_paper_schema_fails_closed`).
   Realistic v9 JSON (dispatch `minimum_cash_reserve_krw` stripped) is used by
   the downgrade helpers.

10. **Kill interaction.** The store blocks any new reservation while a
    non-released kill operation exists (`"paper kill blocks reservation"`,
    `:1797-1805`); kill terminalization of prepared dispatches releases the
    reservation to `released_expired` with zero broker POSTs; kill-driven
    reconciled cancels release exactly once
    (`test_kill_service_cancels_managed_order_exactly_once`,
    `test_killed_store_rejects_new_reserved_dispatch_before_release`).

11. **Durable sell guardrail.** `HarnessService._guardrail_state` projects held
    `sell_quantity` reservations from the durable store into
    `reserved_sell_quantities`, deduplicated by `order_plan_id` against
    checkpoint/dispatch accounting so capacity is counted once at the full held
    amount while held and released only on durable terminals
    (`harness_service.py:856-889`,
    `test_guardrail_reads_only_held_sell_reservations_from_paper_store`).

12. **Safe defaults, secret-free, fake-only.** The full suite runs offline with
    fake clients and fixed clocks; smoke stays `broker=mock`,
    `live_trading_enabled=false`, Level 5 blocked; kill CLI default-blocked;
    reservation JSON and schema columns are asserted secret-free with only the
    opaque `sha256:` fingerprint
    (`test_dispatch_model_and_schema_store_no_raw_secrets_or_account_id` now
    covers `paper_risk_reservations`). No settings or safety flag was changed in
    the audited range (verified via `git diff --name-only 5dff17a..HEAD`).

The contract-file edits inside the audited range add the
`minimum_cash_reserve_krw` binding, the backfilled-reserve reconstruction rule,
and two forgery tests; they strengthen requirements and match the implemented
behavior. They do not weaken any §0/§4/§5/§6 safety clause.

## 4. Findings

| ID | Severity | File / symbol | Defect and impact | Recommendation |
|---|---|---|---|---|
| QP-RES-A1 | P2 — **CLOSED** by `a892210` (follow-up §9) | `db/sqlite_repositories.py:830` `_backfill_open_dispatch_reservations` (sell branch, `minimum_cash_reserve_krw = snapshot_equity - current_gross`) | For a legacy open **sell** dispatch with fractional float equity/cash where `ceil(equity − cash) > floor(equity)` (e.g. `equity=10_000_000.5`, `cash=0.3`), the reconstructed reserve is `−1`, so `PaperRiskReservation` raises a raw pydantic `ValidationError` instead of the contract-mandated `PaperStateMigrationRequired` (§9). **Reproduced** offline: `OTHER EXCEPTION pydantic_core...ValidationError: minimum_cash_reserve_krw ... input_value=-1`; after the failure `user_version=9`, metadata `schema_version=9`, no reservation table — the migration transaction rolls back completely, so the failure is fully fail-closed and no weaker reservation is created. Impact is diagnosability/error-contract only. | Clamp the sell-branch reconstruction (`max(0, ...)`) or wrap backfill model construction to re-raise as `PaperStateMigrationRequired`, plus one regression test with fractional legacy equity/cash. |
| QP-RES-A2 | P2 | `harness_service.py:856-889` (held-reservation guardrail projection) | The durable held sell-reservation loop is not filtered by `policy_id` (reservations carry none), while the paired dispatch loop skips `dispatch.policy_id != policy.policy_id`. If one paper store ever serves multiple policies, one policy's held sell reservation inflates another policy's `reserved_sell_quantities`. Direction is strictly conservative (over-blocking, never under-reserving), and current usage is one policy per paper store, so this cannot over-allocate capacity. | Either document single-policy-per-store as an invariant or join reservations to their dispatch's `policy_id` before projecting. |
| QP-RES-A3 | P3 | `db/sqlite_repositories.py:1975-1979` (pair-insert `sqlite3.IntegrityError` handler) | Any integrity failure during the pair insert (trigger, FK, constraint) is reported as "paper dispatch or reservation identity already exists", which is misleading for non-duplicate integrity causes (visible in the fault-injection test, which must match "already exists" for a forced trigger abort). Rollback behavior is correct. | Distinguish duplicate-key from other integrity causes in the message when convenient. |

No other defect was found. Specifically checked and clean: reservation release
on every terminal path, no second POST path, no reservation release owned by the
in-memory applier, no partial release on `partially_filled`, no capacity
mutation of a held reservation, no float tolerance in admission, no
direct-SQL terminal write bypassing release, and no secret material in the new
model, table, or JSON.

## 5. Residual severity counts and recommendation

- **Residual P0: 0**
- **Residual P1: 0**
- P2: 1 residual (QP-RES-A2 — strictly conservative in direction; cannot
  over-allocate capacity, release on ambiguity, or grant POST authority).
  QP-RES-A1 was a second P2 at the original audit HEAD `58715bd`; it is
  **CLOSED** by fix commit `a892210` (verified in the §9 follow-up) and is kept
  in the §4 table for history.
- P3: 1 residual (QP-RES-A3)

**Recommendation: ACCEPT** `QP-RISK-RES-V1-impl` for Gate 1 development
readiness. The P2/P3 findings do not block under the contract's blocking rule
(P0/P1 only); they are recommended as small follow-ups before or alongside
`QP-EXEC-EVENTS-V1`.

## 6. Safety, network, and authority statement

- This audit was read-only over all runtime code, tests, contracts, settings,
  and ledgers; only `docs/atomic_risk_reservation_v1_claude_audit.md` (this
  report) and the Claude-audit fields of
  `docs/atomic_risk_reservation_v1_workboard.md` were created/modified.
- No network, KIS, broker endpoint, external connector, or package installer was
  called; no credential, account number, or secret was accessed or printed.
- All safety invariants were re-verified unchanged: `LIVE_TRADING_ENABLED=false`,
  `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`,
  `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock`;
  `DurablePaperSubmissionCoordinator` remains the sole broker POST authority; an
  ambiguous broker POST is never automatically retried.
- No authority boundary was widened by the audited change or by this audit.

## 7. Known limits

- Real KIS paper TR semantics (`VTTC0084R` cancelable-order inquiry, real
  buying-power field mapping, real session-calendar edges) remain unverified by
  automatic tests; they gate **paper operational readiness (Gate P)** and
  require explicit user credentials and manual authorization
  (`roadmap_acceptance_matrix.md` §3, Gate P). This audit covers fake-client
  development readiness only.
- Concurrency evidence uses two real SQLite connections with barriers; true
  multi-process crash timing is approximated by connection-level fault injection
  and restart tests, which is the strongest evidence available offline.

## 8. Integration requests to the Codex mission lead

1. Treat QP-RES-A1 and QP-RES-A2 as small non-blocking follow-ups: clamp or
   re-raise the sell-branch backfill reserve as `PaperStateMigrationRequired`
   with a fractional-legacy regression test, and either document the
   single-policy-per-paper-store invariant or policy-scope the reservation
   guardrail projection.
2. On integration, re-run the §2 commands on the clean integration commit per
   `roadmap_acceptance_matrix.md` §2/§5, and record Gate 1 ledger evidence with
   the verbatim lines (the acceptance ledger is lead-owned; not touched here).
3. Preserve the reservation aggregate identity (`reservation_id` ↔
   `order_plan_id`) unchanged before `QP-EXEC-EVENTS-V1` dual-write begins, as
   already recorded in the workboard integration requests.
4. Append the capability-scorecard evidence row for this audit task
   (`claude-fable-5`, independent implementation audit) at the mission
   completion checkpoint (scorecard is lead/owner-managed; not modified by this
   audit).

## 9. Follow-up audit of QP-RES-A1 fix (2026-07-11 KST)

Independent follow-up review by Claude Code, exact model `claude-fable-5`
(CLI model selector alias `fable`, resolved to `claude-fable-5`), on branch
`claude/qp-risk-reservation-v1-audit` at HEAD
`a892210150db0c9c4b84fb1f8159d6e2cec8b7ee` (clean tree verified before any
read). Reviewed delta: `c021a50..a892210`, a narrow Codex fix for QP-RES-A1
plus one regression test; no other runtime file changed
(`git diff c021a50..a892210 --stat`: `sqlite_repositories.py` +7/−1,
`test_paper_dispatch_persistence.py` +56).

**What the fix does.** In `_backfill_open_dispatch_reservations`
(`db/sqlite_repositories.py:954-959`), the synthesized-reservation construction
`PaperRiskReservation(**values)` is now wrapped in
`try/except ValueError`, re-raised as
`PaperStateMigrationRequired("open legacy dispatch cannot be promoted to a
valid risk reservation") from exc`.

**Verified properties:**

1. **Correct error type, fail-closed.** A legacy open sell with fractional
   equity/cash (`snapshot_equity=10_000_000.5`, `snapshot_cash=0.3`), whose
   reconstructed `minimum_cash_reserve_krw` is `−1`, now fails as
   `PaperStateMigrationRequired` per contract §9 instead of a raw pydantic
   `ValidationError`. The handler only re-raises; nothing clamps, substitutes,
   or admits a weaker reservation, and the chained `from exc` preserves the
   original validation evidence.
2. **Catch scope is exactly synthesized model validation.** The `try` block
   wraps only the `PaperRiskReservation(**values)` constructor call. Pydantic
   `ValidationError` subclasses `ValueError` and is caught;
   `PaperStateMigrationRequired`/`PaperStateError` subclass `RuntimeError`
   (`db/sqlite_repositories.py:36`, `:52`), so the pre-existing explicit
   backfill raises (missing buy evidence `:907`, gross-capacity `:913`,
   conflicting existing reservation `:868`) and any store error cannot be
   intercepted or re-labeled by this handler. The SQL insert and dispatch
   decode remain outside the `try`.
3. **Whole-migration rollback preserved.** Backfill runs at
   `db/sqlite_repositories.py:568` inside the single `BEGIN IMMEDIATE`
   migration transaction (`with self._transaction():`, `:326`; rollback on any
   exception, `:661-669`) that also creates the reservation table, applies the
   metadata CAS update to v10, and sets `PRAGMA user_version = 10` (`:623`).
   The regression test
   `test_migration_fractional_sell_audit_failure_uses_migration_error`
   asserts post-failure `PRAGMA user_version == 9`, metadata
   `schema_version == 9`, and no `paper_risk_reservations` table.
4. **Regression test is faithful.** It builds a real v10 store, downgrades to
   v9 via the existing helper, injects the fractional equity/cash directly into
   the legacy dispatch JSON, and requires the exact
   `PaperStateMigrationRequired` match — the same reproduction shape as the
   original §4 finding.

**Focused offline evidence (no network, no KIS, no credentials):**

```text
python -m pytest quantpilot/tests/unit/test_paper_dispatch_persistence.py \
    -p no:cacheprovider --basetemp=.pytest_tmp_claude_a1
# 41 passed in 3.16s  (includes the new regression test; also passes alone
#  under -k fractional_sell)

git diff --check
# clean
```

**Verdict: QP-RES-A1 is CLOSED.** The fix implements exactly the §4
recommendation (re-raise as `PaperStateMigrationRequired` plus a
fractional-legacy regression test), weakens no evidence check, admits no
invalid reservation, and changes no safety flag or POST authority.
QP-RES-A2 (P2) and QP-RES-A3 (P3) remain open, non-blocking, and unchanged by
this delta. **Residual P0: 0. Residual P1: 0.** The §5 ACCEPT recommendation
stands for the fixed HEAD `a892210`.
