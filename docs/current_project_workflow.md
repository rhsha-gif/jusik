# QuantPilot 현재 워크플로우와 작동 방식

> 코드 기준: `5eb70a9db32b59d41ebd1e7878df5c2314621554` (`2026-07-11` Gate 1 통합 검증)
>
> 이 문서는 미래 설계가 아니라 **현재 저장소에서 실행되는 경로**를 설명한다. 보고서의 완료 주장보다 코드와
> 테스트를 우선했고, 아직 연결되지 않았거나 기본 설정에서 잠긴 기능은 그 사실을 따로 표시한다. QuantPilot은
> fixture-first 안전 하네스이며 live 주문 경로는 구현·활성화되어 있지 않다.

조사 시작 시 메인 working tree에는 CORS 설정과 API router 분리에 관한 미커밋 사용자 변경이 있었다. 이 문서는
재현 가능한 기준을 위해 위 커밋의 동작을 본문 기준으로 삼고, 미커밋 변경은 안정 동작으로 단정하지 않은 채
14절의 진행 중 변경으로만 구분한다. 문서화 작업 자체는 별도 worktree에서 수행해 사용자 변경을 수정·stage하지
않았다.

## 1. 한눈에 보는 시스템

QuantPilot은 하나의 거대한 자동매매 루프가 아니라 권한이 다른 여러 경로를 같은 계약 위에 쌓은 구조다.

```text
정책/전략 사양
  -> 데이터 공급자 -> 유니버스 -> 기술지표/신호 -> 포트폴리오 계획
  -> 주문 제안 -> 배치/개별 위험 검사 -> 승인 또는 자동 권한 검사
  -> 주문 상태 머신 -> Mock/Paper broker -> 체결/조정 -> 감사/리포트
```

- Level 1-2는 연구, 신호, 리밸런싱 제안까지만 만들며 주문을 제출하지 않는다.
- 별도의 Level 1-2 mock 실행은 같은 판단을 `MockBroker`에만 제출한다.
- Level 3은 주문 제안 뒤 사람 승인을 요구한다.
- Level 4는 guarded flag, 정책 승격, 시간·손실·신선도·멱등성 게이트를 모두 통과해야 자동 제출한다.
- Level 5는 한 번의 bounded operator cycle이다. 기본 레지스트리에는 Level 5 전략이 없고 플래그도 꺼져 있어
  기본 실행은 결정적인 no-op/차단 결과를 낸다.
- 실제 외부 연결이 있는 유일한 주문 경계는 **KIS 모의투자 전용 CLI 세션**이다. 이 경로는 일반 API가 아니라
  명시적 환경 게이트, 로컬 과거데이터, KIS paper 호가, schema v10 SQLite 디스패치·위험예약 저널을 함께 사용한다.
- LLM 분석과 RL 출력은 브로커 주문을 직접 생성·승인·제출할 권한이 없다. RL 계약은 전략 선택 또는 제한된
  목표비중 변화만 표현한다 ([`RLOutput`](../quantpilot/packages/core/rl/outputs.py)).

## 2. 구성요소와 책임

| 계층 | 현재 책임 | 주요 근거 |
|---|---|---|
| API | FastAPI 라우팅, 입력 모델 검증, 저장소 오류/데이터 설정 오류의 HTTP 변환 | [`main.py`](../quantpilot/services/api/main.py), [`dependencies.py`](../quantpilot/services/api/dependencies.py) |
| 오케스트레이션 | 정책→신호→계획→제안→위험검사→제출, 승인 티켓, guarded autopilot | [`HarnessService`](../quantpilot/packages/core/harness_service.py) |
| Level 5 | bounded run, 전략 선택, fallback, 실행 리포트, run 멱등성 | [`OperatorService`](../quantpilot/packages/core/operator/service.py) |
| professional operator | 전략 건강 검토, 보호 청산 우선, 주간 리밸런싱 lease, 체결 귀속 | [`ProfessionalOperatorCycle`](../quantpilot/packages/core/operator/professional_cycle.py) |
| 데이터 | fixture/CSV/KIS historical 공급자 선택과 데이터 품질 차단 | [`providers.py`](../quantpilot/packages/core/data/providers.py), [`quality.py`](../quantpilot/packages/core/data/quality.py) |
| 퀀트 | 유니버스, pullback/multifactor 신호, 포트폴리오 최적화, 백테스트 | [`signals/service.py`](../quantpilot/packages/core/signals/service.py), [`portfolio/optimizer.py`](../quantpilot/packages/core/portfolio/optimizer.py), [`backtest/engine.py`](../quantpilot/packages/core/backtest/engine.py) |
| 안전 | pre-trade risk, Level 4/5 권한 체인, 주문 상태 전이 | [`gatekeeper.py`](../quantpilot/packages/core/risk/gatekeeper.py), [`state_machine.py`](../quantpilot/packages/core/execution/state_machine.py) |
| 브로커 | 결정적 mock, 내부 paper, 외부 KIS paper adapter | [`mock_broker.py`](../quantpilot/packages/brokers/mock_broker.py), [`paper_broker.py`](../quantpilot/packages/brokers/paper_broker.py), [`kis_paper.py`](../quantpilot/packages/brokers/kis_paper.py) |
| 상태 저장 | API용 in-memory registry, KIS paper 복구용 SQLite | [`repositories.py`](../quantpilot/packages/db/repositories.py), [`sqlite_repositories.py`](../quantpilot/packages/db/sqlite_repositories.py) |
| UI | React/Vite, React Query 기반 API 콘솔 및 안전 상태 표시 | [`App.tsx`](../quantpilot/apps/web/src/App.tsx), [`queries.ts`](../quantpilot/apps/web/src/lib/queries.ts) |

