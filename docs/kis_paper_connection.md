# 한투 모의투자 연결

사용자 환경 변수 `KIS_PAPER_APP_KEY`, `KIS_PAPER_APP_SECRET`,
`KIS_PAPER_ACCOUNT_NUMBER`를 등록한 뒤 새 터미널에서 프로젝트 루트를 열고 실행한다.

```powershell
python -m quantpilot.jobs.check_kis_paper_connection
```

계좌번호는 앞 8자리, 전체 10자리 또는 `앞8자리-뒤2자리`를 받는다.
상품코드는 `KIS_PAPER_PRODUCT_CODE` 또는 계좌번호 뒤 2자리에서 가져오며,
둘 다 없으면 국내주식 종합계좌 코드 `01`을 사용한다. 두 입력이 충돌하면 중단한다.
앱키와 시크릿은 반드시 모의투자용이어야 한다.

`KIS_PAPER_ACCESS_TOKEN`이 없으면 토큰을 한 번 발급해 메모리에서만 사용한다.
기존 토큰을 제공하면 재발급하지 않는다. 만료된 토큰은 제거하거나 갱신한다.
이 명령은 일회성 수동 점검용이다. 토큰 발급 제한을 피하도록 반복 스케줄링하지 않는다.
등록된 Windows 환경 변수 변경은 기존 프로세스에 자동 반영되지 않는다.
`.env`는 자동으로 읽지 않으며 비밀값을 명령행 인자로 받지 않는다.

`status: connected`는 모의투자 서버의 삼성전자 시세와 해당 계좌 잔고 조회가
모두 성공했다는 뜻이다. 출력에는 키·토큰·계좌번호·보유 내역·금액을 포함하지 않는다.
실패 시 `stage`가 실패한 단계이고 명령은 종료 코드 1을 반환한다.
`broker_code: 90070000` / `reason: paper_account_user_mismatch`는 한투가
모의투자 처리계좌 ID와 사용자정보 불일치로 거절한 경우다. API 키를 발급한
사용자와 모의투자 계좌 연결 정보를 한투에서 확인해야 한다.
공식 공지는 HTS ID 변경 또는 실전/모의 계좌 혼동을 원인으로 안내한다.
한투 앱 `메뉴 > 모의투자 > 모의투자 로그인 > 상시 모의투자 > 메뉴 > 주식 잔고 손익`
또는 홈페이지 `트레이딩 > 모의투자 > 주식/선물옵션 모의투자 > 모의투자안내 > 나의계좌`에서
실제 모의계좌를 확인하고 API 신청 계좌 및 환경 변수와 비교한다.
HTS ID를 변경한 경우 이전 ID 신청정보 삭제 및 재신청에 관한 공식 안내를 따른다.
이 명령은 계정 신청정보를 자동 변경하지 않는다.
명령은 인증 POST와 두 조회 GET만 허용한다. 주문·취소는 지원하지 않는다.

이 연결 확인에는 자동매매 정책이나 상태 DB가 필요하지 않다.
기존 자동주문 세션은 별도 정책·위험 검증·실행 허용 설정을 요구하며,
이 명령이 해당 설정을 변경하지 않는다. 실거래는 비활성 상태를 유지한다.

공식 근거:

- [한투 공식 설정 예제: 계좌 구분과 상품코드](https://github.com/koreainvestment/open-trading-api)
- [공식 인증 코드: 모의투자 앱키로 토큰 발급](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/kis_auth.py)
- [공식 국내주식 잔고 조회](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_balance/inquire_balance.py)
- [개발자센터 모의투자 API 사용 안내: 90070000](https://apiportal.koreainvestment.com/)

## Windows 실행과 자동운용 준비

최신 Windows 사용자 환경 변수를 반영하는 실행기:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts/kis-paper.ps1" -Action Check
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts/kis-paper.ps1" -Action Prepare
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts/kis-paper.ps1" -Action Smoke
```

`Prepare`는 기본적으로 `%USERPROFILE%\.quantpilot\paper`에 다음을 만든다.
`-RuntimeDirectory`로 다른 저장소 밖 경로를 지정할 수 있다.

- `state.sqlite3`: 현재 계좌의 해시 지문에 묶인 paper 상태 DB. 주문·체결·손실 기준선을 생성하지 않는다.
- `policy.draft.json`: 기존 모델 기본값의 검토용 정책. kill switch가 켜져 있고 mock/Level 2이며 자동운용은 꺼져 있다.
- `registry.draft.json`: 현재 `pullback_trend_v2` 버전·해시가 연결된 draft. 승인·검증 이력이나 실행 권한은 없다.
- `historical/`: 실제 국내주식 CSV를 넣을 빈 폴더.
- `runtime.json`: 경로와 아직 충족해야 할 활성화 요건. 비밀값을 포함하지 않는다.

기존 파일은 보존한다. `prepared_not_armed`는 파일 준비 결과이며 기존 파일의 유효성이나
실행 가능 여부를 보증하지 않는다. 다른 계좌로 기존 DB를 다시 묶으려 하면 실패한다.
실행기는 사용자 환경 변수를 영구 변경하지 않으며 토큰도 저장하지 않는다.
`Smoke`는 해당 프로세스에서 모의투자 자격정보를 제외하고 mock/fixture 검증을 실행한다.
스모크 출력의 체결은 가짜 체결이며 한투에 전송되지 않는다.

운용 한도와 대상 종목이 미정이면 초안을 승격하지 않는다. 실제 자동운용에는 다음이 남는다.

1. 사용자 운용 한도·대상 종목 결정과 정책 검토.
2. 대상 종목의 `securities.csv`·`ohlcv.csv` 수집, 신선도·품질 검증 및 백테스트.
3. 실제 전략 검증 증거와 사람 승인에 따른 승격 이력. 가짜 fixture 이력을 복사하지 않는다.
4. 사람이 확인한 전일 종가 자산·월초 자산 기준선.
5. 승인 영업일과 전용 paper 세션의 명시적 활성화.

이후 실행 절차는 [paper 세션 운영 문서](_archive/krx_kis/kis_paper_session_runbook.md)를 따른다.
조회용 자동 토큰 발급은 기존 자동주문 세션에 토큰을 주입하거나 스케줄링하는 기능까지 제공하지 않는다.
