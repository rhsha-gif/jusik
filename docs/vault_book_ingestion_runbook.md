# 지식 vault에 자료 한 건 추가하기 (runbook)

대상: `quantpilot-foundation/` 지식 vault에 책 또는 논문 한 건을 추가할 때.
이 문서는 절차만 담는다. vault가 무엇이고 왜 그렇게 생겼는지는
`quantpilot-foundation/README.md`와 `00-governance/`가 기준이다.

## 0. 먼저 알아야 할 두 가지 경계

**vault는 닫힌 집합이다.** `tools/validate_vault.py`는 R0 릴리스 매니페스트
(`00-governance/release-manifest.md` §4)의 인벤토리와 실제 파일 목록을 대조해서,
등록되지 않은 파일이 있으면 `MISSING_MANIFEST_PATH`로, 바이트가 바뀌었으면
`MANIFEST_HASH_MISMATCH`로 거부한다. 아무 파일이나 넣을 수 없다.

다만 세 가지 면제가 있다 (`tools/validate_vault.py:76-81`):

| 면제 대상 | 규칙 |
|---|---|
| `60-book-notes/**` | R0 인벤토리 밖. `60-book-notes/release-manifest-r1.md`가 관장 |
| `90-private-sources/**` | 원문 PDF 자리. 이 저장소에서는 **비워둔다** (아래 참조) |
| 파일명에 `r1` 토큰 | `validate_r1_books.py`, `test_package_r1_vault.py` 등 |

**그래서 새 자료의 노트는 반드시 `60-book-notes/` 아래에 만든다.** vault 루트나
`00`~`50` 디렉터리에 새 파일을 넣으려면 R0 매니페스트를 수정해야 하는데, 그것은
릴리스 기록을 고쳐 쓰는 일이므로 이 runbook의 범위가 아니다. 파일명에 `r1`을 끼워
넣어 면제를 우회하지 않는다.

**원문 PDF는 저장소에 들어오지 않는다.** 위치는 다음 한 곳이다:

```
C:\Users\goyan\.local\qp-private-sources\
```

OneDrive 경계 밖이라 클라우드 동기화도 되지 않는다. 저장소 루트 `.gitignore`에
백스톱 규칙이 있지만 그건 실수 방지용이고, 기본 동작은 "애초에 넣지 않는 것"이다.
근거: `60-book-notes/source-authorization-r1.md`의 `redistribution: private_owner_only`.

## 1. 원문 확보와 해시 고정

```powershell
$priv = "C:\Users\goyan\.local\qp-private-sources"
Copy-Item "<원문>.pdf" $priv
(Get-FileHash "$priv\<원문>.pdf" -Algorithm SHA256).Hash.ToLower()
```

이 SHA-256이 이후 모든 단계에서 자료의 신원이다. 판(edition)과 인쇄본이 다르면
다른 자료로 취급한다 — QRM 2005 초판과 2015 개정판이 별도 레코드인 이유가 그것이다.

## 2. Owner 승인 레코드 append

`60-book-notes/source-authorization-r1.md`에 **새 레코드를 덧붙인다.** 기존 행을
고치지 않는다. 근거: `00-governance/project-charter.md §7` — 새 결정은 과거 기록을
덮어쓰지 않고 `superseded` 표시와 successor 링크로 남긴다.

레코드에 반드시 들어갈 것:

- `source_id` (`book-<저자>-<제목>-<판>-owner-copy` 형식)
- 파일 SHA-256
- `provenance_confidence` — 출처 경로를 확인하지 못했으면 `unverified_web_download`로
  솔직히 적는다. 이 필드는 적법성 판정이 아니라 **무엇을 확인했고 무엇을 못 했는지**의
  기록이다.
- `allowed_use` — 연구까지인지, production 설계 근거까지인지
- `redistribution: private_owner_only`

`10-sources/source-registry-r1-owner.jsonl`에도 같은 `source_id`와 해시로 한 줄
추가한다. 두 곳의 해시가 다르면 `validate_r1_books.py`가 잡는다.

## 3. 스펙에 챕터 구조 등록

`60-book-notes/books-r1-spec.json`이 검증기의 기준표다. 여기 없는 노트는
`UNSPECIFIED_CHAPTER_NOTE`로 거부되고, 여기 있는데 파일이 없으면 누락으로 거부된다.

책 하나당 등록할 것:

```json
{
  "source_id": "book-...-owner-copy",
  "directory": "<디렉터리명>",
  "chapter_count": 29,
  "section_count": 187,
  "claim_count": 187,
  "claim_prefix": "HARRIS2003",
  "source_sha256": "<해시>",
  "private_pdf": "<파일명>.pdf",
  "chapters": [
    { "number": 1, "file": "01-....md", "required_sections": ["1.1", "1.2"] }
  ]
}
```

