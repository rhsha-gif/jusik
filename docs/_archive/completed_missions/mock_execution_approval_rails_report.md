# QuantPilot Mock Execution + Approval Alert Rails Report

Date: 2026-06-15

## Implemented

- Preserved `POST /api/level-1-2/run` as suggestion-only; it still creates no `OrderPlan`, broker order, or fill.
- Added `POST /api/level-1-2/mock-execute` for fixture Level 1-2 mock execution through `MockBroker`.
- Added approval-ticket rails:
  - `POST /api/execution/approval-tickets/generate`
  - `GET /api/execution/approval-tickets/pending`
  - `POST /api/execution/approval-tickets/{ticket_id}/approve-and-submit`
  - `POST /api/execution/approval-tickets/{ticket_id}/reject`
- Added frontend controls for “제안만 실행” vs “모의체결 실행”.
- Added an approval-alert page with pending tickets, approve/reject actions, browser notification permission request, and in-app fallback.

## Update — program-trading reframe (same day)

Level 1-2 is now presented as **program auto-trading first**, not suggestion-first,
to match the intended autonomy: the program judges trade timing and executes on the
mock account without per-order human approval. Real trading keeps the human gate.

- Harness: `run_level_1_2_mock_execution` now returns an additive `auto_execution`
  summary (`_build_auto_execution_summary`) with per-symbol timing decisions
  (`executed`/`blocked`) and reconciled fill totals. No new side effects; safety
  paths untouched.
- Frontend:
  - `Run` page leads with “모의 자동매매 실행” (primary) over “제안만 보기 (체결 안 함)”,
    and renders a “프로그램 자동 매매 판단” panel with the timing-decision table.
  - App shell gains a global approval-alert bell (pending-ticket count, pulses when
    `> 0`) so the “trade timing detected” alarm is visible from any page.
  - `Execution` page gains a 타이밍 포착 → 승인 알림 → 사용자 승인 → 시스템 집행 stepper and
    copy clarifying the system (not the user) submits after approval.
  - `Overview` adds a two-mode card (모의 자동매매 vs 실거래 알림→승인→시스템 집행).
- Tests: added `test_level_1_2_mock_execution_reports_program_trade_timing_decisions`;
  updated `run-mock-execute.test.tsx` for the new label + `auto_execution` payload.
- Validation: `pytest quantpilot/tests` → 274 passed, 1 skipped; web `npm run test` →
  20 passed; `npm run build` ok; live browser E2E confirmed auto-trade decisions,
  fills, and the active alert bell, all with `live_trading_enabled=false`, `broker=mock`.

## Safety Invariants

- Live trading remains disabled: `live_trading_enabled=false`.
- No live broker mode or credentials were added.
- Level 1-2 mock execution requires `BrokerMode.mock`.
- `live_trading_candidate` ticket approval records user intent but blocks before broker submission with `live_broker_unavailable`.
- Market orders remain disabled by default through the existing risk gates.
- Order submission still runs through `HarnessService.submit_order_plan`, preserving fresh risk checks, idempotency, state transitions, broker adapter boundaries, and audit logging.

## Validation

- Targeted backend tests:
  - `python -m pytest quantpilot/tests/unit/test_level_1_2.py quantpilot/tests/unit/test_approval_tickets.py quantpilot/tests/integration/test_level3_flow.py`
  - Result: passed.
- Full backend tests:
  - `python -m pytest quantpilot/tests`
  - Result: failed only on Windows temp permission for `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan`.
  - `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
  - Result: `273 passed, 1 skipped`.
- Smoke:
  - `python -m quantpilot.jobs.run_smoke`
  - Result: passed; `broker=mock`, `live_trading_enabled=false`, Level 5 `fallback=level5_flag_disabled`.
- Frontend build:
  - `npm run build` from `quantpilot/apps/web`
  - Result: passed.
- Frontend tests:
  - `npm run test` from `quantpilot/apps/web`
  - Result: `20 passed`.

## Remaining Out Of Scope

- KIS paper-trading API integration.
- Any credentialed broker connector.
- Live broker submission.
- External notification channels such as Slack, SMS, or email.
