# Gate 2 Mainline Integration Workboard

## Document edit lease

- Lease status: `released`
- Document editor: `none` (마지막 편집: Codex GPT-5)
- Mission/task ID: `QP-GATE2-MAINLINE-INTEGRATION`
- Acquired at: `2026-07-12 KST` / Released at: `2026-07-12 KST`

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
| Completion criteria | merge 부모가 정확히 `9769d98`와 `8eaf15a`이고 `9769d98^ = 1c24985`, `1c24985^ = b7cd4b1`; 충돌 해결이 양쪽 의미를 보존; backend 전체·smoke·OpenAPI/frontend checks 통과; P0/P1=0; 현 Claude session-limit 시 기존 실질 산출물(`80e05d5`, Drift 최종 리뷰) + 세 독립 fallback 감사 P0/P1=0을 main 통합 게이트로 사용하고 post-reset Claude 감사는 Kernel runtime 전에 완료; 사용자 소유 `CLAUDE.md.20260705.bak` 미접촉. |

## Counterpart plan review

- Reviewer/model: Gate 2의 기존 Claude Code `claude-fable-5` 계약 리뷰 `80e05d5`와 Drift 최종 리뷰를 계승. 이번 integration 전용 Claude 시도는 session-limit 429로 산출물 없이 종료했고, 초기 분해 + 최종 runtime/test/Git 감사는 서로 다른 독립 Codex fallback reviewers가 수행했다.
- Review status: `accepted_with_availability_deviation` — 초기 P1(부모 기준)·P2(env tab)는 후보 전에 수정. 최종 세 감사 모두 P0=0/P1=0; 후보 `377a5d6` ACCEPT. post-reset Claude 감사는 main 통합 후 Kernel runtime 착수 전 필수다.
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
| `QP-G2I-010` | Codex GPT-5 lead | independent read-only agents | `QP-G2I-000` | same | merge commit, `.env.example`, `docs/STATUS.md` | done | 양쪽 계보 보수적 합집합, runtime 수동 재작성 없음, 안전 기본값 불변 | `377a5d6` (parents `9769d98` + `8eaf15a`) |
| `QP-G2I-020` | Codex GPT-5 lead | independent read-only agents | `QP-G2I-010` | same | tests/verification + cross-feature regression assertions | done | 전체 backend, smoke, kill-disabled, OpenAPI, frontend, diff checks 통과 | targeted `234/103/84`, cross `44`, full `1046 passed, 2 skipped`; safe smoke/kill; OpenAPI 51; frontend 23 + build |
| `QP-G2I-030` | Claude Fable 5 시도 → three independent Codex fallback auditors | Codex mission lead | `QP-G2I-020` | read-only candidate audit | 전체 runtime/test/docs/Git diff | done | 전체 merge diff P0/P1=0, main 통합 권고 | runtime ACCEPT P2=1; test/docs ACCEPT P2=2; Git safety READY P2=0 |
| `QP-G2I-035` | Claude Fable 5 | Codex mission lead | Claude reset | 기존 Claude review worktree | post-reset read-only audit artifact | ready | 통합 main의 전체 Gate 2 diff를 재감사해 P0/P1=0; Kernel runtime 시작 전 완료 | reset 00:30 KST 대기 |
| `QP-G2I-040` | Codex mission lead | fallback audit quorum | `QP-G2I-030` | main | mainline integration + 종결 기록 | done | 검증된 후보만 main에 통합, 사용자 변경 보존 | main fast-forward `2d34275`; post-main `1046 passed, 2 skipped`, safe smoke/kill, OpenAPI/frontend green |

## Integration requests

