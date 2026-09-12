# paper-audit 2026-09-12 — B1 원본 보고 (KIS 커넥터·인프라 층 OSS 대조)

에이전트: Claude Code `aorch-analyst`, model `fable`(claude-fable-5-1), 독립 컨텍스트, 읽기 전용. 통합 보고서: `paper-audit-20260912.md`.

---

# KIS 모의투자 커넥터 OSS 대조 보고 (B1)

## 1. 한 줄 결론

가장 중요한 갭 3개: **(G1) 비-200 HTTP 응답의 본문(`msg_cd`)을 버려서 `EGW00201`(초당 초과)·`EGW00133`(토큰 1분 1회)·`EGW00123`(토큰 만료)을 구분 못 함**, **(G2) 4개 프로세스가 각자 `/oauth2/tokenP`를 호출하는데 "1분당 1회" 제한과 `access_token_token_expired`를 무시함**, **(G3) `RateLimiter(1.05)`가 프로세스별 독립이라 합산 ~3.8 req/s가 되는데 모의서버 한도는 어느 저장소도 확정하지 못함(2/s, 5/min, 5/s 세 가지 주장 공존)**. 그다음이 체결통보(`H0STCNI9`) 부재와 구독 한도 40 충돌, `CTCA0903R` 휴장일 조회 부재.

## 2. 대상 저장소

| 저장소 | HEAD | 라이선스 파일·종류 | 최근 커밋 | 읽은 파일 |
|---|---|---|---|---|
| https://github.com/koreainvestment/open-trading-api | `b4e6249714418aa57833d1cbbbced39cbcc5b125` | 루트 LICENSE **없음**(미확인) | 2026-08-26 | `examples_llm/kis_auth.py`, `README.md`, `kis_devlp.yaml`, `examples_llm/domestic_stock/{chk_holiday,ccnl_notice,inquire_psbl_order,inquire_daily_ccld,inquire_time_itemchartprice,volume_rank,market_time,inquire_psbl_rvsecncl,order_cash,inquire_asking_price_exp_ccn}/*.py`(핵심 부분 grep) |
| https://github.com/Soju06/python-kis | `3be3d012a4398cded24edf761351516a0d7d7083` | `LICENCE` MIT (2024 Soju06) | 2025-10-13 | `pykis/kis.py`, `__env__.py`, `client/{auth,websocket,messaging,exceptions}.py`, `api/auth/{token,websocket}.py`, `utils/rate_limit.py`, `api/websocket/order_execution.py`, `api/stock/{trading_hours,day_chart,chart}.py`, `api/account/{orderable_amount,pending_order}.py`, `responses/{response,exceptions}.py`(부분), `daily_order.py`·`order.py`(grep) |
| https://github.com/sharebook-kr/mojito | `da87f1470b2b6f178138f506c30248832473c608` | `LICENSE` MIT (2022 sharebook-kr) | 2024-02-20 | `mojito/koreainvestment.py` :160-465, :530-610, :875-905, :1120-1164 |
| https://github.com/joshua-jingu-lee/ante | `69492e61e16b76ca72b1c36cc608cd93f28777c5` | `LICENSE` MIT (2025-present Joshua-Jingu-Lee) | 2026-09-07 | `src/ante/broker/{kis,kis_stream,error_codes,circuit_breaker,exceptions}.py` 전체, `fill_scheduler.py` :1-80, `docs/specs/broker-adapter/07-kis-base-adapter.md`(grep) |
| https://github.com/pjueon/pykis (WebSearch 추가) | `ef3d34e39411393ef9f51625e0506d200215b312` | `LICENSE` Apache-2.0 (2022 Jueon Park) | 2022-09-03 (휴면) | `src/pykis/access_token.py`, `request_utility.py`(grep) |

