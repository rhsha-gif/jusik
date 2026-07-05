# 제품 구상 ↔ 아키텍처 정렬 설계 문서

> 작성: 2026-07-06 · 상태: 설계 초안 (구현 착수 전)
> 사용자의 제품 구상 3단계를 기존 Level 1~5 아키텍처와 대조하고,
> 갭을 메우기 위한 설계 추가분과 우선순위 로드맵을 정의한다.
> 기존 계약은 `docs/contracts/operator_contracts.md`, 현황은 `docs/STATUS.md` 참조.

## 1. 사용자 구상 (원문 요지)

1. **뉴스 브리핑** — 주요 금융 뉴스·애널리스트 분석을 수집·선별해 간편 확인 (MVP 제외).
2. **대화형 전략 수립** — 사용자가 관심 섹터/종목을 말하면 시스템이 매매 전략을 수립.
3. **전략 승인 → 자동 운용** — 사용자는 **전략만** 승인하고, 매매·리밸런싱은 시스템이 자동 수행.

핵심 요구 두 가지:

- 매매 각각이 아닌 **전략 단위 승인** (per-trade approval 아님).
- **증권사식 리스크 시스템** — 일정 손실 이상 손절, 심하면 전략 폐기, 현금/주식 비중 조절, 포트폴리오 구성.

## 2. 현재 아키텍처와의 매핑

| 구상 | 기존 구현 | 상태 |
|---|---|---|
| ① 뉴스 브리핑 | 없음 | 신규 모듈 필요 (후순위 유지) |
| ② 대화형 전략 수립 | 레시피 파이프라인(가설→신호→리스크 매트릭스→백테스트 프로토콜 YAML) 존재. 웹앱 내 대화형 입구 **없음** | UI/API 레이어 신규 |
| ③ 전략 승인 → 자동 운용 | **Level 4 (Guarded Autopilot)와 동일 모델.** 플래그 잠김 상태로 구현 완료 | 신뢰 근거 축적 단계 |

구상의 "주목할 점"들도 계약 수준에 이미 반영되어 있다:

- **전략 단위 승인** → `StrategyRegistryEntry.allowed_execution_levels` + 승인 티켓 레일.
- **손절→전략 폐기** → `FallbackDecision` (Level 5→4→3→2→0 결정적 강등) + `StrategyRegistryEntry.status`의 `disabled`/`revoked`.
- **비중 조절·포트폴리오 구성** → step 05 포트폴리오 최적화 + step 06 배치 리스크 게이트.

**결론: 새 아키텍처는 불필요. 잠긴 레벨을 신뢰할 근거(실데이터 검증)와 4개의 접합부 설계가 부족할 뿐이다.**

Level 구분과 구상의 관계:

- Level 3 = 매매 **각각** 승인 (제안 생성 → 사용자 승인 → 제출).
- Level 4 = **전략(정책) 게이트** 안에서 자동 집행 — 구상 ③과 정확히 일치.
- Level 3 운용 기간은 건너뛸 낭비가 아니라 **전략 신뢰도를 검증하는 데이터 수집 구간**으로 사용한다
  (승격 사다리 §4.6 참조).

## 3. 구상에서 보완이 필요한 지점 (설계 원칙)

### 3.0 전략 승인 ≠ 즉시 매수 (arming 원칙)

사용자가 섹터/종목을 고르고 전략을 승인하는 것은 **매수 명령이 아니라 감시 상태
무장(arming)**이다. 진입·이익실현·손절 타이밍 판단은 전략(신호 분류기)의 몫이며,
승인된 전략은 셋업이 완성될 때만(`buy_ready`) 진입한다 — 승인 직후 며칠간
아무 매매가 없는 것이 정상 동작이다. 현재 신호 체계(`watch`/`buy_wait`/`buy_ready`/
`trim`/`exit`)가 이미 이 원칙을 구현하고 있으며, UI는 "승인됨 = 대기 중" 상태를
명시적으로 보여줘야 한다 (사용자 확인 사항, 2026-07-06).

### 3.1 LLM은 전략을 즉석 발명하지 않는다

안전 불변식: **LLM/RL 출력에서 직접 주문을 만들지 않는다** (기존 Forbidden Actions).
따라서 "섹터를 말하면 전략 수립"의 실제 흐름은:

```text
대화 입력 → LLM이 recipe 초안(YAML) 작성 → 자동 백테스트 검증
→ 통과 시 StrategyRegistry 등록(draft→validated_l3)
→ 이때에만 승인 버튼 활성화
```

백테스트 리드타임을 UX에서 숨기지 않는다. 검증 리포트가 첨부되지 않은
전략에는 승인 버튼을 렌더링하지 않는다 (승인의 요식화 방지).

### 3.2 전략 승인은 만료된다 (드리프트 대응)

승인 시점의 전제(변동성·유동성·시장 국면)는 변한다. 전략 승인에
**유효기간과 재승인 트리거**를 포함한다. 기존 `PolicyReviewRequest` /
`PolicyVersionChange` 계약 위에 얹는다.

재승인 트리거 (초안, 값은 사람 확정 필요):

- 실현 MDD가 백테스트 최악 MDD의 1.5배 도달
- 승인 후 N일 경과 (기본 30일)
- 전략이 참조하는 정책의 material change (`requires_review=True`)
- 폴백 발생으로 execution level이 강등된 채 M일 유지

### 3.3 체결모델·비용 가정이 수익률을 좌우한다

> **2026-07-06 확정**: 거래비용·세금 기준은 한투 실거래 오픈API + 일반 개인
> 투자자로 결정됨 — 수수료 0.0140527%/편도(뱅키스 온라인, API 추가 수수료 없음),
> 매도세 0.20%(2026년 KRX 세율, 코스피·코스닥 동일), 양도세 없음(소액주주 장내).
> 구현: `quantpilot/packages/core/backtest/costs.py`. 슬리피지 5bps는 연구 가정 유지.

첫 실데이터 백테스트(`docs/local_data_backtest_validation_report.md`)에서 확인된 사실:

- limit=종가 체결모델은 모멘텀 갭업에서 구조적으로 미체결 (`--limit-buffer-bps`로 민감도 측정 가능)
- exit 시 잔량 ~0.5주 잔존 (전량 청산 미보장)
- 대형주 3종목·13개월에 실행신호 5건 — 유니버스 확대 전에는 전략 평가 자체가 불가

**자동 운용(구상 ③)을 켜기 전 수수료·증권거래세·슬리피지 가정치 확정이 선행조건**이다
(현재 STATUS "사람 입력 대기" 항목).

### 3.4 다중 전략 자본 배분 (구상에 없던 요소)

전략을 2개 이상 승인하면 발생하는 문제:

- 같은 종목에 반대 신호 → 상계 규칙 필요
- 현금 배분 경쟁 → 전략별 자본 한도(capital budget) 필요
- 전략 간 상관 → correlation budget (리스크 매트릭스 확장)

`StrategyRegistryEntry.priority`는 존재하지만 자본 배분 계층은 없다. §4.4에서 설계.

### 3.5 통지·킬스위치 (전략 단위 승인의 유일한 실시간 안전장치)

폴백 매트릭스(자동 강등)는 있으나 **사용자 통지 채널과 전체 정지 버튼이 없다**.
전략 단위 승인 모델에서는 사용자가 개별 매매를 보지 않으므로,
"뭔가 잘못됐을 때 알게 되는 것"과 "즉시 세울 수 있는 것"이 필수다.

### 3.6 백테스트 ≠ 실전 — paper trading 중간 단계 필수

승격 사다리: **백테스트 통과 → KIS 모의투자에서 N주 무사고 운용 → 실거래 후보**.
KIS 토큰 발급(`/oauth2/tokenP`) 미구현·앱키 사람 입력 대기 상태이므로 이 단계는 외부 의존.

### 3.7 뉴스 모듈은 읽기 전용으로 시작한다

뉴스→매매 신호 연결은 look-ahead 바이어스·데이터 품질 문제로 최고 난도.
초기 버전은 **신호 파이프라인과 완전히 분리된 브리핑 대시보드**로만 도입하고,
전략 입력으로 승격하려면 별도 검증 단계(백테스트 프로토콜에 뉴스 피처 포함)를 거친다.

## 4. 설계 추가분 (신규 계약·컴포넌트)

### 4.1 StrategyApprovalTicket (승인 티켓 레일 확장)