API 프로세스는 전역 `RepositoryRegistry`와 지연 생성 `HarnessService`/`OperatorService`를 쓴다. 따라서 정책,
신호, 주문, 리포트는 **같은 API 프로세스 안에서는 유지되지만 재시작하면 사라진다**. `DATA_MODE` 관련 설정이
바뀌면 dependency layer가 서비스를 다시 만들지만 전역 in-memory repository 객체는 재사용한다. 반대로 KIS
paper 세션의 안전·주문·체결 복구 상태는 명시적으로 생성한 SQLite DB에 저장한다.

## 3. 실행 모드와 데이터 모드

### 3.1 실행 권한 축

`UserPolicy.execution_mode`는 다음 다섯 값을 가진다 ([`ExecutionMode`](../quantpilot/packages/core/schemas.py)).

| 값 | 의미 | 주문 가능 조건 |
|---|---|---|
| `backtest_only` | 연구/백테스트 | 제출 안 함 |
| `paper_trading` | paper 성격 정책 | 별도 승인/권한 및 안전 게이트 필요 |
| `approval_required` | Level 3 | 사용자가 제안을 승인한 뒤 재검사 |
| `guarded_autopilot` | Level 4 | authority 4 + guarded flag + 17단계 권한 검사 |
| `fully_automated` | Level 5 | authority 5 + Level 5 flag + 레지스트리/생명주기 + 19단계 권한 검사 |

브로커 모드는 `mock`, `paper`, `live_disabled`뿐이다. Level 4/5는 `mock` 또는 `paper`만 허용하며
`live_disabled`는 제출 대상이 아니다. `OperatorRunRequest.run_mode`는 `dry_run`, `mock_submit`,
`paper_submit`이고 live 모드는 없다 ([`operator/schemas.py`](../quantpilot/packages/core/operator/schemas.py)).

### 3.2 데이터 축

코드의 `DataMode` enum은 여섯 값이다.

| 값 | provider factory 상태 | 네트워크/용도 |
|---|---|---|
| `fixture` | 구현, 기본값 | 저장소 fixture만 사용 |
| `local_historical` | 구현 | 로컬 `securities.csv`, `ohlcv.csv`; 네트워크 없음 |
| `external_historical` | KIS 또는 명시적 주입으로 구현 | KIS historical은 환경·자격증명 필요, 자동 테스트는 fake transport |
| `realtime_market_data` | enum만 존재 | 일반 provider factory에서 fail closed |
| `paper_trading` | enum만 존재 | 일반 provider factory에서 fail closed; KIS 세션은 전용 runtime이 구성 |
| `live_trading` | enum만 존재하며 unsafe | provider factory에서 fail closed |

알 수 없는 값은 `DataModeConfigError`, 알려졌지만 provider branch가 없는 값은 `ProviderError`가 된다. API에서는
둘 다 `503`으로 노출되며 fixture로 조용히 fallback하지 않는다. KIS paper CLI는 판단 데이터에
`local_historical`만 허용한다. 일반 `external_historical` KIS client는 production origin을 사용할 수 있어
paper 세션에서 `paper_external_historical_origin_not_hardened`로 차단한다.

`AGENTS.md`가 요구하는 `live_trading_candidate`, `live_canary`, `live_scaled`는 현재 코드 enum에 없다. 이는 확장
시 해결해야 할 문서-코드 계약 차이다.

## 4. 안전 기본값과 시작 명령

기본 환경은 [`.env.example`](../.env.example)과 같다.

```text
LIVE_TRADING_ENABLED=false
GUARDED_AUTOPILOT_ENABLED=false
FULLY_AUTOMATED_OPERATOR_ENABLED=false
BROKER_MODE=mock
DEFAULT_ORDER_TYPE=limit
MARKET_ORDERS_ENABLED=false
DATA_MODE=fixture
```

### 4.1 백엔드와 smoke

```powershell
# 선택: 가상환경 활성화 후 개발/테스트 의존성 설치
python -m pip install -e ".[test]"

# 전체 테스트
python -m pytest quantpilot/tests

# 기본 안전 경로 smoke
python -m quantpilot.jobs.run_smoke

# UI 기본 주소(8010)와 맞춘 API 서버
python -m uvicorn quantpilot.services.api.main:app --reload --port 8010
```

권한 문제로 pytest 임시 디렉터리를 만들지 못하는 Windows 환경에서는 다음을 쓴다.

