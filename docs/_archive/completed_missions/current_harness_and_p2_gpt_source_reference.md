# QuantPilot 현재 하네스와 P2: GPT 소스 참고 안내서

> 작성일: 2026-07-17 KST
>
> 기본 코드 스냅샷: main `4241dc46e11454b8bd4c915e4ae52ca32570e9ef`
>
> 별도 후보 스냅샷: `claude/qp-ker015-expiry-hardening`의 `31ac3a11324b92fa634b29c404054176513d9446`
>
> 용도: GPT에게 QuantPilot 소스 파일을 첨부할 때 현재 실행 구조, 안전 경계, P2 현황과 파일 간 우선순위를 정확히 설명한다.

## 1. 이 문서를 먼저 읽혀야 하는 이유

QuantPilot에는 현재 세 가지 상태가 동시에 존재한다.

1. main `4241dc4`: 지금 저장소에 통합된 실행 기준이다.
2. branch `31ac3a1`: `QP-KER-015` 구현과 감사 P1 수정이 끝났지만 main에는 아직 통합되지 않은 후보다.
3. main working tree의 미커밋 파일: 다른 세션의 사용자 작업이며 설명·평가의 기준으로 사용하면 안 된다.

GPT가 이 세 상태를 섞으면 `paper_submission.py`의 현재 동작, `QP-KER-015`의 완료 여부, 테스트 증거를 모두
잘못 판단할 수 있다. 별도 표기가 없는 본문 설명은 main `4241dc4` 기준이다. `31ac3a1`의 내용은 항상
**미통합 후보**라고 표시한다.

## 2. 용어를 먼저 구분한다

| 용어 | 이 저장소에서의 뜻 | 현재 상태 |
|---|---|---|
| P0/P1/P2/P3 | 협업 감사 결함 심각도. P0/P1은 통합 차단, P2는 비차단 후속 과제, P3는 참고 사항이다. | 열린 P2는 8절 참조 |
| Roadmap Gate 2 | Canonical Order/Execution Events v1과 schema v11 shadow journal을 뜻한다. | main 통합 완료 |
| Gate P | 실제 KIS 모의투자 API의 cancel, buying-power, session-calendar 의미를 사람이 확인하는 수동 게이트다. | 미완료, 자동 실행 금지 |
| `QP-KER-0xx` | Execution Kernel v2 하위 작업 번호다. 예: `QP-KER-015`. | 7절 참조 |
| schema v9/v10/v11 | KIS paper SQLite 상태의 kill journal, risk reservation, canonical event journal 버전이다. | main 통합 완료 |

따라서 “P2가 열려 있다”와 “Roadmap Gate 2가 끝났다”는 동시에 참일 수 있다. `QP-KER-015`의 `015`도
감사 심각도가 아니라 작업 번호다.

## 3. 절대 바꾸지 않는 안전 전제

GPT에는 아래 값을 설계 취향이 아니라 프로젝트 권한 경계로 전달한다.

```text
LIVE_TRADING_ENABLED=false
GUARDED_AUTOPILOT_ENABLED=false
FULLY_AUTOMATED_OPERATOR_ENABLED=false
MARKET_ORDERS_ENABLED=false
BROKER_MODE=mock
DATA_MODE=fixture
```

- broker 자격증명, API key, 계좌 ID, 비밀, 개인 거래 정보를 소스나 답변에 넣지 않는다.
- 자동 테스트는 fixture와 fake client만 사용하고 인터넷이나 비밀을 요구하지 않는다.
- LLM/RL 출력은 주문을 직접 생성, 승인, 제출하지 못한다.
- pre-trade risk, kill switch, idempotency, order state machine, audit, reconciliation을 우회하지 않는다.
- 모호한 KIS POST 결과는 자동 재전송하지 않는다.
- UI의 경고 배너는 설명 계층이다. 실제 권한 차단은 백엔드 게이트가 담당한다.
- `live_trading_candidate`는 승인 티켓의 라벨일 뿐 live 주문 권한이 아니다. 현재는
  `live_broker_unavailable`에서 차단된다.

### 데이터 모드 주의

