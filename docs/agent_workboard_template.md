# Best-Fit Agent Mission Workboard Template

> 새 미션마다 이 파일을 복사하고 `<...>` 값을 확정한다. 이 템플릿 자체에는 진행 상태를 기록하지 않는다.

## Document edit lease

- Lease status: `free`
- Document editor: `none`
- Mission/task ID: `none`
- Acquired at: `none`

한 번에 한 에이전트만 이 문서를 수정한다. 편집자는 lease를 잡고 문서를 다시 읽은 뒤 최소 변경만 적용하고
즉시 해제한다.

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `<mission-id>` |
| Received by | `<codex or claude>` |
| Mission lead | `<same as received-by>` |
| Lead model/version | `<exact model>` |
| Goal | `<observable outcome>` |
| In scope | `<bounded scope>` |
| Out of scope | `<explicit exclusions>` |
| Safety constraints | `<project rules and authority limits>` |
| Completion criteria | `<commands and observable results>` |

## Counterpart plan review

- Reviewer/model: `<other agent and exact model>`
- Review status: `pending`
- Decomposition findings: `<dependencies, missing acceptance, path conflicts>`
- Required substantive counterpart role: `<implementation, binding design/research, or blocking audit>`

## Routing assessment

점수는 1~5이며 총점은 `domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`이다.
비교 가능한 실적이 없으면 `track=3`으로 기록하고, 안전한 bounded seed 작업 적용 여부를 근거에 명시한다.

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `<task-id>` | Codex `<model>` | 0 | 0 | 0 | 0 | 0 | 0.00 | `<evidence>` |
| `<task-id>` | Claude `<model>` | 0 | 0 | 0 | 0 | 0 | 0.00 | `<evidence>` |

## Work queue

상태: `proposed`, `ready`, `in_progress`, `review`, `integrated`, `done`, `blocked`.

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `<task-id>` | `<agent/model>` | `<agent/model>` | `<ids or none>` | `<absolute path>` / `<branch>` | `<disjoint paths>` | proposed | `<testable criteria>` | pending |

## Integration requests

- `<requesting task -> owning task: required cross-boundary interface or decision>`

## Blockers and authority requests

- `<failing command, repeated root cause count, required user/external authority>`

## Checkpoint log

- `<timestamp> — <agent/model> — <claim, handoff, review, integration, or blocker with exact evidence>`

## Handoff record

```text
task_id:
agent_and_model:
commit:
owned_paths:
acceptance_met:
exact_checks:
known_limits:
integration_requests:
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `<task-id>` | `<class>` | `<agent/model>` | `<yes/no>` | 0 | 0 | 0 | 0 | `<results>` | `<duration>` | 0 |

- Routing decision quality: `<correct, revise, or insufficient evidence>`
- Capability scorecard update: `<record added; preference unchanged/changed and why>`
- User-owned changes preserved: `<exact status evidence>`
- Remaining limitations: `<explicit follow-ups>`
