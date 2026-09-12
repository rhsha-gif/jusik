# paper-audit 2026-09-12 — A2 원본 보고 (운영 실사용·테스트 커버리지·문서 드리프트)

에이전트: Claude Code `aorch-reviewer`, model `fable`(claude-fable-5-1), 독립 컨텍스트, 읽기 전용(검증 명령만 실행). 통합 보고서: `paper-audit-20260912.md`.

---

# A2 감사 보고: QuantPilot 모의투자 단타 CLI 하네스 (운영·문서·테스트 갭)

## 1. 한 줄 결론

오프라인 검증은 전부 통과(1,632 passed / 2 skipped, smoke·tach 통과)하지만, **숨김 프로세스 3개의 시작 실패·크래시가 원장에 아무 흔적을 남기지 않고, 프로세스가 장 마감 후에도 종료되지 않으며, 레거시(`legacy`) 전략 세대에는 `daily_loss_limit`/`peak_drawdown_limit`가 전혀 적용되지 않는데 `config`는 이를 표시한다**는 점이 운영자가 실제로 겪을 가장 큰 구멍이다. 테스트는 KIS 429/EGW00201·토큰 만료 401·거래소 휴장/반일장·flatten 완료·reporter 재시작 outbox 복구·계좌 fingerprint 불일치(신규 경로)를 덮지 않는다.

---

## 2. (a) 실행 증거

| 명령 (인터프리터 `.venv\Scripts\python.exe`) | 종료 코드 | 수치 / 소요 | 특이 출력 |
|---|---|---|---|
| `scripts\verify-paper.py` (pytest 전체 + `run_smoke`, 자격증명 제거 환경) | **0** | **1,632 passed, 2 skipped, 1 warning**, pytest 55.31s, 전체 57.7s | 경고 1건: `starlette/testclient.py:45` AnyIO `BlockingPortal` deprecation. smoke: `broker=mock`, `live_trading_enabled=false`, `operator.status=blocked`, `fallback=level5_flag_disabled`, mock fills 3 |
| skipped 2건 확인 (`-rs`) | 0 | 2 skipped | `test_kis_paper_operator_manual.py:21`, `test_kis_historical_manual.py:16` — `RUN_KIS_MANUAL_INTEGRATION=1` 수동 opt-in |
| `.venv\Scripts\python.exe -m tach check` | 1 | — | **`No module named tach`** (프로젝트 `.venv`에 tach 없음) |
| `~\.local\share\aorch-tools\.venv\Scripts\tach.exe check` (runbook 경로) | **0** | — | `[OK] All modules validated!` |
| `.venv` 패키지 확인 | — | — | **`exchange_calendars` MISSING, `websockets` MISSING**, fastapi 0.133.1, starlette 1.0.1, pydantic 2.13.5 |
| `-m quantpilot.paper --help` | 0 | 0.4s | 서브커맨드 13개, **옵션·서브커맨드 help 문자열 없음** |
| `--runtime-dir <scratch/rt-empty/ledger> --json status` (존재하지 않는 디렉터리) | 0 | 0.3s | **경고 없이 새 `experiment.sqlite3`(73,728B) 생성**, `control=stopped`, `data_mode=fixture` |
| `status` / `report` (비 JSON) | 0 | — | 한국어 렌더 정상(PowerShell UTF-8에서 확인) |
| `--json strategies` / `--json config` | 0 | — | 기본 정책 v1 출력(한도 코드와 일치) |
| `dashboard --help` | 0 | — | `--port`, `--sample-seconds`만 노출 |
| `--runtime-dir <원장 없음> dashboard --port 8799` | 2 | — | `{"status":"blocked","reason":"ledger_missing"}` — 서버 기동 전 거부 (안전) |
| `--json reconcile` (자격증명 제거) | 2 | — | `{"status":"blocked","reason":"KisPaperConfigurationError"}` — 어떤 변수가 빠졌는지 안내 없음 |
| `config --set '{"data_mode":"paper_trading"}'` (버전 없음) | 2 | — | `expected_version_required` |
| `config --expected-version 7 --set ...` | 2 | — | `policy_version_conflict` |
| `config --expected-version 1 --set '{"trade_risk":0.02}'` (한도 초과) | 2 | — | **`ValidationError`** — 어떤 필드가 왜 거부됐는지 없음 |
| `config --expected-version 1 --set '{"nonexistent":1}'` | 2 | — | `ValidationError` (동일) |
| `review-drawdown --reason short` | 2 | — | `drawdown_review_reason_required` |
| `--runtime-dir <repo>\.pytest_tmp\x status` | 2 | — | `runtime_directory_inside_repository` (파일 생성 전 거부, 안전) |
| `bogus` 서브커맨드 | 2 | — | argparse usage (stderr) |