현재 `DataMode` 코드 enum은 다음 여섯 값이다.

```text
fixture
local_historical
external_historical
realtime_market_data
paper_trading
live_trading
```

프로젝트 규칙의 `live_trading_candidate`, `live_canary`, `live_scaled`는 현재 `DataMode` enum에 없다.
일부 티켓·전략 생명주기·Kernel 증거 모델에는 같은 문자열이 등장하지만, 이를 일반 provider가 지원하는
데이터 모드로 해석하면 안 된다. `realtime_market_data`, `paper_trading`, `live_trading`도 일반 provider
factory에서는 fixture로 후퇴하지 않고 fail closed한다.

## 4. 현재 하네스의 범위

여기서 “하네스”는 전체 웹앱이 아니라 `HarnessService`를 중심으로 정책, 데이터, 위험 검사, 권한 검사,
상태 전이, broker adapter, 감사·조정을 연결하는 안전 오케스트레이션을 뜻한다.

```text
정책/전략
  -> 데이터 provider와 품질 검사
  -> 유니버스/신호/포트폴리오 계획
  -> OrderPlan 제안
  -> 배치 + 개별 risk check
  -> 사람 승인 또는 Level 4/5 권한 검사
  -> 주문 상태 머신
  -> Mock/Paper/KIS-paper 경계
  -> 체결 적용, audit, reconciliation
```

### 4.1 핵심 구성요소

| 계층 | 현재 책임 | 핵심 소스 |
|---|---|---|
| 공통 모델 | 정책, 주문, 상태, broker/data mode | `quantpilot/packages/core/schemas.py` |
| 주 오케스트레이터 | Level 1~4, 승인 티켓, 위험 검사, 공통 제출 경계 | `quantpilot/packages/core/harness_service.py` |
| 권한·상태 전이 | Level 4/5 권한 체인, 주문 상태 머신, feature flags | `quantpilot/packages/core/execution/state_machine.py` |
| pre-trade risk | 개별 위험 검사, order type, 신선도·노출·손실 한도 | `quantpilot/packages/core/risk/gatekeeper.py` |
| Level 5 | bounded operator run, 전략 선택, fallback, run idempotency | `quantpilot/packages/core/operator/service.py` |
| professional cycle | 포지션 보호, retirement, 주간 rebalance lease, fill 귀속 | `quantpilot/packages/core/operator/professional_cycle.py` |
| KIS paper 제출 | durable prepare/claim/POST/replay/no-rePOST | `quantpilot/packages/core/execution/paper_submission.py` |
| 영속 상태 | schema v9~v11 SQLite, reservation/dispatch/event 원자 갱신 | `quantpilot/packages/db/sqlite_repositories.py` |
| canonical event | append-only event 모델과 read-only reducer | `quantpilot/packages/core/execution/events.py`, `reducer.py` |
| 순수 Kernel v2 | immutable evidence를 입력받는 결정적 read-only 평가기 | `quantpilot/packages/core/execution/kernel.py` |
| 실제 진입점 | smoke, KIS paper session/kill, FastAPI | `quantpilot/jobs/`, `quantpilot/services/api/` |

### 4.2 Level별 현재 실행 경로

| Level | 현재 동작 | 주문 권한 |
|---|---|---|
| Level 1~2 연구 | 신호, 포트폴리오 계획, 제안·리포트 생성 | 제출 없음 |
| Level 1~2 mock | 같은 판단을 batch risk 후 `MockBroker`에만 제출 | mock만 |
| Level 3 | 제안 생성 후 사람 승인, 제출 직전 risk 재검사 | 승인된 mock/paper 경로 |
| Level 4 | guarded flag, policy promotion, 시간·손실·신선도·멱등성 등 17단계 검사 | 기본 flag off |
| Level 5 | 한 번의 bounded operator cycle, 19단계 검사와 fallback | 기본 flag off, 기본 registry에서 no-op/blocked |
| KIS paper professional | 전용 CLI가 lease, reconciliation, 보호 주문, rebalance 순으로 실행 | 명시 환경 게이트와 KIS paper만 |

