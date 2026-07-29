# OneDrive × git 간섭 완화 제안 → C안 실행 완료

작성 2026-07-30. 같은 날 사용자 승인으로 **C안을 실행했다** — 기록은 문서 말미 §6.

## 1. 무엇이 일어나고 있나 (실측)

이 저장소는 OneDrive 동기화 경로(`OneDrive\문서\코덱스\주식트레이더`) 안에 있고,
OneDrive Files On-Demand가 `.git` 내부까지 관리한다. 관측된 증상:

| 증상 | 증거 |
|---|---|
| `.git/worktrees/` 항목이 ReadOnly+ReparsePoint로 잠겨 git의 정리(prune)가 `Permission denied`로 실패 | 2026-07-29 `주식트레이더-drift-daily` 사례 — 커밋마다 경고 발생, ReadOnly 해제 후 prune 성공 |
| **정리 후 같은 속성이 재적용됨** | 2026-07-30 재확인: `.git/worktrees` 디렉터리가 다시 `ReadOnly, ReparsePoint` |
| 중단된 쓰기의 쓰레기 객체 | `git count-objects`: `garbage found: .git/objects/d3/tmp_obj_oH5zp2` |
| `.git` 파일 1,279개 중 **1,038개가 ReparsePoint** (Files On-Demand 관리 하) | 실측. 현재 Offline(탈수화) 0개이나 관리 대상임 |

같은 계열의 기존 기록: OneDrive 안 npm 설치 손상 → `node_modules`를
`~\.local\node_modules_store`로 junction (메모리 `onedrive-node-modules-junction`).

규모: `.git` 12.6 MB (pack 5.57 MiB). worktree 91개 중 85개가 OneDrive 안
(대부분 `.codex-worktrees/`), 6개가 `C:\qp-*` (밖).

## 2. 왜 방치하면 안 되나

- git은 `.git` 내부 파일의 원자적 rename/delete에 의존한다. 동기화 클라이언트가
  파일을 잠그거나 속성을 바꾸면 **락 실패, ref 손상, 인덱스 경합**이 간헐적으로
  발생한다. 지금까지는 경고 수준이었지만 실패 지점이 ref 갱신으로 옮겨가면
  커밋·병합이 깨진다.
- 이 저장소는 페이퍼 트레이딩 원장(SQLite journal)의 무결성을 git 이력으로
  감사한다. git 메타데이터 자체가 불안정하면 그 감사 체계의 바닥이 흔들린다.

## 3. 선택지

### A안 — 저장소 전체를 OneDrive 밖으로 이전 (예: `C:\repos\주식트레이더`)

- **효과**: 간섭 원인 완전 제거.
- **비용**: ① OneDrive가 제공하던 작업 트리 백업 상실 — `CONCLUSIONS.md §6`의
  "자동 백업 + 외부 보관" 요건을 다른 수단(GitHub push는 추적 파일만 커버,
  **gitignore된 vault·local_data는 별도 백업 필요**)으로 재구성해야 함.
  ② 외부 worktree 6개 `git worktree repair` 필요. ③ 문서·설정 내 절대경로 갱신.
  ④ CLAUDE.md 등 "Location:" 표기 갱신.
- **되돌림**: 폴더를 다시 옮기고 repair. 데이터 손실 없음.

### B안 — OneDrive에서 이 폴더만 동기화 제외

- **불가에 가까움**: 개인용 OneDrive의 선택 동기화는 클라우드→로컬 방향 선택이며,
  로컬 하위 폴더 하나를 동기화에서 빼는 공식 기능이 없다. "문서 폴더 백업 해제"는
  문서 전체 단위라 다른 프로젝트까지 영향.

### C안 (권장) — `.git`만 밖으로 분리 (separate git dir)

`.git` 디렉터리를 OneDrive 밖(예: `C:\Users\goyan\.local\git-meta\주식트레이더.git`)
으로 옮기고, 저장소 루트에는 `gitdir: <경로>` 한 줄짜리 `.git` **파일**을 둔다.
git이 네이티브로 지원하는 구성이다.

- **효과**: 간섭이 일어나는 곳은 `.git` 내부 메타데이터다 — 그것만 밖으로 나간다.
  **작업 트리(코드·문서·vault)는 OneDrive에 남아 백업이 유지된다.** 기존 백업
  요건을 깨지 않으면서 원인을 제거하는 유일한 방안.
