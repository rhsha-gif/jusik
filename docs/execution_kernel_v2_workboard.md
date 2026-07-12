# Execution Kernel v2 Workboard

## Document edit lease

- Lease status: `free`
- Document editor: `none` (last held by GPT-5 Codex for `QP-KER-000E` final
  static-purity hardening in the isolated
  `codex/qp-kernel-v2-final-hardening` branch; released for read-only audit)
- Mission/task ID: `QP-KERNEL-V2`
- Acquired at: `none`

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-KERNEL-V2` |
| Received by | Codex |
| Mission lead | Codex |
| Lead model/version | GPT-5 Codex desktop |
| Goal | Make Level 3, Level 4, Level 5, professional risk-reduction, and KIS paper use one typed execution-kernel handoff without adding broker authority or weakening the accepted reservation/event contracts. |
| In scope | Repository-grounded contract, pure decision model, default-off shadow runner, normalized parity, gated level-by-level cutover, KIS paper last, independent audit, completion report. |
| Out of scope | Live trading, real KIS automatic tests, market-order enablement, partial-batch redesign, outbox/worker, authoritative accounting ledger, continuous runtime, flatten, Postgres/Kafka, multi-account, multi-broker, UI changes. |
| Safety constraints | Safe defaults stay false/mock; fake clients only; shadow has no broker/store/repository/audit/clock/env authority; KIS order POST remains coordinator-only; ambiguous POST is never retried; schema-v10 reservation and schema-v11 dual-write/provenance/fencing remain unchanged. |
| Completion criteria | All staged gates and audits in this board are accepted, Level 3/4/5/professional/KIS use the common handoff, no dual POST path exists, full backend/smoke pass, P0/P1=0, original dirty main is untouched. |

## Counterpart plan review

- Reviewer/model: Claude Code CLI `2.1.205`, model alias `Fable 5`, session
  model ID `claude-fable-5` (no fuller dated build ID is exposed to the
  session; none is claimed).
- Review status: `QP-KER-000D final review committed`. The independent
  adversarial re-verification of the hardened contract against current source
  (state machine, harness submit ordering, lifecycle/registry binding,
  gatekeeper/batch risk, approval tickets, operator run binding, durable
  coordinator, KIS session composition, broker adapter) found P0=0, P1=2,
  P2=3; all five were closed inside the two owned documents. The earlier
  `QP-KER-000B` partial worktree remains preserved and untouched. Runtime was
  held for the required post-fix audits; `QP-KER-000D` was later superseded
  and only accepted `QP-KER-000E` integration may now unblock it.
- Claude `QP-KER-000D` findings (all closed in the contract):
  - P1: the recipe/registry version rule omitted the legacy
    `strategy_versions_match` non-numeric fallback (trimmed exact string
    equality, `state_machine.py`), so a contract-faithful kernel would block
    version pairs legacy accepts; the full rule and a fallback parity case are
    now binding.
  - P1: `normalized reason-code set` parity was undefined for stages where the
    legacy path fails fast with one exception (candidate and
    external-paper-input checks in `submit_order_plan()`) while the kernel
    reports every same-stage defect; a closed subset/equality normalization
    rule is now specified.
  - P2: KIS Level 3 kernel-vs-legacy blocked-stage difference (authorization
    `actor_assurance_missing` vs legacy explicit paper-input failure) is now
    explicitly excluded from every parity corpus including Gate 065.
  - P2: the "exact" AST rule item `module-scope List, Set, Dict` was ambiguous;
    now stated as module-scope `list()`/`set()`/`dict()` constructor calls.
  - P2: the contract-task verification diff base was stale for review
    branches; each review branch now diffs against its own accepted base.
- Post-review independent Codex purity audit of the committed `QP-KER-000D`
  artifact (`c74a491`): P0=0, P1=1, P2=1. Claude Code's follow-up commit
  `0bbec72` (`docs(kernel): close final purity audit gaps`) addressed both and
  self-assessed P0/P1/P2=0, subject to the required cross-check:
  - P1: the "exact" AST purity rule rejected only mutable
    literals/comprehensions and `list()`/`set()`/`dict()` constructor calls,
    so module-scope calls such as `sorted(...)`, `json.loads(...)`, or
    `model_json_schema()`/`model_dump()` could still create mutable module
    globals. The rule is now mechanically complete: a closed module-scope
    statement/expression allowlist forbids every module-scope call, a closed
    type-alias form is carved out explicitly, class bodies get their own
    closed form list, a recursive runtime module-global immutability scan
    backs the AST gate, and a regression fixture proving
    `CHECKS = sorted(...)` is rejected is binding. The import allowlist is
    unchanged and only immutable constants the pure implementation needs are
    permitted.
  - P2: legacy `strategy_versions_match` (`state_machine.py`) uses
    `str.isdigit()` then `int()`, so Unicode digit strings such as `"²"`
    (U+00B2) raise `ValueError`; the helper is not total. The contract now
    specifies the decision-complete total rule (conversion failure is a
    mismatch, never an exception, never a string-fallback acceptance), binds
    the gate ordering (legacy helper hardened fail-closed no later than Gate
    015, before any version parity), and requires a focused Unicode-digit
    regression case for kernel/legacy parity. Accepted versions are not
    broadened.
- Three independent audit axes of `0bbec72` withheld integration. The purity
  axis reported P0=0/P1=2/P2=1, the authorization/version axis P0=0/P1=0/P2=2,
  and the KIS/durable axis P0=0/P1=0/P2=2 (consolidated distinct findings:
  P0=0/P1=2/P2=4):
  - the runtime global scan rejected normal interpreter metadata and imported
    typing special forms;
  - function defaults/annotations/decorators plus class bases/keywords still
    allowed definition-time calls and hidden mutable values;
  - the Unicode corpus did not distinguish superscript fallback from accepted
    fullwidth numeric normalization;
  - Gate-015 owned paths/handoff omitted the version helper, audit evidence
    claimed final zero counts prematurely, and the existing Gate-070 flag P2
    needed an explicit readiness blocker.
- Claude Code began a third docs-only closure attempt but reached its session
  limit before completing or committing it. Its isolated worktree remains at
  `0bbec72` with one partially edited contract file and is preserved without
  reset, cleanup, or integration.
- `QP-KER-000E` is the Codex mission-lead closure of those findings. It
  forbids module-global type aliases, binds exact interpreter/import
  provenance handling, closes every function/class definition-time AST
  expression, adds valid/invalid checker fixtures, expands the Unicode
  version corpus, and fixes Gate-015 ownership/handoff. Final acceptance is
  pending three read-only audits with P0/P1=0.
- Claude cross-checks that found no defect: Level 4/5 authority check
  sequences match `authorize_level4/5()` exactly; registry status-to-earned-
  levels table matches `REGISTRY_STATUS_LEVELS`; lifecycle rank/minimum-status
  rules match `lifecycle_binding.py`; final-safety check order matches
  `current_submission_safety_failures()`; submit-time batch gate omits
  `partial_allow`; the current-order-expiry P1 reproduction is real
  (`risk_check_expires_at` is checked, `OrderPlan.expires_at` is not, and the
  batch helper re-appends the current order unconditionally); sole KIS POST is
  `place_limit_cash_order()` at exactly one coordinator call site;
  claimed/`outcome_unknown` dispatches can never re-POST; `PaperRunStatus` and
  session-status enums match; the dispatch model already enforces
  `submission_evidence_expires_at <= risk_check_expires_at`, so the Gate 015
  min-deadline needs no schema change; all §9 focused suites exist.
- Codex draft findings:
  - all submission paths already converge on
    `HarnessService.submit_order_plan()`;
  - approval evidence and failure translation remain level-specific;
  - the first kernel boundary must consume already-computed authorization and
    risk evidence because current risk/authority functions read environment
    state;
  - each stage-local shadow observation must run immediately before the
    specific legacy mutation or blocked return it compares, while the ready
    hook remains before the first submission mutation; and
  - external-paper shadow cannot call buying power or durable preparation.
- Required substantive counterpart role: independently inspect the current
  call graph and hardening revision, revise the binding contract/workboard,
  commit the two-document artifact in a fresh/clean Claude review branch, and
  close every P1 before `QP-KER-010` begins.

Three independent Codex read-only audits completed while Claude was capacity
limited:

- authorization/binding: P0=0, P1=6;
- purity/decision/parity: P0=0, P1=7, P2=2; and
- KIS/durable cutover: P0=0, P1=4, P2=1.

Overlapping findings are consolidated into `QP-KER-000C`; audit counts are not
added as though they were independent defects.

## Routing assessment

Score formula:
`domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`.

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `QP-KER-000A` repository draft | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Mission recipient with direct Gate 1/2 and repository integration context. |
| `QP-KER-000B` binding review | Claude Code `claude-fable-5` or available exact model | 5 | 4 | 5 | 3 | 5 | 4.55 | Gate 2 contract review was accepted first pass; cross-model review is mandatory and disjoint. |
| `QP-KER-010` pure model | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Cross-cutting contracts and stateful integration are the best evidenced class; the slice is pure and bounded. |
| `QP-KER-010` pure model | Claude Code candidate | 4 | 4 | 3 | 3 | 4 | 3.65 | Capable challenger but lower current repository continuity; assigned review rather than overlapping implementation. |
| `QP-KER-020+` integration | GPT-5 Codex lead | 5 | 5 | 5 | 5 | 4 | 4.90 | Existing scorecard strongly favors Codex for repository-wide stateful integration and release verification. |

## Work queue

Statuses: `proposed`, `ready`, `in_progress`, `review`, `integrated`, `done`,
`blocked`.

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-KER-000A` | GPT-5 Codex lead | Claude Code | Gate 2 | `주식트레이더-kernel-v2-contract` / `codex/qp-kernel-v2-contract` | `docs/execution_kernel_v2_contract.md`, `docs/execution_kernel_v2_workboard.md` | review | Repository inventory and decision-complete draft; no runtime edits; original main untouched. | draft `d3023a2` |
| `QP-KER-000B` | Claude Code exact model to be recorded | GPT-5 Codex lead | `QP-KER-000A` | `주식트레이더-claude-kernel-v2-review` / `claude/qp-kernel-v2-review` | the same two docs in an isolated review branch only | blocked | Preserve partial file; do not claim completion or integrate without a reviewed commit. | one uncommitted contract file; account reset `2026-07-13 00:30 KST` |
| `QP-KER-000C` | GPT-5 Codex lead | three read-only Codex audits | `QP-KER-000A`, Gate 2 mainline | `주식트레이더-kernel-v2-contract-hardening` / `codex/qp-kernel-v2-contract-hardening` | the same two docs only | done | Rebind to `70219d4`; close decision/auth/purity/durable/KIS findings; no runtime edits. | `2de0965`; auth P0/P1/P2=0, purity P0/P1/P2=0, KIS P0/P1=0/P2=1 |
| `QP-KER-000D` | Claude Code CLI 2.1.205 / `claude-fable-5` | GPT-5 Codex lead | committed `QP-KER-000C` | `주식트레이더-claude-kernel-v2-final-review` / `claude/qp-kernel-v2-final-review` | the same two docs only | done | Independent final review/revision and first purity closure committed; only two docs changed. | `c74a491`; follow-up `0bbec72`; three post-fix axes consolidated P0=0/P1=2/P2=4 and rejected finality; third attempt stopped at session limit with one preserved uncommitted file |
| `QP-KER-000E` | GPT-5 Codex lead | three independent read-only audits | `QP-KER-000D` | `주식트레이더-kernel-v2-final-hardening` / `codex/qp-kernel-v2-final-hardening` | the same two docs only | review | Close interpreter/import false positives and every definition-time/runtime escape; bind Gate-015/version and Gate-070 P2 tracking; exact two-doc allowlist; final P0/P1=0. | pre-commit audits: authorization/version P0/P1/P2=0; purity/decision P0/P1/P2=0; KIS/durable P0/P1=0/P2=1 (Gate-070 ADR only); commit pending |
| `QP-KER-010` | GPT-5 Codex lead | Claude Code + independent read-only audit | accepted `QP-KER-000E` | new `주식트레이더-kernel-v2-pure` / `codex/qp-kernel-v2-pure` | `quantpilot/packages/core/execution/kernel.py`, `quantpilot/tests/unit/test_execution_kernel_v2.py` | proposed | Strict deeply frozen pure model, import allowlist, closed decision order, deterministic fingerprint, zero side effects. | pending |
| `QP-KER-015` | GPT-5 Codex lead | independent safety audit | `QP-KER-010` | separate expiry-hardening worktree | `quantpilot/packages/core/harness_service.py`, `quantpilot/packages/core/execution/paper_submission.py`, `quantpilot/packages/core/execution/state_machine.py`, `quantpilot/packages/db/sqlite_repositories.py`, `quantpilot/jobs/run_kis_paper_session.py`, focused expiry/coordinator/session tests, and new `quantpilot/tests/unit/test_strategy_version_matching.py` only | proposed | Use existing v11 order payload (no migration), strict expiry parse/effective deadline/reopen test; both safety fences, preclaim/restart/pre-POST; no ambiguous retry/release; total fail-closed `strategy_versions_match` hardening with superscript/fullwidth Unicode regressions before any version parity. | pending |
| `QP-KER-020` | GPT-5 Codex lead | independent audit | `QP-KER-015` | new isolated worktree / `codex/qp-kernel-v2-shadow` | `quantpilot/packages/core/execution/kernel_shadow.py`, focused tests, minimal stage-local adapters/config boundary | proposed | `off` default; unknown mode fails closed; real blocked-path evidence prefixes; zero authoritative mutation; no second broker callable. | pending |
| `QP-KER-030` | best-fit test owner | independent auditor | `QP-KER-020` | separate parity worktree | kernel parity tests only | proposed | L1-2, mock/sim L3, L4 blocked/success, L5 blocked/success, dry-run zero-call; professional closed; all shadow counters zero. | pending |
| `QP-KER-035` | GPT-5 Codex lead | independent safety audit | `QP-KER-030` | separate facade worktree / `codex/qp-kernel-v2-facade` | `quantpilot/packages/core/execution/kernel_service.py`, focused facade tests, minimal dependency injection | proposed | One authoritative facade preserves snapshot/quote/binding/run/ATR/fence arguments, invokes the fence exactly once in legacy order, and reaches only the existing single submit boundary. | pending |
| `QP-KER-040` | GPT-5 Codex lead | Claude Code/read-only audit | `QP-KER-035` | separate Level 3 worktree | Level 3 adapters and tests | proposed | Direct/ticket use common handoff only for mock/simulated paper; caller label is not actor assurance; KIS L3 stays blocked until separately authenticated subject binding. | pending |
| `QP-KER-050` | GPT-5 Codex lead | independent audit | `QP-KER-040` | separate Level 4 worktree | Level 4 adapter and tests | proposed | Existing `authorize_level4()` evidence is adapted once; default disabled and kill behavior unchanged. | pending |
| `QP-KER-060A` | GPT-5 Codex lead | independent audit | `QP-KER-050` | separate Level 5 worktree | ordinary operator adapter and tests | proposed | Existing Level 5 fallback/reporting preserved; no LLM/RL authority; common handoff used. | pending |
| `QP-KER-060B` | GPT-5 Codex lead | independent audit | `QP-KER-060A` | separate professional worktree | professional protective/retirement adapter and tests | proposed | Full frozen position/quote/reserved-quantity evidence lets kernel recompute current reduce-only predicate; opaque fingerprint never authorizes; no exposure-increasing regression. | pending |
| `QP-KER-065` | best-fit test owner | Claude Code + safety audit | `QP-KER-060B` | separate fake-KIS rehearsal worktree | KIS composition adapter in shadow plus fake-only tests | proposed | Actual SQLite/coordinator composition; fake client; zero shadow mutation/client queries; expiry/restart/claim/unknown/kill/reconciliation corpus; P0/P1=0. | pending |
| `QP-KER-070` | GPT-5 Codex lead | Claude Code + safety audit | `QP-KER-065` plus reviewed flag/config ADR | separate KIS-paper worktree | KIS cutover adapter and fake-only tests | proposed | Before `ready`, close the exact reversible flag name and closed mode/profile/data/unknown-value matrix; default false; coordinator-only POST; reservation/events/provenance/fencing/no-rePOST unchanged; no real network. | pending; known nonblocking P2 for Gates 010/015 |
| `QP-KER-080` | mission lead | three independent audits where available | `QP-KER-070` | integration worktree | duplicate legacy orchestration only after proof | proposed | No alternate broker POST path; full parity/race/crash/restart/reconciliation suite; P0/P1=0; completion report. | pending |

