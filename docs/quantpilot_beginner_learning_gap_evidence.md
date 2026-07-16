# QuantPilot 초보자 학습 공백 증거 분석 (QP-LRN-01)

> 미션 `QP-LRN-20260716`의 독립 상대 에이전트 산출물. 최근 QuantPilot 작업에서
> **반복해서 비용을 발생시킨 실제 문제 패턴**을 저장소 증거(커밋 해시·문서·감사 기록)로
> 확인하고, 그 문제를 **개발 경험이 전혀 없는 학습자가 익혀야 할 개발 지식**으로
> 변환한다. 커리큘럼 본문(`QP-LRN-02`, Codex 소유)이 이 문서를 근거로 삼는다.
>
> 이 문서는 `docs/quantpilot_beginner_learning_gap_evidence.md` **한 파일만** 생성한다.
> 코드·거래 로직·작업보드·다른 문서를 변경하지 않는다. 모든 거래 플래그는 비활성이며
> 네트워크·비밀·브로커·외부 부작용을 사용하지 않았다.

## 1. 분석자, 모델, 범위

| 항목 | 값 |
|---|---|
| 분석자 | Claude Code (독립 상대 에이전트, 비구현자) |
| 해석된 모델/버전 | `claude-opus-4-8` (하네스가 노출한 정확 모델 ID; 표시명 Opus 4.8, Claude Code) |
| 브랜치/워크트리 | `claude/qp-learning-curriculum-evidence` (`C:/qp-learning-curriculum-claude`) |
| 검토 시작 HEAD | `9cb0b05` (`docs: dispatch curriculum evidence review`) |
| 날짜 | 2026-07-16 (KST) |
| 요청 증거 창 | 2026-06-16 ~ 2026-07-16 (Git·작업보드·회고·감사) |
| 실제 관측 창 | **2026-07-06 ~ 2026-07-16** (창 내 최초 커밋은 `37f869d`, 2026-07-06; 07-06 이전 커밋 0개) |

### 1.1 방법론과 무비난 원칙

- 각 문제 패턴은 **식별 가능한 저장소 증거**(커밋 해시, 문서명, 감사 finding ID,
  또는 반복된 재작업 커밋열)에 결속한다.
- **지식·프로세스 공백**과 **평범한 구현 복잡도**를 구분한다. 같은 개념 범주가
  서로 다른 작업에서 여러 번 재작업을 유발했을 때만 "학습 공백"으로 분류하고,
  일회성 난도는 복잡도로 남긴다.
- 재작업의 상당수는 **적대적 감사 프로토콜이 설계대로 작동한 결과**(병합 전 P1 차단)
  이지 실패가 아니다. 따라서 결함 **개수**가 아니라 반복 등장하는 **개념 범주**를
  학습 신호로 삼는다.
- 저장소에는 사용자의 개인 개발 이력이 없다. 이 분석은 **사용자를 탓하지 않으며**,
  "어떤 지식을 미리 알았다면 프로젝트 결정이 더 효율적이었을까"만 다룬다.

## 2. 증거 창과 사용 불가·불확실 증거

**관측 가능한 증거**

- Git 히스토리 `37f869d`(2026-07-06) ~ `9cb0b05`(2026-07-16), 약 140개 커밋.
- 완료·감사 문서: `agent_capability_scorecard.md` §8(seed evidence, QP-000…QP-900),
  `atomic_risk_reservation_v1_claude_audit.md`, `canonical_order_events_v1_claude_review.md`,
  `execution_kernel_v2_workboard.md`, `roadmap_execution_workboard.md`,
  `qp_drift_daily_workboard.md`, `STATUS.md`, `local_data_backtest_validation_report.md`.

**사용 불가·불확실 증거 (명시)**

1. **창 앞부분 공백**: 요청 창은 2026-06-16부터지만 그 구간 커밋이 없다. 실질 증거는
   2026-07-06 이후다. 2026-06-16~07-05 사이의 활동은 저장소에서 관측되지 않는다.
2. **Codex 세션 원본 로그 부재**: Codex의 세션 전개·회고 원문은 직접 열람할 수 없고,
   작업보드/점수표에 **요약된 결과**(라우팅 점수, finding 개수)만 관측된다. 재작업의
   내부 사고 과정은 추론이다.