clone 위치: 세션 스크래치패드 `scratchpad\b1\` (open-trading-api는 긴 경로 때문에 `core.longpaths=true` 후 `git restore`로 복구). pip install·import·실행은 하지 않음.

## 3. 축별 비교

우리 파일 경로 접두: `quantpilot\`

### 축 1. 토큰 수명주기
| 항목 | 우리 | 상대 | 운영 영향 | 판정 |
|---|---|---|---|---|
| 발급 제한 사실 | 없음 | 공식 `examples_llm/kis_auth.py:68,192` 주석 "유효시간 1일, 6시간 이내 발급신청시는 기존 토큰값과 동일, 발급시 알림톡 무조건 발송"; 공식 `README.md:371` "1분당 1회 발급"; ante `error_codes.py` `EGW00133`="접근토큰 발급은 1분당 1회만 허용"(실측 기반) | 4프로세스가 1분 내 동시 기동하면 2번째부터 `EGW00133`. 6시간 내 재발급은 같은 토큰이므로 **앞 토큰 무효화는 없음**(공식 주석 근거; 모의서버 실측은 미확인) | 직접 구현 |
| 만료 판단 | `paper/auth.py:32-34` `expires_in*0.9`를 **발급 시각 기준** | mojito `koreainvestment.py:400-405` "expires_in has no reference time… I've seen 4000 seconds" → `access_token_token_expired`(KST) 사용; python-kis `api/auth/token.py:28` 동일 | 6시간 내 재발급으로 **기존 토큰**을 받았을 때 `expires_in`이 잔여인지 24h인지 불명 → 우리 갱신 시점이 실제 만료보다 늦을 수 있음 | 직접 구현 |
| 디스크 캐시 | 없음(메모리) | 공식 `~/KIS/config/KISyyyymmdd` 평문; python-kis `~/.pykis/token_*.json`; mojito `~/.cache/mojito2/token.dat` pickle; ante 프로세스 내 dict + app_key 단위 asyncio.Lock + 60s cooldown(`kis.py:154-296`) | 안전 불변식 "비밀은 메모리에만"과 충돌 | 제외(캐시) / 차용 개념(ante cooldown) |
| 만료 코드 처리 | 없음 | python-kis `kis.py:586-591` `EGW00123`이면 토큰 폐기 후 재발급 | 서버측 무효화 시 우리는 `KisPaperTransportError`로만 보임 → 갱신 안 함 | 직접 구현 |
| `revokeP` | 없음 | python-kis `token.py:93` `discard()`만 | 6시간 동일 토큰 → 한 프로세스가 revoke하면 다른 3개도 죽음 | 제외 |

### 축 2. 레이트리밋
| 항목 | 우리 | 상대 | 판정 |
|---|---|---|---|
| 한도 값 | 없음(1.05s 간격 = 0.95/s/프로세스, `paper/data.py:24-35`) | python-kis `__env__.py:14-15` 실전 19/s·모의 **2/s**; ante `kis.py:486` 모의 **5/분**(!) vs ante 문서 `07-kis-base-adapter.md:31` "초당 5회"(코드·문서 불일치); 공식 `README.md:395-397` "모의투자 계좌는 REST API 호출 제한이 낮습니다"(수치 없음); 공식 `kis_auth.py:146-151` `_smartSleep` 실전 0.05·모의 0.5(그러나 지역변수 대입 버그로 실제 미적용) | 미확인 |
| 4프로세스 합산 | 트레이더 `paper/cli.py:61`, 수집기 `paper/intraday/collection.py:71`, 복구 `paper/recovery.py:45`, readiness `jobs/check_paper_readiness.py:129` 각각 별도 `LimitedTransport` | python-kis `utils/rate_limit.py` `multiprocessing.Lock`이지만 실제로는 객체 공유 안 하면 무의미; ante "멀티프로세스 미해결"(`kis.py:165-168`) | 직접 구현 |
| `EGW00201` | 없음. 비-200이면 본문 미파싱(`packages/core/kis_paper.py:214-215`); 200이면 `KisPaperBusinessError`(`:1145-1152`) | python-kis `kis.py:579-584` 비-200 본문에서 `EGW00201` → 0.1s sleep 재시도; ante `error_codes.py` TRANSIENT + 지수 백오프(주문 TR도 최대 3회 재시도) | 직접 구현(조회만), 주문 재시도는 제외 |

### 축 3. hashkey
우리: 미사용. 공식 `kis_auth.py:270` "현재는 hash key 필수 사항아님, 생략가능", `_url_fetch:442` 주석 처리; python-kis·ante 미사용; mojito만 `issue_hashkey`(`:446-463, :1152`). **판정: 제외**(공식 근거 확보).

### 축 4. WebSocket
| 항목 | 우리 (`paper/intraday/stream.py`) | 상대 | 판정 |
|---|---|---|---|
| 체결통보 | 없음(`parse_frame:28` `"0"` 프레임만, 암호화 프레임 거부) | 공식 `ccnl_notice.py:174,196` 모의 `H0STCNI9`, tr_key=**HTS ID**, `CNTG_YN` 2=체결/1=접수, AES256 복호; python-kis `order_execution.py:524` `H0STCNI9`, 23컬럼; 공식 26컬럼(`ORD_COND_PRC`,`ORD_EXG_GB`,`POPUP_YN`,`FILLER` 추가) → 스키마 드리프트; ante `kis_stream.py:24` 실전 `H0STCNI0`만, 복호 없음(모의 미지원 상태) | 직접 구현(후순위, 조건부) |
| approval key | `request_approval:100-122` 1회 발급, 갱신 없음 | 전부 동일(만료 규정 어디에도 없음) | 유지 |
| PINGPONG | `:199-200` `ws.pong(raw)` | 공식 `kis_auth.py:700` `ws.pong(raw)`; python-kis `:458`·mojito `:268`은 텍스트 echo | 미확인(둘 다 동작 보고) |
| 재접속 | 없음(예외 → 수집 종료) | python-kis `_run_forever:358-405` 5s 간격 + 구독 복원; ante `_connection_loop:188-207` 1→60s 지수 | 직접 구현(연구용이라 낮음) |
| 암호화 | 없음 | 공식 pycryptodome, python-kis `cryptography`(`messaging.py:127-160`) | 체결통보 도입 시 새 의존성 필요 |
| 구독 한도 | 20종목×2TR=40(`:129,166`) | 공식 `kis_auth.py:710` "max is 40", python-kis `__env__.py:12` 40, ante 40 | **충돌**: 체결통보 1건 추가 시 41 → 종목 19개로 줄여야 함 |
| 모의 WS 주소 | `ws://ops.koreainvestment.com:31000` | 전부 동일(`kis_devlp.yaml`, ante `:472`, python-kis `__env__.py:10`) | 일치 |