비밀 유출: 모든 출력에서 키·토큰·계좌번호 없음. `blocked_result`(`cli.py:124-133`)가 예외 문자열을 클래스명으로 축약하므로 안전하지만 진단성이 낮다.

주의: `scripts/verify-paper.py:34-36`은 임시 디렉터리를 `~/.codex/paper-verification/pytest-*`에 만든다(스크래치패드 밖). 과제 지시대로 실행했으며 이 점을 명시한다.

---

## 3. (b) 문서-코드 드리프트

| # | 문서 위치 | 코드 위치 | 차이 |
|---|---|---|---|
| D1 | `docs/paper_intraday_runbook.md:67` "실험 자산 기준 계획 손실 0.5%, 전략 60%, 종목 25%, 동시 4종목 한도" / `config` 출력의 `daily_loss_limit=0.01`, `peak_drawdown_limit=0.05` | `quantpilot/paper/config.py:22-23` 필드 존재; 소비처는 `quantpilot/paper/intraday/controls.py:50,53,77`뿐. `runtime.py:153-163, 305-313`은 `strategy_generation == "intraday_v2"`일 때만 `loss_budget` 검사. `risk.py` 전체에 `daily_loss_limit` 참조 없음 | **`legacy` 세대(9/11 시험운영이 쓰는 "기존 세 전략")에는 일손실·누적낙폭 halt가 없다.** `config`가 값을 보여주고 `reporting.py:156-164`·대시보드가 `intraday_loss_state`를 조건부로만 표시하므로 운영자는 1% 일손실 중단이 있다고 오인할 수 있다. 문서에 "legacy 세대에는 미적용" 명시 없음 |
| D2 | `paper_intraday_runbook.md:58` "`exchange-calendars==4.13.2`가 필요한 선택 의존성(`paper` extra)" ; `scripts/setup-paper.ps1:12` | `pyproject.toml:14-19` `paper` extra = `websockets==15.0.1`, `exchange-calendars==4.13.2`, `fastapi==0.133.1`, `starlette==1.0.1` | `setup-paper.ps1`은 **`websockets`를 설치하지 않는다**. `quantpilot/paper/intraday/stream.py`가 websockets를 import하므로 `intraday_v2` 전환 시 runtime-venv에서 ImportError |
| D3 | `CLAUDE.md` Commands / `AGENTS.md:48` "`tach check`" | 프로젝트 `.venv`에 tach 없음(실행 증거) ; runbook `:83`만 절대경로 `~/.local/share/aorch-tools/.venv/Scripts/tach.exe` 제시 | CLAUDE/AGENTS의 `tach check`는 PATH 의존. 프로젝트 venv에서 그대로 실행하면 실패 |
| D4 | `docs/kis_paper_connection.md:15-16` "기존 토큰을 제공하면 재발급하지 않는다" | `check_kis_paper_connection.py:60-63`은 맞음. 그러나 CLI 경로 `quantpilot/paper/auth.py:14-15,24-31` `RefreshingClient`는 `_renew_at = clock()`으로 초기화되어 **첫 호출에 무조건 `request_access_token()`** — `KIS_PAPER_ACCESS_TOKEN`을 무시 | `Readiness`→`reconcile`→`start`를 1분 안에 연달아 실행하면 프로세스마다 토큰을 새로 발급한다. 문서(`paper_trial_20260911.md:71`, `kis_paper_connection.md:17`) "토큰 발급 제한을 피하도록 반복 스케줄링하지 않는다"의 전제가 CLI에는 성립하지 않음 |
| D5 | `paper_intraday_runbook.md:23-32` 명령 목록 | `cli.py:102-120` 서브커맨드: `review-drawdown`, `start --once`, `worker --once`, `reporter --once`, `dashboard --sample-seconds` | runbook·trial·acceptance·STATUS 상단·connection 문서 어디에도 `review-drawdown`(누적낙폭 halt 해제 유일 수단, `store.py:166-186`) 없음(`docs/intraday_redesign_report.md`에만). `--once` 플래그도 미문서 |
| D6 | `paper_intraday_runbook.md:56` "시작 실패는 `status`의 heartbeat와 incident로 확인한다" | `cli.py:233-234` `build_runtime` 실패(`paper_submission_disabled`, `unsafe_environment`, `recovery_incomplete`, `fixture_requires_injected_test_clients`, ImportError)는 `cli.py:277-279`에서 stdout JSON만 출력하고 종료. `store.put("incident")`·audit 없음 | **시작 실패는 incident로 남지 않는다.** heartbeat는 `runtime.py:76`에서 control≠stopped일 때만 기록되므로 "heartbeat 없음"은 "시작 실패"와 "정지 상태 정상 대기"를 구분하지 못함 |
| D7 | `paper_trial_20260911.md:106` "자동 Slack 실패 중지 기능은 없다" | `reporting.py:253-260`: 실패 시 `delivery_unknown`만 기록 | 일치(드리프트 아님). 다만 runbook `:77`은 이 사실을 명시하지 않음 |
| D8 | `docs/STATUS.md:372-373` 검증 블록 `--basetemp=.pytest_tmp`, `run_kis_paper_kill engage`; `AGENTS.md:39` `python -m pytest quantpilot/tests`(basetemp 없음) | `CLAUDE.md` Commands: basetemp는 실행마다 고유해야 함 | 세 문서의 검증 명령이 서로 다름. STATUS는 아직 구형 kill CLI 증거를 요구 |
| D9 | `paper_intraday_runbook.md:21` "다른 위치는 `--runtime-dir`… 저장소 내부 위치는 거절된다" | `cli.py:138-146` 거절 OK; `cli.py:175` + `store.py:41-42` `mkdir(parents=True)` | 문서에 **없는 경로를 주면 새 원장을 조용히 만든다**는 사실이 없음(실행 증거 참조). 오타로 다른 빈 원장을 보고 "정지·잔고 500만"으로 오판 가능 |
| D10 | `scripts/kis-paper.ps1:5` 기본 `RuntimeDirectory=.quantpilot\paper`, `:6` `-Python 'python'` | `paper-runtime.ps1:5-6` 기본 `.quantpilot\intraday`, runtime-venv python | 두 실행기의 기본 경로·인터프리터가 다름. `kis_paper_connection.md:48`이 Prepare를 구형이라 표기하므로 의도적이나, `Readiness`를 `-Python` 없이 실행하면 시스템 `python`(hermes venv)을 쓴다 |
| D11 | `paper_intraday_runbook.md:34` 대시보드 "5초마다 다시 읽는다" | `dashboard.py:786` 페이지 폴링 5s, 표본 수집 `--sample-seconds` 기본 10s(`cli.py:120`), 0이면 수집 중지(`dashboard.py:481`) | 표본 주기·0 옵션 미문서(경미) |
| D12 | `paper_acceptance.md:17`, `paper_trial_20260911.md:21,115`, `STATUS.md:11,25,42` | 실행 결과 1,632 | 문서마다 1,476/1,489/1,608/1,632 — 시점별 기록이므로 오류는 아니나 acceptance 문서는 갱신되지 않음(경미) |

