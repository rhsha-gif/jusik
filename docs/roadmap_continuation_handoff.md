# QuantPilot 전체 로드맵 연속 작업 인계서

기준 시각: **2026-07-14 KST**

작성 기준 main: `f8162e295850df7656a6816f2222282520d07f36`

문서 목적: 다른 Codex/Claude 채팅이 이전 대화 없이 저장소만 보고 안전하게 다음 작업을 이어간다.

## 0. 한눈에 보는 현재 위치

현재 main에는 다음 개발 게이트까지 통합되어 있다.

1. KIS paper managed-order cancel-all kill v1과 schema v9 기준점
2. 원자적 현금·매도수량·gross exposure 위험예약 v1, schema v10
3. 캐노니컬 실행 이벤트 shadow journal v1, schema v11
4. Execution Kernel v2의 계약·정적 안전 게이트 `QP-KER-000A~000E`

**2026-07-14 갱신: Gate 010 통합 완료.** 아래 1~4단계가 그대로 수행되어 완료됐다.

1. Claude Code(CLI 2.1.209, `claude-fable-5`)가 recursion-review worktree에서 최종 해시 결속
   follow-up `76fdef8`을 직접 커밋했다(hash-bound 판정 P0=0/P1=0/P2=0, 전체 243/243,
   adversarial 12/12, semantic AST digest 독립 재계산 일치).
2. Codex 독립 재감사가 P0=0/P1=0/P2=1을 반환했고, 유일한 P2(회고 행 누락)는 `28ba4c3`으로
   폐쇄했다.
3. 새 clean 통합 worktree(`C:/qp-gate010-integration-20260714`)에서 recursion 문서 계보
   (`b70d848`→`0419707`→`083b55a`→`76fdef8`→`28ba4c3`, merge `a9a15d3`)와 runtime
   `61a4f93`(merge `fed4ed6`)을 순서대로 통합하고 main을 `fed4ed6`으로 fast-forward했다.
4. 통합 트리 재검증: backend `1289 passed, 2 skipped, 0 failed`; smoke `broker=mock`,
   `live_trading_enabled=false`, operator `blocked`, submitted `[]`; `git diff --check` clean;
   두 runtime 파일 SHA256은 §2.1의 최종값과 동일.

현재 활성 게이트는 **`QP-KER-015`** (temporal/durable expiry와 strategy version totality
hardening)다.

## 1. 절대 보존해야 하는 상태

main worktree에는 다음 untracked 경로가 있다.

```text
.omo/
CLAUDE.md.20260705.bak
```

둘 다 이번 미션의 소유물이 아니다. 읽기·정리·이동·삭제·stage·commit하지 않는다. `git clean`,
`git reset --hard`, 재귀 삭제, main을 깨끗하게 만들기 위한 자동 정리는 금지한다. 다른 오래된
worktree도 별도 승인 없이 prune/remove/reset하지 않는다.

프로젝트 안전 기본값은 계속 다음과 같아야 한다.

```text
LIVE_TRADING_ENABLED=false
GUARDED_AUTOPILOT_ENABLED=false
FULLY_AUTOMATED_OPERATOR_ENABLED=false
MARKET_ORDERS_ENABLED=false
BROKER_MODE=mock
```

- 실제 KIS 주문·조회, 실계좌, live endpoint, 자격증명, API key를 자동 테스트에 사용하지 않는다.
- ambiguous broker POST는 자동 재전송하지 않는다.
- LLM/RL은 주문 생성·승인·제출 권한을 갖지 않는다.
- risk, kill, idempotency, order state, audit, reconciliation 경계를 우회하지 않는다.

## 2. 검증된 기준점과 브랜치

