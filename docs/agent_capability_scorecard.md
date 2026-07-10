# Agent Capability Scorecard

> Evidence-based routing record for the QuantPilot multi-agent collaboration (COLLAB-V1).
> Principle: **"잘하는 건 잘하는 에이전트가 한다"** — the agent that is demonstrably better at a task class
> does that task class. Preferences follow evidence; they are priors, never fixed ownership.
> Owner of this file: Claude Code (COLLAB-V1). All other collaboration documents keep their existing owners.

## 1. Purpose, scope, and governing documents

This scorecard makes mission routing decision-complete: given a new mission, a dispatcher (human or
agent) can read this file alone and decide which agent instance should execute it, with what
confidence, and what evidence would change that decision.

Governance for new missions is the root `AGENTS.md` plus `docs/agent_collaboration_protocol.md`
(mission-lead-owned). Under that protocol each agent commits in its own isolated worktree and the
mission lead is the sole mainline integrator. `docs/professional_operator_workboard.md` is a **completed
historical artifact**: its checkpoint log remains valid *evidence* (§8), but its execution rules —
including rules 4–6 and the old main-repo Git authority — are **not** policy constraints for new
missions.

Two layers are deliberately separated:

- **Policy constraints (non-negotiable, outside this scorecard).** QuantPilot trading safety
  invariants — `LIVE_TRADING_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`,
  `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock` — plus
  the order state machine, risk gates, no-secrets rule, and any standing user-directed rule. No
  accumulation of capability evidence ever routes work across a policy constraint. If evidence
  suggests a constraint is misallocated, the finding is escalated to the user; the scorecard itself
  never overrides it.
- **Capability preferences (this scorecard).** Everything else — including which agent implements,
  reviews, or integrates a given task class — is routable and mutable under the evidence rules below.

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

## 3. Pre-task routing formula (mandatory)

Routing is decided **before** a mission starts, using exactly these five user-mandated dimensions.
Routing score = Σ (weight × dimension score) / 100, on a 1.00–5.00 scale, computed per candidate
instance. Weights are fixed at 30/25/25/10/10 and may only be changed by the user.

| # | Dimension | Weight | What it asks | Anchor 5 | Anchor 3 | Anchor 1 |
|---|---|---|---|---|---|---|
| R1 | Problem/domain fit | 30 | Does the mission's core difficulty match this instance's strengths? | Core difficulty squarely in the instance's strongest demonstrated class | Adjacent class, partial overlap, or no comparable evidence yet (unknown is neutral) | Negative evidence only: demonstrated weakness in this difficulty type, or an actual structural mismatch — never mere absence of samples |
| R2 | Repository/tool fit | 25 | Can this instance's harness, tools, and environment do the work natively? | All required tools/permissions native and already configured | Workable with minor adaptation or one missing convenience | Requires tools, access, or environment the instance lacks |
| R3 | Comparable track record | 25 | What does §4 retrospective evidence say for this task class and model version? | ≥3 comparable samples, mean rating ≥4.5, no open P0/P1 | 1–2 samples, ≥3 with mixed (3–4) ratings, or **n=0** (no comparable samples — unknown is neutral) | Negative evidence only: recorded failures (mean ≤2), an unresolved P0/P1, or directly contradicting evidence — never mere absence of samples |
| R4 | Current context continuity | 10 | How much mission context does the instance already hold? | Already holds the mission's full context (same worktree/session, directly preceding related work) | Partial context; moderate re-derivation needed | Cold start; must re-derive everything |
| R5 | Handoff/conflict cost | 10 | What does routing here cost in handoffs and conflicts? | No handoff artifacts needed; no overlap with the other agent's active paths | One clean artifact handoff, or minor serialization | Heavy multi-artifact handoff, or would conflict/serialize with active work |

**Tie rule:** if the top two candidates' routing scores differ by **≤ 0.5**, route to the **initial
mission recipient side** (the instance the mission was first addressed or assigned to). Ties never
justify pulling a mission away from its recipient.

R3 is the only dimension fed by accumulated evidence; R1/R2/R4/R5 are assessed per mission at
dispatch time. Score each candidate honestly even when only one candidate is plausible — the recorded
scores become auditable routing rationale.

**Unknown is neutral, never weakness.** Across R1 and R3, the absence of comparable samples scores
**3** (neutral/unknown), never 1. A score of 1 requires *negative* evidence: recorded failures, an
actual capability mismatch, or an unresolved serious defect. Without this rule the instance with the
head start wins every class forever simply because the other instance was never measured — evidence
starvation, not evidence.

