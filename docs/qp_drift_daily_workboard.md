# QP-DRIFT-DAILY Workboard — DriftMonitor daily revaluation + fee-aware PnL

## Document edit lease

- Lease status: `held`
- Document editor: `Claude Code`
- Mission/task ID: `QP-DRIFT-DAILY`
- Acquired at: `2026-07-12 KST`

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
| `QP-DD-01` | Claude Fable 5 | Codex (read-only) | `QP-DD-00` | `주식트레이더-drift-daily` / `claude/qp-drift-daily-01` | `quantpilot/packages/core/harness_service.py`, `quantpilot/packages/core/schemas.py`, `quantpilot/tests/unit/test_strategy_drift.py`, `quantpilot/tests/unit/test_strategy_performance_feed.py`, `openapi.json`, `quantpilot/apps/web/src/lib/openapi.d.ts`, `docs/STATUS.md`, this workboard | in_progress | 일별 종가 재평가 + 비용 반영 + 후방호환 스키마, 신규 테스트, 전체 검증 통과 | |
| `QP-DD-02` | Claude Fable 5 (lead) | — | `QP-DD-01` | main tree | mainline integration | pending | 전체 검증 재실행 후 main 병합, STATUS/작업보드 갱신 | |

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

## Checkpoint log

- `2026-07-12` — Claude Fable 5 — 미션 수임, 설계 확정, worktree `claude/qp-drift-daily-01` 생성, Codex 읽기 전용 분해 검토 요청 발송.
- `2026-07-12` — Claude Fable 5 — Codex CLI가 `~/.codex/config.toml` 오류로 차단 → 독립 컨텍스트 fallback 검토자 투입 (QP-WORKFLOW-DOC 선례).
- `2026-07-12` — Claude Fable 5 — 구현 완료: fee-aware daily-close 재평가, 스키마 필드 2개, 신규 테스트 7개. openapi.json 재생성 시 기존 스테일 발견(`/api/operator/professional-status` 누락, 51 paths로 보정).
- `2026-07-12` — fallback reviewer — APPROVE-WITH-CHANGES (F1~F6); 필수 F1·F6 및 권고 F2·F3 반영, F4는 후속 항목.
