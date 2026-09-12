# 모의투자 단타 프로그램 점검 통합 보고 — 2026-09-12

## 결론

**2026-09-14 시험운영 재개 전에 고쳐야 할 결함이 있다.** 정상 경로의 불변식(이중 제출 방지, 상태기계, 손실 한도 영속, 대사 fail-closed)은 성립하지만, KIS 전송 오류 한 번으로 촉발되는 두 개의 high 결함(취소 POST 실패 → 보호 청산 영구 봉쇄, 결과 미상 매도 → 커널 전체 claim 차단)과 시험운영 프로필(`legacy` 세대)에 일손실·누적낙폭 halt가 아예 적용되지 않는 운영 위험이 확인됐다. 오픈소스 대조에서는 비-200 응답 본문 폐기(오류 코드 구분 불가), 토큰 발급 제한 미인지, 프로세스 감시·휴장일 이중 확인 부재가 가장 큰 갭이다.

오프라인 검증 증거(A2 실행): `scripts/verify-paper.py` **1,632 passed · 2 skipped**(pytest 55.3초), `run_smoke` 통과, `tach check` 통과(`~/.local/share/aorch-tools/.venv/Scripts/tach.exe`; 프로젝트 `.venv`에는 tach 없음).

## 방법

| 트랙 | 에이전트 | 렌즈 | 원본 |
| --- | --- | --- | --- |
| 내부 감사 A1 | `aorch-reviewer`, fable | 주문 경로 불변식 실패-경로 반증 | [paper-audit-20260912-a1.md](paper-audit-20260912-a1.md) |
| 내부 감사 A2 | `aorch-reviewer`, fable | 실행 증거·문서 드리프트·테스트 갭·운영 위험·구형 경로 판정 | [paper-audit-20260912-a2.md](paper-audit-20260912-a2.md) |
| OSS 비교 B1 | `aorch-analyst`, fable | KIS 커넥터·인프라 층 (open-trading-api, python-kis, mojito, ante, pykis) | [paper-audit-20260912-b1.md](paper-audit-20260912-b1.md) |
| OSS 비교 B2 | `aorch-analyst`, fable | 단타 봇 운영·전략 층 (RoboTrader, ante, jocoding autotrade) | [paper-audit-20260912-b2.md](paper-audit-20260912-b2.md) |

네 에이전트는 서로의 출력을 보지 않았다. 리드(Claude Fable 5.1)가 아래 "확인된 발견"의 각 항목을 코드에서 직접 열어 확인했다. 어제 조사 [kis-paper-oss-research-20260911.md](kis-paper-oss-research-20260911.md)(전량취소 계약)는 재조사하지 않았다. 코드·설정·실제 원장은 변경하지 않았다.

## 확인된 발견 (리드가 코드로 재확인, 심각도 순)