Only one task per agent may be `in_progress`. No runtime task becomes
`in_progress` before accepted `QP-KER-000E` integration.

## Dependency graph

```text
main `70219d4` accepted
  -> QP-KER-000A Codex draft
  -> QP-KER-000B partial Claude review (preserved, not accepted)
  -> QP-KER-000C Codex audit hardening
  -> QP-KER-000D Claude final binding review
  -> QP-KER-000E Codex final static-purity closure
  -> QP-KER-010 pure model
  -> QP-KER-015 temporal/durable expiry hardening
  -> QP-KER-020 off/shadow runner
  -> QP-KER-030 exhaustive parity
  -> QP-KER-035 common authoritative facade
  -> QP-KER-040 Level 3 cutover
  -> QP-KER-050 Level 4 cutover
  -> QP-KER-060A Level 5 ordinary cutover
  -> QP-KER-060B professional risk-reduction cutover
  -> QP-KER-065 fake-KIS shadow rehearsal
  -> QP-KER-070 KIS paper last
  -> QP-KER-080 legacy removal/final audit
  -> QP-DURABLE-OUTBOX (next roadmap mission)
```

## Integration requests

- `QP-KER-000E -> QP-KER-010`: approve or revise the exact evidence fields,
  stage order, purity/import boundary, mismatch policy, and cutover sequence.
