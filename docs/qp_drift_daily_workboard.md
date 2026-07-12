# QP-DRIFT-DAILY Workboard — DriftMonitor daily revaluation + fee-aware PnL

## Document edit lease

- Lease status: `released`
- Document editor: `—` (마지막 편집: Claude Code, 미션 완료 갱신)
- Mission/task ID: `QP-DRIFT-DAILY`
- Acquired at: `2026-07-12 KST` / Released at: `2026-07-12 KST`

## Mission charter

| Field | Value |
|---|---|
| Mission ID | `QP-DRIFT-DAILY` |
| Received by | Claude Code |
| Mission lead | Claude Code |
| Lead model/version | Claude Fable 5 |
| Goal | DriftMonitor 성과 피드를 개선: 보유 포지션의 일별 종가 재평가 + KIS 실거래 비용(수수료·매도세) 반영으로 실현 MDD/수익률을 백테스트 증빙(이미 KIS 비용 기준)과 비교 가능하게 만든다. |
| In scope | `compute_strategy_performance` 재작성, `StrategyPerformanceRecord` 후방호환 필드 추가(`cost_basis`, `valuation`), 단위 테스트, openapi.json/openapi.d.ts 재생성, STATUS.md 갱신 |
| Out of scope | 드리프트 임계값(1.5x) 변경, 승인 기준 확정, conflict_rule/상관 예산, 라이브 기능, 프론트 UI 변경 |
| Safety constraints | 모든 live/guarded/full automation/market order 플래그 기본 비활성, `BROKER_MODE=mock`, fixture 결정성 유지, 드리프트 만료 fail-closed 동작 약화 금지, 피드에서 예외로 안전 경로를 중단시키지 않음 |
| Completion criteria | pytest 전체 통과, run_smoke 통과, apps/web build+test 통과, openapi 타입 동기화, 상대 검토 반영 기록 |

## Counterpart plan review

- Reviewer/model: `Codex 시도 → CLI config 오류로 차단; 독립 컨텍스트 Claude 검토자(fallback)로 대체`
- Review status: `completed — APPROVE-WITH-CHANGES, 필수 사항 반영됨`
- Blocked detail: `~/.codex/config.toml` 4행 `unknown variant 'priority', expected 'fast' or 'flex'` —
  사용자 설정 파일이므로 리드가 임의 수정하지 않고 QP-WORKFLOW-DOC 선례(상대 차단 시 bounded
  fallback)를 따름. 사용자에게 수정 안내 예정.
- Decomposition findings (독립 검토, APPROVE-WITH-CHANGES):
  - `F1(필수, 반영)` 수수료 반영으로 제로-MDD 증거 티켓은 첫 매수 체결에서 즉시 드리프트 만료됨 —
    의도된 fail-closed 동작임을 `_drift_trigger_fired` 주석과
    `test_zero_mdd_evidence_ticket_expires_on_first_fee_bearing_fill` 테스트로 명문화.
  - `F2(반영)` "새 MDD ≥ 기존 MDD"는 단일 종목·수수료 효과에서 보장되고 다종목 교차 mark에서는
    경험적 경향 — 아래 설계 요약 문구 완화. 안전성 주장의 근거로 사용하지 않음.
  - `F3(반영)` `filled_at` UTC 정규화 (naive/aware 혼재 정렬 TypeError·세션 날짜 어긋남 방지).
  - `F4(기록만)` 날짜 파싱 실패 전량 발생 시 재평가가 조용히 비활성 — never-raise 원칙과 상충하는
    로깅/라벨 강등은 후속 검토 항목으로 남김.
  - `F5(이상 없음)` fixture 결정론 재확인. `F6(반영)` openapi.json + openapi.d.ts 동반 재생성 절차 준수
    (한글 경로 URL 인코딩 버그로 ASCII 경로 경유 생성).
  - 검토자 검증: 수수료 수식이 backtest engine(`engine.py:315,349-354`)과 일치, 정확값 단언 기존
    테스트 없음, 프론트 소비처 없음(additive 안전).