### 축 5. 휴장일·장운영시간
우리: `paper/calendar.py:29` `exchange_calendars` XKRX + `packages/core/marketdata/kis_paper.py:25-26` 09:00–15:30 고정. 상대: 공식 `chk_holiday.py:87-91` `CTCA0903R`, `opnd_yn`로 주문 가능 판단, "**가급적 1일 1회 호출**"(원장 연동); python-kis `trading_hours.py:180-185` KRX 09:00–15:30 고정, 휴장일 조회 없음; ante KST 영업일 계산만; mojito 없음. `market_time`(`HHMCM000002C0`)은 국내선물 영업일. **판정: 직접 구현** — 기동 시 1일 1회 `CTCA0903R`로 당일 `opnd_yn` 확인, 캘린더와 불일치하면 fail-closed. 단 `CTCA0903R`은 개장 여부만 주고 개장 시각(지연 개장·반일장)은 없으므로 시각 지연은 여전히 미해결.

### 축 6. 계좌·주문
| 항목 | 우리 | 상대 | 판정 |
|---|---|---|---|
| 매수가능 | `kis_paper.py:720-731` `ORD_DVSN=01`, 지정가, `CMA=N`,`OVRS=N`, `nrcvb_buy_amt`·`ord_psbl_cash` 최소값 사용(`broker.py:374-376`) | 공식 `inquire_psbl_order.py:407-411` 동일 권고; ante `:1321-1350` 동일, `nrcvb_buy_amt` SSOT; mojito `:898-899` `'1'` 전달(공식과 다름) | 일치 |
| 잔고 페이지 | `_paginated_get:998-1034` 커서 반복·요약 불일치 시 예외 | ante `:998-1075` 커서 누락 시 warning 후 중단(잔여 누락 허용) | 우리가 더 엄격, 유지 |
| 시장가·IOC/FOK·정정 | 없음 | ante 시장가 기본·정정(`:1501-1566`, orgno 없으면 fail-closed); python-kis IOC/FOK "모의투자 미지원" | 제외(불변식) |
| 일별체결 | `VTTC0081R`, 3개월 경계 `kis_recent_three_month_start:68-80`, 초과 시 거부 | ante `_ccld_three_month_cutoff:304-325` 동일 산식, 초과 시 `VTSC9215R` 전환; 공식 "모의계좌 한 번 호출 최대 15건" | 일치(우리 `_MAX_PAGES=100` 충분) |
| 미체결 조회 | `VTTC0084R` allowlist·`get_cancelable_orders:669-702` 존재 | 공식 예제 `TTTC0084R`만(demo 분기 없음); python-kis `pending_order.py:695` 모의 `NotImplementedError`; ante `VTTC8036R`(구세대) | 우리 09-11 문서와 일치하게 미사용 경로임을 확인. 코드에 남은 이유는 재조사 대상 아님 |