- `QP-KER-010 -> QP-KER-015`: expose only frozen value objects and a pure
  evaluator; do not introduce ports that can write.
- `QP-KER-015 -> QP-KER-020`: close every order/risk/quote/snapshot durable
  expiry TOCTOU path and accept the total fail-closed
  `strategy_versions_match` superscript/fullwidth corpus before parity can
  treat ready or version evidence as safe.
- `QP-KER-020 -> QP-KER-030`: return structured in-memory comparison evidence;
  do not persist a shadow result.
- `QP-KER-030 -> QP-KER-040+`: each level needs its own accepted parity corpus
  before authority switches.
- `QP-KER-030 -> QP-KER-035`: introduce one facade that preserves every
  existing submission argument and fence ordering; it may delegate only to the
  accepted legacy submit boundary and may not receive a broker client directly.
- `QP-KER-035 -> QP-KER-040+`: level adapters call the facade, not a new broker
  port; each cutover proves one facade call and at most one existing submit.
- `QP-KER-060B -> QP-KER-065`: rehearse actual KIS-paper composition with a
  fake client, real SQLite/coordinator, and zero shadow mutations.
- `QP-KER-065 -> QP-KER-070`: KIS cutover may compose only with the existing
  coordinator and store provenance; it cannot call the client directly. The
  exact default-false flag and closed configuration matrix must be accepted
  before Gate 070 becomes `ready`.
