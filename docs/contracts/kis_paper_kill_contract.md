# KIS Paper Cancel-All Kill v1 Contract

This contract binds the paper-only kill implementation. `MUST` and `MUST NOT`
are safety requirements. The command cancels only KIS paper orders attributable
to an exact durable `PaperOrderDispatch`; it does not flatten positions or cancel
manual/external orders.

## Safety boundary

- The command MUST require `data_mode=paper_trading`, `broker_environment=kis_paper`,
  the exact approved paper origin, matching store/client/account fingerprints,
  `LIVE_TRADING_ENABLED=false`, `KIS_PAPER_KILL_ENABLED=true`, and the exact
  confirmation phrase. Secrets and account identifiers MUST NOT be persisted or
  printed.
- Engage MUST acquire the existing execution-session lease/fence. It MUST NOT
  steal an unexpired lease. `KILLING`, `KILLED`, and `RECOVERY_REQUIRED` MUST
  block a normal paper session both at startup and immediately before dispatch.
- `prepared` order dispatches MUST be terminalized locally without a broker POST.
  `dispatch_claimed` and `outcome_unknown` dispatches MUST be reconciled before
  cancellation eligibility is considered.
- Only `accepted` or `partially_filled` dispatches with one exact broker identity
  and positive broker-reported cancelable quantity are eligible. The identity
  MUST agree across the durable dispatch, daily-order evidence, and cancelable-
  order inquiry (business date, order number, branch/organization identity,
  symbol, side, original quantity, price, and remaining/cancelable quantity).
  Missing, conflicting, duplicate, regressed, or ambiguous evidence MUST fail
  closed without a cancel POST.

## Durable state machines

Engage MUST commit a kill operation before broker inspection or mutation.
Repeated CLI invocations resume the latest blocking operation; they MUST NOT
create parallel operations for the same store/account fence.

| Current kill state | Allowed next state | Meaning |
|---|---|---|
| none or `RELEASED` | `KILLING` | New kill fence is durable; new paper submission is blocked. |
| `KILLING` | `KILLED` | A fresh verification pass proves no unresolved managed or external working order. |
| `KILLING` | `RECOVERY_REQUIRED` | Any ambiguity, query failure, external order, or unresolved target remains. |
| `RECOVERY_REQUIRED` | `KILLING` | Explicit engage resumes query/reconciliation only; claimed cancels are never reposted. |
| `KILLED` | `RELEASED` | Explicit release succeeds after a fresh proof described below. |

No other transition is valid. In particular, `RECOVERY_REQUIRED` MUST NOT go
directly to `KILLED` or `RELEASED`, and release MUST NOT re-arm strategy or
autonomy flags.

Each managed target has one durable cancel request keyed uniquely by the
original order dispatch/broker identity. Its state transitions are:

```text
prepared -> cancel_claimed -> cancel_accepted
                          \-> cancel_outcome_unknown
                          \-> rejected

prepared | cancel_accepted | cancel_outcome_unknown | rejected
    -> reconciled_cancelled | reconciled_filled
```

- `prepared` means no cancel POST was allocated. It may be claimed only after a
  fresh exact cancelable-order inquiry.
- The `cancel_claimed` compare-and-swap and `attempt_count=1` MUST commit before
  the sole POST. There is no transition back to `prepared` and no second claim.
- A crash, timeout, transport error, malformed response, response/identity
  mismatch, or persistence failure after the claim MUST result in (or recover
  as) `cancel_outcome_unknown`. Recovery is query-only even when the process may
  have crashed before sending bytes.
- An explicit KIS business rejection may record `rejected`, but does not prove
  the original order terminal. Only daily-order evidence may move any nonterminal
  cancel state to `reconciled_cancelled` or `reconciled_filled`.
- Partial fills observed during cancellation MUST add only the monotonic fill
  delta through the existing reconciliation path. A row with positive remaining
  quantity is unresolved regardless of cancel acknowledgment.

## Reconciliation, external orders, and release

Every engage pass MUST perform read-only order reconciliation before selecting
cancel targets, then inquire cancelable orders, issue eligible one-attempt
cancels, and finally re-query daily orders and cancelable orders. POST responses
are evidence of request handling, never the authoritative terminal outcome.

A broker working order is external/manual when it has no unique exact match to a
managed durable dispatch. Such an order MUST NOT be cancelled, adopted, or
silently ignored. Duplicate mappings and identity collisions are treated the
same way. The kill operation MUST become `RECOVERY_REQUIRED` and report only a
safe reason code/count.

