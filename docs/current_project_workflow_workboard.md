# QuantPilot Current Workflow Documentation Workboard

## Document edit lease

- Lease status: `free`
- Document editor: `none`
- Mission/task ID: `QP-WORKFLOW-DOC`
- Acquired at: `2026-07-11 05:40 KST`

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-WORKFLOW-DOC` |
| Received by | Codex |
| Mission lead | Codex |
| Lead model/version | GPT-5.4 Codex |
| Goal | 현재 저장소에서 실제로 동작하는 QuantPilot의 워크플로우, 실행 경로, 데이터 흐름, 안전 게이트, 상태 저장, API/UI/배치 운영 방법과 확장 경계를 근거 링크와 함께 상세 Markdown 문서로 남긴다. |
| In scope | 코드·테스트·운영 문서 교차 확인, 현재 동작 설명, 단계별 흐름, 실패/차단 동작, 실행 명령, 확장 시 보존해야 할 계약, 알려진 간극, 사용자가 추가 요청한 단순 작업 fast path의 협업 프로토콜 명문화 |
| Out of scope | 애플리케이션 동작 변경, 거래 기능 활성화, 기존 사용자 수정 반영, 배포, 실브로커 호출 |
| Safety constraints | 모든 live/guarded/full automation/market order 플래그 기본 비활성, `BROKER_MODE=mock`, 비밀 미열람·미기록, fixture 결정성, 주문 안전 계약 우회 금지 |
| Completion criteria | 상세 문서가 실제 파일·심볼·명령을 인용하고, 상대 에이전트의 분해 검토 및 실질 산출물을 포함하며, 링크/경로 검사와 `git diff --check`를 통과한다. |

## Counterpart plan review

- Reviewer/model: `Claude Code 시도 후 session limit로 차단; Codex/GPT-5 sub-agent(point release 미노출)로 대체`
- Review status: `completed and integrated`
- Decomposition findings: 범위·소유경로·격리는 타당했다. 독립 검증축으로 API in-memory와 KIS paper SQLite persistence 구분, reconciliation/idempotency, 전략 lifecycle, 문서-코드 간극을 명시해야 한다고 지적했고 최종 문서에 반영했다.
- Required substantive counterpart role: 저장소 코드 경로를 독립 조사해 초안 문서를 작성하고 커밋한다. Codex 리드는 해당 커밋을 검토·통합하고 누락과 부정확성을 보완한다.

## Routing assessment

점수식: `domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`.

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `QP-WD-01` 조사·초안 | Codex GPT-5.4 | 4 | 5 | 4 | 5 | 4 | 4.35 | 현재 세션과 저장소 상태를 보유하고 통합·검증에 강점 |
| `QP-WD-01` 조사·초안 | Claude Code | 5 | 4 | 3 | 3 | 4 | 4.00 | 구조 종합과 장문 계약 문서 초안에 적합; 비교 실적은 중립값 |
| `QP-WD-02` 통합·검증 | Codex GPT-5.4 | 5 | 5 | 5 | 5 | 4 | 4.90 | 미션 리드이며 실행 기반 저장소 검증과 통합 책임 보유 |
| `QP-WD-02` 통합·검증 | Claude Code | 4 | 4 | 4 | 3 | 4 | 3.90 | 독립 검토에는 적합하나 mainline 통합은 리드 책임 |

## Work queue

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-WD-00` | Codex GPT-5.4 | fallback reviewer | none | `C:\\Users\\goyan\\OneDrive\\문서\\코덱스\\주식트레이더-workflow-doc` / `codex/qp-workflow-doc-core` | `docs/current_project_workflow_workboard.md` | done | 미션·경계·라우팅·검증 계획 확정 | `0434aa4`, `54f0911` |
| `QP-WD-01` | Codex/GPT-5 sub-agent(point release 미노출) | Codex GPT-5.4 | `QP-WD-00` | `C:\\Users\\goyan\\OneDrive\\문서\\코덱스\\주식트레이더-workflow-claude` / `claude/qp-workflow-doc-draft` | `docs/current_project_workflow.md` | integrated | 실제 코드와 테스트에 근거한 상세 초안 및 커밋 | source `ab816c2`, integrated `e879a67` |
| `QP-WD-02` | Codex GPT-5.4 | Codex/GPT-5 sub-agent | `QP-WD-01` | `C:\\Users\\goyan\\OneDrive\\문서\\코덱스\\주식트레이더-workflow-doc` / `codex/qp-workflow-doc-core` | 통합된 `docs/current_project_workflow.md`, 작업보드, capability evidence | done | 교차 검토 반영, 링크/경로/Markdown/diff 검사 통과 | backend `785 passed, 2 skipped`; smoke/frontend/build pass; 48 links resolve |

