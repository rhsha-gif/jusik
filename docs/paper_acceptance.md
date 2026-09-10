# 모의투자 하네스 개편 인수 기록

기준일: 2026-09-10. 실제 모의운용은 활성화하지 않았다.

## 구현 경로

`quantpilot/paper`에 CLI, 실험 원장, 초기 세 전략, 배분·주문·위험 관리, 데이터 수집, AI 평가·전략 연구, 보고 경로를 연결했다. 기존 durable 주문 저널·체결 대사·주문 상태 머신을 재사용한다. 구형 Level 승격 절차는 새 CLI 진입 경로에서 호출하지 않는다.

500만 원 실험 자금, 전략 60%·종목 25%·동시 4종목·거래당 계획 손실 0.5% 상한을 코드에서 강제한다. 계좌별 프로세스 잠금과 별도 현금 예약 검사를 둔다. 미실현 가상 성과를 운용 현금에 더하지 않는다. pause는 flatten 의도를 취소하지 않으며, 새 진입을 멈춰도 검증된 기존 포지션은 보호한다.

모의 분봉은 별도 수집 스레드가 축적한다. AI worker와 Slack reporter도 주문 루프와 분리했다. 설정·주문·체결·작업·보고 상태는 저장소 밖 SQLite에 보존한다. 생성 코드는 Docker 외부에서 실행하지 않으며, 실패하면 후보를 보류한다.

## 자동 검증

| 검사 | 결과 |
|---|---|
| 전용 환경 `python scripts/verify-paper.py` | **1,476 passed, 2 skipped** |
| `python -m quantpilot.jobs.run_smoke` | 통과, mock 체결 3건, 실거래 false |
| `tach check` | 통과 |
| `git diff --check` | 통과 |
| Black 검사 | 새 운용 모듈·검증 스크립트 22개 파일 통과 |
| PowerShell 실행기의 Status 경로 | 정상 JSON 출력, fixture/stopped |
| 거래일 달력 설치·조회 | XKRX 2026-09-10 09:00–15:30 일정 조회 확인 |

전용 환경은 `%USERPROFILE%\.quantpilot\runtime-venv`다. FastAPI 0.133.1·Starlette 1.0.1 조합으로 기존 API 경로 검사와의 호환성을 유지했다. 기존 시스템 Python 환경은 변경하지 않았다. 검증 시 자격정보 환경 변수를 제거했다. 경고 한 건은 Starlette의 AnyIO 별칭 deprecation이며 실패가 아니다.

## 주요 실패 경로의 증거

| 검증 대상 | 테스트 |
|---|---|
| 완성 봉·미래 시점·누락·중복, 실제 세 신호, 종목 충돌·배분 상한 | `test_intraday_strategies.py` |
| 누적 부분체결 재생, 중복 주문, 자금 예약·원장, 설정·pause 지속 | `test_paper_experiment_ledger.py` |
| 보호 매도·현재 위험 한도·오래된 호가·기본 비활성 | `test_paper_runtime_controls.py` |
| 기존 주문 커널과 새 원장 연결 | `test_intraday_durable_gateway.py` |
| 실제 KIS 클라이언트의 제한된 주문 전송 경로, 가짜 HTTP로 POST 1회 확인 | `test_paper_production_wiring.py` |
| 고아 세션 복구·begin 실패 정리·취소 체결 경합·flatten 지속·별도 보유분 중 보호·시험 전략 보고 | `test_paper_review_regressions.py` |
| 실제 수집 스레드와 주문 루프의 독립 실행 | `test_paper_collector.py` |
| AI 제공자 전환·잘못된 평가·유효기간·도구 제한·CLI 모델 메타데이터 | `test_paper_intelligence.py` |
| 코드 해시·독립 검토·편입 기준·후보 수·Docker 제한 | `test_paper_strategy_lab.py` |
| 연구 비활성·격리 실패·원장 분리·편입 연결·평가 저장 복원 | `test_paper_research_integration.py` |
| 호스트의 출력·실행시간 제한 | `test_paper_process_bounds.py` |
| 복기 종료 후 보고·슬랙 중복 방지·중단 복구 | `test_paper_reporting_worker.py` |

기존 core의 부분체결·응답 유실·재시작·취소·예약 테스트도 전체 검증에 포함된다.

## 구독 CLI 점검

브로커·계좌 정보가 없는 가상 입력으로 양쪽 실제 구독 CLI를 호출했다. Codex와 Claude 모두 제한된 도구 설정에서 `{"status":"ok"}` JSON 응답을 반환했다. Claude의 빈 MCP 설정 형식과 Windows 임시 디렉터리 정리 경합을 수정했다. 실제 투자 평가나 주문을 실행한 점검은 아니다.

## 독립 검토

OpenAI 구현과 별도로 Anthropic Opus가 검토했고 최종 안전 검토는 **PASS**다. 세션 복구, 취소 경합, flatten 중단, 실제 KIS 클라이언트 연결, 별도 보유분 중 보호, 시험 전략 보고와 종목별 대사 문제를 수정하고 재현 테스트를 추가했다. 최종 결과는 `docs/plans/paper-protection-acceptance-result.json`에 보존했다. 검토자는 실제 gateway의 미검증 종목 매도·대사 실패 중 매수·매도 가능 수량 부족 차단을 fake client로 별도 확인했고, 전체 1,474개 테스트도 독립 실행했다.

검토 사본 이후 변경은 CLI 모델 메타데이터 기록과 테스트 2개, 보호 대기 종목의 보고·알림 표시와 재현 검증이다. 주문 권한은 변경하지 않았다. 낮은 중요도의 잔여 관찰 사항은 직접 제출 차단의 자동 회귀 검증 보강, 미사용 import, 격리 출력 초과의 세부 오류 분류, 일부 진단 분기의 테스트 보강이다. 현재 차단 동작 자체는 검토를 통과했으며 세부 근거는 검토 결과에 남겼다.

## 남은 수동 인수·승인 범위

- 실제 모의 API의 분봉 시간 표기·후보 탐색 지원, 지정가 주문·체결·취소·대사 확인.
- Docker 엔진과 digest 고정 이미지의 실제 격리 실행 확인. 현재 검증되지 않아 생성 코드 운용은 기본 비활성이다.
- 본인 Slack DM 실제 전달 확인. 실제 메시지는 아직 보내지 않았다.
- 웹 파일 59개는 2026-09-10 사용자 승인 후 삭제 완료. 목록 밖 구형 기능과 과거 기록은 보존했다.

운영 절차와 명령은 `paper_intraday_runbook.md`, 삭제 후보·의존 관계는 `paper_retirement_inventory.md`를 따른다. 커밋·푸시·PR은 만들지 않았다.
