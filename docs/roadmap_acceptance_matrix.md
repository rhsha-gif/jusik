# QuantPilot Roadmap Acceptance Matrix

Repository-grounded, stage-gated acceptance criteria from the verified v9 KIS
paper-kill baseline through paper operational readiness. This document is the
authoritative bind for what each roadmap gate must prove, with which commands,
under which safety invariants, and how *fake-only fixture development* is
distinguished from *manual KIS operational validation*.

- Mission: `QP-ROADMAP-EXECUTION` (lead: GPT-5 Codex; see
  `docs/roadmap_execution_workboard.md`).
- This artifact: `QP-RM-00A` counterpart role (independent acceptance authoring),
  authored by Claude Code on branch `claude/qp-roadmap-contracts`.
- Governing documents: root `AGENTS.md`, `docs/agent_collaboration_protocol.md`,
  `docs/roadmap_baseline_v9_report.md`, `docs/contracts/kis_paper_kill_contract.md`,
  and the reservation contract `docs/contracts/atomic_risk_reservation_v1.md`.

This matrix does not grant any authority. It records what evidence a gate must
present before its workboard may move to `integrated`/`done`. It never widens a
safety boundary.

## 1. Standing safety invariants (every gate, non-negotiable)

These come from `AGENTS.md`, the collaboration protocol §8, and the mission
charter. No accumulation of evidence routes work across any of them; a suspected
misallocation is escalated to the user, never overridden by a gate.

| Invariant | Required value | Repository anchor |
|---|---|---|
| Live trading | `LIVE_TRADING_ENABLED=false` | `AGENTS.md`; smoke prints `live_trading_enabled=false` |
| Guarded autopilot | `GUARDED_AUTOPILOT_ENABLED=false` | `AGENTS.md` |
| Fully automated operator | `FULLY_AUTOMATED_OPERATOR_ENABLED=false` | `risk/gatekeeper.py:36` (`allowed_execution_modes`) |
| Market orders | `MARKET_ORDERS_ENABLED=false` | `risk/gatekeeper.py:27` (`market_orders_enabled`) |
| Broker mode | `BROKER_MODE=mock` | smoke prints `broker=mock` |
| Secrets | no credentials/account IDs/tokens persisted or printed | `StateStoreProvenance.account_scope_fingerprint` is an opaque `sha256:` digest only (`operator/position_ledger.py:106-119`) |
| External connectors | fake-client unit tests only; real KIS is skipped/manual | `docs/contracts/kis_paper_kill_contract.md` "Adversarial executable test matrix" |
| Order-path integrity | risk gate, kill switch, idempotency, order state machine, audit, reconciliation are never bypassed | `PAPER_DISPATCH_TRANSITIONS`/`PAPER_KILL_TRANSITIONS`/`PAPER_CANCEL_TRANSITIONS` (`db/sqlite_repositories.py:62-117`) |
| LLM/RL authority | model output cannot create, approve, or submit broker orders | `DurablePaperSubmissionCoordinator` is the sole POST authority (`execution/paper_submission.py:107-108`) |

A gate that cannot demonstrate every invariant above is **not** acceptable,
regardless of feature completeness.

## 2. Authoritative verification commands

Exact commands and the verbatim evidence lines a gate must reproduce. The
`--basetemp`/`--junitxml` form is required because the default pytest temp roots
are locked on this Windows host and the summary line is suppressed
(see the pytest-basetemp memory note); use the JUnit XML for authoritative
pass/skip counts.

```powershell
# Backend (authoritative test evidence)
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp `
    --junitxml=.pytest_tmp/roadmap.xml
# v9 baseline evidence of record: 819 passed, 2 skipped (roadmap_baseline_v9_report.md)

# Smoke / orchestration safety
python -m quantpilot.jobs.run_smoke
# Required lines: broker=mock; live_trading_enabled=false;
#                 operator.status=blocked; operator.fallback=level5_flag_disabled