3. **초기 모델 버전 미기록**: `scorecard §8`의 QP-000…QP-900 행은 "Codex"/"Claude Code"만
   기록하고 정확한 모델 버전이 없다(§8 data-quality note). 이 문서는 그 행을 개인 능력이
   아니라 **개념 재발** 증거로만 사용한다.
4. **"공백" vs "설계된 감사"의 경계**: 특정 재작업이 지식 부족인지, 계획된 교차 감사가
   정상 작동한 것인지는 커밋 메시지만으로 단정할 수 없다. 반복 개념 범주로만 추론한다.

## 3. 반복 문제 패턴과 필요한 개발 지식

각 패턴: **증거 → 분류 → 필요한 지식 → 초보자 우선순위**. 우선순위는 (재발 빈도 ×
기초성 × 저비용 학습 가능성 × 라이브 리스크)로 판단한다.

### 3.1 금액의 부동소수점 vs 정수/Decimal 연산  — 우선순위: 최상

- **증거**
  - `scorecard §8.3` `QP-RM-00A`: **P1 — "persisted capacity as floats contrary to
    the approved roadmap"** (`roadmap_execution_workboard.md` 체크포인트: "float
    persistence").
  - `atomic_risk_reservation_v1_claude_audit.md §3(2)`: 코디네이터가 buy/sell 승인에서
    `1e-6`/`0.01` **float 허용오차를 제거**하고 `Decimal` floor/ceil로 정확 정수를
    비교하도록 전환. 보수적 반올림 방향(reserve는 올림, broker cash는 내림)을 시험으로 고정.
  - 동 문서 finding **QP-RES-A1**: 소수 float 잔고(`equity=10_000_000.5`, `cash=0.3`)에서
    재구성한 최소현금유보가 `−1`이 되어 잘못된 예약을 만들 뻔함. 수정 커밋 `a892210`.
- **분류**: **지식 공백** (서로 다른 3개 지점에서 같은 근본 원인 재발).
- **필요한 지식**: 부동소수점(IEEE-754) 표현 오차, 금액은 **정수 최소단위(원)** 또는
  `Decimal`로 다루기, 반올림 **방향의 보수성**(안전한 쪽으로 올림/내림), 허용오차 비교의 위험.
- **왜 효율에 기여**: 이 개념을 먼저 알면 금액·수량 필드를 설계 시점에 정수/Decimal로
  고정해, P1 재작업과 감사 왕복을 사전에 없앤다.

### 3.2 멱등성·지문(fingerprint)·프로비넌스  — 우선순위: 최상

- **증거**
  - `scorecard §8` `QP-040`: **P2 — "idempotency fingerprint bound per-request field"**
    (요청 지문이 개별 요청 필드에 잘못 결속되어 재작업).
  - 프로비넌스 결속 재작업열: `cb10b15`(tighten canonical event provenance),
    `7e9bd53`(bind reservation release provenance), `d52321b`(close paper event
    provenance gaps), `9769d98`(correct integration ancestry after fallback review).
  - `atomic_risk_reservation_v1_claude_audit.md §3(5)(8)`: 같은 idempotency 키 재준비는
    기존 쌍을 반환하고, **모호한 broker POST는 절대 재시도하지 않음**을 시험으로 고정.
- **분류**: **지식 공백** (주문/이벤트/예약 전반에서 반복).
- **필요한 지식**: 멱등성(idempotency)·재시도 안전성, 요청 지문/키 설계, 데이터 출처
  (provenance) 추적, "정확히 한 번" 처리의 어려움.
- **왜 효율에 기여**: 주문 시스템의 핵심. 처음부터 멱등 키와 프로비넌스를 설계하면
  중복 주문·이중 처리 결함을 원천 차단한다.

### 3.3 트랜잭션 원자성과 크래시 복구  — 우선순위: 상

- **증거**
  - `atomic_risk_reservation_v1_claude_audit.md §3(1)(9)`: 예약+dispatch 삽입과 마이그레이션이
    각각 단일 `BEGIN IMMEDIATE` 트랜잭션 안에서 **all-or-nothing**으로 실행되고, 어느 예외든
    전체 롤백. 두 방향 롤백을 fault-injection 시험으로 증명.
  - `scorecard §8` `QP-020`: **restart recovery, stale-write** 8 passed (재시작·낡은쓰기 가드).
  - CAS(compare-and-set): 모든 예약 쓰기는 `(reservation_id, status='held', revision)`
    CAS에 `rowcount==1` 단언.
