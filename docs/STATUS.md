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

## 다음 단계 후보 (우선순위 제안)

1. 실 KRX 데이터 기반 백테스트 검증 (Stage 03 프로토콜을 local_historical로 재실행)
2. KIS 앱키 확보 시: 토큰 발급 헬퍼 구현 → `RUN_KIS_MANUAL_INTEGRATION=1` 수동 통합 테스트
3. 소소한 부채: `MARKET_ORDERS_ENABLED` 판독 중복 제거, `.env.example` 동기화 코드 가드

## 사람 입력 대기

- [ ] KIS 오픈API 앱키/시크릿 (계좌 개설 필요; 모의투자 도메인 `openapivts.koreainvestment.com:29443`)
- [ ] Stage 03 거래비용·슬리피지 가정치
- [ ] 워크포워드 윈도 정책
- [ ] 전략 승격(promotion) 승인자·증빙 형식 정책
- [ ] 라이브 체크리스트 12항목 (전부 사람 서명 필요)

## 검증 명령

```powershell
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp
python -m quantpilot.jobs.run_smoke
# 프론트 (quantpilot/apps/web): npm run test && npm run build
```