---

## 4. (c) 테스트 커버리지 표

| 시나리오 | 있는 테스트 (`file::test`) | 없음 → 제안 |
|---|---|---|
| 프로세스 재시작 후 원장 복구 | `test_paper_experiment_ledger.py::test_pause_and_config_survive_restart`; `test_paper_review_regressions.py::test_exclusive_restart_reclaims_orphan_before_lease_expiry`; `test_paper_daily_cancel.py::test_cancel_timeout_restart_is_query_only_and_late_fill_is_applied_once`; `test_paper_recovery.py::test_restart_after_projection_commit_still_finishes_correction`; `test_intraday_v2_risk.py::test_halts_survive_restart_and_policy_changes` | (커버) |
| 토큰 갱신 실패·401 | `test_paper_token_renewal.py::test_token_renewal_does_not_retry_failed_order`(1건, 내용은 권한 차단으로 미열람); `test_paper_production_wiring.py::test_refreshed_real_client_reaches_the_closed_order_transport`(성공 경로) | **없음**: `RefreshingClient.current_client()`가 `request_access_token` 예외/`token_refresh_backoff`(`auth.py:26-27`)를 어떻게 전파하고 `runtime.cycle`이 incident로 남기는지; 만료 전 401(`KisPaperTransportError "HTTP status 401"`, `kis_paper.py:214-215`) 시 강제 재발급 없음 검증 → `test_paper_token_renewal.py::test_401_mid_life_sets_incident_and_does_not_reissue_within_backoff` |
| 레이트리밋 초과(`EGW00201`) | **없음.** 코드에도 `EGW0` 처리 없음(`grep` 무결과). `_assert_business_success`(`kis_paper.py:1145-1152`)는 `KisPaperBusinessError`; `runtime.py:519-522` 백오프는 `KisPaperTransportError`에만 적용 | → `test_paper_runtime_controls.py::test_business_rate_limit_error_backs_off_and_does_not_loop` (10초 주기 재시도 루프 방지) |
| 휴장일·반일장 | `test_paper_readiness.py::test_closed_session_defers_quotes_and_minutes`(fake 달력); `test_explicit_paper_session_authority.py::test_weekend_is_never_treated_as_an_open_session`(구형) | **없음**: `quantpilot/paper/calendar.py::Calendar`를 실제 `exchange_calendars` XKRX로 검사하는 테스트 없음(테스트 어디에도 `exchange_calendars` import 없음; `.venv`에 미설치). 반일장·연초 10시 개장에 `entry_cutoff/liquidation` 상대 시각이 맞는지 미검증 → `test_paper_calendar_xkrx.py::test_holiday_returns_none_and_half_day_uses_actual_close` (`pytest.importorskip`) |
| 부분체결 후 취소 | `test_paper_daily_cancel.py::test_partial_fill_then_confirmed_remaining_cancel_preserves_fill`, `::test_cancel_flag_without_confirmed_quantity_cannot_close_order` | (커버) |
| Slack outbox `sending`→`delivery_unknown` 복구 | `test_paper_reporting_worker.py::test_slack_uncertain_result_not_resent`(send 예외 경로만) | **없음**: reporter 재시작 시 `cli.py:210-212`의 `UPDATE outbox SET state='delivery_unknown' WHERE state='sending'` 경로 테스트 없음(`reporter_interrupted` grep 무결과) → `test_paper_reporting_worker.py::test_reporter_restart_marks_stuck_sending_rows_delivery_unknown` |
| 대시보드 `mode=ro` 강제 | `test_paper_dashboard.py::test_view_is_read_only_and_never_creates_a_ledger`(`PRAGMA data_version` 불변), `::test_serve_binds_loopback_and_holds_its_own_lock` | (커버) |
| 4개 프로세스 lock stale(죽은 PID) | `test_paper_dashboard.py:250-255`(동시 보유 충돌), `test_paper_recovery.py::test_active_owner_and_wrong_account_block_apply` | 설계상 `msvcrt.locking`(`cli.py:26-29`)은 OS가 프로세스 종료 시 해제하므로 stale PID 문제 없음. 다만 **충돌 시 운영자에게 보이는 이유가 `OSError`/`PermissionError` 클래스명**(`blocked_result`)임을 고정하는 테스트 없음 → `test_paper_cli.py::test_lock_conflict_reports_already_running` |
| 수집기 바 격리 후 해제 | `test_paper_collector.py::test_revised_completed_bar_quarantines_symbol_for_session` (`:76-89`, 다음날 해제 포함) | (커버) |
| 일별 주문 페이지네이션 실패 | `test_kis_paper_client.py::test_malformed_business_response_and_repeated_pagination_cursor_fail_closed`; `test_paper_daily_cancel.py::test_daily_query_failure_cannot_be_treated_as_empty_account`; `test_paper_reconciliation.py::test_reconcile_unresolved_queries_full_pages_and_returns_fresh_balance` | (커버) |
| 손실 한도 halt 재시작 유지 | `test_intraday_v2_risk.py::test_halts_survive_restart_and_policy_changes` | **legacy 세대는 halt 자체가 없음(D1).** → 정책 결정 필요: legacy에 적용하거나 `config` 출력·문서에서 "intraday_v2 전용"으로 표기하고 `test_paper_runtime_controls.py::test_legacy_generation_has_no_daily_loss_halt_documented` |
| `flatten` 완료 조건 | `test_paper_review_regressions.py::test_pause_preserves_pending_flatten_and_resume_stays_blocked`(상태 보존만) | **없음**: `runtime.py:298-304` 포지션·미종결 0 → `paused` 전환 + `flatten_completed` audit(`grep flatten_completed` 테스트 무결과) → `test_paper_runtime_controls.py::test_flatten_completes_to_paused_only_when_ledger_and_orders_empty` |
| `configure --expected-version` 충돌 | `test_paper_experiment_ledger.py::test_pause_and_config_survive_restart`(`:40-43` `version_conflict`, 한도 초과) | CLI 계층(`cli.py:180-182` `expected_version_required`)은 실행 증거로만 확인, 테스트 없음(경미) |
| 계좌 fingerprint 불일치 | `test_paper_recovery.py::test_active_owner_and_wrong_account_block_apply`(`recovery_account_mismatch`) | **없음**: 신규 트레이더 경로 `broker.py:50-52` `experiment_account_mismatch`(원장이 다른 계좌에 결합된 상태에서 `start`) 테스트 없음(`grep` 무결과; `test_intraday_durable_gateway.py::test_missing_account_attribution_blocks_new_profile`은 잔고 불일치) → `test_intraday_durable_gateway.py::test_bound_ledger_rejects_different_account_fingerprint` |

