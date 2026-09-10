---
name: vault-consult
description: "Consult the quantpilot-foundation knowledge vault (20 books, 60+ papers, 550+ notes on market microstructure, risk, asset pricing, factor investing, financial machine learning, technical analysis, time series, statistical learning, optimization, forecasting, data systems, SRE, security) through the `vault` MCP server, and ground the answer in cited notes. Use this skill whenever a question or coding task touches quant strategy, signals, alpha, risk limits, position sizing, drawdown, VaR/tail risk, execution, slippage, transaction cost, liquidity, spreads, market microstructure, backtesting methodology, factor models, valuation, time-series models, forecasting and calibration, portfolio optimization — and equally when it touches data pipeline design, SLO/reliability, or security design for QuantPilot. Applies to questions (\"왜 손절을 이렇게 잡지\", \"이 신호가 유효한가\", \"무엇을 근거로\") AND to implementation work in those areas, where the vault's principles must be checked against the design before writing code. Also use when the user says \"볼트\", \"도서관\", \"서재\", \"지식 vault\", \"책에서 찾아\", \"근거 찾아\", \"무슨 책에 나와\". Do NOT use for routine work outside these domains (UI styling, build config, unrelated refactors)."
---

# Vault Consult

## Why this exists

`quantpilot-foundation/` 볼트는 열람용 위키가 아니라 **판단의 근거 창고**다.
노트 449편이 전부 같은 규격(3줄 요약 → 설명체 본문 → 접힌 근거 장부)이고, 모든
주장이 원문 페이지까지 추적된다. 이 자산은 쓰이지 않으면 존재하지 않는 것과 같다.

인용 없는 답변은 볼트를 읽은 답변과 파라메트릭 지식으로 지어낸 답변이 구별되지
않는다. 그래서 이 스킬의 핵심은 검색이 아니라 **인용 책임**이다.

## When to consult

**반드시 조회한다** — 다음 영역의 질문 또는 코드 작업:

- 전략·신호·알파·팩터, 포지션 사이징, 리스크 한도·드로다운·꼬리위험
- 집행·슬리피지·거래비용·유동성·스프레드·시장 미시구조
- 백테스트 방법론, 시계열 모형, 예측·캘리브레이션, 포트폴리오 최적화, 가치평가
- QuantPilot의 데이터 파이프라인 설계, SLO·신뢰성, 보안 설계

**조회하지 않는다** — 프런트엔드 스타일링, 빌드 설정, 문서 오타, 위 영역과 무관한
리팩터링. 볼트는 이런 작업에 할 말이 없고, 매번 조회하면 노이즈와 비용만 늘어난다.

판단 기준: **"이 결정이 틀리면 돈이나 신뢰성을 잃는가?"** 그렇다면 조회한다.

## How to consult

1. **검색** — `vault` MCP 서버의 `vault_search`.
   - 질문 어휘와 노트 어휘가 다를 수 있으므로 **여러 질의를 한 번에** 던진다
     (`queries[]` — RRF로 병합되어 어느 한 질의에서만 잘 잡혀도 상위로 올라온다).
   - 한국어·영어를 섞어 던진다. 노트는 한글 제목에 영문 원제를 병기한다.
   - 개념이 모호하면 `mode: "semantic"`, 정확한 용어를 알면 기본 `hybrid`.
   - 한 주제를 깊게 파야 하면 `rerank: true` (다국어 크로스인코더, 1–3초 추가).
2. **정독** — 상위 결과를 `vault_read`로 **실제로 읽는다**. 스니펫만 보고 인용하지
   않는다. 스니펫은 노트가 그 주제를 다룬다는 신호일 뿐 주장의 내용이 아니다.
3. **확장(필요 시)** — 노트가 부분적 답만 주면 그래프를 탄다:
   `vault_search`에 `path` + `related: true`로 링크·백링크 이웃을 본다. 주제 MOC
   (`주제 MOC/`)는 사람이 큐레이션한 횡단 인덱스라 출발점으로 좋다.
4. **인용** — 답변에 근거 노트를 `[[노트명]]` 형태로 명시한다. 어느 책 어느 장에서
   왔는지 사용자가 바로 열어볼 수 있어야 한다.

**MCP가 없을 때의 폴백**: `vault` 도구가 목록에 없으면 Grep으로 볼트를 검색하고
`홈.md` → `주제 MOC/` → `개요 — <책>` 순으로 탐색한다. 폴백을 썼다는 사실을 답변에
밝힌다 (검색 품질이 다르므로 놓쳤을 가능성이 있다).

## How to cite

- 근거 노트를 `[[19장 유동성 (Liquidity)]]`처럼 노트명으로 적는다.
- 볼트에 **없는** 내용으로 답할 때는 그 사실을 밝힌다: "이건 볼트 밖 지식이다."
  이것이 인용 규약의 핵심 절반이다 — 공백을 감추면 사용자가 근거의 강도를 오판한다.
- 노트에 dated 경고(구 판본, 스캔본, 출간 전 초고, Reg NMS 이전 미국 시장 등)가
  붙어 있으면 함께 전달한다. KRX에 그대로 옮길 수 없는 서술이 실제로 있다.