```powershell
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp
```

`make test`, `make smoke`, `make api`도 동일 계열 명령이지만, 검증된 Windows 환경에는 `make`가 없을 수 있다.

### 4.2 프론트엔드

```powershell
Set-Location quantpilot/apps/web
npm install
npm run dev
```

UI는 기본적으로 `http://127.0.0.1:8010`을 호출한다. `VITE_API_BASE_URL` 또는 Settings 화면이 저장하는
`localStorage`의 `qp.apiBase`로 바꿀 수 있다 ([`api.ts`](../quantpilot/apps/web/src/lib/api.ts)). API CORS는
localhost/127.0.0.1의 Vite `5173`, preview `4173` origin과 GET/POST만 허용한다.

## 5. 부팅과 요청 수명

1. FastAPI가 10개 router를 `/api` 아래에 연결한다.
2. 첫 서비스 요청에서 `build_providers_from_env()`가 `DATA_MODE`를 해석한다.
3. 전역 `RepositoryRegistry`를 공유하는 `HarnessService`와 `OperatorService`가 만들어진다.
4. provider 설정 오류는 서비스가 만들어지기 전에 `503`으로 닫힌다.
5. 각 요청은 Pydantic `extra="forbid"` 모델을 통과한다. 예기치 않은 필드는 거부된다.
6. in-memory repository는 add/update 때 모델 snapshot을 복사하므로 호출자가 저장 객체를 우연히 직접 변경하지
   못한다. 중복 ID는 `409`, 없는 항목은 `404`로 변환된다.

## 6. 데이터에서 주문까지의 전체 흐름

### 6.1 정책과 전략 준비

`POST /api/policies/preview`는 파싱 결과를 저장하지 않고 보여준다. `parse`는 `UserPolicy`를 저장하고 audit
event를 남기며, `confirm`은 현재 fixture 하네스에서 확인 event를 남긴다. 정책의 broker, execution mode,
위험 한도, 허용 주문 유형, authority와 feature fields가 뒤 단계의 권한 계약이다.

전략 YAML은 [`quantpilot/docs/strategy_specs`](../quantpilot/docs/strategy_specs)에 있고
`load_strategy_recipe()`가 허용 execution level과 promotion status의 모순, 잠긴 v2 규칙을 거부한다.

### 6.2 Level 1-2 연구 경로

`POST /api/level-1-2/run`의 실제 순서는 다음과 같다.

1. 저장된 정책과 기본 전략을 읽는다.
2. security provider와 정책의 시장·선호·blocklist·유동성 조건으로 후보 유니버스를 만든다.
3. OHLCV를 읽고 look-ahead를 피하는 기술지표와 신호를 만든다.
4. analyst report를 만들지만 analyst 결과가 신호 action을 덮어쓰지는 않는다.
5. fixture portfolio snapshot과 quote로 목표비중/현금비중 및 리밸런싱 제안을 만든다.
6. 신호, portfolio plan, daily operation report, audit event를 저장한다.
7. 결과의 `order_submission_enabled`는 항상 `false`, 주문 ID와 fill ID는 비어 있다.

세분 API인 `/research/universe`, `/research/analyst`, `/signals/board`,
`/portfolio/rebalance-suggestions`, `/reports/research-signal-daily`도 같은 service 결과의 일부를 노출한다.
`GET /api/briefing/daily`는 fixture 브리핑이며 **신호 입력이 아닌 읽기 전용 경계**다.

### 6.3 Level 1-2 mock 실행

`POST /api/level-1-2/mock-execute`는 `LIVE_TRADING_ENABLED=false`, 정책 broker=`mock`일 때만 진행한다.
연구 경로 뒤 executable portfolio plan을 다시 만들고 batch risk gate를 통과한 주문 제안을 mock 정책 이름으로
승인한 뒤 `submit_order_plan()`을 거쳐 `MockBroker`에 보낸다. `partial_allow=false`면 배치의 한 주문이 막힐 때
전체를 막고, true면 허용된 제안만 진행한다. 최종 응답은 신호 수, 제출·차단 주문 수, 체결 수와 종목별
타이밍 판단을 함께 반환한다.

### 6.4 주문 제안과 Level 3 승인

핵심 도메인 흐름은 다음 상태 머신을 따른다.

```text
draft -> risk_checked -> proposed -> user_approved -> submitted
                                                    -> accepted
                                                    -> partially_filled -> filled
각 비종결 상태 -> modified/cancelled/rejected/expired/failed
```

종결 상태에서 재진입할 수 없고 잘못된 전이는 `InvalidOrderTransition`으로 실패한다. `generate_order_proposals()`는
포트폴리오 intent마다 결정적 idempotency key를 만들고 batch + 개별 risk evidence와 설명을 붙인다. 제안을
수정하면 원본은 `modified` 종결 상태가 되고 새 risk/idempotency가 필요한 대체 제안이 생성된다.