- **비용**: ① 일회성 이전 절차(아래). ② worktree 91개의 gitdir 포인터를
  `git worktree repair`로 재연결. ③ `.git`이 디렉터리라고 가정하는 도구가 있으면
  (드묾 — git 표준 기능이라 대부분 호환) 확인 필요.
- **되돌림**: 디렉터리를 제자리로 옮기고 `.git` 파일을 지우면 원상복구.
- **주의**: `~\.local`은 OneDrive 백업이 없으므로 git 메타는 push가 백업이 된다.
  미푸시 커밋이 로컬 디스크 단일 사본이 되는 구간이 생김 — push 습관과 결합해야 함.

절차 개요 (실행 승인 후):
```powershell
# 1. 모든 git 작업 중지 확인, OneDrive 일시중지
# 2. Move-Item "<repo>\.git" "C:\Users\goyan\.local\git-meta\주식트레이더.git"
# 3. Set-Content "<repo>\.git" "gitdir: C:/Users/goyan/.local/git-meta/주식트레이더.git"
# 4. git -C <repo> worktree repair   (외부 worktree 6개는 경로 지정 repair)
# 5. git status·pytest로 검증, 쓰레기 객체는 git gc로 정리
```

### D안 — 응급처치: `.git`을 "이 디바이스에 항상 유지"로 pin

```powershell
attrib +P -U "<repo>\.git" /S /D
```

- **효과**: 탈수화(Offline 전환)는 막는다.
- **한계**: ReadOnly/ReparsePoint 재적용과 쓰기 시점 간섭은 **막지 못한다** —
  이번에 관측된 증상이 정확히 그것이므로 근본 해결이 아니다. C안 실행 전
  임시 완화로만 의미 있음.

## 4. 권장

**C안.** 근거: 백업 요건(§6)을 유지하면서 원인을 제거하는 유일한 선택지이고,
`.git`이 12.6 MB라 이전 비용이 사실상 0이며, git 표준 기능이라 되돌림이 한 줄이다.
A안은 백업 재설계가 선행돼야 하므로 vault 백업 전략이 정해진 뒤의 후속 선택지로
남긴다. D안은 C안 실행 전까지의 임시 완화로 병행 가능.

## 5. 이 문서가 하지 않는 것 (실행 전 기준)

- (실행 전) 어떤 방안도 실행하지 않았다.
- 쓰레기 객체(`tmp_obj_oH5zp2`) 정리도 보류 — C안 실행 시 `git gc`로 함께 처리.

## 6. 실행 기록 (2026-07-30, 사용자 승인)

- 타이밍: push 직후 — 미푸시 커밋 0, dirty 0, fsck 깨끗한 상태에서 실행.
- **제안과 한 가지 다름**: 대상 이름을 `주식트레이더.git`이 아닌 **`jusik.git`(ASCII)**
  으로 했다. gitdir 경로가 worktree 포인터 파일 91개에 박히는데, 한글 절대경로에서
  도구가 깨진 전력이 두 건 기록돼 있어(비대화형 세션 경로 매칭, openapi-typescript)
  ASCII가 실질 개선이다.
- 절차 실행: OneDrive `/shutdown` → `Move-Item .git → C:\Users\goyan\.local\git-meta\jusik.git`
  → 루트에 `gitdir:` 포인터 파일 → `git worktree repair` (연결 worktree 90개 일괄)
  → OneDrive 재시작.
- 검증 (전부 실측):
  - HEAD `0753365` 유지, `main...origin/main` 동기화 인식
  - 연결 worktree **90/90** `rev-parse` 응답 (실패 0)
  - `git fsck --no-dangling` exit 0 (이전 전·후 동일)
  - `git gc` 후 `garbage: 0` — `tmp_obj_oH5zp2` 정리 확인
  - `run_smoke` exit 0, 루트 `.git`은 한 줄짜리 파일, `git rev-parse --git-dir`가
    OneDrive 밖 경로 반환
- 되돌림: `C:\Users\goyan\.local\git-meta\jusik.git`을 `.git`으로 다시 옮기고
  포인터 파일 삭제 후 `git worktree repair`.
- 남는 운영 규율: git 메타는 이제 OneDrive 백업 밖이다. **push가 곧 백업**이므로
  미푸시 커밋을 오래 쌓아두지 않는다.
