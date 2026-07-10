# Agent Capability Scorecard

> Evidence-based routing record for the QuantPilot multi-agent collaboration (COLLAB-V1).
> Principle: **"잘하는 건 잘하는 에이전트가 한다"** — the agent that is demonstrably better at a task class
> does that task class. Preferences follow evidence; they are priors, never fixed ownership.
> Owner of this file: Claude Code (COLLAB-V1). All other collaboration documents keep their existing owners.

## 1. Purpose and scope

This scorecard makes mission routing decision-complete: given a new task, a dispatcher (human or agent)
can read this file alone and decide which agent instance should execute it, with what confidence, and
what evidence would change that decision.

Two layers are deliberately separated:

- **Policy constraints (non-negotiable, outside this scorecard).** Safety invariants
  (`LIVE_TRADING_ENABLED=false` etc.), the order state machine, risk gates, and any user-directed
  ownership rule (e.g. workboard rules 9–10, Git integration authority in the main repository) are
  *constraints*, not preferences. No accumulation of capability evidence ever routes work across a
  policy constraint. If evidence suggests a constraint is misallocated, the finding is escalated to the
  user; the scorecard itself never overrides it.
- **Capability preferences (this scorecard).** Everything else is routable and mutable under the
  evidence rules below.

## 2. Agent identity model: instance, not vendor

A "capability" belongs to a **model instance**, never to a vendor. "Claude" and "GPT/Codex" are not
capability units; `claude-fable-5` and `gpt-5.x-codex-cli` (exact version) are. Rules:

- Every evidence record MUST name the exact model/version that produced the work
  (e.g. `claude-fable-5`, `claude-opus-4-8`, `gpt-5.4-codex`, plus harness: Claude Code / Codex CLI).
- Scores are NEVER aggregated across model versions. A new model version (Fable 5 → Fable 5.x,
  GPT-5.4 → GPT-5.5) starts a new record row. Its predecessor's scores are *priors with decayed
  confidence* (treat inherited sample count as `floor(n/2)`), not inherited fact.
- No vendor is treated as a single timeless capability: "Claude was better at X in 2026-07" is a claim
  about one model version in one time window, and it expires when the model does.

### Registered instances

| Instance key | Vendor | Model / version | Harness | Status | Notes |
|---|---|---|---|---|---|
| `fable5-cc` | Anthropic | claude-fable-5 | Claude Code | active | Current Claude executor for COLLAB-V1 |
| `opus4x-cc` | Anthropic | claude-opus-4-x (exact id per record) | Claude Code | available | Register exact id on first evidence row |
| `codex-gpt5x` | OpenAI | GPT-5.x Codex (exact version per record) | Codex CLI | active | Historical workboard rows did not record the exact version — see §8 data-quality note |

New instances (other vendors, other harnesses) are added here before their first evidence row.

## 3. Routing dimensions and weights

Composite score = Σ (weight × dimension score) / 100, on a 1.00–5.00 scale. Weights are fixed at
30/25/25/10/10 and may only be changed by the user.

| # | Dimension | Weight | What it measures | Anchor 5 | Anchor 1 |
|---|---|---|---|---|---|
| D1 | First-pass correctness | 30 | Was the first submitted artifact accepted as functionally correct? | Accepted as-is; reviewer found no behavioral defect | Rejected or fundamentally wrong approach |
| D2 | Defect severity & rework cost | 25 | Severity of defects found downstream and cost to fix them | Zero defects ≥ P3; zero rework cycles | P0/P1 defect, or ≥3 rework cycles |
| D3 | Scope & protocol discipline | 25 | Stayed inside owned paths, honest evidence, no invariant drift | Perfect boundary compliance; claims grounded in exact command output | Edited unowned paths, weakened tests, or unverifiable claims |
| D4 | Verification evidence quality | 10 | Did the agent supply reproducible checks with exact output? | Targeted + full-suite output recorded verbatim | No verification, or asserted-only claims |
| D5 | Throughput (elapsed time) | 10 | Wall-clock from claim to review-ready, normalized to task size | Clearly faster than the comparable baseline | Stalled; required escalation to progress |

Severity scale used throughout: **P0** safety-invariant or live-order risk; **P1** wrong trading
decision logic or data-integrity defect that reached review; **P2** material correctness issue caught
and fixed in review; **P3** cosmetic/style.

## 4. Completion rating rubric (1–5, per evidence record)

