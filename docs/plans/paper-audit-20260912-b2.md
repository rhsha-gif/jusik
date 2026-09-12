# paper-audit 2026-09-12 — B2 원본 보고 (단타 봇 운영·전략 층 OSS 대조)

에이전트: Claude Code `aorch-analyst`, model `fable`(claude-fable-5-1), 독립 컨텍스트, 읽기 전용. 통합 보고서: `paper-audit-20260912.md`.

---

## B2 보고: KIS 오픈소스 봇 대비 운영·전략 층 갭

경로 약어:
- `<qp>` = `quantpilot\paper`
- `<b2>` = 세션 스크래치패드 `scratchpad\b2` (클론 4개: `koreainvestment-autotrade`, `ante`, `auto_trade`, `RoboTrader`)

### 1. 한 줄 결론

가장 중요한 갭 3개: (1) **운영 감시 부재** — trader 하트비트가 장중에 끊겨도 DM이 가지 않고, 자동 재시작·장후 보고 누락 감지가 없다(`paper-runtime.ps1`은 `Start-Process`만 함); (2) **휴장일 이중 확인 부재** — `exchange-calendars` 단일 소스이고 KIS `chk-holiday` 대조가 없다(2026-09-24/25 추석 임박); (3) **유니버스 품질 필터 부재** — 거래량순위 상위 N을 그대로 쓰고 가격대·등락률·거래대금·우선주/ETF 제외가 없다(특히 네이버 폴백 경로). 그리고 커넥터 담당자에게 넘길 미확인 1건: ante가 문서화한 **KIS 모의 `inquire-daily-ccld` 당일 지연(레거시 tr_id에서 당일 0건)** 이 우리 대사 경로(`broker.py` `get_daily_orders_and_fills`)에 해당하는지.

### 2. 후보 표

| 저장소 | URL | 명시 라이선스 | 최근 push | 활동 신호 | 선택 | HEAD |
|---|---|---|---|---|---|---|
| tgparkk/RoboTrader | https://github.com/tgparkk/RoboTrader | LICENSE 파일 없음(README "교육·연구 목적") | 2026-06-04 | ★2, 실거래 매매일지·incident 주석 다수, 3,198줄 main | **선택** (KIS 단타 실운용 루프, 우리와 가장 근접) | `16712489e1cd6cc4173a808369b2b00d28f81da5` |
| joshua-jingu-lee/ante | https://github.com/joshua-jingu-lee/ante | `LICENSE` (API: MIT) | 2026-09-07 | ★4, 671 py, spec 문서 200+, CI | **선택** (전략·스케줄러·리스크·대사 층만) | `69492e61e16b76ca72b1c36cc608cd93f28777c5` |
| youtube-jocoding/koreainvestment-autotrade | https://github.com/youtube-jocoding/koreainvestment-autotrade | 없음 | 2023-01-28 (커밋 2022-05) | ★61, 포크 57, 단일 파일 277줄 | **선택** (원형 루프, 대조군) | `8eabb7e8e79e2f66658d39936038f9195283396b` |
| tofulim/auto_trade | https://github.com/tofulim/auto_trade | `LICENSE` (API: MIT) | 2026-05-09 | ★20, Airflow+FastAPI | 클론했으나 **비교 축에서 제외**: 장중 루프 없음(cron 08:30/18/19/20시 예약주문, 월 1회 적립) | `5aca8374ca10692e2b9536e4de0759f8ad8011a9` |
| Soju06/python-kis | https://github.com/Soju06/python-kis | MIT | 2026-02-21 | ★288 | 미선택: 라이브러리, 예제 봇 없음(`tests/.env.sample`뿐) | - |
| koreainvestment/open-trading-api | https://github.com/koreainvestment/open-trading-api | API상 없음 | 2026-08-26 | ★1,602 | 미선택: 함수 예제 모음, 자동매매 루프 없음 | - |
| koreainvestment/kis-ai-extensions | https://github.com/koreainvestment/kis-ai-extensions | 없음 | 2026-06-24 | ★212 | 미선택: 에이전트 스킬 묶음, 봇 아님 | - |
| geongi-im/kis-us-auto-trading | https://github.com/geongi-im/kis-us-auto-trading | 없음 | 2025-10-20 | ★6 | 미선택: 미국주식 | - |
| leebyeungkok/AutoStock | https://github.com/leebyeungkok/AutoStock | 없음 | 2026-05-17 | ★6, 파일 3개 | 미선택: 볼린저 샘플 수준 | - |
| pjueon/pykis, kenshin579/korea-investment-stock | (생략) | Apache-2.0 / MIT | 2022 / 2026-09 | ★57 / ★0 | 미선택: 래퍼 | - |

