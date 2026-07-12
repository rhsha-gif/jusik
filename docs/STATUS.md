# QuantPilot 현재 상태 (living document)

> 이 문서는 시점별 보고서가 아니라 **갱신형 현황판**입니다.
> 스테이지가 끝날 때마다 이 파일을 덮어쓰고, 상세 근거는 기존 `docs/*_report.md`에 남깁니다.
> 마지막 갱신: **2026-07-13**

## 목적 (한 줄)

개인용 AI 퀀트 자동운용 웹앱 — 라이브 트레이딩이 물리적으로 불가능한 모의 환경에서
Level 1~5 자율화 전 과정을 먼저 완성하고, 사람 승인 게이트를 거쳐서만 실거래로 확장한다.

## 단계별 상태

| 영역 | 상태 | 비고 |
|---|---|---|
| Level 1-2 신호→제안/모의체결 | ✅ 완료 | `/run` 제안 전용, `/mock-execute` MockBroker 체결 + 타이밍 판단 요약 |
| Level 3 승인 기반 오토파일럿 | ✅ 완료 (플래그 잠김) | 제안 생성→사용자 승인→제출 |
| Level 4 가드 오토파일럿 | ✅ 완료 (플래그 잠김) | 17단계 권한 체인 |
| Level 5 완전 자동 오퍼레이터 | ✅ 완료 (플래그 잠김) | 19단계 권한 체인 + 폴백 매트릭스 |
| 승인 티켓 레일 | ✅ 완료 | live 후보 티켓은 승인해도 `live_broker_unavailable` 차단 (의도됨) |
| 퀀트 엔진 (step 04~08) | ✅ 완료 | 공급자 연동 신호·최적화·배치 리스크 게이트·후보 랭킹·캘리브레이션 모델 |
| 데이터: fixture | ✅ 기본값 | |
| 데이터: local_historical (CSV) | ✅ 완료 + **실데이터 검증됨** | `fetch_krx_local_data` 잡으로 pykrx→CSV, 실 KRX 일봉으로 스모크 통과 |
| 데이터: external_historical (KIS) | 🟡 코드 완성, 실서버 미검증 | 가짜 transport로 단위 테스트됨; 실 키 확보 시 `RUN_KIS_MANUAL_INTEGRATION=1` 수동 테스트 준비됨 |
| KIS 토큰 발급 (`/oauth2/tokenP`) | ✅ 헬퍼 구현 (실서버 미검증) | `request_access_token(_from_env)` — 앱키/시크릿으로 발급; fake transport 단위 테스트 완료, 실 키 확보 시 수동 검증 |
| 뉴스 브리핑 (구상 ①) | 🟡 골격 완료 (fixture) | 읽기 전용 격리 경계 + `GET /api/briefing/daily`; 실제 수집기·프론트 페이지는 후속 |
| 실시간 일반 provider | ❌ 미구현, fail closed | 일반 provider factory는 realtime/paper 요청을 fixture로 fallback하지 않음 |
| KIS paper managed-order kill v1 | 🟡 schema v9 개발 검증 완료, 운영 미승인 | fake-client cancel journal/kill 검증 완료; `VTTC0084R`와 cancel POST는 Gate P 수동 검증 대기 |
| KIS paper atomic reservation v1 | ✅ schema v10 Gate 1 개발 완료 | baseline `5eb70a9`; Gate P buying-power/실서버 semantics는 미검증 |
| KIS paper canonical execution events v1 | ✅ schema v11 Gate 2 fake-only 개발 완료 | schema v10 row가 계속 authoritative; append-only shadow journal/replay parity 완료, 실제 KIS Gate P는 미검증 |
| Execution Kernel v2 계약 Gate | ✅ 계약·교차감사 완료, runtime 미착수 | Claude Code `c74a491`/`0bbec72` + Codex `6bfdb5d`/`2f0ab85`; 다음은 side-effect 없는 `QP-KER-010` 순수 모델, broker/store 권한 없음 |
| 라이브 트레이딩 | ❌ 의도적 미구현 | `docs/live_trading_enablement_checklist.md` 12항목 전부 사람 체크 필요 (현재 0/12) |

