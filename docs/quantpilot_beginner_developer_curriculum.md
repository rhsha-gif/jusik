# 개발 지식 0에서 시작하는 QuantPilot 프로젝트 오너 커리큘럼

> 대상: 개발 경험이 전혀 없지만 QuantPilot의 방향을 결정하고, AI 에이전트가 만든 결과를 검토하며,
> 안전하게 프로젝트를 운영하고 싶은 사람
>
> 권장 기간: **준비주 1주 + 본과정 16주(총 17주), 주 6시간**
> (개념 2시간 + 코드 읽기 2시간 + 실습·회고 2시간)
>
> 기준일: 2026-07-16

## 1. 먼저 내릴 결론

“문제를 방치한 원인이 전부 나의 개발 지식 부족이다”라는 판단은 절반만 맞다.
QuantPilot은 초보 프로젝트가 아니다. Python, FastAPI, React, SQLite, 상태 머신, 동시성,
백테스트 편향, 주문 안전, 감사 로그가 한꺼번에 얽힌 **안전 중요 시스템**이다. 최근 재작업 중 상당수는
전문 에이전트도 첫 시도에 놓쳤고, 독립 감사와 테스트가 정상적으로 잡아낸 문제였다.

그러므로 목표를 “혼자 모든 코드를 작성하는 개발자”로 잡을 필요는 없다. 먼저 다음 능력을 갖춘
**기술을 이해하는 프로젝트 오너**가 되는 것이 가장 효율적이다.

1. 요구사항을 모호하지 않은 완료 조건으로 바꾼다.
2. 코드·테스트·로그에서 주장과 증거를 구분한다.
3. 안전 경계를 넘는 변경을 알아보고 멈춘다.
4. 실패 원인을 분류하고 적합한 에이전트에게 맡긴다.
5. 검증 명령의 결과를 읽고 승인·보류를 결정한다.

코드를 능숙하게 쓰는 것은 그다음 목표다.

## 2. 이 커리큘럼을 만든 근거

증거 범위는 2026-06-16~2026-07-16으로 잡았지만, 저장소에서 실제로 관찰할 수 있는 커밋은
2026-07-06~2026-07-16에 집중되어 있다. 이 기간 약 140개 커밋, 최근 작업보드·감사·상태 문서,
최근 Codex 세션 요약을 검토했다. 상세 근거와 한계는
[초보자 학습 공백 증거 분석](quantpilot_beginner_learning_gap_evidence.md)에 정리되어 있다.

중요한 해석 원칙은 다음과 같다.

- 재작업이 있었다고 해서 사용자가 잘못했다는 뜻은 아니다.
- 한 번뿐인 어려운 구현은 학습 공백으로 단정하지 않았다.
- 서로 다른 작업에서 같은 개념이 반복해서 문제를 만들었을 때 학습 주제로 채택했다.
- 테스트와 독립 감사에서 병합 전에 발견된 문제는 안전 절차가 작동한 증거이기도 하다.

### 문제에서 학습 주제로 바꾼 결과

| 관찰된 문제 패턴 | 저장소의 대표 근거 | 먼저 배울 지식 | 배치 |
|---|---|---|---|
| 금액·수량에 float가 섞여 예약 계산 재작업 | `QP-RM-00A`, `QP-RES-A1`, `a892210` | 정수 최소 단위, `Decimal`, 보수적 반올림 | 4주차 |
| 멱등 키·요청 지문·출처 연결 재작업 | `QP-040`, `cb10b15`, `d52321b` | 멱등성, provenance, 재시도 안전성 | 9주차 |
| 부분 저장·재시작·낡은 쓰기 위험 | 원자적 리스크 예약 감사, `QP-020` | ACID, 트랜잭션, CAS, 복구 | 8~10주차 |
| 순수성 감사와 부작용 누수의 반복 | 실행 커널 작업보드, `QP-KER-010`, `61a4f93` | 순수 함수, 불변성, 결정론 | 5~6주차 |
| 안전 실패 분기 자체의 오류 | `QP-050`, `25182df` | fail-closed, 오류 경로 테스트 | 6·11주차 |
| 검사 뒤 만료되는 주문 계획 | `QP-KER-015`, `588f4df` | TOCTOU, 신선도, 만료 시각 | 10주차 |
| 당일 미완성 봉·체결 가정·비용 민감도 | `QP-030`, `4068d22`, 백테스트 검증 보고서 | look-ahead, 체결 모델, 비용, 편향 | 12주차 |
| dirty main·worktree·Windows 경로 마찰 | 능력 점수표의 COLLAB-V1 회고 | Git, worktree, 현재 경로, 권한 | 1~2주차 |
| 계약·작업보드가 여러 차례 수정됨 | 협업 프로토콜·커널·이벤트 작업보드 | 명세 우선, 독립 검토, 증거 기반 승인 | 15주차 |

API와 프론트엔드는 반복 결함의 학습 공백으로 분류한 것이 아니다. 현재 QuantPilot의 실제 기술 스택과
변경 영향 범위를 이해하기 위해 7·14주차에 포함했다.

## 3. 학습 운영 원칙

### 시간표

매주 다음 리듬을 권장한다.

- 1회차 2시간: 개념을 배우고 자기 말로 10줄 이내 요약
- 2회차 2시간: QuantPilot 코드와 테스트에서 그 개념의 실제 위치 찾기
- 3회차 2시간: fixture/mock 기반 실습, 결과 기록, 모르는 점 질문 만들기

