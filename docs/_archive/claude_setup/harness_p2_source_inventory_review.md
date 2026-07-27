# Harness / P2 Source Inventory and Omission Audit (HP2REF-001 `AUDIT`)

Binding counterpart artifact for mission `HP2REF-001` (workboard:
`C:/Users/goyan/OneDrive/문서/코덱스/주식트레이더/.codex/worktrees/harness-p2-gpt-reference-doc/docs/harness_p2_gpt_reference_workboard.md`).
This document constrains the Codex `DOC` deliverable
(`docs/current_harness_and_p2_gpt_reference.md`): scope, status wording, source
manifest, and the overclaim list in §7 are review-blocking inputs, not
suggestions.

## 1. Auditor, snapshot, and method

| Field | Value |
|---|---|
| Task | `HP2REF-001/AUDIT` (counterpart source-contract audit) |
| Auditor | Claude Code, exact model `claude-fable-5` |
| Worktree / branch | `.claude/worktrees/harness-p2-gpt-reference-review` / `claude/harness-p2-gpt-reference-review` |
| Audit base HEAD | `4241dc46e11454b8bd4c915e4ae52ca32570e9ef` (= current `main`) |
| Date | 2026-07-17 KST |
| Method | Read-only over all runtime code, tests, contracts, workboards, and git history; commit inspection of `949aa7d`/`31ac3a1` via `git show`/`git cat-file` without checking out any other branch; read-only `git status`/`git diff --stat` of the user's main working tree. Only this file is created. |
| Safety | No network, broker, KIS, or credential access. All safety flags observed at defaults (`LIVE_TRADING_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock`, `DATA_MODE=fixture`). No runtime, workboard, or user file was modified. |

Every repository path referenced below was existence-checked at `4241dc4` in
this worktree on 2026-07-17; the two branch-only paths in §5.2 were
existence-checked with `git cat-file -e` against `31ac3a1`.

## 2. Decomposition review (required counterpart plan review)

The two-task decomposition (`AUDIT` → `DOC`), the routing scores, and the
Codex-lead/Claude-independent-audit split are sound and match protocol §1.4/§2.
Four binding findings on the plan itself:

1. **The DOC must pin exactly one committed snapshot per claim.** Three
   competing states exist right now (§5). A reference document that mixes them
   silently will misdescribe `paper_submission.py`, which differs across all
   three. Required: every code claim carries either "main `4241dc4`" or
   "branch `31ac3a1`, unmerged".
2. **"P2 구성요소" must be defined before first use.** In this repository "P2"
   collides with at least three vocabularies (§4). The DOC's mission charter
   uses the term without definition; that is itself an omission risk.
3. **Path-existence acceptance must be snapshot-scoped.** The workboard
   acceptance says "모든 경로가 존재한다"; at least one KER-015 path
   (`quantpilot/tests/unit/test_strategy_version_matching.py`) exists **only**
   on the unmerged branch, not on main. A naive existence check in the DOC
   worktree (based at `4241dc4`) will fail for branch paths, and a naive claim
   of existence will be false for main.
4. **The dirty main working tree is not a source.** The DOC worktree was
   correctly created clean at `4241dc46e...`, but the DOC must also not quote
   or describe the uncommitted `paper_submission.py` rewrite sitting in the
   user's main working tree (§5.3).

Verdict on decomposition: **accept with the four constraints above.**

## 3. Exact current harness boundaries (at main `4241dc4`)

"Harness" here means the safety-gated execution orchestration around
`HarnessService`, not the whole webapp. Boundaries verified in source:

| Boundary | Exact current state | Primary evidence (all exist at `4241dc4`) |
|---|---|---|
| Execution authority axis | Level 1-2 research/mock-execute, Level 3 approval, Level 4 guarded (17-step chain), Level 5 bounded operator (19-step chain, no default `validated_l5` strategy → default run blocks at `level5_flag_disabled`), professional KIS-paper CLI session | `quantpilot/packages/core/harness_service.py`, `quantpilot/packages/core/execution/state_machine.py`, `quantpilot/packages/core/operator/service.py`, `quantpilot/packages/core/operator/professional_cycle.py` |
| Broker boundary | `mock` / `paper` / `live_disabled` only; live path unimplemented. The **sole external order POST** is `DurablePaperSubmissionCoordinator` calling `place_limit_cash_order` at `quantpilot/packages/core/execution/paper_submission.py:604` (client defined in `quantpilot/packages/core/kis_paper.py`; broker adapter in `quantpilot/packages/brokers/kis_paper.py`). Ambiguous POST is never retried. | `paper_submission.py`, `quantpilot/packages/brokers/mock_broker.py`, `quantpilot/packages/brokers/paper_broker.py` |
| Data axis | Six-value `DataMode` enum; `realtime_market_data`/`paper_trading`/`live_trading` fail closed in the general provider factory; KIS-paper CLI accepts `local_historical` only. `AGENTS.md`'s eight-mode list (`live_trading_candidate`, `live_canary`, `live_scaled`) is a documented doc-code gap, not code. | `quantpilot/packages/core/schemas.py`, `quantpilot/packages/core/data/providers.py`, `quantpilot/packages/core/data/quality.py`, `docs/current_project_workflow.md` §3.2/§14 |
| Persistence | General API state is in-memory `RepositoryRegistry` (process-lifetime only); durable state is the opt-in SQLite `PaperStateStore`, schema **v11** (v10 risk reservations + v11 append-only shadow event journal; v10 rows remain authoritative) | `quantpilot/packages/db/repositories.py`, `quantpilot/packages/db/sqlite_repositories.py`, `quantpilot/packages/core/execution/events.py`, `quantpilot/packages/core/execution/reducer.py` |
| Kernel v2 | `quantpilot/packages/core/execution/kernel.py` (Gate 010) is a pure frozen evaluator with **zero runtime imports/call sites** — grep over `quantpilot/packages`, `quantpilot/services`, `quantpilot/jobs` finds no non-test import. It decides nothing in production yet; shadow runner (Gate 020) is open. | `kernel.py`, `quantpilot/tests/unit/test_execution_kernel_v2.py` |
| LLM/RL | No order create/approve/submit authority; RL contract limited to strategy selection / bounded target-weight change | `quantpilot/packages/core/rl/outputs.py` |
| Briefing | Read-only fixture boundary, not a signal input | `docs/current_project_workflow.md` §6.2 |
| Live trading | Intentionally unimplemented; 12-item human checklist at 0/12; `live_trading_candidate` ticket label blocks at `live_broker_unavailable` | `docs/STATUS.md` |

## 4. "P2" disambiguation — severity vs roadmap Gate 2 (binding for the DOC)

The DOC must state all of the following distinctions explicitly:

| Term | Meaning in this repository | Not to be confused with |
|---|---|---|
| **P0/P1/P2/P3** | Audit-finding severities under the collaboration protocol. P0/P1 block merge; **P2 is a non-blocking follow-up recommendation**; P3 is informational. | Roadmap stages, schema versions, autonomy levels |
| **Roadmap Gate 2** | Macro-roadmap stage 2: Canonical Order/Execution Events v1, schema v11 shadow journal. Status: **integrated into main** (2026-07-12, main lineage through `2d34275`). | The severity "P2". "Gate 2 done" says nothing about P2 findings, and open P2 findings do not reopen Gate 2. |
| **Gate P** | Manual, user-authorized real KIS paper verification (`VTTC0084R`, buying-power mapping, session calendar). Status: **open/manual, never automated**. | Both of the above. Every kill/reservation/events claim is fake-client development evidence only until Gate P. |
| **`QP-KER-0xx`** | Execution Kernel v2 sub-gates (contract ladder). E.g. `QP-KER-015` is a gate ID, not a severity count. | Severity labels. |
| Schema v9/v10/v11 | SQLite `PaperStateStore` migrations (kill journal / risk reservations / event journal). | Roadmap gate numbers. |

## 5. Snapshot precedence (binding)

### 5.1 Main `4241dc46e11454b8bd4c915e4ae52ca32570e9ef` — default authority