모든 현재 제출 경로는 `HarnessService.submit_order_plan()`으로 수렴한다. KIS paper에서 정상 주문 POST를
실행하는 유일한 호출 지점은 `DurablePaperSubmissionCoordinator`가
`KisPaperClient.place_limit_cash_order()`를 호출하는 곳이다. 이 권한을 다른 서비스나 Kernel에 복제하면 안 된다.

### 4.3 주문과 영속 상태

일반 API의 `RepositoryRegistry`는 프로세스 메모리 상태다. 재시작, 다중 worker 공유, 장기 감사 보존을
제공하지 않는다. KIS paper 전용 상태만 별도 SQLite store에 영속화된다.

```text
OrderPlan:
draft -> risk_checked -> proposed -> user_approved -> submitted
                                               -> accepted
                                               -> partially_filled -> filled
각 비종결 상태 -> modified/cancelled/rejected/expired/failed
```

- schema v9: managed-order kill/cancel journal
- schema v10: atomic cash, sell quantity, long-gross risk reservation
- schema v11: dispatch/reservation/cancel mutation과 같은 transaction에 기록하는 canonical shadow event
- schema v10의 authoritative row가 계속 source of truth다.
- schema v11 event journal과 reducer는 broker를 호출하거나 authoritative row를 고치지 않는다.
- `dispatch_claimed`나 `outcome_unknown`은 broker 결과가 모호하므로 자동 re-POST하지 않는다.

## 5. Execution Kernel v2의 정확한 현재 위치

main에는 `QP-KER-010`의 `kernel.py`와 테스트가 통합돼 있다. 이 Kernel은 frozen evidence bundle을 검증하고
허용/차단 결정을 결정적으로 계산하는 순수 모델이다.

그러나 현재 runtime 모듈에는 `kernel.py`를 import하거나 호출하는 지점이 없다. 즉:

- 현재 주문 판단·제출을 대체하지 않는다.
- broker, store, repository, audit, clock, environment, network 권한이 없다.
- shadow runner도 아직 없다.
- Kernel이 실제 제출 경로에 연결됐다고 말하면 틀리다.

| Gate | 내용 | 현재 상태 |
|---|---|---|
| `QP-KER-000A~000E` | 계약과 정적 purity gate | integrated |
| `QP-KER-010A/010B` | recursion containment 계약·hash-bound review | integrated |
| `QP-KER-010` | frozen pure evaluator와 테스트 | integrated |
| `QP-KER-015` | temporal/durable expiry, strategy version totality | branch에서 review, main 미통합 |
| `QP-KER-020` | default-off shadow runner | open |
| `QP-KER-030` | L1~L5 legacy-vs-kernel parity | open |
| `QP-KER-035` | common authoritative facade | open |
| `QP-KER-040/050/060A/060B` | Level 3/4/5 단계별 cutover | open |
| `QP-KER-065` | fake-KIS composition rehearsal | open |
| `QP-KER-070` | KIS paper cutover last | locked |
| `QP-KER-080` | legacy 경로 제거와 최종 감사 | open |

## 6. `QP-KER-015` 미통합 후보

`QP-KER-015`는 main 동작이 아니라 branch `claude/qp-ker015-expiry-hardening`의 후보 구현이다.

| 항목 | 값 |
|---|---|
| 구현 commit | `949aa7d650fa70003a9ffb329b8f04844cca4d30` |
| 감사 P1 수정 commit | `31ac3a11324b92fa634b29c404054176513d9446` |
| 상태 | 구현·P1 수정 완료, main 미통합, 독립 Codex 재감사와 통합 검증 대기 |
| schema 변화 | 없음. 기존 schema v11 payload와 event vocabulary 유지 |

후보가 구현하는 내용은 다음과 같다.

1. 현재 주문의 `OrderPlan.expires_at`까지 포함하는 legacy 제출 전후 시간 fence
2. `str.isdigit()`은 통과하지만 `int()`가 실패하는 Unicode digit을 예외 없이 거부하는 version totality
3. order/risk/quote/snapshot의 최소 deadline을 사용하는 durable expiry
4. restart와 invalid payload에서 값을 추측하지 않는 never-guess 처리
5. 외부 세션의 prepared row를 terminal 처리하기 전 dispatch와 reservation fence를 함께 재결속