- `8eaf15a -> QP-G2I-010`: Gate 1/2의 schema-v10/v11, atomic reservation, typed mutation origins, replay/parity, no-rePOST 의미를 그대로 보존한다.
- `b7cd4b1 -> QP-G2I-010`: QP-DRIFT-DAILY의 equity/snapshot/fill/market/calendar 증거 fingerprint와 fail-closed activation 차단을 보존한다.
- `QP-G2I-035/040 -> QP-KERNEL-V2`: Kernel 계약은 통합 main을 새 기준점으로 검토할 수 있으나 runtime 구현은 post-reset Claude 감사 P0/P1=0 뒤 시작하며 두 계보 중 어느 것도 우회하지 않는다.

## Blockers and authority requests

- 실제 KIS paper TR 검증은 명시적 사용자 자격증명·수동 권한이 필요한 Gate P이며 이 fake-only 통합을 차단하지 않는다.
- main의 `CLAUDE.md.20260705.bak`는 사용자 소유 untracked 파일이다. 이 격리 worktree로 복사하거나 어떤 커밋에도 포함하지 않는다.
- Claude Code integration 전용 감사는 account session-limit(429, reset 00:30 KST)로 현재 불가하다. 기존 Claude 실질 Gate 2 계약/Drift 리뷰와 세 독립 fallback 감사로 main 통합을 진행하되, post-reset 감사가 Kernel runtime을 차단한다.

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
- `2026-07-12 KST` — candidate merge `377a5d6` — exact parents `9769d98`/`8eaf15a`; first-parent ancestry includes Drift main `b7cd4b1`, second-parent ancestry includes Gate 1 `0a9b644`; clean worktree, user backup absent.
- `2026-07-12 KST` — three independent final audits — runtime ACCEPT P0=0/P1=0/P2=1 (documented policy-scope conservative over-block only), test/docs ACCEPT P0=0/P1=0/P2=2 (SHA/command documentation fixed in this closeout), Git safety READY P0/P1/P2=0. Mainline integration recommended under recorded Claude availability deviation; `QP-G2I-035` remains mandatory before Kernel runtime.
- `2026-07-12 KST` — Codex mission lead — main에 `codex/qp-gate2-mainline-integration`을 fast-forward해 main=`2d34275`; 사용자 `CLAUDE.md.20260705.bak`는 유일한 untracked 파일로 미접촉. 조상 확인: Drift `b7cd4b1`, Gate 2 `8eaf15a`, merge candidate `377a5d6` 모두 main 조상.
- `2026-07-12 KST` — post-main verification — backend `1046 passed, 2 skipped`; frontend `23 passed (7 files)` + build; smoke broker=mock/live=false/operator blocked/submitted IDs empty; kill CLI `paper_kill_disabled`; OpenAPI 51 paths byte-exact + d.ts sync; `git diff --check` clean. 첫 병렬 검증 wrapper는 도구 timeout으로 결과를 회수하지 못했으나 잔존 프로세스 없이 종료됐고, 각 명령을 분리 재실행해 위 결과를 얻었다.
- `2026-07-12 KST` — mission closeout — QP-G2I-000/010/020/030/040 done, document lease released. QP-G2I-035 post-reset Claude audit는 이 통합 종결과 분리된 Kernel runtime 선행 게이트로 유지한다.

## Verification commands

```powershell
python -m pytest -p no:cacheprovider --basetemp=.pytest_tmp_g2i_state `
  quantpilot/tests/unit/test_paper_execution_event_migration.py `
  quantpilot/tests/unit/test_paper_dispatch_persistence.py `
  quantpilot/tests/unit/test_paper_risk_reservation_model.py `
  quantpilot/tests/unit/test_paper_submission_coordinator.py `
  quantpilot/tests/unit/test_paper_kill_persistence.py `
  quantpilot/tests/unit/test_kis_paper_client.py `
  quantpilot/tests/unit/test_kis_paper_kill_job.py `
  quantpilot/tests/unit/test_kis_paper_session_job.py `
  quantpilot/tests/unit/test_paper_reconciliation.py `
  quantpilot/tests/unit/test_paper_reconciliation_apply.py `
  quantpilot/tests/unit/test_external_paper_harness_integration.py
