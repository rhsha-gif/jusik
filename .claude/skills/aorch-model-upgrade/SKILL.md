---
name: aorch-model-upgrade
description: 새 프런티어 모델이 나왔을 때 프로젝트를 신모델의 판단으로 점검·개선하고 모델 참조를 옮기는 런북. 사용자가 "신모델 나왔어", "모델 업그레이드", "새 모델로 점검해", "Fable 6 대응", "모델 바뀌었으니 점검"이라고 하거나 카탈로그에 새 모델 프로필을 넣을 때 사용한다. 단일 파일의 모델 ID 치환 요청에는 쓰지 않는다.
---

# aorch 모델 업그레이드 런북

새 모델이 나올 때마다 같은 순서로 돈다. 판단은 `auditor` 역할이, 인벤토리와 실사용 실행은 오케스트레이터(이 세션)가, 수정은 사용자가 고른 것만 worker 태스크가 한다. 산출물은 프로젝트 `docs/plans/<날짜>-model-upgrade-<모델>.md` 한 문서에 계획·실측·발견·선택·이월을 모은다.

## 0. 카탈로그 등록

- `config/aorch.config.json` `models`에 새 프로필을 넣는다. **`maturity`는 `challenger`**로 시작한다 — 라우터의 critical 게이트(`criticalMinimumSamples`)는 challenger에만 걸리고, stable로 등록하면 관측 0건인 모델이 곧바로 critical 작업의 단독 실행자가 된다(2026-09-02 실측: fable이 그렇게 등록돼 있었다).
- 품질 prior는 직전 최상위 프로필 + 0.02 이내. 에스컬레이션 사다리 끝에 붙인다.
- 검증: `node --test test/config.test.js test/router.test.js`.

## 1. 브랜치와 설치본

- `node src/cli.js branch status` → 사용자 승인 → linked worktree에서 작업.
- 워크트리에서는 dispatch 전에 반드시 `node src/cli.js install --project . --target both --force-config`. 없으면 Claude CLI가 상위 체크아웃의 구 설치본을 읽어 `--agent not found`로 2초 만에 죽는다(2026-08-30 실측).
- **dispatch는 git bash에서 `nohup node src/cli.js dispatch … > log 2> err < /dev/null &`로 띄운다.** 검증 게이트는 dispatch 프로세스의 환경을 물려받는데, PowerShell `Start-Process`로 띄우면 `test/executor.test.js`의 PATH shim 테스트가 `spawn fixture ENOENT`로 죽어 멀쩡한 워커가 실패·에스컬레이션된다(2026-09-02 실측). 종료 감시는 로그 파일 크기(dispatch는 끝에 JSON을 쓴다)로 하고, Windows PID가 필요하면 `Get-CimInstance Win32_Process`로 찾는다.

## 2. 인벤토리 — 오케스트레이터가 직접

정찰은 위임하지 않는다(adaptive-orchestrate 규율). 아래 패턴을 대상 루트에서 직접 돌리고 결과를 문서 "매핑표" 절에 `경로 | 현재 값 | 새 값 | 승인` 표로 적는다. 갱신은 사용자가 승인한 행만 한다.

```bash
grep -rnE "claude-[a-z]+-[0-9]|\b(opus|sonnet|haiku|fable|mythos)\b|gpt-[0-9]|canonicalModel|^model:" \
  --include=*.md --include=*.json --include=*.toml --include=*.yaml --include=*.yml --include=*.js --include=*.py \
  --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=task-runs --exclude-dir=worktrees .
```

## 3. 실사용 실행 — 오케스트레이터가 직접

플랜에 실사용 태스크를 두지 않는다. 바뀐 것 없는 트리에서 스모크만 돌리는 읽기 전용 태스크는 모델과 무관하게 통과해 무의미한 관측을 남긴다. 대신 대상 프로젝트의 규약 문서와 기존 smoke 명령에서 핵심 사용자 흐름 1회를 고른다. 이를 직접 실행하고 로그·스크린샷을 `<usage-evidence-dir>`(관례: `.aorch/evidence/<날짜>/`)에 남긴다.

## 4. 감사