branch에서 바뀌는 파일은 정확히 다음 일곱 개다.

```text
quantpilot/packages/core/execution/paper_submission.py
quantpilot/packages/core/execution/state_machine.py
quantpilot/packages/core/harness_service.py
quantpilot/packages/db/sqlite_repositories.py
quantpilot/tests/unit/test_external_paper_harness_integration.py
quantpilot/tests/unit/test_paper_submission_coordinator.py
quantpilot/tests/unit/test_strategy_version_matching.py  # branch-only new file
```

마지막 테스트 파일은 main `4241dc4`에는 없다. GPT에 제공하려면 반드시 `31ac3a1`에서 추출했다고 표시한다.
main working tree의 미커밋 `paper_submission.py`는 이 branch 작업과 겹치는 별도 초안이므로 참고 소스에서 제외한다.

## 7. Roadmap Gate 2는 무엇이 완료됐는가

Roadmap Gate 2는 `QP-KER-015`가 아니라 Canonical Order/Execution Events v1이다.

- schema v11 append-only shadow event가 main에 통합됐다.
- dispatch, reservation, cancel의 authoritative mutation과 event write가 같은 transaction에 묶인다.
- pure reducer는 event를 replay하지만 broker POST나 authoritative row repair 권한이 없다.
- 기존 schema v10 row가 계속 authoritative하다.
- 실제 KIS 의미 검증은 Gate P가 남아 있으므로 “운영 검증 완료”라고 말하면 안 된다.

## 8. 현재 열린 P2 인벤토리

아래는 역사적으로 발견된 모든 P2가 아니라, 현재 GPT가 설계·리뷰 때 알아야 할 열린 비차단 항목이다.

| # | 영역 | 열린 P2 | 영향과 현재 처리 |
|---:|---|---|---|
| 1 | Kernel Gate 070 | KIS cutover flag의 정확한 이름과 `EXECUTION_KERNEL_V2_MODE`/profile/data mode/unknown 값 조합표가 아직 ADR로 확정되지 않았다. | Gate 010/015는 막지 않지만 Gate 070은 `ready`로 갈 수 없다. 기본 false와 unknown fail-closed가 필수다. |
| 2 | Gate 010 AST digest | Python AST schema나 minor version이 바뀌면 semantic digest를 수동 재산출하고 독립 재검토해야 한다. 자동 갱신은 금지다. | 한 번 고쳐 끝내는 결함이 아니라 상시 운영 제약이다. `REVIEWED_KERNEL_AST_SHA256`은 Kernel 테스트 파일에 있다. |
| 3 | `QP-RES-A2` | held sell reservation을 guardrail에 투영할 때 reservation 자체에 `policy_id`가 없어 여러 policy가 한 store를 공유하면 다른 policy의 수량도 보수적으로 합산될 수 있다. | 초과 주문을 허용하지 않고 over-block만 일으킨다. 현재 one-policy-per-paper-store 가정 아래 비차단이다. |
| 4 | `QP-KER-015` operator clock | `OperatorService._submit_proposals()`가 `authorization_time`을 한 번 잡아 반복문의 모든 proposal에 전달한다. 앞선 반복에 소비된 시간이 이후 proposal의 entry baseline에 반영되지 않는다. | 별도 후속 티켓 권고. `QP-KER-015`가 새로 만든 결함은 아니다. |
| 5 | `QP-KER-015` 감사 추적성 | `949aa7d` 감사가 P2 두 건을 셌지만 committed history에는 두 번째 P2의 정체가 남아 있지 않다. | 통합 전 감사 결과를 committed artifact에 결속해야 한다. 증거 품질 P2다. |
| 6 | roadmap handoff 문서 | 기존 handoff 독립 리뷰의 P2-1~3: off-repo 감사 증거, branch-only 파일 표기, 통합 계보 표현 보강이다. | 실행 안전 결함이 아니라 문서 추적성·명료성 후속이다. |

다음 항목은 열린 P2로 다시 세지 않는다.