> **구현됨 (2026-07-06)**: `StrategyApprovalTicket` 스키마(core/schemas.py),
> 백테스트 증빙 저장소(`backtest_results`), fail-closed 생성/승인/거절/폐기/
> 만료/대체(supersede), 레벨별 활성화 게이트(`strategy_activation_allowed`),
> `/api/execution/strategy-tickets/*` 5개 엔드포인트. 아래 초안과 필드가 다소
> 다른 부분은 구현이 기준 (status에 `rejected` 추가, `valid_days` 파라미터화).
> DriftMonitor(§4.2)는 `valid_until` 만료만 구현됨 — MDD 기반 트리거는 실적
> 추적(운용 성과 기록)이 선행되어야 하므로 후속 단계.

기존 `/api/execution/approval-tickets/*` 레일에 전략 단위 티켓 타입을 추가한다.

```python
class StrategyApprovalTicket(BaseModel):
    ticket_id: str
    ticket_type: Literal["strategy_activation"]
    strategy_id: str
    strategy_version: str
    spec_hash: str                      # 승인 대상 고정 (레시피 변조 방지)
    backtest_report_id: str             # 검증 리포트 없으면 티켓 생성 자체가 불가
    requested_execution_level: Literal["level_3", "level_4"]
    capital_budget_pct: float           # 이 전략에 허용된 총자본 대비 상한
    valid_until: datetime               # 승인 유효기간 (§3.2)
    reapproval_triggers: list[str]      # 재승인 트리거 코드 목록
    status: Literal["pending", "approved", "expired", "revoked", "superseded"]
    approved_at: datetime | None = None
    approved_by: str | None = None
```

규칙:

- `backtest_report_id`가 유효한 검증 리포트를 가리키지 않으면 생성 거부 (fail-closed).
- `valid_until` 경과 또는 재승인 트리거 발화 시 `expired`로 전이하고,
  해당 전략의 신규 주문 생성을 차단한다 (기보유 포지션 청산 주문은 허용 — 정책으로 명시).
- 티켓 승인 없이 `StrategyRegistryEntry.status`를 `validated_l4` 이상으로 올릴 수 없다.

### 4.2 재승인 트리거 평가기 (DriftMonitor)

> **평가기 구현됨 (2026-07-06)**: `StrategyPerformanceRecord`(실현 MDD·수익률
> 스냅샷) + 드리프트 체크 — 실현 MDD가 티켓 증빙 백테스트 MDD × 1.5(사람 확정
> 대기 상수)를 초과하면 티켓 자동 만료(`strategy_ticket_drift_expired` 감사) 후
> 활성화 게이트 차단. 증빙 MDD가 0이면 어떤 실현 낙폭도 트리거 발화 (fail-closed).
> 수동/잡 피드용 `POST /api/execution/strategy-performance` 제공.
>
> **자동 피드 구현됨 (2026-07-06)**: 스키마 변경 없이 해결 — 귀속 체인은
> Fill → OrderPlan.explanation(strategy_id/version)이 이미 보유.
> `compute_strategy_performance`가 체결 시퀀스 PnL 커브(체결가 마킹,
> 수수료 제외, research-only 근사)에서 누적 매수원금 대비 MDD·수익률을 적산,
> `run_strategy_performance_feed`가 체결 있는 전략 전부를 기록.
> `POST /api/execution/strategy-performance/refresh`로 트리거.
> 향후 개선: 일별 종가 재평가 커브(현재는 체결 시점 마킹), 수수료 반영.

운영자 사이클마다 활성 전략별로 §3.2 트리거를 평가하고, 발화 시:

1. `PolicyReviewRequest` 생성 (`blocks_automatic_submission=True`)
2. 해당 전략 티켓을 `expired`로 전이
3. 통지 이벤트 발행 (§4.5)

기존 19단계 권한 체인에 단계 추가가 아니라, 체인 앞단의 정책 검증 단계에서 함께 평가한다
(체인 구조 변경 최소화).

### 4.3 대화형 전략 수립 플로우 (구상 ②의 API/UI)

> **백엔드 구현됨 (2026-07-06)**: `StrategyDraft` 스키마 + `create_strategy_draft`
> (fail-closed 유니버스 매칭) + `validate_strategy_draft`(리플레이 백테스트,
> KIS 비용 기준, 증빙 자동 저장) + `/api/strategy-studio/draft`,
> `/drafts/{id}`, `/drafts/{id}/validate`. 초안→검증→티켓→승인→게이트
> 전체 경로가 테스트로 검증됨. **프론트 페이지도 구현됨** (`/studio`,
> 4단계 카드 플로우, 브라우저 실검증 완료). 남은 것: validate 통과 기준
> 연동(§7 사람 확정 대기), LLM 기반 레시피 다변화(현재는 기본 룰 레시피 고정).