| id | 심각도 | 수정 비용 | 내용 | 위치 | 출처 |
| --- | --- | --- | --- | --- | --- |
| F1 | **high** | standard | 취소 claim 후 POST 실패 시 재시도·해소 경로 없음. `cancel_claim:*`는 어디서도 지워지지 않고 `cancel_unknown`은 `OPEN`이므로 `sell_quantity`가 0 → 해당 종목 보호 청산 영구 봉쇄, `flatten` 완료 불가 | `paper/broker.py:408-409,436-446`, `paper/store.py:15-22`, `paper/risk.py:194-196` | A1-01 |
| F2 | **high** | standard | `outcome_unknown` dispatch 종결 경로 없음. HTTP 오류는 상태코드 불문 `KisPaperTransportError`→unknown, 일별 조회 0건이면 그대로 유지, 운영자 종결 명령 없음. 미상 **매도** 1건이 커널의 모든 신규 claim(다른 종목 보호 매도 포함)을 거부 | `packages/db/sqlite_repositories.py:3351-3357`, `packages/core/kis_paper.py:214-215`, `packages/core/execution/paper_reconciliation.py:173-193` | A1-02 |
| F3 | **high** | low~standard | `legacy` 전략 세대(정책 기본값, 09-11 시험운영 프로필)에는 `daily_loss_limit`/`peak_drawdown_limit` halt가 적용되지 않음. `loss_budget` 소비처는 전부 `strategy_generation == "intraday_v2"` 분기 안. `config` 출력은 한도를 표시해 오인 유발 | `paper/runtime.py:153-163,305-313`, `paper/risk.py:138-168`, `paper/config.py:16,22-23` | A2 D1/R3 |
| F4 | **high** | low | 트레이더 루프가 장 마감 후에도 무한 대기하고 control이 `running`이면 다음 거래일 자동 진입 재개. 시험운영표는 장후 수동 Pause에 의존 | `paper/cli.py:246-251`, `paper/runtime.py:80-82,127-142` | A2 R2 |
| F5 | **high** | low | 숨김 프로세스 3개의 stdout/stderr 미리다이렉트, 시작 실패·크래시가 원장 incident로 남지 않음, 하트비트 정지 알림·자동 재시작 없음 | `scripts/paper-runtime.ps1:16-18`, `paper/cli.py:277-279`, `paper/reporting.py:102-106` | A2 R1/D6, B2 G1/G2/G3 (독립 일치) |
| F6 | standard | low | 비-200 HTTP 응답 본문을 버려 `EGW00201`(TPS 초과)·`EGW00133`(토큰 1분 1회)·`EGW00123`(토큰 만료)을 네트워크 장애와 구분 못 함. 비즈니스 오류에는 백오프도 없음 | `packages/core/kis_paper.py:214-215,1145-1152`, `paper/runtime.py:519-522` | B1 G1, A2 R5, A1 미해결#3 (독립 일치) |
| F7 | standard | low | 토큰: `RefreshingClient`가 `_renew_at = clock()`으로 시작해 첫 호출에 무조건 재발급(`KIS_PAPER_ACCESS_TOKEN` 무시). 4개 프로세스 개별 발급 + 공식 "1분당 1회" 제한 미인지. `expires_in`만 신뢰하고 `access_token_token_expired` 미파싱 | `paper/auth.py:14-34`, `paper/cli.py:65`, `paper/recovery.py:46` | B1 G2, A2 D4/R4, A1 미해결#4 (독립 일치) |
| F8 | standard | low | 요청 예산이 프로세스 간 비공유(트레이더+수집기 ≈1.9 req/s, 스트림 프로세스 별도) + 사이클당 잔고 4회·일별 2~3회 중복 조회. 모의서버 TPS 한도는 OSS 셋이 2/s·5/min·5/s로 엇갈려 미확정 | `paper/data.py:24-47`, `paper/broker.py:147,162,364` | A1-04, B1 G3 (독립 일치) |
| F9 | standard | low | 복구 경로로 `accepted`가 된 주문은 forwarding ID가 없어 취소가 매 사이클 조용히 지연(인시던트 없음). `ord_gno_brno`와 `KRX_FWDG_ORD_ORGNO` 동일성은 저장소 안에서 해석 상충 | `packages/core/execution/paper_reconciliation.py:248-264`, `paper/broker.py:427-435`, `paper/order_evidence.py:24-45` | A1-03 |
| F10 | standard | standard | 첫 `reconcile()` 예외가 그 사이클 보호 단계를 전부 생략. 원장 불변식 위반은 결정적이라 매 사이클 반복 → 다른 포지션 보호도 정지 | `paper/runtime.py:111-124,185,502-528` | A1-05 |
| F11 | standard | low | 휴장일이 `exchange-calendars` 단일 소스. KIS `CTCA0903R` 대조 없음(추석 09-24/25 임박). `.venv`에 `exchange_calendars` 미설치라 실제 XKRX 달력 테스트가 없음 | `paper/calendar.py:26-41` | B1 G5, B2 G4, A2 커버리지 (독립 일치) |
| F12 | standard | low | `setup-paper.ps1`이 `paper` extra 중 `websockets`를 설치하지 않음 → `intraday_v2` 전환 시 runtime-venv ImportError | `scripts/setup-paper.ps1:12` vs `pyproject.toml:14-19` | A2 D2 |
| F13 | standard | low | 없는 `--runtime-dir`를 주면 읽기 명령도 새 원장을 조용히 생성. 오류는 예외 클래스명만 노출(`ValidationError`, `KisPaperConfigurationError`) | `paper/cli.py:124-133,175`, `paper/store.py:41-42` | A2 R6/R7 |
| F14 | low | low | 청산 지정가 60초 취소·재주문 루프에 횟수 상한·에스컬레이션 없음(동시호가 구간 churn), `flattening` 상태 탈출 경로 없음, 5분 lease 미갱신 | `paper/runtime.py:165-176`, `paper/store.py:197-204`, `paper/broker.py:67-69` | B2 G10, A1-07/08 |