| Rating | Meaning |
|---|---|
| 5 | Accepted first pass. No defects ≥ P3 in review or after. Evidence complete. |
| 4 | Accepted with minor review edits (P3, or reviewer additions that did not change behavior). ≤1 rework cycle. |
| 3 | Required one material rework cycle, or reviewer fixed a P2 behavioral defect before acceptance. |
| 2 | Multiple rework cycles, or a P1 defect discovered in review; task still landed. |
| 1 | Task failed, was abandoned, caused a P0, or violated a safety/protocol boundary. |

The completion rating is a summary; routing decisions use the weighted composite (§3) when dimension
scores exist, and the completion rating alone when only coarse historical evidence is available.

## 5. Raw evidence record format

Every completed task appends one record. Required fields — a record missing any of these is not
usable for preference changes:

```yaml
task_id:            # e.g. QP-110
date:               # ISO date, KST
instance:           # instance key from §2 + exact model id if newly observed
task_class:         # from §6 table
first_pass_result:  # accepted / accepted-with-edits / rework-required / rejected
defects:            # list of {severity: P0..P3, description}, or []
rework_cycles:      # integer
checks_run:         # exact commands + verbatim result line(s)
elapsed:            # claim → review-ready wall clock, or "not recorded"
reviewer:           # who reviewed (instance or human)
completion_rating:  # 1–5 per §4
dimension_scores:   # optional {D1..D5}, when reviewer scored them
```

## 6. Task-class preference table

Current priors. **Every entry is a prior, not ownership.** `n` = comparable samples for the preferred
instance in that class. Entries marked `policy` are user-directed routing (workboard rule 10) that has
not yet been re-derived from ≥3 samples; they are still subject to the same evidence rules and the
policy-constraint carve-out in §1.

| Task class | Preferred instance | Basis | n | Confidence |
|---|---|---|---|---|
| Pure technical/signal modules (indicators, entry rules) | `fable5-cc`* | QP-110 | 1 | low |
| Pure position-risk evaluators | `fable5-cc`* | QP-120 | 1 | low |
| Read-only root-cause diagnosis (stuck-task rescue) | `fable5-cc`* | QP-040/QP-050 diagnoses, both confirmed | 2 | medium |
| Research synthesis / backtest forensics / risk-matrix & recipe design | `fable5-cc` | policy (rule 10) | 0 | policy prior |
| Independent contract/evidence review | `fable5-cc` | policy (rule 10) | 0 | policy prior |
| Cross-cutting contracts & rails (schemas, xfail targets, fixtures) | `codex-gpt5x` | QP-010, QP-030 | 2 | medium |
| Stateful integration (DB, broker adapters, API, scheduler, UI wiring) | `codex-gpt5x` | QP-020/030/040/050/060 | 5 | high |
| Release acceptance & Git integration | `codex-gpt5x` | QP-900 + main-repo policy | 1 + policy | policy-backed |
| Documentation / protocol authoring | no preference | a8867f0 (claude-drafted, codex-committed) | 1 | none |

\* Historical "Claude Code" rows predate exact model-id recording; the prior is provisionally
assigned to the current Claude instance. See §8.

## 7. Preference-change and tie rules

- **Minimum evidence.** A task-class preference changes only after **≥3 comparable samples** (same
  task class, same instance, comparable difficulty) show the challenger's composite ≥0.5 above the
  incumbent's. Fewer samples update `n` and confidence, never the preference.
- **P0/P1 exception.** A single P0 or P1 defect attributable to the currently preferred instance
  triggers an **immediate preference review** for that task class — review, not automatic flip: the
  incumbent is suspended for that class pending a root-cause read, and the class reverts to
  case-by-case routing until the review concludes.
- **Tie handling (within 0.5).** If two instances' composites differ by **less than 0.5**, treat as a
  tie. Ties never flip an existing preference. Break ties for a specific mission in this order:
  1. Policy constraints (§1) — eliminates ineligible instances first;
  2. Integration adjacency — the instance already holding neighboring state/paths;
  3. Availability and parallelism — keep both agents' single `in_progress` slots utilized;
  4. Cost/latency, judged per mission.
- **Staleness.** Evidence older than the producing model version is decayed (§2). Evidence older than
  6 months is advisory only, regardless of version.

## 8. Seed evidence (QuantPilot, grounded only in the completed workboard)

Source: `docs/professional_operator_workboard.md` (QP-000…QP-900, all `done`; checkpoint log
2026-07-10 – 2026-07-11 KST). **Data-quality note:** the workboard recorded agents as "Codex" /
"Claude Code" without exact model versions. These rows therefore carry instance keys with a
`version-unrecorded` flag and cannot, by themselves, justify cross-version claims. From this record
onward, the exact model id field in §5 is mandatory.

