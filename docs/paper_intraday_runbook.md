# QuantPilot CLI 모의운용

2026-09-11 첫 시험은 [전용 운영표](paper_trial_20260911.md)의 별도 원장과 낮춘 한도를 사용한다.

이 경로는 500만 원의 실험 원장을 별도 관리한다. 기본값은 `fixture`, `stopped`이며 실거래·시장가·기존 자동 운용 플래그를 켜지 않는다. 초기 전략은 검증 대상이며 수익성이 입증된 전략이 아니다.

## 구성

| 책임 | 구현 | 재사용·경계 |
|---|---|---|
| 설정·실험 원장 | `quantpilot/paper/config.py`, `store.py` | 저장소 밖 SQLite, 버전 기록, 누적 체결만 자금에 반영 |
| 데이터·신호 | `data.py`, `calendar.py`, `strategy.py` | 모의 분봉, 완성 봉, 실제 세션 시각, 공개 후보 대체 경로 |
| 자금·주문 | `risk.py`, `broker.py`, `runtime.py` | 기존 durable submission, idempotency, 주문 상태 머신, 대사·감사 기록 |
| AI·연구 | `intelligence.py`, `lab.py`, `research.py`, `jobs.py` | 별도 프로세스, 평가 TTL, Docker 외 생성 코드 실행 금지 |
| 보고·제어 | `cli.py`, `reporting.py` | CLI JSON, 슬랙 DM outbox, pause/flatten 구분 |

기존 Level 승격·반복 승인 경로는 새 CLI에서 호출하지 않는다. 기존 `research_agents`와 `briefing`은 새 운용 패키지에 접근할 수 없으며 `tach check`로 경계를 검사한다. 기존 원장과 연구 기록은 이전하거나 삭제하지 않는다.

## 명령

프로젝트 루트에서 실행한다. 기본 원장은 `%USERPROFILE%\.quantpilot\intraday`다. 다른 위치는 전역 옵션 `--runtime-dir`로 지정한다. 저장소 내부 위치는 거절된다.

```powershell
python -m quantpilot.paper --json status
python -m quantpilot.paper --json config
python -m quantpilot.paper --json strategies
python -m quantpilot.paper --json pause
python -m quantpilot.paper --json resume
python -m quantpilot.paper --json flatten
python -m quantpilot.paper --json report
python -m quantpilot.paper --json review-drawdown --reason "<10자 이상>"
python -m quantpilot.paper --json recancel --order <주문 id>
python -m quantpilot.paper --json resolve-unknown --order <주문 id> --reason "<10자 이상>"
python -m quantpilot.paper --runtime-dir <원장 디렉터리> dashboard --port 8770
```

`start`·`worker`·`reporter`는 `--once`로 한 주기만 실행할 수 있다. status·report·stabilize 미리보기·acceptance는 읽기 전용이며 없는 원장을 만들지 않는다. 제어·실행 명령에는 정확한 경로를 지정한다.

`dashboard`는 읽기 전용 현황 페이지다. 원장을 `mode=ro`로만 열고 127.0.0.1에만 바인딩하며 trader·worker·reporter 잠금을 잡지 않는다. 첫 화면에는 주의 경고(incident·손실 한도·오래된 미종결 주문·보호 대기·격리), 자산·손익 타일, 평가자산 곡선, 보유·미종결 주문, 전략별 요약만 둔다. 왕복 거래·주문 타임라인·운영 상태 상세·후보 종목 상태·감사 기록은 접힌 섹션이며 펼침 상태는 브라우저에만 저장된다. 5초마다 다시 읽는다. 평가자산 곡선은 이 프로세스가 `dashboard.sqlite3`에 직접 표본을 저장하므로 서버가 꺼진 시간은 비어 있다. 제어 명령은 제공하지 않으며 `powershell -NoProfile -File scripts/paper-runtime.ps1 -Action Dashboard -RuntimeDirectory <원장 디렉터리>`로 숨김 창에서 시작할 수 있다. 기본 포트 8770이 사용 중이면 실행 중인 프로세스를 종료하지 말고 `--port`로 다른 포트를 지정한다.

설정 갱신에는 조회한 현재 버전을 전달한다. 예를 들어 PowerShell에서:

```powershell
python -m quantpilot.paper --json config --expected-version 1 --set '{"data_mode":"paper_trading"}'
```