- `QP-KER-080 -> QP-DURABLE-OUTBOX`: preserve the accepted typed handoff and
  coordinator no-rePOST semantics when command dispatch becomes durable.

## Blockers and authority requests

- Claude Code committed `QP-KER-000D` as `c74a491` plus `0bbec72`. Its third
  static-purity follow-up hit the session limit before commit; that later
  one-file partial edit and the earlier `QP-KER-000B` partial worktree are
  both preserved and excluded. Runtime implementation waits on accepted
  `QP-KER-000E` integration and P0/P1=0 cross-checks.
- Real KIS paper validation requires user credentials and explicit manual
  authority. It remains Gate P and does not block fake-only contract/model work.
- Reproduced P1 pre-cutover finding: current Level 3 legacy submission can fill
  an order whose `OrderPlan.expires_at` is already past while its risk evidence
  remains fresh. The full fix includes both safety fences, durable deadline,
  preclaim, restart, and final pre-POST handling; one pre-submit check is not
  accepted.
- Nonblocking for Gates 010/015 but blocking Gate 070: choose the exact
  default-false KIS cutover flag name and close its composition matrix with
  kernel mode/profile/data mode and unknown values in a reviewed ADR.
- Main is clean except the user-owned untracked
  `CLAUDE.md.20260705.bak`. It is never read, edited, staged, committed, reset,
  or cleaned.