`section_count`와 `claim_count`는 노트를 다 쓴 뒤 실제 값으로 채운다. 검증기가
스펙 값과 노트 실물을 대조하므로, 스펙을 먼저 어림잡아 적어두면 반드시 틀린다.

## 4. 챕터 노트 작성

템플릿: `99-templates/book-chapter-note-r1.md`.
살아있는 예시: `60-book-notes/trading-and-exchanges-2003/chapters/19-liquidity.md`.

절 하나마다 아래 6개 필드가 **전부** 있어야 한다. 하나라도 빠지면
`MISSING_SECTION_FIELD`다.

```
- 중심 질문·논리:
- 핵심 개념·수식:
- 전제·필요 입력:
- 한계·실패 조건:
- QuantPilot 적용:
- QuantPilot 비적용·추가 검증:
- 근거: 인쇄면 pp. N-M, PDF pp. N-M
```

그리고 장 끝에 `claim_id` 장부(`<PREFIX>-C<장>-<절>`)와 검토 범위 체크리스트.

지켜야 할 선:

- **자기 문장으로 요약한다.** 장문 인용, 표 전재, 연습문제·해답 복제는 금지다.
  검증기가 반복 보일러플레이트와 템플릿 조각 재사용을 탐지해 거부한다.
- **읽지 못한 내용을 지어내지 않는다.** 수식이나 도표가 불확실하면
  `needs_visual_check`와 페이지 locator를 남긴다.
- **시대 조건을 분리한다.** 2003년 미국 시장 제도 서술과 항구적 원리는 다르다.
  전자는 dated로 표시한다 — 이 vault는 한국 시장도 대상이므로 특히 중요하다.

`00-book-map.md`도 같이 쓴다: 서지 신원, PDF 해시, provenance 단서, 전체 챕터 표,
책 내부 선행관계, QuantPilot 관련도 지도, 시각 확인이 필요한 절 목록.

## 5. 검증

세 검증기가 **전부 exit 0**이어야 완료다.

```powershell
$v = "quantpilot-foundation"
$priv = "C:\Users\goyan\.local\qp-private-sources"

python "$v\tools\validate_registry.py" "$v\10-sources\source-registry.jsonl"
python "$v\tools\validate_vault.py" "$v"
python "$v\tools\validate_r1_books.py" "$v" `
    --spec "$v\60-book-notes\books-r1-spec.json" `
    --private-source-dir $priv `
    --release-profile quantpilot-foundation-r1
```

계약 테스트도 돌린다:

```powershell
python -m unittest discover "quantpilot-foundation/tests"
```

기대 결과는 `OK (skipped=11)`이다. **skip 11건은 정상이다** — Windows가 적대적
픽스처를 물리적으로 만들 수 없어서다 (심볼릭 링크 6+2건은 Developer Mode 필요,
비-UTF8 파일명 1건, 대소문자 충돌 1건, 파일명 내 역슬래시 1건). 플랫폼이 아니라
능력을 탐지하므로, Developer Mode를 켜거나 Linux에서 돌리면 자동으로 실행된다.
skip 수가 11보다 **늘면** 새로 깨진 것이니 확인한다.

> `--release-profile quantpilot-foundation-r1`은 현재 3권 범위를 동결한 프로파일이다.
> 책을 추가하면 이 프로파일과 어긋나 `RELEASE_PROFILE_MISMATCH`가 난다.
> `tools/validate_r1_books.py`의 `RELEASE_PROFILES`를 새 범위로 갱신하거나,
> 새 프로파일 이름을 만들어 함께 관리한다.

## 6. 남기는 기록

`60-book-notes/release-manifest-r1.md`에 추가한 파일과 해시, 그리고 검증 실제 출력을
기록한다. 완료 보고에는 다음을 포함한다:

1. 추가한 자료와 해시
2. 작성한 노트 파일 목록
3. 실행한 명령과 **실제 출력** (테스트 수, skip 수, 검증기 exit code)
4. 확인하지 못한 것 — provenance, 시각 확인이 필요한 절, 시대 조건

## 지금 하지 않는 것

- LLM/RAG 검색 경로 연결 — 아직 미구현이다. 현재 vault는 사람이 Obsidian으로
  읽고, 에이전트가 파일을 직접 읽는 수준까지다.
- 원문 PDF를 저장소나 OneDrive에 올리는 것.
- 확보하지 못한 자료의 노트 작성. `10-sources/book-acquisition-queue.md`의
  P0/P1/P2와 `paper-access-exceptions.md`가 미확보 목록이다.