Everything integrated: schema v9/v10/v11, Kernel v2 contract
(`QP-KER-000A~000E`), Gate 010 pure kernel (merge `fed4ed6` lineage). All §3
boundary claims are made against this snapshot. The DOC describes **this**
tree unless explicitly labeled otherwise.

### 5.2 Branch `claude/qp-ker015-expiry-hardening` — accepted-unmerged `QP-KER-015`

| Item | Value (verified via git, no checkout) |
|---|---|
| Commits | `949aa7d650fa70003a9ffb329b8f04844cca4d30` (implementation) → `31ac3a11324b92fa634b29c404054176513d9446` (audit-P1 closure) |
| Base | `76ee0e2` (main before later docs commits); **not an ancestor of main** (`git merge-base --is-ancestor` fails) |
| Worktree | `C:/qp-ker015-expiry-20260715` |
| Changed paths | exactly 7: `quantpilot/packages/core/execution/paper_submission.py`, `.../execution/state_machine.py`, `.../core/harness_service.py`, `quantpilot/packages/db/sqlite_repositories.py`, `quantpilot/tests/unit/test_external_paper_harness_integration.py`, `.../test_paper_submission_coordinator.py`, `.../test_strategy_version_matching.py` (**new; branch-only**) |
| Content | Legacy temporal fences (own-order `expires_at`, pre/post-callback double fence), total fail-closed `strategy_versions_match` over Unicode digits, durable expiry min-deadline + never-guess reopen/cleanup, fence-rebind-before-terminal recovery |
| Status | Workboard row `QP-KER-015` = `review`; evidence field cites `949aa7d` only (predates `31ac3a1`). Commit-message evidence at `31ac3a1`: backend 1325 passed / 2 skipped, smoke safe. Independent **Codex** re-audit of `31ac3a1` is still pending (its first run crashed); integration to main has not happened. |

Precedence rule: authoritative **only** for describing what `QP-KER-015`
implements, always labeled "accepted-unmerged, on branch, awaiting independent
audit + integration". Never described as main behavior.

### 5.3 Dirty user changes in the main working tree — never a source

Read-only `git status` of the main worktree (2026-07-17) shows:

```text
 M quantpilot/packages/core/execution/paper_submission.py   (+224/-56)
?? .codex/worktrees/
?? .omo/
?? CLAUDE.md.20260705.bak
?? docs/qp_ker015_codex_handoff.md
```

- The modified `paper_submission.py` is an **uncommitted duplicate re-draft**
  of the KER-015 work by a separate session; the uncommitted handoff note
  `docs/qp_ker015_codex_handoff.md` (main worktree only; not in any commit)
  explicitly asks that it be abandoned or moved out. It differs from both the
  branch tip and main (387-line diff vs `31ac3a1`). Lowest precedence; the DOC
  must not quote, describe, stage, or "fix" it.
- `.omo/` and `CLAUDE.md.20260705.bak` remain read/clean/stage-prohibited per
  `docs/roadmap_continuation_handoff.md` §1.
- `docs/qp_ker015_codex_handoff.md` is factual and was used here as a lead,
  but it is **not repository history**; any DOC claim sourced from it must be
  re-grounded in commits or committed docs (as done in §6 below).

## 6. Open / residual P2 inventory (every currently open P2, with evidence)

Closed P2s are excluded from the open set but listed for anti-regression. All
evidence paths verified to exist at `4241dc4` unless marked otherwise.

