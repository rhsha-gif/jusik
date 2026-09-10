# 2026-09-11 단타 모의 시험운영표

목표: 기존 세 전략 + AI 평가 + 장후 복기 + 본인 Slack 보고를 하루 검증한다.
수익률은 인수 조건이 아니다. 실거래·시장가·생성 전략은 사용하지 않는다.

## 준비한 프로필

- 전용 Python: `%USERPROFILE%\.quantpilot\runtime-venv\Scripts\python.exe`
- 시험 원장: `%USERPROFILE%\.quantpilot\intraday-trial-20260911\experiment.sqlite3`
- 초기 자본 500만 원, 동시 1종목, 종목당 10%, 거래당 계획 손실 0.1%, 전략당 60% 상한.
- 기존 세 전략 모두 활성, AI·Slack 활성, 기본 Claude/실패 시 Codex, 생성 연구 비활성.
- 원장 상태 `paused`. 프로세스 시작·주문 활성화는 하지 않았다. Slack 시험 한 건은 별도 승인 후 API 접수 확인.
- 일반 기본 원장과 구형 `kis-paper.ps1 -Action Prepare`를 사용하지 않는다.

계획 손실은 실제 손실 보장이 아니다. 원장의 설정과 주문 제출 권한은 별개다.

## 10일 증거와 남은 조건

| 항목 | 결과 |
|---|---|
| 기존 전체 검증 | 1,476 passed, 2 skipped + smoke 통과; 수정 후 최종 결과는 아래 기록 |
| 의존성 경계 | `tach check` 통과 |
| 첫 연결 점검 | 인증·시세 통과, 잔고 통신 오류; 원인은 확정하지 않음 |
| 호출 간격 제한 후 읽기 점검 | 시세·잔고·한투 거래량 후보 20개 조회 통과 |
| 분봉·호가 | 장외이므로 보류. 11일 장중 재검증 필수 |
| 달력 | 설치된 XKRX 달력에서 11일 09:00–15:30 KST 확인; 당일 공지와 대조 필요 |
| AI | 실제 호출 중 Windows CP949 디코딩 오류 발견, UTF-8 수정·회귀 테스트 통과. 최종 실제 호출 결과는 아래 기록 |
| Slack | 기존 SecondBrain 설정 두 변수만 실행기에서 로드. 10일 23:15 KST 승인된 본인 DM 시험 한 건 API 접수, 사용자 수신 확인 완료 |
| PC | AC·배터리 자동 절전 시간 0 확인. 덮개 동작·전원·재부팅·인터넷은 사용자 확인 필요 |
| 주문·취소·청산 | 미실행·미검증. 조회 성공을 주문 승인으로 취급하지 않음 |

새 PowerShell을 열어 Windows 사용자 환경 변수를 반영한다. 승인된 Slack 전용 로더 외에는 `.env`를 읽지 않는다.
단, 기존 Slack 봇은 Windows 사용자 환경 변수와 별도로 보관되어 있다. Claude Code 조회 결과
`%USERPROFILE%\Documents\SecondBrain-scripts\slack-worker\.env`와
`%USERPROFILE%\.quantpilot-research.sources` 경로를 확인했고, 부모 작업에서도 파일 존재만 확인했다.
`scripts/run-with-env.ps1`가 기존 로더이며 `paper-runtime.ps1`는 이를 호출하지 않는다.
사용자가 기존 로더의 Slack 두 변수 로드와 본인 DM 한 건 전송을 승인했다.
시험 디렉터리의 `paper-with-slack.ps1`가 기존 로더를 호출하고, 별도 `slack-only.sources`로
기본 소스 자동 로드를 막는다. 값 출력·저장소 복사·Windows 전역 등록은 하지 않았다.
키·계좌번호·토큰·보유 내역을 채팅이나 저장소에 기록하지 않는다.