## Verification matrix

### Contract task

```powershell
git diff --check
git status --short
git diff --name-only <accepted-base>..HEAD
```

`<accepted-base>` is the accepted commit the branch started from (`1bb6be4` for
the `QP-KER-000C` hardening branch, `e5d91c9` for the `QP-KER-000D` review
branch, `c74a491` for the `QP-KER-000D` purity-audit closure follow-up on the
same branch, and `0bbec72` for `QP-KER-000E`). Only the two Kernel v2
documents may change in each contract/review branch.

### Pure-model task

```powershell
python -m pytest quantpilot/tests/unit/test_execution_kernel_v2.py `
  -p no:cacheprovider --basetemp=.pytest_tmp_kernel
python -m pytest quantpilot/tests `
  -p no:cacheprovider --basetemp=.pytest_tmp_kernel_full
python -m quantpilot.jobs.run_smoke
git diff --check
```

### Shadow and cutover tasks

In addition to the pure/full checks:

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

Required invariant evidence:

- mock smoke reports `live_trading_enabled=false` and Level 5 remains blocked
  under defaults;
- shadow broker/client/store/repository/audit counters are zero;
- KIS order POST static authority count is unchanged;
- `outcome_unknown`, restart, kill, and reconciliation never re-POST;
- schema-v10 reservation and schema-v11 event parity suites remain green; and
- no secret/account/raw payload appears in kernel evidence.

## Checkpoint log

- `2026-07-12 KST` — Gate 2 accepted at `8eaf15a`; full backend `1003 passed,
  2 skipped`; safe smoke; three final audits P0/P1/P2=0.
- `2026-07-12 KST` — Codex created clean
  `codex/qp-kernel-v2-contract` worktree at `8eaf15a`; original main remained
  untouched.
- `2026-07-12 KST` — Read-only inventory confirmed all current submission
  levels converge on `HarnessService.submit_order_plan()` and KIS POST remains
  coordinator-only. Level 4 lacks a current successful integration fixture,
  which must be added to the parity corpus.
- `2026-07-12 KST` — Claude background attempt `d2ac7d89` stopped at a project
  MCP approval prompt; safe-mode background `5e49f1b2` lost its control pipe;
  foreground Fable produced no file/output before the ten-minute timeout; the
  Sonnet retry returned the explicit `12:20 KST` session reset. All attempts
  left the Claude worktree clean.
- `2026-07-12 KST` — Codex drafted the decision-complete contract and this
  workboard as `d3023a2`. No runtime implementation started.
- `2026-07-12 KST` — Read-only feasibility review corrected two draft
  assumptions before counterpart review: mutable `OrderIntent` cannot be
  embedded in a mechanically frozen input, and the shadow hook can precede the
  first submission-phase mutation but not planning/approval/professional writes
  that already occurred before `submit_order_plan()`.
- `2026-07-12 KST` — Submit-time source inspection found that the current
  order's `OrderPlan.expires_at` is not explicitly rejected by
  `submit_order_plan()`. The contract records this as a required fail-closed
  hardening delta; parity may not conceal it.