| # | ID / area | Open P2 statement | Status | Evidence paths |
|---|---|---|---|---|
| 1 | Kernel Gate-070 flag/config ADR | Exact KIS-cutover flag name + closed composition matrix with `EXECUTION_KERNEL_V2_MODE`/profile/data-mode/unknown-value handling is an acknowledged non-blocking P2 for Gates 010/015; Gate 070 cannot go `ready` until closed in a reviewed contract/ADR (default false, fail closed). | **Open**; blocks only Gate 070 readiness | `docs/execution_kernel_v2_contract.md` §8 (≈ lines 1784–1788); `docs/STATUS.md` "Execution Kernel v2 계약 Gate" entry; `docs/execution_kernel_v2_workboard.md` `QP-KER-070` row |
| 2 | Gate 010 AST-digest operational constraint | On any Python AST schema/minor-version change the semantic digest must be explicitly re-derived and independently re-reviewed; automatic regeneration is forbidden. Standing operational P2, cannot be "fixed" once. | **Open (standing rule)** | `docs/roadmap_continuation_handoff.md` §2.1/§7; digest constant `REVIEWED_KERNEL_AST_SHA256` lives in `quantpilot/tests/unit/test_execution_kernel_v2.py` (**not** in `kernel.py` — the handoff §7 wording is imprecise) |
| 3 | `QP-RES-A2` (Gate 1 reservation guardrail) | Held sell-reservation guardrail projection in `HarnessService._guardrail_state` is not policy-scoped; safe (strictly over-blocking) only under the one-policy-per-paper-store operating assumption. | **Open**; non-blocking; direction strictly conservative | `docs/atomic_risk_reservation_v1_claude_audit.md` §4/§5; `quantpilot/packages/core/harness_service.py` (`_guardrail_state`, def ≈ line 704, reservation loop ≈ line 922); `docs/current_project_workflow.md` §14; `docs/roadmap_execution_workboard.md` (residual P2=1/P3=1 checkpoint) |
| 4 | `QP-KER-015` audit residual (operator clock baseline) | `operator/service.py` captures `authorization_time` once and submits every proposal with `now=authorization_time` (lines 781/799/827/880 at main), so wall-time consumed by earlier loop iterations is invisible to the entry baseline. Pre-existing, not introduced by the gate. | **Open**; recommended as a separate follow-up ticket; **not yet on any committed workboard** | `quantpilot/packages/core/operator/service.py:781-880`; `31ac3a1` commit message (audit of `949aa7d`: P0=0/P1=2/P2=2; both P1 closed); narrative detail only in uncommitted `docs/qp_ker015_codex_handoff.md` (main worktree) |
| 5 | `QP-KER-015` audit second P2 — identity not durably recorded | The `949aa7d` audit counted **two** P2s; committed history (the `31ac3a1` message) records the count but not both identities, and the uncommitted handoff names only one P2 (#4) plus one P3. | **Open as an evidence-traceability gap** | `31ac3a1` commit message; `docs/qp_ker015_codex_handoff.md` "Known limits" (uncommitted); `docs/execution_kernel_v2_workboard.md` `QP-KER-015` row (stale at `949aa7d`) |
| 6 | Handoff-review doc P2-1/2/3 | `QP-HO-020` review of `docs/roadmap_continuation_handoff.md` returned ACCEPT with P2=3 (all narrative/traceability quality: off-repo audit artifacts, KER-015 scope wording, integration-order phrasing). Remediation is Codex-owned narrative work. | **Open (docs-quality only)** | `docs/roadmap_continuation_handoff_claude_review.md` (Findings §, P2 1–3) |

**Closed — must not be listed as open:** `QP-RES-A1` (closed by `a892210`,
verified in audit §9); the Kernel contract-phase P2s from `QP-KER-000C/000D/
000E` and `QP-KER-010B` (all closed inside the contract/workboard; final
Gate 010 verdicts P0/P1/P2=0 except the deferred #1 above); Gate 2 review P2
clarifications (all six incorporated into the binding contract,
`docs/canonical_order_events_v1_workboard.md` / `..._claude_review.md` §10).
**Not P2:** `QP-RES-A3` and the KER-015 direct-path informational finding are
P3.

Gate-status note the DOC must carry: `QP-KER-015` itself is **not** an open
P2 — it is a gate in `review` whose audit P1s are closed on the branch and
whose independent Codex re-audit + mainline integration are outstanding.

## 7. Misleading claims the DOC must avoid (overclaim guard)

1. Do **not** say `QP-KER-015` is integrated/complete on main. Correct:
   implemented and P1-closed on `claude/qp-ker015-expiry-hardening`
   (`31ac3a1`), workboard status `review`, independent audit pending.
2. Do **not** cite `quantpilot/tests/unit/test_strategy_version_matching.py`
   as existing on main — branch-only until integration.
3. Do **not** imply `kernel.py` participates in any runtime order decision.
   It has zero non-test call sites; Gates 020–080 are open/locked.
4. Do **not** conflate P2 severity with roadmap Gate 2, Gate P, `QP-KER-0xx`
   IDs, or schema v10/v11 (§4 table is the required wording).
5. Do **not** call KIS paper kill v1 / atomic reservation v1 / canonical
   events v1 "operationally verified". All are fake-client development
   evidence; Gate P manual verification is open (0 items done) and the live
   checklist is 0/12.
6. Do **not** describe the dirty main-worktree `paper_submission.py` as
   current behavior, and do not present `docs/qp_ker015_codex_handoff.md` as
   committed history.
7. Do **not** treat `live_trading_candidate` (ticket label or lifecycle
   status) as live authority — it blocks at `live_broker_unavailable`, and
   lifecycle `live_candidate` is evidence maturity, not permission.
8. Do **not** claim the code supports the eight `AGENTS.md` data modes; the
   code enum has six, and three doc-only modes are a recorded contract gap.
9. Do **not** describe the briefing endpoint as a signal input (read-only
   fixture boundary), nor the UI banners as enforcement (backend gates are
   authoritative).
10. Do **not** quote a single global test count without a snapshot: main
    lineage last recorded 1289 passed/2 skipped (Gate 010 integration tree,
    `fed4ed6`); the KER-015 branch recorded 1323→1325 (commits) and 1327
    (junit in the uncommitted handoff). Counts are snapshot-bound; the DOC
    should either re-run on `4241dc4` or attribute each count to its commit.
11. Do **not** state that all three Gate 010 pre-integration Codex audits have
    in-repo artifacts — the handoff itself records they were off-repo;
    in-repo evidence is the handoff summary plus
    `docs/roadmap_continuation_handoff_claude_review.md`.
12. Do **not** present the `31ac3a1` P1-closure verification as a completed
    independent Codex audit — the Codex re-audit job crashed; closure was
    verified by a separate adversarial reviewer and recorded only in the
    uncommitted handoff. The committed workboard still awaits the audit.

## 8. GPT source attachment manifest and read order

All Tier 0–5 paths exist at `4241dc4` (existence-checked); Tier 6 items are
branch- or worktree-scoped and must be labeled as such if attached.

| Tier / order | Attach | Why first-to-last |
|---|---|---|
| 0. Orientation | `AGENTS.md`, `docs/STATUS.md`, `.env.example`, `README.md` | Safety invariants, current stage table, real default flags |
| 1. System map | `docs/current_project_workflow.md` | The one accurate end-to-end description of executing paths, fail-closed gates, and known doc-code gaps (§14) |
| 2. Binding contracts | `docs/execution_kernel_v2_contract.md`, `docs/contracts/atomic_risk_reservation_v1.md`, `docs/contracts/operator_contracts.md`, `docs/roadmap_continuation_handoff.md` | What future code is allowed to do; gate ladder and acceptance |
| 3. Core source (read in this order) | `quantpilot/packages/core/schemas.py` → `quantpilot/packages/core/harness_service.py` → `quantpilot/packages/core/risk/gatekeeper.py` → `quantpilot/packages/core/execution/state_machine.py` → `quantpilot/packages/core/execution/paper_submission.py` → `quantpilot/packages/core/execution/paper_reconciliation.py` → `quantpilot/packages/core/execution/events.py` + `reducer.py` → `quantpilot/packages/core/execution/kernel.py` → `quantpilot/packages/db/sqlite_repositories.py` → `quantpilot/packages/db/repositories.py` → `quantpilot/packages/core/operator/service.py` → `quantpilot/packages/core/operator/professional_cycle.py` → `quantpilot/packages/brokers/mock_broker.py` / `paper_broker.py` / `kis_paper.py` + `quantpilot/packages/core/kis_paper.py` → `quantpilot/packages/core/data/providers.py` + `quality.py` | Domain model before orchestration; authority chain before durable store; kernel last because it is not yet wired |
| 4. Entry points | `quantpilot/jobs/run_smoke.py`, `quantpilot/jobs/run_kis_paper_session.py`, `quantpilot/jobs/run_kis_paper_kill.py`, `quantpilot/services/api/main.py`, `quantpilot/services/api/dependencies.py`, `quantpilot/services/api/routers/` (dir), `openapi.json` | How the harness is actually invoked; API surface snapshot |
| 5. Behavior-binding tests | `quantpilot/tests/unit/test_execution_kernel_v2.py`, `test_paper_submission_coordinator.py`, `test_external_paper_harness_integration.py`, `test_paper_dispatch_persistence.py`, `test_paper_risk_reservation_model.py`, `test_paper_reconciliation.py`, `test_paper_execution_shadow_parity.py`, `test_approval_tickets.py`, `test_harness_batch_risk.py`, `quantpilot/tests/integration/test_level3_flow.py` / `test_level4_guarded_flow.py` / `test_level5_operator_run_once.py` | These encode the contracts the prose claims |
| 6. Branch/worktree-scoped (label explicitly) | Diffs of `949aa7d` and `31ac3a1`; `test_strategy_version_matching.py` from `31ac3a1`; mission workboard in the `.codex` worktree | Only with the "accepted-unmerged" label from §5.2 |

Optional UI tier: `quantpilot/apps/web/src/App.tsx`,
`quantpilot/apps/web/src/lib/api.ts`, `queries.ts`, `openapi.d.ts`.

## 9. Integration requests to the DOC task (`AUDIT -> DOC`)

1. Pin the DOC to main `4241dc4`; label every KER-015 statement per §5.2 and
   never source from §5.3.
2. Reproduce the §4 disambiguation table (or equivalent wording) verbatim in
   the DOC's terminology section.
3. Carry the §6 open-P2 table (items 1–6) with the same statuses; do not
   promote or drop any entry without new committed evidence.
4. Run the DOC's own path-existence check per snapshot as in §1, and record
   the check in the workboard handoff.
5. Honor the §7 list as review-blocking: any DOC sentence matching an item
   there is a P1 documentation defect in my final review of `DOC`.

## 10. Verification record for this audit

Commands (all read-only, offline, from this worktree unless noted):

```text
git status / git log --oneline -5                       # clean, HEAD 4241dc4
git show --stat 949aa7d / 31ac3a1                       # commit inspection, no checkout
git branch -a --contains 949aa7d|31ac3a1                # only claude/qp-ker015-expiry-hardening
git merge-base --is-ancestor 31ac3a1 main               # false (unmerged)
git worktree list                                       # KER-015 worktree present
git cat-file -e 31ac3a1:...test_strategy_version_matching.py   # branch-only file
(main worktree) git status --short; git diff --stat     # dirty state, read-only
existence loop over every §3/§6/§8 path                 # all OK at 4241dc4
grep: place_limit_cash_order sole runtime call site; kernel zero runtime imports;
      REVIEWED_KERNEL_AST_SHA256 in test file only; authorization_time lines
```

No pytest/smoke run was required or performed for this docs-only audit; no
safety flag, runtime file, or workboard was changed. Known limit: the identity
of the second `949aa7d` audit P2 (§6 item 5) is unrecoverable from committed
history alone; closing it requires the Codex lead to durably record the audit
findings when integrating `QP-KER-015`.

## 11. Final DOC review (HP2REF-001 `DOC` counterpart review)

| Field | Value |
|---|---|
| Reviewer | Claude Code, exact model `claude-fable-5` |
| Reviewed commit | `4c5d8ec30fe933c9f0196e2ab4cf211e06e155df` (tip of `codex/harness-p2-gpt-reference-doc`; parent `48a1c91`) |
| Reviewed files | `docs/current_harness_and_p2_gpt_source_reference.md`, `docs/harness_p2_gpt_reference_workboard.md` — read via `git show 4c5d8ec:<path>`, no checkout, no network |
| Date | 2026-07-17 KST |
| Findings | **P0 = 0, P1 = 0, P2 = 0**; two P3 informational notes below |
| Verdict | **ACCEPT** |

### Verification performed (all read-only, offline, from this worktree)

1. **Snapshot precedence** — DOC §1/§6/§12 pin main `4241dc4` as default
   authority, `31ac3a1` as accepted-unmerged candidate, and exclude the dirty
   main working tree; matches §5 of this audit exactly. `31ac3a1` re-confirmed
   as the branch tip of `claude/qp-ker015-expiry-hardening` and not an
   ancestor of main.
2. **Disambiguation** — DOC §2 reproduces the §4 table (P2 severity vs
   Roadmap Gate 2 vs Gate P vs `QP-KER-0xx` vs schema v9–v11), including the
   "`015` is a task number, not a severity" caveat.
3. **Open P2 inventory** — DOC §8 carries all six §6 items with identical
   statuses plus the same closed/not-P2 exclusion list. Spot re-verified:
   `REVIEWED_KERNEL_AST_SHA256` lives only in
   `quantpilot/tests/unit/test_execution_kernel_v2.py` (lines 24/2860);
   `OperatorService._submit_proposals` captures `authorization_time` once and
   reuses it (`operator/service.py:781/827/880`); QP-RES-A2 "reservations
   carry no `policy_id`" matches the `paper_risk_reservations` schema and the
   accepted A2 audit wording.
4. **Source attachment manifest** — every §9.1/§9.2/§9.3 path (50 files +
   `quantpilot/services/api/routers/` directory) existence-checked with
   `git cat-file -e` at `4241dc4`: all present.
   `test_strategy_version_matching.py` confirmed present at `31ac3a1` and
   absent on main, and the DOC labels it branch-only as required.
5. **KER-015 changed-file set** — re-derived from
   `git diff --name-only $(git merge-base 4241dc4 31ac3a1)..31ac3a1`
   (merge-base `76ee0e2`): exactly the seven files the DOC lists. The DOC
   states integration/independent-re-audit as *pending* (avoids overclaim
   §7 items 1/2/12).
6. **Kernel runtime reachability** — `git grep` at `4241dc4` over
   `quantpilot/packages`, `quantpilot/services`, `quantpilot/jobs`: zero
   non-test imports of `execution.kernel`. Sole external order POST re-located
   at `paper_submission.py:604` → `KisPaperClient.place_limit_cash_order`
   (defined `kis_paper.py:764`). All runtime submission paths (operator
   service, professional cycle, orders router, L4 internal) converge on
   `HarnessService.submit_order_plan`, as the DOC claims.
7. **Safety claims** — six-value `DataMode` enum verified in `schemas.py`;
   `build_providers` fails closed with `ProviderError` for
   `realtime_market_data`/`paper_trading`/`live_trading` (no fixture
   fallback); `live_trading_candidate` blocks at `live_broker_unavailable`
   (`harness_service.py:1563`). DOC §11 reproduces the §7 overclaim guard,
   including no unbound test counts (§7 #10) and no claim that the Codex
   KER-015 re-audit completed (§7 #12).
8. **Lineage** — `git patch-id --stable` proves `48a1c91` is an identical
   cherry-pick of my `b022d25` AUDIT commit; the DOC branch therefore
   contains this audit unmodified.

All five §9 integration requests are satisfied; no DOC sentence matches any
§7 review-blocking item.

### P3 informational notes (non-blocking; no correction required)

1. DOC §5 renders workboard status `proposed` (Gates 020–080) as "open" and
   `QP-KER-070` as "locked", and summarizes `QP-KER-000A~000E` as
   "integrated". All three are semantically supported (contract §8: Gate 070
   may not move to `ready` until the flag ADR P2 closes, "KIS is last";
   contract integration `9fbf035`), but the vocabulary differs from the
   committed workboard status column. A one-line mapping note would remove
   any ambiguity for GPT readers.
2. The workboard's handoff record and mission retrospective rows are still
   `pending`; the lead should fill them when closing the mission with this
   review result.
