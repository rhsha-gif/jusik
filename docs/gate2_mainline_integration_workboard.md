# Gate 2 Mainline Integration Workboard

## Document edit lease

- Lease status: `held`
- Document editor: `Codex GPT-5`
- Mission/task ID: `QP-GATE2-MAINLINE-INTEGRATION`
- Acquired at: `2026-07-12 KST`

한 번에 한 에이전트만 이 문서를 수정한다. 최종 검증과 상대 감사까지 끝나면 lease를 즉시 해제한다.

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-GATE2-MAINLINE-INTEGRATION` |
| Received by | Codex |
| Mission lead | Codex |
| Lead model/version | GPT-5 Codex (desktop; exact point release not exposed) |
| Goal | 현재 main `b7cd4b1`의 QP-DRIFT-DAILY 안전 강화와, 수용된 Gate 1/2 계보 `8eaf15a`의 atomic risk reservation + canonical execution-event shadow journal을 하나의 검증된 mainline 후보로 통합한다. |
| In scope | 두 계보의 merge, 충돌 파일 `.env.example`/`docs/STATUS.md`의 보수적 합집합 해결, 기존 Gate 문서·schema v10/v11·테스트 보존, 전체 검증, 독립 Codex 감사, Claude Code 최종 감사, mainline 통합 기록. |
| Out of scope | 새 runtime 기능, Kernel v2 구현, event source-of-truth cutover, 실제 KIS 호출, live/market 주문 활성화, ledger/flatten/Postgres/Kafka, 사용자 백업 파일. |
| Safety constraints | `LIVE_TRADING_ENABLED=false`, `GUARDED_AUTOPILOT_ENABLED=false`, `FULLY_AUTOMATED_OPERATOR_ENABLED=false`, `MARKET_ORDERS_ENABLED=false`, `BROKER_MODE=mock`; fake/offline 검증만 허용; ambiguous POST no-retry, 단일 broker POST authority, reservation/event same-transaction 불변조건 유지. |
| Completion criteria | merge 부모가 정확히 `9769d98`와 `8eaf15a`이고 `9769d98^ = 1c24985`, `1c24985^ = b7cd4b1`; 충돌 해결이 양쪽 의미를 보존; backend 전체·smoke·OpenAPI/frontend checks 통과; P0/P1=0; Claude Code가 최종 통합을 권고; 사용자 소유 `CLAUDE.md.20260705.bak` 미접촉. |

## Counterpart plan review

- Reviewer/model: `Claude Code / claude-fable-5` 시도 → session-limit 429로 산출물 없이 종료; 독립 Codex fallback reviewer가 초기 분해 검토 완료
- Review status: `changes_accepted` — P0=0, P1=1(작업보드 부모 기준 오류), P2=1(`.env.example` 예시 경로의 literal tab). P1은 병합 전에 이 커밋에서 수정했고 P2는 충돌 해결에 포함한다. Claude 최종 감사 의무는 유지한다.
- Decomposition findings: 사전 `merge-tree`에서 실제 내용 충돌은 `.env.example`과 `docs/STATUS.md` 두 파일로 한정됐다. Gate 2 브랜치는 Gate 1을 조상으로 포함하므로 Gate 1을 별도 병합하지 않는다. 자동 병합되는 `run_kis_paper_session.py`는 kill fence와 Drift data-mode 결속을, `harness_service.py`는 durable reservation 집계와 Drift 성과 증거 결속을 각각 보존해야 한다. Kernel v2 계약/runtime은 이 통합이 끝날 때까지 시작하지 않는다.
- Required substantive counterpart role: 병합 결과 전체 diff, 충돌 해결, schema-v11/event parity, Drift 증거 결속, 안전 기본값을 독립 감사하고 P0/P1 판정과 main 통합 권고를 커밋 가능한 문서 산출물로 남긴다.

## Routing assessment

점수는 `domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`이다.

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Gate 2 계보 병합·충돌 해결 | Codex GPT-5 | 5 | 5 | 5 | 5 | 4 | 4.90 | stateful SQLite/broker integration 선호 클래스 n=5, 현재 두 계보와 충돌 사전조사 연속성 보유. |
| Gate 2 계보 병합·충돌 해결 | Claude Fable 5 | 4 | 4 | 3 | 3 | 4 | 3.65 | 계약/감사 강점은 있으나 이 mainline stateful 병합 클래스의 비교 표본은 중립. |
| 최종 독립 감사 | Claude Fable 5 | 5 | 5 | 5 | 4 | 5 | 4.90 | Gate 1/2 계약과 Drift 최종 검토를 이미 수행했고 구현자가 아닌 독립 검토자다. |
| 최종 독립 감사 | Codex GPT-5 lead | 4 | 5 | 4 | 5 | 2 | 4.05 | 병합 구현자이므로 자기승인 불가; 내부 병렬 감사만 보조 증거로 사용. |

## Work queue

상태: `proposed`, `ready`, `in_progress`, `review`, `integrated`, `done`, `blocked`.

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-G2I-000` | Codex GPT-5 lead | Claude Fable 5 시도 → independent Codex fallback | none | `주식트레이더-gate2-mainline-integration` / `codex/qp-gate2-mainline-integration` | 이 workboard, merge preflight | done | 범위·부모·충돌·라우팅·검증 게이트 확정 | `1c24985`, fallback CHANGES 반영 `9769d98` |
| `QP-G2I-010` | Codex GPT-5 lead | independent read-only agents | `QP-G2I-000` | same | merge commit, `.env.example`, `docs/STATUS.md` | review | 양쪽 계보 보수적 합집합, runtime 수동 재작성 없음, 안전 기본값 불변 | merge parents `9769d98` + `8eaf15a`; final SHA assigned on commit |
| `QP-G2I-020` | Codex GPT-5 lead | independent read-only agents | `QP-G2I-010` | same | tests/verification + cross-feature regression assertions | done | 전체 backend, smoke, kill-disabled, OpenAPI, frontend, diff checks 통과 | targeted `234/103/84`, cross `44`, full `1046 passed, 2 skipped`; safe smoke/kill; OpenAPI 51; frontend 23 + build |
| `QP-G2I-030` | Claude Fable 5 | Codex mission lead | `QP-G2I-020` | 별도 Claude review worktree/branch | 최종 감사 보고서 + workboard 감사 필드만 | pending | 전체 merge diff P0/P1=0, main 통합 권고 | pending |
| `QP-G2I-040` | Codex mission lead | Claude Fable 5 | `QP-G2I-030` | main | mainline integration + 종결 기록 | pending | 검증된 후보만 main에 통합, 사용자 변경 보존 | pending |