설정 버전이 달라지면 갱신을 거절한다. 초기 자본은 고정이다. 기존 포지션의 손절·목표가·전략 버전은 진입 당시 값을 유지한다. 코드 규칙 변경은 새 버전으로 평가하며 기존 평가 결과를 승계하지 않는다.

`pause`는 신규 진입을 막고 다음 실행 주기에서 진입 미체결을 취소한다. 손절·마감 처리는 계속한다. `flatten`은 전량 청산 요청이며 원장과 주문이 모두 비면 `paused`로 바뀐다. 시장 폐장·미체결·응답 불명 상태에서는 완료를 보고하지 않는다. 재시작해도 pause 상태를 유지한다.

장 마감 처리가 끝나면 살아 있는 trader는 스스로 `paused`로 바꾼다(`auto_pause_after_close`, 기본 true, 감사 `auto_paused_after_close`). 다음 거래일 진입은 반드시 `resume`으로 명시해야 한다. 마감 시각에 trader가 죽어 있었다면 자동 pause가 기록되지 않으므로, 재시작 전에 `status`로 `control`을 확인하고 필요하면 `pause`한다.

일손실 1%·누적 낙폭 5% 한도는 전략 세대(`legacy`, `intraday_v2`)와 무관하게 신규 진입을 막고 거래당 위험 예산도 남은 손실 예산 안으로 제한한다. 상태는 `intraday_loss_state`로 `status`·대시보드에 나타나고 재시작·정책 변경을 넘어 유지된다. 이미 초기 자본 대비 5% 이상 잃은 기존 `legacy` 원장은 첫 기동에서 바로 누적 낙폭 halt가 된다. 해제는 `review-drawdown --reason <10자 이상>`뿐이며(원장이 비고 `paused`일 때만) 주문을 다시 켜지 않는다.

## 응답 불명 상태의 운영자 해소

브로커 응답이 끊긴 주문은 자동으로 재전송하거나 종결하지 않는다. `outcome_unknown`·`cancel_unknown` 상태가 5분(`manual_resolution_after_seconds`) 넘게 남으면 하루 한 번 `manual_resolution_required` DM을 보내고 신규 진입은 막힌 채 보호 매도만 계속한다. 두 명령 모두 `paused` 상태와 계좌 결합 일치를 요구하고, 전송은 토큰·잔고·일별 주문 조회만 허용된다.

```powershell
# 취소 POST가 응답 없이 끝난 주문: 원주문이 여전히 미체결이고 취소 자식 행이 없음을 일별 조회로 확인한 뒤 claim만 해제한다.
# trader가 다음 주기에 취소를 한 번 더 보낸다. 취소 자식 행이 있거나 잔량이 0이면 거절한다. 당일 주문에만 적용되고
# 전날 주문은 항상 거절된다(다음 거래일 조회로 종결 확인). trader 잠금은 잡지 않으므로 paused 상태의 trader가
# 같은 순간 취소를 보내고 있었다면 브로커가 두 번째 취소를 업무 거절하고 대사가 종결을 확인한다(포지션 영향 없음).
python -m quantpilot.paper --runtime-dir <원장> --json recancel --order <주문 id>

# 주문 POST가 응답 없이 끝나고 브로커에 흔적이 없는 주문: trader를 멈춘 뒤(trader.lock 필요) 실행한다.
# 10분 경과 + 최신 대사에서 일치 행 0건 + 같은 종목·방향·수량·가격의 주인 없는 당일 행이 없음 + 계좌 보유수량이
# 원장과 일치할 때만 rejected로 종결하고 사유를 커널 이벤트(source=operator_resolution)와 감사에 남긴다.
# 브로커가 주문을 보여주면 대신 accepted로 기록하고 거절한다.
python -m quantpilot.paper --runtime-dir <원장> --json resolve-unknown --order <주문 id> --reason "<10자 이상>"
```

`recancel`·`resolve-unknown`이 주 1회 넘게 필요하면 시험운영 감사 건수(`cancel_failed`, `outcome_unknown`)를 근거로 조건부 자동 재시도 도입을 별도 검토한다.

## Windows 실행

전용 Python 환경은 저장소 밖 `%USERPROFILE%\.quantpilot\runtime-venv`에 둔다. 설치·재현은 `powershell -NoProfile -File scripts/setup-paper.ps1`로 수행한다. 기존 Python 환경을 덮어쓰지 않는다. 이후 직접 Python 명령은 이 환경의 `Scripts\python.exe`를 사용한다.