| 구분 | 상태 | 브랜치/커밋 | 검증 사실 |
|---|---|---|---|
| 현재 main 기준 | `integrated` | `main` / `f8162e2` | 2026-07-14 재검증: backend `1046 passed, 2 skipped`; smoke `broker=mock`, `live=false`, operator blocked, submitted `[]` |
| Gate 0 baseline/kill | `integrated` | 계보가 main에 포함 | schema v9; kill/cancel journal; manual/external orders와 flatten은 자동 범위 밖 |
| Gate 1 atomic reservation | `integrated` | main에 포함 | schema v10; fake-only acceptance; reservation+prepared dispatch 원자성 |
| Gate 2 canonical events | `integrated` | main에 포함 | schema v11; authoritative row와 canonical shadow event same-transaction dual-write |
| Kernel 계약 | `integrated` | main `f8162e2` | `QP-KER-000A~000E` 계약/정적 안전 게이트 완료 |
| Recursion 계약 보강 | `integrated` | `b70d848` -> `0419707`, merge `a9a15d3` | 2026-07-14 main 통합 |
| Claude recursion 리뷰 | `integrated` | `083b55a` -> `76fdef8` -> `28ba4c3` | 최종 hash-bound follow-up P0=0/P1=0/P2=0; Codex 재감사 P0=0/P1=0; main 통합 |
| Pure Kernel runtime | `integrated` | `61a4f93`, merge `fed4ed6` | 통합 트리 재검증: backend `1289 passed, 2 skipped`; safe smoke; Claude+Codex P0/P1=0 |
| Manual KIS Gate P | `open_manual` | 자동화 대상 아님 | `VTTC0084R`, 실제 paper cancel/buying-power semantics는 사용자 승인·자격증명이 있는 수동 검증 필요 |

### 2.1 Gate 010 최종 파일 결속값

대상 커밋:

```text
61a4f93a222b936459681f592a8ce71ba9bd11fc
parent: 7af13b02348691c8839ee9229b6a13c5af2188f5
```

파일 SHA256:

```text
quantpilot/packages/core/execution/kernel.py
FA84BADC2710FA8E53BA9EBF002C4DD6F39F5D2ECC163B3A282EC3FDE791A7E1

quantpilot/tests/unit/test_execution_kernel_v2.py
8E9B320CDB9DAA84FD08B9984CAFC8C2A5EE44E874F97C1A7E0D2A0C576C3BAC
```

테스트가 결속하는 normalized semantic AST SHA256:

```text
750680273FB34423CD095E8C8E64B384D2ECB6FBD9154271A03CC104C6065102
```

독립 감사 결과:

- authority/KIS/decision: P0=0/P1=0/P2=0
- purity/totality: P0=0/P1=0/P2=0
- contract/test: P0=0/P1=0/P2=1
- 유일한 P2는 Python AST schema/minor-version 변경 시 digest를 명시적으로 재산출하고 독립 재검토해야
  한다는 운영 제약이다. digest 자동 갱신은 금지한다.
- 위 세 Codex 감사의 원래 별도 저장소 artifact는 없었다. 현재 저장소 증거는 이 인계서의 요약과
  `docs/roadmap_continuation_handoff_claude_review.md`의 독립 재현 기록이며, runtime 통합 승인 증거는
  다음 채팅에서 Claude `QP-KER-010B` follow-up과 Codex 재감사로 새로 남겨야 한다.

### 2.2 Claude 초안 처리 완료 (2026-07-14)

worktree `주식트레이더-claude-kernel-v2-recursion-review`의 두 uncommitted 초안은 최종 해시로
교정되어 `76fdef8`(hash-bound follow-up)과 `28ba4c3`(Codex 재감사 P2 폐쇄)으로 커밋됐고,
`083b55a`는 amend되지 않았다. 해당 계보는 merge `a9a15d3`으로 main에 통합됐다.

## 3. 전체 매크로 로드맵 0~12

상태 의미:

- `integrated`: main에 포함되고 해당 개발 게이트 검증 완료
- `accepted_unmerged`: 검증됐지만 main 미통합
- `active`: 현재 진행 중인 게이트
- `open`: 아직 시작하지 않음
- `manual`: 사용자 권한이 필요한 외부 검증
- `locked`: 선행 게이트 전에는 시작 금지

