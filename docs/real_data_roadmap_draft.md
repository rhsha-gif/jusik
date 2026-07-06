# Real-Data Usage Roadmap — DRAFT (미확정 / 컨펌 대기)

> **상태: 임시 제안일 뿐 확정된 계획이 아님.**
> 이 문서의 Step 16~21 번호와 범위는 **제안(draft)**이며, 사용자 컨펌 전까지
> 확정 step으로 취급하지 않는다. STATUS.md의 확정 step 표에는 반영하지 않는다.
> 컨펌 후에야 STATUS.md에 정식 step으로 승격한다.
>
> 작성: 2026-07-06 (session claude/status-md-continuation-zshmzd, Step 13 완료 시점).

## 목적 / "실 사용"의 정의

`docs/live_trading_enablement_checklist.md`에 따라 **실거래(live)는 이 레포의
구현 대상이 아니며 12개 인간 승인 게이트로만 열린다**(AI는 절대 체크 불가).
따라서 코드로 도달 가능한 상한은 **실데이터 기반 무인 페이퍼 트레이딩**이다.
이는 동시에 향후 인간이 live를 결정할 때의 선행조건(체크리스트 4·6·8번)이기도 하다.

## 현재 갭 (Step 13 시점)

| 영역 | 현재 상태 | 실(페이퍼) 사용에 필요한 것 |
| --- | --- | --- |
| 과거 실데이터 | KIS 일봉 HTTP 클라이언트 배선됨 | — |
| 신호/검증 | 캘리브레이션 + walk-forward | 승격 증거·진단 (확정 Step 14·15) |
| 실시간 호가 | OHLCV/일봉만, 실시간 현재가 피드 없음 | staleness 보장 실시간 소스 |
| 페이퍼 브로커 | `MockBroker` 상속, 즉시 합성 체결 | 실제 모의계좌 왕복(비동기 체결/거부) |
| 정합성 대사 | 없음 | 브로커 보고 상태 vs 내부 상태 대사 |
| 세션 스케줄러 | 수동 실행만 | 거래소 캘린더 연동 무인 실행 |
| 관측/알림·드릴 | 없음 | 헬스/메트릭·발산 알림·킬스위치 드릴 |

## 제안 상한: (확정된) Step 14~15 이후 **Step 16~21 추가 시 무인 페이퍼 운영 도달**

Step 20에서 무인 페이퍼 운영이 서고, Step 21(트랙레코드 평가)에서 신뢰 가능 상태.
그 이후 live까지 남는 것은 전부 인간·법률·운영 게이트이지 코드가 아님.

## 제안 Step 설계 (모두 fixture-first, 단일 제출 경로·default-off·fail-closed 준수)

### (확정 예정) Step 14 — 승격 증거 통합
- Step 09 `PromotionEvidenceReport`를 전략 라이프사이클 레지스트리에 연결.
- 승격은 인간 게이트 유지, `promotion_allowed=false` 기본, AI 자체 승격 불가.

### (확정 예정) Step 15 — 진단 플레이스홀더 실제 구현
- `DiagnosticPlaceholder` 뒤 PBO·DSR 결정론적 구현. 임계 미달 전략은 증거 fail.

### (제안) Step 16 — 실시간 호가 프로바이더 (fixture-first)
- `RealtimeQuoteProvider` 프로토콜 + KIS `inquire-price`(현재가) 클라이언트.
- staleness 보장(호가 나이 초과 시 fail-closed), `DATA_MODE` 게이팅, 테스트용 fake.
- 자문용. 결정 시점 시계는 wall-clock. 실계좌·비밀 불요(테스트).
- 근거: 체크리스트 #4.

### (제안) Step 17 — 페이퍼 브로커 비동기 어댑터 (fixture-first)
- 비동기 수명주기(submitted→accepted→partial→filled/rejected) 모델링.
- 교체 가능한 transport 뒤 KIS 모의투자 주문 어댑터 셸(비밀 필요, 테스트 미호출)
  + fake transport.
- `submit_order_plan` 단일 제출 경로 유지, 시장가 비활성 유지, 자격증명 노출 금지.

### (제안) Step 18 — 정합성 대사 루프
- 브로커 보고 포지션/체결 vs 내부 리포지토리 상태 비교, 발산 시 감사 + fallback.
- 발산은 fail-closed(신규 매수 중단). 신규 감사 액션은 `AUDIT_EVENT_ACTIONS` 선등록.
- 근거: 체크리스트 #6.

### (제안) Step 19 — 세션 인식 스케줄러 / 운영 오케스트레이션
- 거래소 캘린더(휴장일·정지 확장) + 시장 세션 러너가 정의된 시각에 operator 실행.
- 세션 외/킬스위치 시 fail-closed, 모든 Level 5 게이트 통과 필수, dry_run 우선.
- 근거: 체크리스트 #5.

### (제안) Step 20 — 관측성 & 운영 드릴
- 헬스/메트릭 엔드포인트, 발산·stale·킬스위치·fallback 알림 훅.
- 자동 킬스위치 드릴 테스트(정책 킬스위치·`OPERATOR_KILL_SWITCH`·프로세스 종료 각각 검증).
- 근거: 체크리스트 #10.

### (제안) Step 21 — 페이퍼 트랙레코드 평가 하니스
- 사전 정의 평가창에서 특정 전략 레지스트리의 페이퍼 성과를 결정론적 집계·리포트.
- 리포트는 결정론적(LLM 불요), 승격/실거래 권한 없음.
- 근거: 체크리스트 #8 — 이후 인간의 live 결정 입력물.

## 컨펌 시 결정할 사항

- 번호 확정(16~21 그대로 갈지, 순서/분할 조정할지).
- Step 16과 17의 우선순위(실시간 호가 먼저 vs 페이퍼 브로커 왕복 먼저).
- KIS 모의투자 연동을 실제로 붙일지, 어댑터 셸까지만 두고 셸은 미배선으로 남길지.
- 상한을 Step 21로 둘지, Step 20(무인 운영)까지만으로 볼지.
