# 전략가팀 5 · 설계자팀 4: 매크로·지정학 시나리오와 시스템 관점 전략 설계

## 목표

매주 일요일 저녁 전략가팀이 레짐 판정·시나리오 2~4개·무효화 조건을 원장 `research/` 노트와 슬랙
브리핑으로 내고, 사용자가 가설 한 줄을 주면 설계자팀이 코드가 강제하는 `StrategyRecipe` YAML 초안과
코드가 계산한 백테스트·디플레이티드 샤프·포렌식·리스크 판정을 연구 노트로 남기는 상태가 되면 끝이다.
두 팀은 독립이며 사람이 `/invest-judge`에서 종합한다.

## 접근

리서치 팀(`qp-*`, `docs/research_agents.md`)의 형태를 그대로 복제한다: 숫자는 파이썬이 계산해 증거
JSON으로 주고 에이전트는 인용만 하며, 잡이 `claude.exe -p --agent`로 프로젝트 에이전트를 부르고,
산출물은 거래 입력이 아니다(`signal_input: false`). 전략가팀은 `research_agents` 패키지를 확장하고,
설계자팀은 백테스트 엔진을 읽어야 하므로 별도 패키지 `services/strategy_design/`에 tach 경계를 새로 건다.

## 확정된 설계 결정 (2026-09-14 인터뷰 4라운드, 재제안 금지)

| # | 항목 | 결정 | 근거 |
|---|---|---|---|
| 1 | 거처 | QuantPilot `.agents/aorch/definitions.json`. 헤드리스 잡 + 대화형 aorch dispatch(`agentId`) 겸용. aorch 저장소 무변경 | aorch에 팀 구조가 없고 qp-* 패턴이 정본 |
| 2 | 팀 관계 | 독립. 계약 결합 없음. 사람이 `/invest-judge`에서 종합 | 사용자 선택 |
| 3 | 전략가팀 | `qp-strat-macro-regime` · `qp-strat-geopolitics` · `qp-strat-scenario-writer` · `qp-strat-refuter` · `qp-strat-editor` | 매크로·지정학은 데이터 원천이 다름; 시나리오화는 별도 역할 |
| 4 | 반론 구조 | 독립 반증자 1명, 토론 없음. 반증자는 **작성자 출력을 보지 않고** 매크로·지정학 분석만 입력받는다 | Debate or Vote(2508.17536)·편향 합의(2608.02827): 토론은 정확도를 올리지 않음 |
| 5 | 전략가 산출물 | `~/investment-decisions/research/YYYY-MM-DD-macro-outlook.md`(`type: macro-outlook`) + 슬랙 브리핑 12줄. 일요일 20:00 예약 | 원장 규약(시한 있는 질문·무효화 조건 4종) 재사용 |
| 6 | 시나리오 스키마 | 이름·확률·테제·지지 선례·반대 선례·관측 가능한 변화 요인 1개·질문·시한·판정 출처·무효화 조건(id/임계값/주기/출처) | Geopol-Forecast-Council 6필드 → 원장 규약 매핑 |
| 7 | 데이터 1단계 | ECOS(한국은행) + FRED + GPR 지수. 2단계(첫 브리핑 후) GDELT DOC API + Manifold. ACLED 제외(약관이 AI/ML 용도 금지) | 수치 앵커 우선 |
| 8 | 키 | `ECOS_API_KEY`는 보유(사용자가 `~/.quantpilot-research.sources`가 가리키는 env 파일에 직접 기입, 값은 저장소·로그·문서 어디에도 없음), `FRED_API_KEY`는 발급 예정. 키 없는 수집은 건너뛰고 노트에 "판정 불가" | 스마트한 실패 대신 명시적 생략 |
| 9 | GPR 파일 | 원본은 xls뿐(CSV 404 실측). `xlrd`는 선택 의존성(없으면 건너뜀) + 수동 CSV 폴백 `local_data/gpr.csv` | 표준라이브러리 원칙 유지 |
| 10 | 설계자팀 | `qp-design-market-structure` · `qp-design-strategy-designer`(기존 아키텍트 + source-curator 흡수) · `qp-design-backtest-forensics`(흡수) · `qp-design-risk-gate`(흡수) | 사용자 선택 |
| 11 | 출력 계약 | 코드가 강제하는 `StrategyRecipe`(`packages/core/schemas.py`) YAML → `quantpilot/docs/strategy_specs/`. `promotion_status: draft`, `allowed_execution_levels: []` 고정. fable5-level34 YAML 형태 폐기 | 디스크에 그 형태 산출물 0개, 필드 불일치 |
| 12 | 백테스트 | 잡(코드)이 `run_backtest` + purge/embargo 워크포워드 + PSR/DSR/MinTRL을 돌리고 에이전트는 결과 JSON만 읽는다. 통계는 stdlib 직접 구현(purgedcv는 참조) | 시행 횟수 추적·도구 제한 유지 |
| 13 | 레거시 | `quant-recipe-architect`·`backtest-forensics-agent`·`risk-gatekeeper-agent`·`source-curator-agent` 매니페스트 제거. `rl-research-contract-agent`·`codex-handoff-writer`·`/write-codex-handoff`·`/fable5-level34` 유지, 존재하지 않는 경로 참조만 정정 | 변경 범위 최소 |
| 14 | 모델 | 분석가·작성자·편집자·설계자 = `QUANTPILOT_RESEARCH_MODEL`(opus), 반증자·포렌식·리스크게이트 = `QUANTPILOT_RESEARCH_JUDGE_MODEL`(fable) | 리서치 팀 관례 |
| 15 | KRX 규칙 | ±30%·호가단위·VI는 범위 밖. 포렌식이 매번 `info`로 "엔진 미모델링" 표기, 설계자 잡은 `allow_fractional_shares=False` | 엔진 실측 |
| 16 | 측정 | 전략가: 4주 중 3회 이상 브리핑 읽힘 + 무효화 조건 1개 이상이 `invest-resolve`로 기록. 설계자: draft 레시피 1개 이상이 포렌식 medium 이상, 시행 원장 채워짐 | 한 달 |

