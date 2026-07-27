# QP-KER-015 Handoff to the Active Codex Session

Written: 2026-07-15 KST, by the Claude Code mission lead. This file is
deliberately uncommitted so the active Codex session sees it in its working
tree immediately.

## TL;DR — stop duplicate work

Gate `QP-KER-015` (temporal/durable expiry + strategy version totality
hardening) is **already implemented, audited, P1-closed, and independently
verified** on a dedicated branch. The uncommitted rewrite of
`quantpilot/packages/core/execution/paper_submission.py` currently sitting in
this main working tree re-drafts the same functionality and should be
abandoned or moved out by the session that created it. Main's committed
history is not affected.

## Authoritative state

| Item | Value |
|---|---|
| Branch | `claude/qp-ker015-expiry-hardening` |
| Worktree | `C:\qp-ker015-expiry-20260715` |
| Base | `76ee0e2` (= main before the two later docs commits) |
| Implementation commit | `949aa7d650fa70003a9ffb329b8f04844cca4d30` |
| Audit P1-closure commit | `31ac3a11324b92fa634b29c404054176513d9446` |
| Gate 010 | already integrated to main: docs merge `a9a15d3`, runtime merge `fed4ed6`, status docs `76ee0e2`, claim `4d3146f`, review `893af91` |

Owned paths (the two commits touch exactly these):

```text
quantpilot/packages/core/execution/paper_submission.py
quantpilot/packages/core/execution/state_machine.py
quantpilot/packages/core/harness_service.py
quantpilot/packages/db/sqlite_repositories.py
quantpilot/tests/unit/test_external_paper_harness_integration.py
quantpilot/tests/unit/test_paper_submission_coordinator.py
quantpilot/tests/unit/test_strategy_version_matching.py   (new)
```

## What the branch implements

1. **Legacy temporal fences** (`harness_service.py`): `submit_order_plan`
   rejects the current order's own expired `OrderPlan.expires_at`
   (`now == expires_at` blocks); pre-transition temporal fence plus a
   post-callback second fence over order/risk/quote/snapshot deadlines. The
   post-callback fence observes real elapsed time even with an injected
   `now=` baseline: `post_callback_time = submission_time +
   max(0, utc_now() - fence_reference_time)`.
2. **Version totality** (`state_machine.py` + new focused test file):
   `strategy_versions_match` catches the `isdigit()`-passes/`int()`-fails
   conversion (`ValueError -> False`), never raises, never falls back to
   string equality for such a pair. Corpus: `"²" vs "2"` mismatch without
   exception; `"²" vs "²"` mismatch, no string fallback; `"２" vs "2"` match;
   `"２.０" vs "2"` match; one non-numeric fallback pair.
3. **Durable expiry fences** (`paper_submission.py`, `sqlite_repositories.py`):
   - New prepares store `submission_evidence_expires_at =
     min(order, risk, quote, snapshot)` and persist `order_plan_payload`.
   - v11 reopen/cleanup strictly parses the aware payload `expires_at` and
     uses `min(existing deadline, payload expiry)`. Missing/naive/corrupt
     payload expiry is never guessed: prepared rows terminalize locally with
     one atomic release/event and POST=0; claimed/`outcome_unknown` rows
     become reconciliation-required with the reservation held.
   - Payload-free rows (`order_plan_payload is None`, legacy fixtures) keep
     the pre-015 stored-deadline fence — new rows fold order expiry into the
     stored minimum at prepare time, so no fence is weakened.
   - Claim rechecks `min(stored deadline, payload expiry)` inside the store
     transaction. Re-entry with a changed order expiry fails the durable
     identity check.
   - Foreign prepared rows are fence-rebound (takeover of dispatch AND
     reservation, `DispatchFenceRebound`/`RiskReservationFenceRebound`)
     **before** any kill/expiry terminal write, on both the sweep and the
     direct `submit_prepared_order` recovery path; a live predecessor fails
     the takeover loudly. Invalid-payload rows cannot be revalidated for
     takeover and keep their historical binding (documented exception).
   - The canonical event vocabulary stays closed: invalid durable expiry uses
     the accepted codes (`submission_evidence_expired` /
     `broker_history_window_manual_resolution_required`); no new reducer code
     was added because `reducer.py`/`events.py` are outside gate ownership.