검색 한계: `gh search repos`의 한국어 질의는 대부분 0건을 돌려줘 WebSearch·토픽 페이지에 의존했다. 미발견 저장소가 있을 수 있다.

### 3. 축별 비교표

| 축 | 우리 | RoboTrader | ante | jocoding | 차이·판정 |
|---|---|---|---|---|---|
| 1 스케줄링 | `<qp>\calendar.py:26-41` XKRX 세션만; `runtime.py:80-82` 폐장이면 즉시 return, `cli.py:247-251` 10초 sleep 고정; preopen 증거 `runtime.py:86-99`; 진입 종료 closes-30m·청산 closes-20m `runtime.py:143-150`; 마감 후 격리 `:127-142`; 재시작 자동화 없음(`docs\paper_intraday_runbook.md:56`) | `config\market_hours.py:15-54` 하드코딩 공휴일 + `:71-83` 특수일(수능일 10시 개장) + `utils\holiday_kis_sync.py:47-91` KIS chk-holiday 일 1회 동기화(fail-open); 매수 컷오프 12시, 15:00 시장가 일괄청산 `:67-69`; 프리마켓 08:00-08:55 태스크 `main.py:1373-1497`; 폐장 시 60초 sleep `main.py:433-435`; 장 마감 1~15분 후 저장, 15:45 확대수집 `main.py:1706-1772` | `rule\global_rules.py:181-303` 계좌 IANA 시간대의 HH:MM 창(휴일 미인지), `rule\defaults.py:32-35` kis-domestic에 기본 주입(장종료 시 시장가 반복→KIS 40580000 회귀 기록); 일일 보고 `trading_hours_end+30m` `trade\daily_report.py:34-45` | `KoreaStockAutoTrade.py:228-275` 09:00-09:05 잔량 매도, 09:05-15:15 매수, 15:15-15:20 일괄매도, 15:20 종료, 주말 종료; tz 없는 `datetime.now()` | 우리 달력이 가장 정확하나 **단일 소스**. KIS chk-holiday 대조는 직접 구현. 특수일 개장시각은 xcals 지원 여부 미확인. 09:00 직후 회피는 우리 전략이 구조적으로(ORB 15봉·v2 16개 5분봉) 만족. **15:20-15:30 동시호가 중 60초 취소·재주문 루프**(`runtime.py:165-176`)는 검토 필요 |
| 2 유니버스 | `data.py:138-163` 거래량순위 상위 N(`FID_TRGT_EXLS_CLS_CODE="1111111111"`), 폴백 네이버 시총 리스트 거래대금 정렬 `:164-190`, 5분 갱신 `collector.py:33-39`; v2는 `intraday\universe.py` 적격성 메타 | `core\stock_screener.py:118-254` 3단계: 거래금액순 KOSPI/KOSDAQ(가격대 API 필터) → 등락률 0.5~5%·가격 5천~50만·거래대금 10억·우선주/ETF/ETN/스팩/리츠 이름·코드 끝자리 5 제외 → 시가대비 0.8~4%·갭 필터; 일일 상한 15, 09:01~11:50 2분 주기; `:125-129` "거래증가율 정렬 제거: 시뮬-실거래 종목풀 79.5% 불일치 원인" | 전략 메타 고정 심볼(`strategies\_template.py:25`), 스크리너 없음 | 고정 4종목 | 우리 기본 경로는 KIS 제외 플래그에 의존(비트 의미 미검증), 폴백 경로는 필터 전무. Phase 2 로직 **차용**(같은 KIS 필드 `prdy_ctrt`,`stck_prpr`,`acml_tr_pbmn` 사용) |
| 3 포지션 관리 | 손절/목표/시간/추세이탈 `runtime.py:203-287`, 트레일링 `intraday\strategy.py:179-199`; 일 1%·누적 5% halt `intraday\controls.py:10-95`; 동시 2/4 `config.py:21,41-44`; 재시작 복구 = 원장 + 대사(`broker.py:113-234`, `recovery.py`), 잔고 불일치 → `unverified_symbols` 진입 차단 | 매수가 대비 % 손익절 `trading_decision_engine.py:693-755`; 프리마켓 심리로 최대 종목·손절 조정 `:817-848`; 25분 재매수 쿨다운 `models.py:264-285`; 자금 예약 `fund_manager.py:107-186`; **재시작 = 잔고 조회로 POSITIONED 생성 + 합성 BUY 기록** `main.py:2894-3145`; 종목별 복구 3회 서킷 `:3045-3061` | 룰엔진: 일손실(매도는 항상 허용) `global_rules.py:31-105`, 총노출, 포지션 크기·미실현손실·시간당 거래빈도 `strategy_rules.py`; 30분 주기 대사 `broker\scheduler.py:21,72-89`(비실행 봇은 detect-only); 미귀속 보유 detect-only `trade\reconciler.py:24-31` | 없음(전량 매도만) | 리스크 한도는 우리가 동등 이상. 없는 것: **동일 종목 재진입 쿨다운**, **연속 손실 N회 당일 중단**, 시장 레짐 게이트. 잔고→포지션 생성은 불변식 충돌 |
| 4 체결 추적 | 매 사이클 `reconcile`(잔고+일별주문) `broker.py:113-234`; 60초 경과 미체결 취소 `runtime.py:164-176`; 취소는 claim 후 query-only `broker.py:407-450`; 부분체결은 누적 증거로 원장 반영 | 3초 폴링 `order_manager.py:253-264`; 매수 300s/매도 180s + 3분봉 4개 타임아웃 `:295-306`; 체결 오탐 10분 재검증 `:314-362`; 상태 불명 5분 → TIMEOUT `:443-452`; **취소 실패 시 로컬 강제 정리** `:563-572`; 부분체결은 PARTIAL 표시만 `:525-540`; pending이 **메모리 dict** `:25-27` | 백스톱 폴 ≥60s event-gated `broker\fill_scheduler.py:1-29,58-59`; **KIS 모의 당일 ccld 지연 → 잔고 증분 역도출 fallback** `:17-27`; transactional outbox `trade\fill_outbox.py`; EOD `expire_stale`은 체결 미관측 open만 `trade\order_tracker.py:612-646`; 회로차단기 `broker\circuit_breaker.py` | 응답 `rt_cd` 만 확인, 추적 없음 | 우리가 가장 보수적. **차이**: 우리는 매도도 60초마다 취소·재주문(횟수 상한·알림 없음). ante의 KIS 모의 ccld 지연 발견은 우리 대사 전제와 직결 → 커넥터 담당 검증 |
| 5 알림 | Slack DM outbox at-most-once `reporting.py:195-263`; incident 알림 일·코드 키 dedup `runtime.py:37-52`; 장후 보고는 AI 복기 후 enqueue `jobs.py:120-122`; 복구 정정 보고 `reporting.py:265-283`; 응답 유실 재전송 없음 | Telegram: 주문접수/체결/취소/신호/오류/30분 주기 상태/일일요약 `telegram_integration.py:158-360`; 인바운드 `/status /positions /orders`(README) | 단일 `NotificationEvent` 라우터: min_level·quiet hours·60초 dedup·CRITICAL 항상 `notification\service.py:35-61,130-159,265-293`; 일일 보고는 시간 기반, 거래 0건이면 이벤트만 `daily_report.py:227-232`; Telegram 인바운드 `/status`, `/stop` 2단계 확인 `telegram_receiver.py:23,379-384` | Discord webhook 즉시 POST(재시도 없음) | 없는 것: **장중 하트비트 정지 알림**, **시간 기반 장후 보고 누락 감지**(우리는 trader가 postclose 사이클에 도달해야 보고), 체결·취소 단위 알림, 레벨/무음. 인바운드 명령은 제외 권고 |
| 6 로깅·장애 | `logging` 미사용(grep 0건); stdout JSON은 숨김창에서 유실; sqlite `audit`; 전송 오류 지수 백오프 ≤60s `runtime.py:519-522`; 사이클 예외 → incident + 진입 차단 `:502-528`; 킬스위치 = `review-drawdown --reason` 감사 기록 `cli.py:196-202`, `store.py:166-186` | 일자 파일 로그(회전 없음) `utils\logger.py:37-72`; asyncio 태스크 감시·재시작 10초 `main.py:353-368`; PID 파일 중복 방지 `:232-262`; 디스크 JSON 킬스위치(누적 -5%·5연패, 삭제 후 재시작으로 복구) `:747-848`; 전일 지수 -3% 서킷 `:850-884`(데이터 없으면 차단 안 함); 24h API 재초기화 `:2448-2466` | systemd `Restart=on-failure` + 하드닝 `deploy\ante.service`; 봇 auto_restart·cooldown·max attempts `bot\config.py:57-62`, `bot\manager.py:1259-1321`; step timeout·연속 3회 실패 → ERROR `bot\bot.py:139-208`; JSONL KST 자정 회전 `core\log\handlers.py`; readiness fail-closed `account\readiness.py` | `try/except` 한 번, 오류 시 종료 | 없는 것: **프로세스 감시·재시작**, **파일 로그**, 시장 레짐 서킷. 킬스위치는 우리가 더 낫다(감사·원장 내) |
| 7 백테스트·설정 | `Policy` 버전·상한 고정 `config.py:12-54`, `config --expected-version`; `intraday\replay.py:20-29`가 실매매 `evaluate`·`VALIDATED_POLICY`·구현 해시 공유; 안전 플래그 4개+`BROKER_MODE` fail-closed `config.py:63-73` | Python 클래스 상수 `config\strategy_settings.py` + `trading_config.json`; 시그널 식 단일 모듈 공유 `core\strategies\macd_cross_signal.py:1-8`; 실데이터 신호 리플레이·미충족 조건 설명 `utils\signal_replay.py:29-36`; `VIRTUAL_ONLY`/`PAPER_STRATEGY` 플래그 | 동일 `Strategy` 클래스를 백테스트 실행 `backtest\executor.py`, `docs\specs\strategy\03-15`; `Account.trading_mode` VIRTUAL/LIVE `account\models.py:20-24` + 가상 실행기 `bot\providers\virtual.py`; 전결 승인 설정 `config\system.toml.example:33-40` | `config.yaml`의 URL 주석 토글 | 코드 공유는 동등. 참고할 것: RoboTrader의 "특정 시각에 왜 신호가 없었나" 설명형 리플레이(우리 replay가 이를 제공하는지 미확인) |
| 8 전략 아이디어 | ORB·추세되돌림·레인지회귀(+v2 VWAP 목표) | `backtests\strategies\`: vwap_bounce, bb_lower_bounce, gap_down_reversal, gap_up_chase, volume_surge, rsi_oversold, limit_up_chase, intraday_pullback(EMA20), orb, closing_drift·close_to_open·macd_cross(오버나이트); `core\indicators\pullback\` 눌림목 캔들 패턴(저거래 3봉·회복양봉·이등분선); `config\dynamic_profit_loss_config.py` 패턴별 손익비 | `strategies\_examples\` ma_crossover, rsi_mean_reversion, volume_breakout(일봉) | 변동성 돌파 `KoreaStockAutoTrade.py:65-85` | 이름·위치만 기록. 오버나이트 계열은 당일 청산 원칙과 충돌 |

### 4. 우리에게 없는 기능 (우선순위 순)

| id | 기능 | 상대 위치 | 우리 부재 확인 | 운영 영향 | 불변식 충돌 | 판정 + 근거 |
|---|---|---|---|---|---|---|
| G1 | 장중 trader/collector 하트비트 정지 시 DM 알림 | RoboTrader `core\telegram_integration.py:342-360`(30분 주기 상태), ante `account\readiness.py` | `reporting.py:102-106`은 `trader_unavailable`을 **status 조회 시에만** 계산; reporter 루프 `cli.py:206-220`는 outbox만 배출; runbook `:56` "PC 절전 중 실행 안 됨" | 절전·크래시 시 보유 포지션 보호가 조용히 멈춤(운영표 `paper_trial_20260911.md:106` 수동 Pause 의존) | 없음 | **직접 구현**: reporter 루프에서 세션 중 `heartbeat` 나이 > 180s면 `store.enqueue("liveness:<day>:<slot>", ...)` (~15줄). 상대 코드는 asyncio 단일 프로세스 전제라 이식 불가 |
| G2 | 프로세스 감시·자동 재시작 | ante `deploy\ante.service`(systemd), `bot\config.py:57-62` 재시작 상한·쿨다운; RoboTrader `main.py:353-368` 태스크 감시 | `scripts\paper-runtime.ps1:16-18` `Start-Process` 3회뿐; 재시작 경로 없음 | 위와 동일 | 재시작 자체는 기존 `trader.lock`·`recover_exclusive_owner`(`cli.py:81-90`)가 처리하므로 충돌 없음. 단 `recovery_pending_since`·incident 상태에서는 재시작하지 않아야 함(`cli.py:40-41`) | **직접 구현**: Windows 예약 작업(기존 `scripts\register-market-brief-task.ps1` 패턴) + 하트비트 검사 스크립트. systemd 유닛은 플랫폼 불일치로 제외 |
| G3 | 시간 기반 장후 보고 누락 감지 | ante `trade\daily_report.py:270-291` (16:00 KST 독립 스케줄, 거래 0건도 이벤트 발행) | `jobs.py:17-36`는 `ai_due` postclose 키가 있어야 동작하고, 그 키는 trader의 postclose 사이클 `runtime.py:139-141`이 넣음 → trader가 죽어 있으면 보고 자체가 없음 | 보고 부재를 "조용함"으로 오해 | 없음 | **직접 구현**: worker/reporter가 `session.closes+30m` 이후 `last_close_day != today`면 `postclose_missing` DM enqueue |
| G4 | 휴장일 KIS `chk-holiday` 대조 | RoboTrader `utils\holiday_kis_sync.py:47-91`, `config\market_hours.py:185-194` | `calendar.py:26-41` xcals 단일; runbook `:58` "거래소 공지와 수동 대조" | 달력 오류 시 폐장일에 주문 시도 또는 개장일에 휴면(RoboTrader 주석 `market_hours.py:218` "휴일 미체크→33,798회 재시도" 회귀 사례) | 상대는 fail-open(`:36-40` 캐시 미스→False, 예외→기존 로직); 우리는 불일치 시 fail-closed여야 함 | **직접 구현**(설계만 차용): readiness 또는 collector에서 하루 1회 `chk-holiday` 조회 → xcals와 불일치면 `incident`. API 호출 함수는 커넥터 담당 |
| G5 | 유니버스 품질 필터(가격대·등락률·거래대금·우선주/ETF/스팩 제외) | RoboTrader `core\stock_screener.py:180-254` | `data.py:158-163` 원본 순위 그대로; 폴백 `:167-190` 무필터; `intraday\universe.py`는 메타 존재 시에만 | 저가주·급등주·ETF가 후보에 섞임; 폴백 시 특히 | 없음 | **차용**(로직 20줄): 같은 KIS 필드명 사용. 상수는 `Policy` 필드로 상한 고정. 상대의 in-process 일일 상한/거부 기억은 `store`로 옮겨야 함 |
| G6 | 동일 종목 재진입 쿨다운·일 1회 진입 상한 | RoboTrader `core\models.py:264-285`, `main.py:1189-1193`, `_has_buy_today` `:712-716` | `strategy.py:482-519` `occupied`는 보유·미체결만; `risk.py:85-89` 동일. 손절 직후 다음 분 재진입 가능 | 손절 반복(whipsaw) 비용 | 없음 | **직접 구현**: `trades` 테이블에서 최근 청산 시각 조회해 `entry_size`에서 0 반환 |
| G7 | 시장 레짐 게이트(전일 지수 -3%·장중 급락 시 신규 진입 차단) | RoboTrader `main.py:850-923`, `core\pre_market_analyzer.py`(미독) | `runtime.py:146-163` `no_entries` 조건에 시장 상태 없음 | 급락장 매수 | 상대는 데이터 없으면 차단 안 함(`:868-869`); 우리는 차단이어야 함 | **직접 구현**(지수 일봉 조회 API 필요 → 커넥터 담당). NXT 프리마켓 심리는 제외(별도 데이터원·복잡) |
| G8 | 파일 로그(회전) | ante `core\log\handlers.py`(stdlib만 의존, 154줄), RoboTrader `utils\logger.py` | `quantpilot\paper`에 `logging` 없음; `cli.py:277-279` 예외는 JSON 한 줄 stdout → 숨김창 유실 | 크래시 원인 추적 불가 | 없음 | **차용 가능(선택)**: ante 핸들러는 `zoneinfo`·`logging.handlers`만 의존, prefix/dir만 바꾸면 됨. 다만 stdlib `TimedRotatingFileHandler` 3줄로 충분해 직접 구현 권고 |
| G9 | 연속 손실 N회 당일 진입 중단 | RoboTrader `core\performance_gate.py:193-234`(연패), `main.py:799-848`(누적 -5%·5연패 킬) | `intraday\controls.py:47-54` 금액 기준만 | 손실 예산 소진 전 조기 정지 | 없음 | **직접 구현**(선택): `trades` 집계. 롤링 승률 게이트는 제외(상대도 `strategy_settings.py:213` ENABLED=False) |
| G10 | 청산 주문 취소·재주문 반복 상한과 에스컬레이션 | (상대는 시장가로 회피: RoboTrader `trade_executor.py:159-166`) | `runtime.py:165-176` 60초마다 취소, `sell_quantity`가 다음 사이클 재주문; 횟수 상한·알림 없음 | 유동성 없는 종목에서 무한 루프, 동시호가 구간 churn | 시장가 금지 유지 | **직접 구현**: 종목당 연속 취소 횟수 감사 후 N회 초과 시 `incident(block=False)` DM. 15:20 이후 취소 억제는 KIS 모의 동시호가 취소 가능 여부 확인 후 |
| G11 | 알림 레벨·무음 시간·중복 억제 | ante `notification\service.py:130-159,265-293` | outbox 키 기반 at-most-once만(`store.py:410-414`) | 현재 알림량이 적어 영향 작음 | 없음 | **제외(현 시점)**: 개념만 기억. G1~G3 추가 후 필요해지면 키 설계로 해결 |
| G12 | 텔레그램/슬랙 인바운드 원격 제어 | ante `notification\telegram_receiver.py`, RoboTrader README | reporter는 송신 전용 | 외출 중 Pause 불가 | 원장 밖 입력 경로·비밀 확대 | **제외**: 새 공격면. 필요 시 별도 보안 검토 후 allowlist 사용자 한정 |
| G13 | 잔고 증분으로 체결 역도출 | ante `broker\fill_scheduler.py:17-27` | `broker.py:145-158` 잔고는 검증 입력 | (KIS 모의 당일 ccld 지연 시) 체결 미반영으로 진입 차단 지속 | **충돌**: 잔고를 체결 권위로 승격 | **제외**. 대신 미확인 사항으로 커넥터 담당에 전달: 우리 daily-ccld tr_id가 당일 체결을 반환하는 세대인지(ante 문서 `docs\specs\broker-adapter\18-fill-recovery.md` §2.1: `VTTC8001R` 당일 0건, `VTTC0081R` 반환) |
| G14 | 미확정 분봉(volume=0) 재조회 | RoboTrader `core\data_reconfirmation.py:1-50` | `store.py:420-432` 완성봉 변경 시 예외 → `collector.py:79-88` 종목 당일 격리 | 관측이 맞다면 격리가 잦아져 유니버스 축소 | **충돌**: 상대는 덮어쓰기, 우리는 격리 | **제외**(코드). 다만 관측 자체는 검증 가치 있음: 최신 완성봉 수용을 N초 지연하거나 volume>0 확인 후 저장하는 쪽을 직접 구현 검토(커밋 592feba와 연관) |

동등 또는 우리가 우위라 갭이 아닌 항목: 일손실 한도 시 매도 허용(ante `global_rules.py:62-79` ↔ `runtime.py:143-163`), 장종료 주문 차단(`risk.py:207-208`), 킬스위치 감사(`store.py:166-186` ↔ RoboTrader 파일 삭제 복구), 체결 오탐 방지(누적 증거 대사 ↔ `order_manager.py:314-362`), 백테스트-실매매 코드 공유(`replay.py:21-29`), 페이퍼/실전 스위치 다층 fail-closed(`config.py:63-73`).

### 5. 상대 설계와 우리 설계가 적극적으로 충돌하는 지점

1. **진실의 소재**: RoboTrader `main.py:2938-3026`는 잔고에서 `POSITIONED`를 만들고 합성 BUY 기록("미관리종목복구")을 저장; jocoding `:217-220`은 잔고가 곧 보유목록; ante fallback은 잔고 증분을 체결로 반영. 우리는 원장이 진실이고 잔고 불일치는 `unverified_symbols`로 **차단**(`broker.py:145-158`, `risk.py:150`). 상대의 "복구" 코드는 어느 것도 이식 불가.
2. **시장가**: RoboTrader 손절·EOD 청산 전부 시장가(`trade_executor.py:159-166`, `main.py:2389-2412`), jocoding 전부 시장가. 우리는 지정가만 → 상대의 타임아웃·청산 정책 수치(15:00 시장가)를 그대로 옮길 수 없다.
3. **fail-open 기본값**: RoboTrader 휴장일 동기화 실패·서킷브레이커 데이터 부재·타임아웃 취소 실패(`order_manager.py:563-572` 로컬 강제 정리)·ML 오류 통과. 우리는 취소 실패 → `cancel_reconciliation_required` incident(`runtime.py:171-176`), 결과 불명 → 진입 차단. 상대 로직을 가져올 때 분기 방향을 전부 뒤집어야 하므로 "직접 구현"이 대부분 답이다.
4. **상태 저장 위치**: RoboTrader `pending_orders`는 메모리 dict(`order_manager.py:25-27`) → 재시작 시 소실, 킬스위치·성과 게이트는 파일. 우리는 sqlite 원장·`settings`. ante는 sqlite+outbox로 우리와 같은 방향.
5. **취소 후 재POST**: RoboTrader 가격 정정 = 취소 후 재주문(`order_manager.py:710-753`, 비활성). 우리는 claim 후 query-only, 재POST 금지(`broker.py:442`).
6. **프로세스 모델**: 상대 둘 다 단일 asyncio 프로세스 + 태스크 감시; 우리는 3프로세스 + 파일 잠금 + 계좌 잠금. 감시는 프로세스 밖(예약 작업)에서 해야 한다.
7. **설정 변경 = 코드 편집**: RoboTrader `strategy_settings.py` 상수. 우리는 버전·상한이 있는 `Policy`. 상대 상수 값(손절 5%·익절 6%·종목 19%)은 우리 상한(`trade_risk≤0.5%`, `symbol_cap≤25%`)과 체계가 다르다.
8. **오버나이트**: RoboTrader 활성 전략 `macd_cross`는 2거래일 보유(`strategy_settings.py:60`), EOD 격리 로직이 오버나이트 허용 쪽. 우리는 마감 미청산분 격리·다음 세션 청산.
9. **인바운드 채널**: Telegram 폴링·명령 vs 송신 전용 DM.
10. **시간 표현**: jocoding naive `datetime.now()`, RoboTrader pytz KST, ante UTC 저장. 우리는 `aware()` 강제 UTC + KST 변환. 상대 시간 비교 코드는 그대로 못 쓴다.

### 6. 읽지 않은 범위·미확인 사실

읽지 않음:
- RoboTrader: `api\*`(커넥터, 과제 범위 외), `core\trading_decision_engine.py` 584~863행 외, `core\intraday_stock_manager.py` 본문, `core\pre_market_analyzer.py`(927줄), `core\candidate_selector.py` 본문, `core\indicators\*`, `analysis\*`, `backtests\common\*`, `utils\signal_replay*.py` 본문, `db\*`, `tests`, `docs`.
- ante: `rule\engine.py`(1,692줄), `bot\manager.py`(grep만), `main.py`(grep + 40줄), `gateway\gateway.py`(grep), `broker\kis.py`·`kis_stream.py`(커넥터), `trade\order_tracker.py`의 `expire_stale` 외, `trade\fill_applier.py`, `treasury\treasury.py`, `ipc`, `member`, `approval\service.py`, `backtest\executor.py` 본문, 스펙 3건 외 문서, tests.
- jocoding `UsaStockAutoTrade.py`; auto_trade `fastapi_server\*`, `plugins\prophesy.py`·`report.py`·`asset_update.py`.
- 우리: `store.py` 일부, `intraday\collection.py`·`data.py`·`replay.py` 본문·`shadow.py`·`evaluation.py`, `lab.py`, `research.py`, `intelligence.py`, `dashboard.py` 본문, `packages\core\execution\*` 커널, tests. 따라서 "v2 유니버스 적격성 메타를 누가 채우는지"와 "replay가 특정 시각의 신호 미발생 사유를 설명하는지"는 확인하지 못했다.

미확인 사실:
- `FID_TRGT_EXLS_CLS_CODE="1111111111"`(`data.py:150`)의 비트별 의미(관리종목·우선주·ETF 제외 여부)를 KIS 문서로 검증하지 않았다.
- `exchange-calendars==4.13.2` XKRX가 2026-09-24/25 추석과 수능일 지연 개장을 담고 있는지.
- 우리 클라이언트의 일별 체결 조회 tr_id 세대(ante §2.1의 당일 지연 이슈 해당 여부) — 커넥터 담당.
- KIS 모의가 15:20~15:30 동시호가 중 취소를 접수하는지.
- 네이버 `marketValue` 목록에 우선주·ETF 포함 여부.
- RoboTrader 블로그·매매일지의 성과 주장은 검증하지 않았고 판단 대상도 아니다.
- 라이선스 종류는 GitHub API `license.spdx_id`와 저장소 내 `LICENSE` 파일 존재만 기록했으며 본문은 읽지 않았다(별도 과제).
