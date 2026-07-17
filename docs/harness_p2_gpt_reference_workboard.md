# Harness and P2 GPT Source Reference Workboard

## Document edit lease

- Lease status: `held`
- Document editor: `Codex / GPT-5.6`
- Mission/task ID: `HP2REF-001/LEAD`
- Acquired at: `2026-07-17 Asia/Seoul`

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `HP2REF-001` |
| Received by | `Codex` |
| Mission lead | `Codex` |
| Lead model/version | `GPT-5.6 / Codex CLI` |
| Goal | 현재 QuantPilot 하네스와 P2 구성요소를 GPT가 원본 소스와 함께 정확히 참고할 수 있는 단일 Markdown 문서를 제공한다. |
| In scope | 하네스 진입점, 실행 흐름, 안전 불변식, P2 구성요소와 상태, 핵심 소스·테스트·계약 경로, GPT 전달 방법 |
| Out of scope | 코드 동작 변경, 거래 권한 변경, 실거래·paper 주문 실행, 비밀·계좌 정보, 미완료 기능을 완료로 선언하는 일 |
| Safety constraints | 모든 거래 플래그는 비활성·mock 기본값을 유지한다. 외부 connector나 broker를 호출하지 않는다. 기존 사용자 변경을 복사·수정·stage·commit하지 않는다. |
| Completion criteria | 최종 문서 생성, 상대 에이전트의 독립 소스 감사 완료, 문서의 모든 저장소 경로 존재 확인, 안전 플래그·data mode·P2 상태를 코드/계약과 교차 검증, Markdown 구조 수동 확인 |

## Counterpart plan review

- Reviewer/model: `Claude Code / claude-fable-5`
- Review status: `accepted with four binding constraints`
- Decomposition findings: 모든 주장을 main `4241dc4` 또는 미통합 branch `31ac3a1`에 명시적으로 결속하고, P2 심각도·Roadmap Gate 2·Gate P를 구분하며, 경로 존재를 snapshot별로 확인하고, dirty main 작업트리를 출처로 사용하지 않는다.
- Required substantive counterpart role: 현재 코드에서 하네스/P2 경계를 독립 조사하고, 최종 문서를 구속하는 소스 인벤토리·누락 감사 문서를 별도 경로에 작성·커밋한다.

## Routing assessment

점수는 `domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`으로 계산했다.

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `DOC` | Codex `GPT-5.6` | 5 | 5 | 3 | 5 | 5 | 4.50 | 최초 수신자이며 로컬 탐색·통합·경로 검증에 가장 높은 연속성이 있다. |
| `DOC` | Claude Code `claude-fable-5` | 5 | 4 | 3 | 2 | 3 | 3.80 | 장문 기술 설명 적합도는 높지만 현재 저장소 문맥과 통합 비용에서 열세다. |
| `AUDIT` | Codex `GPT-5.6` | 4 | 5 | 3 | 5 | 4 | 4.10 | 독립성 없이 자기 문서를 감사하게 되는 한계가 있다. |
| `AUDIT` | Claude Code `claude-fable-5` | 5 | 4 | 3 | 3 | 5 | 4.20 | 경로가 격리된 독립 감사로 누락과 과장된 상태 주장을 차단할 수 있다. |

## Work queue

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `AUDIT` | Claude Code / claude-fable-5 | Codex / GPT-5.6 | none | `C:/Users/goyan/OneDrive/문서/코덱스/주식트레이더/.claude/worktrees/harness-p2-gpt-reference-review` / `claude/harness-p2-gpt-reference-review` | `docs/claude/harness_p2_source_inventory_review.md` | integrated | 코드·계약·테스트 근거로 하네스와 P2 범위, 상태, 누락 위험을 표로 제공하고 직접 커밋한다. | Claude `b022d25`; lead scope/diff review passed; cherry-pick `48a1c91` |
| `DOC` | Codex / GPT-5.6 | Claude Code / claude-fable-5 | `AUDIT` | `C:/Users/goyan/OneDrive/문서/코덱스/주식트레이더/.codex/worktrees/harness-p2-gpt-reference-doc` / `codex/harness-p2-gpt-reference-doc` | `docs/current_harness_and_p2_gpt_source_reference.md`, this workboard | review | GPT가 첨부할 소스 묶음과 읽기 순서, 실행 흐름, 안전 경계, P2 상태를 단일 문서에서 이해한다. 모든 경로가 snapshot별로 존재한다. | 358-line draft; `git diff --check` passed; 52 main/branch manifest objects exist; final counterpart review pending |

## Integration requests

- `AUDIT -> DOC`: 하네스/P2의 확정 범위, 상태 판정 근거, 반드시 포함할 소스 경로와 과장 방지 주의점을 전달한다.

## Blockers and authority requests

- none

## Checkpoint log

- `2026-07-17` — Codex / GPT-5.6 — mainline `4241dc46e11454b8bd4c915e4ae52ca32570e9ef`에서 두 개의 격리 worktree와 전용 branch를 생성했다.
- `2026-07-17` — Claude Code / claude-fable-5 — 소스 인벤토리·누락 감사 `b022d25` 완료. main/미통합 branch/dirty tree 분리, P2 용어 충돌, 열린 P2 6개, GPT 첨부 순서를 구속 조건으로 확정했다.
- `2026-07-17` — Codex / GPT-5.6 — `b022d25`가 단일 소유 파일만 변경하고 `git diff --check`를 통과함을 검토한 뒤 `48a1c91`로 통합했다.
- `2026-07-17` — Codex / GPT-5.6 — 358줄 GPT 참고 문서를 작성했다. main `4241dc4`의 51개 manifest object와 branch `31ac3a1`의 신규 version test를 Git object database에서 확인했고, Kernel runtime import 0개와 KIS POST 단일 호출부를 재확인했다.

## Handoff record

```text
task_id: pending
agent_and_model: pending
commit: pending
owned_paths: pending
acceptance_met: pending
exact_checks: pending
known_limits: pending
integration_requests: pending
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `AUDIT` | source-contract audit | pending | pending | 0 | 0 | 0 | 0 | pending | pending | 0 |
| `DOC` | technical documentation | Codex / GPT-5.6 | pending | 0 | 0 | 0 | 0 | pending | pending | 0 |

- Routing decision quality: `pending`
- Capability scorecard update: `pending`
- User-owned changes preserved: `pending`
- Remaining limitations: `pending`