```text
POST /api/strategy-studio/draft        # 입력: 관심 섹터/종목/제약 → recipe 초안 생성
POST /api/strategy-studio/validate     # 초안 → run_local_backtest 실행 → 검증 리포트
GET  /api/strategy-studio/drafts/{id}  # 초안 + 리포트 + 승인 가능 여부 조회
```

- draft 단계 출력은 **레시피 YAML + 근거 요약**이며 주문 계획을 포함하지 않는다.
- validate 통과 기준(초안): 왕복 체결 ≥ N건, 비용 반영 후 양의 기대수익,
  MDD ≤ 정책 한도 — 값은 사람 확정 필요.
- 프론트: 초안 카드 → 백테스트 진행 표시 → 리포트 첨부된 승인 화면 (승인 버튼은 §3.1 규칙).

### 4.4 CapitalAllocationPolicy (다중 전략 자본 배분)

> **예산 게이트 구현됨 (2026-07-06)**: `strategy_capital_budget_check` —
> 전략의 순 투입 원금(귀속 체결 매수−매도) + 신규 주문 노셔널이 활성 승인
> 티켓의 `capital_budget_pct` × 스냅샷 equity를 초과하면 매매 티켓 제출을
> `strategy_capital_budget_exceeded`로 차단 (approve-and-submit 경로에 삽입).
> 전략 티켓이 없는 전략은 per-trade 승인 레일이 통제하므로 게이트 통과.
> 아래 초안의 `conflict_rule`(동일 종목 반대 신호)·`correlation_budget`은
> 다중 전략 동시 운용이 실제로 시작될 때 구현.

```python
class CapitalAllocationPolicy(BaseModel):
    policy_id: str
    version: int
    total_investable_pct: float           # 총자산 중 주식 투입 상한 (현금 비중 조절)
    per_strategy_budget: dict[str, float] # strategy_id → capital_budget_pct
    conflict_rule: Literal["higher_priority_wins", "net_out", "block_both"]
    correlation_budget: float | None = None
```

- 배치 리스크 게이트(step 06) 앞단에서 평가: 전략별 예산 초과 주문 계획은 `block`.
- 같은 종목 반대 신호는 `conflict_rule`로 결정하고 `OperatorDecision`에 근거를 남긴다.
- 초기값은 `block_both` (가장 보수적) 권장.

### 4.5 통지 + 킬스위치

> **킬스위치 연결됨 (2026-07-06)**: 기존 정책 단위 킬스위치(`engage_kill_switch`)가
> 이제 무장된 전략 티켓 전부를 `kill_switch_engaged` 사유로 revoke하고,
> `strategy_activation_allowed`는 킬스위치 중 무조건 차단. 해제(명시적 확인 문구
> 필요) 후에도 전략은 재승인 전까지 비활성 — 원버튼 정지 요건 충족.
>
> **통지 인박스 구현됨 (2026-07-06)**: `OperatorNotification` 스키마+저장소,
> 드리프트 만료(critical)·유효기간 만료(warning)·티켓 폐기(킬스위치 시
> critical)·킬스위치 발동(critical) 이벤트가 자동 적재.
> `GET /api/notifications`(unacknowledged_only 필터) +
> `POST /api/notifications/{id}/acknowledge`. 외부 채널(이메일/푸시)
> 어댑터와 프론트 인박스 UI는 후속.

```python
class OperatorNotification(BaseModel):
    notification_id: str
    severity: Literal["info", "warning", "critical"]
    event_type: Literal[
        "fallback_triggered", "strategy_stopped_out", "strategy_revoked",
        "reapproval_required", "kill_switch_engaged", "run_failed",
    ]
    run_id: str | None
    strategy_id: str | None
    message: str
    created_at: datetime
    acknowledged_at: datetime | None = None
```

- 채널: 1차는 웹앱 인박스(폴링) — 외부 채널(이메일/푸시)은 어댑터 인터페이스만 정의하고 후순위.
- **킬스위치**: `POST /api/operator/kill-switch` — 모든 전략 티켓을 `revoked`로,
  진행 중 run을 `blocked`로, 이후 run 생성을 차단. 해제는 명시적 사람 조작 + 사유 기록.
