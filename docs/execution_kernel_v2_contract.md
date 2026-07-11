# Execution Kernel v2 Contract

Status: Codex repository-grounded draft; runtime implementation is held until
the required Claude Code counterpart review is committed and integrated.

## 1. Purpose and governing baseline

`QP-KERNEL-V2` turns the existing shared submission function into an explicit,
versioned execution contract without adding trading authority. The first slice
is a pure, read-only shadow evaluator. It does not submit, persist, reserve,
transition, reconcile, or repair anything.

This contract is subordinate to `AGENTS.md`,
`docs/agent_collaboration_protocol.md`, the accepted Gate 1 reservation
contract, and the accepted Gate 2 canonical-event contract. The accepted base
is commit `8eaf15a`.

The following invariants remain binding:

- `LIVE_TRADING_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `BROKER_MODE=mock`
- automatic tests use only fixtures and fake clients
- LLM/RL output cannot create, approve, or submit a broker order
- a claimed or otherwise ambiguous external POST is never automatically sent
  again
- schema-v10 reservations and authoritative dispatch rows remain authoritative
- schema-v11 execution events remain an append-only shadow journal
- account provenance, leases, and fencing remain mandatory for KIS paper
- kill, reconciliation, and risk-reducing work retain priority over new buys

## 2. Repository truth at the accepted baseline

### 2.1 Existing entry points

| Path | Entry point | Approval source | Common handoff |
|---|---|---|---|
| Level 1-2 fixture mock | `HarnessService.run_level_1_2_mock_execution()` | `_authorize_simulated_order_plan()` | `HarnessService.submit_order_plan()` |
| Level 3 direct | orders API: generate, approve, submit | `approve_order_plan()` / user approval | `HarnessService.submit_order_plan()` |
| Level 3 ticket | execution approval-ticket API | approved `TradeApprovalTicket` | `HarnessService.submit_order_plan()` |
| Level 4 | `HarnessService.run_guarded_autopilot_once()` | `authorize_level4()` | `HarnessService.submit_order_plan()` |
| Level 5 ordinary | `OperatorService.run_once()` -> `_submit_proposals()` | `authorize_level5()` | `HarnessService.submit_order_plan()` |
| Level 5 protective/retirement | professional cycle | Level 5 plus position-binding evidence | `HarnessService.submit_order_plan()` |
| KIS paper session | `run_kis_paper_session.build_runtime()` / `execute_runtime()` | Level 5/professional paths | the same harness handoff plus durable coordinator |

The submission path is already physically shared. What is not yet shared is a
typed authorization handoff and a separately testable execution-decision
contract. Level-specific services construct approval evidence, mutate the
`OrderPlan` to `user_approved`, and translate failures independently.

### 2.2 Current common submission responsibilities

`HarnessService.submit_order_plan()` currently performs all of the following:

1. loads in-memory order and policy state;
2. checks required explicit paper inputs;
3. checks order status and risk-check freshness;
4. performs a fresh single-order risk check;
5. performs submit-time batch risk evaluation;
6. captures final policy, kill, live, pause, and broker-health checks;
7. transitions the in-memory order to `submitted`;
8. for external paper, prepares the schema-v10 reservation and dispatch
   atomically;
9. calls exactly one broker adapter;
10. validates broker-order and fill evidence; and
11. updates in-memory projections and audit records.

Proposal-time and submit-time risk checks are deliberately both retained. The
second check is a time-of-check/time-of-use defence, not accidental duplicate
code.

### 2.3 Sole external-order authority

For KIS paper, the only normal order POST is:

```text
HarnessService.submit_order_plan
  -> DurablePaperSubmissionCoordinator.prepare_order
  -> KisPaperBrokerAdapter.submit_order
  -> DurablePaperSubmissionCoordinator.submit_prepared_order
  -> KisPaperClient.place_limit_cash_order