- **분류**: **지식 공백 + 정당한 복잡도**(분산/동시성은 본질적으로 어렵지만, ACID 기본은 학습 대상).
- **필요한 지식**: ACID 트랜잭션, all-or-nothing 커밋/롤백, 크래시·재시작 복구, CAS·낙관적 동시성.
- **왜 효율에 기여**: 상태 지속성의 문법을 알면 "중간에 죽으면?"을 설계 단계에서 답할 수 있다.

### 3.4 순수 함수·부작용 격리·결정론  — 우선순위: 상 (가장 많이 재작업된 영역)

- **증거**
  - `execution_kernel_v2_workboard.md` 전체: Kernel v2 계약이 **순수성(purity) 감사만
    6라운드 이상** 반복. `c74a491`→`0bbec72`→3축 감사(purity P1×2, auth P0/P1=0,
    KIS P0/P1=0) 거절→`6bfdb5d`(close final static purity gate)→`2f0ab85`.
  - 반복된 근본 원인: **모듈 전역 가변값**(`sorted(...)`, `json.loads(...)`,
    `model_dump()`가 만드는 mutable global), **정의 시점 호출**
    (`def f(cache=json.loads("[]"))`), 정규 인터프리터 메타데이터 오탐.
  - `QP-KER-010` 순수 evaluator는 broker/store/repository/audit/clock/env 권한이 **전무**하도록
    설계되고 AST 게이트로 강제(`61a4f93`, 병합 `fed4ed6`).
- **분류**: **지식 공백**. 재작업 총량은 감사가 정상 작동한 결과이나, 재발 개념(순수성·불변성·결정론)은 단일하다.
- **필요한 지식**: 순수 함수(입력→출력, 부작용 없음), 부작용(side effect)의 정의,
  불변성(immutability), 결정론(같은 입력→같은 출력), 이것이 왜 테스트·감사를 쉽게 만드는가.
- **왜 효율에 기여**: "이 함수는 무엇을 건드리는가?"를 습관적으로 물으면 부작용 누수 결함을 줄인다.

### 3.5 fail-closed(안전 실패) 기본값 사고  — 우선순위: 상 (거의 모든 작업에 등장)

- **증거**
  - `AGENTS.md`·`agent_collaboration_protocol.md §8`·전역 규칙 `security.md`:
    "**fail-open 기본값을 피하고 인증·권한·위험 판정 실패 시 안전하게 중단**".
  - `scorecard §8` `QP-050`: **`signals/service.py:341` NameError in fail-closed branch**
    — 가장 덜 실행되는 안전 분기에서 기초 오류가 표출(외부 진단으로 발견).
  - `25182df`(bind performance evidence fail closed): 증거 열화 시 티켓 만료 대신
    사유코드로 실행 차단(`strategy_activation_allowed`가 fail-closed).
  - 모든 스모크가 `broker=mock`, `live_trading_enabled=false`, operator `blocked`를 출력.
- **분류**: **지식/사고방식 공백** (개념은 단순하나 모든 분기에 일관 적용이 어려움).
- **필요한 지식**: fail-closed vs fail-open, 안전 기본값, "확신 없으면 거부", 오류 경로도 코드다.
- **왜 효율에 기여**: 안전 기본값을 먼저 두면 새 기능마다 위험 판정을 재발명하지 않는다.

### 3.6 TOCTOU와 시간·만료 경계  — 우선순위: 상

- **증거**
  - `execution_kernel_v2_workboard.md` Blockers: **재현된 P1** — Level 3 레거시 제출이
    `risk_check_expires_at`는 확인하지만 `OrderPlan.expires_at`는 확인하지 않아, **위험
    증거는 신선한데 이미 만료된 주문이 체결**됨. 단일 사전 검사로는 불충분, 양쪽 안전
    펜스+preclaim+restart+pre-POST 필요.
  - 커밋 `588f4df`(record expired order cutover blocker), `QP-KER-015`(`949aa7d`,
    만료 경화 + 두 안전 펜스).
- **분류**: **지식 공백** (여러 만료 시각의 일관성은 초보자에게 비직관적).
- **필요한 지식**: TOCTOU(time-of-check/time-of-use) 경쟁, 서로 다른 만료 시각의 정합성,
  검사와 사용 사이의 시간 창.