운용 전 아래 수동 점검을 끝낸 명시적 모의 프로필에서만 시작한다. `KIS_PAPER_ORDER_SUBMISSION_ENABLED=true`가 필요하며 실행기는 환경이나 자격정보를 변경하지 않는다. 기존 실거래·시장가·자동 운용 플래그는 모두 false다.

```powershell
powershell -NoProfile -File scripts/paper-runtime.ps1 -Action Start
```

실행기는 숨김 창으로 trader, AI worker, reporter를 각각 시작한다. 분봉 수집은 별도 스레드가 담당한다. 대화창과 독립적으로 실행되며 역할별 잠금과 계좌별 잠금으로 중복 운용을 거절한다. AI가 지연되어도 reporter는 별도 주기로 알림을 전달한다. PC 종료·절전 중에는 실행되지 않는다. 실제 모의주문은 별도 수동 인수 절차에서 시작한다.

각 역할의 stdout·stderr는 원장 디렉터리의 `logs\<역할>-<날짜>.out.log`·`.err.log`에 남는다. 시작이 거절되거나 크래시하면 그 파일과 감사 `process_failed`(Slack이 켜져 있으면 DM)로 확인한다. 거래소 달력의 거래 세션 전후 10분 동안 trader heartbeat가 3분 넘게 끊기면 reporter가 한 시간에 한 번 `liveness` DM을 보낸다. heartbeat는 실행 루프의 생존을 나타내며 주문 가능 여부는 entry_gates에서 별도로 확인한다. 감독 기능을 선택한 프로필의 재시작 규칙은 아래 안정화 절차를 따른다.

KIS 토큰은 1분에 1회만 발급되고 유효기간 안에서는 같은 토큰이 다시 내려온다. 여러 프로세스가 각자 발급해도 앞 토큰은 무효화되지 않지만, 1분 안에 두 번째 발급은 거절되므로 `Readiness`→`reconcile`→`Start`처럼 연달아 실행할 때는 1분 간격을 둔다. 이미 토큰을 가진 프로세스는 발급이 거절돼도 기존 토큰으로 계속 동작한다. 모의서버 조회 한도는 초당 약 2건이며 초과분은 HTTP 500 `EGW00201`로 거절되고 주문에 도달하지 않는다(확정 거부로 분류). 안정화 예산은 저장소 밖 SQLite를 공유하며, 증명된 한도 오류의 GET만 최대 두 번 재시도한다. 주문·취소 POST와 결과 미상 전송은 자동 재시도하지 않는다.

`exchange-calendars==4.13.2`·`websockets==15.0.1`과 cryptography==50.0.1이 필요한 선택 의존성이다 (`paper` extra, `setup-paper.ps1`이 설치). 달력이 없으면 시장 시각을 추측하지 않고 시작을 거절한다. KIS 휴장일 조회(`CTCA0903R`)는 모의서버가 지원하지 않으므로 거래일 정보는 설치된 달력 버전과 실제 거래소 공지를 수동 점검에서 대조해야 한다.

## 고정된 초기 전략·비용 가정

- 장초반 돌파: 개장 15분 고점의 완성 1분봉 종가 돌파, 직전 봉은 범위 이하, 거래량은 개장 구간 평균의 1.5배 이상, 개장 후 90분 이내.
- 추세 눌림목: 완성 5분봉 EMA20 상승, 눌림목 이후 EMA 회복. 기본 ATR14 손절 거리, 목표 2R.
- 횡보 되돌림: 5분봉 최근 20개 범위 폭 3% 이하, 하단 20% 구간과 RSI14의 35 상향 회복. 기본 ATR14 손절 거리, 목표 2R.
- 데이터가 부족하거나 중간 봉이 누락되면 해당 신호를 만들지 않는다. 과거 봉을 미래 데이터로 채우지 않는다.
- 초기 모델 비용: 편도 수수료 1.40527bp, 매도세금 20bp, 추가 슬리피지 편도 5bp. 이는 실험 가정이며 종목·계좌의 실제 요율을 확인했다는 뜻이 아니다. 체결 총액은 따로 보존한다.
- 실험 자산 기준 계획 손실 0.5%, 전략 60%, 종목 25%, 동시 4종목 한도. AI는 이 상한을 변경할 수 없다. 미실현 평가이익을 신규 운용 자본에 더하지 않는다.