```

`DurablePaperSubmissionCoordinator` is the sole KIS order-POST authority. Its
prepared/claimed/outcome journal, reservation transaction, replay-without-POST,
and ambiguous-outcome rules cannot be copied into Kernel v2. Cancel authority
belongs to the existing paper-kill path and is outside this mission.

`MockBroker` and the non-external `PaperBroker` are deterministic simulators.
They remain side-effect owners for their current paths until their individual
cutover gates are accepted.

### 2.4 Important existing limitations

- `ExecutionKernel`, `AuthorizationDecision`, `ExecutionContext`, and a broker
  capability model do not yet exist.
- `AuthorityCheckResult` is the current Level 4/5 check result but does not by
  itself bind a human actor or approval ticket.
- The direct Level 3 approval endpoint currently accepts no approver identity
  and `approve_order_plan()` does not set `OrderPlan.approved_by`. It proves a
  local approval transition and audit source, not an authenticated human
  signature.
- `run_risk_check()`, `authorize_level4()`, and `authorize_level5()` read
  environment-derived feature state. They therefore cannot be called from the
  pure kernel unless those reads are first moved behind an adapter.
- schema-v10 reservation is implemented only for external KIS paper. A shadow
  result must not claim that mock or simulated-paper paths have a durable
  reservation.
- schema-v11 execution events shadow external paper dispatch/reservation/cancel
  aggregates. They are not a general OMS ledger.
- `partial_allow` is still a boolean at several API and risk boundaries. Its
  portfolio semantics are a later, separate change.
- The current `submit_order_plan()` checks `risk_check_expires_at` but does not
  explicitly reject the current order's own `OrderPlan.expires_at`. The helper
  that excludes expired pre-submission orders from guardrail calculations
  excludes the current order during submit-time evaluation. This is a known
  fail-closed hardening delta, not parity evidence to normalize away.

## 3. Bounded mission outcome

The first accepted runtime outcome is:

> Given a complete immutable evidence bundle, Kernel v2 returns the same
> allow/block execution intent deterministically, while being mechanically
> incapable of observing or changing external state.

The first slice does **not**:

- replace `HarnessService.submit_order_plan()`;
- change any Level 1-5 authorization decision;
- create or submit a broker order;
- call a broker, KIS client, coordinator, repository, audit recorder, event
  store, clock, environment reader, or random/ID generator;
- prepare, claim, release, or consume a risk reservation;
- write canonical events or repair authoritative rows;
- add a transactional outbox, worker, accounting ledger, or continuous loop;
- redesign `partial_allow`;
- add live, market-order, flatten, multi-account, or multi-broker capability;
- change API or frontend contracts; or
- validate real KIS behavior.

## 4. Pure domain boundary

### 4.1 Module boundary

The first implementation owns only:

```text
quantpilot/packages/core/execution/kernel.py
quantpilot/tests/unit/test_execution_kernel_v2.py
```

The production module may import immutable core schemas and Python standard
library value types. It must not import from:

```text
quantpilot.packages.brokers
quantpilot.packages.db
quantpilot.packages.core.kis_paper
quantpilot.packages.core.execution.paper_submission
quantpilot.packages.core.execution.paper_kill
quantpilot.packages.core.execution.paper_reconciliation
quantpilot.services
quantpilot.jobs
```

It also must not import `os`, `time`, `random`, `uuid`, or a wall-clock helper.
An AST/import-boundary test enforces this list.

### 4.2 Versioned input

`KernelEvaluationInputV1` is a deeply immutable value object. It contains
primitive frozen projections of current authoritative objects; it is not a new
persisted order schema. It must not embed a mutable `HarnessModel` instance.

Required fields:

```text
schema_version = 1
candidate: KernelOrderCandidateV1
authorization: AuthorizationEvidenceV1
risk: RiskEvidenceV1
context: ExecutionContextSnapshotV1
broker: BrokerCapabilitySnapshotV1
evaluated_at: aware datetime
```

Every nested value uses `ConfigDict(frozen=True, extra="forbid")` or an
equivalent frozen standard-library value type, is copied at the adapter
boundary, and is JSON serializable with a canonical ordering.

#### `KernelOrderCandidateV1`

This is a read-only projection of an existing `OrderPlan`. The mutable
`OrderIntent` model is converted to `KernelIntentSnapshotV1` rather than
embedded directly:

```text
order_plan_id
intent: KernelIntentSnapshotV1
policy_id
policy_version
purpose
status
idempotency_key
risk_check_id
risk_check_expires_at
approved_by
order_expires_at
```

`KernelIntentSnapshotV1` contains the existing intent ID, normalized symbol,
side, order type, quantity, limit price, notional, target weight, reason, and
aware quote time as scalar values. Numeric source values are converted with
`Decimal(str(value))`, must be finite, and serialize as canonical decimal
strings; the evaluator does not perform portfolio arithmetic on them. It must
retain the existing IDs rather than generate new ones. The adapter validates
that the intent, policy identity, and idempotency key came from one `OrderPlan`
snapshot.

#### `AuthorizationEvidenceV1`

```text
kind:
  simulated_level_1_2
  human_direct_level_3
  human_ticket_level_3
  guarded_level_4
  automated_level_5
  professional_risk_reduction