---

## 5. (d) 운영 위험

| # | 심각도 | 위치 | 근거 | 운영자에게 보이는 증상 | 최소 완화책 |
|---|---|---|---|---|---|
| R1 | **높음** | `scripts/paper-runtime.ps1:16-18` | `Start-Process … -WindowStyle Hidden`에 `-RedirectStandardOutput/-RedirectStandardError` 없음. 자동 재시작·감시 없음. `cli.py:277-279`의 blocked JSON과 미포착 traceback은 숨김 콘솔로 사라짐 | 시작 실패·크래시 시 아무 파일도 남지 않음. `status`의 heartbeat null만 보이고 원인 불명(D6). 3개 중 하나만 죽어도 나머지가 알리지 않음(reporter는 trader 사망을 감지하지 않음, `reporting.py:33-36`는 reporter 자신의 heartbeat만 반영) | 실행기에 stdout/stderr를 원장 디렉터리 `logs\<role>-<date>.log`로 리다이렉트; `build_runtime`/lock 실패 시 `store.audit("start_failed", …)` 기록; 대시보드/리포터에 "trader heartbeat 180초 초과 → outbox incident" 추가 |
| R2 | **높음** | `cli.py:246-251` `while True: runtime.cycle(); sleep` ; `runtime.py:80-82,127-142` | 장 마감 후 `closed`/`postclose`만 반환하고 루프는 무한. control이 `running`이면 **다음 거래일에 자동으로 신규 진입 재개**. `paper_trial_20260911.md:100-101`은 장후 수동 Pause를 요구 | 시험운영 하루 뒤 Pause를 잊으면 다음날 무인 운용. 절전 복귀 후에도 그대로 재개 | 정책에 `session_scope_date` 또는 `auto_pause_after_close` 도입: postclose 처리 후 `store.control("pause")` 기본값, 명시적 `resume`로만 다음날 진입 |
| R3 | **높음** | D1 (`runtime.py:153-163`, `controls.py`) | legacy 세대에 일손실/누적낙폭 halt 없음. 거래당 손실·종목·전략 cap만 있음(`risk.py:88-131`) | 연속 손실 4종목×0.5%(시험 프로필은 1종목×0.1%)이 하루에 반복 진입될 수 있음. 대시보드 "손실 예산" 타일이 비어 있어 한도가 없다는 사실을 알기 어려움 | legacy에도 `loss_budget` 적용하거나, `config`/`status`/대시보드에 `loss_limits_enforced: false` 명시 |
| R4 | 중간 | `auth.py:14-15,24-31`; `cli.py:65`; `recovery.py:46`; `jobs/check_paper_readiness.py` | 프로세스마다 토큰 즉시 재발급(D4). KIS 토큰 발급 제한에 걸리면 `KisPaperBusinessError`/`TransportError`로 `token_refresh_backoff` 60s(`auth.py:26-27`) | Readiness→reconcile→Start 연속 실행 시 trader가 첫 사이클부터 `execution_reconciliation_required` incident(`runtime.py:504`) + Slack 알림 | `KIS_PAPER_ACCESS_TOKEN`이 있으면 초기 `_renew_at`을 만료 시각으로; 프로세스 간 토큰 파일 공유는 비밀 저장이므로 지양하고, 실행기 순서 안내(1분 간격)만 문서화 |
| R5 | 중간 | `runtime.py:519-522` | 백오프는 `KisPaperTransportError`에만. `KisPaperBusinessError`(EGW00201 등)는 10초 후 재시도 | 레이트리밋 상황에서 10초마다 재요청·audit `cycle_failed`(60초 dedupe) 반복. 사이클마다 토큰이 아닌 조회 요청이므로 429 지속 | 비즈니스 오류 코드가 `EGW`로 시작하면 동일 백오프 적용 + 테스트(4c) |
| R6 | 중간 | `cli.py:175`, `store.py:41-42` | 없는 `--runtime-dir`는 즉시 새 원장 생성(실행 증거) | 경로 오타 → "잔고 500만·stopped" 오판, 잘못된 위치에 원장 파편 | 읽기 명령(`status/report/config/strategies`)은 `experiment.sqlite3` 부재 시 `ledger_missing`으로 거부; 생성은 `config`/`start`에만 허용 |
| R7 | 중간 | `cli.py:124-133`, `dashboard.py:522`, `runtime.py:398` | 오류를 예외 클래스명으로만 축약. `ValidationError`·`KisPaperConfigurationError`·`PermissionError`·`ModuleNotFoundError` | 어느 필드/환경변수/락이 문제인지 모름(실행 증거). 예: 이미 실행 중인 trader와 충돌하면 `PermissionError` | 비밀이 없는 안전 코드(pydantic `loc`, 누락 env **이름**, 락 파일명)를 화이트리스트로 노출 |
| R8 | 중간 | `paper_intraday_runbook.md:56` "PC 종료·절전 중에는 실행되지 않는다" ; `paper_trial_20260911.md:29` | 절전 복귀 시 처리 코드 없음. `Session.lease_expires_at` 5분(`broker.py:67-69`)은 매 사이클 갱신되므로 복귀 후 자동 재개; 미종결 주문은 `age >= 60`(`runtime.py:167`)로 즉시 취소 시도 → 취소 후 대사. 네트워크 단절은 `KisPaperTransportError` 백오프 최대 60s | 절전 복귀 직후 "오래된 미종결 주문" 경고 + 취소 시도 + `cancel_reconciliation_required` 가능. 복귀 감지 로그 없음 | 사이클 간 시계 점프(> 2×cycle_seconds) 감지 시 audit `clock_gap` 기록 후 첫 사이클은 대사만 수행 |
| R9 | 낮음 | `cli.py:213-220` | reporter가 1초마다 `reporter_heartbeat` put(WAL+`synchronous=FULL`) | 하루 ~86,400 회 쓰기; 대시보드 `mode=ro` 읽기와 WAL 경합 → `ledger_busy` 503 간헐 | 주기 10초 |
| R10 | 낮음 | `runtime.py:186-191`, `store.py:114-118` | 미검증 포지션이 있으면 사이클마다 `position_awaiting_reconciliation` audit. 로그 회전·크기 제한 없음(원장 audit 테이블이 유일한 로그) | 하루 8,640행/종목. 대시보드는 접어서 표시(`dashboard.py:289-292`) | 동일 사유 audit은 60초 dedupe(`cycle_failed`와 동일 방식) |
| R11 | 낮음 | `store.py:38-85` | 새 원장의 `initial_capital`·`cash`가 `Policy.initial_capital`과 별개로 하드코딩 5,000,000 | (현재 값 동일) | 상수 단일화 |
| R12 | 정보 | 시험운영표 `paper_trial_20260911.md:30` "주문·취소·청산 미실행·미검증" | fake 전송으로 `test_paper_production_wiring.py`, `test_paper_daily_cancel.py`가 존재. 실제 KIS는 수동 항목. `:29` "PC 절전"은 코드 무근거(R8). `:25` 분봉·호가 `pending_open_session`은 `check_paper_readiness.py:101-116,145` 종료 코드 1로 뒷받침 | — | — |