### 축 7. 오류·응답 처리
| 항목 | 우리 | 상대 | 판정 |
|---|---|---|---|
| 비-200 본문 | `StrictUrllibKisPaperTransport:214-215` `HTTPError` → 상태코드만, 본문 폐기 | ante `_handle_response:814-860` 비-200에서도 `rt_cd/msg_cd` 파싱; python-kis `kis.py:572-577` 동일 | **직접 구현(최우선)** |
| 코드 표 | `90070000`만(`check_kis_paper_connection.py:73`) | ante `error_codes.py` 모의 실측: `40580000` 장종료, `40570000` 장시작전, `40240000` 모의 잔고 없음, `40910000` 모의 주문불가 계좌, `IGW00022` 원주문번호 오류, `EGW00133`, `EGW00201`; python-kis `EGW00123` | 직접 구현(코드만, 재시도 정책은 별도) |
| 비밀 마스킹 | `safe_failure:1169-1173` 코드만 남김 | python-kis `safe_request_data` appkey/secret/Authorization 마스킹 | 우리가 더 보수적, 유지 |
| 재시도 | 없음(runtime.py:522 `broker_retry_after` 지수 백오프는 사유 무관) | ante 주문 TR 최대 3회 재시도(`_ORDER_TR_IDS`,`:934-967`) | 제외(at-most-once 충돌) |

### 축 8. 시세
| 항목 | 우리 | 상대 | 판정 |
|---|---|---|---|
| 분봉 | `PaperMarket.minutes:98-111` `now` 기준 1회 호출, `FID_PW_DATA_INCU_YN=Y` | 공식 `inquire_time_itemchartprice.py:35` "한 번의 호출에 최대 30건"; mojito `:557-579`·python-kis `day_chart.py:332-380` 마지막 봉-1분을 커서로 역방향 반복 | 직접 구현(장중 재기동 시 30분 이전 봉 부재가 전략에 문제면) |
| 거래량순위 | `candidates:140-163` `FHPST01710000` 실패 시 Naver 공개 순위 폴백 | 공식 `volume_rank.py:90` env 분기 없음(모의 지원 여부 미기재); 다른 3개 미구현 | 미확인(폴백 유지) |
| 현재가·호가 | `FHKST01010100`/`FHKST01010200` | 공식 실전/모의 동일 tr_id | 일치 |