authorized: bool
source
policy_id
policy_version
actor_id: optional string
approval_reference: optional ticket/run/claim reference
assurance:
  simulated
  unverified_local
  ticket_attested
  policy_authorized
  operator_authorized
evaluated_at: aware datetime
checks: ordered immutable list of {name, passed, detail_code}
first_failed_check: optional string
```

Level adapters bind current evidence as follows:

- Level 1-2: simulated authorization marker and mock-only policy evidence;
- Level 3 direct: approved order state plus `unverified_local` assurance at the
  current baseline. An actor may be included only when a caller actually
  supplies and persists it; policy user ID alone is not treated as
  authentication;
- Level 3 ticket: immutable ticket ID, approved actor, ticket data mode, and
  ticket/order/policy identity with `ticket_attested` assurance;
- Level 4: the complete `AuthorityCheckResult` plus the policy authority
  identity;
- Level 5 ordinary: the complete `AuthorityCheckResult`, operator run ID, and
  registry/recipe identity evidence;
- professional risk reduction: Level 5 evidence plus position binding,
  purpose, and reduce-only verification reference.

The kernel does not perform authentication or call `authorize_level4()` /
`authorize_level5()`. It verifies internal consistency of supplied evidence.
`unverified_local` is accepted only for fixture/mock shadow evidence. It cannot
qualify external paper, a later live candidate, or a Level 3 cutover without a
separately reviewed identity-binding change.

#### `RiskEvidenceV1`

```text
single_order:
  risk_check_id
  passed
  policy_version
  idempotency_key
  created_at
  expires_at
  passed_checks
  failed_checks
batch:
  passed
  mode
  policy_version
  accepted_order_plan_ids
  failed_checks