세 트랙 이상에서 독립적으로 같은 지점을 짚은 항목: F5(운영 감시), F6(오류 코드), F7(토큰), F8(레이트), F11(휴장일).

## 문서 드리프트·테스트 갭 (A2, 요약)

- 드리프트 12건: 핵심은 D1(legacy 손실 한도 미적용 미명시), D4(토큰 재발급 전제 불일치), D5(`review-drawdown`·`--once` 미문서), D6(시작 실패가 incident로 남지 않음), D8(세 문서의 검증 명령 상이).
- 누락 테스트: 토큰 401·백오프 경로, `EGW00201` 백오프, 실제 XKRX 달력(휴장·반일장), reporter 재시작 outbox 복구, `flatten` 완료 조건, 신규 경로 계좌 fingerprint 불일치, lock 충돌 메시지.
- 구형 Level-5 경로 판정: `run_kis_paper_session.py` 폐기 후보(비테스트 importer 없음). `run_kis_paper_kill.py`+`paper_kill.py`는 **CLI 트레이더의 POST를 막을 수 있는 유일한 durable 킬 펜스**이므로 유지 후 CLI `kill` 명령으로 이관 권장(CLI `broker.sqlite3` provenance 통과 여부 미검증). `position_ledger.py`·`status_snapshot.py`는 핵심 의존. 나머지 operator 패키지는 API·`harness_service` 은퇴 결정에 종속.

## OSS 갭 판정 요약 (B1·B2)

| 판정 | 항목 |
| --- | --- |
| 직접 구현(우선) | 비-200 본문에서 `msg_cd`만 추출(F6); `access_token_token_expired` 파싱·`EGW00133` 쿨다운·기동 스태거(F7); 조회 경로 한정 `EGW00201` 백오프(F6); 하루 1회 `CTCA0903R` 대조 fail-closed(F11); 리포터 하트비트 정지 DM·장후 보고 누락 감지·예약 작업 기반 재시작(F5); 취소 루프 상한(F14) |
| 직접 구현(후순위) | 체결통보 `H0STCNI9`를 대사 트리거 힌트로만(구독 예산 40 충돌, `cryptography` 의존성 심사 필요); 오류 코드 분류표; 분봉 30건 역방향 페이징; WS 재접속; 동일 종목 재진입 쿨다운; 연속 손실 N회 중단; 시장 레짐 게이트 |
| 차용 검토 | RoboTrader 유니버스 품질 필터(가격대·등락률·거래대금·우선주/ETF 제외, ~20줄, 라이선스 파일 없음 → 로직만 참고해 직접 작성); ante 로그 핸들러(stdlib만 의존, 다만 3줄로 직접 가능) |
| 제외 | 토큰 디스크 캐시, `revokeP`, 주문 POST 자동 재시도, 시장가·정정·IOC/FOK, hashkey(공식이 선택 사항 명시), 잔고→포지션 합성, 완성봉 덮어쓰기, 인바운드 원격 제어 |

## Gate P 읽기 전용 프로브 결과 (2026-09-12 10:42~10:44 KST, 사용자 지시로 실행)

스크래치패드 스크립트 `gatep/probe.py`·`probe2.py`(토큰 POST와 GET 조회만, 주문·취소·approval·revoke 엔드포인트 호출 금지, 비밀·토큰·계좌번호 미출력). 트레이더·워커·대시보드 프로세스는 건드리지 않았다.

