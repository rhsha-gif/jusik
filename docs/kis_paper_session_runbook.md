# KIS Paper Session Runbook

이 문서는 QuantPilot의 KIS 모의투자 1회 세션 작업기를 안전하게 운영하는 절차다. 기본 설정에서는 작업기가 즉시 `paper_session_disabled`로 끝나며 네트워크와 자격증명에 접근하지 않는다. 실전투자 호스트와 live 주문은 지원하지 않는다.

## 운영 경계

- OHLCV 판단 데이터는 현재 `local_historical`만 허용한다. `external_historical`은 paper origin에 고정된 전용 hardened provider가 추가될 때까지 차단한다.
- 실행 호가는 승인된 KRX 영업일의 KIS paper L2만 사용한다.
- 주문은 지정가만 허용하며 시장가 전환은 없다.
- 모든 외부 POST 전에 SQLite 디스패치 저널을 `prepared`로 기록하고 단 한 번 `dispatch_claimed`로 선점한다.
- `dispatch_claimed` 또는 `outcome_unknown`은 브로커 조회로만 조정하며 재전송하지 않는다.
- SQLite 상태 DB는 계좌번호 대신 `sha256` 계정 범위 지문만 저장한다. DB에는 포트폴리오·주문 증거가 있으므로 저장소 밖의 접근 제한 디렉터리를 권장한다.
- access token은 작업기 밖의 비밀 관리 절차에서 갱신해 주입한다. 1분 작업기가 토큰 발급을 반복하지 않는다.

## 사전 파일

`KIS_PAPER_POLICY_FILE`은 이미 사람의 검토를 거친 `UserPolicy` JSON이어야 한다. 작업기는 아래 값을 자동으로 바꾸거나 승격하지 않는다.

- `broker: "paper"`
- `authority_level: 5`
- `execution_mode: "fully_automated"`
- `fully_automated_operator_enabled: true`
- `guarded_autopilot_enabled: false`
- `kill_switch_engaged: false`
- `allowed_order_types: ["limit"]`

`KIS_PAPER_REGISTRY_FILE`은 다음 두 배열을 가진 JSON 객체다.

```json
{
  "entries": [],
  "lifecycle_records": []
}
```

정확히 `pullback_trend_v2` 한 종목전략만 Level 5 대상이어야 한다. 레지스트리의 `spec_hash`는 현재 recipe 해시와 같아야 하고, 생명주기는 `live_candidate`까지 사람 확인 이력과 `paper_track_record`, `risk_review` 증거를 포함해야 한다. 여기서 `live_candidate`는 전략 검증 단계 이름일 뿐 live 주문 권한을 켜지 않는다.

## 손실 기준선 기록

첫 세션과 매월 첫 세션은 사람이 전일 종가 자산과 월초 자산을 확인한 뒤 기준선을 기록한다. 값은 환경 또는 로컬 비밀 관리자를 통해 전달하고 저장소 파일에 넣지 않는다.

필수 환경 이름:

- `KIS_PAPER_BASELINE_CONFIRMATION=confirm paper loss baseline`
- `KIS_PAPER_STATE_DB` — 절대 경로, `:memory:` 금지
- `KIS_PAPER_APPROVED_BUSINESS_DATE`
- `KIS_PAPER_BASELINE_SOURCE_DATE`
- `KIS_PAPER_PRIOR_CLOSE_EQUITY`
- `KIS_PAPER_MONTH_START_EQUITY`
- `KIS_PAPER_ACCOUNT_NUMBER`
- `KIS_PAPER_PRODUCT_CODE`
- `LIVE_TRADING_ENABLED=false`

실행:

```powershell
python -m quantpilot.jobs.record_paper_loss_baseline
```

같은 날짜와 같은 값의 재실행은 기존 증거를 반환한다. 다른 값으로 덮어쓰기는 차단된다. 기준선이 없으면 세션은 `paper_loss_baseline_missing`으로 닫힌다.

## 1회 세션 실행

명시적 안전 게이트:

- `KIS_PAPER_SESSION_ENABLED=true`
- `KIS_PAPER_ORDER_SUBMISSION_ENABLED=true`
- `FULLY_AUTOMATED_OPERATOR_ENABLED=true`
- `LIVE_TRADING_ENABLED=false`
- `MARKET_ORDERS_ENABLED=false`
- `GUARDED_AUTOPILOT_ENABLED=false`
- `BROKER_MODE=paper`
- `DATA_MODE=local_historical`

