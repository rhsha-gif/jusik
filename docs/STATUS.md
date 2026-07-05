# QuantPilot 현재 상태 (living document)

> 이 문서는 시점별 보고서가 아니라 **갱신형 현황판**입니다.
> 스테이지가 끝날 때마다 이 파일을 덮어쓰고, 상세 근거는 기존 `docs/*_report.md`에 남깁니다.
> 마지막 갱신: **2026-07-06**

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
| KIS 토큰 발급 (`/oauth2/tokenP`) | ❌ 미구현 | 현재 `KIS_ACCESS_TOKEN` 수동 주입 가정 (24h 만료) |
| 실시간 시세 / paper trading | ❌ 범위 밖 | 라이브 체크리스트 선행조건 |
| 라이브 트레이딩 | ❌ 의도적 미구현 | `docs/live_trading_enablement_checklist.md` 12항목 전부 사람 체크 필요 (현재 0/12) |

## 안전 불변식 (변경 금지 기본값)

`LIVE_TRADING_ENABLED=false` · `GUARDED_AUTOPILOT_ENABLED=false` ·
`FULLY_AUTOMATED_OPERATOR_ENABLED=false` · `MARKET_ORDERS_ENABLED=false` ·
`BROKER_MODE=mock` · `DATA_MODE=fixture`

## 최근 완료 (2026-07-06)

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

## 다음 단계 후보 (우선순위 제안)

> 제품 구상(대화형 전략 수립 → 전략 단위 승인 → 자동 운용)과의 정렬 설계 및
> 전체 로드맵: `docs/product_vision_alignment_design.md`

1. 승인 기준(acceptance thresholds) 확정 — 표본이 생겼으므로 이제 논의 가능 (사람 입력)
2. DriftMonitor 성과 피드 배선 — 평가기는 ✅ 완료 (실현 MDD > 백테스트 MDD×1.5 시
   티켓 자동 만료 + 감사 로그; 1.5배 값은 사람 확정 대기). 남은 것: mock 체결/
   operator run 결과에서 `StrategyPerformanceRecord` 자동 생성하는 피드
3. 라우터 분리 부채: strategy-studio·strategy-tickets 엔드포인트가 execution.py에 동거
   (main.py에 타 세션 미커밋 CORS 변경 있음 — 커밋 전 주의)
4. KIS 앱키 확보 시: 토큰 발급 헬퍼 구현 → `RUN_KIS_MANUAL_INTEGRATION=1` 수동 통합 테스트
5. 소소한 부채: `MARKET_ORDERS_ENABLED` 판독 중복 제거, `.env.example` 동기화 코드 가드

## 사람 입력 대기

- [ ] KIS 오픈API 앱키/시크릿 (계좌 개설 필요; 모의투자 도메인 `openapivts.koreainvestment.com:29443`)
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
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp
python -m quantpilot.jobs.run_smoke
# 프론트 (quantpilot/apps/web): npm run test && npm run build
```