## 안전 불변식 (변경 금지 기본값)

`LIVE_TRADING_ENABLED=false` · `GUARDED_AUTOPILOT_ENABLED=false` ·
`FULLY_AUTOMATED_OPERATOR_ENABLED=false` · `MARKET_ORDERS_ENABLED=false` ·
`BROKER_MODE=mock` · `DATA_MODE=fixture`

## 최근 완료 (2026-07-11)

- **Roadmap Gate 1 — Atomic Risk Reservation v1 완료**: KIS paper의 현금,
  매도 가능수량, incremental long-gross를 schema v10 SQLite에서 주문과
  원자적으로 예약한다. 모호한 POST 결과와 부분체결은 reservation을
  유지하고, 확정 terminal에서만 같은 transaction으로 해제한다.
- Claude Code `claude-fable-5` 독립 감사와 QP-RES-A1 후속 재감사를 완료했다.
  잔여 P0=0/P1=0이며 A1은 closed다. 기준선 재검증은 `885 passed,
  2 skipped`, smoke `mock/live=false/operator blocked`, kill CLI
  `paper_kill_disabled`다. 실제 KIS 호출은 하지 않았다.
- Gate 2 canonical execution events가 후속 계보에서 완료됐다. Gate P/manual
  KIS 운영 검증은 별도 보류한다.

- Level 1-2 모의체결 + 승인 티켓 레일 + Execution 페이지 커밋 (`pytest 274 passed`, `vitest 20 passed`, `build ok`)
- `quantpilot/jobs/fetch_krx_local_data.py` 추가 — pykrx로 실 KRX 일봉을 받아
  `local_historical` CSV 생성·자체 검증. 삼성전자/SK하이닉스/NAVER 798봉으로
  `DATA_MODE=local_historical` 스모크 실행 성공 (signals=3, broker=mock, live 비활성 유지)
- **실 KRX 데이터 첫 백테스트 완료** — `backtest/replay.py` (no-lookahead 신호 리플레이)
  + `run_local_backtest` 잡 추가. 13개월 실데이터에서 삼성전자 매수→청산 왕복 체결
  (+5.32%, MDD 2.56%). 상세: `docs/local_data_backtest_validation_report.md`.
  발견: ① limit=종가 체결모델은 모멘텀 갭업에서 구조적으로 미체결
  (`--limit-buffer-bps`로 민감도 측정 가능) ② exit 시 잔량 ~0.5주 남는 엔진 특성
  ③ 대형주 3종목·13개월에 실행신호 5건 — 유니버스 확대 필요
- **거래비용 가정 확정 + 유니버스 확대 백테스트** — 비용 기준을 사용자 결정으로 확정:
  한투 실거래 오픈API·일반 개인 (`backtest/costs.py`, 수수료 1.40527bps/편도
  뱅키스 온라인, 매도세 20bps 2026년 KRX 세율, 양도세 없음). 유니버스 15종목·
  24개월(7,320봉)로 확대 재실행: buffer 0bps → 체결 19건 +1.11% (Sharpe 0.20),
  buffer 50bps → 체결 21건 +6.09% (MDD 3.58%, Sharpe 0.79). 관찰: 평균 노출도
  ~5-6%로 현금 유휴가 최대 병목, 체결모델 민감도 여전. 검증: pytest 294개
  중 293 passed·1 skipped (junit), run_smoke OK. 상세: 같은 리포트의
  "Universe expansion + confirmed cost basis run" 섹션
- **엔진 개선: exit 전량 청산 + 리스크 exit 시장가화** — exit 잔량 제거, 갭다운에서
  손절이 조용히 차단되던 문제 해결 (`exit_fill_policy=marketable_next_open` 기본,
  trim은 limit 유지). 정직해진 기준선: buffer 50bps +4.86% (Sharpe 0.65).
  노출도 분석: 병목은 신호 동시성(2년간 매수 11건, 동시 보유 최대 3종목)이며
  건당 비중(12%) 아님 — 비중 상향은 순수 리스크 스케일링 (Sharpe 불변)