Level 3은 `/orders/{id}/approve` 또는 trade approval ticket의
`/execution/approval-tickets/{ticket_id}/approve-and-submit`을 통해 사람이 승인한다. 제출 직전에 정책 버전,
승인 상태, quote·risk expiry, 중복 key, 손실/노출 한도를 다시 검사한다. `live_trading_candidate`라는 ticket
label은 live 권한이 아니며 현재 `live_broker_unavailable`에서 차단된다.

### 6.5 Level 4 guarded autopilot

`POST /api/autopilot/guarded/run-once`는 제안마다 `authorize_level4()`를 실행한다. 대표 순서는 feature flag,
kill switch, pause, broker, authority 4 + `guarded_autopilot`, 정책/사용자 identity, broker health, quote freshness,
전략 promotion/level, KRX 자동주문 시간, 주문유형, 월 손실 stop/pause, 미체결 충돌, 미해결 paper buy,
idempotency, fresh risk check다. 첫 실패에서 즉시 차단되고 사유가 저장된다.

KRX 자동 주문 시간은 서울 시간 평일 09:10 이상 15:10 미만이다. 이는 개장·종가 auction 구간을 피하기 위한
코드 상의 시간창일 뿐 공휴일 달력 검증은 아니다. pause/resume과 kill-switch engage/release API가 있으며,
kill switch가 발동되면 전략 승인 티켓도 revoke된다. release에는 명시적 확인문구가 필요하고 기존 전략은
자동 재승인되지 않는다.

### 6.6 Level 5 bounded operator

`POST /api/operator/run-once`의 상위 흐름은 다음과 같다.

1. run request 전체의 fingerprint와 idempotency key를 결합한다.
2. Level 5 flag, 정책 존재/사용자, live=false, 두 kill switch, broker/run-mode 일치, 정책 버전, authority 5와
   `fully_automated`를 순서대로 검사한다.
3. registry에서 정책 버전에 맞는 `validated_l5` 전략을 결정적으로 선택한다. 기본 registry에는 없으므로 기본
   설정에서는 여기까지 도달하기 전 `level5_flag_disabled`로 닫힌다.
4. mock/paper broker snapshot을 동기화하고 월 손실 stop을 검사한다.
5. provider-bound signal set, portfolio plan, order proposals를 만든다. 데이터 품질이 unusable이면 주문 없이
   완료한다.
6. `dry_run`은 제안을 `cancelled/dry_run_no_submission`으로 종결한다.
7. submit 모드는 각 제안마다 `authorize_level5()`의 fresh 권한·risk chain을 다시 통과시킨다.
8. 모든 branch를 `OperatorDecision`, `FallbackDecision`, `OperatorReport`, audit event로 남긴다. 자동 retry는 없다.

같은 idempotency key와 같은 요청은 기존 결과를 replay한다. 다른 요청 payload가 같은 key를 쓰면 거부한다.
캐시 뒤 kill switch가 켜졌다면 과거 성공 결과를 replay하지 않고 다시 차단한다. SQLite professional store가
주입된 경우 run 시작/종료 결과도 `paper_run_checkpoints`에 영속화한다.

### 6.7 KIS paper professional operator

이 경로는 API의 일반 `HarnessService.from_environment()`가 아니라
`python -m quantpilot.jobs.run_kis_paper_session`이 전용 runtime을 조립한다.

사전 게이트는 다음을 모두 요구한다.

