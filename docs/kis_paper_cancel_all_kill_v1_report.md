# KIS Paper Cancel-All Kill v1 Completion Report

## Status

KIS 모의투자 전용 managed-order cancel-all kill을 완료했다. Kill은 durable SQLite fence를 먼저 기록하고, QuantPilot 주문과 KIS 조회 identity가 정확히 일치하는 미체결 주문만 한 번 취소한다. 수동·외부 주문, 불명확한 응답, 조회 충돌은 자동 처리하지 않고 `RECOVERY_REQUIRED`로 격리한다.

- Live trading enabled: **no**
- Validation broker: **mock smoke + fake KIS paper clients**
- Market orders enabled: **no**
- Position flattening: **not implemented**
- Account-wide/manual-order cancellation: **not implemented**

## Delivered behavior

- SQLite schema v9에 durable kill operation과 cancel request journal을 추가하고 v8 상태를 보존해 이행한다.
- Kill/cancel 상태 전이, revision CAS, account provenance, one-attempt claim을 강제한다.
- KIS paper 정정취소 가능주문 조회와 전량 취소 POST를 strict origin/response validation으로 추가했다.
- `python -m quantpilot.jobs.run_kis_paper_kill engage|release` CLI를 추가했다. 기본값은 `paper_kill_disabled`다.
- Engage는 prepared 주문을 broker POST 없이 종료하고 기존 주문을 먼저 조정한 뒤, business date·주문번호·branch/organization·종목·side·가격·누적체결·잔량이 모두 일치할 때만 취소한다.
- Cancel claim 이후 crash, timeout, 응답 영속 실패, business rejection은 자동 재POST하지 않는다. Daily-order evidence만 원 주문의 cancelled/filled 종결을 확정한다.
- `KILLING`, `KILLED`, `RECOVERY_REQUIRED`는 일반 KIS paper 세션의 startup, prepare, 최종 pre-POST를 차단한다.
- Release는 동일한 broker proof를 다시 수행하며 전략 승인, policy authority, autonomy flag를 재무장하지 않는다.

## Verification

- `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp`
  - **819 passed, 2 skipped in 7.22s**
- `python -m quantpilot.jobs.run_smoke`
  - broker=`mock`, live trading=`false`, Level 5=`level5_flag_disabled`
- `python -m quantpilot.jobs.run_kis_paper_kill engage` with default environment
  - `{"status":"blocked","reason_code":"paper_kill_disabled"}`
- Independent audit found no P0. Three initial P1 identity/transition/data-mode findings were fixed; scoped re-audit found no remaining P0/P1, and its final P2 transition/test request was also fixed.

## Known limitations and next step

- Automatic tests use no network, credentials, account identifiers, or real KIS POSTs.
- The official KIS sample publishes `TTTC0084R` for cancelable-order inquiry; this implementation uses the inferred paper counterpart `VTTC0084R`. It remains an explicit skipped/manual KIS paper validation item before operational use.
- Claude Code 2.1.205 was selected for the cross-vendor contract task but returned a session-limit error before producing output. A separate Codex GPT-5.4 agent supplied the committed contract, and another independent Codex agent performed the blocking audit; the reduced cross-vendor independence is recorded rather than hidden.
- The next kernel mission is durable atomic cash/exposure risk reservation. It must remain separate from this paper kill slice.