- `2026-07-12 KST` — Direct Level 3 approval inspection found no approver input
  or persisted `approved_by`. The contract now records the baseline as
  `unverified_local`, non-external fixture evidence and requires real actor
  binding before Level 3 external-paper cutover.
- `2026-07-12 KST` — Contract-branch baseline verification after the read-only
  review passed: backend `1003 passed, 2 skipped in 22.09s`; smoke remained
  `broker=mock`, live=false, Level 5 blocked with zero submitted operator
  orders. The temporary pytest tree was removed and only the two contract docs
  remained modified.
- `2026-07-12 KST` — A read-only/in-memory Level 4 success probe showed the
  default strategy correctly blocks at `strategy_promotion_approved`; with an
  explicitly injected approved Level 4 recipe and open-window function, the
  same fixture submits and fills three mock orders with live=false. This is the
  required success-parity fixture and does not justify changing safe defaults.
- `2026-07-12 KST` — The current-order expiry gap was reproduced rather than
  inferred: a past-expiry, user-approved Level 3 mock order with a still-valid
  risk check became `filled` with one broker fill. Classified P1 for cutover;
  runtime fix remains held for counterpart contract review.
- `2026-07-12 09:00 KST` — Scheduled hidden process PID `71524` to start the
  required Claude Code Fable review at `12:21 KST` in
  `claude/qp-kernel-v2-review`. Standard output/error are redirected to
  temporary `qp-kernel-claude-review.*.log` files. The prompt restricts writes
  to the two contract docs and requires its own reviewed commit.
- `2026-07-12 KST` — Mainline integration advanced to `70219d4`: Drift and
  Gate 2 were accepted; backend `1046 passed, 2 skipped`; frontend `23 passed`
  plus build; smoke remained mock/live=false/operator blocked; OpenAPI stayed
  byte-exact. The user-owned backup remained untouched.
- `2026-07-12 KST` — Claude review produced a useful but incomplete one-file
  edit (current-order structural gap and unauthenticated ticket-label caveat),
  then hit the `2026-07-13 00:30 KST` account limit. No reviewed commit exists;
  its worktree remains preserved.
- `2026-07-12 KST` — Three read-only Codex audits found P0=0 and overlapping
  contract P1s in authorization binding, blocked-path parity, purity, deep
  immutability, decision precedence, professional reduce-only evidence,
  durable expiry/TOCTOU, KIS provenance, and sole-POST ownership.
- `2026-07-12 KST` — `QP-KER-000C` hardening began on a separate docs-only
  worktree. The contract was rebound to `70219d4`, KIS rehearsal Gate 065 and
  temporal Gate 015 were added, and all runtime work remained held for Claude
  final review.
- `2026-07-12 KST` — QP-KER-000C committed as `2de0965`. Final current-file
  audits: authorization P0/P1/P2=0; purity/decision P0/P1/P2=0; KIS/durable
  P0/P1=0 with one nonblocking Gate-070 flag-name P2. Only the two docs changed.
- `2026-07-13 KST` — Claude Code (CLI `2.1.205`, `claude-fable-5`) started
  `QP-KER-000D` on the fresh clean worktree
  `주식트레이더-claude-kernel-v2-final-review`, branch
  `claude/qp-kernel-v2-final-review` at `e5d91c9`, and acquired this document
  lease.
- `2026-07-13 KST` — Independent source re-verification of the hardened
  contract: authority check sequences, registry earned levels, lifecycle
  binding, harness submit ordering and both final-safety fences, the
  current-order-expiry P1 reproduction, batch gate `full_batch`-only submit,
  ticket state machine and caller-label-only approval, direct approval without
  `approved_by`, Level 5 single captured instant and `paper_run_id=run_id`
  binding, durable coordinator prepared/claimed/no-rePOST semantics, sole
  coordinator-only `place_limit_cash_order` POST authority, and KIS session
  composition all match the contract text. No P0 found.
- `2026-07-13 KST` — Two P1 contract defects closed in-document: the
  version-comparison rule now states the legacy non-numeric string fallback of
  `strategy_versions_match` with a binding parity case, and blocked-outcome
  parity normalization is now a closed subset/equality rule covering the
  legacy fail-fast candidate and paper-input stages. Three P2s closed: KIS
  Level 3 parity exclusion, AST-rule constructor-call wording, and per-branch
  contract-task diff base. Runtime work remains held; `QP-KER-010` was not
  started.