snapshot_id
snapshot_captured_at
guardrail_fingerprint
reservation_state: none | required_not_prepared | prepared_authoritatively
```

For the first shadow slice, `reservation_state` is `none` for mock/simulated
paper and `required_not_prepared` for external KIS paper. Shadow construction
must occur before `prepare_order()`. `prepared_authoritatively` is reserved for
later read-only observation of an already completed authoritative transaction;
the kernel cannot create it.

The legacy adapter supplies already-computed risk results from the same
submit-time evidence. The kernel does not recalculate portfolio arithmetic and
does not generate a replacement risk-check ID.

#### `ExecutionContextSnapshotV1`

All values are captured outside the kernel:

```text
data_mode
run_mode
live_trading_enabled
guarded_autopilot_enabled
fully_automated_operator_enabled
market_orders_enabled
policy_kill_switch_engaged
operator_kill_switch_engaged
autopilot_paused
broker_healthy
external_paper_enabled
paper_run_id_present
explicit_snapshot_present
explicit_quote_present
account_scope_fingerprint: optional opaque value
session_id: optional opaque value
fencing_token: optional integer
```

`data_mode` uses the project-wide closed deployment values from `AGENTS.md`.
`run_mode` is one of:

```text
level_1_2_mock
level_3_direct
level_3_ticket
guarded_level_4
operator_dry_run
operator_mock_submit
operator_paper_submit
professional_risk_reduction
```

No caller may place an arbitrary route or endpoint string in the evidence.

Secrets, account IDs, tokens, request headers, and raw KIS payloads are
forbidden.

#### `BrokerCapabilitySnapshotV1`

This is a caller-supplied capability description, not a live query:

```text
broker_mode: mock | paper
environment: mock | simulated_paper | kis_paper
supports_limit_orders
supports_market_orders
requires_durable_prepare
requires_atomic_reservation
requires_account_provenance
```

The first slice accepts no live environment and no unknown capability value.

### 4.3 Versioned output

`KernelDecisionV1` is frozen and contains no callable command:

```text
schema_version = 1
order_plan_id
verdict: eligible_for_legacy_submit | blocked
blocked_stage: authorization | risk | final_safety | capability | none
reason_codes: sorted unique immutable list
would_require_durable_prepare: bool
would_require_atomic_reservation: bool
intended_next_stage: none | legacy_submit_handoff
evaluated_at
evidence_fingerprint
```

`eligible_for_legacy_submit` means only that the immutable evidence bundle may
continue through the existing legacy handoff. It never means that an order was
prepared, submitted, accepted, or filled. In particular, it does not predict
the `before_broker_submit` callback or the second final-safety check that the
legacy path performs immediately before durable preparation/broker submission.

`evidence_fingerprint` is SHA-256 over a canonical JSON projection of all input
fields except no fields are omitted for nondeterminism: there must be no random
or implicit current value in the input. The fingerprint is diagnostic only and
must never become a broker idempotency key or event identity.

### 4.4 Deterministic decision order

The evaluator applies these closed stages in order:

1. schema, timezone, and identity consistency;
2. candidate state (`user_approved`) and non-expired order;
3. authorization evidence and policy binding;
4. single-order risk pass, policy/idempotency binding, and freshness;
5. batch risk pass and membership of the current order;
6. final safety snapshot (live off, kill off, not paused, healthy broker);
7. required explicit paper evidence and provenance fields;
8. broker capability compatibility; and
9. return `eligible_for_legacy_submit` with declarative requirements only.

All applicable failures within the first failing stage are returned as sorted
reason codes. Later stages are not evaluated after a failed stage. Unknown
enum values, missing required evidence, naive timestamps, fingerprint mismatch,
or inconsistent policy/order identities fail closed.

The v1 output reason vocabulary is closed:

```text
invalid_evidence_schema
naive_or_invalid_timestamp
order_identity_mismatch
policy_identity_mismatch
policy_version_mismatch
order_not_user_approved
order_expired
authorization_denied
authorization_evidence_mismatch
risk_evidence_missing
risk_check_mismatch
risk_check_expired
single_order_risk_failed
batch_risk_failed
batch_order_not_accepted
live_trading_enabled
policy_kill_switch_engaged
operator_kill_switch_engaged
autopilot_paused
broker_unhealthy
market_order_disabled
explicit_snapshot_missing
explicit_quote_missing
paper_run_id_missing
account_provenance_missing
paper_session_fence_missing
broker_capability_mismatch
```

Adapters map existing check names and exceptions to this vocabulary for parity;
arbitrary exception messages and human-readable authority details never become
kernel reason codes.

## 5. Mode and composition contract

The later shadow runner reads `EXECUTION_KERNEL_V2_MODE` at the composition
boundary and converts it to a closed mode enum:

```text
off       # default; kernel is not constructed or called
shadow    # pure evaluate + non-authoritative comparison only
```

There is no `cutover`, `enforce`, or `live` value in the first feature. An
unknown value fails application composition before any order work. Parsing the
configuration is outside `kernel.py`; the pure evaluator never reads the
environment. The missing-variable default is exactly `off`; no `.env.example`
edit is required for the first slice.

In `shadow` mode:

- legacy authorization, risk, transition, reservation, event, and broker paths
  remain authoritative;
- the kernel receives deep-copied evidence;
- the returned decision cannot call anything;
- no result is written to the repository, audit log, schema-v11 store, or
  broker;
- no KIS client method, including buying-power queries, may be called to build
  shadow input;
- no `prepare_order()`, reservation, claim, or dispatch mutation is permitted;
  and
- external-paper shadow tests use already captured fake evidence only.

The first pure-model task need not add mode parsing or integrate a runner. Mode
and runner are a separate audited task after the contract is accepted.

## 6. Shadow integration point and parity

### 6.1 Integration point

The first runner is attached only after the legacy path has produced immutable
authorization, fresh single-risk, batch-risk, and the first final-safety
snapshot. Within that same `submit_order_plan()` call, it runs before the first
submission-phase authoritative mutation:

```text
OrderStatus.submitted transition
DurablePaperSubmissionCoordinator.prepare_order
broker.submit_order
any repository, audit, reservation, dispatch, or event write caused by the
  submission phase after the evidence snapshot