### 3.7 경계 조건·유니코드·전역 정의(totality)  — 우선순위: 중

- **증거**
  - `execution_kernel_v2_workboard.md` 카운터파트 리뷰: 레거시 `strategy_versions_match`가
    `str.isdigit()` 후 `int()`를 호출해 **`"²"`(U+00B2) 같은 유니코드 숫자에서 `ValueError`**
    발생 — 헬퍼가 전역적으로 정의되지 않음. superscript/fullwidth 유니코드 회귀 코퍼스로
    수정(`QP-KER-015` 신규 `test_strategy_version_matching.py`).
- **분류**: **지식 공백** (입력 다양성·경계값을 처음엔 과소평가).
- **필요한 지식**: 입력 검증, 경계값 사고, 유니코드/인코딩, 함수의 totality(모든 입력에 정의된 결과).

### 3.8 예외 경로 테스트 커버리지  — 우선순위: 중

- **증거**
  - `QP-050`의 NameError가 **fail-closed 분기**에서 나온 점(§3.5): 정상 경로만 테스트하면
    안전 분기의 기초 오류를 놓친다.
  - 반대 모범: kernel/reservation 시험이 fault-injection·재시작·동시성 분기를 명시 커버.
- **분류**: **지식 공백** (테스트 습관).
- **필요한 지식**: 예외·오류 경로 테스트, 커버리지 개념, 정적 분석/린트로 미실행 분기 잡기.

### 3.9 퀀트 도메인 정확성 — look-ahead·체결모델·비용  — 우선순위: 중 (라이브 경계 필수)

- **증거**
  - `4068d22`(no-lookahead signal replay): 미래 정보 누수 방지 리플레이 추가.
  - `scorecard §8` `QP-030`: **P2 — same-day forming-bar inclusion**(당일 미완성 봉 포함)
    이 look-ahead 위험, 자가 수정.
  - `STATUS.md` / `local_data_backtest_validation_report.md`: **limit=종가 체결모델은
    모멘텀 갭업에서 구조적 미체결**, `--limit-buffer-bps` 민감도로 승인 여부가 갈림
    (buffer 0bps는 Sharpe 0.144로 탈락, 50bps는 통과). 비용 기준 `backtest/costs.py`
    (수수료 1.40527bps/편도, 매도세 20bps).
  - 프로젝트에 `backtest-forensics` 스킬(look-ahead·overfitting·survivorship 감사)이 존재.
- **분류**: **정당한 도메인 복잡도 + 지식 공백**(편향 유형은 학습 가능).
- **필요한 지식**: look-ahead bias, 체결(fill) 모델 가정, 거래비용·세금, survivorship/overfitting.
- **경고**: 이 지식은 **연구용**이다. 백테스트 통과·페이퍼 성공은 라이브 승인 근거가 **아니다**(§6).

### 3.10 도구·환경 마찰 — git worktree·Windows·경로/권한  — 우선순위: 중

- **증거**
  - `scorecard §8.1` `COLLAB-V1-codex-core`: **P2(process) — `apply_patch`가 격리 워크트리
    대신 dirty main 워크스페이스를 대상으로 함**. "다음 미션은 편집 도구의 작업 디렉터리
    의미를 먼저 검증하라"는 learning note.
  - `roadmap_execution_workboard.md`(QP-EVT-030B): **머신 전역 temp 권한 오류**로 필수
    pytest 명령이 실패, 워크트리 로컬 `--basetemp`로 해결.
  - 프로토콜: 각 에이전트는 `claude/<mission>-<task>` **분리 워크트리**에서 자기 경로만 수정.
- **분류**: **지식/환경 공백** (도구 멘탈 모델).
- **필요한 지식**: git branch/worktree 격리 모델, 현재 작업 디렉터리, Windows 경로(공백·한글)·권한,
  왜 격리가 사용자 변경을 보호하는가.

### 3.11 협업 개념·계약 우선·독립 감사  — 우선순위: 하 (고급, 후반 학습)

- **증거**
  - `scorecard §8.1` `COLLAB-V1-scorecard`: **P2 — 사전(routing) 차원과 사후(retrospective)
    성능 차원을 혼동**, **완료된 워크보드의 옛 Git 권한을 현행 정책으로 오인**. 2회 재작업(rating 2).
  - Kernel/이벤트/예약 미션이 **코드 이전에 계약 문서**를 다수 커밋(`d3023a2`, `1c7488a`,
    `c0a625e` 등)한 뒤 구현.