추가 필수 입력:

- `KIS_PAPER_STATE_DB`
- `KIS_PAPER_POLICY_FILE`
- `KIS_PAPER_REGISTRY_FILE`
- `KIS_PAPER_APPROVED_BUSINESS_DATE`
- `KIS_PAPER_APP_KEY`
- `KIS_PAPER_APP_SECRET`
- `KIS_PAPER_ACCOUNT_NUMBER`
- `KIS_PAPER_PRODUCT_CODE`
- `KIS_PAPER_ACCESS_TOKEN`
- 로컬 과거데이터 제공자에 필요한 기존 `DATA_MODE` 설정

`DATA_MODE=external_historical`은 현재 `paper_external_historical_origin_not_hardened`로 즉시 차단된다. 일반 KIS 과거데이터 제공자는 production origin을 사용할 수 있으므로 paper 세션에 주입하지 않는다. paper origin 고정, redirect/proxy 차단, 응답 검증을 갖춘 전용 historical provider가 마련되기 전에는 이 게이트를 해제하지 않는다.

실행:

```powershell
python -m quantpilot.jobs.run_kis_paper_session
```

Windows 작업 스케줄러는 정규 자동주문 시간에 1분 간격으로 위 명령을 실행할 수 있다. 매 실행은 fenced lease를 얻고 다음 순서를 따른다.

1. POST 전 중단으로 남은 만료 `prepared` 기록을 외부 전송 없이 종료한다.
2. `dispatch_claimed`, `outcome_unknown`, 접수·부분체결 주문을 KIS 조회로 조정한다.
3. 영속 주문 원본이 정확히 일치할 때만 로컬 주문·브로커·체결 저널을 복원한다.
4. 새 체결을 영속 managed-position 원장에 귀속한다.
5. 보유 포지션의 8%/2ATR 손절과 기술적 퇴출·축소를 먼저 평가한다.
6. 보호 주문이 없고 전략 상태가 active인 경우에만 주간 리밸런싱을 평가한다.
7. 세션 lease를 닫는다.

`outcome_unknown`, 브로커 매칭 중복, 로컬/영속 증거 충돌, 기준선 누락, 오래된 데이터, 세션 날짜 불일치가 있으면 신규 자동 매수는 중지된다. 미확정 매수가 있어도 검증된 보호 매도는 별도 위험 게이트를 통과할 수 있다.

## 브로커 조회기간 만료 주문

KIS paper 일별 주문·체결 조회는 실행일을 기준으로 최근 3개월의 달력 날짜 경계까지만 사용한다. 예를 들어 실행일이 7월 10일이면 4월 10일은 포함하고 4월 9일 이전 주문은 자동 조회 대상에서 제외한다. 월말은 해당 월의 마지막 날짜로 보정한다.

조회기간보다 오래된 `dispatch_claimed`, `outcome_unknown`, 접수 또는 부분체결 기록은 `broker_history_window_manual_resolution_required`로 차단한다. 세션 최상위 결과는 `paper_broker_history_manual_resolution_required`로 구분한다. 이 기록은 자동 재전송하지 않으며, 오래된 한 건 때문에 조회 가능한 최신 주문의 조정까지 중단하지 않는다.

이 상태가 표시되면 운영자는 다음 증거를 KIS 모의투자 화면에서 확인해 저장소 밖의 승인된 운영 기록에 보존한다.

1. 주문 영업일과 주문번호
2. 종목, 매수·매도 구분, 주문수량과 가격
3. 체결수량, 미체결수량, 취소·거부 상태
4. 확인 시각과 확인 담당자

현재 작업기에는 오래된 주문을 임의로 확정하는 명령이 없다. SQLite DB를 직접 수정하거나 같은 주문을 다시 전송하지 말고 차단 상태를 유지한 채 안전 검토로 넘긴다. 향후 감사 가능한 수동 해결 절차가 추가되기 전에는 이 상태에서 신규 자동 매수를 재개하지 않는다.

## 수동 연동 확인

자동 테스트는 인터넷과 자격증명을 사용하지 않는다. 실제 KIS paper 확인은 모든 위 게이트를 준비한 뒤에만 다음처럼 별도로 실행한다.

```powershell
$env:RUN_KIS_MANUAL_INTEGRATION='1'
python -m pytest quantpilot/tests/integration/test_kis_paper_operator_manual.py
```

이 검사는 모의투자만 대상으로 한다. live host, live 계좌, 시장가 주문은 허용되지 않는다.