- claim ID(`HAR-C19-03` 형식) 인용은 필수가 아니다. 다만 특정 수치나 수식을 옮길
  때는 근거 장부에서 확인하고 필요하면 함께 적는다.

## Proactive surfacing

위 영역의 **코드 작업이나 설계**를 시작할 때, 묻지 않았더라도 착수 시점에 한 번
조회한다. 볼트의 원칙과 지금 설계가 충돌하면 지적한다.

- 착수당 **1회**로 족하다. 파일마다 검색하지 않는다.
- 충돌이 없으면 침묵한다. "볼트를 봤지만 관련 내용이 없었다"는 보고는 노이즈다.
- 지적은 구체적으로: 어느 노트의 어떤 원칙과 어떻게 어긋나는지.

## Growing the library

볼트는 사용을 통해 자란다. 다만 **감사 경로를 끊지 않는 범위**에서만.

**허용**

- **공백·충돌 기록** — 답하다 발견한 미결 질문이나 노트 간 충돌을
  `종합/주장 충돌과 미결 질문*` 계열 노트에 추가한다. 확보하지 못한 자료가 필요하면
  `quantpilot-foundation-meta/`의 획득 큐에 적는다.
- **종합 노트 작성** — 여러 책을 가로지르는 통합이 반복해서 필요해지면 `종합/`에
  노트를 만든다. 규칙:
  - **기존 장 노트 인용만으로** 구성한다. 원문 PDF에서 새 주장을 끌어오지 않는다.
  - frontmatter에 에이전트 작성 표시와 근거 노트 목록을 남긴다.
  - **폴더 밖의 장 노트를 링크할 때는 경로를 명시한다**
    (`[[책 폴더명/NN장 제목|NN장 제목]]`). 장 제목은 책끼리 겹친다 — 실제로
    `01장 서론 (Introduction)`이 두 권에, `01장 소개 (Introduction)`가 네 권에 있어
    바닥 이름만 쓰면 Obsidian이 엉뚱한 책으로 해석할 수 있다. 같은 책 폴더 안에서의
    상호 링크만 바닥 이름을 허용한다.
  - 원천 주장은 사람이 검증한 장 노트에만 존재하고 에이전트는 그 위의 조합만
    담당한다. 이 경계가 유지되면 근거 추적성이 살아 있다.
- 노트를 추가·수정했으면 `vault_reindex`로 인덱스를 갱신한다.

**금지**

- 장 노트·개요 노트·QuantPilot 연결 노트의 생성이나 수정. 이들은
  `docs/vault_book_ingestion_runbook.md`의 입고 절차(원문 확보 → 해시·대장 기록 →
  절별 검토 원본 → 규격 재작성)가 관할한다. 그 절차를 건너뛴 노트는 대조 기준이
  없어 볼트 전체의 신뢰도를 떨어뜨린다.
- 원문 PDF를 저장소나 OneDrive 안으로 옮기는 것.
- 확보하지 못한 자료에 대한 노트 작성.

## Operational notes

- **경로는 검색이 돌려준 문자열을 그대로 `vault_read`에 넘긴다.** 결과 경로의 한글은
  NFD(분해형)로 오고 사람이 타이핑한 한글은 NFC라, 눈으로 같아 보여도 문자열 비교는
  어긋난다. 도구는 양쪽을 모두 받아주므로 읽기에는 문제가 없으나, **경로를 직접 비교하거나
  조립하지 말고** 검색 결과를 그대로 쓴다. 사용자에게 인용할 때는 노트 제목(`title` 필드)을
  쓰면 이 문제를 피할 수 있다.

- 인덱스는 `C:\Users\goyan\.cache\qpf-search\index.db` — OneDrive **밖**이다.
  동기화 중인 폴더의 살아 있는 SQLite는 잠금 충돌을 부른다. 볼트 안으로 되돌리지 않는다.
- 임베딩은 로컬 모델(`Xenova/multilingual-e5-small`)로 강제돼 있다. `.mcp.json`의
  `OPENAI_API_KEY: ""`가 그 장치다 — 이 환경에는 실제 키가 설정돼 있어서, 비우지
  않으면 개인 장서 449편이 원격 API로 전송된다. **이 설정을 지우지 않는다.**
- `.gitignore`가 볼트를 제외하므로 `OBSIDIAN_RESPECT_GITIGNORE=false`가 필요하다.
  이것 없이는 색인이 조용히 0편이 된다.
- **색인 상태: 551편 전량**(2026-08-06 확인, 누락 0). 검색에 안 잡히는 노트가 생기면
  프런트매터 YAML 오류를 먼저 의심한다 — 값 안에 콜론+공백이 따옴표 없이 들어가면
  (`책: Forecasting: Principles and Practice`) 파싱이 깨져 그 노트가 통째로 색인에서
  빠진다. 실제로 17편이 이 문제로 빠져 있었다. 진단은 포크의 `yaml-check.mjs`,
  수정은 `yaml-fix.mjs`(기본 dry-run), 이후 `vault_reindex`.
- 설계 근거: `docs/superpowers/specs/2026-08-05-vault-knowledge-layer-design.md`