마감 기준 신규 진입 종료는 폐장 30분 전, 청산 시작은 20분 전이다. 마감 미청산분은 격리하고 잔여 실험 현금만 사용한다. 계좌 보유 수량과 실험 귀속이 일치하지 않으면 신규 진입을 막는다. 수량이 일치하는 기존 종목의 손절·청산은 계속하며, 불일치 종목에는 매도 주문을 만들지 않는다. 별도 보유 종목이 있는 계좌는 전체 일치 검증을 통과하지 못하므로 수동 점검에서 확인한다.

## AI·생성 전략·보고

시간별 평가는 최대 75분 동안만 사용한다. 데이터 관측 시점부터 유효기간을 계산하고 만료·실패 시 규칙 점수로 돌아간다. 기본 제공자는 Claude이며 실패 시 Codex로 한 번 전환한다. AI는 수량·주문·승인 권한을 갖지 않는다.

생성 연구는 기본 비활성이다. 명시적인 digest 고정 Docker 이미지가 로컬에 있고 엔진·격리가 검증되어야 실행한다. 이미지 자동 다운로드는 하지 않는다. 후보 생성은 하루 한 번, shadow 최대 세 개, paper trial 한 개·최대 10%다. 코드 해시가 바뀌면 이전 증거는 무효다. 편입은 독립 검토·고정 검사와 동일 버전의 최소 5거래일·청산 20건·비용 반영 양수 성과를 요구한다. 가상 평가 손익은 실험 현금과 분리한다.

슬랙은 `SLACK_BOT_TOKEN`과 본인 DM 대상 `SLACK_ALLOWED_USER_ID`를 런타임에서만 사용한다. 기존 프로필은 장후 복기와 숫자를 함께 보고한다. independent_reports_enabled 프로필은 마감 후 60초 이내 운영 숫자를 먼저 생성하고 AI 복기를 별도로 보고한다. 종가 증거가 없으면 미확인을 표시하고 확인 뒤 한 번 정정한다. 이는 outbox 생성 기한이며 외부 Slack 장애 때의 전달 시각을 보장하지 않는다. 응답 유실은 `delivery_unknown`으로 기록하고 자동 재전송하지 않는다. 운영자가 슬랙을 확인한 뒤 판단한다. 긴급 알림은 outbox에 즉시 기록하며 별도 reporter가 전달한다.

## 검증과 실제 연결 인수

```powershell
python scripts/verify-paper.py
& "$env:USERPROFILE/.local/share/aorch-tools/.venv/Scripts/tach.exe" check
```

검증 스크립트는 호스트의 브로커·슬랙 환경을 제거한 자식 프로세스에서 `python -m pytest quantpilot/tests`와 `python -m quantpilot.jobs.run_smoke`를 실행한다. fixture 테스트는 실제 네트워크·자격정보를 사용하지 않는다.

실제 연결 인수는 별도 수동 절차다. 자격정보를 출력하지 않은 상태에서 다음 증거를 남긴다.

1. 모의 계좌·모의 호스트 확인, 실험 밖 현금 보존, 잔고와 주문 대사.
2. 당일 분봉 지원·시간·완성 봉 확인, 후보 탐색의 모의 지원 여부와 공개 대체 데이터 출처 확인.
3. 소액 지정가 주문 한 건의 접수·체결·취소, 부분체결과 응답 유실 시 조회 복구, 재시작 중복 방지 확인.
4. pause 상태 유지, 보호 매도, flatten 완료 추적, 장마감 미청산 격리 확인.
5. 양쪽 구독 CLI의 도구 제한과 실패 전환, Docker 격리, 본인 슬랙 DM 전달 확인.

실제 모의 API·슬랙 전송·Docker 코드 실행은 자동 테스트 통과와 구별해 기록한다. 과거 수익성을 운용 시작 조건으로 요구하지 않는다.