| 단계 | 목표 | 현재 상태 | 다음 진입 조건 |
|---:|---|---|---|
| 0 | 기준점 동결, KIS kill v1, ADR/문서 정합 | `integrated` + Gate P `manual` | `VTTC0084R` 실제 paper 검증은 별도 사용자 승인 시에만 |
| 1 | 원자적 현금·매도수량·gross exposure 위험예약 | `integrated` | 완료. schema v10 보존 |
| 2 | Canonical Order/Execution Event 모델과 reducer | `integrated` | 완료. schema v11 shadow journal 보존 |
| 3 | Execution Kernel v2 단일 순수 판단 경계와 단계별 cutover | `active` | `QP-KER-010 integrated` (2026-07-14, main `fed4ed6`); 현재 게이트는 `QP-KER-015` |
| 4 | Transactional outbox와 계좌별 single writer | `open` | `QP-KER-080` 완료 후 시작 |
| 5 | Execution/Position/Cash authoritative ledger와 reconciliation break | `open` | outbox와 canonical event 계약 안정화 |
| 6 | Continuous OMS/Risk/Reconciliation runtime | `open` | authoritative ledger와 worker 복구 계약 완료 |
| 7 | Pause/Kill/Flatten v2 분리와 reduce-only 청산 | `open` | authoritative position ledger, continuous runtime, broker parity 필요 |
| 8 | KRX calendar, security master, KIS stream, broker capability, rate-limit scheduler | `open` | 커널/worker 경계를 우회하지 않는 별도 계약 필요 |
| 9 | 연구 artifact chain, bias 검증, LEAN differential backtest | `open` | research plane으로 분리; 주문 권한 없음 |
| 10 | 인증·권한·tamper-evident audit·backup/restore·관측성 | `open` | 원격 접근이나 live 후보 이전 필수 |
| 11 | Live-data shadow와 반복 KIS paper 운영 검증 | `locked` + 일부 Gate P `manual` | duplicate POST/fill 0, unexplained break 0, replay/restart/kill parity |
| 12 | Live candidate/canary | `locked` | 앞선 모든 gate와 별도 사람 승인. 현재 구현·활성화 금지 |

## 4. Execution Kernel v2 서브로드맵

| Gate | 내용 | 상태 | 핵심 수용 조건 |
|---|---|---|---|
| `QP-KER-000A~000E` | 저장소 기반 계약, Claude 최종 리뷰, 정적 purity closure | `integrated` | main `f8162e2`에 포함 |
| `QP-KER-010A` | low-recursion containment 계약 보강 | `integrated` | `b70d848`와 child `0419707`; merge `a9a15d3` |
| `QP-KER-010B` | Claude recursion counterpart review | `integrated` | `083b55a` + hash-bound follow-up `76fdef8` + P2 폐쇄 `28ba4c3`; P0=0/P1=0/P2=0 |
| `QP-KER-010` | 순수 frozen model/evaluator, 결정적 fingerprint, zero I/O | `integrated` | `61a4f93`, merge `fed4ed6`; 파일 hash 일치, Claude+Codex P0/P1=0 |
| `QP-KER-015` | temporal/durable expiry와 strategy version totality hardening | `ready` | 기존 schema v11 payload 사용, migration 없음; 양쪽 expiry fence, restart/pre-POST, Unicode version corpus |
| `QP-KER-020` | 기본 off인 shadow runner | `open` | authoritative mutation 0, broker callable 0, unknown mode fail-closed |
| `QP-KER-030` | L1~L5 exhaustive legacy-vs-kernel parity | `open` | blocked/success/dry-run corpus, shadow counters 0 |
| `QP-KER-035` | common authoritative facade | `open` | 모든 level adapter가 같은 facade 사용, 새 broker authority 없음 |
| `QP-KER-040` | Level 3 cutover | `open` | 명시 승인·fresh risk·기존 idempotency 보존 |
| `QP-KER-050` | Level 4 cutover | `open` | guarded 기본 off, kill/window/budget/stale quote fail-closed |
| `QP-KER-060A` | Level 5 ordinary cutover | `open` | lifecycle/registry/operator authority 증거 동일 커널 통과 |
| `QP-KER-060B` | professional risk-reduction cutover | `open` | reduce-only 의미와 일반 진입 권한 분리 |
| `QP-KER-065` | fake-KIS composition rehearsal | `open` | 네트워크 0, 실제 KIS 구성 의미만 fake-client로 검증 |
| `QP-KER-070` | KIS paper cutover last | `locked` | Gate P 및 flag/profile ADR 완료, 수동 외부 증거 필요 |
| `QP-KER-080` | legacy 경로 제거와 최종 감사 | `open` | 우회 경로 0, 전체 replay/parity, P0/P1=0 |