| # | 사실 | 관측 | 설계 영향 |
| --- | --- | --- | --- |
| 1 | 한도 초과 `EGW00201`의 HTTP 상태 | **HTTP 500**, 본문 `{rt_cd:"1", msg_cd:"EGW00201", msg1, message}`. 즉시 6연발 → 2건 200·4건 500. 1.05초·0.55초 간격 8연속은 전부 200. 0.6초 간격 7번째에서 1회 발생 | 한도 ≈ 초당 2건(초 단위 버킷). 현 상태에서 5xx→`KisPaperTransportError`→주문이면 `outcome_unknown`. **F6 필수**: 5xx 본문에서 `msg_cd`를 읽어 게이트웨이 거부(`EGW*`)를 확정 거부로 분류. F8(요청량 저감) 필수 |
| 2 | 토큰 재발급 | 1차 200(`expires_in=86400`, `access_token_token_expired="2026-09-13 10:42:18"`). 2초 뒤 2차 → **HTTP 403**, 본문 `{error_code, error_description}`(rt_cd 형식 아님). 82초 뒤 3차 → 200, **1차와 동일 토큰**, 만료 시각 불변 | 6시간 내 동일 토큰 확인 → 다중 프로세스 개별 발급은 앞 토큰을 무효화하지 않음. 1분 내 재발급만 403. **F7**: `access_token_token_expired`를 만료 기준으로, 403은 60초 쿨다운. 기존 `token_refresh_backoff` 60초로 이미 부분 대응 |
| 3 | 만료된 미체결 주문의 일별 행 | 09-08~09-11 주문 3건 전부 `tot_ccld_qty == ord_qty`, `rmn_qty=0`, `cncl_yn=N` → **미체결 만료 사례 없음, 미관측 유지** | A1 미해결#1 그대로. 09-14 운영 중 미체결 발생 시 다음날 조회로 확인 |
| 4 | 취소 자식 행 표현 | 취소 이력 없음 → 미관측 | A1 미해결#2 그대로 |
| 5 | `ord_gno_brno` vs `KRX_FWDG_ORD_ORGNO` | 일별 행 `ord_gno_brno="00950"`, `ord_orgno=""`. 주문 응답은 이번 프로브 범위 밖 | 미확정. 기존 저장된 dispatch의 forwarding 값과 대조는 구현 단계에서 원장 읽기로 가능 |
| 6 | 동시호가 호가 갱신·취소 접수 | 주말이라 미관측 | 그대로 |
| 7 | `CTCA0903R` 휴장일 조회 | **모의서버 미지원**: HTTP 500 `EGW02006` (5개 날짜 전부) | **F11 변경**: KIS 휴장일 대조는 모의에서 불가. `exchange-calendars` 단일 소스 유지 + 장 시작 후 시세 정지 감지로 대체 검토(후순위) |
| 8 | `FHPST01710000` 거래량순위 | 200, 30행 | 모의 지원 확인. 네이버 폴백은 보조 |
| 9 | 무효 토큰으로 시세 GET | **200 정상** | 시세 엔드포인트는 토큰을 검증하지 않음. 토큰 만료는 계좌·주문 엔드포인트에서만 드러남 → 만료 코드(`EGW00123`) HTTP 상태는 미관측 |

원본 JSON: 세션 스크래치패드 `gatep/probe-result.json`, `probe2-result.json`(저장소 밖).

## 미해결 위험

- A2가 `~/.quantpilot` 접근 금지로 runtime-venv의 실제 패키지 상태를 확인하지 못했다(F12는 설치 스크립트 기준).
- B1은 KIS 포털 원문에 접근하지 못해(403/404) 한도 수치는 전부 OSS·공식 예제 주석 근거다.
- B2 검색은 GitHub 한국어 질의 실패로 후보 누락 가능성이 있다.
- RoboTrader는 LICENSE 파일이 없어 코드 차용 불가. 로직 참고만.

## 결정 (인터뷰, 2026-09-12)