- `QP-RES-A1`: 수정·재감사로 closed
- `QP-RES-A3`: P2가 아니라 P3
- `QP-KER-000C~000E`, `QP-KER-010B`의 과거 P2: 계약·테스트에서 closed
- Gate 2 리뷰의 P2 보정: binding contract에 반영돼 closed
- `QP-KER-015`: P2 심각도가 아니라 review 상태의 작업 gate

## 9. GPT에 첨부할 소스 묶음

### 9.1 최소 묶음

대화 컨텍스트가 제한되면 다음 순서로 첨부한다.

| 순서 | 파일 | 목적 |
|---:|---|---|
| 1 | 이 문서 | snapshot, 용어, P2, 읽기 규칙 |
| 2 | `AGENTS.md`, `.env.example` | 절대 안전 경계와 기본값 |
| 3 | `docs/STATUS.md` | living status. 단, 2026-07-13 이후 Kernel 상태는 본 문서가 우선 |
| 4 | `docs/execution_kernel_v2_contract.md` | Kernel 허용 범위와 gate ladder |
| 5 | `quantpilot/packages/core/schemas.py` | 공통 도메인 모델 |
| 6 | `quantpilot/packages/core/harness_service.py` | 현재 공통 오케스트레이션·제출 경계 |
| 7 | `quantpilot/packages/core/execution/state_machine.py` | 권한 체인과 상태 전이 |
| 8 | `quantpilot/packages/core/risk/gatekeeper.py` | pre-trade risk |
| 9 | `quantpilot/packages/core/execution/paper_submission.py` | durable KIS paper 단일 POST 경계 |
| 10 | `quantpilot/packages/core/execution/kernel.py` | 아직 runtime에 연결되지 않은 pure evaluator |
| 11 | `quantpilot/packages/db/sqlite_repositories.py` | schema v9~v11 authoritative persistence |
| 12 | `quantpilot/packages/core/operator/service.py` | Level 5와 열린 clock P2 |
| 13 | 핵심 테스트 | 문서보다 강한 동작 증거 |

### 9.2 전체 하네스 묶음

최소 묶음에 다음을 추가한다.

```text
docs/current_project_workflow.md
docs/roadmap_continuation_handoff.md
docs/contracts/atomic_risk_reservation_v1.md
docs/contracts/operator_contracts.md
docs/atomic_risk_reservation_v1_claude_audit.md
quantpilot/packages/core/operator/professional_cycle.py
quantpilot/packages/core/operator/position_ledger.py
quantpilot/packages/core/execution/paper_reconciliation.py
quantpilot/packages/core/execution/paper_reconciliation_apply.py
quantpilot/packages/core/execution/events.py
quantpilot/packages/core/execution/reducer.py
quantpilot/packages/db/repositories.py
quantpilot/packages/core/kis_paper.py
quantpilot/packages/brokers/mock_broker.py
quantpilot/packages/brokers/paper_broker.py
quantpilot/packages/brokers/kis_paper.py
quantpilot/packages/core/data/mode.py
quantpilot/packages/core/data/providers.py
quantpilot/packages/core/data/quality.py
quantpilot/jobs/run_smoke.py
quantpilot/jobs/run_kis_paper_session.py
quantpilot/jobs/run_kis_paper_kill.py
quantpilot/services/api/main.py
quantpilot/services/api/dependencies.py
quantpilot/services/api/routers/
openapi.json
```

### 9.3 동작을 결속하는 테스트

```text
quantpilot/tests/unit/test_execution_kernel_v2.py
quantpilot/tests/unit/test_paper_submission_coordinator.py
quantpilot/tests/unit/test_external_paper_harness_integration.py
quantpilot/tests/unit/test_paper_dispatch_persistence.py
quantpilot/tests/unit/test_paper_risk_reservation_model.py
quantpilot/tests/unit/test_paper_reconciliation.py
quantpilot/tests/unit/test_paper_execution_shadow_parity.py
quantpilot/tests/unit/test_approval_tickets.py
quantpilot/tests/unit/test_harness_batch_risk.py
quantpilot/tests/integration/test_level3_flow.py
quantpilot/tests/integration/test_level4_guarded_flow.py
quantpilot/tests/integration/test_level5_operator_run_once.py
```