준비주를 포함한 17주는 마감일이 아니라 권장 순서다. 주차별 통과 기준을 충족하지 못하면 다음 주제로
넘어가지 않는다.
주 3시간만 가능하면 각 주차를 2주에 걸쳐 진행하면 된다.

### 학습의 증거

영상이나 글을 본 것만으로는 완료가 아니다. 매주 다음 네 가지를 남긴다.

1. **설명**: 개념을 비개발자에게 설명하는 5~10문장
2. **위치**: 프로젝트 안에서 관련 코드·테스트·문서 경로 각 1개 이상
3. **실행**: 안전한 명령과 실제 결과
4. **판단**: 이 개념이 깨졌을 때 어떤 위험이 생기는지 한 문장

개인 학습 노트는 저장소 밖에 두어도 된다. 계좌 정보, API 키, 토큰, 실제 주문 정보는 절대로 기록하지 않는다.

### 항상 유지할 안전 기본값

```text
LIVE_TRADING_ENABLED=false
GUARDED_AUTOPILOT_ENABLED=false
FULLY_AUTOMATED_OPERATOR_ENABLED=false
MARKET_ORDERS_ENABLED=false
BROKER_MODE=mock
DATA_MODE=fixture
```

이 커리큘럼의 어떤 단계를 끝내도 위 값을 바꿀 권한이 생기지 않는다. LLM/RL 출력 역시 주문을 직접
생성·승인·제출할 수 없다.

## 4. 시작 전 진단: 점수가 아니라 출발점 찾기

다음 항목을 설명할 수 없으면 정상이다. 0주차부터 시작하면 된다.

- 파일과 폴더, 절대 경로와 상대 경로의 차이
- 터미널에서 현재 폴더를 확인하는 방법
- Git의 저장소, 변경, 커밋, 브랜치가 무엇인지
- 변수, 함수, 조건문, 반복문이 무엇인지
- JSON 한 개를 읽고 key와 value를 찾는 방법
- 테스트가 무엇을 보장하고 무엇을 보장하지 않는지
- API, 데이터베이스, 프론트엔드가 각각 하는 일
- mock, fixture, paper trading, live trading의 차이

시작 전에 [README](../README.md), [현재 워크플로우](current_project_workflow.md),
[안전 체크리스트](safety_checklist.md)는 내용이 이해되지 않아도 한 번 훑는다. 본과정 16주 뒤 같은 문서를 다시 읽어
이해 범위가 얼마나 넓어졌는지 비교한다.

## 5. 준비주 + 16주 전체 지도

| 주차 | 핵심 주제 | 그 주에 답할 수 있어야 하는 질문 |
|---:|---|---|
| 0 | 컴퓨터·터미널·안전 기본값 | “지금 어느 폴더에서 무엇을 실행하려는가?” |
| 1 | 프로젝트 지도와 Git 읽기 | “무엇이 바뀌었고 아직 커밋되지 않았는가?” |
| 2 | 브랜치·worktree·안전한 변경 관리 | “사용자 작업을 건드리지 않고 격리할 수 있는가?” |
| 3 | Python 기초 | “이 함수가 어떤 입력을 받아 어떤 출력을 내는가?” |
| 4 | 타입·금액·스키마 | “왜 돈을 float로 저장하면 위험한가?” |
| 5 | 함수 설계·부작용·예외 | “같은 입력에서 같은 결과가 나오는가?” |
| 6 | pytest·fixture·디버깅 | “실패 메시지가 실제로 말하는 원인은 무엇인가?” |
| 7 | HTTP·FastAPI·Pydantic·OpenAPI | “화면 요청이 어느 검증 경계를 통과하는가?” |
| 8 | SQL·SQLite·ACID | “중간 실패 시 전체가 되돌아가는가?” |
| 9 | 상태 머신·멱등성·감사·조정 | “같은 요청이 두 번 오면 무엇이 일어나는가?” |
| 10 | 동시성·재시작·시간 경계 | “검사 후 실행 전 상태가 바뀌면 안전한가?” |
| 11 | 트레이딩 안전 공학 | “확신이 없을 때 시스템은 왜 멈추는가?” |
| 12 | 시장 데이터·백테스트 정확성 | “이 성과에 미래 정보나 비현실적 체결이 섞였는가?” |
| 13 | 주문 수명주기와 paper/live 경계 | “주문이 어디서 생성·승인·제출·조정되는가?” |
| 14 | React UI와 API 계약 | “화면의 값이 실제 백엔드 상태와 일치하는가?” |
| 15 | 요구사항·작업보드·코드 리뷰 | “완료 주장을 어떤 증거로 승인할 것인가?” |
| 16 | 종합 과제 | “낮은 위험의 변경을 독립적으로 승인 또는 보류할 수 있는가?” |

## 6. 주차별 커리큘럼

### 0주차: 컴퓨터·터미널·QuantPilot 안전 경계

**배울 것**

- 파일, 폴더, 확장자, 경로, 현재 작업 디렉터리
- PowerShell에서 명령·옵션·출력·종료 코드의 의미
- `fixture`, `mock`, `paper`, `live`의 차이
- “읽기”, “로컬 실행”, “외부 상태 변경”의 위험 차이
- fail-closed의 첫 정의: 확신이 없거나 오류가 나면 위험 행동을 허용하지 않고 차단함

**최초 1회 환경 준비**

이 문서의 상대 경로 명령은 별도 안내가 없으면 항상 **저장소 루트**에서 실행한다. 현재 설치 위치에서는
다음과 같이 이동한다. 프로젝트를 옮겼다면 첫 줄만 실제 저장소 절대 경로로 바꾼다.