| 항목 | 결정 |
| --- | --- |
| 09-14 재개 전 수정 범위 | high 5건 전부(F1~F5). 안전 중요 변경이므로 구현자와 다른 독립 리뷰어 승인 후 완료 처리 |
| F1·F2 해소 방식 | **빈도 저감 + 수동 경로 먼저, 자동 재시도는 데이터로 결정**(1차 결정 "조건부 자동 1회 재시도"를 사용자 기준 "주 1회 이하면 수동 개입 가능"에 따라 변경). (1) F8 중복 조회 제거로 요청량 저감, (2) F6 비-200 본문 `msg_cd` 추출 + 비즈니스 오류 백오프로 한도 초과·토큰 만료를 "미상"이 아닌 "확정 거부"로 분류, (3) 운영자 수동 종결 명령(`resolve-unknown --order --reason`, `recancel --order`; 전제 paused·잔고 일치·재대사 후 증거 0건) + 미상 상태 N사이클 지속 시 Slack DM 인시던트. 자동 재-POST는 넣지 않고 `test_paper_daily_cancel.py:86-100`의 at-most-once 고정을 유지. 시험운영 중 `cancel_failed`·`outcome_unknown` 감사 건수를 집계해 **주 1회를 넘기면** 조건부 자동 1회 재시도를 추가 |
| 09-14 전 수정 범위(보정) | F1~F5 + F6·F8(F1·F2 빈도 저감의 전제) |

## 구현 결과 (2026-09-12, 사용자 "계획대로 실행해" 지시)

커밋하지 않은 작업 트리 변경. 실제 원장·실행 중인 시험운영 프로세스는 건드리지 않았다.

| 발견 | 변경 | 위치 |
| --- | --- | --- |
| F6 | 비-2xx 본문에서 `msg_cd`/`error_code`만 추출. 게이트웨이 거부 코드 4개(`EGW00201`·`EGW02006`은 GET 조회에서 HTTP 500으로 실측, `EGW00133`은 토큰 POST 403의 문서 코드, `EGW00123`은 미관측·문서 근거)는 `KisPaperGatewayRejected(KisPaperBusinessError)` → 주문 POST에서 `broker_business_rejected`로 종결(unknown 아님). **주문 POST 엔드포인트에서의 5xx 본문 형태는 미관측이며 "게이트웨이가 전달 전에 거부한다"는 추론이다**(미해결 위험 참조). 거부 코드는 `PaperSubmissionRejected` 메시지의 `(code=…)`로 남아 `cycle_failed` 감사의 `broker_code`로 집계된다. 나머지 비-2xx는 전송 오류 유지. 런타임 백오프가 게이트웨이 거부에도 적용. `EGW00123` 수신 시 토큰 재발급을 앞당김 | `packages/core/kis_paper.py`, `packages/core/execution/paper_submission.py`, `paper/runtime.py`, `paper/auth.py` |
| F7 | `access_token_token_expired`(KST) 파싱 → 갱신 시각을 절대 만료 5분 전으로 앞당김. 발급 거절(1분 1회) 시 보유 토큰 유지 + 60초 백오프, 토큰 없으면 fail-closed | `packages/core/kis_paper.py`, `paper/auth.py` |
| F8 | 대사기 잔고 재사용(체결이 새로 반영된 패스에서만 재조회; 정상 패스당 `get_balance` 1회), 매수가능조회 결과를 `prepare_order(buying_power=)`로 전달(주문당 1회 절감, 종목·가격 불일치 시 거부) | `paper/broker.py`, `packages/core/execution/paper_submission.py` |
| F3 | `loss_budget` 일손실 1%·누적낙폭 5% halt를 `legacy` 세대에도 적용(런타임 진입 게이트 + `entry_size`) | `paper/runtime.py`, `paper/risk.py` |
| F4 | 장 마감 처리 후 `auto_pause_after_close`(정책, 기본 true)로 자동 `paused` + 감사 | `paper/runtime.py`, `paper/config.py` |
| F5 | 실행기가 역할별 `logs\<role>-<date>.{out,err}.log`로 리다이렉트; `start/worker/reporter` 실패 시 감사 `process_failed` + DM; reporter가 평일 장중 하트비트 3분 단절 시 시간당 1회 `liveness` DM | `scripts/paper-runtime.ps1`, `paper/cli.py`, `paper/reporting.py` |
| F1·F2 수동 경로 | `recancel --order`(일별 조회로 원주문 미체결·취소 자식 행 없음 확인 후 claim만 해제 → trader가 다음 주기에 취소 1회 재전송), `resolve-unknown --order --reason`(paused·trader.lock·10분 경과·최신 대사 일치 0건·**같은 종목·방향·수량·가격의 주인 없는 당일 행 없음**·보유수량 일치 시 rejected 종결, 커널 출처 `operator_resolution`, 리듀서가 fill·브로커 식별자 없는 unknown 행의 rejected 전이만 허용). unknown 5분 지속 시 비차단 알림 `manual_resolution_required`. 자동 재-POST 없음, at-most-once 테스트 그대로 유지 | `paper/operator.py`(신규), `paper/cli.py`, `packages/core/execution/{events,reducer}.py`, `paper/config.py` |
| F12 | `setup-paper.ps1`에 `websockets==15.0.1` 추가 | `scripts/setup-paper.ps1` |
| 문서 | 런북에 자동 pause, 세대 무관 손실 한도, 운영자 해소 명령, 로그·liveness, 토큰·한도 사실, 휴장일 API 미지원 반영 | `docs/paper_intraday_runbook.md` |