- 킬스위치 상태는 안전 플래그와 동급으로 취급: 켜져 있으면 권한 체인 최상단에서 fail-closed.

### 4.6 승격 사다리 (전략 라이프사이클 확정)

```text
draft ─validate─> validated_l3 ─[Level 3 운용 N주 무사고 + 사람 승인]─> validated_l4
      └ 각 단계 승격마다 StrategyApprovalTicket 필요
validated_l4 ─[KIS 모의투자 M주 무사고 + 라이브 체크리스트 12항목]─> live 후보 (현재 의도적 차단 유지)
```

- "무사고" 정의(초안): 폴백 0건, 리스크 게이트 위반 0건, 실현 손실이 백테스트 예상 범위 내.
- 기존 `docs/operator_strategy_promotion_policy.md`와 정합성 유지하며 세부는 그 문서에서 확정.

### 4.7 뉴스 브리핑 모듈 (최후순위, 읽기 전용)

> **골격 구현됨 (2026-07-06)**: `quantpilot/services/briefing/` —
> `BriefingCard` + 결정적 fixture 카드 3종, `GET /api/briefing/daily`.
> 격리 원칙을 정적 import-guard 테스트로 강제 (신호·포트폴리오·집행·브로커
> 모듈 참조 시 테스트 실패). `signal_input: false` 상수 필드로 계약에 명시.
> 남은 것: 실제 수집기(웹/RSS) 어댑터, 프론트 브리핑 페이지.

- 별도 서비스 경계 (`quantpilot/services/briefing/` 예정) — 신호·주문 코드와 import 관계 금지.
- 산출물: 일간 브리핑 카드(출처·시각·요약·관련 종목 태그). 매매 신호 미생성.
- 전략 입력 승격은 별도 설계 문서에서 look-ahead 방지 프로토콜과 함께 다룬다.

## 5. 로드맵 (우선순위)

| 순위 | 작업 | 구상 대응 | 선행조건 |
|---|---|---|---|
| 1 | 백테스트 신뢰 기반: ~~유니버스 확대~~(15종목·24개월 완료), ~~비용·세금 가정 확정~~(한투 실거래 API·개인 기준, `backtest/costs.py`), exit 전량 청산 수정·체결모델 현실화 (잔여) | ③의 전제 | 슬리피지·체결버퍼는 연구 가정 유지 |
| 2 | `StrategyApprovalTicket` + DriftMonitor (§4.1–4.2) | ③ "전략만 승인" | 1 |
| 3 | 대화형 전략 수립 (strategy-studio API + 프론트) (§4.3) | ② | 1 (validate가 백테스트에 의존) |
| 4 | `CapitalAllocationPolicy` (§4.4) | ③ "비중 조절" | 2 |
| 5 | 통지 + 킬스위치 (§4.5) | ③ 안전장치 | 없음 (병행 가능) |
| 6 | KIS 모의투자 연동 (토큰 발급 헬퍼 → 수동 통합 테스트) | ③ "모의계좌 자동 체결" | 앱키 = 사람 입력 |
| 7 | 뉴스 브리핑 모듈 (§4.7) | ① | 없음 (최후순위 유지) |

## 6. 변경하지 않는 것

- 안전 불변식 기본값 전부 (`LIVE_TRADING_ENABLED=false` 외 5종).
- 주문 상태기계·리스크 게이트 우회 금지, LLM 출력→주문 직결 금지.
- Level 5 권한 체인 구조 (신규 검증은 기존 단계 내부에 합류).
- 승인 티켓 레일의 fail-closed 동작 (`live_broker_unavailable` 차단 포함).

## 7. 사람 확정 대기 (이 문서에서 추가된 것)

- [ ] 재승인 트리거 값 (MDD 배수, 유효기간 N일 등 §3.2)
- [ ] strategy-studio validate 통과 기준 (§4.3)
- [ ] 자본 배분 초기값: `total_investable_pct`, 전략별 예산, `conflict_rule` (§4.4)
- [ ] "무사고" 정의와 Level 3 운용 기간 N주 (§4.6)
- [ ] 만료된 전략의 기보유 포지션 처리 정책 — 청산 주문 허용 범위 (§4.1)