**Bounded capability seeding (evidence-starvation guard, not a quota).** When all of the following
hold for an eligible challenger in a task class:

- the challenger has `n=0` comparable samples in that class, and
- its R1 and R2 each score ≥4 for the slice, and
- the slice is low/medium risk, reversible, and isolated, and
- it does not cross any project safety or authority constraint (§1),

the mission lead assigns **at most one small seed slice** in that class to the challenger as its
substantive role in the mission. This is deliberate evidence exploration so R3 can eventually be
scored on samples rather than silence — it is **not** a 50:50 workload quota, creates no entitlement
to further slices, and **never** applies to live trading, secrets, external side effects, or mainline
integration authority.

## 4. Post-task retrospective evidence (performance, not routing)

The dimensions below measure how a task **went**, after the fact. They exist to feed R3 and the §6
priors. They are **never** substituted for the §3 routing formula.

### 4.1 Retrospective dimensions

| # | Dimension | What it measures |
|---|---|---|
| E1 | First-pass correctness | Was the first submitted artifact accepted as functionally correct? |
| E2 | Defect severity & rework cost | Severity of defects found downstream and cost to fix them |
| E3 | Scope & protocol discipline | Stayed inside owned paths, honest evidence, no invariant drift |
| E4 | Verification evidence quality | Reproducible checks supplied with exact output |
| E5 | Throughput | Wall-clock from claim to review-ready, normalized to task size |

Severity scale used throughout: **P0** safety-invariant or live-order risk; **P1** wrong trading
decision logic or data-integrity defect that reached review; **P2** material correctness issue caught
and fixed in review; **P3** cosmetic/style.

### 4.2 Completion rating rubric (1–5, per evidence record)

| Rating | Meaning |
|---|---|
| 5 | Accepted first pass. No defects ≥ P3 in review or after. Evidence complete. |
| 4 | Accepted with minor review edits (P3, or reviewer additions that did not change behavior). ≤1 rework cycle. |
| 3 | Required one material rework cycle, or reviewer fixed a P2 behavioral defect before acceptance. |
| 2 | Multiple rework cycles, or a P1 defect discovered in review; task still landed. |
| 1 | Task failed, was abandoned, caused a P0, or violated a safety/protocol boundary. |

