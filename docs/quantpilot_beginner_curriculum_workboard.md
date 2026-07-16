# QuantPilot Beginner Curriculum Mission Workboard

## Document edit lease

- Lease status: `free`
- Document editor: `none`
- Mission/task ID: `QP-LRN-20260716`
- Acquired at: `none`

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-LRN-20260716` |
| Received by | Codex |
| Mission lead | Codex |
| Lead model/version | GPT-5.6 Codex |
| Goal | 최근 QuantPilot 작업의 실제 문제와 재작업에서 필요한 개발 지식을 도출하고, 개발 경험이 전혀 없는 사용자가 실행할 수 있는 맞춤형 한국어 커리큘럼을 Markdown 파일로 제공한다. |
| In scope | 2026-06-16~2026-07-16 Git·작업보드·회고·Codex 세션 증거, 초보자 학습 순서, 실습·완료 기준·프로젝트 안전 수칙 |
| Out of scope | 코드·거래 로직 변경, 실거래 또는 외부 API 실행, 일반 컴퓨터과학 전공 과정 전체, 투자 성과 보장 |
| Safety constraints | 모든 거래 기능 기본 비활성, `BROKER_MODE=mock`, 비밀·계좌 정보 금지, fixture-first 오프라인 실습, 사용자 dirty 파일 보존 |
| Completion criteria | 커리큘럼과 근거 문서가 존재하고, 내부 링크·필수 섹션·Markdown 형식·변경 경로 검증을 통과하며, 상대 에이전트 분석과 검토가 반영된다. |

## Counterpart plan review

- Reviewer/model: Claude Code 2.1.210 / resolved model pending
- Review status: `pending`
- Decomposition findings: `pending`
- Required substantive counterpart role: 최근 결함·재작업 증거를 초보자 학습 요구사항으로 변환한 독립 연구 문서를 작성하고 커밋한다.

## Routing assessment

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `QP-LRN-01` 독립 증거 분석 | Codex GPT-5.6 | 4 | 5 | 4 | 5 | 4 | 4.35 | 저장소 탐색과 검증 실적은 강하나 본문 작성 경로와 분리된 독립 시각이 필요하다. |
| `QP-LRN-01` 독립 증거 분석 | Claude Code / resolved model pending | 5 | 4 | 5 | 3 | 5 | 4.55 | 장문 연구·독립 감사 실적과 문제 패턴 종합 적합도가 높아 소유한다. |
| `QP-LRN-02` 커리큘럼 작성·통합 | Codex GPT-5.6 | 4 | 5 | 5 | 5 | 5 | 4.70 | 저장소 전체 workflow 문서화와 실행 검증에서 평점 5의 직접 실적이 있다. |
| `QP-LRN-02` 커리큘럼 작성·통합 | Claude Code / resolved model pending | 4 | 4 | 3 | 3 | 4 | 3.65 | 문서 종합에는 적합하지만 현재 미션 문맥과 통합 비용에서 Codex가 우위다. |

## Work queue

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-LRN-00` 미션 보드 | Codex GPT-5.6 | Claude Code | none | `C:/qp-learning-curriculum-core` / `codex/qp-learning-curriculum-core` | `docs/quantpilot_beginner_curriculum_workboard.md` | in_progress | 목표·범위·작업 그래프·라우팅·검증이 확정됨 | 이 문서 |
| `QP-LRN-01` 학습 공백 증거 | Claude Code / resolved model pending | Codex GPT-5.6 | `QP-LRN-00` | `C:/qp-learning-curriculum-claude` / `claude/qp-learning-curriculum-evidence` | `docs/quantpilot_beginner_learning_gap_evidence.md` | proposed | 실제 커밋·작업보드에서 문제 패턴, 필요한 지식, 초보자 우선순위를 근거와 함께 제시하고 직접 커밋함 | pending |
| `QP-LRN-02` 커리큘럼 | Codex GPT-5.6 | Claude Code | `QP-LRN-01` | `C:/qp-learning-curriculum-core` / `codex/qp-learning-curriculum-core` | `docs/quantpilot_beginner_developer_curriculum.md` | proposed | 0지식 전제, 단계별 목표·실습·완료 기준·안전 경계·문제-학습 매핑·운영 방법을 포함함 | pending |
| `QP-LRN-03` 최종 검증·통합 | Codex GPT-5.6 | Claude Code | `QP-LRN-01`, `QP-LRN-02` | `C:/qp-learning-curriculum-core` / `codex/qp-learning-curriculum-core` | 위 세 문서 | proposed | 상대 커밋 검토, 링크·형식·경로 검증, 사용자 변경 보존 증거 기록 | pending |

## Integration requests

- `QP-LRN-02 -> QP-LRN-01`: 증거 문서의 문제 분류, 우선순위, 추천 학습 결과물을 커리큘럼에 반영한다.

## Blockers and authority requests

- 없음. 외부 시스템, 비밀, 거래 권한을 사용하지 않는다.

## Checkpoint log

- 2026-07-16 — Codex GPT-5.6 — 증거 범위를 2026-06-16~2026-07-16으로 확정. main은 사용자 변경(`paper_submission.py`, `.omo/`, 백업·handoff 파일)이 있어 `0494b57`에서 별도 worktree 두 개를 생성함.

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
| `QP-LRN-01` | research/audit | pending | pending | 0 | 0 | 0 | 0 | pending | pending | 0 |
| `QP-LRN-02` | documentation/integration | pending | pending | 0 | 0 | 0 | 0 | 0 | pending | pending | 0 |

- Routing decision quality: pending
- Capability scorecard update: 문서 미션의 소규모 증거이므로 완료 후 기록 여부 판단
- User-owned changes preserved: pending final `git status` comparison
- Remaining limitations: pending