- `2026-07-13 KST` — A post-review independent Codex purity audit of the
  committed `QP-KER-000D` artifact at `c74a491` found P0=0, P1=1, P2=1: the
  AST purity rule still admitted mutable module globals created by
  module-scope calls (`sorted(...)`, `json.loads(...)`,
  `model_json_schema()`/`model_dump()`), and legacy `strategy_versions_match`
  is not total over Unicode digit strings (`isdigit()` passes, `int()` raises
  `ValueError`, verified against `state_machine.py:93-107`). The same Claude
  Code reviewer (`claude-fable-5`) closed both in the two owned documents:
  closed module-scope statement/expression policy with a full call ban, an
  explicit type-alias form, closed class-body forms, a recursive runtime
  module-global immutability scan, and a binding `CHECKS = sorted(...)`
  rejection fixture; plus the decision-complete total version rule,
  fail-closed conversion handling, Gate-015-bounded legacy hardening
  ordering, and a Unicode-digit parity regression. Claude's post-fix
  self-assessment was P0/P1/P2=0; the next checkpoint records why it was not
  accepted as final.
  Safe defaults, no-rePOST, KIS, auth, expiry, and stage-precedence contracts
  are unchanged; no runtime code was touched; `QP-KER-010` was not started.
  Committed as `0bbec72` (`docs(kernel): close final purity audit gaps`).
- `2026-07-13 KST` — Three required post-fix audit axes of `0bbec72` rejected
  final integration: purity P0=0/P1=2/P2=1; authorization/version
  P0=0/P1=0/P2=2; KIS/durable P0=0/P1=0/P2=2; consolidated distinct
  P0=0/P1=2/P2=4. Python-created module metadata and imported typing special
  forms made the runtime scan reject a valid module; open function/class
  definition headers allowed import-time calls such as
  `def f(cache=json.loads("[]"))`; the Unicode corpus, Gate-015 ownership,
  audit evidence, and Gate-070 P2 tracking were incomplete. Version totality
  remained fail-closed and KIS/no-rePOST contracts remained sound.
- `2026-07-13 KST` — Claude Code began a third two-doc correction but reached
  its `05:50 KST` session reset before completing it. The worktree remains at
  `0bbec72` with one uncommitted status-only contract edit and is not reset,
  cleaned, committed by Codex, or integrated.
- `2026-07-13 KST` — `QP-KER-000E` opened from clean `0bbec72` in
  `codex/qp-kernel-v2-final-hardening`. It replaced the open purity wording
  with exact import binding/provenance sets, forbidden module type aliases,
  closed function/class/type-expression grammars, runtime metadata shapes,
  and positive/negative checker fixtures. It also bound the complete Unicode
  version corpus, expanded Gate-015 owned paths/handoff, and kept the Gate-070
  flag/matrix P2 explicitly blocking only Gate 070. Runtime code remains
  untouched. Final pre-commit audit axes: authorization/version P0/P1/P2=0;
  purity/decision P0/P1/P2=0; KIS/durable P0/P1=0/P2=1, where the sole P2 is
  the deliberately deferred Gate-070 flag/config ADR and does not block Gates
  010/015. All three axes approve integration after the two-doc commit checks.

## Handoff record

```text
task_id: QP-KER-000A
agent_and_model: GPT-5 Codex desktop
commit: d3023a2
owned_paths:
  - docs/execution_kernel_v2_contract.md
  - docs/execution_kernel_v2_workboard.md
acceptance_met: repository-grounded draft complete; counterpart acceptance pending
exact_checks: two-file allowlist and final diff check passed; backend 1003 passed, 2 skipped; smoke mock/live=false/operator blocked
known_limits: Claude Code review unavailable until 12:20 KST; no runtime work authorized
integration_requests: Claude must revise/accept the binding contract before QP-KER-010
```

```text
task_id: QP-KER-000C
agent_and_model: GPT-5 Codex desktop with three independent read-only audits
commit: 2de0965
base: 1bb6be4 contract branch; governing repository main 70219d4
owned_paths:
  - docs/execution_kernel_v2_contract.md
  - docs/execution_kernel_v2_workboard.md
acceptance_met: yes; three audit axes P0/P1=0; two-doc allowlist and diff check pass
known_limits: Claude final acceptance waits for 2026-07-13 00:30 KST reset; no runtime work authorized
integration_requests: fresh Claude review must commit P0/P1=0 acceptance before QP-KER-010
```

```text
task_id: QP-KER-000D
agent_and_model: Claude Code CLI 2.1.205, model alias Fable 5, session model ID
  claude-fable-5 (no fuller dated build ID exposed; none claimed)
commit: c74a491 (`docs(kernel): complete Claude final contract review`)
base: e5d91c9 on claude/qp-kernel-v2-final-review; governing main 70219d4
owned_paths:
  - docs/execution_kernel_v2_contract.md
  - docs/execution_kernel_v2_workboard.md
acceptance_met: yes for the review artifact; independent adversarial source
  re-verification completed; P0=0; both P1 contract defects and all three P2s
  closed inside the two owned documents; post-fix contract P0/P1=0 pending the
  lead's cross-check
exact_checks: git diff --check (clean); git status --short (only the two owned
  docs modified pre-commit); git diff --name-only e5d91c9..HEAD (only the two
  owned docs)
known_limits: docs-only review; no test/smoke run was required or performed;
  the reproduced current-order-expiry P1 remains open in runtime until Gate
  015; KIS Gate P remains manual
integration_requests: Codex lead diff-reviews and integrates this commit,
  cross-checks P0/P1=0, then and only then starts QP-KER-010
```