- **분류**: **프로세스 공백** (개념적, 초보자에겐 이르다).
- **필요한 지식**: 계약/명세 우선 개발, 독립 리뷰 읽는 법, 사전 라우팅과 사후 회고의 분리.

## 4. 최우선 학습 공백 5선

빈도·기초성·라이브 리스크를 종합한 상위 5개. 커리큘럼은 이 순서를 우선한다.

| 순위 | 학습 공백 | 대표 증거 | 이 지식이 막았을 재작업 |
|---|---|---|---|
| 1 | **fail-closed 안전 기본값 사고** | `security.md`; `QP-050` fail-closed 분기 NameError; `25182df` | 안전 분기의 기초 오류, 위험 판정 재발명 |
| 2 | **금액의 부동소수점 → 정수/Decimal** | `QP-RM-00A` P1 float persistence; reservation 감사 `1e-6`/`0.01` 제거; `QP-RES-A1`(`a892210`) | 화폐 계산 P1, 잘못된 예약 |
| 3 | **멱등성 + 트랜잭션 원자성/크래시 복구** | `QP-040` fingerprint P2; `atomic_risk_reservation` 감사 §3(1)(5)(8)(9); `QP-020` 재시작 복구 | 중복 주문, 부분 쓰기, 재시작 손상 |
| 4 | **순수 함수·부작용 격리·결정론** | `execution_kernel_v2_workboard.md` 순수성 감사 6+라운드; `QP-KER-010`(`61a4f93`) | 모듈 전역 가변·정의시점 부작용 누수 |
| 5 | **퀀트 도메인 정확성(look-ahead·체결모델·비용)과 라이브 경계** | `4068d22`; `QP-030` forming-bar P2; `STATUS.md` limit-buffer 민감도 | 미래정보 누수, 비현실적 체결 가정, 조기 라이브 오판 |

## 5. 추천 학습 순서 (0개발 배경 전제)

각 단계는 이전 단계 없이는 비효율적이다. 위 5선을 이 순서 안에 배치했다.

0. **컴퓨터·터미널·Git 기초**: 파일/폴더, PowerShell 기본, `git status/log/diff` 읽기,
   커밋·브랜치 개념. (§3.10 대비)