4. **Binding corpus coverage**: deadline crossings (prepare=0/POST=0),
   `now == expires_at`, post-claim/pre-client expiry, prepared/restart
   terminalize+release-exactly-once, claimed/unknown reconciliation-only,
   changed-expiry re-entry rejection, callback-exception blocks before
   prepare, POST-exactly-once restart matrix (accepted/rejected/unknown), v11
   reopen fixture that rewrites row AND dispatch events consistently for
   deadline shape (payload-only divergence models the never-guess case),
   transaction-fault rollback.

## Verification evidence

- Codex read-only audit of `949aa7d`: P0=0/P1=2/P2=2. All other axes
  confirmed (path scope, no schema change, safe defaults, single POST site,
  no ambiguous re-POST, kill fences, dual-write coupling, closed vocabulary,
  v11 fixture legitimacy).
- Both P1s closed in `31ac3a1`:
  - P1-1 frozen post-callback clock -> elapsed-delta fence (regression:
    `test_explicit_now_does_not_freeze_post_callback_temporal_fence`).
  - P1-2 predecessor-fence release on the direct recovery path -> takeover
    before terminal writes (regression:
    `test_direct_recovery_submission_rebinds_before_expiry_terminalization`).
- The Codex re-audit job of `31ac3a1` crashed (runtime policy blocks + OOM),
  so an independent adversarial reviewer verified the closures instead, with
  discriminating counter-proof runs: both regression tests FAIL on `949aa7d`
  (via `git archive` scratch extraction) and pass on `31ac3a1`. Verdict:
  P1-1 CLOSED, P1-2 CLOSED, no new P0/P1 in the delta.
- Tests (junit-verified): full backend `tests=1327 failures=0 errors=0
  skipped=2`; focused three files `57/57`; smoke `broker=mock`,
  `live_trading_enabled=false`, operator `blocked`, submitted `[]`.
  Junit artifacts: `C:/Users/goyan/.claude/jobs/fcc7f5f6/tmp/ker015-full4.xml`
  and `.../indep-full.xml`.

## Known limits / follow-ups

- Residual pre-existing P2 (not introduced by this gate):
  `operator/service.py` captures `authorization_time` once and submits each
  proposal in a loop with `now=authorization_time`; wall time consumed by
  earlier iterations is invisible to the entry baseline. Recommended as a
  separate follow-up ticket.
- Pre-existing P3 (informational): the direct-path invalid-payload
  terminalization can terminalize a foreign prepared row while its
  predecessor is live (fail-closed, CAS-protected; mirrors the sweep's
  intentional historical-binding rule).
- The `durable_order_expiry_invalid` error code proposed in earlier test
  drafts was rejected to keep the Gate-2 reducer vocabulary closed; widening
  it would need a reviewed contract change.
- KIS Gate P remains manual; no live/network anything was touched.

## Requests to the Codex session

1. Abandon (or move to its own branch) the uncommitted
   `paper_submission.py` rewrite in this main working tree — it duplicates
   the accepted branch. The full draft diff is preserved at
   `C:/Users/goyan/.claude/jobs/fcc7f5f6/tmp/zombie-codex-ker015-draft-on-main.patch`
   if anything from it needs comparison.
2. Optionally run your own read-only re-audit of `31ac3a1` (diff scope:
   exactly `paper_submission.py`, `harness_service.py`, and the two focused
   test files) — the mission lead will treat any P0/P1 you find as blocking.
3. Signal when the main working tree is clean; the mission lead will then
   merge the branch into main, re-run full backend + smoke + `git diff
   --check` on the integrated tree, update the workboard/handoff statuses,
   and start `QP-KER-020` (default-off shadow runner).
