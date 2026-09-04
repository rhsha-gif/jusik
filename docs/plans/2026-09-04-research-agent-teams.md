# QuantPilot 리서치 에이전트 팀: 시황 3 · 투자 4 · 보안 게이트 1

## 목표

장 마감 후 매 거래일 슬랙과 투자 원장에 시황 브리핑이 도착하고, 사용자가 고른 테마·종목에 대해
투자 팀이 후보 노트(7절 초안 + 반증 + 방향 메모)를 원장에 제안하며, `/ship`이 주문 경로·비밀·
리서치 패키지를 건드리는 변경을 보안 게이트 판정 없이 커밋하지 않는 상태가 되면 끝이다.

## 접근

리서치 코드는 `quantpilot/services/research_agents/` 한 패키지에 두고, 기존
`services/briefing`이 이미 쓰는 "거래 코드 import 금지" 경계를 tach 계약과 pytest 정적 검사로
기계화한다. 숫자는 전부 파이썬이 계산해 JSON 증거 파일로 만들고, 에이전트는 그 JSON을 서술·
인용만 한다(FinRobot 방식). 반복 실행은 aorch 런타임을 거치지 않고 QuantPilot 잡이
`claude.exe -p --agent <이름>`으로 프로젝트 에이전트를 직접 부른다. 후보는 기계가 제안하고
사람이 `/invest-judge`로 결정한다 — 후보 노트는 QuantPilot의 승인 풀·주문 코드와 연결되지 않는다.

## 확정된 설계 결정 (2026-09-04 인터뷰 + 포니테일 점검, 재제안 금지)

