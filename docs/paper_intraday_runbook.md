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
```

설정 갱신에는 조회한 현재 버전을 전달한다. 예를 들어 PowerShell에서:

```powershell
python -m quantpilot.paper --json config --expected-version 1 --set '{"data_mode":"paper_trading"}'
```

설정 버전이 달라지면 갱신을 거절한다. 초기 자본은 고정이다. 기존 포지션의 손절·목표가·전략 버전은 진입 당시 값을 유지한다. 코드 규칙 변경은 새 버전으로 평가하며 기존 평가 결과를 승계하지 않는다.

`pause`는 신규 진입을 막고 다음 실행 주기에서 진입 미체결을 취소한다. 손절·마감 처리는 계속한다. `flatten`은 전량 청산 요청이며 원장과 주문이 모두 비면 `paused`로 바뀐다. 시장 폐장·미체결·응답 불명 상태에서는 완료를 보고하지 않는다. 재시작해도 pause 상태를 유지한다.

## Windows 실행

전용 Python 환경은 저장소 밖 `%USERPROFILE%\.quantpilot\runtime-venv`에 둔다. 설치·재현은 `powershell -NoProfile -File scripts/setup-paper.ps1`로 수행한다. 기존 Python 환경을 덮어쓰지 않는다. 이후 직접 Python 명령은 이 환경의 `Scripts\python.exe`를 사용한다.

운용 전 아래 수동 점검을 끝낸 명시적 모의 프로필에서만 시작한다. `KIS_PAPER_ORDER_SUBMISSION_ENABLED=true`가 필요하며 실행기는 환경이나 자격정보를 변경하지 않는다. 기존 실거래·시장가·자동 운용 플래그는 모두 false다.

```powershell
powershell -NoProfile -File scripts/paper-runtime.ps1 -Action Start
```

실행기는 숨김 창으로 trader, AI worker, reporter를 각각 시작한다. 분봉 수집은 별도 스레드가 담당한다. 대화창과 독립적으로 실행되며 역할별 잠금과 계좌별 잠금으로 중복 운용을 거절한다. AI가 지연되어도 reporter는 별도 주기로 알림을 전달한다. PC 종료·절전 중에는 실행되지 않는다. 시작 실패는 `status`의 heartbeat와 incident로 확인한다. 실제 모의주문은 별도 수동 인수 절차에서 시작한다.

`exchange-calendars==4.13.2`가 필요한 선택 의존성이다 (`paper` extra). 설치되지 않으면 시장 시각을 추측하지 않고 시작을 거절한다. 거래일 정보는 설치된 달력 버전과 실제 거래소 공지를 수동 점검에서 대조해야 한다.

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

슬랙은 `SLACK_BOT_TOKEN`과 본인 DM 대상 `SLACK_ALLOWED_USER_ID`를 런타임에서만 사용한다. 장후 복기 종료 후 코드 계산 수치와 실패 내역을 전송한다. 응답 유실은 `delivery_unknown`으로 기록하고 자동 재전송하지 않는다. 운영자가 슬랙을 확인한 뒤 판단한다. 긴급 알림은 outbox에 즉시 기록하며 별도 reporter가 전달한다.

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