The completion rating summarizes E1–E5 into the single number that R3 consumes.

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
completion_rating:  # 1–5 per §4.2
```

## 6. Task-class preference table

Current priors feeding R1 and R3. **Every entry is a prior, not ownership.** `n` = comparable samples
for the preferred instance in that class. Entries whose basis is the historical workboard's rule-10
routing are labeled `historical routing`: they came from a user direction inside a now-completed
mission and carry no samples of their own — they are starting priors like any other, fully subject to
§7.

| Task class | Preferred instance | Basis | n | Confidence |
|---|---|---|---|---|
| Pure technical/signal modules (indicators, entry rules) | `fable5-cc`* | QP-110 | 1 | low |
| Pure position-risk evaluators | `fable5-cc`* | QP-120 | 1 | low |
| Read-only root-cause diagnosis (stuck-task rescue) | `fable5-cc`* | QP-040/QP-050 diagnoses, both confirmed | 2 | medium |
| Research synthesis / backtest forensics / risk-matrix & recipe design | `fable5-cc` | historical routing (workboard rule 10) | 0 | prior only |
| Independent contract/evidence review | `fable5-cc` | historical routing (workboard rule 10) | 0 | prior only |
| Cross-cutting contracts & rails (schemas, xfail targets, fixtures) | `codex-gpt5x` | QP-010, QP-030 | 2 | medium |
| Stateful integration (DB, broker adapters, API, scheduler, UI wiring) | `codex-gpt5x` | QP-020/030/040/050/060 | 5 | high |
| Release acceptance & mainline integration | mission lead (role, per collaboration protocol) | QP-900 as evidence for `codex-gpt5x` in that role | 1 | role-based, not a capability preference |
| Documentation / protocol authoring | no preference | a8867f0 (claude-drafted, codex-committed) | 1 | none |

\* Historical "Claude Code" rows predate exact model-id recording; the prior is provisionally
assigned to the current Claude instance. See §8.

Mainline integration is a **role** assigned by the collaboration protocol (the mission lead is the
sole mainline integrator), not a class won by evidence; it is listed for completeness so dispatchers
do not route it by score. Each agent's commits in its own worktree need no routing decision at all.

## 7. Preference-change rules

- **Minimum evidence.** A task-class preference changes only after **≥3 comparable samples** (same
  task class, same instance, comparable difficulty) show the challenger's mean completion rating (or
  E1–E5 composite where scored) clearly above the incumbent's. Fewer samples update `n` and
  confidence, never the preference.
- **P0/P1 exception.** A single P0 or P1 defect attributable to the currently preferred instance
  triggers an **immediate preference review** for that task class — review, not automatic flip: the
  incumbent is suspended for that class pending a root-cause read, and the class reverts to
  case-by-case §3 routing until the review concludes.
- **Close calls at dispatch.** Near-equal routing scores are handled by the §3 tie rule (≤ 0.5 →
  initial mission recipient), not by changing the §6 prior.
- **Staleness.** Evidence older than the producing model version is decayed (§2). Evidence older than
  6 months is advisory only, regardless of version.

## 8. Seed evidence (QuantPilot, grounded only in the completed workboard)

Source: `docs/professional_operator_workboard.md` (QP-000…QP-900, all `done`; checkpoint log
2026-07-10 – 2026-07-11 KST) — a completed historical artifact used here strictly as evidence.
**Data-quality note:** the workboard recorded agents as "Codex" / "Claude Code" without exact model
versions. These rows therefore carry instance keys with a `version-unrecorded` flag and cannot, by
themselves, justify cross-version claims. From this record onward, the exact model id field in §5 is
mandatory.

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

### 8.1 COLLAB-V1 mission evidence (2026-07-11 KST)

First evidence recorded under the new protocol itself. Source: this branch's commit history
(`8b903a0`, `5774fab`, `f05ecb6`), branch `codex/collab-v1-core` (protocol `6d7bfec`, scenarios
`aeab47f`, transferred scorecard commits, bounded-exploration `22eee85`), and the Codex mission
lead's consolidated review record of the Opus audit.

| task_id | instance | task_class | first_pass_result | defects | rework | checks_run (verbatim core) | elapsed | reviewer | rating |
|---|---|---|---|---|---|---|---|---|---|
| COLLAB-V1-scorecard | `fable5-cc` (claude-fable-5, Claude Code) | capability-scorecard design | rework-required | P2: pre-task routing initially conflated with post-task performance dimensions; P2: obsolete Codex-only Git authority initially treated as active policy; second review found a stale integration note and evidence-starvation clarity gaps | 2 | `git diff --check` passed at each of the three commits (`8b903a0`, `5774fab`, `f05ecb6`); staged path verified single-file each time | ~647s across the three Claude executions (excluding reviewer wait) | Codex mission lead + Claude Opus | 2 |
| COLLAB-V1-opus-audit | `opus4x-cc` (claude-opus alias; exact resolved version unrecorded) | independent protocol audit | accepted | [] — the audit itself was defect-free; it correctly found three P2 issues in the target documents, all confirmed and fixed | 0 | read-only verification of `82945d6..HEAD` | 258.4s | Codex mission lead | 5 |
| COLLAB-V1-codex-core | `codex-gpt5x` (GPT-5 Codex; exact resolved version unrecorded) | protocol authoring & integration | rework-required | P2 (process): `apply_patch` initially targeted the dirty main workspace instead of the sibling worktree; work was mechanically transferred and main restored without touching user-owned dirty files; P2 (documentation): mainline-integration wording and the evidence-starvation guard needed Opus-driven revision | 2 | `git diff --check 82945d6..HEAD` passed; local links 9 files passed; nine final contracts passed; no legacy fixed-ownership wording; historical workboard unchanged | not recorded | Claude Opus + Fable revisions | 2 |

Ratings follow §4.2 strictly: two rework cycles score 2 regardless of final artifact quality — the
rubric measures the path, not the destination, and is not rewritten upward after acceptance.

**Learning note.** (1) Next mission must explicitly verify patch/edit tool working-directory
semantics before parallel edits — the COLLAB-V1-codex-core process defect was a working-directory
assumption, not a content error. (2) The bounded capability-seed rule (§3) was learned from the Opus
audit during this mission and is now active.

## 9. Operating notes

- Append new evidence records to §8 (or a successor evidence log split out when it grows) at each
  mission's completion checkpoint. Under the current protocol this file is the routing memory; the
  historical workboard is closed and receives no new entries.
- Record the §3 routing scores (all candidates) in the mission record at dispatch, so routing
  rationale stays auditable alongside outcome evidence.
- When a new model version replaces an instance, add the row in §2, decay inherited confidence per §2,
  and re-evaluate §6 at the next ≥3-sample checkpoint.
- Disagreement between this scorecard and any standing user-directed rule is resolved in favor of the
  user rule, and recorded here as a policy constraint until the user says otherwise.