- Required substantive counterpart role: 구현 전 설계·분해 검토 (읽기 전용 감사). 단일 파일 중심의 국소 변경이므로 구현 소유는 리드가 유지하고, Codex는 독립 검토를 소유한다.
- **후속 Codex 실질 산출물 (CLI 복구 후)**: Codex가 `codex/qp-drift-daily-audit` 브랜치에서 안전
  감사를 넘어 fail-closed 강화 구현 커밋 `f8bb594` ("fix(drift): bind performance evidence fail
  closed")를 독립 소유·산출함. 리드(Claude Fable 5)가 diff 전수 독립 검토 후 P0/P1 없음으로 판정,
  `claude/qp-drift-daily-final-review`에 cherry-pick (`25182df`). 핵심 내용:
  - 성과 정규화 분모를 누적 매수 명목가 → **첫 매수 제안 시점 계좌 equity**(`account_equity_at_proposal`
    + `portfolio_snapshot_id` 동반 증거, both-or-neither 검증)로 교체해 백테스트 MDD와 동일 기준 비교.
  - 증거 열화 시 **만료 대신 차단**: `valuation_status`/`normalization_basis`/fingerprint(체결 워터마크·
    시세·캘린더) 불일치가 있으면 `strategy_activation_allowed`가 사유 코드와 함께 fail-closed 차단.
    티켓은 일시 데이터 문제로 파괴되지 않되 실행은 불가. 드리프트 임계 로직(×1.5, zero-MDD)은 무변경.
  - KST 세션 마감(15:30+30분 finality) 이전의 당일 종가 미확정 처리, 미래 체결/재고 초과 매도 →
    `reconciliation_required`, provider 실패가 MDD 만료를 우회할 수 없음을 테스트로 고정.
  - 테스트 31개 신규 추가(제거 0), 기존 단언은 새 equity 기준의 정확값으로 재조준(약화 없음).

## Routing assessment

점수식: `domain*0.30 + tools*0.25 + track*0.25 + continuity*0.10 + coordination*0.10`.

| Task | Candidate | Domain | Tools | Track | Continuity | Coordination | Total | Decision rationale |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `QP-DD-01` 구현 | Claude Code | 5 | 4 | 4 | 5 | 4 | 4.45 | 퀀트·리스크 설계 강점, 직전 세션 연속성(라우터/CORS 통합 직후), 대상 코드 전체를 이미 파악 |
| `QP-DD-01` 구현 | Codex GPT-5.4 | 4 | 5 | 4 | 2 | 4 | 4.05 | 구현력 동급이나 세션 연속성 없음 |
| `QP-DD-02` 검토 | Codex GPT-5.4 | 4 | 5 | 4 | 3 | 4 | 4.15 | 독립 감사 관점 확보 (리드 자기승인 금지) |

## Work queue

| Task ID | Owner/model | Reviewer/model | Depends on | Worktree/branch | Owned paths | Status | Acceptance | Evidence/commit |
|---|---|---|---|---|---|---|---|---|
| `QP-DD-00` | Claude Fable 5 | — | none | main tree | `docs/qp_drift_daily_workboard.md` | done | 미션·설계·라우팅 확정 | this file |
| `QP-DD-01` | Claude Fable 5 | Codex (read-only) → fallback 검토자 | `QP-DD-00` | `주식트레이더-drift-daily` / `claude/qp-drift-daily-01` | `quantpilot/packages/core/harness_service.py`, `quantpilot/packages/core/schemas.py`, `quantpilot/tests/unit/test_strategy_drift.py`, `quantpilot/tests/unit/test_strategy_performance_feed.py`, `openapi.json`, `quantpilot/apps/web/src/lib/openapi.d.ts`, `docs/STATUS.md`, this workboard | done | 일별 종가 재평가 + 비용 반영 + 후방호환 스키마, 신규 테스트, 전체 검증 통과 | `6721d24` (main) |
| `QP-DD-02` | Claude Fable 5 (lead) | — | `QP-DD-01` | main tree | mainline integration | done | 전체 검증 재실행 후 main 병합, STATUS/작업보드 갱신 | main = `6721d24` |
| `QP-DD-03` | Codex GPT-5.x | Claude Fable 5 (독립 diff 검토) | `QP-DD-02` | `codex/qp-drift-daily-audit` | `harness_service.py`, `schemas.py`, `data/providers.py`, `data/external.py`, `operator/professional_cycle.py`, `services/api/dependencies.py`, `jobs/run_kis_paper_session.py`, `.env.example`, `openapi.json`, `openapi.d.ts`, 관련 unit tests | done | 드리프트 성과 증거 fail-closed 결속 (P0/P1 폐쇄), 안전 기본값 무변경, 테스트 약화 없음 | `f8bb594` → cherry-pick `25182df` (리드 최종 검토 P0/P1=0, main 통합 완료) |
| `QP-DD-04` | Claude Fable 5 (lead) | — | `QP-DD-03` | `주식트레이더-claude-drift-final` / `claude/qp-drift-daily-final-review` | cherry-pick + `docs/qp_drift_daily_workboard.md`, `docs/STATUS.md` | done | f8bb594 독립 검토·체리픽, 전체 검증(백엔드/스모크/openapi/프론트) 재실행, 문서 최종화 | 검증 출력은 Checkpoint log 참조 |

## Design summary (QP-DD-01)

1. **비용 반영**: `backtest/costs.py`의 확정 기준 사용 — 매수 `cash -= notional*(1+1.40527bps)`,
   매도 `cash += notional*(1-1.40527bps-20bps)`. 슬리피지는 미적용(실체결가에 이미 내재).
   분모(invested)는 총 매수 명목가 유지.
2. **일별 종가 재평가**: `market_data_provider.get_price_history()`를 (symbol|ticker 정규화 후)
   (종목, 일자)별 종가로 인덱싱. 최초 체결일부터 시간순으로 당일 체결 반영 → 당일 종가로
   보유 포지션 재평가 equity 포인트 추가. 체결도 보유 포지션도 없는 날은 스킵.
   종가 결측 시 마지막 관측가로 폴백(피드는 절대 예외로 실패하지 않음 — 드리프트 감시가
   피드 오류로 멈추면 안 됨).
3. **스키마**: `StrategyPerformanceRecord`에 `cost_basis: str = "none"`,
   `valuation: str = "last_fill_price"` 추가 (기존 레코드/수동 입력 후방호환).
   신규 auto_feed 레코드는 `kis_bankis_online_api+krx_tax_2026` / `daily_close`.
4. **불변**: 드리프트 임계 로직(백테스트 MDD×1.5, zero-MDD fail-closed) 무변경.
   실현 MDD 증가는 단일 종목·수수료 효과에서 보장되며 다종목 교차 mark에서는 경험적 경향
   (F2) — fail-closed 안전성은 이 불변식이 아니라 임계 로직 무변경 + F1 명문화로 담보.

## Known limits (비차단, 명시 기록)

라이브 준비 완료를 주장하지 않는다. 다음 한계는 P0/P1이 아니며 후속 미션 대상이다.

1. **capital_epoch/현금흐름 원장 부재**: 정규화 분모(첫 매수 시점 계좌 equity)는 증거 epoch 동안
   외부 현금흐름이 없다는 가정에 기댄다. 권위 있는 capital_epoch/cashflow ledger가 없으므로
   **증거 epoch 진행 중 paper 자본 리셋·출금은 금지**된다 (운영 규율로 담보, 코드 강제 아님).
2. **수동 `SimpleKrxCalendar`는 라이브 후보 권위가 아님**: 주말 + `KRX_HOLIDAYS` 수동 설정 기반의
   최소 캘린더다. 캘린더 설정 변경은 fingerprint로 기존 증거를 무효화하지만, 휴장일 누락 자체는
   탐지하지 못한다. live-candidate 단계 전에 권위 있는 KRX 캘린더 소스로 교체해야 한다.
3. **동일 타임스탬프 체결 순서**: 같은 `filled_at`의 체결 간 순서는 canonical event ordering이
   도입될 때까지 정렬 안정성에 의존한다. 현재 fixture 결정성 하에서는 재현 가능하나, 실서버
   이벤트 스트림 기준의 순서 보증은 아니다.
4. `F4` (이월): 날짜 파싱 실패 전량 발생 시 재평가가 조용히 비활성화되는 경로의 로깅/라벨 강등은
   여전히 후속 검토 항목 — 단, f8bb594 이후에는 해당 경우 `valuation_status`가 `complete`가 될 수
   없어 활성화가 차단되므로 실행 경로 위험은 폐쇄됨.

## Checkpoint log

- `2026-07-12` — Claude Fable 5 — 미션 수임, 설계 확정, worktree `claude/qp-drift-daily-01` 생성, Codex 읽기 전용 분해 검토 요청 발송.
- `2026-07-12` — Claude Fable 5 — Codex CLI가 `~/.codex/config.toml` 오류로 차단 → 독립 컨텍스트 fallback 검토자 투입 (QP-WORKFLOW-DOC 선례).
- `2026-07-12` — Claude Fable 5 — 구현 완료: fee-aware daily-close 재평가, 스키마 필드 2개, 신규 테스트 7개. openapi.json 재생성 시 기존 스테일 발견(`/api/operator/professional-status` 누락, 51 paths로 보정).
- `2026-07-12` — fallback reviewer — APPROVE-WITH-CHANGES (F1~F6); 필수 F1·F6 및 권고 F2·F3 반영, F4는 후속 항목.
- `2026-07-12` — Claude Fable 5 (lead) — QP-DD-01/02 main 통합 확인 (main = `6721d24`).
- `2026-07-12` — Codex GPT-5.x — CLI 복구 후 `codex/qp-drift-daily-audit`에서 안전 검토 + fail-closed
  강화 커밋 `f8bb594` 산출 (13 files, +1918/−99; 신규 테스트 31개).
- `2026-07-12` — Claude Fable 5 (lead) — `f8bb594` 독립 diff 전수 검토: P0/P1 없음, 안전 플래그·드리프트
  임계 로직 무변경, 테스트 제거 0건·단언 약화 없음 확인 → `claude/qp-drift-daily-final-review`에
  cherry-pick (`25182df`).
- `2026-07-12` — Claude Fable 5 (lead) — 전체 검증 (branch `claude/qp-drift-daily-final-review`, `25182df`):
  - 표적 pytest (`test_strategy_drift.py`, `test_strategy_performance_feed.py`, `test_providers.py`):
    `82 passed in 4.04s`
  - 전체 백엔드 pytest (junit): `tests=830, failures=0, errors=0, skipped=2` (828 passed)
  - `run_smoke`: OK — `"broker": "mock"`, `"live_trading_enabled": false`, operator
    `"status": "blocked"` (`level5_flag_disabled`)
  - OpenAPI 정확 동기화: 앱 재생성 `openapi.json`이 커밋 blob과 **바이트 단위 일치** (51 paths);
    `npm run generate:api` 재생성 후 `openapi.d.ts` git diff 없음
  - 프론트 (`quantpilot/apps/web`, 임시 node_modules junction 사용 후 제거): vitest
    `23 passed (7 files)`, `npm run build` 성공 (17.65s)
- `2026-07-12` — Claude Fable 5 (lead) — 워크보드·STATUS 최종화, 문서 lease 해제, 통합 권고 확정.
- `2026-07-12` — Claude Fable 5 (lead) — **mainline 통합 완료**: 리드 최종 검토와 독립 Codex 통합
  감사가 모두 P0=0, P1=0으로 fast-forward 통합을 권고 → main을
  `claude/qp-drift-daily-final-review`로 fast-forward (merge commit 없음). 문서 후속 커밋 이전 기준
  main = `2bbd833` (= `25182df` + 미션 문서 커밋, 조상 `6721d24` 확인). 사용자 untracked 백업 파일은
  손대지 않음. 통합 후 전체 검증 증거는 아래 체크포인트 참조.
- `2026-07-12` — Claude Fable 5 (lead) — **통합 후 main 전체 검증 (2bbd833 기준, 오프라인·mock 전용)**:
  - `git diff --check`: 이상 없음
  - 전체 백엔드 pytest (junit): `tests=830, failures=0, errors=0, skipped=2` (828 passed)
  - `run_smoke`: OK — `"broker": "mock"`, `"live_trading_enabled": false`, operator
    `"status": "blocked"` (`level5_flag_disabled`)
  - OpenAPI 정확 동기화: 앱 재생성 `openapi.json`이 작업트리 파일과 바이트 단위 일치 (51 paths);
    `npm run generate:api` 재생성 후 `openapi.d.ts` git diff 없음
  - 프론트 (`quantpilot/apps/web`): vitest `23 passed (7 files)`, `npm run build` 성공 (26.16s)
  - 후속 커밋은 문서 2건(`docs/qp_drift_daily_workboard.md`, `docs/STATUS.md`)만 포함 —
    QP-DD-03 상태 done 반영 및 통합 체크포인트 기록. 미션 종결.
