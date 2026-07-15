---
name: openapi-sync
description: >
  QuantPilot의 두 API 스키마 산출물(루트 openapi.json,
  quantpilot/apps/web/src/lib/openapi.d.ts)을 함께 재생성하고 byte-exact로
  검증하는 절차. FastAPI 라우터나 Pydantic 요청/응답 모델을 추가·수정·삭제한
  직후, 프론트에서 openapi.d.ts에 타입이 없거나 안 맞는다고 할 때,
  openapi.json이 스테일한지 점검할 때, generate:api 실행이 필요할 때, 커밋
  전에 스키마 산출물 두 개의 싱크를 확인할 때, 완료 보고용 byte-exact
  증거(경로 수, d.ts diff 없음)를 만들 때, 한글 경로에서 openapi
  재생성/openapi-typescript가 ENOENT로 죽을 때 — 이 모든 경우 반드시 이
  스킬을 사용한다. "API 타입 동기화", "스키마 재생성", "openapi 재생성",
  "프론트 타입 갱신", "d.ts 다시 만들어" 같은 요청은 전부 해당된다. API 응답
  모델을 바꾼 뒤라면 사용자가 산출물 갱신을 명시하지 않아도 이 스킬로 두
  파일을 함께 동기화해야 한다. Use this skill whenever the FastAPI API
  surface changes, when frontend types drift from openapi.d.ts, when
  openapi.json staleness must be checked, or before committing API changes.
  Both artifacts are generated — never hand-edit them.
triggers:
  - "openapi 재생성"
  - "API 타입 동기화"
  - "openapi.d.ts"
  - "regenerate openapi"
  - "schema sync"
---

# OpenAPI Sync

## Why this exists

QuantPilot의 API 스키마 산출물은 두 개이며 **항상 함께** 재생성·커밋해야 한다.
하나만 갱신하면 프론트 타입과 서버 계약이 어긋나고, 완료 보고의
"openapi byte-exact" 증거를 만들 수 없다. 또한 이 저장소는 한글 절대경로에
있어서 `openapi-typescript`가 절대경로 입력에서 죽는 알려진 함정이 있다.

## Artifacts

1. 루트 `openapi.json` — FastAPI 앱에서 생성
2. `quantpilot/apps/web/src/lib/openapi.d.ts` — openapi.json에서 생성

## Procedure

저장소 루트에서:

```powershell
python -c "import json; from quantpilot.services.api.main import app; f = open('openapi.json','w',encoding='utf-8',newline='\n'); json.dump(app.openapi(), f, indent=2, ensure_ascii=False); f.write('\n'); f.close()"
```

그다음 `quantpilot/apps/web`에서 (상대경로 기반이라 안전):

```powershell
npm run generate:api
```

## Known trap: 한글 절대경로

`openapi-typescript`(@redocly resolver)는 한글이 포함된 **절대경로**를 URL
퍼센트 인코딩한 뒤 literal 경로로 취급해 ENOENT로 실패한다 (2026-07-12 확인).

- `npm run generate:api`는 상대경로(`../../../openapi.json`)를 쓰므로 정상 동작한다.
- worktree 등에서 절대경로가 불가피하면: `openapi.json`을 ASCII 경로
  (예: 세션 스크래치패드)에 복사 → 거기서 생성 → 결과 `.d.ts`를 복사해 온다.

## Verification (완료 주장 전 필수)

1. `git diff --stat openapi.json quantpilot/apps/web/src/lib/openapi.d.ts`
   - diff가 없으면 "byte-exact 동기화 확인"으로 보고한다.
   - diff가 있으면 내용을 읽는다: 이번 변경으로 의도된 경로/모델 변화인지 확인.
2. 경로 수 확인 (보고서 증거 형식):
   ```powershell
   python -c "import json; print(len(json.load(open('openapi.json',encoding='utf-8'))['paths']), 'paths')"
   ```
3. **Stale 감지**: 재생성 diff에 이번 작업과 무관한 경로 추가가 보이면, 커밋된
   openapi.json이 이전부터 스테일했을 수 있다 (과거
   `/api/operator/professional-status` 누락 사례). 그 경우 스테일 보정임을
   보고에 명시하고 함께 커밋한다.
4. 프론트 타입 반영 확인: `quantpilot/apps/web`에서 `npm run build`
   (tsc가 새 타입으로 통과하는지).

## Commit rule

두 파일은 같은 커밋에 포함한다. API 변경 커밋과 분리하지 않는 것이 기본이며,
스테일 보정만 있는 경우에는 `chore(api): regenerate stale openapi artifacts`
같은 별도 커밋으로 만든다.