- **전략 단위 승인 티켓 구현 (설계 §4.1)** — `StrategyApprovalTicket` + 백테스트
  증빙 fail-closed 검증 + 만료/대체/폐기 + 레벨별 활성화 게이트 +
  `/api/execution/strategy-tickets/*` 5개 엔드포인트. 검증: pytest 305개 중
  304 passed·1 skipped (junit), run_smoke OK, vitest 20 passed, build OK,
  openapi 타입 재생성. 원칙 추가: 전략 승인 = arming (즉시 매수 아님, §3.0)

- **strategy-studio 백엔드 구현 (설계 §4.3)** — 섹터/종목 입력 → `StrategyDraft`
  생성(fail-closed 유니버스 매칭) → 검증(현 데이터 모드에서 no-lookahead 리플레이
  백테스트, KIS 비용 기준) → 증빙 저장 → 티켓 생성 가능. 구상의 전체 경로
  (선택→초안→검증→승인→활성화 게이트)가 테스트로 엔드투엔드 검증됨.
  `/api/strategy-studio/*` 3개 엔드포인트. 검증: pytest 309개 중 308 passed·
  1 skipped (junit), run_smoke OK, vitest 20 passed, build OK. 남은 것:
  라우터 파일 분리(현재 execution.py에 동거)
- **전략 스튜디오 프론트 페이지** — `/studio` 라우트 + 사이드바 진입점.
  4단계 카드 플로우(초안 입력 → 초안 검토 → 백테스트 검증 리포트 → 전략 승인)
  + arming 원칙 문구. 브라우저 실검증 완료: 초안→검증→티켓→승인 전 과정을
  프리뷰에서 클릭으로 통과 (`approved`, `live_trading_enabled: false` 표시,
  콘솔 에러 0). vitest 20 passed, build OK. 참고: 8010의 구버전 API 서버는
  새 엔드포인트가 없어 404 — 재시작 필요 (검증은 8011 신규 서버로 수행)

- **DriftMonitor + 킬스위치 + 통지 인박스 (설계 §4.2·§4.5 완성)** — ① 실현 MDD >
  백테스트 MDD×1.5 시 티켓 자동 만료 (1.5배는 사람 확정 대기), 성과 자동 피드는
  Fill→OrderPlan.explanation 귀속으로 체결 시퀀스 PnL 적산
  (`/api/execution/strategy-performance/refresh`) ② 킬스위치 발동 시 무장 전략
  전부 revoke + 게이트 차단, 해제해도 재승인 필요 ③ 안전 이벤트가
  `OperatorNotification` 인박스에 자동 적재 (`GET /api/notifications`).
  검증: pytest 317개 중 316 passed·1 skipped (junit), smoke OK, vitest 20, build OK

- **§4.4 예산 게이트 + KIS 토큰 헬퍼 + 브리핑 골격(§4.7) + 부채 정리** —
  ① 전략 자본 예산이 매매 티켓 제출 경로에서 강제됨
  (`strategy_capital_budget_exceeded` 차단) ② `/oauth2/tokenP` 토큰 발급 헬퍼
  (fake transport 테스트, 실서버는 앱키 대기) ③ 읽기 전용 브리핑 경계
  (`GET /api/briefing/daily` + `/briefing` 페이지, import-guard 테스트로 격리 강제)
  ④ `MARKET_ORDERS_ENABLED` 판독 단일화. 검증: pytest 325개 중 324 passed·
  1 skipped (junit), smoke OK, vitest 20, build OK, 브리핑 페이지 브라우저 실검증

## 최근 완료 (2026-07-12, Roadmap Gate 2)

