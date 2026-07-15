---
name: workboard-flow
description: >
  QuantPilot 미션 작업보드(docs/*_workboard.md)의 태스크 상태를 프로토콜대로
  바꾸는 절차: 문서 편집 lease 획득·해제, ready 작업 claim(정확히 하나),
  ready→in_progress→review→integrated→done/blocked 상태 전이, checkpoint log
  기록, 커밋 해시와 정확한 검증 수치(pytest junit, smoke)가 담긴 handoff
  record(인계 기록) 작성. QP-KER-020, QP-DRIFT-002 같은 태스크 ID를
  claim해라/시작해라/리뷰로 옮겨라/integrated로 바꿔라/blocked 처리해라는
  요청, "작업보드 갱신", "workboard에 기록", "핸드오프 기록 남겨줘", "인계
  준비해줘", "체크포인트 남겨줘", "lease 잡고 상태 바꿔" 등 기존 미션
  작업보드의 상태·기록을 변경하는 모든 작업에 반드시 이 스킬을 사용한다.
  구현이 끝나서 작업보드에 증거와 함께 review 전환하는 경우도 포함한다.
  Use for ANY status transition, claim, checkpoint, or handoff on an EXISTING
  mission workboard (task IDs like QP-XXX-NNN). Not for creating a brand-new
  mission workboard (that is /start-collaboration) and not for writing a
  Codex implementation task spec (that is the codex handoff skill).
triggers:
  - "claim"
  - "작업보드"
  - "workboard"
  - "핸드오프"
  - "리뷰로 옮겨"
  - "QP-"
---

# Workboard Flow

## Why this exists

QuantPilot의 Codex–Claude 협업은 미션별 작업보드(`docs/*_workboard.md`)의
상태 전이와 증거 기록으로 굴러간다. 전이 규칙이나 handoff 형식을 어기면
상대 에이전트가 검증할 수 없는 인계가 생기고, lease를 무시하면 문서 충돌이
난다. 규칙 원문: `docs/agent_collaboration_protocol.md`,
템플릿: `docs/agent_workboard_template.md`.

## Step 0 — 활성 작업보드 찾기

- 사용자가 미션/작업 ID를 말하면 `docs/`에서 해당 `*_workboard.md`를 grep한다.
- 불명확하면 `docs/STATUS.md`의 "다음 단계 후보"와 최근 git log에서 활성 미션을
  추정하고, 추정했음을 보고에 명시한다.
- `docs/professional_operator_workboard.md`는 **역사 기록**이다. 새 상태를
  절대 추가하지 않는다.

## Step 1 — Document edit lease

작업보드를 수정하기 전에:

1. Lease 섹션을 읽는다. `free`가 아니면 수정하지 말고 보고한다.
2. Lease를 잡는다 (editor=자기 agent/model, task ID, acquired at).
3. **문서를 다시 읽고** 최소 변경만 적용한다 (lease 획득 사이에 상대가 바꿨을 수 있다).
4. 변경 직후 lease를 `free`로 해제한다. lease를 잡은 채 오래 작업하지 않는다.

## Step 2 — Claim과 상태 전이

상태: `proposed → ready → in_progress → review → integrated → done` (+`blocked`).

- 정확히 **하나의 `ready` 작업만** claim해서 `in_progress`로 바꾼다.
- 작업은 `claude/<mission-id>-<task-id>` branch의 분리 worktree에서 하고,
  Work queue에 기록된 **소유 경로만** 수정한다.
- 다른 branch, mainline, 사용자 dirty 경로는 수정·정리·stash·reset·commit하지 않는다.
- 각 전이에는 증거가 필요하다:
  - `in_progress → review`: 커밋 hash + 정확한 검증 출력 (아래 형식)
  - `review → integrated`: 상대(또는 별도 검토자)의 리뷰 결과. **안전 중요 변경은
    자기 승인 금지.**
  - `integrated → done`: mainline 통합 커밋과 프로젝트 전체 검증. 통합은
    **미션 리드만** 수행한다.

## Step 3 — Checkpoint log

전이·인계·블로커마다 한 줄 추가:

```text
2026-07-15 — claude claude-fable-5 — QP-KER-015 review 전환: commit 949aa7d, pytest 1046 passed/2 skipped (junit), smoke OK
```

## Step 4 — Handoff record

`review` 전환 시 작업보드의 Handoff record 블록을 채운다:

```text
task_id: QP-KER-015
agent_and_model: claude claude-fable-5
commit: <hash>
owned_paths: <기록된 소유 경로 그대로>
acceptance_met: <acceptance 기준 대비 충족 여부>
exact_checks: pytest N passed/M skipped (junit), run_smoke <결과>, vitest/build <해당 시>
known_limits: <비차단 한계 — 라이브 준비 주장 금지>
integration_requests: <mainline 통합 요청 또는 none>
```

## 검증 출력 형식

`exact_checks`의 수치는 어림잡지 말고 junit XML에서 읽는다. 이 저장소의
Windows 환경에서는 공유 temp root가 간헐적으로 잠기고 `-q` 요약줄이 파이프에
안 잡히므로 세션 전용 basetemp + junitxml을 쓴다:

```powershell
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp="$env:CLAUDE_JOB_DIR\tmp\pt" --junitxml="$env:CLAUDE_JOB_DIR\tmp\junit.xml"
```

junit testsuite 속성의 `tests`/`failures`/`skipped`로 수치를 보고한다.
smoke는 `python -m quantpilot.jobs.run_smoke` — 기대 상태는
`broker mock / live=false / operator blocked`.

## Failure rule

같은 원인의 실패가 **두 번** 반복되면 추가 수정을 멈추고 작업보드
Blockers 섹션에 기록한 뒤 Codex의 읽기 전용 진단을 요청한다.