---

## 6. (e) 구형 Level-5 경로 판정

| 모듈 | CLI 하네스와 중복 | 유일하게 제공하는 기능 | 의존 테스트·문서·경계 | 삭제 시 깨질 import (`grep`, 비테스트) | 판정 |
|---|---|---|---|---|---|
| `quantpilot/jobs/run_kis_paper_session.py` (1,050줄) | 세션·대사·제출 흐름이 `paper/runtime.py`+`broker.py`와 기능 중복 | Level-5 승격 사다리(`validated_l5`)·fingerprint allowlist·`run_mode=paper_submit` 통한 `OperatorService` 경로 | `test_kis_paper_session_job.py`(≈15 test); `docs/operator_runbook.md:36`, `STATUS.md:110`; `kis-paper.ps1:14,26`는 `KIS_PAPER_SESSION_ENABLED`만 false로 고정 | 비테스트 importer **없음** | **폐기 후보** (문서 2곳·env 이름 정리 동반). `paper_retirement_inventory.md:10`도 "호환성 검토 후 별도 폐기" |
| `quantpilot/jobs/run_kis_paper_kill.py` (271줄) + `packages/core/execution/paper_kill.py` (582줄) | CLI에 대응 명령 없음(`pause/flatten`은 원장 의도일 뿐 durable fence 아님) | **durable paper kill fence.** 새 CLI가 재사용하는 `DurablePaperSubmissionCoordinator`가 `paper_kill_blocks_submission()`을 매 단계 확인(`paper_submission.py:356,645,725,783`) → 이 잡이 `<runtime>/broker.sqlite3`(`KIS_PAPER_STATE_DB`)를 가리키면 CLI 트레이더의 POST를 차단할 수 있는 **유일한 킬 스위치** | `test_kis_paper_kill_job.py`(3), `test_paper_kill_persistence.py`; `roadmap_acceptance_matrix.md:68`, `STATUS.md:109,373` | `run_kis_paper_kill.py` → `paper_kill.py`만; 다른 importer 없음 | **유지 → 부분 이관.** `python -m quantpilot.paper kill --confirm` 형태로 CLI에 편입하고 runbook에 기재 권장. 단, CLI `broker.sqlite3`의 provenance/fingerprint 검사를 kill 잡이 통과하는지는 **미검증** |
| `quantpilot/packages/brokers/kis_paper.py` (246줄) | CLI는 `KisPaperClient`를 직접 사용(`cli.py:53-57`). `KisPaperBrokerAdapter`는 미사용 | `harness_service.py`(API)의 브로커 어댑터, `PaperPortfolioLossMetrics` | `test_kis_paper_broker_adapter.py`; `tach.toml:78` unchecked | `packages/core/harness_service.py`, `packages/core/operator/paper_loss.py` | **유지**(API·harness_service 은퇴와 동시 폐기) |
| `packages/core/operator/position_ledger.py` (1,630줄) | 중복 아님 — 새 CLI 커널의 데이터 모델 | `PaperStateStore`·execution kernel의 세션/디스패치 모델 | 다수 | `execution/{paper_submission,paper_reconciliation,paper_reconciliation_apply,reducer,events,state_machine,transitions,paper_kill}.py`, `db/sqlite_repositories.py`, `db/paper_status_reader.py`, `risk/{gatekeeper,batch}.py` | **유지 (핵심 의존)** |
| `packages/core/operator/status_snapshot.py` (835줄) | 부분 중복(`paper/reporting.snapshot`) | `PaperStatusReader`가 import → `paper/recovery.py:19,77-79`가 `reconcile` 미리보기에 사용 | `test_professional_operator_status_snapshot.py`, `test_paper_status_reader.py` | `db/paper_status_reader.py`, `services/api/*` | **유지** |
| `packages/core/operator/{service,professional_cycle,retirement,paper_loss,reporting,schemas}.py` (≈3,900줄) | Level-5 사이클·리타이어먼트·보고 — CLI 하네스는 미호출(`quantpilot/paper`에서 `core.operator` import 0건) | Level-5 authority/professional cycle, API `/api/operator/*` | `test_level5_*`, `test_professional_*`, `integration/test_level5_operator_run_once.py`(25), `docs/safety_checklist.md`, `operator_runbook.md`; `tach.toml:62` unchecked | `services/api/routers/operator.py`, `services/api/dependencies.py`, `packages/core/harness_service.py`, `jobs/run_kis_paper_session.py`, `jobs/record_paper_loss_baseline.py` | **부분 이관/폐기 후보** — API·harness_service 유지 여부 결정이 선행. `paper_retirement_inventory.md:14` "API 서버와 스키마는 CLI 커널 의존성 확인 전까지 보존" 조건: CLI 커널은 `core.operator.position_ledger`·`status_snapshot`에만 의존하므로 나머지는 API 은퇴 시 함께 제거 가능 |