새 테스트 3파일 34건: `test_paper_gateway_errors.py`(오류 분류·토큰 만료·갱신), `test_paper_operator_resolution.py`(claim 해제·unknown 종결·요청 절감), `test_paper_safety_nets.py`(legacy halt·자동 pause·운영자 알림·liveness·process_failed).

검증(2026-09-12, `.venv\Scripts\python.exe`):

| 명령 | 결과 (리뷰 반영 후 최종) |
| --- | --- |
| `scripts/verify-paper.py` (자격증명 제거, pytest 전체 + `run_smoke`) | 종료 코드 0 |
| `python -m pytest quantpilot/tests` (junit) | **1,678 passed · 0 failed · 2 skipped**(수동 opt-in 통합), 44.5s |
| `tach check` | `[OK] All modules validated!` |

새 테스트 4파일 45건(리뷰 후 추가 포함): `test_paper_gateway_errors.py`, `test_paper_operator_resolution.py`, `test_paper_safety_nets.py`, `test_paper_operator_origin_reducer.py`. 작업 트리 diff: 기존 14파일 +402/−44, 신규 10파일(보고서 5, 코드 1, 테스트 4).

미반영(후순위, 인터뷰 범위 밖): F9 forwarding ID 지연 인시던트, F10 대사 예외 격리, F11 휴장일 대안, F13 CLI 오류 메시지·빈 원장 생성, F14 취소 루프 상한.

## 독립 리뷰 (2026-09-12, `aorch-reviewer` fable, 구현자와 별도 컨텍스트)

판정 **PASS with required fixes** → 필수·권고 항목 전부 반영 후 재검증 통과.