```

Planning, approval, and professional checkpoint writes that occur before
`submit_order_plan()` are explicitly outside the initial shadow boundary. The
shadow evaluator itself still performs zero writes. After it returns, the
legacy path remains free to perform its existing submission mutations,
`before_broker_submit` callback, second TOCTOU safety snapshot, durable prepare,
and sole broker call.

Adding the runner must not add a second broker adapter or submission callable.
The runner constructor has no broker/store/repository/audit parameter.

Level-specific services retain their current error translation and fallback
behavior. The runner only consumes an adapter snapshot.

### 6.2 Normalized parity projection

Parity compares:

```text
order_plan_id
eligible-for-legacy-submit/block
first blocked stage
normalized reason-code set
durable-prepare requirement
atomic-reservation requirement
```

It does not compare:

- newly generated IDs;
- audit/event counts;
- wall-clock values not explicitly supplied;
- broker order IDs or fill IDs;
- mutable object identity;
- human-readable detail text; or
- eventual broker outcome.

Legacy parity evidence must be derived from the same captured inputs. It may
not be inferred from a later fill or terminal order state.

The known current-order expiry delta is handled explicitly: a kernel
`order_expired` block versus a legacy allow is a real mismatch. `QP-KER-020`
must add a separately reviewed legacy fail-closed expiry check (before the
`submitted` transition) or otherwise resolve the contract with stronger
repository evidence. Tests may not delete the kernel check, suppress the
mismatch, or add it to an ignore list merely to obtain parity.

### 6.3 Mismatch policy

- Unit/integration parity tests fail on any mismatch.
- The initial production `off` default makes mismatches impossible in ordinary
  operation.
- Enabling `shadow` is allowed only in fake/mock development profiles covered
  by the parity tests.
- A shadow mismatch cannot authorize, submit, retry, repair, release capacity,
  or change the legacy verdict.
- No mismatch is silently counted as parity. The runner returns a structured
  comparison to its caller; a later observability task may choose a
  non-authoritative sink under a separate contract.
- KIS paper shadow remains disabled until fake-only parity and a separate
  KIS-last cutover review are accepted.

## 7. Required first-slice tests

`quantpilot/tests/unit/test_execution_kernel_v2.py` must prove:

1. two evaluations of identical input are exactly equal;
2. input objects are unchanged after evaluation;
3. output contains no callable and cannot perform a command;
4. the module has no forbidden imports or implicit clock/env/ID source;
5. naive time, mismatched policy, stale risk, failed authorization, failed
   single risk, failed batch, absent batch membership, kill, live, pause,
   unhealthy broker, unsupported order type, and missing paper provenance all
   fail closed at the correct stage;
6. a valid mock Level 3 bundle returns `eligible_for_legacy_submit`;
7. valid Level 4/5 evidence can be represented without the kernel calling the
   authorization functions;
8. external KIS evidence returns only declarative durable requirements;
9. fake broker/client/store/repository/audit sentinels have zero calls; and
10. canonical fingerprinting is stable across mapping insertion order and
    rejects secret-shaped fields.

The shadow-runner task additionally proves:

- mode defaults to `off` and rejects unknown values;
- off mode constructs no evaluator;
- shadow execution occurs before the first submission-phase authoritative
  mutation after its evidence snapshot;
- repository, audit, event-store, coordinator, broker, and fake-client
  snapshots/counters are identical before and after shadow-only evaluation;
- Level 1-2 mock, Level 3 direct, Level 3 ticket, Level 4 blocked/success,
  Level 5 dry-run/blocked/success, risk-reducing, and KIS fake evidence produce
  the expected normalized parity;
- `outcome_unknown`, restart, kill, and reconciliation tests retain their
  existing no-rePOST counters; and
- the static sole-POST-authority regression remains green.

## 8. Gate sequence

| Gate | Outcome | Authority change |
|---|---|---|
| `QP-KER-000` | accepted contract, workboard, and counterpart review | none |
| `QP-KER-010` | pure frozen model/evaluator and unit tests | none |
| `QP-KER-020` | default-off mock shadow runner and normalized comparison | none |
| `QP-KER-030` | exhaustive Level 1-5 mock/simulated-paper parity corpus | none |
| `QP-KER-040` | Level 3 direct/ticket handoff cutover | only after separate audit; no new broker |
| `QP-KER-050` | Level 4 handoff cutover | existing guarded authority only |
| `QP-KER-060` | Level 5 ordinary and professional handoff cutover | existing Level 5 authority only |
| `QP-KER-070` | KIS paper cutover through the unchanged durable coordinator | existing paper authority only |
| `QP-KER-080` | remove proven duplicate legacy orchestration | no authority change |

Every cutover is separately reversible and starts disabled. KIS is last.
`partial_allow`, outbox/single-writer, ledger, and continuous runtime remain
subsequent roadmap missions.

## 9. Gate acceptance and rollback

Minimum checks for every implementation commit:

```powershell
python -m pytest quantpilot/tests/unit/test_execution_kernel_v2.py -p no:cacheprovider --basetemp=.pytest_tmp_kernel
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp_kernel_full
python -m quantpilot.jobs.run_smoke
git diff --check
```

Relevant existing focused suites must also remain green:

```powershell
python -m pytest `
  quantpilot/tests/integration/test_level3_flow.py `
  quantpilot/tests/integration/test_level4_guarded_flow.py `
  quantpilot/tests/integration/test_level5_operator_run_once.py `
  quantpilot/tests/unit/test_approval_tickets.py `
  quantpilot/tests/unit/test_harness_batch_risk.py `
  quantpilot/tests/unit/test_external_paper_harness_integration.py `
  quantpilot/tests/unit/test_paper_submission_coordinator.py `
  quantpilot/tests/unit/test_paper_execution_shadow_parity.py `
  -p no:cacheprovider --basetemp=.pytest_tmp_kernel_focused
```

No frontend verification is required unless a later task explicitly changes
the frontend contract.

Rollback for the pure and shadow slices is deletion of the new module/tests and
leaving mode `off`. No database migration, event rewrite, reservation change,
or broker reconciliation is needed. A cutover gate cannot claim rollback by
changing historical authoritative rows; it must switch the relevant adapter
back to the accepted legacy handoff and re-run the full parity suite.

## 10. Completion conditions for Kernel v2

The whole `QP-KERNEL-V2` gate is complete only when:

- Level 3 direct/ticket, Level 4, Level 5 ordinary/professional, and KIS paper
  all pass through one typed kernel handoff;
- level differences are confined to authorization-evidence adapters and
  caller-specific reporting/fallback translation;
- no alternate broker POST path exists;
- KIS paper still uses the same coordinator reservation/claim/no-rePOST
  protocol;
- all canonical events remain atomically paired with authoritative paper
  mutations;
- parity, race, crash, restart, kill, and reconciliation tests pass;
- default safe flags and mock broker remain unchanged;
- independent review reports P0=0 and P1=0; and
- real KIS validation is still correctly reported as manual Gate P evidence,
  not implied by fake-only tests.
