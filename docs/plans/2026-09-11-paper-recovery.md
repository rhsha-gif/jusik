# Paper execution recovery

## Confirmed cause

A query-only paper diagnostic found a ten-zero `orgn_odno` sentinel. Every other
identity condition matched, but the reconciler and cancellation guard accepted
only blank or one zero. The broker reported a complete fill while the local
dispatch stayed accepted/pending. Private evidence remains outside the repository.
The regression failed before the fix and passes with the bounded ASCII-zero helper.

## Implementation and acceptance

- `reconcile` (or `reconcile --dry-run`) copies both ledgers into memory and previews
  the normal cumulative reconciliation path. The source databases are read-only.
- `reconcile --apply` requires paused paper provenance, matching account binding,
  trader/worker/reporter/account locks, a successful fresh preview, and SQLite
  backups of both ledgers. It repeats broker reads and uses the existing kernel
  and experiment fill transitions. Its transport permits only paper authentication,
  balance and daily-order reads. It cannot submit or cancel orders.
- Recovery must tolerate a crash after kernel commit but before experiment update.
  Repeating it must not duplicate fills, fees, cash or positions. After-hours
  attributed holdings are quarantined using the existing next-session exit rule.
- Reports expose reconciliation freshness, protection and notification states.
  Incomplete valuations are masked; pre-recovery chart samples stay on disk but
  are excluded from charts. Pending historical notifications are superseded by
  an idempotent correction report during apply; ambiguous deliveries are never requeued.
- Close baselines carry a session date and validity. Daily returns are unavailable
  when the preceding session's close is unverified; new entries remain blocked.
  A valid current valuation may still support the cumulative report and protection.
- Missing AI evidence stays unclaimed. Safe error classification and bounded
  transport-failure backoff retain query recovery and never retry an ambiguous POST.

## Operating sequence

Keep entries paused. Finish offline verification and independent safety review
before applying to the paper ledger. Identify only the intended runtime processes,
stop them, acquire ownership and retain uncertain request evidence. Apply recovery,
verify exact broker-derived costs and quantities, and replay to prove idempotency.
Restore owner Slack reporting and existing position protection while entries remain
paused. Closed-session holdings await the next permitted session; no test alone
constitutes actual liquidation evidence. Preserve existing dashboard changes.

## Verification

Use the dedicated runtime Python with `scripts/verify-paper.py` for the full backend
suite and smoke; run `tach check`. Independent review must approve the final diff.
Actual paper recovery, notification delivery and subsequent protection/exit evidence
are recorded separately from fixture tests. Live trading remains disabled.

## Independent review follow-up

The initial review blocked on F1/F2 (construction inventory and cancel audit
short-circuit), F3 (malformed unrelated dates), F4 (correction-report wiring),
F5 (pre-open quarantine), and F6 (stale daily baseline). All six have specific
fixes and regression coverage. The follow-up review passed all six findings.
A final independent review also passed the durable recovery marker: a crash
after projection commit still requires correction-report completion on retry,
and runtime startup is blocked until that transaction completes.

## Completion evidence (2026-09-11)

- Final full verification: **1,632 passed, 2 skipped**; smoke and `tach check`
  passed. The final focused recovery suite has 24 tests. One existing
  Starlette/AnyIO deprecation warning remains unrelated to recovery.
- Independent review receipts are linked from
  `paper-recovery-followup-review-result.json` and
  `paper-recovery-checkpoint-review-result.json`; both return PASS.
- The approved real paper-ledger recovery completed under exclusive locks with
  both SQLite backups. Broker fill evidence, modeled policy costs and cash
  conservation match. A second apply changed no orders or economic state and
  did not duplicate the correction report.
- Entries remain paused. Trader, AI worker and owner Slack reporter were
  restarted; liveness and completed reconciliation were observed. The correction
  report reached Slack API acceptance (`sent`); this does not assert user reading.
  One transient balance transport failure after restart recovered through the
  bounded read retry, with no additional orders.
- The read-only dashboard restarted successfully. Superseded chart observations
  remain on disk and are excluded from the displayed series.
- Carried holdings remain quarantined for the existing next-session limit exit.
  The installed XKRX calendar gives 2026-09-14 as the next session. A thread
  heartbeat checks actual exit evidence on trading weekdays and pauses itself
  after completion or an actionable failure. Actual liquidation is still pending.
- Private operational receipts stay outside the repository, under
  `%USERPROFILE%/.quantpilot/intraday-trial-20260911/`:
  `recovery-verification.json` and `recovery-runtime-verification.json`.
  Live trading, market orders and general autopilot remain disabled.
