---
name: qp-strategist
description: "전략가팀(매크로 레짐·지정학·시나리오 작성자·독립 반증자·편집자) 실행 런북. 사용자가 \"매크로 전망\", \"시나리오 짜줘\", \"전략가팀 돌려\", \"레짐 판정\", \"지정학 리스크 정리\", \"무효화 조건 뽑아줘\"라고 하면 사용한다. 일간 시황 브리핑(qp-market)이나 종목 후보(qp-invest)에는 쓰지 않는다. 산출물은 원장 research/ 노트이며 거래 입력이 아니다."
---

# 전략가팀 (qp-strat-*)

## 무엇을 내는가

`~/investment-decisions/research/YYYY-MM-DD-macro-outlook.md` — 코드가 판정한 성장×물가 레짐, 시나리오
2~4개(확률·지지/반대 선례·관측 지표·**질문/시한/판정 출처**·무효화 조건 4종), 작성자를 보지 않은 독립 반증과의
대조, 지정학 노출 경로, 열린 결정 레코드와의 접점. 슬랙 12줄 브리핑은 선택. 예측 정확도가 아니라 **판정 가능한
구조화**가 제품이다 — `invest-resolve`가 시한이 온 질문을 채점할 수 있어야 한다.

## 기존 전체 파이프라인: Claude 잡

```powershell
# 수집만(키·네트워크 확인)
powershell -ExecutionPolicy Bypass -File scripts\run-with-env.ps1 --% .\.venv\Scripts\python.exe -m quantpilot.jobs.run_macro_outlook --dry-run
# 전체(원장 노트까지, 슬랙 제외)
powershell -ExecutionPolicy Bypass -File scripts\run-with-env.ps1 --% .\.venv\Scripts\python.exe -m quantpilot.jobs.run_macro_outlook --no-slack
```

- `ECOS_API_KEY`·`FRED_API_KEY`가 없으면 그 출처는 `skipped`에 이름이 남고 레짐은 `undetermined`로 나온다. 키는
  `~/.quantpilot-research.sources`가 가리키는 env 파일에만 두고 이 대화·저장소·로그에 적지 않는다.
- GPR 지수는 `xlrd`가 없으면 `local_data/gpr.csv`(수동 다운로드) 폴백, 그것도 없으면 생략.
- 셸에 `KIS_PAPER_*`가 있으면 런타임 가드가 잡을 거부한다(설계된 동작). 모의계좌 변수 없는 셸에서 돌린다.
- ECOS 코드 검증: `... run_macro_outlook --list-items 722Y001` 뒤 `quantpilot/services/research_agents/config/macro_series.json`의 `verified`를 갱신.
- 같은 날 재실행은 `--force`. 종료코드 2 수집 / 3 에이전트 / 4 발행.

기존 Python 잡은 `quantpilot/services/research_agents/runner.py`에서 Claude를 직접 호출한다. 에이전트의 Codex 지원이 이 잡의 실행 제공자를 자동 변경하지 않는다. Codex에서는 아래 aorch 역할별 계약을 사용한다.

## 역할 하나만 다시 돌리고 싶을 때: aorch plan

증거 파일(`.research_agents_out/evidence_macro_<날짜>.json`)이 이미 있으면 역할 하나를 `agentId`로 골라 dispatch한다.
`agentRole`은 라우팅 봉투만 정한다(researcher = 분석가/작성자, reviewer = 반증자). 전부 `write: false`이며 Claude와 Codex를 지원한다.

```json
{
  "objective": "2026-09-14 매크로 증거로 시나리오만 다시 작성",
  "decomposed": false,
  "tasks": [
    {
      "id": "scenarios",
      "agentRole": "researcher",
      "agentId": "qp-strat-scenario-writer",
      "kind": "risk-analysis",
      "risk": "standard",
      "complexity": "high",
      "write": false,
      "allowedProviders": ["anthropic", "openai"],
      "acceptanceCriteria": ["시나리오 2~4개, 확률 합 1.0, 모든 시나리오에 질문·시한·판정 출처·무효화 조건 4종"],
      "objective": "…/evidence_macro_2026-09-14.json 과 두 분석가 출력(경로)을 읽고 에이전트 정의의 JSON 스키마대로 시나리오를 작성한다"
    }
  ]
}
```

`aorch decompose --plan plan.json` → `aorch dispatch --plan plan.json`. 반증자는 **시나리오를 입력에 넣지 않는다**(독립성 설계).

## 읽는 법

1. 첫 줄의 레짐과 `## 미검증·결측`을 먼저 본다 — 결측 위에 세운 시나리오는 가중치를 낮춘다.
2. `## 반증 대조`에서 `→ S-이름`이 붙은 항목은 그 시나리오가 무너진 논거에 기대고 있다는 뜻이다.
3. 무효화 조건은 `invest-judge` 결정 레코드의 `## 무효화 조건`에 그대로 옮길 수 있는 형태다. 옮길 때는 새 결정 레코드를 만든다(원장 append-only).
4. 시한이 온 질문은 `invest-resolve`로 채점한다.

## 금지

매수·매도·비중 문장 추가, 증거 JSON에 없는 수치 인용, `.env`·키 열람, 원장 기존 파일 수정.

## Codex의 단계별 계약

- 정본 `agentId`를 유지하고 `allowedProviders: ["openai"]`로 Codex를 지정할 수 있다. Codex는 반드시 aorch의 `--ignore-user-config` 격리 dispatch를 사용한다. 네이티브 프리셋은 이 경로로 안내하는 라우팅 가드이며 직접 역할을 실행하지 않는다. 네이티브 자식의 상속 MCP로는 필요한 제한을 보장할 수 없다. 이름을 임의 변환하거나 Claude 전용 `--agent` 호출을 Codex에 사용하지 않는다.
- 역할은 전부 읽기 전용이며 셸·쓰기·위임을 비활성화한다. 호출자가 코드로 계산한 증거, 선행 역할 출력, 참조 규약과 요구 JSON 스키마를 본문 또는 허용된 파일 읽기 도구로 제공한다. 에이전트가 계산·백테스트·검증 명령을 실행했다고 보고하게 하지 않는다.
- 필요한 볼트 근거는 승인된 읽기 전용 조회 또는 호출자의 실제 검색·정독 증거(질의·조회 시점·반환 경로·제목·본문·근거 위치)로 제공한다. 출처가 없으면 누락으로 보고하고 직접 조회했다고 꾸미지 않는다.
- 호출자가 각 단계의 스키마와 기존 코드 검증을 통과시킨 뒤 다음 단계로 넘긴다. 기록·파일 저장·발행은 호출자의 권한과 원장 규약을 따르며 역할에 쓰기 권한을 주지 않는다.
- 매크로/GPR/뉴스 증거 → 두 분석가 → 시나리오 작성자와 독립 반증자 → 편집자 순서다. 반증자에게는 두 분석가 출력만 주고 시나리오는 절대 전달하지 않는다. 편집자가 마지막에 둘을 대조한다. 수집·발행은 별도 권한이며 Codex 역할 지원만으로 네트워크 수집이나 Slack 발송을 허가하지 않는다.

<!-- aorch-generated: skill:qp-strategist; mode=native; edit .agents/aorch/definitions.json -->