- `examples/plan-model-upgrade.json`을 복사해 `<project-root>`, `<scope>`, `<conventions-doc>`, `<usage-evidence-dir>`를 채운다. A1의 `allowedProfileIds`에 새 프로필 id를, R1의 `allowedProviders`에 다른 프로바이더를 넣고 `--dry-run`으로 확인한다. 품질 라우팅으로는 고정할 수 없다 — 2026-09-02 실측: `minimumQuality 0.95`+anthropic이 opus xhigh로 갔다(opus는 review 사전값 0.96에 effort 가산, fable은 review 사전값 없음·high effort가 critical 복잡도 전용). 그래서 A1은 complexity critical, risk high다.
- git이 아닌 대상(전역 `~/.claude`)은 aorch 저장소를 projectRoot로 두고 objective에 절대 경로를 적는다. 세 태스크가 모두 `write: false`라 change guard는 aorch 트리만 본다.
- git 아닌 대상은 dispatch 전에 `~/.claude/backups/<날짜>-pre-audit/`로 복사하고, 런 종료 후 diff가 비어 있는지 확인한다.
- `dispatch --timeout-ms 1200000`. **run 중 커밋 금지**(change guard가 HEAD 이동을 잡아 run 전체가 무효).
- receipt 스키마를 손댈 때는 `required`에 모든 키를 넣고 선택 필드를 두지 않는다 — OpenAI strict 구조화 출력이 400 `invalid_json_schema`로 모든 Codex 워커를 죽인다(2026-09-02 실측, `test/task-runner-receipt.test.js`의 재귀 계약 테스트가 회귀를 막는다).
- run이 실패해 멈추면 `verification.json`의 검증 출력과 change guard를 먼저 읽는다. 에스컬레이션은 직전 시도의 트리 변경을 되돌리지 않으므로, 게이트가 환경 문제로 실패한 경우 직전 워커의 변경을 직접 검증하고 그 태스크를 뺀 플랜으로 재dispatch한다.
- A1 receipt의 `findings`를 문서 "발견" 절에 표로 옮긴다. 표기: critical 치명, high 불편, standard·low 사소. `fixCost`는 그대로.

## 5. 선택

AskUserQuestion(multiSelect)으로 수정할 finding id를 고른다. 자동 수정은 없다.

## 6. 적용