| task_id | instance (flag) | task_class | first_pass_result | defects | rework | checks_run (verbatim core) | elapsed | rating |
|---|---|---|---|---|---|---|---|---|
| QP-110 | claude-cc (version-unrecorded) | pure signal module | accepted-with-edits | [] — reviewer added ATR first-session seed proof, no behavioral fix recorded | 0 | `12 passed` (Codex-reviewed targeted) | ~25m (06:40→07:05) | 4 |
| QP-120 | claude-cc (version-unrecorded) | pure position-risk evaluator | rework-required | P2: contract semantics corrected in review (realtime quote → protective stop only; completed close → SMA20 exit/trim; unrounded threshold comparison) | 1 | targeted `10 passed`; backend `388 passed, 1 skipped` | ~20m (07:05→07:25 incl. review) | 3 |
| QP-040-diag | claude-cc (version-unrecorded) | root-cause diagnosis | accepted | [] — fingerprint/`requested_at` hypothesis confirmed by the QP-040 fix | 0 | read-only; backend snapshot `559 tests, 2 failures` correctly localized to Codex paths | n/a (snapshot) | 5 |
| QP-050-diag | claude-cc (version-unrecorded) | root-cause diagnosis | accepted | [] — `signals/service.py:341` NameError root cause confirmed exactly | 0 | read-only; backend snapshot `728 tests, 8 failures` correctly localized | n/a (snapshot) | 5 |
| QP-000 | codex (version-unrecorded) | rails/baseline | accepted | [] | 0 | backend `324 passed, 1 skipped`; smoke mock/default-blocked; frontend `20 passed` + build | not recorded | 5 |
| QP-010 | codex (version-unrecorded) | contracts & rails | accepted | [] | 0 | targeted `3 passed, 16 xfailed`; full `325 passed, 1 skipped, 8 xfailed` | not recorded | 5 |
| QP-020 | codex (version-unrecorded) | stateful persistence | accepted | [] | 0 | `8 passed` (restart recovery, stale-write, secret-field guards) | not recorded | 5 |
| QP-030 | codex (version-unrecorded) | operator integration | rework-required (self-caught) | P2×2: blocked-signal→liquidation leak; same-day forming-bar inclusion — found and fixed in own final safety review before merge | 1 | `379 passed, 1 skipped, 7 xfailed`; smoke mock/default-blocked | not recorded | 4 |
| QP-040 | codex (version-unrecorded) | orchestration (retirement/rebalance) | rework-required | P2: idempotency fingerprint bound per-request field, 2 mid-task test failures (externally diagnosed); fixture root cause fixed | 1 | `568 passed, 1 skipped in 10.26s`; smoke mock/default-blocked; two independent audits no P0/P1 | ~10.5h (07:25→17:54) | 4 |
| QP-050 | codex (version-unrecorded) | KIS paper adapters/reconciliation | rework-required | P2: `signals/service.py` NameError in fail-closed branch (externally diagnosed), tz-representation test failure; audit fixes applied before done | 1 | `757 passed, 2 skipped`; smoke mock/default-blocked; paper job `paper_session_disabled`, no network | ~5.5h (17:54→23:16) | 4 |
| QP-060 | codex (version-unrecorded) | status projection/UI | accepted | [] — independent audits no P0/P1 | 0 | focused `74 passed`; tree `788 passed, 2 skipped`; frontend `23 passed` + build | ~50m (23:16→00:08) | 5 |
| QP-900 | codex (version-unrecorded) | release acceptance & Git | accepted | [] — secret scan clean, exact staged diff check passed | 0 | isolated `fa5e76a`: backend `785 passed, 2 skipped`; frontend `23 passed` + build; smoke mock/default-blocked | ~6m (00:08→00:14) | 5 |

Seed reading, honestly bounded: `codex` shows a strong, well-sampled record in stateful integration
(5 samples, ratings 4–5, all defects P2 and fixed pre-merge); `claude-cc` shows a small but clean
record in pure modules (2 samples, one P2 corrected in review) and a perfect 2/2 in read-only
root-cause diagnosis. No class has enough same-class samples to satisfy the ≥3 rule for a *change*
yet; the table in §6 is the starting prior, nothing more.

## 9. Operating notes

- Append new evidence records to §8 (or a successor evidence log split out when it grows) at the same
  checkpoint where the workboard is updated; the workboard remains the canonical execution board, this
  file is the routing memory.
- When a new model version replaces an instance, add the row in §2, decay inherited confidence per §2,
  and re-evaluate §6 at the next ≥3-sample checkpoint.
- Disagreement between this scorecard and any user-directed rule is resolved in favor of the user rule,
  and recorded here as a policy constraint until the user says otherwise.