python -m pytest -p no:cacheprovider --basetemp=.pytest_tmp_g2i_events `
  quantpilot/tests/unit/test_execution_transition_compatibility.py `
  quantpilot/tests/unit/test_paper_execution_event_model.py `
  quantpilot/tests/unit/test_paper_execution_reducer.py `
  quantpilot/tests/unit/test_paper_execution_event_store.py `
  quantpilot/tests/unit/test_paper_execution_dual_write.py `
  quantpilot/tests/unit/test_paper_execution_shadow_parity.py
python -m pytest -p no:cacheprovider --basetemp=.pytest_tmp_g2i_drift `
  quantpilot/tests/unit/test_strategy_drift.py `
  quantpilot/tests/unit/test_strategy_performance_feed.py `
  quantpilot/tests/unit/test_providers.py `
  quantpilot/tests/unit/test_env_example_sync.py
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp_g2i_full
python -m quantpilot.jobs.run_smoke
python -m quantpilot.jobs.run_kis_paper_kill engage
python -c "import json; from pathlib import Path; from quantpilot.services.api.main import app; assert Path('openapi.json').read_text(encoding='utf-8') == json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\n'; print(len(app.openapi()['paths']))"
# quantpilot/apps/web
npm run generate:api
git diff --exit-code -- ../../../openapi.json src/lib/openapi.d.ts
npm run test
npm run build
```

## Handoff record

```text
task_id: QP-GATE2-MAINLINE-INTEGRATION
agent_and_model: Codex GPT-5 lead + Claude Gate 2/Drift artifacts + three fallback final auditors
commit: 377a5d6dbfb27df7430fbf0cd7bbc473bfc9230d
owned_paths: merge integration, .env.example, docs/STATUS.md, this workboard
acceptance_met: candidate checks/fallback audits and main integration passed;
  post-reset Claude audit remains a separate Kernel-runtime prerequisite
exact_checks: exact commands are recorded in `Verification commands`; results targeted
  234/103/84, cross-feature 44, full 1046 passed/2 skipped, smoke mock/live=false/
  operator blocked, kill paper_kill_disabled, OpenAPI 51 byte-exact + d.ts sync,
  frontend 23 passed + build
known_limits: real KIS Gate P and all post-Gate-2 roadmap gates remain pending
integration_requests: main integrate after fallback P0/P1=0 quorum; complete QP-G2I-035 before Kernel runtime
```

## Mission retrospective

| Task ID | Task class | Agent/model | First-pass | P0 | P1 | P2 | Rework cycles | Required checks | Elapsed | Rating |
|---|---|---|---|---:|---:|---:|---:|---|---|---:|
| `QP-G2I-010` | stateful release integration | Codex GPT-5 | no | 0 | 1 | 2 | 2 | targeted/full/smoke/kill/OpenAPI/frontend + three audits | 2026-07-12 | 2 |
| `QP-G2I-030` | independent merge audit | three Codex fallback reviewers | yes | 0 | 0 | 3 | 0 | runtime/test-docs/Git complete-diff audits | 2026-07-12 | 5 |
| `QP-G2I-040` | mainline integration | Codex GPT-5 mission lead | yes | 0 | 0 | 0 | 1 tooling retry | full backend/frontend/smoke/kill/OpenAPI after fast-forward | 2026-07-12 | 4 |

- Routing decision quality: stateful merge를 Codex, 독립 감사를 분리한 결정은 정확했다. Claude session-limit은 품질 증거가 아니라 가용성 편차로 기록했다.
- Capability scorecard update: QP-G2I-035 post-reset Claude 감사와 함께 기록한다. 단일 통합 표본이므로 현재 선호는 변경하지 않는다.
- User-owned changes preserved: main의 `CLAUDE.md.20260705.bak`는 계속 untracked이며 읽기·수정·stage·commit하지 않았다.
- Remaining limitations: QP-G2I-035, Gate P manual KIS evidence, Kernel v2, ledger/runtime, and live-candidate gates remain pending.