## Integration requests

- `8eaf15a -> QP-G2I-010`: Gate 1/2의 schema-v10/v11, atomic reservation, typed mutation origins, replay/parity, no-rePOST 의미를 그대로 보존한다.
- `b7cd4b1 -> QP-G2I-010`: QP-DRIFT-DAILY의 equity/snapshot/fill/market/calendar 증거 fingerprint와 fail-closed activation 차단을 보존한다.
- `QP-G2I-040 -> QP-KERNEL-V2`: Kernel 계약/구현은 통합된 main을 새 기준점으로 사용하며 두 계보 중 어느 것도 우회하지 않는다.

## Blockers and authority requests

- 실제 KIS paper TR 검증은 명시적 사용자 자격증명·수동 권한이 필요한 Gate P이며 이 fake-only 통합을 차단하지 않는다.
- main의 `CLAUDE.md.20260705.bak`는 사용자 소유 untracked 파일이다. 이 격리 worktree로 복사하거나 어떤 커밋에도 포함하지 않는다.

## Checkpoint log

- `2026-07-12 KST` — Codex lead — QP-DRIFT-DAILY를 main `b7cd4b1`에 통합하고 Claude/Codex 양쪽 검증 완료: backend `828 passed, 2 skipped`, smoke mock/live=false/operator blocked, frontend `23 passed` + build, OpenAPI 51 paths byte-exact.
- `2026-07-12 KST` — Codex lead — accepted Gate 2 head `8eaf15a`가 accepted Gate 1 `0a9b644`를 포함함을 확인. 별도 Gate 1 병합은 생략하며, 사전 merge-tree 실제 충돌은 `.env.example`/`docs/STATUS.md` 두 파일.
- `2026-07-12 KST` — Codex lead — main `b7cd4b1`에서 격리 worktree/branch 생성, 사용자 백업 제외 확인, workboard lease 획득.
- `2026-07-12 20:10 KST` — Claude Code `claude-fable-5` — 초기 계획 검토 중 session-limit 429 (reset 00:30 KST)로 종료; 파일 변경·커밋 0건. 프로토콜 availability fallback 적용, Claude 최종 감사 슬롯은 유지.
- `2026-07-12 KST` — independent Codex fallback reviewer — initial plan verdict CHANGES, P0=0/P1=1/P2=1. 이미 workboard 커밋 `1c24985`가 있으므로 정확한 merge 부모 조건을 `1c24985 + 8eaf15a` 및 `1c24985^ = b7cd4b1`로 수정. `.env.example`의 `C:\path<TAB>o\...` P2는 충돌 해결에서 `C:\path\to\...`로 교정. 이 수정 후 후보 병합 승인; main 통합은 Claude 최종 감사 대기.
- `2026-07-12 KST` — Codex lead — fallback 수정 기록 커밋 `9769d98` 뒤 `8eaf15a` 병합을 시작. 최종 merge 부모는 `9769d98 + 8eaf15a`; 기존 main 기준점은 첫 부모 조상 `b7cd4b1`로 보존한다.
- `2026-07-12 KST` — Codex lead — 충돌 해결: `.env.example`은 Drift의 operator kill/CORS/KRX calendar/paper DB와 Gate 2의 `KIS_PAPER_KILL_ENABLED=false`/confirmation을 합집합으로 보존하고 기존 예시 경로의 literal tab을 교정. `docs/STATUS.md`는 Gate 1/2, Drift, schema-v10 authoritative/schema-v11 shadow, Gate P pending, 다음 Kernel v2를 함께 기록. 자동 병합 runtime은 수동 재작성하지 않음.
- `2026-07-12 KST` — independent semantic/test reviewers — runtime P0=0/P1=0; cross-feature P2 하나를 발견. equity/snapshot 증거가 durable prepare → canonical replay → 실제 DB reopen/hydrate → reconciled fill → performance feed까지 보존되고 broker POST가 1회뿐임을 기존 external-paper 통합 테스트에 추가했다. held sell reservation과 dispatch가 중복 합산되지 않는 기존 단언도 명문화. 관련 `44 passed`.
- `2026-07-12 KST` — Codex lead — 후보 검증 완료: state/migration/no-rePOST/kill `234 passed`; canonical event/reducer/store/parity `103 passed`; Drift/env `84 passed`; 전체 backend `1046 passed, 2 skipped`; smoke mock/live=false/operator blocked/submitted IDs empty; kill CLI `paper_kill_disabled`; OpenAPI 51 paths byte-exact + d.ts sync; frontend `23 passed (7 files)` + build. `git diff --check`/부모/allowlist 최종 확인은 merge commit 직전 수행.

