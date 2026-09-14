---
name: qp-designer
description: "설계자팀(시장구조 분석가·전략 설계자·백테스트 포렌식·리스크 게이트) 실행 런북. 사용자가 \"전략 설계해줘\", \"이 가설 백테스트\", \"설계자팀 돌려\", \"레시피 만들어\", \"시장을 시스템으로 보면\", \"퀀트 전략 검증\"이라고 하면 사용한다. 기존 레시피의 백테스트만 다시 돌릴 때는 local-backtest, 기존 결과의 편향 감사만 할 때는 backtest-forensics 스킬을 쓴다. 산출물은 draft 레시피와 연구 노트이며 승격·거래 입력이 아니다."
---

# 설계자팀 (qp-design-*)

## 무엇을 내는가

- `quantpilot/docs/strategy_specs/<strategy_id>.yaml` — 코드가 강제하는 `StrategyRecipe`(`packages/core/schemas.py`).
  `promotion_status: draft`, `allowed_execution_levels: []`, 지정가 전용은 **코드가 고정**하고 모델은 바꿀 수 없다.
- `~/investment-decisions/research/YYYY-MM-DD-strategy-<strategy_id>.md` — 시장구조, 레시피, **코드가 돌린** 백테스트
  (전체·purge/embargo 워크포워드·PSR/DSR/MinTRL), 포렌식 판정, 리스크 게이트 판정, 출처.
- `.research_agents_out/backtest_<id>_<날짜>.json` — 보고서 원본. `docs/strategy_specs/.trials.jsonl` — 시행 원장(DSR의 시행 횟수).

흐름: 시장구조(코드 계산 → 분석가) → 설계자(규칙 문법 JSON) → **코드**: 조립·계약 검증·문법 검증(실패 시 1회 재시도) →
YAML 저장 → **코드**: 신호 생성·백테스트·통계 → 포렌식 ∥ 리스크 게이트 → 노트. 에이전트는 백테스트를 돌리지 않는다.

## 기존 전체 파이프라인: Claude 잡

```powershell
# 시장구조 증거만
.\.venv\Scripts\python.exe -m quantpilot.jobs.run_strategy_design --hypothesis "코스피 20일 추세 위 눌림목 매수" --dry-run
# 전체(노트까지)
.\.venv\Scripts\python.exe -m quantpilot.jobs.run_strategy_design --hypothesis "코스피 20일 추세 위 눌림목 매수"
```

- 데이터는 `local_data/`(15종목, 2024-07~2026-07). `LOCAL_DATA_DIR`로 바꿀 수 있다. 자격 증명 불필요.
- 셸에 `KIS_PAPER_*`가 있으면 런타임 가드가 거부한다(설계된 동작).
- 같은 `strategy_id`·같은 날 노트는 `--force` 없이는 거부. 종료코드 2 데이터 / 3 에이전트·검증 2회 실패 / 4 발행.
- 규칙 문법은 `quantpilot/services/strategy_design/rule_engine.py`의 `GRAMMAR_HELP`가 정본이다. 산문 규칙은 코드가 거부한다.

기존 Python 잡은 `quantpilot/services/research_agents/runner.py`에서 Claude를 직접 호출한다. 에이전트의 Codex 지원이 이 잡의 실행 제공자를 자동 변경하지 않는다. Codex에서는 아래 aorch 역할별 계약을 사용한다.

## 역할 하나만 돌리고 싶을 때: aorch plan

보고서 JSON이 이미 있으면 포렌식이나 리스크 게이트만 `agentId`로 다시 판정할 수 있다(`agentRole: reviewer`, `write: false`,
`allowedProviders: ["anthropic", "openai"]`, `kind: review`). 설계자만 다시 부르는 것은 권하지 않는다 — 검증·백테스트가 코드 쪽에 있어서 잡이 필요하다.

## 읽는 법

1. 포렌식 `overall_confidence`와 `critical`/`major` 발견을 먼저. `info`의 엔진 한계(KRX ±30%·호가단위·VI 미모델링, 2년 표본)는 모든 레시피에 공통이다.
2. `statistics.dsr`이 음수이거나 `n_trials`가 커질수록 같은 데이터에서의 반복 시행이 결과를 부풀린다. `variance_source`가 `walk_forward_windows`면 근사치.
3. 리스크 게이트 `block`이면 레시피를 고쳐 다시 돌린다. 게이트를 통과해도 승격은 사람이 `strategies/promotion.py`의 확인 문자열로만 한다.
4. 채택할 가설은 `invest-judge`로 결정 레코드를 만든다. 레시피 파일은 결정이 아니다.

## 금지

매수·매도·승격 문장, 보고서에 없는 수치 인용, `promotion_status`·`allowed_execution_levels` 수정, 주문·브로커 코드 접근.

## Codex의 단계별 계약

- 정본 `agentId`를 유지하고 `allowedProviders: ["openai"]`로 Codex를 지정할 수 있다. Codex는 반드시 aorch의 `--ignore-user-config` 격리 dispatch를 사용한다. 네이티브 프리셋은 이 경로로 안내하는 라우팅 가드이며 직접 역할을 실행하지 않는다. 네이티브 자식의 상속 MCP로는 필요한 제한을 보장할 수 없다. 이름을 임의 변환하거나 Claude 전용 `--agent` 호출을 Codex에 사용하지 않는다.
- 역할은 전부 읽기 전용이며 셸·쓰기·위임을 비활성화한다. 호출자가 코드로 계산한 증거, 선행 역할 출력, 참조 규약과 요구 JSON 스키마를 본문 또는 허용된 파일 읽기 도구로 제공한다. 에이전트가 계산·백테스트·검증 명령을 실행했다고 보고하게 하지 않는다.
- 필요한 볼트 근거는 승인된 읽기 전용 조회 또는 호출자의 실제 검색·정독 증거(질의·조회 시점·반환 경로·제목·본문·근거 위치)로 제공한다. 출처가 없으면 누락으로 보고하고 직접 조회했다고 꾸미지 않는다.
- 호출자가 각 단계의 스키마와 기존 코드 검증을 통과시킨 뒤 다음 단계로 넘긴다. 기록·파일 저장·발행은 호출자의 권한과 원장 규약을 따르며 역할에 쓰기 권한을 주지 않는다.
- 시장구조 증거 → 분석가 → 가설·규칙 문법·기존 식별자·분석 결과를 받은 설계자 → 호출자의 조립·문법 검증·백테스트 → 레시피와 실제 보고서를 받은 포렌식 및 리스크 게이트 순서다. 계산·검증 증거가 없으면 해당 단계를 완료로 표시하지 않는다.

<!-- aorch-generated: skill:qp-designer; mode=native; edit .agents/aorch/definitions.json -->