`run_smoke`(검증 필수)는 `harness_service`→`OperatorService`를 거치므로(`smoke` 출력 `operator.fallback=level5_flag_disabled`) operator 패키지 폐기 시 smoke·`safety_checklist.md` 기준도 함께 재정의해야 한다.

---

## 7. 읽지 않은 범위와 미해결 위험

- **권한 차단으로 미열람**: `quantpilot/tests/unit/test_paper_token_renewal.py`(파일명 deny rule; `--collect-only`로 테스트 1건 `test_token_renewal_does_not_retry_failed_order`만 확인), `.env.example`(deny rule; `test_env_example_sync.py`가 런타임 env 읽기와 동기화를 강제함은 확인).
- **미열람 코드**: `quantpilot/paper/intelligence.py`(sanitized env 함수 `:309-320` 존재만 확인), `lab.py`, `research.py`, `strategy.py`, `intraday/*`(controls.py 제외), `packages/core/kis_paper.py` 대부분(오류 처리·토큰·페이지네이션 라인만), `execution/paper_submission.py` 본문, `run_kis_paper_session.py` 본문.
- **실행하지 않음**: `start/worker/reporter/resume/flatten/reconcile --apply`, `paper-runtime.ps1`, `kis-paper.ps1`, runtime-venv의 존재·패키지 상태(`~/.quantpilot` 금지). 따라서 runtime-venv에 `exchange_calendars`/`websockets`가 실제로 있는지는 미확인(설치 스크립트 기준 추정만).
- **미해결**: (1) 실제 XKRX 달력 기반 휴장/반일장 동작 — 테스트 부재이며 `.venv`에 라이브러리가 없어 본 감사에서도 검증 불가. (2) 구형 kill 잡이 CLI의 `broker.sqlite3`를 실제로 fence할 수 있는지(provenance 검사 통과 여부). (3) 절전 복귀 시 미종결 주문 즉시 취소 흐름의 실제 KIS 응답. (4) reporter 1초 쓰기와 대시보드 ro 읽기의 실측 경합 빈도.