- `KIS_PAPER_SESSION_ENABLED=true`, `KIS_PAPER_ORDER_SUBMISSION_ENABLED=true`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=true`
- `LIVE_TRADING_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`
- `BROKER_MODE=paper`, `DATA_MODE=local_historical`
- 사람이 미리 승격한 policy JSON과 registry/lifecycle JSON
- 현재 KRX 영업일에 대한 명시적 승인 날짜
- 저장소 밖 SQLite 경로와 KIS paper 전용 자격증명
- 별도 job으로 기록한 전일/월초 손실 기준선

한 세션은 fenced lease를 얻은 뒤 다음 순서로만 움직인다.

1. POST 전 중단으로 남은 stale `prepared` dispatch를 외부 전송 없이 만료한다.
2. `dispatch_claimed`, `outcome_unknown`, accepted/partial 주문을 broker 조회로 조정한다.
3. 조정 결과를 로컬 broker/order/fill journal에 적용하고, 일치하는 새 fill만 managed position에 귀속한다.
4. portfolio snapshot과 paper loss baseline으로 daily/monthly loss를 계산한다.
5. 기존 포지션을 먼저 평가해 8%/2ATR stop, 기술적 exit/trim, 전략 retirement 같은 위험 축소 주문을 처리한다.
6. 보호·조정 단계가 안전하게 끝나고 strategy state가 active일 때만 주간 rebalance lease를 평가한다.
7. Level 5 `paper_submit` cycle을 실행하고 세션 lease를 닫는다.

따라서 신규 매수보다 reconciliation과 보호 매도가 우선한다. 미해결 매수가 있으면 추가 매수는 막히지만,
귀속과 수량이 검증된 위험 축소 매도는 별도 검사를 거쳐 허용될 수 있다.

## 7. 영속성, 멱등성, 조정

### 7.1 in-memory 경로

일반 API는 정책, 전략, 신호, 계획, 주문, broker order, fill, audit, report, 승인 티켓, backtest와 notification을
`RepositoryRegistry`에 보관한다. 이는 개발 하네스이지 운영 DB가 아니다. 서버 재시작·다중 worker 간 공유·장기
감사 보존을 제공하지 않는다. API를 여러 worker로 띄우면 각 worker가 서로 다른 service cache를 가질 수 있으므로
현재 구조에서 운영 확장하면 안 된다.

### 7.2 SQLite paper state

`PaperStateStore`는 opt-in이며 schema version 10, foreign keys,
`synchronous=FULL`, 파일 DB의 WAL을 사용한다. schema v9에서 managed-order
kill operation과 cancel request journal이 추가됐고, v10에서
`paper_risk_reservations`가 추가됐다. 주요 테이블은 store provenance,
managed positions, run checkpoints, strategy states, pending liquidations,
cycle claims, processed fills, safety state, execution sessions, order
dispatches, risk reservations, kill operations, cancel requests, loss
baselines다.

- DB는 `data_mode`, broker environment, 계정번호 자체가 아닌 SHA-256 account scope fingerprint에 묶인다.
- 다른 계정/환경 DB를 열거나 provenance가 불완전하면 fail closed한다.
- fill ID는 processed ledger에서 한 번만 상태에 반영된다.
- weekly rebalance는 bucket 단위 unique claim과 lease/fence로 중복 cycle을 막는다.
- optimistic revision과 immutable identity 비교가 충돌/변조된 복구를 막는다.
- raw credential, access token, 계좌번호를 DB나 저장소에 기록하지 않는다.
- v9→v10 migration은 열린 legacy dispatch마다 보수적인 `held` reservation을
  같은 migration transaction에서 backfill한다. 안전한 증거를 만들 수 없으면
  `PaperStateMigrationRequired`로 전체 migration을 rollback하고 v9를 유지한다.

### 7.3 외부 POST 전후 계약

`DurablePaperSubmissionCoordinator`만 KIS 주문 POST 권한을 가진다.

```text
risk reservation + prepared dispatch (same SQLite commit)
  -> dispatch_claimed (단 한 번의 원자적 claim)
  -> accepted / partially_filled / filled / rejected
  -> 응답이 모호하면 outcome_unknown -> broker 조회 reconciliation만 허용
  -> definitive terminal이면 dispatch CAS + reservation release (same transaction)