```powershell
Set-Location "C:\Users\goyan\OneDrive\문서\코덱스\주식트레이더"
Test-Path .\pyproject.toml
Test-Path .\quantpilot
```

두 `Test-Path` 결과가 모두 `True`여야 한다. 그런 다음 필수 도구를 확인한다.

```powershell
python --version
git --version
node --version
npm --version
rg --version
```

- Python은 3.11 이상을 사용한다.
- Node/npm은 14주차 프론트엔드 실습에 필요하다.
- `rg`가 없으면 1주차 파일 목록 명령 대신
  `Get-ChildItem -Recurse -File .\quantpilot | Select-Object -First 30`을 사용해도 된다.
- 도구를 새로 설치해야 한다면 공식 배포처를 사용하고, 설치 범위를 에이전트나 경험자와 먼저 확인한다.

백엔드 의존성이 아직 설치되지 않았다면 저장소 루트에서 전용 가상환경을 만든다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

PowerShell을 다시 열면 테스트 전에 `.\.venv\Scripts\Activate.ps1`을 다시 실행한다. 실제 broker 자격증명이나
live 관련 환경변수는 설정하지 않는다.

**안전 실습**

```powershell
Get-Location
Get-ChildItem
git status --short --branch
```

출력을 복사하는 데 그치지 말고 각 줄이 무엇을 뜻하는지 적는다. `git status`에 보이는 기존 변경은
학습자가 만든 것이 아니면 수정·삭제·stage하지 않는다.

**통과 기준**

- 명령 실행 전 현재 폴더를 확인할 수 있다.
- `pyproject.toml`과 `quantpilot` 폴더로 저장소 루트를 확인할 수 있다.
- Python 가상환경을 활성화하고 필수 도구의 설치 여부를 확인할 수 있다.
- `BROKER_MODE=mock`과 `LIVE_TRADING_ENABLED=false`의 의미를 설명한다.
- fail-closed를 “확신 없으면 위험 행동을 차단”이라고 자기 말로 설명한다.
- 실제 자격증명이 왜 학습 파일과 Git에 들어가면 안 되는지 설명한다.

### 1주차: 프로젝트 지도와 Git 읽기

**배울 것**

- Git 저장소, working tree, stage, commit, HEAD
- `status`, `log`, `diff`, `show`는 서로 무엇을 보여 주는가
- QuantPilot의 큰 폴더: `packages/core`, `packages/db`, `services/api`, `apps/web`, `tests`, `docs`

**프로젝트 읽기**