## 4. 갭 목록 (우선순위 순)

| id | 갭 | 우리 위치 | 상대 위치 | 운영 영향 | 판정·근거 | 모의서버 확인 |
|---|---|---|---|---|---|---|
| G1 | 비-200 응답 본문 폐기 → `EGW00201/00133/00123`이 전부 `KisPaperTransportError`로 뭉개짐 | `packages/core/kis_paper.py:214-215`, `:1140-1152` | ante `kis.py:814-860`; python-kis `kis.py:572-594` | 초당 초과·토큰 만료·토큰 제한을 네트워크 장애와 구분 못 해 백오프·재발급 결정을 못 함 | **직접 구현**: `HTTPError.read()`로 본문을 `_MAX_RESPONSE_BYTES` 한도 내 읽고 `msg_cd`만 `_safe_response_code`로 추출해 `KisPaperTransportError`에 부착(메시지 텍스트는 버림). ante 코드는 aiohttp·EventBus 의존이라 옮길 게 없음 | 필요(비-200 시 본문 형태) |
| G2 | 토큰: 1분 1회 제한 미인지, `expires_in` 기준 갱신, 4프로세스 개별 발급 | `paper/auth.py:27-34`; `paper/cli.py:65`; `paper/intraday/collection.py:72`; `paper/recovery.py:46` | 공식 `kis_auth.py:68,192`, `README.md:371`; ante `kis.py:154-296`; mojito `:400-405` | 동시 기동 시 2번째 프로세스 실패(`token_refresh_backoff` 60s 후 재시도는 있음 `auth.py:25-27`); 갱신 시각 오차 | **직접 구현**: (a) `access_token_token_expired`(KST) 파싱을 1순위, `expires_in`을 검증용으로, (b) `EGW00133` 수신 시 60s 쿨다운(ante 개념), (c) 프로세스 기동 간격 ≥60s를 런북에 명시. 디스크 캐시는 제외(불변식) | 필요(6시간 내 재발급 시 같은 토큰 반환·`expires_in` 값) |
| G3 | 레이트 예산 프로세스별 독립, 모의 한도 미확정 | `paper/data.py:24-47` + 4 배선 | python-kis 2/s; ante 5/min(코드) vs 5/s(문서); 공식 "낮다" | 4×0.95≈3.8/s가 2/s 가정을 초과; 대사(`broker.py:162,418`)는 페이지당 15행이라 여러 호출 | **직접 구현**: `~/.quantpilot/account-locks`와 같은 방식의 파일 락 기반 공유 리미터(비밀 아님) 또는 비트레이더 프로세스 간격 2.1s; `EGW00201`은 조회 경로에서만 지수 백오프, 주문 POST는 outcome_unknown 유지 | 필요(모의 초당 한도 실측) |
| G4 | 체결통보 `H0STCNI9` 부재 | `paper/intraday/stream.py:19,28` | 공식 `ccnl_notice.py`; python-kis `order_execution.py:221-333` | 폴링 대사 지연 보완 불가 | **직접 구현(후순위, 조건부)**: 통보를 **대사 트리거 힌트**로만 쓰고 체결 권위는 `VTTC0081R`에 둠(불변식). 조건: HTS ID 환경변수(`KIS_PAPER_HTS_ID`) 추가, AES-256-CBC 의존성(`cryptography`)이 `/dependency-audit` 필요, 구독 예산 40 안에서 종목 19개로 축소, 26컬럼 스키마 검증 | 필요(모의 통보 프레임 실물) |
| G5 | 휴장일 API 부재 | `paper/calendar.py:29` | 공식 `chk_holiday.py:87-91` | 임시 휴장·정부 임시공휴일이 `exchange-calendars==4.13.2` 배포 전이면 개장으로 오판 | **직접 구현**: 기동 시 1일 1회 `CTCA0903R`(`BASS_DT`=오늘) `opnd_yn` 확인, 불일치 시 fail-closed; allowlist에 `/uapi/domestic-stock/v1/quotations/chk-holiday` 추가. 지연 개장 시각은 여전히 미해결 | 필요(모의서버에서 `CTCA0903R` 지원 여부) |
| G6 | 오류 코드 분류 표 부재 | `check_kis_paper_connection.py:73` | ante `error_codes.py` | 장종료(40580000)·장시작전(40570000)·모의 계좌 부적격(40910000)을 운영자가 구분 못 함 | **직접 구현**: 코드→분류(permanent/transient/auth)만 옮기고 메시지·재시도 정책은 제외. ante 표 자체는 실측 근거가 이슈 번호로 남아 있어 참고 가치 큼 | 일부(모의 코드 실물) |
| G7 | 분봉 30건 제한 역방향 페이징 없음 | `paper/data.py:98-111` | python-kis `day_chart.py:332-380`; mojito `:557-579` | 장중 재기동 시 최근 30분만 보유 | **직접 구현**(전략이 30봉 이상 필요할 때만): 커서=마지막봉-1분, 페이지 상한, 중복 검사(이미 `parse_minutes:74-76` 있음) | 낮음 |
| G8 | WS 재접속 없음 | `stream.py:135-208` | python-kis `:358-405`; ante `:188-207` | 수집기 세션 중단 → partial | 직접 구현(연구용, 낮음) | 아니오 |
| G9 | `EGW00123` 토큰 만료 시 재발급 없음 | `auth.py:21-35` | python-kis `kis.py:586-591` | G1 해결 후에만 감지 가능 | 직접 구현(G1·G2에 종속) | 필요 |
| G10 | User-Agent 기본값(`Python-urllib`) | `kis_paper.py:198-206` | 공식 `kis_auth.py:64` 브라우저 UA 요구, python-kis `PyKis/x` | 차단 여부 미확인 | 미확인, 현재 연결 검사 통과 이력 있으므로 보류 | 낮음 |
| G11 | hashkey 미사용 | — | 공식 `kis_auth.py:270` | 없음 | **제외**(공식이 선택 사항이라 명시) | 아니오 |
| G12 | `revokeP` 미사용 | — | python-kis `token.py:93` | 6시간 동일 토큰 특성상 다른 프로세스 토큰을 죽임 | **제외** | 아니오 |
| G13 | 주문 POST 자동 재시도 | — | ante `:934-967` | at-most-once·durable coordinator와 충돌 | **제외** | 아니오 |
| G14 | 시장가·정정·IOC/FOK | — | ante, python-kis | 지정가 전용 불변식 | **제외** | 아니오 |