`QP-KER-015`의 현재 계약상 예상 소유 범위는 다음과 같다. Gate 010 통합 전에는 수정하지 않는다.

```text
quantpilot/packages/core/harness_service.py
quantpilot/packages/core/execution/paper_submission.py
quantpilot/packages/core/execution/state_machine.py
quantpilot/packages/db/sqlite_repositories.py
quantpilot/jobs/run_kis_paper_session.py
focused expiry/coordinator/session tests
quantpilot/tests/unit/test_strategy_version_matching.py  # planned-new
```

## 5. Gate 010을 이어가는 정확한 순서

### 5.1 첫 read-only 확인

다음 채팅은 main에서 즉시 편집하지 말고 먼저 실행한다.

```powershell
git status --short
git rev-parse HEAD
git worktree list --porcelain
claude auth status
```

예상 main의 기존 비소유 상태:

```text
?? .omo/
?? CLAUDE.md.20260705.bak
```

runtime worktree에서:

```powershell
git status --short
git rev-parse HEAD
Get-FileHash -Algorithm SHA256 -LiteralPath `
  quantpilot/packages/core/execution/kernel.py,`
  quantpilot/tests/unit/test_execution_kernel_v2.py
```

예상 결과는 clean status, HEAD `61a4f93a...`, 위 §2.1의 두 SHA256이다. 하나라도 다르면
Claude review와 통합을 중단하고 새 해시로 전체 Gate 010 감사를 다시 시작한다.

### 5.2 Claude final follow-up

Claude Code가 기존 recursion-review worktree에서 다음을 수행한다.

1. `083b55a`와 자기 uncommitted 두 문서 초안을 읽는다.
2. runtime commit `61a4f93`과 두 파일 SHA256을 전후로 직접 검증한다.
3. `test_execution_kernel_v2.py` 집중 전체와 필요한 adversarial selection을 실행한다.
4. control-flow/path rebinding, semantic AST digest, recursion containment, KIS L3 차단, KIS L5
   declarative durability/reservation만을 재검토한다.
5. 오래된 hash/count를 모두 최종값으로 교정한다.
6. 두 문서만 stage하고 새 follow-up commit을 직접 만든다. `083b55a` amend 금지.

Claude writable allowlist:

```text
docs/execution_kernel_v2_contract.md
docs/execution_kernel_v2_workboard.md
```

### 5.3 Codex 재감사와 통합

Claude follow-up이 P0=0/P1=0이면 Codex 미션 리드는 새 clean integration worktree를 main에서 만들고
다음 두 병렬 계보를 검토한다. `61a4f93`의 parent는 `7af13b0`이므로 recursion 문서 계보의 후손이
아니다. 아래 순서는 Git 조상 관계가 아니라 mainline에 적용할 순서다.