## Handoff record

```text
task_id: QP-GATE2-MAINLINE-INTEGRATION
agent_and_model: Codex GPT-5 lead + Claude Code claude-fable-5 final auditor
commit: pending merge SHA (parents 9769d98 and 8eaf15a)
owned_paths: merge integration, .env.example, docs/STATUS.md, this workboard
acceptance_met: candidate checks passed; Claude final audit and main integration pending
exact_checks: targeted 234/103/84; cross-feature 44; full 1046 passed, 2 skipped;
  smoke mock/live=false/operator blocked; kill paper_kill_disabled; OpenAPI 51
  byte-exact + d.ts sync; frontend 23 passed + build
known_limits: real KIS Gate P and all post-Gate-2 roadmap gates remain pending
integration_requests: integrate only after Claude P0/P1=0 recommendation
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-G2I-010` | stateful release integration | Codex GPT-5 | pending | 0 | 0 | 0 | 0 | pending | pending | 0 |
| `QP-G2I-030` | independent merge audit | Claude Fable 5 | pending | 0 | 0 | 0 | 0 | pending | pending | 0 |

- Routing decision quality: pending
- Capability scorecard update: pending
- User-owned changes preserved: main backup remains untracked and untouched; integration worktree starts clean.
- Remaining limitations: Gate P manual KIS evidence, Kernel v2, ledger/runtime, and live-candidate gates remain pending.