## 5. 상대 설계와 우리 설계가 적극 충돌하는 지점

1. **토큰 디스크 캐시 vs 메모리 전용**: 공식·python-kis·mojito 전부 파일 캐시(평문/pickle)가 "1분 1회" 대응책이다. 우리는 이를 쓸 수 없어 프로세스 기동 스태거링과 쿨다운으로 대체해야 하며, 이는 4프로세스 부팅 시간을 최소 3분 늘린다.
2. **재시도 철학**: ante는 주문 TR(`VTTC0012U/0011U/0013U`)도 3회 재시도·서킷브레이커로 감싼다. 우리는 주문 POST가 durable coordinator의 claim-before-POST에 묶여 있어, ante의 `_request_with_cont`를 통째로 가져오면 at-most-once가 깨진다. 조회 경로만 분리해 백오프해야 한다.
3. **구독 예산**: 우리 20종목×2TR=40은 이미 한도(공식 예제 `max is 40`)에 붙어 있다. 체결통보를 넣으려면 연구 스트림 종목을 줄여야 하며, 이는 `collection.py:152`의 `[:20]`과 `stream.py:129,166`의 검증 상수를 함께 바꿔야 한다.
4. **오류의 정보량**: 우리는 의도적으로 provider 텍스트를 전부 버린다(`safe_failure`). 상대는 `msg1`을 로그·예외에 싣는다. `msg_cd`만 추출하는 중간 지점을 택해야 G1을 풀면서 비밀 노출 원칙을 지킬 수 있다.
5. **체결 권위**: python-kis·mojito는 WS 체결통보를 체결 사실로 소비한다. 우리 불변식은 REST 대사 증거만 durable 상태에 반영하므로, 통보는 "지금 대사하라"는 신호 이상이 될 수 없다.
6. **레이트 한도 자체의 불확실성**: 세 저장소가 세 가지 값을 쓰고(2/s, 5/min, 5/s), ante는 코드와 문서가 서로 다르다. 어느 쪽도 차용 근거가 못 되며 모의서버 실측이 선행돼야 한다.