## 검증 명령

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m pytest quantpilot/tests --basetemp ".pytest_tmp/$PID"
& $py -m quantpilot.jobs.run_smoke
& "$env:USERPROFILE\.local\share\aorch-tools\.venv\Scripts\tach.exe" check
& $py -m quantpilot.jobs.run_macro_outlook --dry-run          # [네트워크] 수집만
& $py -m quantpilot.jobs.run_strategy_design --hypothesis "..." --no-post   # [LLM]
```

## 슬라이스

| # | 내용 | 산출물 |
|---|---|---|
| S0 | 이 문서 | — |
| S1 | `core/backtest/statistics.py`(PSR·DSR·MinTRL), `validation.py` purge/embargo, `run_local_backtest` 통계 | 단위 테스트 |
| S2 | `collectors/{ecos,fred,gpr}.py`, `analytics/macro_regime.py`, `config/macro_series.json`, `MacroEvidence` | 픽스처 테스트 |
| S3 | 전략가 에이전트 5 + `prompts/macro.py` + `pipeline_macro.py` + `jobs/run_macro_outlook.py` + 스케줄 스크립트 | FakeRunner 테스트 |
| S4 | `services/strategy_design/analytics/market_structure.py` + tach 스탠자 | 단위 테스트 |
| S5 | 설계자 에이전트 4 + `strategy_design/pipeline.py` + `jobs/run_strategy_design.py` + 레거시 제거 | FakeRunner + 실제 백테스트 테스트 |
| S6 | 대화형 스킬 `qp-strategist`·`qp-designer`, 문서, `aorch install --project`, 도그푸딩 | 노트 2개 |
| S7 | (첫 브리핑 후) GDELT·Manifold 수집기, 시나리오 확률 대조 | 별도 세션 |

## 조사 근거 요약

차용한 설계: TradingAgents(Apache) 판정자 루브릭·point-in-time 날짜 창, ai-hedge-fund(MIT) "LLM은 conviction만, 주문은 코드",
RD-Agent(Q)(MIT) 가설→백테스트 루프, Geopol-Forecast-Council 시나리오 스키마, qp-invest-refuter 3렌즈.
함정 문헌: Profit Mirage(2510.07920) 학습 컷오프 뒤 성과 소멸, "정직한 평가는 LLM 발견 전략을 전부 기각"(2608.27734),
평가 위생 무시 시 수익 부호 반전(2603.27539). 참조 구현: purgedcv(MIT, CPCV+DSR+PBO).
데이터 약관: GDELT 무제한(인용), ACLED AI/ML 금지, Manifold 비상업 허용, GPR CC BY 4.0(남북 지정학 리스크 지수 포함).