| 지적 | 심각도 | 반영 |
| --- | --- | --- |
| P1 `resolve-unknown`이 대사기의 90초 매칭 창 밖에 있는 동일 주문(종목·방향·수량·가격 동일, 주인 없는 당일 행)을 "증거 0건"으로 보고 rejected로 닫아 유령 주문을 남김(리뷰어가 프로브로 재현) | required | 종결 전 당일 행을 다시 조회해 다른 dispatch가 소유하지 않은 동일 행이 있으면 `broker_evidence_ambiguous`로 거절. 회귀 테스트 추가 |
| P2 F6 "실측 4개 코드"는 GET·토큰 경로만 실측이고 주문 POST 경로는 추론 | required(문구) | 보고서·구현 표 정정, 미해결 위험에 등재. 거부 코드를 `PaperSubmissionRejected` 메시지에 남겨 `cycle_failed` 감사로 집계 가능하게 함 |
| P2 `EGW00123`(토큰 만료) 수신 시 갱신이 앞당겨지지 않음 | required | `RefreshingClient.invalidate()` 추가, 런타임이 해당 코드에서 호출(감사 `token_invalidated_by_broker`). 발급 백오프는 유지 |
| P2 F8 잔고 재사용이 체결 반영보다 오래된 스냅샷을 써 허위 인시던트 가능 | required | 대사 패스에서 포지션이 바뀐 경우에만 잔고 재조회. 테스트로 고정(체결 시 2회, 평시 1회) |
| P2 실행기 로그가 같은 날 재시작 시 덮어써짐 | required | 파일명에 `HHmmss` 추가 |
| P3 리듀서 `operator_resolution` 분기의 함수 레벨 테스트 없음 | 권고 | `test_paper_operator_origin_reducer.py` 추가(허용 1·거절 5) |
| P3 런북 보완(F3 소급 halt·사이징 영향, trader 사망 시 auto-pause 미기록, `recancel` 당일 한정, liveness 문구) | 권고 | 런북 반영 |

리뷰어가 문제 없음으로 확인한 것: at-most-once(기존 취소 테스트 무수정 통과, 운영자 경로는 주문·취소 엔드포인트 호출 불가), 리듀서 출처 검증이 기존 분기를 약화하지 않음, 비-2xx 본문에서 코드만 추출, 토큰 없는 상태 fail-closed, 중첩 트랜잭션 안전, outbox 중복 없음.

## 보안 게이트 (`/ship`, 2026-09-12 11:27, `qp-security-gate`)

증거 `.security-gate/20260912-112731`: gitleaks 0, semgrep 0, tach 0, 시크릿 패턴 0. 판정 **pass**(불변식 앵커 `transitions.py`·`risk/gatekeeper.py`·`.env.example` 무변경, 유일 POST 권한 유지, 새 브로커 POST 경로 없음). 기록된 발견:

| id | 심각도 | 내용 | 처리 |
| --- | --- | --- | --- |
| SG-01 | standard | `operator.py`에서 커널 close 플래그 `owned`가 참조 집합 대입으로 덮어써져 CLI 소유 커널이 닫히지 않거나 호출자 커널이 닫힘 | 커밋 전 수정(`owned_references`로 분리) + CLI 경로 테스트 추가 |
| SG-02 | low | `prepare_order(buying_power=)` 재사용 증거에 시각이 없어 유일 호출자 외 재사용 시 신선도 검사 불가 | 기록. `KisBuyingPower`에 `retrieved_at` 추가는 후속 |
| SG-03 | low | `recancel`이 trader.lock을 잡지 않아 진행 중 취소와 경쟁 시 중복 취소 요청 가능(브로커 업무 거절, 포지션 영향 없음) | 런북에 명시 |
| SG-04 | low | 게이트웨이 거부 4개 코드의 확정 거부 분류는 경험적(프로브) 근거 | 이 문서 "미해결 위험"에 기록. 허용 목록 최소 유지 |

## 미해결 위험 (추가)

- **주문 POST 엔드포인트에서 EGW 코드 5xx가 실제로 OMS 전달 전 거부인지 미관측.** 만약 도달 후 거부 코드가 온다면 확정 거부로 종결한 뒤 체결이 나타나 `account_exposure_unattributed`로 늦게 탐지된다. 시험운영 중 `cycle_failed`의 `broker_code=EGW*` 건수와 대사 결과를 대조해 확인한다.
- 취소 자식 행의 `orgn_odno` 표현이 미관측이라 `recancel`의 `cancel_request_already_recorded` 검사는 문서 근거 위에 있다(취소 중복은 포지션에 영향 없음).
| Gate P 확인 시점 | 코드 수정 전에 읽기 전용 프로브를 먼저 실행(사용자 수동 opt-in 1회). 토큰 재발급 응답·`CTCA0903R`·전날 만료 행 표현을 확인해 F2·F7 설계를 확정 |
| 저장소 반영 | 이 보고서와 원본 4건은 `docs/plans/`에 두되 커밋하지 않음(사용자 요청 시 `/ship`) |