참고: [한투 당일 분봉 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_time_itemchartprice/inquire_time_itemchartprice.py), [수급 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor/inquire_investor.py), [Codex 설정](https://developers.openai.com/codex/config-reference/).

## 체결 누락 진단과 원장 복구

`python -m quantpilot.paper --runtime-dir <원장> --json reconcile`은 브로커 잔고와
일별 주문·체결을 조회하고 메모리에 복사한 원장에만 대사를 적용한다. 기본값은 읽기 전용이며
`--dry-run`으로 명시할 수도 있다. 계좌 결합과 `paused` 상태가 필요하고 주문 제출 플래그는 필요 없다.

`reconcile --apply`는 trader·worker·reporter·계좌 잠금을 모두 확보한 뒤 최신 미리보기,
두 DB의 SQLite 백업, 실제 대사 순서로 처리한다. 해당 프로세스가 실행 중이면 실패한다.
백업은 원장 디렉터리의 `recovery-backups`에 보관한다. 주문·취소 POST는 transport에서 차단한다.
수량이나 매입가를 직접 입력하지 않으며, 커널 반영 뒤 중단돼도 재실행으로 누적 체결을 한 번만 반영한다.

개장 전·장후·비거래일에 복구한 보유분과 이전 거래일의 이월분은 격리한다. 신규 진입 중지를
유지하고 기존 운용기를 재시작하면 다음 거래 가능 시간에 대사·호가·위험 점검을 거쳐 지정가
청산을 시도한다. 접수나 시각 도달만으로 청산 완료로 보지 않는다.

변경이 반영되면 미전송 과거 보고는 보존한 채 `superseded`로 표시하고 정정 보고를 한 번 큐에 넣는다.
Slack 설정은 자동으로 활성화하지 않으며, `delivery_unknown`은 재전송하지 않는다.
정정 보고는 복구 적용 시점의 상태다. 재시작 후 현재 보호·전송 상태는 `status`로 확인한다.
이전 자산 표본은 삭제하지 않고 그래프에서 제외한다. 전일 평가 기준이 불확실하면 당일 손익을
확인 불가로 표시하고 신규 진입을 막는다. 실시간 평가와 누적 손익의 확인 여부는 별도로 표시한다.

## 9월 14일 안정화 프로필 전환

[승인된 구현 계약](plans/2026-09-14-paper-stabilization.md)을 따른다. 네 기능(shared_api_budget_enabled, hybrid_feed_enabled, supervisor_enabled, independent_reports_enabled)은 새 설치에서 모두 false다. 현재 시험의 legacy 전략·500만원·동시 1종목·종목 10%·거래 위험 0.1%·일 손실 1%·낙폭 5%를 유지한다.

운영 적용은 별도 승인을 받은 뒤 수행한다. 사용 중인 환경 로더는 계속 사용하며 자격정보를 문서나 명령 출력으로 복사하지 않는다. 새 통지 입력 KIS_PAPER_HTS_ID는 모의 계좌의 HTS 사용자 식별값으로, 환경에서만 읽는다. 스트림의 복호화 키·IV는 메모리에만 유지한다.

1. 지정한 시험 원장을 pause하고 미체결과 보유를 확인한다. Stop은 서비스 종료이며 청산을 뜻하지 않는다. 보유 보호가 필요한 동안 서비스를 중단하지 않는다.
2. `paper-runtime.ps1 -Action Stop -RuntimeDirectory <경로>`로 감독 프로세스와 역할을 정상 종료하고 잠금 해제를 확인한다. 운영 가상환경에는 setup-paper.ps1의 고정 의존성을 적용한다.
3. `python -m quantpilot.paper --runtime-dir <경로> --json stabilize`로 버전·한도·중지 상태를 검토한다. `stabilize --apply --expected-version <조회 버전>`은 두 원장 백업 및 무결성 확인 후 기능만 선택한다. 자동 재개하지 않는다.
4. `paper-runtime.ps1 -Action Start -RuntimeDirectory <경로>`는 해당 프로필에 감독 프로세스 한 개를 숨김으로 시작한다. 감독 프로세스는 자신이 만든 trader·worker·reporter만 관리하며, 정상 종료 요청 후 30초 유예를 거쳐 자신이 소유한 자식만 종료한다. 역할별 하루 재시작 한도는 3회(30/60/120초), 소진 상태는 supervisor 진단에 표시한다. OS 부팅 작업은 등록하지 않는다.
5. 재시작 대사·시세·기준·손실 조건을 확인한다. 당일 resume 권한이 있어도 이 조건을 충족하기 전에는 진입하지 않는다. 다음 거래일은 새 resume이 필요하며 수동 pause와 손실 halt는 유지한다.

후보 20개 틱, 보유·미체결과 상위 5개 호가, 암호화 계좌 통지를 합쳐 40구독 이내다. 슬롯은 해제 ACK 뒤 재사용하고 재접속·미확인 구독·오래된 시세는 신규 진입을 막는다. 통지는 대사 힌트만 제공하며 현금과 체결은 REST 증거로만 반영한다. 확정 분봉은 종료 후 최소 15초 유예를 지나 저장한다. 확정 뒤 수정된 종목은 당일 진입을 격리하고 보유분 보호는 계속한다.

status에는 진입 차단 이유·누적 시간, 범위별 장애, 구독 상태, 공통 API 대기·처리 시간과 한도 거부, 감독 상태·당일 재개 권한, 종가 증거, AI 실행기·시도 내역이 나타난다. AI 복기는 최초 시도에서 1분·5분 후, 총 3회까지 시도하며 제공자별 실패를 남긴다.

네 개의 완전한 거래일이 지난 뒤 `python -m quantpilot.paper --runtime-dir <경로> --json acceptance --start-day YYYY-MM-DD`로 증거를 판정한다. 누락은 성공으로 처리하지 않는다. 현금·수량 보존, 중복 주문·체결, 분봉 유예, 마감 시 보유·미체결 0, 기준과 보고·AI 종료, API 한도, 장 시작 전 기동, 실제 시장 입력과 기간 내 체결 통지 증거, 수동 개입 횟수를 확인한다. 거래가 없는 날에 체결을 강제로 만들지 않는다. 수익률은 인수 기준에 없다.

되돌릴 때는 pause → 미체결 대사 → 서비스 정상 종료 후 현재 정책 버전으로 hybrid_feed_enabled=false, supervisor_enabled=false, independent_reports_enabled=false를 설정하고 REST 운용을 재개한다. 공유 API 예산은 다른 모의 프로세스와의 한도 보호를 위해 유지한다. 새 체결이 존재하는 원장 위에 과거 백업을 복원하지 않는다.

### 감독·보고를 유지하는 REST 전환

체결통보 구독 장애가 지속되면 기존 REST 시세·체결 대사 경로로 전환할 수 있다. 운영자의 모의 가동 요청 범위에서 pause와 보유·미체결 상태를 확인하고 감독 프로세스를 정상 종료한다. 원장 백업 후 현재 정책 버전을 지정해 `hybrid_feed_enabled=false`만 변경하고 재시작한다. 공유 API 예산·감독·독립 보고와 기존 전략·위험 한도는 유지한다. 실행 중 설정만 바꾸면 이미 생성된 피드 객체는 없어지지 않으므로 재시작이 필요하다.

REST 재시작 검증은 정상 대사, 유효한 일일 기준, 사용 가능한 손실 예산, 모든 후보의 신선한 확정 분봉을 요구한다. 이 조건을 충족하고 피드 객체가 없을 때만 기존 `feed_unavailable` 장애를 감사 기록과 함께 해소한다. 다른 피드 오류·다른 범위의 장애·수동 pause는 유지된다. 운영자는 신선한 REST 호가와 대사, 월 기준, kill switch, 미확인 주문, 역할 heartbeat와 알림을 확인한 뒤 당일 resume을 수행한다. 주문 직전의 대사·호가·위험·중복 방지 검사는 계속 적용된다. 체결통보를 사용하지 않는 기간을 체결통보 수신 또는 하이브리드 4거래일 인수 완료로 표시하지 않는다.

당일 분봉 요청은 `FID_PW_DATA_INCU_YN=N`을 사용한다. 개장 초반 `Y` 응답에 전일 행이 섞여 당일 응답 전체가 거절되는 문제를 방지한다. 응답의 전일·미래 시각 거절과 종료 후 15초 확정 유예는 그대로 유지한다.


월말 종가가 없거나 원장 정정으로 월 기준이 무효가 되면 월 위험 검사는 계속 진입을 차단한다. 과거 종가를 임의로 복원하지 않는다. 당일 종가가 대사로 확인되고 보유·미체결이 없을 때 서비스를 정상 종료한 뒤 month-baseline으로 현재 월 기준과 제안된 새 기준을 미리 확인할 수 있다. `month-baseline --apply --expected-version <버전> --reason <검토 사유>`는 확인된 당일 종가에서 새 월 위험 측정 기간을 시작하는 운영자 명령이다. 일일 기준·일 손실 정지·누적 낙폭 정지·pause는 그대로 유지된다. 이 변경은 baseline_override로 감사되므로 해당 날짜를 포함한 4거래일 인수를 통과할 수 없다. 재설정 이후의 월 손실 검사는 이전 월간 손실을 포함하지 않으므로 운영자가 이 영향을 검토해야 한다. 숫자를 직접 입력해 주문을 허용하는 명령이 아니다. 잘못된 기존 디렉터리에 apply를 실행하면 빈 기본 원장 파일이 만들어질 수 있으므로 읽기 전용 미리보기로 대상 경로를 먼저 확인한다.