첫 시도는 `conversations.open`의 `missing_scope`로 메시지 POST 전에 거절됐다.
인증 성공·`chat:write` 있음·`im:write` 없음과 대화방 열기 거절을 별도 진단해 최초 기록을
`rejected_before_post`로 해소했다. 기존 봇과 [Slack 공식 문서](https://docs.slack.dev/reference/methods/chat.postMessage/)의
사용자 ID 직접 전송 방식으로 reporter를 수정한 뒤, 승인된 문구
`QuantPilot 모의 시험운영 연결 확인입니다. 주문은 실행하지 않았습니다.` 한 건이 API 접수됐다.
토큰 권한을 확장하지 않았다. 결과는 시험 디렉터리 `slack-check-result.json`에 보존했고,
응답 불명 시 자동 재전송하지 않는 정책은 유지한다. 사용자가 채팅에서 실제 수신을 확인했다.

## 명령 카드

프로젝트 루트에서 실행한다. 아래 함수는 시험 원장 경로를 매번 지정한다.

```powershell
$paperPython = Join-Path $env:USERPROFILE '.quantpilot\runtime-venv\Scripts\python.exe'
$trialRoot = Join-Path $env:USERPROFILE '.quantpilot\intraday-trial-20260911'
function Invoke-Trial([string]$Action) {
    & (Join-Path $trialRoot 'paper-with-slack.ps1') -Action $Action
}
Invoke-Trial Status
& $paperPython -m quantpilot.paper --runtime-dir $trialRoot --json config
# 인증 및 조회 전용. 주문·취소 POST와 실전 호스트는 transport에서 차단한다.
& '.\scripts\kis-paper.ps1' -Action Readiness -Python $paperPython
```

Readiness는 시세·잔고·한투 후보·장중 완성 분봉·15초 이내 호가를 검사한다.
이 점검에서는 공개 데이터 대체 경로를 차단하며, 한투 후보 조회가 실패하면 실패로 보고한다.
`pending_open_session`과 종료 코드 1은 장중 재검증이 필요하다는 뜻이다.
삼성전자 단일 종목의 데이터 점검이며 전체 후보의 데이터 준비를 보증하지 않는다.
`passed`도 계좌의 실험 귀속·미체결 대사 또는 주문 인수 완료를 뜻하지 않는다.
토큰 발급 제한을 피하도록 반복 스케줄링하지 않는다.

| 명령 | 의미 |
|---|---|
| `Invoke-Trial Status` | heartbeat·수집 오류·incident·미체결·보호 대기 확인 |
| `Invoke-Trial Pause` | 신규 진입 중지 요청. 실행 중인 trader가 진입 미체결 취소·보호 매도를 처리 |
| `Invoke-Trial Flatten` | 전량 청산 요청. 실제 체결·대사 완료를 별도로 확인 |
| `Invoke-Trial Report` | 코드 계산 보고 조회. Slack 도착 확인과 별개 |
| `Invoke-Trial Start` | **별도 승인 후** trader·AI worker·reporter 시작. 정지 원장은 running으로 바뀔 수 있으므로 조회용 사용 금지 |
| `Invoke-Trial Resume` | **주문 인수 후** 신규 진입 재개. 시험 원장의 paused 상태는 Start만으로 해제되지 않음 |

Start 전에는 모의용 환경과 `KIS_PAPER_ORDER_SUBMISSION_ENABLED=true`가 명시적으로 필요하다.
실거래·시장가·기존 자동운용 플래그는 false를 유지한다. 준비 작업은 이 권한을 설정하지 않았다.
Pause/Flatten은 원장에 의도를 남긴다. trader가 실행되지 않으면 주문·취소가 처리되지 않는다.

## 11일 순서와 중단 기준

1. **08:30** 전원·세션 공지·환경·계좌 확인. 다른 프로그램의 동시 거래 금지.
   실험 밖 보유분·미체결이 있으면 정리 방법을 먼저 결정하며 원장 수치를 임의 수정하지 않는다.
2. **장초** Readiness 재실행. 완성 분봉·신선한 호가가 통과하기 전 Resume 금지.
   첫 완성 봉이 생기기 전 데이터 부족은 정상 보류다.
3. **별도 승인 후 주문 인수** 종목·최소 수량·지정가를 구체화해 접수/체결 시험과 미체결 취소
   시험을 구분한다. 현재 CLI에는 수동 주문 명령이 없으므로 새 broker 직접 호출로 우회하지 않는다.
   한투 앱에서 수행한 시험은 브로커 동작만 증명하며, 하네스의 주문 경로는 첫 전략 주문으로
   별도 확인한다. 앱 시험을 했다면 보유분·미체결을 해소하고 계좌 대사 후 전략 운용으로 넘어간다.
4. **낮춘 한도로 운용** 첫 주문·첫 AI 평가를 직접 확인하고 매시간 Status 확인.
   신호가 없으면 강제 주문하지 않는다. 장초 전략 시간대를 놓치면 다른 두 전략을 관찰한다.
5. **15:00 신규 진입 종료, 15:10 청산 시작**(당일 달력이 달라지면 상대 시각 적용).
   잔량·취소 미확정·응답 불명은 미완료다. 무작정 프로세스를 종료하지 않는다.
6. **장후** AI 복기·Slack 보고·브로커 잔고와 원장 일치를 확인한다. 잔량과 미체결이 모두 없으면
   Pause를 요청해 다음 거래일 신규 진입을 방지하고, 상태를 재확인한다. 다음날 운용은 별도 결정한다.

- 연결·데이터 실패: 신규 진입 중지, 조회·수집 시험으로 한정.
- 응답 불명·잔고 불일치: Pause 후 조회 복구. 주문 재전송 및 원장 강제 수정 금지.
- AI 실패·만료: 규칙으로 계속하고 AI 항목을 미완료로 기록.
- Slack 실패·미수신: **운영자가 즉시 Pause** 후 Status 확인. 자동 Slack 실패 중지 기능은 없다.
  따라서 Slack 실제 전달과 직접 확인 수단이 마련되지 않으면 AI+Slack 포함 운영 준비 완료로 보지 않는다.
- 청산 잔량: Flatten 의도와 broker 상태를 추적한다. 폐장 후 미청산분은 격리 상태로 인계한다.

## 최종 준비 결과 / 장후 기록

실제 Claude·Codex 개별 평가, Claude 실패를 주입한 Codex 전환, Claude 장후 복기가 모두
가상 입력으로 통과했다. Windows UTF-8 디코딩과 허용 키를 명시하는 평가 스키마를 수정했다.
실제 시장 근거의 AI 평가와 11일 장후 보고 전달은 별도 인수 항목이다.
Slack 직접 전송 수정 후 최종 전용 환경 검증은 **1,489 passed, 2 skipped**,
smoke·`tach check`·`git diff --check` 통과다. 경고 한 건은 기존 Starlette/AnyIO deprecation이다.
그에 앞선 조회·AI 준비 변경의 Anthropic 독립 재검토는 **PASS**이며
변경 가드도 통과했다. [최종 검토 기록](plans/paper-trial-readiness-final-review-result.json)에
별도 실행한 범위 테스트 44개 통과와 판단 근거를 보존했다. 첫 검토 실패 기록은 수정 계보로 보존한다.
낮은 우선순위 후속 항목은 AI 설명(`reasons`) 키의 호스트 측 allowlist 재검사다.
현재 설명 키는 CLI 스키마에서 제한되며 주문·전략 점수 계산에는 사용되지 않는다.

수정 후 실제 Readiness 재실행도 시세·잔고·한투 후보 20개 통과,
분봉·호가는 `pending_open_session`으로 확인했다. 시험 원장은 최종 재조회에서
`paused`, 정책 버전 2, 생성 주문 0건이다. 프로세스·주문 권한은 활성화하지 않았다.
Slack 연결 시험만 별도 승인 후 수행했으며, Slack 포함 실행기의 Status 경로도 검증했다.
기계 판독 준비 기록은 저장소 밖 시험 디렉터리의 `preflight-20260910.json`에 있다.

장후 기록은 저장소 밖 시험 원장 디렉터리에 남긴다. 연결/데이터/주문/취소/대사/청산/AI/Slack의
각 통과 여부, 시작·종료 시각, 다음 시험의 해결 과제를 기록한다. 거래 없음은 체결 미검증으로 표기한다.