## 6. 읽지 않은 범위·미확인 사실

**읽지 않음**: python-kis `order_modify.py`·`balance.py`·`scope/`·`event/`·`adapter/`; ante `gateway`·`treasury`·`order_tracker`·`fill_applier`·`tests/`; mojito 해외주식 부분·`examples/`; open-trading-api `examples_user/` 대부분, `MCP/`, `backtester/`, `strategy_builder/`, `legacy/`, `examples_llm/domestic_stock`의 나머지 150여 디렉터리; pjueon/pykis `public_api.py`; 우리 쪽 `paper/runtime.py` 본문(주기만 grep), `packages/core/execution/paper_submission.py`·`paper_reconciliation.py`, 테스트 전체. 전량취소 `VTTC0013U`는 지시대로 재조사하지 않고 `docs/plans/kis-paper-oss-research-20260911.md`만 참조.

**미확인(포털 원문 확보 실패)**: `apiportal.koreainvestment.com` 문서 페이지는 403/404, FAQ는 0건, README·검색·블로그에서 수치 확인 실패. 따라서 다음은 전부 "미확인": (a) 모의서버 초당 호출 한도의 정확한 값, (b) "6시간 이내 동일 토큰"과 "1분 1회"의 정확한 상호작용(1분 내 재요청이 오류인지 동일 토큰 반환인지), (c) `expires_in`이 동일 토큰 재반환 시 잔여 시간인지, (d) WS 구독 한도가 40인지 41인지(코드는 전부 40), (e) `FHPST01710000` 거래량순위의 모의 지원, (f) `CTCA0903R`의 모의 지원, (g) PINGPONG에 pong 프레임과 텍스트 echo 중 무엇이 요구되는지. 공식 저장소 근거로 확인된 것: 토큰 1분당 1회(`README.md:371`), 유효 1일·6시간 동일·알림톡(`kis_auth.py:68`), hashkey 선택(`kis_auth.py:270`), 분봉 30건(`inquire_time_itemchartprice.py:35`), 일별체결 모의 15건/페이지(`inquire_daily_ccld.py:630`), 휴장일 1일 1회 권고(`chk_holiday.py:87`), 모의 체결통보 `H0STCNI9`(`ccnl_notice.py:196`), WS 구독 최대 40(`kis_auth.py:710`).

참고 출처: [koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api), [Soju06/python-kis](https://github.com/Soju06/python-kis), [pjueon/pykis](https://github.com/pjueon/pykis), [tgparkk 초당 20건](https://tgparkk.github.io/robotrader/2025/10/09/robotrader-1-70stocks-problem.html), [hky035 쓰로틀링](https://hky035.github.io/web/kis-api-throttling/) — 후자 두 블로그는 실전 20/s와 `EGW00201`만 확인하고 모의 수치는 없음.