- **Atomic Risk Reservation v1 + Canonical Execution Events v1 mainline 통합 완료** —
  schema v10 reservation은 주문 prepare와 현금·매도수량·incremental long-gross를
  한 transaction으로 묶고, schema v11 append-only event journal은 dispatch/reservation/
  cancel의 모든 authoritative mutation과 같은 transaction으로 shadow dual-write한다.
- schema v10 row는 계속 source of truth다. reducer/replay는 broker를 호출하거나 row를
  수리하지 않으며, ambiguous POST no-retry와 단일 submission authority를 유지한다.
  Gate 2 accepted head `8eaf15a`와 Drift 기준선 계보를 main `2d34275`까지
  fast-forward한 뒤 재검증:
  상태/마이그레이션 `234 passed`, event/reducer/parity `103 passed`, Drift/env
  `84 passed`, 교차 기능 `44 passed`, 전체 backend `1046 passed, 2 skipped`.
  smoke는 broker mock/live=false/operator blocked, kill CLI는 `paper_kill_disabled`,
  OpenAPI 51 paths byte-exact + d.ts 동기화, frontend `23 passed` + build 성공.
- 실제 KIS paper buying-power/cancel TR, 계좌 round trip, 세션 캘린더는 Gate P 수동
  증거이며 이 fake-only 개발 완료에 포함되지 않는다.

## 최근 완료 (2026-07-13, Execution Kernel v2 계약 Gate)

- Level 1~5와 KIS paper의 공통 typed execution handoff를 위한
  `docs/execution_kernel_v2_contract.md` 및 전용 workboard를 승인했다.
- Claude Code `claude-fable-5`가 독립 소스 대조와 두 차례 계약 보완을 커밋했고,
  Codex가 후속 정적 순수성 결함을 별도 branch에서 닫았다. 최종 감사 결과는
  authorization/version P0/P1/P2=0, purity/decision P0/P1/P2=0,
  KIS/durable P0/P1=0/P2=1이다. 유일한 P2는 Gate 070 전 확정할 default-false
  KIS cutover flag/config ADR이며 QP-KER-010/015를 차단하지 않는다.
- 계약 Gate는 권한을 추가하지 않는다. runtime 파일, broker 호출, 저장소,
  reservation/event row, API/UI는 변경하지 않았고 safe defaults와 ambiguous POST
  no-retry를 유지한다. 다음 허용 작업은 `QP-KER-010` 두 파일의 순수 frozen
  evaluator와 unit tests뿐이다.

## 최근 완료 (2026-07-12, QP-DRIFT-DAILY)

- **DriftMonitor 성과 피드 개선 (1차, Claude)** — auto_feed가 KIS 실거래 비용
  (수수료 1.40527bps/편도 + 매도세 20bps) 반영 + 보유 포지션 일별 종가 재평가로
  체결 사이 드로다운 관측. `cost_basis`/`valuation` 후방호환 필드 추가.
  main `6721d24`. 임계 로직(×1.5)·zero-MDD fail-closed 무변경.
- **드리프트 성과 증거 fail-closed 결속 (2차, Codex 안전 검토 산출물)** —
  Codex가 `codex/qp-drift-daily-audit`에서 커밋 `f8bb594` 산출, Claude 리드가
  독립 diff 검토(P0/P1 없음) 후 `claude/qp-drift-daily-final-review`에 cherry-pick
  (`25182df`) → main에 fast-forward 통합 완료 (문서 후속 커밋 이전 기준 main = `2bbd833`).
  ① 정규화 분모를 누적 매수 명목가 → 첫 매수 제안 시점 계좌 equity
  (`account_equity_at_proposal`+`portfolio_snapshot_id` 동반 증거)로 교체해 백테스트
  MDD와 동일 기준 비교 ② 증거 열화 시 티켓 만료 대신 `strategy_activation_allowed`가
  사유 코드로 실행 차단 (`valuation_status`, 체결 워터마크·시세·캘린더 fingerprint)
  ③ KST 세션 마감 finality(15:30+30m), 미래 체결/재고 초과 매도 →
  `reconciliation_required`, provider 실패의 MDD 만료 우회 불가를 테스트로 고정.
  신규 테스트 31개, 제거 0. 검증(`25182df`): 백엔드 pytest 830개 중 828 passed·
  2 skipped (junit), 표적 82 passed, smoke OK (broker mock, live 비활성, operator
  blocked), openapi.json 바이트 단위 동기화(51 paths) + d.ts diff 없음,
  vitest 23 passed, build OK.