```

`dispatch_claimed` 이후 예외가 나면 재전송하지 않는다. `outcome_unknown`을 포함한 비-prepared 상태를 다시
호출하면 broker POST 없이 기존 증거를 replay하거나 reconciliation 필요 오류를 낸다. prepared 당시 order,
risk check, quote, snapshot, strategy, account fingerprint의 immutable evidence가 현재 제출과 정확히 일치해야 한다.
`outcome_unknown`, `accepted`, `partially_filled`에서는 전체 reservation을
`held`로 유지한다. `filled`, `cancelled`, `rejected`,
`expired_pre_dispatch`, `failed_pre_dispatch`처럼 확정된 terminal 증거가
있을 때만 dispatch 갱신과 같은 transaction에서 release한다.

KIS 일별 조회의 최근 3개월 범위를 벗어난 미해결 주문은 자동 추측하지 않고
`paper_broker_history_manual_resolution_required`로 차단한다. 현재 DB 직접 수정이나 임의 확정 명령은 없다.

## 8. 전략 생명주기와 실행 권한

전략에는 서로 다른 세 축이 있다.

1. **Spec**: YAML `StrategyRecipe`가 무엇을 할지 정의한다.
2. **Lifecycle**: `draft -> backtested -> paper_candidate -> paper_validated -> live_candidate`가 사람이 확인한
   evidence 성숙도를 나타낸다.
3. **Registry authority**: 현재 operator가 어느 실행 level에서 전략을 선택할 수 있는지 나타낸다.

`live_candidate`는 이름과 달리 live 주문 승인이 아니다. 각 promotion은 필요한 evidence 종류, 정확한 확인문구,
비어 있지 않은 human attribution을 요구한다. draft를 벗어난 같은 version/spec hash는 불변이며 수정하려면 새
version을 draft로 등록해야 한다. revoked는 종결 상태다.

실행 권한이 있는 registry entry는 같은 `strategy_id + version + spec_hash`의 lifecycle evidence와
`lifecycle_binding.py`에서 일치해야 한다. Level 3에는 최소 backtested, paper/guarded/Level 5 후보에는 최소
paper_validated, 미래 live/canary label에는 live_candidate가 필요하다. 그러나 이 evidence는 필요조건일 뿐
정책 승격, runtime flag, risk gate를 대신하지 않는다.

별도의 제품 UI 승인 흐름은 Strategy Studio에서 draft→local backtest validation→strategy approval ticket을
만든다. 티켓 승인은 전략을 **arming**할 뿐 즉시 매수하지 않는다. 유효기간, drift(MDD), kill switch, 수동
revoke가 재승인을 요구하며 capital budget도 주문 제출 경로에서 검사된다.

## 9. API와 UI

### 9.1 API 표면

| 영역 | 대표 endpoint | 작동 |
|---|---|---|
| 상태 | `GET /api/health`, `POST /api/harness/run-smoke` | 플래그/데이터 모드, fixture smoke |
| 정책 | `/api/policies/preview|parse|confirm` | 정책 미리보기·저장·확인 |
| 연구 | `/api/level-1-2/*`, `/api/research/*`, `/api/signals/*` | 유니버스, 분석, 신호, mock 실행 |
| 계획/주문 | `/api/portfolio/plan`, `/api/orders/*` | 계획, 제안, 승인/거절/수정/제출/조회 |
| 승인 | `/api/execution/approval-tickets/*` | 거래별 Level 3 승인 레일 |
| 전략 | `/api/strategy-studio/*`, `/api/execution/strategy-tickets/*` | 초안, 검증, 전략 단위 승인 |
| Level 4 | `/api/autopilot/*` | guarded run, pause/resume, kill switch |
| Level 5 | `/api/operator/run-once`, `/status`, `/reports/latest` | bounded operator와 report |
| durable 관측 | `GET /api/operator/professional-status` | SQLite read-only snapshot; DB 미설정/오류도 typed unavailable 응답 |
| 통지 | `/api/notifications*` | drift/expire/revoke/kill-switch inbox |

정확한 전체 목록은 [`services/api/routers`](../quantpilot/services/api/routers)에 있다. OpenAPI snapshot은
[`openapi.json`](../openapi.json), 프론트 타입은 [`openapi.d.ts`](../quantpilot/apps/web/src/lib/openapi.d.ts)다.

### 9.2 UI 작동

라우트는 Overview, Research, Policies, Signals, Run, Briefing, Studio, Execution, Operator, Jobs, Settings다.
React Query가 health 30초, pending trade/notification 20초, professional status 30초 등의 간격으로 읽는다.
mutation 성공 뒤 관련 query를 invalidate한다. 모든 API 호출은 브라우저 local activity log에 요청 경로, 상태,
지연과 응답을 기록하지만 이것은 SQLite audit의 대체물이 아니다.

UI가 노출한다고 해서 기능이 활성화되는 것은 아니다. 안전 banner와 버튼 상태는 관측/UX 방어층이고 최종
권한은 백엔드 flag, policy, state machine, fresh risk check가 결정한다.

## 10. 실패 폐쇄 안전 게이트

다음 조건은 오류를 무시하거나 fixture로 대체하지 않고 주문을 막는다.

- live flag가 true인 Level 1-2 mock 및 Level 5 실행
- 미구현/오류 데이터 모드, 불완전 CSV, 중복 bar, 결측 symbol, unusable provider quality
- 현재 정책과 request/order의 version·user·broker 불일치
- stale/future/naive quote 또는 portfolio snapshot
- KRX 자동 주문 시간 밖, market order flag 꺼짐, 정책 미허용 주문유형
- 일/월 손실, 단일 주문·포지션·섹터·현금·turnover·order count 한도 위반
- duplicate idempotency, 같은 전략/종목/방향 미체결 충돌, unresolved paper buy
- broker unhealthy, autopilot pause, policy/env kill switch
- 전략 status/allowed level/lifecycle/spec hash 불일치
- risk check 누락·실패·만료, 승인 누락, 유효기간 만료
- paper session provenance/lease/fence/account fingerprint/영업일 불일치
- broker 응답 모호, reconciliation 중복 매칭, 로컬/영속 증거 충돌, 오래된 미해결 주문

모든 order 제출은 기존 pre-trade risk check, kill switch, idempotency, 상태 머신, audit,
reconciliation을 통과해야 한다. 실패한 안전 테스트를 삭제하거나 threshold를 느슨하게 해서 확장하면 안 된다.

## 11. 테스트와 검증 방식

필수 명령은 다음과 같다.

```powershell
# backend 전체
python -m pytest quantpilot/tests

# smoke/orchestration
python -m quantpilot.jobs.run_smoke

# frontend (quantpilot/apps/web에서)
npm run test
npm run build
```

테스트 구조는 다음 의도를 반영한다.

- `unit`: 정책 파싱, 데이터 모드/품질, no-lookahead, 리스크, 권한 체인, 승인, lifecycle, SQLite, dispatch,
  reconciliation, professional cycle을 fake/fixture로 검증한다.
- `integration`: Level 3/4/5 흐름과 professional path, smoke를 연결 검증한다.
- KIS 실서버 테스트는 기본 skip이며 `RUN_KIS_MANUAL_INTEGRATION=1`과 명시적 자격증명이 있어야 실행한다.
- frontend는 안전 banner, mock 실행, operator/professional status, 알림과 API 오류를 Vitest로 검증한다.

기본 smoke의 성공 기준은 단순 exit code가 아니다. fixture 정책/신호/계획/감사 흐름이 성공하고 broker가 mock,
live가 false이며 operator가 `blocked`, fallback=`level5_flag_disabled`, submitted=[]를 출력해야 한다.

### 11.1 이 문서 작성 시점의 검증 스냅샷

`2026-07-11 KST`에 기준 커밋을 격리 worktree에서 다시 검증한 결과는 다음과 같다.

| 검증 | 결과 | 비고 |
|---|---|---|
| `python -m pytest quantpilot/tests` | 환경 오류 | 코드 실패가 아니라 `C:\Users\goyan\AppData\Local\Temp\pytest-of-goyan` 접근 거부 |
| `python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp_gate1` | `885 passed, 2 skipped` | schema v10 Gate 1 통합 후 전체 backend 통과 |
| `python -m quantpilot.jobs.run_smoke` | 통과 | `broker=mock`, live=false, Level 5 blocked, 제출 ID 없음 |
| `python -m quantpilot.jobs.run_kis_paper_kill engage` | 기본 차단 | `paper_kill_disabled`; 실제 KIS 호출 없음 |
| `npm run test` | `23 passed` | Gate 1 이전 frontend snapshot; Gate 1은 frontend를 변경하지 않음 |
| `npm run build` | 통과 | Gate 1 이전 snapshot; 번들 크기 경고가 있으나 typecheck/Vite build 성공 |

## 12. 개발 변경과 에이전트 협업 워크플로우

프로젝트의 기능 작동 방식뿐 아니라 **변경을 반영하는 방식**도 안전 계약의 일부다. 기준 문서는
[`AGENTS.md`](../AGENTS.md), [`agent_collaboration_protocol.md`](agent_collaboration_protocol.md),
[`agent_workboard_template.md`](agent_workboard_template.md)다.

### 12.1 단순 작업 fast path

목표와 수정 지점이 명확한 한두 파일의 국소 변경, 짧은 설명, 오탈자처럼 저위험·가역적이고 외부 side effect가
없는 작업은 최초 수신자가 바로 처리한다. 별도 작업보드, 상대 에이전트, 라우팅 점수, 전용 worktree를 만들지
않고 필요한 최소 검증만 실행한다. 다만 안전 규칙과 필수 검증은 단순 작업에서도 생략할 수 없다.

### 12.2 비단순 미션

저장소 전반 조사, 다중 모듈 변경, 원인 미상의 버그, 거래·데이터·상태 계약 변경처럼 비단순한 작업은 다음
순서를 따른다.

1. 최초 수신자가 mission lead가 되어 목표, 범위, 안전 경계, 완료 조건을 작업보드에 확정한다.
2. 능력 점수표로 구현자와 검토자를 정하고 서로 겹치지 않는 소유 경로와 별도 worktree/branch를 배정한다.
3. 상대 에이전트가 구현 전에 분해와 검증 계획을 검토하고, 독립 구현·구속력 있는 연구/설계·차단 가능한 감사 중
   최소 하나의 실질 산출물을 커밋한다.
4. 각 작업자는 자기 경로만 stage/commit하고 정확한 검사 결과와 한계를 handoff한다.
5. mission lead가 상대 커밋을 검토해 mainline에 통합하고 최종 커밋 상태에서 프로젝트 전체 검증을 반복한다.
6. 작업 중 단순 범위를 벗어났다면 그 시점에 작업보드를 만들고 비단순 절차로 승격한다.

이 절차는 작업량을 나누기 위한 것이 아니라 dirty workspace의 사용자 변경 보존, 독립 검증, 안전 중요 변경의
단일 통합 책임을 확보하기 위한 것이다. 완료된 과거 작업보드는 증거일 뿐 새 미션의 활성 상태판으로 재사용하지
않는다.

## 13. 작동방식을 발전시킬 때 보존할 불변조건

1. 새 기능은 데이터 출처, 전략 evidence, 실행 권한을 한 flag로 합치지 않는다.
2. API/UI/LLM/RL이 broker adapter를 직접 호출하지 않고 service→risk→state machine→broker 경계를 유지한다.
3. 새 data mode는 enum, provider factory, health, API error, test, runbook을 함께 추가하며 묵시적 fixture fallback을
   만들지 않는다.
4. 외부 connector는 fake-client unit test와 opt-in manual integration만 두고 테스트가 인터넷/비밀을 요구하지
   않게 한다.
5. 외부 주문은 POST 전에 durable intent를 기록하고, claim 이후 outcome unknown은 재전송하지 않는다.
6. 멱등성 key는 payload identity에 묶고 duplicate는 replay 또는 명시적 충돌이어야 한다.
7. 정책·전략·주문·snapshot·계정 provenance를 끝까지 연결하고 stale evidence로 제출하지 않는다.
8. 신규 매수보다 reconciliation, kill switch, 보호 청산, retirement를 우선한다.
9. 전략 승격은 evidence와 사람 확인을 요구하고 LLM/RL이 확인자를 대행하지 않는다.
10. live/canary를 추가하더라도 기본값 false, market order false, 별도 broker adapter, enablement checklist,
    canary budget, rollback/kill switch, reconciliation을 먼저 구현한다.
11. in-memory API를 durable/multi-worker로 바꾸려면 repository transaction, uniqueness, concurrency, migration,
    audit retention 계약을 먼저 설계한다.
12. 계약이나 API를 바꾸면 tests, OpenAPI snapshot, generated frontend types, 운영 문서를 같은 변경에서 갱신한다.

## 14. 현재 문서와 코드의 간극·알려진 한계

- README의 첫 문장은 fixture-only라고 하지만 코드는 local/external historical과 opt-in KIS paper runtime까지
  확장되어 있다. 다만 **기본 경로가 fixture이고 live가 미구현**이라는 핵심은 맞다.
- `docs/STATUS.md`는 일반 realtime provider와 전용 KIS paper runtime을
  구분한다. 전용 runtime의 kill v1과 atomic reservation v1은 fake-client
  개발 검증을 마쳤지만 Gate P/manual KIS 검증 전에는 운영 준비로 보지 않는다.
- `AGENTS.md`의 8개 data mode와 코드의 6개 `DataMode`가 다르다. candidate/canary/scaled를 코드에 넣을지,
  문서의 운영 stage vocabulary로만 둘지 먼저 결정해야 한다.
- README의 uvicorn 명령은 기본 8000 포트지만 UI 기본값은 8010이다. 함께 실행할 때 `--port 8010`을 붙이거나
  UI API base를 바꿔야 한다.
- 기본 API repository와 operator report는 in-memory다. `professional-status`만 SQLite를 read-only로 보며,
  API에서 KIS paper session을 시작하거나 DB를 수정하지 않는다.
- held sell reservation의 guardrail projection은 현재 policy scope를 별도
  저장하지 않아 one-policy-per-paper-store 운용 가정 아래 보수적으로
  동작한다(QP-RES-A2). 다중 policy를 한 store에 넣기 전에 dispatch
  `policy_id`와의 join 또는 reservation scope 확장이 필요하다.
- 기본 registry에는 의도적으로 `validated_l5`가 없다. Level 5 코드가 존재한다는 사실은 기본 자동주문이
  가능하다는 뜻이 아니다.
- KIS historical/token/paper connector는 fake transport로 자동 검증되었지만 실제 서버는 자격증명과 명시적
  manual test 전까지 미검증이다.
- KRX 자동주문 시간 검사는 평일/시각 기반이다. KIS paper path는 별도 승인 영업일 authority를 쓰지만 일반
  Level 4/5 mock 경로의 시간 함수는 공휴일 달력을 모른다.
- 브리핑은 fixture read-only이며 실제 뉴스 수집기나 신호 통합이 없다.
- strategy studio와 strategy ticket, notification endpoint가 한 `execution.py` router에 함께 있어 모듈 경계가
  느슨하다. 조사 시작 시 메인 working tree에는 이를 세 router로 분리하고 CORS origin 설정을 확장하는 미커밋
  변경이 있었지만 기준 커밋에는 아직 통합되지 않았다. 해당 변경이 검토·커밋되기 전에는 완료된 구조로 간주하지
  않는다.
- OpenAPI snapshot/generated type이 실제 app과 동기화됐는지는 API 계약 변경 때마다 재생성 검증이 필요하다.

## 15. 근거를 찾는 빠른 경로

- 안전 기본값과 명령: [`README.md`](../README.md), [`.env.example`](../.env.example)
- 전체 도메인 모델: [`schemas.py`](../quantpilot/packages/core/schemas.py)
- API 오케스트레이션: [`harness_service.py`](../quantpilot/packages/core/harness_service.py)
- Level 4/5 권한: [`state_machine.py`](../quantpilot/packages/core/execution/state_machine.py)
- Level 5 run/fallback/report: [`operator/service.py`](../quantpilot/packages/core/operator/service.py),
  [`fallback_manager.py`](../quantpilot/packages/core/execution/fallback_manager.py)
- professional 포지션 루프: [`professional_cycle.py`](../quantpilot/packages/core/operator/professional_cycle.py)
- SQLite schema/전이: [`sqlite_repositories.py`](../quantpilot/packages/db/sqlite_repositories.py)
- KIS paper 세션: [`run_kis_paper_session.py`](../quantpilot/jobs/run_kis_paper_session.py),
  [`kis_paper_session_runbook.md`](kis_paper_session_runbook.md)
- 외부 POST와 조정: [`paper_submission.py`](../quantpilot/packages/core/execution/paper_submission.py),
  [`paper_reconciliation.py`](../quantpilot/packages/core/execution/paper_reconciliation.py)
- 전략 lifecycle: [`promotion.py`](../quantpilot/packages/core/strategies/promotion.py),
  [`lifecycle_binding.py`](../quantpilot/packages/core/strategies/lifecycle_binding.py)
- 테스트: [`quantpilot/tests`](../quantpilot/tests), UI 테스트는
  [`apps/web/src/test`](../quantpilot/apps/web/src/test)