1. **Python 읽고 쓰기 기초**: 변수·함수·자료구조·조건/반복, **예외(try/except)**.
2. **테스트 읽기(pytest)와 순수 함수**: `python -m pytest quantpilot/tests` 실행·해석,
   순수 함수/부작용/결정론. (5선 #4)
3. **수의 표현**: 부동소수점 오차, `Decimal`/정수 최소단위, 반올림 방향. (5선 #2)
4. **안전 사고**: fail-closed vs fail-open, 입력 검증, 경계값·유니코드, totality. (5선 #1, §3.7)
5. **상태와 지속성**: 상태 머신·주문 수명주기, ACID 트랜잭션, 멱등성, CAS, 크래시 복구. (5선 #3, §3.3)
6. **TOCTOU·시간 경계**: 검사와 사용 사이의 시간 창, 만료 시각 정합성. (§3.6)
7. **도구·환경 실무**: git worktree/branch 격리, Windows 경로·권한, 작업 디렉터리. (§3.10)
8. **퀀트 도메인 정확성**: look-ahead·체결모델·거래비용/세금·백테스트 편향. (5선 #5, §3.9)
9. **협업·계약 우선·독립 감사 읽기**: 사전 라우팅과 사후 회고 분리, 계약 문서 읽는 법. (§3.11)

## 6. QuantPilot-안전 실습 (오프라인·플래그 비활성)

모든 실습은 `LIVE_TRADING_ENABLED=false`, `BROKER_MODE=mock`, 네트워크·비밀 없이 수행한다.
실제 KIS 호출·실주문·플래그 변경은 실습 대상이 **아니다**.

- **테스트 스위트 관찰(2단계)**: `python -m pytest quantpilot/tests`를 실행하고 통과 수를
  읽는다. (참고: 로컬 temp 잠금 시 `--basetemp=.pytest_tmp --junitxml=...` 우회 —
  프로젝트 관례.)
- **스모크의 안전 출력 확인(4단계)**: `python -m quantpilot.jobs.run_smoke`를 실행해
  `broker=mock`, `live_trading_enabled=false`, operator `blocked`를 눈으로 확인.
- **부동소수점 실습(3단계)**: 파이썬에서 `0.1 + 0.2`, 그리고 `Decimal("0.1")+Decimal("0.2")`를
  비교. 금액을 "원 단위 정수"로 모델링해 보기. (5선 #2, `QP-RES-A1` 재현 축소판)
- **fail-closed 관찰(4단계)**: `python -m quantpilot.jobs.run_kis_paper_kill engage`가
  `{"status":"blocked","reason_code":"paper_kill_disabled"}`를 반환함을 확인 — 권한 없으면 거부.
- **상태 머신 읽기(5단계)**: `agent_collaboration_protocol.md`의 상태 흐름
  (`proposed→ready→in_progress→review→integrated→done`, `blocked`)과 주문 terminal 상태
  5종(`atomic_risk_reservation_v1_claude_audit.md §3(7)`)을 종이에 그려 보기.
- **look-ahead·체결모델 읽기(8단계)**: `local_data_backtest_validation_report.md`에서
  limit-buffer 민감도가 승인 여부를 어떻게 가르는지 확인. `4068d22`가 왜 no-lookahead
  리플레이를 추가했는지 설명해 보기.
- **worktree 실습(7단계)**: 연습용 worktree를 만들어 격리를 체험(사용자 dirty 경로는 건드리지 않음).

## 7. 라이브 트레이딩을 아직 승인하지 않는 지식 경계 (경고)

아래 지식을 익혔더라도 **실거래 권한의 근거가 되지 않는다**. 커리큘럼은 이 경계를 반복 강조해야 한다.

- **백테스트 통과 ≠ 라이브 준비**: `STATUS.md` 실측처럼 체결버퍼 가정 하나로 승인 여부가
  뒤집힌다(0bps 탈락, 50bps 통과). 가정이 결과를 만든다.
- **fake-client 테스트 통과 ≠ 실 KIS 검증**: `atomic_risk_reservation_v1_claude_audit.md §7`
  및 `roadmap_acceptance_matrix.md` Gate P — 실 buying-power/취소 TR/세션 캘린더는 미검증이며
  명시적 사용자 권한이 필요하다.
- **모의/페이퍼 성공 ≠ 실거래 인에이블**: `live_trading_enablement_checklist.md`의 12개 항목은
  **전부 사람 서명**이 필요하며 현재 **0/12**다.
- **개별 지식 습득 ≠ 안전 불변식 완화 권한**: `LIVE_TRADING_ENABLED=false`,
  `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`,
  `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock`는 기본값이며 학습 진도로 바뀌지 않는다.
- **LLM/RL 출력은 브로커 주문을 직접 생성·승인·제출할 수 없다**(`AGENTS.md`). 이 규칙은
  학습 여부와 무관하게 유지된다.

## 8. 안전·권한·부작용 진술

- 이 작업은 저장소 내부의 **읽기 전용 조사**였고, 유일하게 생성한 파일은 이 문서
  (`docs/quantpilot_beginner_learning_gap_evidence.md`)다. 코드·테스트·작업보드·다른 문서를
  변경하지 않았다.
- 네트워크·KIS·브로커·외부 커넥터·패키지 설치를 호출하지 않았고, 비밀·계좌번호·자격증명을
  읽거나 출력하지 않았다.
- 모든 안전 불변식을 확인만 했고 변경하지 않았다(§7 목록).
- 커밋은 자기 소유 경로 한 개만 stage/commit했고, 사용자 dirty 파일이나 다른 브랜치를
  건드리지 않았다.

## 9. 알려진 한계

- 증거 창 앞부분(2026-06-16~07-05)은 커밋이 없어 관측 불가(§2-1).
- Codex 세션 원문 부재로 재작업의 내부 과정은 요약 결과에서 추론했다(§2-2).
- "지식 공백" 분류는 반복 개념 범주에 근거한 추론이며, 개인 능력 평가가 아니다.
- 우선순위는 저장소 증거 기반 추정이다. `QP-LRN-02` 커리큘럼이 학습자 반응에 따라 순서를
  조정할 수 있다.
- 본 문서는 학습 요구사항 분석이며, 어떤 항목도 라이브 트레이딩 준비를 의미하지 않는다.
