# 지식 vault에 자료 한 건 추가하기 (runbook, v2)

대상: `quantpilot-foundation/` 지식 vault에 책 또는 논문을 추가할 때.
2026-07-30 개편 이후 기준이다 — vault는 **Obsidian 열람용 지식 노트만** 담고,
거버넌스·검증기·원본 노트는 `quantpilot-foundation-meta/`에 있다.

> 구 버전(검증기 3종이 강제하던 시절)의 절차는 이 문서의 git 이력과
> `quantpilot-foundation-meta/README.md`의 은퇴 기록에 있다.

## 0. 구조 한눈에

```
quantpilot-foundation/                 Obsidian vault (git 미추적)
├── 홈.md · 주제 MOC/
├── <영문제목> — <한글분야> (<저자 연도>)/   책 폴더
│   ├── 개요 — <영문 짧은제목>.md            허브: 서지·읽기 지도·장 목차(=파일명 규범)
│   ├── NN장 한글제목 (영문제목).md × 장수
│   └── QuantPilot 연결 — <짧은제목>.md      실무 연결 (설계 추론 집약)
└── 종합/                                   책을 가로지르는 통합 노트

quantpilot-foundation-meta/            메타 (git 미추적)
├── original-notes*/                        절별 6필드 형식의 검토 원본 — 재작성의 근거
├── book-notes-meta*/                       스펙 json·owner 승인·릴리스 매니페스트
└── source-ledger.md                        PDF 해시·판본 대장

C:\Users\goyan\.local\qp-private-sources\   원문 PDF (git·OneDrive 밖)
```

## 1. 원문 확보와 대장 기록

1. PDF를 `C:\Users\goyan\.local\qp-private-sources\`에 배치, SHA-256 계산.
2. `meta/source-ledger.md`에 행 추가 (판본·파일명·해시. truncated/스캔본이면 명시).
3. owner 승인 레코드를 `meta/book-notes-meta*/source-authorization-*.md`에 **append**
   (기존 레코드 수정 금지). `redistribution: private_owner_only`.

## 2. 검토 원본 작성 (LLM 작업, 절별 정밀 검토)

`meta/original-notes*/<책슬러그>/` 아래에 구 형식으로 먼저 만든다:
- `00-book-map.md` — 서지, 장 표(인쇄면·PDF면), 페이지 오프셋 규칙, 시각 재확인 항목
- `chapters/NN-slug.md` — 절마다 6필드(중심 질문·논리 / 핵심 개념·수식 / 전제·필요 입력 /
  한계·실패 조건 / QuantPilot 적용 / 비적용·추가 검증) + 말미 claim 장부
  (`<PREFIX>-Cnn-mm | 주장 | 인쇄면 locator`)

이 단계의 원칙: 장문 인용 금지, 원문에 없는 것 창작 금지, 불확실한 수식은
`needs_visual_check` + locator.

> 왜 원본을 거치나: 재작성 노트가 의심스러울 때 돌아갈 **대조 기준**이고,
> claim 장부의 원 출처다. vault 노트만 고치고 원본을 안 만들면 감사 경로가 끊긴다.

## 3. vault 재작성 (승인된 규격)

스타일 견본: Harris `19장 유동성 (Liquidity).md` (서술형),
QRM `07장 극단값이론 (Extreme Value Theory).md` (수식형).

1. **허브 먼저** — `개요 — <짧은제목>.md`. 장 목차 위키링크가 곧 챕터 파일명 규범
   (`NN장 한글제목 (영문제목)`, 파일명 금지문자는 `-`/`·` 치환).
2. **챕터** — 프런트매터(책/저자/장/인쇄면/태그) → `[!abstract]` 3줄 요약 →
   설명체 본문(함정 `[!warning]`, 원칙 `[!note]`, dated 경고, 수식 `$..$`) →
   `## 관련`(이전/다음/개요) → 접이식 `[!info]- 근거 장부`(claim 전량 이관, 인쇄면 기준,
   오프셋 규칙은 장부 헤더에).
   - "QuantPilot 적용/비적용" 필드는 본문 제외 → 연결 노트로.
   - needs_visual_check 승계 필수.
3. **연결 노트** — 장별 "쓴다 / 쓰지 않는다·추가 검증" 압축.
4. 필요 시 종합·주제 MOC·홈 갱신.

대량 작업이면 워커(서브에이전트) 배치: 허브 → 챕터(책당 1-2배치) → 연결.
워커에게 견본 정독을 강제하고, 완료 보고에 claim 행 수 대조를 요구한다.

## 4. 검증 (수동, 스크립트 한 번)

검증기는 은퇴했다. 아래를 일회성 스크립트로 확인한다:

- [ ] 챕터 파일 수 = 북맵 장 수
- [ ] 책 전체 claim 행 수 = 북맵 `claim_count` (**정확히 일치해야 함**)
- [ ] 전 파일 `[!abstract]` 존재, 챕터 전 파일 `근거 장부` 존재
- [ ] 깨진 위키링크 0 (Obsidian 그래프로도 재확인 가능)
- [ ] `needs_visual_check` 항목이 원본 대비 소실되지 않음
- [ ] PDF 해시가 source-ledger와 일치

## 5. 기록

- `docs/STATUS.md` 갱신 (status-sync 규약)
- vault·meta는 git 미추적이므로 커밋 대상은 docs뿐

## 하지 않는 것

- 원문 PDF를 저장소·OneDrive 안에 두는 것
- 확보하지 못한 자료의 노트 작성 (`meta/sources/book-acquisition-queue.md`가 미확보 목록)
- LLM/RAG 검색 연결 (별도 단계 — 현재는 사람이 Obsidian으로, 에이전트가 파일로 직접 읽음)
