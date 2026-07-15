---
name: status-sync
description: >
  Update QuantPilot's living status board docs/STATUS.md after a stage, gate,
  or mission task completes — refresh the stage table, add a dated "최근 완료"
  entry with exact verification evidence, and keep detailed rationale in a
  separate docs/*_report.md. Use this skill whenever work on QuantPilot
  finishes and needs recording, whenever the user says "STATUS 갱신", "현황판
  업데이트", "완료 보고서", "상태 문서에 반영", or a mainline integration /
  gate completion needs documenting. STATUS.md has strict conventions
  (overwrite-style living doc, evidence format, no live-readiness claims) that
  this skill encodes.
triggers:
  - "STATUS 갱신"
  - "현황판"
  - "완료 보고"
  - "상태 문서"
  - "update status"
---

# Status Sync

## Why this exists

`docs/STATUS.md`는 시점별 보고서가 아니라 **갱신형 현황판**이다. 스테이지가
끝날 때마다 이 파일을 덮어쓰고, 상세 근거는 별도 `docs/*_report.md`에 남긴다.
형식이 어긋나면 다음 세션의 에이전트와 사용자가 프로젝트 상태를 오독한다.

## What to update in STATUS.md

1. **머리말의 "마지막 갱신" 날짜** — 오늘 날짜(절대 날짜)로.
2. **단계별 상태 표** — 해당 행의 상태(✅/🟡/❌)와 비고를 갱신. 새 영역이면 행 추가.
3. **"최근 완료 (YYYY-MM-DD, 미션명)" 섹션** — 날짜 헤더로 새 항목 추가.
   오래된 완료 항목은 상세 report 문서가 존재하는 한 압축하거나 제거해도 된다
   (living doc이므로 무한 누적하지 않는다).
4. **"다음 단계 후보"** — 끝난 항목을 지우고 순번 재조정.
5. **"사람 입력 대기"** — 해소된 항목은 `[x]` + 취소선 + 확정 내용으로.

## Evidence format (완료 항목마다 필수)

증거 없는 완료 주장은 쓰지 않는다. 관례 형식:

- 백엔드: `pytest N개 중 M passed·K skipped (junit)` — 수치는 junit XML에서 읽는다
  (요약줄은 이 환경 파이프에 안 잡힘).
- 스모크: `smoke OK (broker mock, live 비활성, operator blocked)`
- 프론트: `vitest N passed, build OK`
- API: `openapi.json 바이트 단위 동기화 (N paths) + d.ts diff 없음` — [[openapi-sync]] 스킬 절차.
- 커밋: mainline hash 또는 branch head hash를 명시.

## Hard conventions

- **라이브 준비 주장 금지.** 어떤 완료 항목도 실거래 가능을 암시하지 않는다.
  fake-only 완료는 "실제 KIS Gate P는 미검증" 류의 한정을 단다.
- **비차단 한계 명시.** 알려진 한계는 "비차단 한계 (라이브 준비 주장 아님)"
  패턴으로 완료 항목에 병기한다.
- **안전 불변식 블록은 건드리지 않는다** (`LIVE_TRADING_ENABLED=false` 등).
  기본값 변경은 이 스킬의 범위 밖이며 사용자 승인 사안이다.
- **검증 명령 블록 유지** — 문서 하단의 명령이 실제와 어긋나면 함께 고친다.

## Detailed report doc

상세 근거(설계 결정, 측정치, 재현 명령)는 `docs/<topic>_report.md` 또는 해당
미션 workboard에 남기고, STATUS.md에서는 한 단락 요약 + 문서 링크만 한다.
기존 report가 있으면 새 문서를 만들지 말고 섹션을 추가한다
(예: local_data_backtest_validation_report.md의 누적 섹션 방식).

## Commit

문서 갱신은 `docs: ...` 커밋으로 분리한다 (예:
`docs: record gate 010 mainline integration`). 코드 변경 커밋에 섞지 않는다.