`QP-KER-015`을 물을 때만 `31ac3a1`의 일곱 파일이나 두 commit diff를 별도 묶음으로 추가하고, 파일명 앞이나
프롬프트에 `accepted-unmerged / not main runtime`이라고 표시한다.

## 10. GPT에 함께 줄 프롬프트

다음 문구를 소스 파일과 함께 전달하면 된다.

```text
첨부한 파일은 QuantPilot fixture-first 트레이딩 안전 하네스의 참고 소스다.

1. 먼저 current_harness_and_p2_gpt_source_reference.md를 읽고 snapshot 우선순위를 지켜라.
2. 기본 실행 기준은 main 4241dc46e11454b8bd4c915e4ae52ca32570e9ef이다.
3. 31ac3a1 파일은 QP-KER-015의 accepted-unmerged 후보이며 현재 main 동작으로 취급하지 마라.
4. 미커밋 working-tree 변경은 근거에서 제외하라.
5. P2 심각도, Roadmap Gate 2, Gate P, QP-KER 작업 번호를 구분하라.
6. LIVE_TRADING_ENABLED=false, GUARDED_AUTOPILOT_ENABLED=false,
   FULLY_AUTOMATED_OPERATOR_ENABLED=false, MARKET_ORDERS_ENABLED=false,
   BROKER_MODE=mock을 불변식으로 유지하라.
7. LLM/RL에 broker 주문 생성·승인·제출 권한을 주지 마라.
8. risk check, kill switch, idempotency, state machine, audit, reconciliation,
   reservation, fencing, ambiguous-POST no-retry를 우회하는 제안을 하지 마라.
9. 코드, 테스트, binding contract, 상태 문서가 충돌하면 이 순서와 명시된 snapshot을 기준으로
   충돌을 보고하고 임의로 완료 상태를 추론하지 마라.
10. 답변에는 현재 구현, 미통합 후보, 열린 P2, 수동 Gate P를 별도 항목으로 표시하라.
```

## 11. GPT가 해서는 안 되는 주장

- `QP-KER-015`가 main에 통합됐거나 완료됐다고 말하지 않는다.
- branch-only `test_strategy_version_matching.py`가 main에 있다고 말하지 않는다.
- `kernel.py`가 현재 runtime 주문 결정을 수행한다고 말하지 않는다.
- KIS paper kill/reservation/events가 실제 broker에서 운영 검증됐다고 말하지 않는다.
- Gate 2 완료를 P2 결함 0건이라는 뜻으로 해석하지 않는다.
- `live_trading_candidate`를 live 거래 권한으로 해석하지 않는다.
- 코드가 프로젝트 규칙에 적힌 8개 데이터 모드를 모두 구현했다고 말하지 않는다.
- 브리핑을 신호 입력으로, UI 배너를 권한 enforcement로 해석하지 않는다.
- snapshot을 적지 않은 전체 테스트 수치를 현재 수치로 재사용하지 않는다.
- 미커밋 `paper_submission.py`나 `docs/qp_ker015_codex_handoff.md`를 committed source of truth로 인용하지 않는다.

## 12. 근거 우선순위와 검증 메모

같은 주장에 여러 근거가 있으면 다음 순서를 쓴다.

1. 명시된 commit의 실제 코드
2. 같은 snapshot의 동작 결속 테스트
3. binding contract와 활성 workboard
4. living status와 handoff
5. 시점별 완료 보고서
6. 미커밋 working-tree 파일은 근거에서 제외

이 문서는 main `4241dc4`의 소스·테스트·계약 경로와 `31ac3a1`의 branch-only 경로를 각각 존재 확인했다.
문서 작성 과정에서는 broker, KIS, network, secret을 사용하지 않았다. 문서 전용 변경이므로 backend pytest와
smoke의 과거 수치를 현재 실행 결과로 재주장하지 않는다.

독립 소스 감사 원문은 `docs/claude/harness_p2_source_inventory_review.md`에 있다.