- `examples/plan-model-upgrade-apply.json`을 복사해 고른 finding마다 `X<n>-<id>` 태스크(동작 보존이면 `refactorer`, 아니면 `worker`), 매핑표 승인 행으로 `M1-model-refs`를 채운다. 모든 write 태스크에 프로젝트의 진짜 검증 명령을 둔다 — 이 플랜이 새 모델의 첫 라우팅 관측을 남기는 자리다.
- 다른 프로젝트에 적용할 때의 함정(2026-09-02 QuantPilot 실측): (a) 프로젝트 규약이 요구하는 linked worktree에서 돌리고 `aorch install`을 그 worktree에 다시 한다. `.aorch`가 gitignore가 아닌 프로젝트는 설치본이 git 변경으로 잡힌다. (b) Codex 워커의 쓰기 루트는 worktree다 — 검증 명령의 임시 경로·node_modules를 worktree 안에 둔다(junction은 안 되고 실복사). (c) pytest basetemp는 만든 계정만 읽는 ACL이라 게이트는 `.pytest_tmp/gate-%RANDOM%`(cmd.exe가 확장, 실행마다 새 디렉터리), 워커는 `.pytest_tmp/worker-<task>`를 각자 만들게 하고 워커에 `gate*` 접근 금지를 명시한다; 부모는 일반 mkdir. (c2) 웹 게이트(vite/vitest)는 Codex 샌드박스 안에서 설정 로더가 상위 경로 접근으로 실패한다 — 워커 지침에 "샌드박스 권한만의 실패면 complete로 보고하고 unresolvedRisks에 적어라, 게이트가 밖에서 같은 명령을 돌린다"를 넣는다. (c3) 이미 dirty한 트리에서 워커는 '이번에 만진 파일'을 원래 dirty였더라도 filesChanged에 적어야 한다(base 프롬프트 문구를 오독해 미신고 → change guard 실패 실측). (d) 감사 receipt는 메인 체크아웃 `.aorch/task-runs`에 있어 worktree 샌드박스가 못 읽는다 — 제안 전문을 플랜 objective에 인라인한다(템플릿이 그렇게 되어 있다). (e) kind `security`는 complexity `standard`에 실행자 프로필이 없다 — 보안 태스크는 `high` 이상으로. (f) 워커가 환경 때문에 `partial`을 내면 run이 멈춘다 — 리드가 게이트를 직접 돌려 채택하고 나머지 태스크만 재dispatch한다. (g) 하드닝(도구 차단·경로 검증·커밋 범위 축소) 라운드는 회귀를 만든다(2026-09-03 볼트 실측: V1·V2 뒤 회귀 10건, 치명 1건은 `--disallowed-tools`에 Write를 넣어 새 노트 생성이 막힌 것). 같은 프로바이더의 게이트는 그것을 통과시켰다 — 하드닝 적용 뒤에는 반드시 다른 프로바이더의 동작 보존 리뷰(`R2-behaviour-review`)를 두고, 회귀 수정 라운드 뒤 새 모델 핀 최종 리뷰로 닫는다. (h) 에스컬레이션 2차 워커가 게이트를 통과시키려고 검증 대상 자체를 무력화할 수 있다(볼트 실측: weekly 드라이런에 조기 return 삽입). 게이트가 스크립트 실행이면 objective에 '이 명령은 진짜 실행이어야 하고 조기 종료·분기로 우회하면 실패'를 명시하고, 리드가 게이트 대상 파일의 diff를 채택 전에 읽는다. (i) 리뷰-수정 루프는 수렴하지 않는다 — 새 모델 핀 리뷰는 직전 수정 라운드에서 매번 standard 1건을 새로 찾는다(2026-09-03 볼트 실측: R3→R8 여섯 리뷰가 각각 직전 라운드의 회귀를 찾았고, 그중 R6는 병합 불가·R7은 보류였다 — 고아 복구처럼 상태 기계를 새로 넣는 수정은 특히 그렇다). 최종 리뷰 뒤 수정 라운드는 최대 2회로 못 박고, 그 뒤의 발견은 critical이 아니면 문서 '이월' 절로 보낸다. 리뷰어가 '병합 불가'를 내면 그 라운드를 되돌리는 것이 기본 선택지여야 하는데, task-run 디렉터리에 attempt별 diff가 없어 되돌릴 수 없었다 — dispatch 전에 `git diff HEAD > <evidence>/state-before-<round>.patch`를 남긴다. 상태 기계·마이그레이션이 들어가는 수정은 리드가 직접 쓰는 편이 빨랐다(V9: 3건 10분, 워커 왕복 17분+회귀). (j) allowedScope의 경로는 워커가 실제로 고칠 파일 경로여야 한다 — 루트 `README.md`를 적었는데 워커가 `slack-worker/README.md`를 고쳐 change guard가 범위 밖으로 실패한 실측. 플랜을 만들 때 scope 경로가 존재하는지 `ls`로 확인한다.
- git이 아닌 대상은 오케스트레이터가 직접 고친다. 고치기 전 원본을 `~/.claude/backups/`에 복사한다.

## 7. 재설치와 승격

- 각 프로젝트에서 `.aorch/config.json`을 `config/aorch.config.json`과 diff한다. 차이가 없으면 `--force-config`, 차이가 있으면 플래그 없이 `install`을 돌리고 새 프로필과 사다리 단계만 프로젝트 config에 손으로 추가한다.
- 검증 명령이 없는 리뷰 태스크(A1 포함)는 관측을 남기지 않는다. 승격 근거는 새 모델이 실제로 실행한 write 태스크 또는 검증 명령을 가진 reviewer 태스크에서만 쌓인다(2026-09-02: 감사 2회 뒤에도 fable 관측 0건).
- `.aorch/observations.jsonl`에서 새 모델의 `role: reviewer` 관측 수를 센다. `criticalMinimumSamples`(기본 3) 이상이면 사용자에게 물어 `maturity`를 `stable`로 올리고 문서에 건수와 날짜를 적는다. 미만이면 challenger로 둔다.

## 8. 되먹임

이번 run에서 절차가 바뀐 곳(패턴 누락, 템플릿 필드, 함정)을 이 스킬과 `CHANGELOG.md`에 반영한다. 문서 "이월" 절에 다음 모델 때 볼 것을 남긴다.

<!-- aorch-generated: skill:aorch-model-upgrade; mode=native; edit integrations/shared/definitions.json -->