```text
recursion docs branch:
b70d8482fa59fd9cd11e84431ad1e40af75b0ba1
  -> 0419707e940f381d6e348a28a3b5a994ed6016a3
  -> 083b55a1bb17f1e33311396ca616ab1c1f97ec33
  -> <Claude final hash-bound follow-up>

parallel runtime branch, parent 7af13b02348691c8839ee9229b6a13c5af2188f5:
61a4f93a222b936459681f592a8ce71ba9bd11fc

mainline apply order:
recursion docs branch commits -> 61a4f93a222b936459681f592a8ce71ba9bd11fc
```

각 커밋의 변경 경로를 먼저 확인한다. 문서 계보는 Kernel 계약/작업보드만, runtime 커밋은
`kernel.py`와 `test_execution_kernel_v2.py`만 바꿔야 한다. 통합 후 다음을 실행한다.

```powershell
python -m pytest quantpilot/tests
python -m quantpilot.jobs.run_smoke
git diff --check
git status --short
```

pytest 전역 temp 권한 문제가 재발하면 worktree-local `--basetemp`를 추가하고 환경 우회임을 기록한다.
smoke는 `broker=mock`, `live_trading_enabled=false`, operator blocked, submitted ids `[]`여야 한다.

## 6. 주요 문서와 신뢰 우선순위

다음 순서로 읽는다.

1. 이 문서
2. `AGENTS.md`
3. `docs/roadmap_continuation_handoff_workboard.md`
4. `docs/execution_kernel_v2_contract.md`
5. `docs/execution_kernel_v2_workboard.md`
6. 완료 증거:
   - `docs/roadmap_baseline_v9_report.md`
   - `docs/atomic_risk_reservation_v1_completion_report.md`
   - `docs/canonical_order_events_v1_completion_report.md`
   - `docs/gate2_mainline_integration_workboard.md`
7. 전체 아키텍처 참고:
   - `docs/current_project_workflow.md`
   - `docs/roadmap_execution_workboard.md`

기존 `docs/roadmap_execution_workboard.md`와 main의 `docs/execution_kernel_v2_workboard.md`에는
작성 시점상 다음 stale 문구가 있을 수 있다.

- Kernel contract/inventory가 다음 작업이라는 표현: 계약은 이미 완료됐다.
- `QP-KER-010`이 단순 `in_progress`라는 표현: 현재 정확한 상태는 `accepted_unmerged`다.
- `QP-KER-000B` account-limit blocker: `000D/000E`로 superseded된 역사 기록이다.
- `083b55a`가 최종 runtime acceptance를 보장한다는 표현: 파일 해시가 결속되지 않아 불충분하다.

이 인계서가 상태 요약의 우선 문서지만, 실제 branch/commit/hash가 다르면 Git과 테스트 결과를 최종
권위로 삼고 이 문서를 갱신한다.

## 7. 현재 알려진 제한과 별도 추적사항

- manual Gate P: `VTTC0084R`, 실제 KIS paper cancel/buying-power/session semantics.
- runtime journal은 아직 authoritative ledger가 아니라 schema-v11 shadow다.
- Kernel `REVIEWED_KERNEL_AST_SHA256`은 Python AST version에 민감하다. 자동 갱신 금지.
- worktree 생성 시 Git이 `warning: ignoring ref with broken name refs/heads/main (1)`을 출력했다.
  현재 테스트/브랜치 생성은 성공했지만, 별도 승인된 repo-hygiene 미션 없이 ref를 삭제·수정하지 않는다.
- 오래된 다수 worktree는 역사적 증거와 사용자/에이전트 변경을 포함할 수 있으므로 임의 정리하지 않는다.

## 8. 다음 채팅에 전달할 한 문장

> Gate 010은 2026-07-14 main `fed4ed6`에 통합 완료됐다. `docs/execution_kernel_v2_contract.md`의
> `QP-KER-015` 소유 범위와 수용 조건을 기준으로 temporal/durable expiry hardening을 별도
> worktree에서 시작하라. live/KIS network/market order 권한은 없다.