| # | 항목 | 결정 | 근거 |
|---|---|---|---|
| 1 | 팀 구성 | 시황 3(가격·수급 분석가, 거시·뉴스 분석가, 편집자) + 투자 4(테마 스카우트, 종목 리서처, 반증자, 포트폴리오 방향) + 보안 게이트 1 | 포니테일은 3역할 최소안을 권했으나 사용자가 원안 유지 선택 |
| 2 | 형태 | `.claude/agents/qp-*.md` 프로젝트 에이전트 8개 + 파이썬 잡 2개. aorch 플랜 템플릿은 만들지 않는다 | 매일 도는 경로에 aorch 런타임이 개입할 이유 없음 |
| 3 | 데이터 1차 | 네이버 파이낸스 공개 JSON/XML(표준 라이브러리, 키 없음) + 보유 네이버 검색 API 키. pykrx는 09-04 의존성 심사에서 기각(아래 "심사 결과"). DART·ECOS·KOSIS·FRED는 브리핑이 한 주 읽힌 뒤 별도 계획 | 첫 브리핑에 필요한 건 시세·수급·헤드라인뿐이고 pykrx는 KRX 로그인 없이는 그중 셋을 못 준다 |
| 4 | 숫자·서술 분리 | 지수·수급·거래량 비율·기저율은 코드가 계산해 `evidence_<날짜>.json`에 기록. 에이전트는 값을 그대로 옮겨 적고 새 수치를 계산하지 않는다 | FinRobot "numbers-in-code, LLM-narrates" |
| 5 | 인용 | `vault-consult` 규율 그대로: 파운데이션 볼트는 `[[노트명]]`, 뉴스는 증거 JSON의 `id`와 URL만 인용, URL 창작 금지. 핸들 검증기는 환각 사례가 쌓이면 | 아직 관측되지 않은 실패 |
| 6 | 파운데이션 볼트 | 읽기 전용(`vault` MCP). 노트 생성·수정 금지 | vault-consult "금지" 절 |
| 7 | 노트 위치 | `~/investment-decisions/market/YYYY-MM-DD.md`, `~/investment-decisions/candidates/YYYY-MM-DD-<종목코드>.md`. 비공개 원장이라 보유·금액 정보 가능 | 사용자 선택 |
| 8 | 배송 | 슬랙 incoming webhook(`QUANTPILOT_SLACK_WEBHOOK_URL`) 단방향. 게시 전 비밀 스크러빙 통과 필수 | 읽기 권한 불필요 |
| 9 | 엔진 | `claude.exe -p --agent <이름> --output-format json`. 프롬프트는 stdin으로(증거 JSON이 커서 명령줄 32K 제한 회피) | 헤드리스 결정 |
| 10 | 모델 | 분석가·편집자·스카우트·리서처·방향 = `opus`, 반증자·보안 게이트 = `fable`. 환경변수 `QUANTPILOT_RESEARCH_MODEL`, `QUANTPILOT_RESEARCH_JUDGE_MODEL`로 덮어씀 | 판정 역할만 최상위 모델 |
| 11 | 격리 | `research_agents`는 `core.execution`, `core.operator`, `core.signals`, `core.portfolio`, `core.risk`, `packages.brokers`, `harness_service`를 import 못 한다. tach 계약 + pytest 정적 검사 + 수용 매트릭스 불변식 행 | 유일한 non-negotiable 불변식의 기계적 보강 |
| 12 | 투자 파이프라인 | 스카우트 → 리서처(후보당) → 반증자 → 방향 메모 → 후보 노트(`status: proposed`) → 사람이 `/invest-judge` | 기계 제안, 사람 승인 |
| 13 | 보안 모드 | 마감 게이트만: gitleaks + semgrep + tach 출력과 diff를 `qp-security-gate`가 읽고 pass/block 판정. 프리플라이트·운영 감시는 실서버 연결 시점의 별도 계획 | 관측 대상 없음 |
| 14 | 게이트 행동 | block이면 `/ship`은 커밋을 중단하고 발견 목록을 보고한 뒤 AskUserQuestion으로 사용자 판단 | 사용자 선택 |
| 15 | 키·비밀 | `.env.example`에 이름만(`NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `QUANTPILOT_SLACK_WEBHOOK_URL`). 잡은 `.env`를 읽지 않고 환경변수만 읽는다. 에이전트는 `.env`를 열지 않는다 | 보안 규약 |
| 16 | 인터프리터 | 저장소 루트 `.venv`(uv, Python 3.11)에 `.[test,research]` 설치. hermes venv에 pykrx를 넣지 않는다 | CLAUDE.md "프로젝트 venv가 의도된 집" |
| 17 | 이식 함정 | `claude.exe`가 exit 0인데 빈 결과를 내는 경우를 실패로 취급(weekly.ps1:161). 서브프로세스 인코딩은 `encoding="utf-8"` + `PYTHONIOENCODING=utf-8` 명시 | SecondBrain 실측 |
| 18 | 예약 | Windows 작업 스케줄러, 평일 16:10, `scripts/run-market-brief.cmd` 래퍼(저장소로 cd 후 `.venv` 파이썬 실행) | 장 마감 15:30 + pykrx 반영 지연 |
| 19 | 측정 | 1주차: 5거래일 중 4일 이상 브리핑을 읽고 1건 이상 행동했는가. 첫 달: 후보 노트에서 결정 레코드 1건 이상 | 포니테일 단일 질문 |
| 20 | 브랜치 | `claude/research-agents-2026-09-04`, 워크트리 `.claude/worktrees/research-agents`. main의 미커밋 사용자 변경(`.claude/settings.json`, `.mcp.json`, `.aorch/`, `aorch-*.md`)은 손대지 않는다 | AGENTS.md 관례 |

## 검증 명령

프로젝트 표준(PowerShell, 저장소 루트). `$py`는 작업 1에서 만드는 프로젝트 venv.

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m pytest quantpilot/tests --basetemp ".pytest_tmp/$PID"
& $py -m quantpilot.jobs.run_smoke
& "$env:USERPROFILE\.local\share\aorch-tools\.venv\Scripts\tach.exe" check
```

네트워크나 실제 LLM 호출이 필요한 단계는 `[네트워크]`로 표시한다. 단위 테스트는 네트워크·비밀
없이 돌아야 한다(AGENTS.md).

---

### 작업 0: 브랜치·워크트리·계획 문서

대상 파일: `docs/plans/2026-09-04-research-agent-teams.md`

- [x] `git worktree add .claude/worktrees/research-agents -b claude/research-agents-2026-09-04 main` 실행
- [x] 이 문서를 워크트리의 같은 경로로 복사하고 워크트리에서 커밋 `docs: plan the research agent teams`
- [x] `~/.claude.json`의 프로젝트 항목에 워크트리 경로 키를 만들고 `hasTrustDialogAccepted: true` 설정(git dir이 `~/.local/git-meta/jusik.git`이라 헤드리스 claude가 신뢰 대화로 멈추는 실측 함정)
- [x] 검증: `git -C .claude/worktrees/research-agents log --oneline -1`에 계획 커밋, `git status`에 main 변경 없음

### 작업 1: 프로젝트 venv와 pykrx 의존성 심사

대상 파일: `pyproject.toml`, `.gitignore`

- [x] `/dependency-audit pykrx` 실행. 결과를 이 문서 "심사 결과" 절에 기록
- [x] 심사 결과 pykrx 기각 → `pyproject.toml`에 `research` 그룹을 추가하지 않는다(새 의존성 0개). 시세 클라이언트는 `collectors/naver_market.py`(표준 라이브러리)
- [x] `.gitignore`에 `.venv/`, `.security-gate/`, `.research_agents_out/`가 없으면 추가
- [x] 워크트리 루트에서 `uv venv .venv --python 3.11` 후 `UV_LINK_MODE=copy uv pip install --python .venv\Scripts\python.exe fastapi==0.133.1 starlette==1.0.1 pydantic pyyaml uvicorn httpx pytest tzdata`(fastapi·starlette는 hermes venv와 같은 판본으로 고정, 발견 5) (editable install은 build-system 부재로 실패; `tzdata`는 uv 파이썬에 tz 데이터베이스가 없어 `Asia/Seoul` 조회가 깨지는 실측 함정)
- [x] 검증: `& $py -c "import fastapi, pydantic, pytest, zoneinfo; zoneinfo.ZoneInfo('Asia/Seoul'); print('ok')"` → `ok`; `& $py -m pytest quantpilot/tests --basetemp ".pytest_tmp/$PID"` 기존 전부 통과

### 작업 2: 격리 패키지 골격과 경계 계약

대상 파일: `quantpilot/services/research_agents/__init__.py`, `quantpilot/services/research_agents/models.py`, `tach.toml`, `quantpilot/tests/unit/test_research_agents_boundary.py`, `docs/roadmap_acceptance_matrix.md`

- [x] `__init__.py` 도크스트링에 경계 선언(briefing/`__init__.py` 어투): 사람이 읽는 리서치 산출물 전용, 거래 입력 아님, 주문·승인·제출 불가
- [x] `models.py`에 pydantic 모델: `MarketSnapshot`(date, kospi/kosdaq close·change_pct, sector_moves[], investor_flows{foreign, institution, individual}, watchlist_rows[symbol,name,close,change_pct,volume_ratio_20d]), `NewsItem`(id, title, link, source_domain, published_at, query), `EvidenceBundle`(date, collected_at, snapshot, news[], sources[], `signal_input: bool = False` 상수), `AgentResult`(agent, model, text, json, elapsed_s, exit_code), `SecurityVerdict`(verdict: pass|block, findings[{id, severity, path, line, rule, evidence, proposal}])
- [x] `tach.toml` 생성: 모듈 `quantpilot.services.research_agents`와 `quantpilot.services.briefing`의 `depends_on`에 `quantpilot.packages.core.schemas`만 허용. `quantpilot.packages.core.execution`, `quantpilot.packages.core.operator`, `quantpilot.packages.core.signals`, `quantpilot.packages.core.portfolio`, `quantpilot.packages.core.risk`, `quantpilot.packages.brokers`, `quantpilot.packages.core.harness_service`, `quantpilot.services.api`를 `cannot_depend_on`에 명시
- [x] `test_research_agents_boundary.py`: `test_briefing.py:30`의 정적 검사 방식으로 `research_agents/**/*.py` 전부에서 금지 문자열(위 8개 + `paper_submission`) 부재 확인, `EvidenceBundle.signal_input`이 False로 고정됨 확인
- [x] `docs/roadmap_acceptance_matrix.md` §1 표에 행 추가: `Research isolation | research_agents/briefing never import execution, operator, signals, portfolio, risk, brokers, harness, api | tach.toml; quantpilot/tests/unit/test_research_agents_boundary.py`
- [x] 검증: `tach check` → 위반 0; pytest 새 테스트 통과; 일부러 `from quantpilot.packages.core.execution import paper_submission`을 임시로 넣으면 tach와 pytest 둘 다 실패하는지 확인 후 제거

### 작업 3: 수집기(네이버 파이낸스 시세·네이버 뉴스)와 증거 파일

대상 파일: `quantpilot/services/research_agents/collectors/__init__.py`, `collectors/krx.py`, `collectors/naver_news.py`, `collectors/evidence.py`, `quantpilot/services/research_agents/config/watchlist.json`, `quantpilot/tests/unit/test_research_collectors.py`, `quantpilot/tests/fixtures/research_agents/{krx_sample.json,naver_news_sample.json}`

- [x] `watchlist.json`: `local_data`의 15개 KRX 종목(코드·이름·테마)으로 시작. 형식 `{"symbols":[{"code":"005930","name":"삼성전자","theme":"반도체"}]}`
- [x] `krx.py`: `collect_market_snapshot(date, client)` — 지수(KOSPI·KOSDAQ 종가·등락률), 업종 등락 상위·하위 3, 투자자별 순매수(외국인·기관·개인, 억원), 관심종목별 종가·등락률·20일 평균 대비 거래량 비율. 실패 시 예외(부분 스냅샷을 만들지 않는다: fail-closed). 클라이언트는 `MarketDataClient` 프로토콜; 실제 구현은 `naver_market.py`의 `NaverMarketClient`(m.stock.naver.com JSON + fchart XML, 표준 라이브러리, 요청 간 0.3초·재시도 3회). 업종 등락과 지수 투자자 수급은 당일 스냅샷만 제공되므로 `--date`가 오늘이 아니면 업종은 비고 수급은 수집 실패로 취급
- [x] `naver_news.py`: `collect_news(date, queries, client=NaverNewsClient())` — `https://openapi.naver.com/v1/search/news.json`을 표준 라이브러리 `urllib.request`로 호출(헤더 `X-Naver-Client-Id/Secret`은 환경변수에서만), 질의는 관심종목 이름 + `코스피`, `코스닥`, `금리`, `환율`. `link` 기준 중복 제거, 각 항목에 `id = news:<sha1(link)[:10]>`, HTML 태그 제거. 키가 없으면 빈 목록이 아니라 명시적 예외
- [x] `evidence.py`: `build_evidence(date, snapshot, news) -> EvidenceBundle`, `write_evidence(bundle, out_dir) -> Path`(`evidence_<날짜>.json`, UTF-8, `ensure_ascii=False`), `sources[]`에 `{"id":"pykrx","fetched_at":...}`, `{"id":"naver_news",...}`
- [x] 테스트: fake 클라이언트로 스냅샷·뉴스 생성이 결정적인지, 중복 링크 제거, 키 없음 예외, 부분 실패 시 예외(파일 미생성), 증거 파일 라운드트립. 픽스처는 실제 응답 형식을 본뜬 소량 JSON
- [x] 검증: pytest 새 테스트 통과; `[네트워크]` 09-04 실측: `NaverMarketClient`로 `collect_market_snapshot('2026-09-04')` → 코스피 6694.87 (+1.75%), 외국인 +3,317억·기관 +10,429억·개인 -24,538억, 관심종목 3개 행 생성(장중 호출이라 거래량 비율 <1)

### 작업 4: 헤드리스 러너와 슬랙·노트 게시기

대상 파일: `quantpilot/services/research_agents/runner.py`, `quantpilot/services/research_agents/publish/__init__.py`, `publish/scrub.py`, `publish/slack.py`, `publish/notes.py`, `quantpilot/tests/unit/test_research_runner.py`, `quantpilot/tests/unit/test_research_publish.py`

- [x] `runner.py`: `run_agent(agent, prompt, *, cwd, model, timeout_s=900, json_schema=None, claude_path=None) -> AgentResult`. 명령: `claude.exe -p --agent <agent> --model <model> --output-format json [--json-schema <schema>]`, 프롬프트는 `input=`(stdin), `encoding="utf-8"`, `env`에 `PYTHONIOENCODING=utf-8` 추가, `claude_path` 기본값은 `%USERPROFILE%\.local\bin\claude.exe`. 결과 JSON의 `result`(또는 `structured_output`)를 꺼내고, exit 0이어도 비어 있으면 `AgentEmptyOutput` 예외. 타임아웃·비정상 종료는 stderr 앞 500자와 함께 예외
- [x] `scrub.py`: `scrub(text) -> ScrubResult(text, replaced: int)`. 규칙: (1) 환경변수 이름이 `KEY|SECRET|TOKEN|WEBHOOK|PASSWORD`를 포함하는 변수의 **값**이 본문에 있으면 `[REDACTED]`, (2) `Bearer <토큰>`, `sk-[A-Za-z0-9]{20,}`, 32자 이상 hex/base64 연속, `hooks.slack.com/services/` URL 패턴, (3) SecondBrain의 봉인 마크(`[봉인]`)가 있는 줄은 통째 대체. 대체가 1건이라도 있으면 호출자가 로그를 남긴다
- [x] `slack.py`: `post_webhook(text, url=None)` — `url` 기본은 환경변수 `QUANTPILOT_SLACK_WEBHOOK_URL`, 없으면 예외. 게시 전 `scrub` 필수(스크럽을 거치지 않은 텍스트를 보낼 수 있는 공개 함수는 두지 않는다). `urllib.request`로 `{"text": ...}` POST, 응답 `ok`가 아니면 예외. URL은 로그·예외 메시지에 절대 넣지 않는다
- [x] `notes.py`: `write_market_note(date, markdown, evidence_path, root=None)` → `<root>/market/YYYY-MM-DD.md`, `write_candidate_note(date, symbol, markdown, root=None)` → `<root>/candidates/YYYY-MM-DD-<symbol>.md`. `root` 기본은 환경변수 `QUANTPILOT_LEDGER_ROOT`, 없으면 `~/investment-decisions`. frontmatter: `type`, `date`, `generated_at`, `generated_by`(에이전트 이름·모델), `evidence`(증거 파일 절대경로), 후보 노트는 `status: proposed`, `candidate_id`, `symbol`. 같은 날 파일이 이미 있으면 `force=True`가 아닌 한 예외(주간 파이프라인의 "조용한 재게시" 함정 회피)
- [x] 테스트: `subprocess.run`을 monkeypatch한 fake로 정상·빈 결과·비정상 종료·타임아웃 4경로, 스크럽 규칙별 1건씩 + 환경변수 값 유출 케이스, 슬랙은 fake `urlopen`으로 성공·실패·스크럽 미통과 경로 없음 확인, 노트는 `tmp_path`로 생성·중복 예외·frontmatter 필드 확인
- [ ] 검증: pytest 새 테스트 통과; `[네트워크]` `& $py -c "from quantpilot.services.research_agents.runner import run_agent; print(run_agent('aorch-scout','현재 디렉터리의 CLAUDE.md 첫 줄만 답하라', cwd='.', model='sonnet').text[:80])"` 가 텍스트 출력

### 작업 5: 시황 팀 에이전트 3종과 파이프라인

대상 파일: `.claude/agents/qp-market-price-flow-analyst.md`, `.claude/agents/qp-market-macro-news-analyst.md`, `.claude/agents/qp-market-editor.md`, `quantpilot/services/research_agents/pipeline_market.py`, `quantpilot/services/research_agents/prompts/market.py`, `quantpilot/tests/unit/test_research_pipeline_market.py`

- [x] 에이전트 파일 frontmatter는 `aorch-invest-analyst.md` 형식(`name`, `description`, `disallowedTools: Write, Edit, NotebookEdit, Agent, Bash`, `maxTurns: 30`, `tools:` 목록 없음 — 구조화 출력이 사라지는 실측 함정). 본문 공통 규율: 증거 JSON의 수치만 인용하고 새 수치를 계산하지 않는다, 뉴스는 `id`와 제공된 URL만 인용, 파운데이션 볼트는 `vault_search`로 조회해 `[[노트명]]` 인용, 볼트 밖 지식은 그렇다고 밝힌다, 매수·매도 지시 문장 금지, 한국어
- [x] 가격·수급 분석가: 입력 = 스냅샷. 출력 절: 지수와 폭, 업종 회전, 투자자 수급 해석, 관심종목 이상치(거래량 비율 ≥ 2 또는 |등락률| ≥ 3%), 확인이 필요한 것
- [x] 거시·뉴스 분석가: 입력 = 뉴스 목록 + 스냅샷 요약 3줄. 출력 절: 오늘의 헤드라인 묶음(출처 등급 A/B/C를 `invest-judge` 규율대로 표기), 거시 변수(금리·환율) 언급, 관심종목 관련 공시·뉴스, 단독 C등급은 `미확인` 표기
- [x] 편집자: 입력 = 두 분석가 출력. `--json-schema`로 `{"slack_text": str, "note_markdown": str}` 고정. 슬랙은 12줄 이내(지수 한 줄, 수급 한 줄, 이상치 3줄 이내, 헤드라인 3줄 이내, "확인할 것" 2줄), 노트는 두 분석가 절을 합치고 맨 끝에 `## 출처` 절(증거 파일 경로, 볼트 인용 목록, 뉴스 id·URL)
- [x] `prompts/market.py`: 세 프롬프트를 함수로(`price_flow_prompt(bundle)`, `macro_news_prompt(bundle)`, `editor_prompt(a, b)`), 증거 JSON은 `ensure_ascii=False`로 삽입
- [x] `pipeline_market.py`: `run_market_pipeline(bundle, *, runner=run_agent, model, cwd) -> MarketBriefOutput(slack_text, note_markdown, agent_results[])`. 분석가 둘은 `concurrent.futures.ThreadPoolExecutor(2)`로 병렬, 편집자는 순차. 어느 단계든 빈 출력이면 예외
- [x] 테스트: fake runner로 세 단계 호출 순서·입력 전달·JSON 스키마 파싱·빈 출력 예외 확인. 실제 LLM 호출 없음
- [x] 검증: pytest 통과; `[네트워크]` 작업 6의 잡 dry-run 이후 실제 1회 실행에서 세 에이전트 결과가 모두 비어 있지 않음

### 작업 6: 일일 잡, 예약, 환경 예시

대상 파일: `quantpilot/jobs/run_market_brief.py`, `scripts/run-market-brief.cmd`, `scripts/register-market-brief-task.ps1`, `.env.example`, `quantpilot/tests/unit/test_run_market_brief_job.py`

- [x] `run_market_brief.py`: `main(argv) -> int`. 인자 `--date`(기본 오늘, 주말·`KRX_HOLIDAYS`면 exit 0 "휴장"), `--out-dir`(기본 `.research_agents_out/`), `--dry-run`(수집 + 증거 파일까지, LLM·게시 없음), `--no-post`(LLM까지, 슬랙·노트 없음), `--force`(같은 날 노트 덮어쓰기). 시작 시 `validate_generic_runtime_environment()` 호출(run_smoke와 같은 관례). 종료 코드: 0 성공, 2 수집 실패, 3 에이전트 빈 출력, 4 게시 실패. 진행 로그는 `out-dir/run_<날짜>.log`에 UTF-8로, 비밀·URL 미포함
- [x] 순서: 수집 → 증거 파일 → 파이프라인 → 노트 기록 → 슬랙 게시(스크럽 후, 대체 건수 로그). 노트를 먼저 쓰는 이유: 슬랙이 실패해도 원장에는 남는다
- [x] `scripts/run-market-brief.cmd`: `cd /d "%~dp0.."` 후 `.venv\Scripts\python.exe -m quantpilot.jobs.run_market_brief %*`
- [x] `scripts/register-market-brief-task.ps1`: `schtasks /Create /TN "QuantPilot Market Brief" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:10 /TR "<저장소 절대경로>\scripts\run-market-brief.cmd" /F`. 등록은 사용자가 실행(외부 시스템 상태 변경이라 계획 실행자가 임의로 등록하지 않는다)
- [x] `.env.example`에 절 추가: `# Research agents (read-only, never a trading input)` 아래 `# NAVER_CLIENT_ID=`, `# NAVER_CLIENT_SECRET=`, `# QUANTPILOT_SLACK_WEBHOOK_URL=`, `# QUANTPILOT_LEDGER_ROOT=C:\Users\<you>\investment-decisions`, `# QUANTPILOT_RESEARCH_MODEL=opus`, `# QUANTPILOT_RESEARCH_JUDGE_MODEL=fable`
- [x] 테스트: fake 수집기·fake 러너·`tmp_path` 원장으로 `--dry-run`이 증거 파일만 만들고 exit 0, `--no-post`가 노트를 안 만듦, 정상 경로가 노트 생성 + 슬랙 fake 호출 1회, 수집 실패 exit 2, 빈 출력 exit 3, 휴장일 exit 0
- [x] 검증: pytest 통과; `& $py -m quantpilot.jobs.run_smoke` 여전히 통과; `[네트워크]` `& $py -m quantpilot.jobs.run_market_brief --date 2026-09-03 --dry-run` → `.research_agents_out/evidence_2026-09-03.json` 생성; `[네트워크]` `--no-post` 실행 후 로그에 세 에이전트 소요 시간 기록

### 작업 7: 보안 게이트 — 도구, 스크립트, 에이전트, /ship 연결

대상 파일: `scripts/security-gate.ps1`, `.claude/agents/qp-security-gate.md`, `docs/security_gate.md`, `~/.claude/skills/ship/SKILL.md`, `quantpilot/tests/unit/test_security_gate_script.py`

- [x] `scoop install gitleaks` (8.30.1). semgrep 1.176.0·tach 0.35.0은 `~/.local/share/aorch-tools/.venv`에 설치됨(2026-09-04 확인). 세 경로를 스크립트 상수로
- [x] `scripts/security-gate.ps1 [-Staged] [-OutDir .security-gate\<timestamp>]`: (1) `git diff --cached --name-only`(또는 `--name-only HEAD`)로 변경 파일 목록, (2) `gitleaks git --staged --report-format json --report-path <out>\gitleaks.json`(비스테이지 모드는 `gitleaks dir`), (3) `semgrep scan --config p/python --config p/secrets --config p/security-audit --json --output <out>\semgrep.json <변경된 .py/.ts 파일>`, (4) `tach check > <out>\tach.txt`, (5) `git diff --cached > <out>\diff.patch`, (6) `summary.json`에 각 도구 종료 코드·발견 수·변경 파일. 도구가 하나라도 실행 불가면 exit 1(fail-closed)
- [x] `qp-security-gate.md`: frontmatter에 `ship_triggers:` 목록 — `quantpilot/packages/core/execution/**`, `quantpilot/packages/brokers/**`, `quantpilot/packages/core/risk/**`, `quantpilot/packages/core/operator/**`, `quantpilot/services/api/**`, `quantpilot/services/research_agents/**`, `quantpilot/jobs/**`, `.env*`, `.mcp.json`, `.claude/**`, `tach.toml`, `pyproject.toml`. 본문: 입력은 `.security-gate/<ts>/`의 파일들과 `docs/roadmap_acceptance_matrix.md` §1, 판정 기준은 (a) gitleaks 발견 1건 이상 → block, (b) tach 위반 → block, (c) diff가 §1 불변식 앵커(`paper_submission.py` POST 권한, `gatekeeper.py` 플래그, `transitions.py`)를 바꾸거나 새 브로커 POST 경로를 추가 → block, (d) semgrep ERROR 등급 → block, WARNING → finding으로만, (e) `research_agents`에 주문·승인·제출 어휘의 함수가 생기면 block. 출력은 `--json-schema`로 `SecurityVerdict`. 재계산 금지, 열어 본 파일 목록을 findings evidence에 명시, 신뢰 경계 통제 제거를 제안하지 않는다
- [x] `docs/security_gate.md`: 언제 도는지(트리거 경로), 수동 실행법, block 시 절차, 프리플라이트·운영 감시가 "실서버 연결 시점 별도 계획"이라는 이월 문장
- [x] `~/.claude/skills/ship/SKILL.md` "### 3. 시크릿 검사" 뒤에 "### 3.5 프로젝트 보안 게이트" 추가: 저장소 `.claude/agents/`에 frontmatter `ship_triggers:`를 가진 에이전트가 있고 스테이징 diff가 그 글롭에 걸리면, 프로젝트의 `scripts/security-gate.ps1 -Staged`(있을 때)를 먼저 돌린 뒤 Agent 도구로 그 에이전트를 호출한다. `verdict: block`이면 커밋하지 않고 findings를 표로 보고한 뒤 AskUserQuestion("수정 후 재검사 / 사용자 책임으로 커밋 / 중단")을 낸다. `pass`면 4단계로
- [x] `test_security_gate_script.py`: 스크립트 파일이 세 도구를 모두 호출하는 문자열을 담고 있고 fail-closed(`exit 1`) 분기가 있는지 정적 검사. 에이전트 파일의 `ship_triggers`가 위 12개 글롭을 포함하는지 확인
- [x] 검증: 워크트리 모드 실행 완료(09-04: gitleaks 2건 — 기존 테스트 픽스처의 가짜 키, semgrep WARNING 3건 → sha256·nosemgrep 근거 주석으로 처리, tach 0, 파일 37). `-Staged` 실행 완료(gitleaks 0·semgrep 0·tach 0, 파일 57); 가짜 키 스테이징 실험은 워크트리 모드에서 픽스처 2건이 잡힌 것으로 갈음; `qp-security-gate` 1회 호출로 `SecurityVerdict` JSON 수신(발견 7)

### 작업 8: 투자 팀 에이전트 4종, 기저율 계산, 파이프라인·잡

대상 파일: `.claude/agents/qp-invest-theme-scout.md`, `.claude/agents/qp-invest-stock-researcher.md`, `.claude/agents/qp-invest-refuter.md`, `.claude/agents/qp-invest-portfolio-direction.md`, `quantpilot/services/research_agents/analytics/base_rate.py`, `quantpilot/services/research_agents/pipeline_invest.py`, `quantpilot/services/research_agents/prompts/invest.py`, `quantpilot/jobs/run_invest_research.py`, `quantpilot/tests/unit/test_research_base_rate.py`, `quantpilot/tests/unit/test_research_pipeline_invest.py`, `quantpilot/tests/unit/test_run_invest_research_job.py`

- [x] `base_rate.py`: `conditional_forward_returns(bars, condition, horizons=(60,120,250))` — 조건(예: 종가가 200일 SMA 위, 고점 대비 -N% 이하)에 맞는 시작일의 전방 수익률 분포(중앙값·손실 확률·최대 낙폭), 조건 판정은 시작일 이전 데이터만 사용(미래 누출 금지), 겹치는 윈도우 수와 비겹침 표본 수를 함께 반환(`invest-judge` Step 3 "표본 한계"). 입력 bars는 pykrx 5년치 또는 `local_data` CSV
- [x] 테마 스카우트: 입력 = 사용자 테마 문장 + 최근 5거래일 시황 노트 본문 + 관심종목. 출력 `--json-schema` `{"candidates":[{"symbol","name","why","vault_citations":[],"news_ids":[]}]}` 최대 5개. 볼트 조회 필수(팩터·산업 구조 노트 인용). 코드에 없는 종목코드는 pykrx 종목 목록으로 검증(코드 쪽에서)
- [x] 종목 리서처: 입력 = 후보 1개 + 스냅샷 행 + 뉴스 + 기저율 JSON. 출력 = 원장 README 7절 초안(사실 / 예측 / 판정 가능한 질문(질문·시한·판정 출처) / 반증 초안 / 정량 기저율(코드 값 그대로 + 표본 한계 문장) / 무효화 조건(ID·임계값·주기·출처) / 실행 규칙 초안). 시한 없는 예측은 쓰지 않는다
- [x] 반증자: `aorch-invest-analyst.md` 본문 규율을 그대로 옮기고 `invest-judge` Step 2 세 장치(렌즈 분할은 프롬프트 안에서 거시·수급 / 구조·제도 / 실행·비용 세 절로, 논거별 "무너뜨려라, 애매하면 기각", C등급 단독 `미확인` 강등) 추가. 출력: 살아남은 반증 / 기각된 반증(이유) / 맹점. 모델은 `QUANTPILOT_RESEARCH_JUDGE_MODEL`
- [x] 포트폴리오 방향: 입력 = `~/investment-decisions/decisions/*.md` 중 `status: open` 레코드의 frontmatter·무효화 조건 절 + 최근 5거래일 시황 노트 + 이번 후보들. 출력: 방향 메모(현재 열린 판단이 오늘 시황과 충돌하는지, 후보가 기존 보유와 집중되는지, 손대지 말아야 할 것). 매매 지시 금지, "확인할 것"으로만
- [x] `pipeline_invest.py`: `run_invest_pipeline(theme, bundle, ledger_root, *, runner, model, judge_model, cwd, max_candidates=3)` — 스카우트 → 후보별 [기저율 계산(코드) → 리서처 → 반증자] → 방향 메모 → 후보 노트 마크다운(7절 + `## 반증 검토` + `## 방향 메모` + `## 출처`)
- [x] `run_invest_research.py`: 인자 `--theme "<문장>"` 또는 `--symbol 005930`(스카우트 생략), `--date`, `--max-candidates`, `--dry-run`(기저율·증거만). 후보 노트를 `candidates/`에 `status: proposed`로 쓰고 슬랙에 "후보 N건 제안됨: 파일 경로" 한 줄만 게시. 종료 코드 규약은 작업 6과 동일
- [x] 테스트: 기저율은 합성 bars로 미래 누출 부재(조건 판정에 미래 값 바꿔도 결과 불변)·표본 수·손실 확률 계산 확인; 파이프라인은 fake runner로 단계 순서·후보 수 상한·종목코드 검증 실패 시 제외; 잡은 `--symbol` 경로와 `--theme` 경로 각 1건, 노트 frontmatter `status: proposed`
- [x] 검증: pytest 통과; `tach check` 통과; `[네트워크]` `& $py -m quantpilot.jobs.run_invest_research --symbol 005930 --date 2026-09-03 --no-post` 로 후보 노트 1건 생성, 7절 전부 비어 있지 않음

### 작업 9: 문서·규약 갱신

대상 파일: `CLAUDE.md`, `AGENTS.md`, `docs/research_agents.md`, `docs/STATUS.md`

- [x] `docs/research_agents.md`: 팀 구성표(에이전트 8개·입력·출력·모델), 일일 잡·투자 잡 실행법, 환경변수 표, 노트 위치, 격리 불변식, 측정 질문, 이월(DART·ECOS·KOSIS·FRED 2차 데이터, 프리플라이트·운영 감시 보안 모드, 핸들 검증기, 투자자 페르소나)
- [x] `CLAUDE.md` "Specialized workflows"에 두 줄: `/ship` 보안 게이트 트리거 안내(`docs/security_gate.md`), 리서치 잡 안내(`docs/research_agents.md`)
- [x] `AGENTS.md` safety adapter에 한 줄: "리서치 에이전트 산출물(시황·후보 노트)은 거래 입력이 아니며 `research_agents`는 거래 코드를 import 할 수 없다(`tach.toml`)"
- [x] `docs/STATUS.md`에 이번 계획 항목 추가(기존 형식 따름)
- [x] 검증: `git diff --stat`에 네 문서; `grep -c research_agents CLAUDE.md AGENTS.md` 각 ≥1

### 작업 10: 도그푸딩과 측정

대상 파일: 이 문서 "실측" 절, `~/investment-decisions/market/`, `~/investment-decisions/candidates/`

- [x] (자격증명 없이 `--skip-news --no-slack`으로 대체 실행; 뉴스·슬랙 포함 실행은 키 준비 후) 환경변수(네이버 2개, 웹훅 1개)를 현재 셸에만 설정하고 `run_market_brief --date <최근 거래일>` 실제 실행. 소요 시간, 에이전트별 모델·시간, 스크럽 대체 건수, 슬랙 도착 여부, 노트 경로를 "실측" 절에 기록
- [x] 브리핑을 읽고 잘못 인용된 수치·URL·볼트 노트가 있는지 대조(증거 JSON과 노트 `## 출처` 절 비교). 불일치는 "발견" 절에 기록하고 프롬프트를 고친 뒤 1회 재실행
- [x] `[네트워크]` `run_invest_research --symbol <관심종목 1개>` 실제 실행. 후보 노트의 7절과 반증 절이 `invest-judge`가 바로 받을 수 있는 형태인지 확인
- [ ] 사용자에게 `scripts/register-market-brief-task.ps1` 실행 여부를 AskUserQuestion으로 확인(예약 등록은 사용자 실행) — 자격증명 파일 준비 후로 이월
- [x] "측정" 절에 1주차 질문과 기록 칸(날짜별 읽음/행동 체크) 작성. 첫 주가 끝나면 결과를 적고 2차 데이터 계획 여부를 판단
- [x] 검증: 전체 검증 명령 3개 통과; 실측·발견·측정 절이 채워짐; 커밋은 `/ship` 요청 시에만(보안 게이트가 이 브랜치 diff에 처음으로 걸린다)

## 심사 결과

`/dependency-audit pykrx` (2026-09-04, sonnet 포크, 도구 호출 38회):

- **기각.** 2025-12-27부터 KRX 정보데이터시스템이 회원제로 바뀌어 pykrx 1.2.x는 `KRX_ID`/`KRX_PW` 자동 로그인 없이는 지수 OHLCV(KeyError)·업종 분류(빈 프레임)·투자자별 순매수(빈 프레임)를 못 받는다. 로그인 없이 되는 것은 종목 OHLCV 하나뿐이고 그것도 pykrx가 네이버 fchart를 긁는 경로다. 오류 처리는 예외를 `print`로 삼키고 빈 DataFrame을 돌려주는 fail-open(이슈 #291).
- 전이 의존성 21개·약 180MB(pandas·numpy·matplotlib·fonttools·PIL). `__init__`이 폰트 등록용으로 `pyplot`까지 import. FinanceDataReader 0.9.202는 pandas 핀이 충돌(`<3.0` vs 3.0.5)하고 KRX 경로는 같은 로그인 벽.
- 개인 계정 자동 로그인은 KRX의 봇 차단 명분("AI 봇 무단 수집")과 정면 충돌하고 로그인 흐름이 하루 단위로 깨진다(이슈 #293, 09-02).
- **채택**: 네이버 파이낸스 공개 엔드포인트를 표준 라이브러리로 호출(09-04 실측 전부 200). 지수 일봉 `fchart.stock.naver.com/sise.nhn?symbol=KOSPI`, 당일 투자자 순매수 `m.stock.naver.com/api/index/KOSPI/trend`(억원), 업종 등락 `/api/stocks/industry`, 종목 일봉 `fchart ...?symbol=<code>`, 종목명 `/api/stock/<code>/basic`. 새 의존성 0개, 자격증명 0개.
- 사용자 선택(09-04): "네이버 JSON + 표준 라이브러리". 지수 수급 이력이 꼭 필요해지면 그때 KRX 계정 개설 여부를 결정한다.

## 실측

### 시황 브리핑 1회 (2026-09-04 13:36, 장중, 워크트리, 뉴스 생략·슬랙 생략)

자격증명 파일이 없어 `--skip-news --no-slack`(이 실행을 위해 추가한 플래그)으로 돌렸고 원장은 스크래치 경로(`QUANTPILOT_LEDGER_ROOT`)로 돌렸다.

| 항목 | 값 |
|---|---|
| 전체 소요 | 107초 (수집 <1초, 분석가 2명 병렬 ≈32초, 편집자 46초) |
| 모델 | opus × 3 |
| 스크럽 대체 | 슬랙 생략이라 해당 없음 |
| 노트 | `market/2026-09-04.md` 생성, frontmatter 6필드·`signal_input: false` |

수치 대조: 노트의 코스피 6700.45(+1.84%)·코스닥 814.47(+3.07%)·외국인 +3,847억·기관 +11,113억·개인 -26,545억·업종 상하위 3·관심종목 이상치 4행(SK하이닉스 +4.07, NAVER +3.37, 신한지주 -3.76, KB금융 -3.60)이 `evidence_2026-09-04.json`과 전부 일치. 새로 계산한 수치 없음("세 주체 합계는 증거에 없음(계산하지 않음)"이라고 스스로 적음). 뉴스가 비었을 때 거시·뉴스 분석가는 네 절 모두 "헤드라인 없음(수집 생략)"으로 답하고 출처를 만들지 않았다. 편집자의 `## 출처`도 "없음"을 정직하게 적었다.

브리핑이 스스로 짚은 것: 거래량 비율이 전부 1 미만인데 지수가 크게 오른 조합 → "데이터 수집 시각(13:36, 장중일 가능성)"을 확인하라고 적었다. 맞는 지적이다(장중 실행). 16:10 예약 실행에서는 사라질 현상.

### 후보 리서치 1회 (2026-09-04 13:43~13:52, 삼성전자 005930, 워크트리 + vault MCP 임시 복사, 뉴스 생략·슬랙 생략)

백그라운드 실행은 두 번 연속 시작 20초 안에 외부에서 중단됐고(원인 미확인, 잡 오류 아님), 전면 실행으로 완료.

| 단계 | 모델 | 소요 |
|---|---|---|
| 종목 리서처 | opus | 199초 |
| 반증자 | fable | 253초 |
| 포트폴리오 방향 | opus | 68초 |
| 합계 | | 520초 |

후보 노트 258줄: 7절 전부 채움(사실 14항목이 증거 JSON과 일치함을 반증자가 대조 확인), 예측 3건 전부 시한 고정("휴장으로 인한 이월 금지"), 기저율은 표본 한계 문장을 수치보다 먼저 배치(비중첩 표본 3/2개라고 정직하게), 무효화 조건 ID·임계값·주기·출처 4종, 실행 규칙 초안에 손실 만회 증액 없음. 볼트 인용 6종 12회([[예측과 캘리브레이션]] 4, [[리스크와 포트폴리오 통합]] 3, [[16장 자금관리와 매매 전술]] 3 등). 반증자는 지지 근거를 "허용 vs 보여줌"으로 갈라 적고 세 렌즈 절을 지켰으며, 방향 메모는 열린 판단 0건(스크래치 원장)임을 그대로 적고 매매 지시를 쓰지 않았다.

반증자가 잡은 결함: 기저율 JSON이 파일로 없어 수치를 검증하지 못했다 → 파이프라인이 `base_rate_<날짜>_<종목>.json`을 에이전트 실행 전에 쓰고 두 프롬프트에 경로를 넣도록 수정(테스트 추가).

## 발견

1. **워크트리에는 vault MCP가 없다.** `vault` 서버와 `mcp__vault__*` 허용은 main의 미커밋 `.mcp.json`·`.claude/settings.json`에만 있어 워크트리에서 띄운 헤드리스 에이전트는 볼트를 못 본다. 첫 브리핑은 "볼트 조회는 하지 않았다"고 스스로 밝혔다. 후보 리서치 실측은 두 파일을 워크트리에 임시 복사해 돌리고 `/ship` 전에 되돌린다. 병합 후 main 체크아웃에서 도는 실제 잡은 영향 없음.
2. **증거 파일 경로가 상대경로로 노트에 들어갔다.** 잡이 `evidence_path`를 상대경로로 넘겨 편집자가 그대로 옮겼다 → `resolve()`로 절대경로를 넘기도록 수정.
3. **pykrx 전제 붕괴** — "심사 결과" 절 참조. 네이버 파이낸스 클라이언트로 교체.
4. **uv 파이썬에는 tz 데이터베이스가 없다** — `Asia/Seoul` 조회가 깨져 `tzdata`를 venv에 추가해야 한다.
5. **`.venv`의 fastapi 0.141/starlette 1.6에서 `test_operator_actor_guard`가 실패한다**(`/api/policies/preview`가 더 이상 미보호 변이 라우트로 열거되지 않아 허용목록과 어긋남). hermes venv(fastapi 0.133.1/starlette 1.0.1)에서는 같은 코드가 통과. 이번 변경과 무관한 의존성 판본 차이라 `.venv`를 0.133.1/1.0.1로 고정했다. 프로젝트에 핀이 없다는 사실 자체가 별도 이월 항목.
6. **OneDrive 안의 `.venv`는 uv 하드링크·삭제와 충돌한다**(os error 396/5). `UV_LINK_MODE=copy`로 설치하고, 패키지 교체가 실패하면 venv를 통째로 새로 만든다. `.env.example` 동기화 테스트는 `USERPROFILE` 같은 OS 변수도 잡으므로 코드에서는 `Path.home()`을 쓴다.
7. **첫 `/ship` 보안 게이트 실측(09-04 14:27, fable 211초)**: verdict `pass`, 발견 6건. 즉시 수정 2건 — RA-001 헤드리스 에이전트가 사용자 수준 MCP(Slack·Figma·Supabase 등)를 물려받음 → 러너가 `--strict-mcp-config --mcp-config <repo>/.mcp.json`으로 프로젝트 MCP(vault)만 허용; RA-006 `--date`가 검증 없이 파일명에 쓰임 → argparse `type`으로 ISO 날짜 강제. 이월 4건 — RA-002 잡이 `services.api.dependencies`를 import(경계 밖이지만 의존 방향 어색; `runtime_guard` 분리 제안), RA-003 `# nosemgrep`를 규칙 ID로 한정(ID 형식 확인 필요), RA-004 게이트 기준 2를 tach 종료 코드 기준으로 명시, RA-005 픽스처 가짜 토큰은 정보성. 스크립트 버그 1건도 잡힘: PS 5.1이 `[]`를 `$null`로 바꿔 gitleaks 0건이 1건으로 세어지던 것 수정.
8. 보안 게이트 워크트리 실행에서 gitleaks가 기존 테스트 픽스처의 가짜 키 2건을 잡았다(`test_professional_operator_path.py:197`, `test_kis_paper_broker_adapter.py:178`). 이번 변경과 무관한 기존 코드라 스테이징 모드에서는 안 잡힌다. 게이트 에이전트가 픽스처를 분간하는지가 첫 `/ship`에서 확인할 점.

## 측정

**1주차 질문**: 5거래일 중 4일 이상 브리핑을 읽고, 1건 이상 행동(관심종목 조정·추가 확인·후보 리서치 실행)했는가.
전제: 자격증명 파일(`~/.quantpilot-research.env`) 준비 → `scripts/register-market-brief-task.ps1` 등록. 등록 전에는 측정을 시작하지 않는다.

| 거래일 | 브리핑 도착 | 읽음 | 행동 | 메모 |
|---|---|---|---|---|
| D1 | | | | |
| D2 | | | | |
| D3 | | | | |
| D4 | | | | |
| D5 | | | | |

판정: 읽음 ≥ 4 이고 행동 ≥ 1 이면 2차 데이터 계획(DART·ECOS·KOSIS·FRED)으로. 아니면 배송 형식·시각·밀도부터 고친다.

**첫 달 질문**: 후보 노트에서 출발한 결정 레코드가 `~/investment-decisions/decisions/`에 1건 이상 있는가.

## 이월

- 보안 게이트 이월 4건(발견 7: RA-002·003·004·005)
- 의존성 핀: `pyproject.toml`에 fastapi·starlette 상한이 없어 새 venv에서 라우트 열거 테스트가 깨진다(발견 5). 핀 도입은 별도 판단
- 2차 데이터: dart-fss(DART 키), PublicDataReader(ECOS·KOSIS·공공데이터포털 키), fredapi(FRED 키) — 브리핑이 한 주 읽힌 뒤 `/dependency-audit` 후 별도 계획
- 보안 모드 2·3: 프리플라이트(trufflehog + pip-audit, 실서버 연결 직전 수동), 운영 감시(JSON 로그·heartbeat·낙폭 알림) — 페이퍼 서버가 로그를 내기 시작할 때
- 불투명 핸들 인용 검증기 — 환각 인용 사례가 2주 이상 쌓이면
- 투자자 페르소나 토론 — 반증자 한 쌍으로 부족하다는 실측이 나오면
- aorch 결함(이월 유지): 워커 exit 1을 anthropic 한도로 오인(`src/limits.js`), reviewer가 플랜 밖 기준 추가