`KILLED` requires one fresh, internally consistent final query pass proving all
of the following: every managed post-dispatch order is `filled`, `cancelled`, or
definitively `rejected`; every cancel request is reconciled or its target is
otherwise daily-order-confirmed terminal; no managed or external/manual working
order remains; no dispatch/cancel reconciliation is pending or blocked; and no
query or history-window ambiguity exists.

Release MUST run the same proof again and succeeds only from `KILLED`. Any query
failure, new working order, external/manual order, unresolved dispatch/cancel,
identity mismatch, or stale/expired history proof MUST leave submission blocked
and move the operation to `RECOVERY_REQUIRED`. Successful release changes only
the kill state to `RELEASED`.

## Crash and replay contract

| Crash point | Required restart behavior |
|---|---|
| Before `KILLING` commit | No broker mutation occurred; a later engage starts normally. |
| After `KILLING`, before enumeration | Resume the same operation and reconcile first. |
| After cancel `prepared`, before claim | Revalidate exact broker evidence, then claim at most once. |
| After `cancel_claimed`, before/during/after POST | Mark/recover outcome unknown; query only, never POST again. |
| After accepted/rejected response, before durable update | Treat as outcome unknown; query only. |
| During fill/cancel reconciliation persistence | Replay idempotently; fill IDs and quantities MUST not duplicate or regress. |
| After `KILLED`, before CLI output | Replay the durable `KILLED` result without mutation. |
| During release proof | Remain blocked unless the proof and `RELEASED` commit both complete. |

## Adversarial executable test matrix

All automatic cases use a deterministic fake KIS client and no network or
credentials. Each test also asserts live trading remains disabled and the fake
transport's cancel POST count.

| Test | Setup/action | Required assertion |
|---|---|---|
| `test_kill_with_no_working_orders` | Empty durable/broker state | `KILLED`; zero POSTs. |
| `test_kill_expires_prepared_dispatch_locally` | One unattempted dispatch | Local terminal state; zero POSTs. |
| `test_kill_reconciles_unknown_before_cancel_selection` | Claimed/unknown order appears working | Exact identity learned first; one cancel claim and at most one POST. |
| `test_cancel_claim_crash_never_reposts` | Crash immediately after durable claim | Restart queries only; total POST count remains zero or one, never two. |
| `test_cancel_timeout_never_reposts` | POST times out, later broker row is working | `cancel_outcome_unknown`, `RECOVERY_REQUIRED`; exactly one POST across restarts. |
| `test_cancel_response_persistence_failure_never_reposts` | Broker accepts, local write fails | Query-only recovery; exactly one POST. |
| `test_cancel_confirmed_by_daily_order` | Accepted cancel later reports zero remainder/cancelled | `reconciled_cancelled`, then `KILLED`. |
| `test_fill_wins_cancel_race` | Order fills before/while cancel is processed | Fill delta applied once; `reconciled_filled`; no repost. |
| `test_partial_fill_then_cancel` | Fill increases and remainder is cancelled | Only new fill delta recorded; terminal cancelled target; one POST. |
| `test_cancel_ack_with_remaining_quantity_is_not_terminal` | Successful response but broker still reports remainder | `RECOVERY_REQUIRED`; release denied. |
| `test_business_rejection_requires_terminal_query_evidence` | Cancel rejected while target remains working | `RECOVERY_REQUIRED`; no second POST. |
| `test_external_working_order_is_never_cancelled` | Cancelable inquiry includes unmatched manual order | `RECOVERY_REQUIRED`; external order receives zero POSTs. |
| `test_identity_collision_or_mismatch_fails_closed` | Branch/org/order/quantity/price mismatch or duplicate match | Zero POSTs; safe blocked reason; release denied. |
| `test_history_window_or_query_failure_fails_closed` | Required evidence expired/unavailable/malformed | `RECOVERY_REQUIRED`; zero speculative POSTs. |
| `test_active_session_lease_cannot_be_stolen` | Unexpired normal session owns fence | Engage fails before broker access. |
| `test_blocking_kill_states_fence_new_submission` | Each of three blocking states at startup and pre-POST | Normal session/order POST is rejected at both gates. |
| `test_release_rechecks_and_detects_new_order` | State is `KILLED`, new working order appears | Release denied; `RECOVERY_REQUIRED`; submission remains blocked. |
| `test_release_does_not_rearm_automation` | Valid release proof | Only state becomes `RELEASED`; strategy/autonomy flags unchanged. |
| `test_schema_v8_to_v9_preserves_state` | Open a representative v8 database | Dispatch, fill, provenance, lease/fence preserved; new tables empty and valid. |

The full acceptance run is `python -m pytest quantpilot/tests` followed by
`python -m quantpilot.jobs.run_smoke`. Real KIS integration remains skipped and
manual.