# Kill CLI default-blocked proof
python -m quantpilot.jobs.run_kis_paper_kill engage
# Required: {"status":"blocked","reason_code":"paper_kill_disabled"}
```

Frontend gates additionally require, from `quantpilot/apps/web`:

```powershell
npm run test
npm run build
```

Documentation-only gates (this matrix, the reservation contract, the reservation
workboard) require `git diff --check` clean and internal-link resolution; they do
not change runtime behavior and therefore do not by themselves re-run the suite,
but the integrating lead re-runs §2 backend + smoke on the clean integration
commit before `done` (collaboration protocol §5).

## 3. Fake-only development vs manual KIS operational validation

This is the single most safety-critical distinction in the roadmap. Every gate
declares which side of the line each acceptance item sits on.

**Fake-only fixture development (automatic, no authority request).**
- Uses a deterministic fake KIS client; no network, no secrets, no real account.
- `data_mode ∈ {fixture, paper_trading-with-fake-client}`; `broker_environment`
  stays `fixture_mock` or a fake `kis_paper` bound to an opaque test fingerprint.
- All assertions are reproducible offline and are part of `python -m pytest`.
- Proceeds automatically inside the repository under the collaboration protocol.
- **Cannot** prove: real TR field semantics (e.g. cancelable-order inquiry
  `VTTC0084R`), real broker rounding/lot rules, real session-calendar edges, or
  real network failure modes.

**Manual KIS operational validation (explicit user opt-in only).**
- Requires user-supplied paper credentials and explicit manual authority
  (`roadmap_execution_workboard.md` blockers; `roadmap_baseline_v9_report.md`
  "Remaining gate").
- Confirms the inferred `VTTC0084R` cancelable-order inquiry and any other real
  TR contract against a separate paper account.
- Is the **only** evidence that authorizes *operational* kill/reservation use.
- Never authorizes live trading and never blocks fixture/fake-client development
  of downstream gates.

Acceptance rule: a gate may reach `done` on fake-only evidence for **development
readiness**, but **paper operational readiness** (Gate P) additionally requires
the manual-validation column to be satisfied and recorded with the user's
explicit authorization.

## 4. Stage-gated acceptance

Dependency order follows the roadmap work queue
(`roadmap_execution_workboard.md`): `QP-RM-00 → QP-RISK-RES-V1 →
QP-EXEC-EVENTS-V1 → QP-KERNEL-V2 → QP-LEDGER-RUNTIME → Gate P (paper readiness)`.
Each gate lists its dependency, the invariants it must additionally hold, its
authoritative evidence, and its fake-vs-manual split.

### Gate 0 — `QP-RM-00` baseline (status: done, evidence of record)

- **Depends on:** none.
- **Scope:** kill v1 fast-forwarded from clean `main`; schema v9 baseline.
- **Additional invariants:** kill fence blocks new paper preparation and final
  POST authority at both startup and pre-dispatch gates; ambiguous cancel outcomes
  are never auto-reposted (`kis_paper_kill_contract.md` "Durable state machines").
- **Authoritative evidence (already recorded):** backend `819 passed, 2 skipped`;
  smoke `broker=mock`, `live=false`, operator blocked; kill CLI
  `paper_kill_disabled` (`roadmap_baseline_v9_report.md`, commit `216ff22`).
- **Fake-only:** all of the above. **Manual:** `VTTC0084R` remains an open manual
  gate; it blocks operational kill use only, not downstream development.

### Gate 1 — `QP-RISK-RES-V1` atomic risk reservation (schema v10; status: done — fake-only development readiness, Gate P pending)

- **Depends on:** Gate 0 baseline and this matrix (`QP-RM-00A`).
- **Scope:** durable, atomic cash + sell-quantity + incremental long-gross
  reservation for KIS paper,
  long-only, KRW, whole-share limit orders. Full contract:
  `docs/contracts/atomic_risk_reservation_v1.md`.
- **Additional invariants (bound to the contract):**
  1. Reservation and its prepared dispatch commit in **one** `BEGIN IMMEDIATE`
     transaction — no window where a dispatch is prepared without its reservation,
     nor a reservation without its dispatch (contract §5, §7).
  2. Availability is computed **conservatively**: `Σ held reservations` is
     subtracted from broker-evidenced capacity before a new reservation is
     admitted; ties fail closed (contract §4).
  3. KRW and whole-share reservation arithmetic is persisted as integers;
     current long gross plus held buy gross plus the new buy must not exceed
     `snapshot_equity - minimum_cash_reserve`. Sells receive no pre-fill gross
     credit.
  4. `outcome_unknown` and any post-claim ambiguity keep the reservation `held`;
     reservations are released only on definitive terminal evidence
     (`filled`/`cancelled`/`rejected`/`expired_pre_dispatch`/`failed_pre_dispatch`)
     — mirroring the kill contract's query-only recovery
     (`kis_paper_kill_contract.md` "Crash and replay contract"; contract §6, §8).
  5. Reservation carries idempotency, provenance (`data_mode=paper_trading`,
     `broker_environment=kis_paper`, opaque account fingerprint), `revision`
     CAS, and `session_id`/`fencing_token` fencing identical in discipline to
     `PaperOrderDispatch` (`operator/position_ledger.py:460-534`; contract §3).
  6. Migration v9→v10 adds the reservation table empty and backfills a `held`
     reservation for every open (non-terminal) dispatch from that dispatch's own
     durable evidence; terminal dispatches need none (contract §9).
- **Authoritative evidence:** baseline integration head `5eb70a9`; backend
  `885 passed, 2 skipped` with reservation concurrency/crash/migration tests;
  smoke `broker=mock`, `live=false`, operator blocked with
  fallback=`level5_flag_disabled`; kill CLI default-blocked with
  `paper_kill_disabled`; `git diff --check` clean. Claude Code
  `claude-fable-5` audit `c021a50` plus follow-up `b280bef` report
  **residual P0=0/P1=0** and close QP-RES-A1. Non-blocking residuals are
  QP-RES-A2 P2 (conservative cross-policy over-blocking) and QP-RES-A3 P3
  (diagnostic error wording).
- **Fake-only:** every automatic test uses the deterministic fake KIS client and
  fixed clocks; no network, no secrets. This proves *development readiness* of the
  reservation arithmetic, atomicity, and crash behavior.
- **Manual:** none required for this gate's development acceptance. Real
  buying-power TR field semantics feed Gate P, not Gate 1.

### Gate 2 — `QP-EXEC-EVENTS-V1` canonical events

- **Depends on:** Gate 1 contract stability (reservation lifecycle aggregate
  identities must be stable before event dual-write —
  `roadmap_execution_workboard.md` integration requests).
- **Scope:** canonical execution events + deterministic reducer + shadow parity.
- **Additional invariants:** replay is deterministic; duplicate or out-of-order
  events cannot corrupt projections; events are derived from, and never widen,
  the existing durable state machines.
- **Authoritative evidence:** §2 backend + smoke green; replay-determinism and
  duplicate/reordering property tests present and passing; shadow projection
  equals the authoritative store on the fixture corpus; zero P0/P1.
- **Fake-only:** all. **Manual:** none.

### Gate 3 — `QP-KERNEL-V2` kernel cutover

- **Depends on:** reservation (Gate 1) + events (Gate 2).
- **Scope:** shadow-first then gated cutover so Level 3/4/5 share one execution
  kernel.
- **Additional invariants:** exactly one broker POST path (no dual-POST); shadow
  mode has **no** side effects; the cutover flag defaults disabled and Level 5
  stays blocked (smoke `operator.status=blocked`,
  `operator.fallback=level5_flag_disabled`).
- **Authoritative evidence:** §2 backend + smoke green with the cutover flag both
  off (default) and, in tests only, on; a test asserting a single POST authority
  and no shadow side effects; zero P0/P1.
- **Fake-only:** all. **Manual:** none.

### Gate 4 — `QP-LEDGER-RUNTIME` authoritative ledger + continuous runtime

- **Depends on:** kernel cutover (Gate 3).
- **Scope:** authoritative position/cash ledger, reconciliation, continuous
  protective runtime.
- **Additional invariants:** projection replay equals broker-evidenced state;
  reconciliation is monotonic (fill IDs and quantities never duplicate or regress
  — `kis_paper_kill_contract.md` "Crash and replay"); continuous protection never
  depends on an in-memory-only reservation; kill fence still blocks the runtime.
- **Authoritative evidence:** §2 backend + smoke green; ledger-vs-broker parity
  test on the fixture corpus; a crash-restart test showing the runtime resumes
  from durable state without re-POSTing; zero P0/P1.
- **Fake-only:** development acceptance. **Manual:** begins to require real
  session-calendar and reconciliation-window evidence, folded into Gate P.

### Gate P — Paper operational readiness (terminal gate)

- **Depends on:** Gates 1–4 all `done` on fake-only evidence.
- **Scope:** authorize *operational* KIS paper use of reservation + kill +
  runtime on a real paper account.
- **Additional invariants:** all §1 invariants hold; live stays disabled; every
  order still flows reservation → risk gate → single POST authority →
  reconciliation.
- **Authoritative evidence (all required):**
  1. Every prior gate's fake-only evidence intact on the integration commit.
  2. **Manual KIS validation** with explicit user authority: `VTTC0084R`
     cancelable-order inquiry confirmed; real buying-power TR fields map to the
     reservation arithmetic; real session-calendar edges verified; one
     round-trip prepare→reserve→submit→reconcile on the paper account with no
     ambiguous repost.
  3. Recorded user authorization for the manual run (protocol §4/§6: trading and
     external-state changes are the only items escalated to the user).
- **Fake-only:** insufficient by itself. **Manual:** mandatory and gating.

## 5. Cross-gate integration requirements

- `QP-RM-00A → QP-RISK-RES-V1`: this matrix binds the reservation transaction,
  release evidence, crash points, and the schema-migration gate (see §4 Gate 1
  and the contract it references).
- `QP-RISK-RES-V1 → QP-EXEC-EVENTS-V1`: reservation lifecycle aggregate
  identities must be stable before any event dual-write begins.
- Every gate re-runs §2 on a clean integration commit; the mission lead is the
  sole mainline integrator and does not self-approve a safety-critical change
  (protocol §5, §6).

## 6. Acceptance ledger (to be filled at each gate's completion)

| Gate | Backend evidence | Smoke evidence | P0/P1 | Fake-only met | Manual met | Status |
|---|---|---|---|---|---|---|
| Gate 0 `QP-RM-00` | `819 passed, 2 skipped` | mock/live=false/blocked | 0 | yes | `VTTC0084R` pending | done |
| Gate 1 `QP-RISK-RES-V1` | `885 passed, 2 skipped` | mock/live=false/operator blocked; kill CLI blocked | 0/0 | yes | n/a for dev; Gate P pending | done |
| Gate 2 `QP-EXEC-EVENTS-V1` | pending | pending | pending | pending | n/a | proposed |
| Gate 3 `QP-KERNEL-V2` | pending | pending | pending | pending | n/a | proposed |
| Gate 4 `QP-LEDGER-RUNTIME` | pending | pending | pending | pending | folded into P | proposed |
| Gate P paper readiness | pending | pending | pending | required | **required** | proposed |

Only the mission lead updates this ledger at an integration checkpoint, with the
verbatim evidence lines from §2.