- **비차단 한계 (라이브 준비 주장 아님)**: ① capital_epoch/현금흐름 원장 부재 —
  증거 epoch 중 paper 자본 리셋·출금 금지(운영 규율) ② 수동 `SimpleKrxCalendar`는
  라이브 후보 권위 아님(휴장일 누락 자체는 미탐지, live-candidate 전 권위 소스 필요)
  ③ 동일 타임스탬프 체결 순서는 canonical event ordering 도입 대기.
  상세: `docs/qp_drift_daily_workboard.md` Known limits.

## 다음 단계 후보 (우선순위 제안)

> 제품 구상(대화형 전략 수립 → 전략 단위 승인 → 자동 운용)과의 정렬 설계 및
> 전체 로드맵: `docs/product_vision_alignment_design.md`

1. `QP-KER-010`: 승인된 Kernel v2 계약대로 broker/store/repository/audit/clock/env
   권한이 없는 frozen pure model/evaluator와 unit tests를 먼저 구현
2. durable outbox + account single-writer
3. authoritative execution/position/cash/NAV ledger + reconciliation break workflow
4. continuous OMS/risk/reconciliation runtime과 운영 health/metric
5. Gate P: 명시적 사용자 권한 아래 실제 KIS paper 수동 검증
6. 제품 backlog: 승인 기준 확정, `conflict_rule`/상관 예산, capital_epoch 원장,
   권위 있는 KRX 캘린더, 동일 타임스탬프 체결 ordering

## 사람 입력 대기

- [ ] KIS 오픈API 앱키/시크릿 (계좌 개설 필요; 모의투자 도메인 `openapivts.koreainvestment.com:29443`)
- [ ] Gate P 명시적 수동 권한: `VTTC0084R`, real buying-power field mapping,
  session-calendar 경계, 소량 paper round trip과 cancel 결과 확인
- [x] ~~Stage 03 거래비용·세금 가정치~~ → 확정: 한투 실거래 API·일반 개인 기준
  (`backtest/costs.py`; 뱅키스 온라인이 아닌 영업점 계좌면 `--fee-bps 14.7` 오버라이드)
- [ ] 슬리피지(현 5bps)·체결버퍼 가정치 — 연구용 가정 유지 중, 브로커 확인 전
- [ ] 승인 기준(acceptance thresholds) 확정 — **제안값 준비됨 (2026-07-06)**:
  `min_total_return ≥ 0` · `max_drawdown ≤ 0.10` · `min_simplified_sharpe ≥ 0.3` ·
  `min_filled_trades ≥ 15` (24개월 기준) · `max_turnover ≤ 4.0`.
  실측: buffer 50bps는 통과, buffer 0bps는 Sharpe 0.144로 탈락 —
  체결모델 가정이 승인 여부를 가르므로 확정 전 체결버퍼 가정 확인 권장.
  CLI: `run_local_backtest --min-total-return 0 --max-drawdown 0.10
  --min-simplified-sharpe 0.3 --min-filled-trades 15 --max-turnover 4.0`
- [ ] 워크포워드 윈도 정책
- [ ] 전략 승격(promotion) 승인자·증빙 형식 정책
- [ ] 라이브 체크리스트 12항목 (전부 사람 서명 필요)

## 검증 명령

```powershell
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp `
  --junitxml=.pytest_tmp/results.xml
python -m quantpilot.jobs.run_smoke
python -m quantpilot.jobs.run_kis_paper_kill engage
# 프론트 (quantpilot/apps/web): npm run test && npm run build
```