```text
task_id: QP-KER-000D (purity-audit closure follow-up)
agent_and_model: Claude Code, session model ID claude-fable-5 (CLI build
  2.1.205 as recorded at QP-KER-000D start; no newer build ID was verifiable
  in this session and none is claimed)
commit: 0bbec72 (`docs(kernel): close final purity audit gaps`)
base: c74a491 on claude/qp-kernel-v2-final-review; governing main 70219d4
owned_paths:
  - docs/execution_kernel_v2_contract.md
  - docs/execution_kernel_v2_workboard.md
acceptance_met: no as a final contract; the two targeted findings were
  addressed and Claude self-assessed P0/P1/P2=0, but the three required
  post-fix axes consolidated P0=0/P1=2/P2=4 and withheld integration
exact_checks: git diff --check (clean); git status --short (only the two
  owned docs modified pre-commit); git diff --name-only c74a491..HEAD (only
  the two owned docs)
known_limits: docs-only closure; the strategy_versions_match runtime
  hardening itself lands at Gate 015 per the bound ordering; no test/smoke
  run was required or performed; KIS Gate P remains manual
integration_requests: superseded by the rejected second post-fix audit and
  QP-KER-000E; do not integrate 0bbec72 without the accepted Codex closure
```

```text
task_id: QP-KER-000E
agent_and_model: GPT-5 Codex desktop mission lead with three independent
  read-only audit axes
base: 0bbec72; governing main 70219d4
worktree_branch: 주식트레이더-kernel-v2-final-hardening /
  codex/qp-kernel-v2-final-hardening
owned_paths:
  - docs/execution_kernel_v2_contract.md
  - docs/execution_kernel_v2_workboard.md
acceptance_met: yes for contract content; authorization/version P0/P1/P2=0;
  purity/decision P0/P1/P2=0; KIS/durable P0/P1=0/P2=1 (Gate-070 ADR only);
  exact two-doc allowlist; commit evidence pending
exact_checks: pre-commit git diff --check and two-doc name allowlist passed;
  pending commit-time status and
  git diff --name-only 0bbec72..HEAD
known_limits: docs-only; Gate-015 runtime expiry/version hardening and manual
  KIS Gate P remain open; Gate-070 flag/config P2 blocks Gate 070 only
integration_requests: after P0/P1=0, mission lead integrates the full
  c74a491 -> 0bbec72 -> QP-KER-000E lineage into main before QP-KER-010
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-KER-000A` | inventory/contract draft | GPT-5 Codex desktop | rework required | 0 | 11 consolidated | 3 consolidated | 1 | two-doc allowlist + diff check | complete draft | pending |
| `QP-KER-000B` | independent binding review | Claude Code exact model pending | incomplete | 0 | not fully assessed | not fully assessed | 0 | reviewed two-doc commit | account reset pending | 0 |
| `QP-KER-000C` | contract hardening | GPT-5 Codex desktop + three read-only audits | accepted Codex gate | 0 | 0 | 1 | 3 | two-doc allowlist + diff check | complete | 4 |
| `QP-KER-000D` | final independent binding review | Claude Code CLI 2.1.205 / `claude-fable-5` | review artifact accepted; later purity closure superseded | 0 | 2 found, 2 closed | 3 found, 3 closed | 0 | reviewed two-doc commit + diff/status/name-only checks | complete artifact; lineage continues through `QP-KER-000E` | 4 |
| `QP-KER-000D` follow-up | purity-audit closure | Claude Code / `claude-fable-5` | closure committed, required cross-check rejected it | 0 | 1 initially targeted; 2 remained | 1 initially targeted; consolidated P2=4 remained | 0 | two-doc allowlist + three post-fix audit axes | rejected/superseded by `QP-KER-000E` | 2 |
| `QP-KER-000E` | final static-purity closure | GPT-5 Codex desktop | accepted content | 0 | 0 after closure | 1 deferred Gate-070-only | 1 | two-doc allowlist + three read-only audits | commit pending | pending |

- Routing decision quality: pending counterpart and implementation evidence.
- Capability scorecard update: append only after an accepted task artifact.
- User-owned changes preserved: original main status remains exactly outside
  this worktree and was not staged, copied, reset, or cleaned.
- Remaining limitations: all runtime/cutover gates and manual KIS Gate P remain
  pending.