- [현재 워크플로우의 시스템 개요](current_project_workflow.md#1-한눈에-보는-시스템)
- [`pyproject.toml`](../pyproject.toml)
- [`quantpilot/apps/web/package.json`](../quantpilot/apps/web/package.json)

**안전 실습**

```powershell
git status --short --branch
git log -5 --oneline
git diff --stat
git show --stat --oneline HEAD
rg --files quantpilot | Select-Object -First 30
```

**산출물**: “데이터가 들어와 주문 제안이 되기까지”를 상자 7개 이하의 화살표로 그린다.

**통과 기준**

- `git diff`가 빈 이유와 `git status`가 깨끗하다는 말의 의미를 설명한다.
- 문서, 테스트, 백엔드, 프론트엔드 파일을 각각 한 개씩 찾는다.
- 커밋 해시를 이용해 특정 시점의 변경을 찾을 수 있다.

### 2주차: 브랜치·worktree·안전한 변경 관리

**배울 것**

- 브랜치와 worktree가 해결하는 문제
- 병합, cherry-pick, 충돌의 개념
- dirty main에서 바로 수정하면 다른 작업을 덮을 수 있는 이유
- 작은 커밋, 소유 경로, 되돌릴 수 있는 변경

**프로젝트 읽기**

- [협업 프로토콜](agent_collaboration_protocol.md)의 worktree·branch·commit 절
- [능력 점수표](agent_capability_scorecard.md)의 COLLAB-V1 process 회고

**안전 실습**

코드를 바꾸지 않고 `git worktree list --porcelain` 결과를 읽는다. 별도 연습용 저장소에서만 새 브랜치와
worktree를 만들어 보고, 이 프로젝트의 기존 worktree는 삭제하지 않는다.

**통과 기준**

- branch와 worktree의 차이를 설명한다.
- “어떤 경로를 누가 수정하는가”를 작업 전에 적을 수 있다.
- `git reset --hard`, 강제 push, 재귀 삭제를 승인 없이 실행하면 안 되는 이유를 설명한다.

### 3주차: Python을 읽기 위한 최소 문법

**배울 것**

- 값과 변수, 문자열·정수·불리언·리스트·딕셔너리
- `if`, `for`, 함수, 매개변수, 반환값
- 모듈과 import, 클래스와 객체의 최소 개념
- `None`, 예외, `try/except`

**프로젝트 읽기 순서**

1. [`data/mode.py`](../quantpilot/packages/core/data/mode.py)
2. [`risk/types.py`](../quantpilot/packages/core/risk/types.py)
3. [`execution/transitions.py`](../quantpilot/packages/core/execution/transitions.py)

처음에는 구현을 외우지 않는다. 입력, 분기, 반환값, 예외에 색을 달리 표시한다.

**안전 실습**

프로젝트 밖 연습 파일에서 두 정수를 더하는 함수, 음수를 거부하는 함수, 리스트를 합산하는 함수를 작성한다.
QuantPilot 코드는 아직 수정하지 않는다.

**통과 기준**

- 간단한 함수에서 입력·출력·오류 조건을 표시한다.
- stack trace의 마지막 줄에서 예외 종류와 메시지를 찾는다.
- 모르는 문법을 한 줄 단위로 질문할 수 있다.

### 4주차: 타입·금액 표현·Pydantic 스키마

**배울 것**

- type hint와 런타임 검증의 차이
- dataclass/Pydantic model이 데이터 계약 역할을 하는 방식
- IEEE-754 부동소수점 오차
- 돈·수량은 정수 최소 단위 또는 `Decimal`로 다루는 이유
- 반올림 방향이 리스크를 줄이거나 늘리는 방식

**프로젝트 근거**

- [학습 공백 증거 §3.1](quantpilot_beginner_learning_gap_evidence.md)
- [`schemas.py`](../quantpilot/packages/core/schemas.py)에서 금액·수량 필드 찾기
- [원자적 리스크 예약 감사](atomic_risk_reservation_v1_claude_audit.md)에서 `Decimal` 검색

**안전 실습**

프로젝트 밖 Python REPL에서 `0.1 + 0.2`와 `Decimal("0.1") + Decimal("0.2")`를 비교한다.
매수 예약 금액은 보수적으로 올리고 가용 현금은 보수적으로 내리는 이유를 말로 설명한다.

**통과 기준**

- 돈을 float로 저장했을 때 생길 수 있는 실제 오류를 설명한다.
- schema validation과 비즈니스 risk check가 같은 것이 아님을 설명한다.
- 금액 필드 리뷰에서 단위, 타입, 반올림 방향을 질문한다.

### 5주차: 순수 함수·불변성·부작용·결정론

**배울 것**

- 순수 함수: 같은 입력이면 같은 출력, 외부 상태를 몰래 바꾸지 않음
- 부작용: DB 쓰기, 네트워크, 로그, 시간 읽기, 환경변수 읽기
- mutable과 immutable, 전역 상태가 테스트를 어렵게 만드는 이유
- 기본 인자에서 함수를 호출하거나 가변 객체를 공유하는 위험

**프로젝트 읽기**

- [`execution/kernel.py`](../quantpilot/packages/core/execution/kernel.py)
- [`execution/reducer.py`](../quantpilot/packages/core/execution/reducer.py)
- [실행 커널 계약](execution_kernel_v2_contract.md)의 purity·결정론 조건

**안전 실습**

코드 두 조각을 보고 순수 계산과 외부 효과를 분리해 표로 만든다. 직접 수정하기보다 “시간, DB, 환경값을
함수 인자로 주입하면 무엇이 좋아지는가”를 설명한다.

**통과 기준**

- 순수 함수와 부작용이 있는 함수를 구별한다.
- 결정론이 fixture-first 테스트와 감사 재현성에 필요한 이유를 설명한다.
- 숨은 전역 상태가 재시작·동시 실행에서 어떤 문제를 만드는지 예를 든다.

### 6주차: pytest·fixture·실패 원인 찾기

**배울 것**

- 단위·통합·smoke 테스트의 차이
- arrange-act-assert, fixture, fake client, mock
- happy path, 오류 경로, 경계값, 재시작 테스트
- 0주차의 fail-closed 기본값을 오류 경로 테스트에 적용하는 법
- 실패한 테스트를 약화하지 않고 원인을 고치는 원칙

**안전 실습**

```powershell
python -m pytest quantpilot/tests/unit/test_data_mode.py
python -m pytest quantpilot/tests/unit/test_safety_invariant_baseline.py
python -m pytest quantpilot/tests
```

Windows 임시 폴더 권한 때문에 실패할 때만 다음 대체 명령을 쓴다.

```powershell
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp
```

**오류 읽기 순서**

1. 실패한 테스트 이름
2. 기대값과 실제값
3. 최초로 프로젝트 코드에 들어온 stack trace 위치
4. 같은 원인을 공유하는 다른 실패
5. 테스트 문제인지 구현 문제인지 판단할 증거

**통과 기준**

- 테스트 개수, 실패, 오류, skip을 구분한다.
- 정상 경로 테스트만 통과해도 안전하다고 말할 수 없는 이유를 설명한다.
- `QP-050`의 fail-closed 분기 NameError 같은 오류를 왜 오류 경로 테스트가 찾아야 하는지 설명한다.

### 7주차: HTTP·FastAPI·Pydantic·OpenAPI

**배울 것**

- 클라이언트/서버, request/response, JSON
- HTTP method와 대표 status code: 200, 400, 404, 409, 422, 503
- router, dependency, service, repository의 책임
- Pydantic 입력 검증과 OpenAPI 계약

**프로젝트 읽기**

- [`services/api/main.py`](../quantpilot/services/api/main.py)
- [`services/api/routers/orders.py`](../quantpilot/services/api/routers/orders.py)
- [`openapi.json`](../openapi.json)
- [`test_api_cors.py`](../quantpilot/tests/unit/test_api_cors.py)

**안전 실습**

서버를 띄우기 전에 테스트에서 요청과 응답을 읽는다. 그다음 필요하면 fixture 기본값에서만 로컬 API를 띄운다.

```powershell
python -m uvicorn quantpilot.services.api.main:app --reload --port 8010
```

**통과 기준**

- 잘못된 입력이 어느 계층에서 422/409/503이 되는지 추적한다.
- API schema가 바뀌면 백엔드 테스트, `openapi.json`, 프론트 타입이 함께 영향을 받는다고 설명한다.
- CORS가 거래 권한이 아니라 브라우저 origin 경계임을 설명한다.

### 8주차: SQL·SQLite·ACID 트랜잭션

**배울 것**

- table, row, column, primary key, index
- CRUD와 schema migration
- ACID, commit, rollback, transaction
- “두 개를 함께 저장하거나 둘 다 저장하지 않는다”는 원자성

**프로젝트 읽기**

- [`db/repositories.py`](../quantpilot/packages/db/repositories.py)
- [`db/sqlite_repositories.py`](../quantpilot/packages/db/sqlite_repositories.py)
- [`test_paper_dispatch_persistence.py`](../quantpilot/tests/unit/test_paper_dispatch_persistence.py)
- 원자적 리스크 예약 감사의 rollback·migration 절

**안전 실습**

프로젝트 밖 임시 SQLite DB에서 한 transaction 안에 두 행을 쓰고, 의도적으로 예외를 내어 둘 다 rollback되는지
확인한다. 실제 paper DB나 운영 데이터는 열거나 수정하지 않는다.

**통과 기준**

- migration과 일반 데이터 변경의 차이를 설명한다.
- 주문 dispatch와 리스크 예약을 원자적으로 저장해야 하는 이유를 설명한다.
- 중간 실패 뒤 일부 행만 남는 상황을 테스트로 증명하는 방법을 말한다.

### 9주차: 상태 머신·멱등성·감사 로그·reconciliation

**배울 것**

- 상태, 전이, 종결 상태, 허용되지 않은 전이
- 멱등 키와 요청 fingerprint
- provenance: 결과가 어떤 입력·정책·결정에서 왔는가
- audit log와 reconciliation의 역할 차이
- exactly-once가 어려운 이유와 “모호한 POST를 자동 재시도하지 않음”

**프로젝트 읽기**

- [`execution/state_machine.py`](../quantpilot/packages/core/execution/state_machine.py)
- [`execution/events.py`](../quantpilot/packages/core/execution/events.py)
- [`execution/paper_reconciliation.py`](../quantpilot/packages/core/execution/paper_reconciliation.py)
- [canonical event 계약](contracts/canonical_order_events_v1.md)

**안전 실습**

`draft -> risk_checked -> proposed -> user_approved -> submitted -> filled`를 직접 그리고,
cancel/reject/expire/unknown 결과가 어디로 가는지 표시한다. 같은 요청이 두 번 왔을 때 “기존 결과 반환”,
“충돌”, “새 처리” 중 무엇이어야 하는지 사례별로 적는다.

**통과 기준**

- 상태 머신이 단순 문자열 모음과 다른 이유를 설명한다.
- 같은 idempotency key에 다른 payload가 오면 거부해야 하는 이유를 설명한다.
- audit log가 broker 실제 상태를 대신하지 못하고 reconciliation이 필요한 이유를 설명한다.

### 10주차: 동시성·크래시 복구·TOCTOU·시간 경계

**배울 것**

- 동시에 두 작업이 같은 상태를 읽을 때 생기는 race condition
- CAS/낙관적 동시성, revision, lease/fencing
- 프로세스가 중간에 죽은 뒤 restart recovery
- TOCTOU: 검사 시점과 사용 시점 사이에 상태가 바뀌는 문제
- quote, risk check, order plan의 서로 다른 만료 시각

**프로젝트 읽기**

- [`test_paper_risk_reservation_model.py`](../quantpilot/tests/unit/test_paper_risk_reservation_model.py)
- [`test_paper_dispatch_persistence.py`](../quantpilot/tests/unit/test_paper_dispatch_persistence.py)
- [실행 커널 작업보드](execution_kernel_v2_workboard.md)에서 `expired`, `restart`, `TOCTOU` 검색

**안전 실습**

“09:00에 위험 검사를 통과했지만 09:05 제출 시 가격·정책·kill switch가 바뀐 상황”을 시간선으로 그린다.
제출 직전 다시 확인할 항목을 목록화한다.

**통과 기준**

- 오래된 revision이 새 상태를 덮어쓰면 안 되는 이유를 설명한다.
- `outcome_unknown`을 성공이나 실패로 성급하게 바꾸면 안 되는 이유를 설명한다.
- 시간에 민감한 증거는 사용 직전에 다시 검사해야 한다고 판단한다.

### 11주차: 트레이딩 안전 공학

**배울 것**

- fail-open과 fail-closed
- pre-trade risk check, kill switch, authority, approval
- 최소 권한과 안전 기본값
- 신규 위험 증가 주문과 위험 축소 주문의 차이
- 오류가 났을 때 조용한 fallback보다 명시적 차단이 필요한 이유

**프로젝트 읽기**

- [AGENTS.md](../AGENTS.md)의 QuantPilot safety adapter
- [안전 체크리스트](safety_checklist.md)
- [`risk/gatekeeper.py`](../quantpilot/packages/core/risk/gatekeeper.py)
- [`test_authority_checks.py`](../quantpilot/tests/unit/test_authority_checks.py)

**안전 실습**

```powershell
python -m quantpilot.jobs.run_smoke
```

결과에서 최소한 `broker=mock`, `live_trading_enabled=false`, operator가 차단 상태임을 확인한다.
출력이 다르면 원인을 조사하되 안전 플래그를 켜서 통과시키지 않는다.

**통과 기준**

- “데이터가 없으면 fixture로 조용히 대체”가 왜 위험할 수 있는지 설명한다.
- kill switch 해제에 별도 확인과 재승인이 필요한 이유를 설명한다.
- 테스트를 통과시키기 위해 안전 검사를 제거하거나 완화하면 안 된다고 판단한다.

### 12주차: 시장 데이터와 백테스트 정확성

**배울 것**

- OHLCV, 완성 봉과 형성 중인 봉
- look-ahead bias, survivorship bias, overfitting
- 거래비용, 세금, slippage, limit fill 가정
- sample size, out-of-sample, 성과 지표의 불확실성
- fixture 결과와 실제 시장 검증의 차이

**프로젝트 읽기**

- [로컬 데이터 백테스트 검증 보고서](local_data_backtest_validation_report.md)
- [`backtest/replay.py`](../quantpilot/packages/core/backtest/replay.py)
- [`backtest/costs.py`](../quantpilot/packages/core/backtest/costs.py)
- [`test_backtest_replay.py`](../quantpilot/tests/unit/test_backtest_replay.py)

**안전 실습**

보고서에서 체결 buffer가 바뀔 때 성과가 어떻게 달라지는지 표로 옮긴다. “좋은 수익률”보다 먼저 데이터 시점,
체결 가능성, 비용, 결측, 표본 수를 확인한다.

**통과 기준**

- 같은 날 종가를 보고 같은 날 종가에 체결했다고 가정하는 위험을 설명한다.
- 백테스트 통과가 paper/live 승인과 같은 뜻이 아니라고 설명한다.
- 민감도 분석에서 작은 가정 변화로 결과가 뒤집히면 보수적으로 판단한다.

### 13주차: 주문 수명주기와 paper/live 경계

**배울 것**

- signal, intent, proposal, approval, order, fill, position의 차이
- mock broker와 KIS paper adapter의 차이
- 신규 주문보다 reconciliation·보호 주문이 먼저인 이유
- paper 성공이 live 안전성을 증명하지 못하는 이유

**프로젝트 읽기**

- [현재 워크플로우 §6](current_project_workflow.md#6-데이터에서-주문까지의-전체-흐름)
- [`execution/paper_submission.py`](../quantpilot/packages/core/execution/paper_submission.py)
- [`brokers/mock_broker.py`](../quantpilot/packages/brokers/mock_broker.py)
- [`test_paper_submission_coordinator.py`](../quantpilot/tests/unit/test_paper_submission_coordinator.py)

**안전 실습**

하나의 가상 매수 아이디어가 signal에서 fill과 position으로 바뀌기까지 필요한 승인·위험·상태 증거를 순서대로
적는다. 실제 broker 자격증명과 paper 주문 제출은 사용하지 않는다.

**통과 기준**

- UI의 “승인” 버튼이 broker 제출과 같은 것이 아님을 설명한다.
- LLM 분석이 주문 권한을 가질 수 없는 이유를 설명한다.
- live 후보, canary, scaled 단계가 별도 증거와 사람 승인을 요구한다고 판단한다.

### 14주차: React UI와 API 계약

**배울 것**

- HTML/CSS/JavaScript/TypeScript/React의 역할 차이
- component, props, state, query, loading/error/empty 상태
- 프론트 타입과 OpenAPI의 연결
- 화면 표시가 권한 또는 실제 broker 상태의 증거가 아닌 이유

**프로젝트 읽기**

- [`apps/web/src/App.tsx`](../quantpilot/apps/web/src/App.tsx)
- [`apps/web/src/lib/api.ts`](../quantpilot/apps/web/src/lib/api.ts)
- [`apps/web/src/lib/openapi.d.ts`](../quantpilot/apps/web/src/lib/openapi.d.ts)
- [`apps/web/src/test/safety-banner.test.tsx`](../quantpilot/apps/web/src/test/safety-banner.test.tsx)

**안전 실습**

```powershell
Set-Location "C:\Users\goyan\OneDrive\문서\코덱스\주식트레이더"
Set-Location quantpilot/apps/web
npm ci
npm run test
npm run build
```

**통과 기준**

- loading, error, empty, success 네 상태를 구분한다.
- 백엔드 schema 변경 뒤 프론트 타입과 테스트를 다시 검증해야 한다고 판단한다.
- 안전 배너가 실제 백엔드 enforcement를 대신할 수 없다고 설명한다.

### 15주차: 요구사항·작업보드·코드 리뷰·에이전트 지시

**배울 것**

- 목표, 범위, 제외 범위, 안전 불변식, 완료 조건
- 계약 먼저 작성하기와 구현 뒤 회고의 차이
- P0/P1/P2/P3의 의미와 병합 차단 기준
- 주장, 증거, 추론, 미검증 항목을 분리하는 법
- 작업 분해, 소유 경로, reviewer 독립성

**프로젝트 읽기**

- [작업보드 템플릿](agent_workboard_template.md)
- [협업 프로토콜](agent_collaboration_protocol.md)
- [능력 점수표](agent_capability_scorecard.md)
- 최근 완료 작업보드 한 개와 감사 문서 한 개

**실습**

코드 변경 없이 가상의 작은 문서 변경을 작업보드 형식으로 작성한다. 다음 항목이 반드시 있어야 한다.

- 관찰 가능한 목표
- 바꾸지 않을 것
- 소유 파일
- 예상 위험
- 정확한 검증 명령
- 실패 시 중단 조건
- reviewer가 독립적으로 확인할 증거

**통과 기준**

- “테스트 통과”만으로 완료 조건이 충분하지 않은 경우를 설명한다.
- 에이전트의 자신감보다 커밋·테스트·실행 결과를 우선한다.
- 모르는 고위험 결정을 추측으로 승인하지 않고 한 개의 정확한 질문으로 올린다.

### 16주차: 종합 과제 — 읽고, 재현하고, 승인하거나 보류하기

코드를 새로 구현하는 과제가 아니다. 최근의 낮은 위험 문제나 문서 변경 하나를 선택해 **검토 보고서**를 만든다.

**필수 구성**

1. 문제를 한 문장으로 설명
2. 사용자에게 보이는 영향과 안전 영향 분리
3. 관련 코드·테스트·문서 경로
4. 재현 방법과 실제 관찰 결과
5. 원인과 단순 증상의 구분
6. 변경 범위와 제외 범위
7. 정상·오류·경계·재시작 검증
8. 남은 불확실성
9. 승인 또는 보류 결론과 이유

**최종 안전 검증**

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
```

프론트엔드가 범위에 들어갔다면 다음도 수행한다.

```powershell
Set-Location quantpilot/apps/web
npm run test
npm run build
```

**수료 기준**

- 명령 결과를 직접 읽고 사실과 추론을 구분한다.
- 안전 기본값을 유지한 채 증거를 수집한다.
- 실패를 숨기거나 테스트를 약화하지 않는다.
- 모르는 항목을 명시하고, 필요한 담당자·검증·권한을 정확히 요청한다.
- low-risk 변경은 근거를 들어 승인 또는 보류할 수 있다.

## 7. 단계별 능력 게이트

주차보다 이 게이트가 중요하다.

### Gate A: 저장소를 안전하게 관찰한다 — 2주차 이후

- 현재 경로, branch, dirty 파일을 확인한다.
- 기존 사용자 변경을 구별하고 건드리지 않는다.
- `status`, `log`, `diff`, `show`로 변경 근거를 찾는다.

### Gate B: Python과 테스트 결과를 읽는다 — 6주차 이후

- 간단한 함수의 입력·출력·예외를 설명한다.
- pytest 실패에서 기대값, 실제값, 최초 프로젝트 코드 위치를 찾는다.
- 정상 경로 외에 오류·경계·재시작 테스트를 요구한다.

### Gate C: 상태가 있는 시스템의 안전성을 질문한다 — 10주차 이후

- transaction, idempotency, state transition, reconciliation을 구분한다.
- 중복 요청, 부분 실패, crash, stale write, 만료 시나리오를 질문한다.
- 금액·수량·시간의 타입과 단위를 확인한다.

### Gate D: 트레이딩 결과를 보수적으로 해석한다 — 13주차 이후

- look-ahead, 체결 가정, 비용, 표본 한계를 확인한다.
- mock/paper/live 증거를 서로 바꾸어 말하지 않는다.
- 학습 결과가 실거래 권한을 만들지 않는다고 이해한다.

### Gate E: 기술 프로젝트 오너로 검토한다 — 16주차 이후

- 요구사항을 범위·완료 조건·검증으로 바꾼다.
- 에이전트의 결과를 독립 증거와 대조한다.
- low-risk 변경은 승인하고, 고위험·미검증 변경은 정확한 이유로 보류한다.

Gate E를 통과해도 live trading 승인자는 아니다. live 전환은 별도의 운영·법적·브로커·데이터·사람 승인
절차를 모두 필요로 한다.

## 8. AI 에이전트에게 매번 물을 12가지

앞으로 작업을 맡길 때 다음 질문을 복사해 사용해도 된다.

1. 이번 목표를 사용자가 관찰할 수 있는 한 문장으로 쓰면 무엇인가?
2. 바꾸는 파일과 절대로 바꾸지 않는 파일은 무엇인가?
3. 현재 data mode, broker mode, live 관련 flag는 무엇인가?
4. 금액·수량의 단위와 타입, 반올림 방향은 안전한가?
5. 같은 요청이 두 번 오면 어떤 결과가 나는가?
6. DB 쓰기 중간에 실패하거나 프로세스가 재시작되면 무엇이 남는가?
7. 검사 후 제출 전 정책·가격·시간·kill switch가 바뀌면 다시 막는가?
8. 정상, 오류, 경계, 중복, 재시작 경로를 각각 어떤 테스트가 증명하는가?
9. 외부 broker 상태와 로컬 상태가 어긋나면 어떻게 reconciliation하는가?
10. 기존 사용자 dirty 파일과 비밀을 보존했다는 증거는 무엇인가?
11. 정확히 어떤 명령을 실행했고 exit code와 핵심 출력은 무엇인가?
12. 아직 검증하지 못한 것과 그 때문에 승인하면 안 되는 것은 무엇인가?

좋은 답은 “문제없습니다”가 아니라 파일 경로, 커밋, 테스트 이름, 실행 결과, 알려진 한계를 포함한다.

## 9. 허용·주의·금지 판단표

### 초록: 혼자 실행해도 되는 학습 활동

- 문서·코드·테스트 읽기
- `git status/log/diff/show` 같은 읽기 명령
- fixture/mock 기반 unit test와 smoke
- 별도 연습 폴더의 Python·SQLite 실습
- 기존 결과를 바꾸지 않는 보고서 작성

### 노랑: 에이전트와 범위·영향을 확인한 뒤 실행

- dependency 설치·업그레이드
- schema migration 또는 저장소 DB 쓰기
- 전체 파일 자동 포맷·대규모 rename
- external historical data 다운로드
- API와 frontend schema를 함께 바꾸는 작업
- branch 병합·cherry-pick·충돌 해결

### 빨강: 이 커리큘럼만으로 승인하지 않음

- live 관련 flag 활성화
- 실제 broker 자격증명·계좌 ID 사용
- market order 허용
- 실제 주문 제출 또는 취소
- risk gate, kill switch, 승인, 상태 머신, audit, reconciliation 우회
- 실패한 안전 테스트 삭제·skip·완화
- LLM/RL 출력에 주문 승인·제출 권한 부여
- 근거 없는 실거래 확대, canary, scaled 전환

## 10. 문제를 방치하지 않기 위한 20분 주간 루틴

매주 같은 시간에 다음만 확인한다.

1. `git status --short --branch`로 미완료 변경 확인
2. `git log -10 --oneline`으로 최근 변화 확인
3. 최신 작업보드에서 `blocked`, `P0`, `P1`, `pending`, `known limits` 검색
4. 마지막 전체 테스트와 smoke 실행 시점·결과 확인
5. 문제마다 아래 8줄 기록

```text
문제:
처음 관찰한 날짜:
사용자 영향:
안전 영향:
재현 방법:
현재 증거:
다음 담당자/행동:
완료 조건:
```

“나중에 보기” 대신 다음 행동과 완료 조건을 적는 것이 핵심이다. 원인을 모르면 원인 칸을 추측해 채우지 말고
`미확인`으로 두고, 재현 가능한 관찰부터 남긴다.

## 11. 최소 용어집

| 용어 | 이 프로젝트에서의 뜻 |
|---|---|
| fixture | 인터넷·비밀 없이 같은 결과를 내는 고정 테스트 데이터 |
| mock broker | 실제 broker 대신 결정적으로 동작하는 시험용 구현 |
| paper trading | 실제 돈 없이 broker 모의 환경에서 주문 수명주기를 시험하는 단계 |
| invariant | 어떤 상황에서도 깨지면 안 되는 조건 |
| contract | 입력·출력·상태·오류·권한에 대한 약속 |
| fail-closed | 확신할 수 없거나 오류가 나면 허용하지 않고 차단하는 방식 |
| idempotency | 같은 요청을 반복해도 중복 효과가 생기지 않는 성질 |
| fingerprint | 같은 idempotency key에 같은 요청인지 비교하는 대표 값 |
| provenance | 데이터·결정·이벤트가 어디서 왔는지 추적하는 정보 |
| transaction | 여러 DB 변경을 모두 성공시키거나 모두 되돌리는 단위 |
| state machine | 허용된 상태와 전이만 명시적으로 통과시키는 모델 |
| reconciliation | broker의 실제 상태와 로컬 기록을 다시 비교해 맞추는 과정 |
| audit log | 누가, 언제, 어떤 근거로 무엇을 결정했는지 남기는 기록 |
| pure function | 외부 상태를 바꾸지 않고 같은 입력에 같은 출력을 내는 함수 |
| side effect | DB·파일·네트워크·시간·환경 등 함수 밖 상태를 읽거나 바꾸는 효과 |
| deterministic | 같은 조건에서 같은 결과를 재현할 수 있는 성질 |
| CAS | 기대한 이전 상태일 때만 새 상태로 바꾸는 동시성 보호 방식 |
| TOCTOU | 검사 뒤 실제 사용 전 상태가 바뀌어 검사가 낡는 문제 |
| look-ahead bias | 당시 알 수 없던 미래 정보를 과거 의사결정에 섞는 오류 |
| kill switch | 추가 위험 행동을 즉시 차단하는 운영 안전장치 |
| worktree | 한 저장소의 다른 branch를 별도 폴더에서 격리해 작업하는 Git 기능 |

## 12. 수료 후 다음 순서

이 커리큘럼을 마친 뒤에도 바로 실거래 준비로 가지 않는다. 다음 순서를 권장한다.

1. 문서·테스트만 바꾸는 작은 low-risk 작업 3개를 독립 검토한다.
2. fixture 기반 Python 버그 한 개를 테스트로 재현하고 수정 과정을 관찰한다.
3. API 또는 UI 한 계층의 작은 변경을 계약·테스트·빌드까지 검토한다.
4. DB·상태 머신 변경은 구현자가 아닌 독립 reviewer 역할부터 맡는다.
5. paper 단계는 별도 체크리스트와 사람 승인 아래에서만 관찰한다.
6. live 관련 결정은 이 학습 과정 밖의 별도 미션으로 유지한다.

가장 중요한 학습 성과는 “모든 것을 안다”가 아니다. **무엇을 알고, 무엇을 아직 모르며, 어떤 증거가 있어야
다음 단계로 갈 수 있는지 판단하는 능력**이다. 그 능력이 생기면 프로젝트의 문제는 방치 대상이 아니라
분류·재현·할당·검증할 수 있는 작업이 된다.

## 13. 이 커리큘럼의 근거 문서

- [초보자 학습 공백 증거 분석](quantpilot_beginner_learning_gap_evidence.md)
- [현재 QuantPilot 워크플로우](current_project_workflow.md)
- [프로젝트 상태](STATUS.md)
- [안전 체크리스트](safety_checklist.md)
- [로컬 데이터 백테스트 검증](local_data_backtest_validation_report.md)
- [원자적 리스크 예약 독립 감사](atomic_risk_reservation_v1_claude_audit.md)
- [실행 커널 v2 작업보드](execution_kernel_v2_workboard.md)
- [에이전트 협업 프로토콜](agent_collaboration_protocol.md)
- [에이전트 능력 점수표](agent_capability_scorecard.md)

문서와 코드는 계속 변한다. 학습할 때는 항상 현재 branch와 최신 커밋을 확인하고, 이 문서의 명령이나
경로가 달라졌다면 최신 `README.md`, `AGENTS.md`, 활성 작업보드를 우선한다.