## Integration requests

- `QP-WD-01 -> QP-WD-02`: 초안 커밋 해시, 조사한 핵심 진입점, 불확실하거나 문서와 코드가 다른 지점을 handoff에 기록한다.

## Blockers and authority requests

- 메인 작업공간은 사용자 변경으로 dirty 상태다. 격리 worktree만 사용하며 해당 변경을 복사·stage·commit하지 않는다.
- Claude Code 호출은 `429 session limit`(09:40 KST reset)로 시작 전에 차단되었다. 대기하지 않고 독립 Codex 검토자로 대체하며 Claude 검토 미수행을 최종 제한으로 남긴다.

## Checkpoint log

- `2026-07-11 05:40 KST` — Codex/GPT-5.4 — 미션 리드 수임, safety invariants 확정, 기준 커밋 `ffbdc20617e248a833ab313fa28f7ec3de172fd1`에서 격리 worktree 생성.
- `2026-07-11 05:46 KST` — Claude Code 2.1.205 / Opus 요청 — API 응답 `429 session limit`, 산출물 없이 차단.
- `2026-07-11 05:48 KST` — Codex/GPT-5.4 — 사용자 지시에 따라 `agent_collaboration_protocol.md`에 단순 작업 fast path를 추가하고, 현재 비단순 문서화 미션에는 독립 Codex 검토자를 대체 배정.
- `2026-07-11 05:53 KST` — Codex/GPT-5 sub-agent — 465-line workflow 초안 커밋 `ab816c2`; 링크 검사, `git show --check`, smoke 통과.
- `2026-07-11 05:55 KST` — Codex/GPT-5.4 — 초안을 `e879a67`로 통합하고 개발 협업 흐름, 실제 검증 스냅샷, dirty working tree 기준 경계를 보완. backend `785 passed, 2 skipped`, smoke pass, frontend `23 passed`와 build pass, Markdown link 48개 resolve 확인.

## Handoff record

```text
task_id: QP-WD-01
agent_and_model: Codex / GPT-5 sub-agent (point release 미노출)
commit: ab816c2cd0ad20d384272b0bf685307d48afc2a6
owned_paths: docs/current_project_workflow.md
acceptance_met: yes; independent repository-wide workflow draft and source map
exact_checks: local Markdown links resolve; git show --check; smoke mock/live=false/operator blocked
known_limits: Claude 429 fallback; KIS real server/manual integration not run
integration_requests: persistence/reconciliation, lifecycle, docs-code gaps retained in final
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-WD-01` | documentation research | GPT-5 sub-agent | yes | 0 | 0 | 0 | 0 | links/diff/smoke pass | not recorded | 5 |
| `QP-WD-02` | integration and verification | GPT-5.4 Codex | yes | 0 | 0 | 0 | 0 | backend/smoke/frontend/build/links pass | ~20m | 5 |

- Routing decision quality: Claude가 session limit로 차단된 뒤 bounded Codex fallback을 사용한 결정은 산출물과 검증 관점에서 적절했다. Claude 대비 품질 비교 증거는 얻지 못했다.
- Capability scorecard update: `QP-WORKFLOW-DOC` evidence row 추가.
- User-owned changes preserved: 메인 working tree의 기존 modified/untracked 파일은 수정·stage·commit하지 않았고 별도 worktree에서 작업했다.
- Remaining limitations: KIS 실서버와 manual integration 미실행; Claude 교차 검토 미수행; 미커밋 router/CORS 변경은 안정 기준에서 제외.
