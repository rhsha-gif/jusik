# 매매 프로토콜 재검토 — 반응속도 경로의 결정론적 분리

2026-09-15. 전략 손실 진단과 4-에이전트 독립 검토(저장소 밖 `~/.codex/loss-diagnosis-20260915/`)를
근거로 현재 매매 프로토콜을 다시 그리고, 사용자 인터뷰로 결정한 순서에 따라 A 단계를 구현했다.
이 문서는 개정된 프로토콜과 결정 근거, B·C 단계의 설계와 완료 기준을 담는다. 수익성 주장이 아니며
운영 재개 승인도 아니다.

## 1. 진단이 확정한 사실과 그 해석

| 진단 결론 | 프로토콜에서의 의미 |
|---|---|
| 6건 순손실 -23,853원 중 55%는 LG전자 체결 인식 실패 → 주말 이월 | 운영 사고. 반응속도와 무관하며, 체결 인식·보호·마감을 잇는 검증이 먼저다 |
| 신호 봉 마감 → 주문 준비 55~106초 | LLM이 아니라 **단일 직렬 사이클**(대사 → 취소 → 보호 → 진입)과 시계 분 단위 평가 결함의 결과 |
| 손절은 로컬 지정가, 계획 손실 0.1%는 보장 아님 | 보호 매도 허가가 마지막 REST 대사 후 15초 창에 묶여 있음(`broker.py protection_allowed`) |
| legacy 진입에 비용 반영 순목표수익 검사 없음(0.11~0.69R) | 속도를 올려도 손실 진입이 더 빨리 체결될 뿐이다 |
| 손실을 지연 탓으로 확정 불가(체결가 불리 2·유리 3·동일 1) | 지연 개선 효과는 계측으로만 판정한다 |

LLM은 주문 경로에 없다. `ai_enabled`는 별도 worker 프로세스의 점수 조정(legacy 20%·v2 0.8~1.2배)뿐이고,
실패·만료 시 규칙 점수로 돌아간다. 따라서 "AI를 빼서 빠르게 한다"는 방향은 효과가 없다.

## 2. "매크로"의 정의

사용자 가설 "반응속도가 필요한 부분을 매크로로 돌린다"를 다음으로 확정했다.

- **채택**: 코드 안의 결정론적 **보호 lane**. 보유 종목의 손절·목표·마감 청산만 담당하고, 전략 재계산·
  LLM·후보 탐색은 넣지 않는다. 매도는 기존 `place()` → durable kernel → idempotency → kill switch →
  감사 경로를 그대로 통과한다.
- **제외**: HTS/MTS 화면 자동화(감사·주문 상태 머신·대사 우회), 시장가(`MARKET_ORDERS_ENABLED=false`),
  브로커 예약 스탑(KIS OpenAPI 국내주식 미지원, 조건부지정가만 존재).

## 3. 개정된 프로토콜: 세 lane

| lane | 담당 | 입력 | API 우선순위(`api_budget._PRIORITIES`) | 절대 우회하지 않는 게이트 |
|---|---|---|---|---|
| 보호 | 보유 종목 `bid <= stop`, `bid >= target`, 마감 20분 전 청산, flatten, 격리 청산 | A: 사이클마다 REST/스트림 호가. B: 스트림 틱·호가 이벤트 | `sell`/`cancel` 40 | `protection_allowed`, `sell_quantity`, `authorize_order`, kernel `before_send`, kill switch |
| 대사 | 잔고·일별주문 REST, 체결통보 wakeup | 보유 있으면 10초, 없으면 60초. B: 잔고 10초 / 일별주문 30~60초 분리 | `reconcile` 30 | 현금·체결은 REST 증거만 반영. 통보는 힌트 |
| 진입 | 확정 봉 도착 이벤트 평가 → 위험 게이트 → 매수 | collector의 `data:<symbol>.last_bar` | `entry` 20, `minute` 10 | `entry_size` 3중 검사, 신호 나이 상한, 근거 만료 재검사 |

### 허가 창 규칙(인터뷰 결정)

보호 매도 허가는 **잔고 REST 확인 후 15초**를 유지한다. 대사를 잔고 조회만으로 축소해(일별주문 조회 분리)
창이 닫히는 구간을 줄인다. 체결통보 수신·스트림 단절은 즉시 허가를 무효화하고 재대사한다.
현금·체결 반영은 REST 증거만 사용한다. 이는 B 단계 구현 항목이다.

### 위험 비교표

| 안 | 5초 목표 | 위험 | 선택 |
|---|---|---|---|
| 전체 대사 후 15초(현재) | 불가(20초마다 약 5초 닫힘) | 청산 거절 반복(`position_reconciliation_required`) | 현재 |
| 잔고만 대사 후 15초 | 조건부 가능(잔고 조회 p50 5.4초) | 잔고 p95가 길면 창이 다시 닫힘. 계측 필요 | **채택** |
| 통보 힌트 + REST 120초 | 가능 | 통보 스트림 신뢰가 전제. 미확인 체결 종목에 수량 부족 매도 위험 | 보류(스트림 인수 후 재검토) |
| 스트림 없이 60초 | 구현 단순 | 미확인 체결 시 잘못된 수량 매도 시도, 예산 소모 | 기각 |

### 속도 목표(잠정)

- 손절: 조건 최초 관측 → 매도 POST **송신 시작** 5초 이내(스트림 필수). 체결 시각은 지정가라 목표가 아니다.
- 진입: 신호 봉 마감 → 매수 POST 송신 시작 30초 이내.
- 진행 중인 저우선 요청(분봉 2.3초 등)은 선점하지 못하므로 5초 목표의 하한은 API 예산 구조가 정한다.
- 손절 가격은 bid 지정가를 유지하고 미체결 재발주 간격만 단축한다(`exit_reissue_seconds`).

## 4. A 단계 — 구현 완료(이 커밋)

정책 한도·live 플래그·시장가·브로커 모드는 바꾸지 않았다.

| 항목 | 변경 | 검증 |
|---|---|---|
| A1 주문 직전 전체 근거 만료 재검사 | `broker.py before_send`가 durable dispatch의 `submission_evidence_expires_at`(계획·호가·잔고 스냅샷 만료의 최솟값)을 다시 검사. 만료 시 POST 0회, `submission_evidence_expired` 감사 | `test_paper_stabilization_integration.py` `balance_stale`·`quote_stale` 게이트 |
| A2 확정 봉 도착 기준 평가 | 시계 분 `signal_bucket` 제거. 종목별 `evaluated_bar:<symbol>`로 같은 봉은 한 번만, 늦게 온 봉은 그 분 안에 평가. `Signal`에 `bar_end`·`observed_at`·`computed_at`. 봉 마감 후 90초(=기존 150초 봉 시작 기준) 넘긴 신호는 `signal_stale` 감사 후 폐기. 15초 확정 유예는 유지 | `test_paper_collector.py` 늦은 봉 1회 평가·중복 없음·다른 후보 비차단, stale 폐기 |
| A3 체결 인식·보호·마감 통합 시나리오 | 진입 접수 → 취소 POST 미상 → 지연 체결 인식 1회 → 보호 매도 POST 1회 → 마감 격리 → 재시작 후 재전송 0. 코드 결함은 발견되지 않았다 | `test_paper_protocol_scenarios.py` |
| A4 긴급취소 원주문 식별 통일 | `paper_kill.py`가 `kis_paper.is_original_order`를 사용(열 자리 0 인식) | `test_kis_paper_kill_job.py` |
| A5 지연 계측 | `quantpilot/paper/latency.py`: 주문 ID별 `timeline:<id>`(봉 마감·관측·계산·결정·준비·대기 진입·송신 시작·응답·체결 인식; 청산은 조건 최초 관측·호가 관측 포함). 계측 전용 감사 `entry_net_target`·`reentry_after_stop`·`protective_sell_reissued`. CLI `latency [--day]`, `status.latency` 헤드라인, 대시보드 접힌 섹션 | `test_paper_latency.py`, 시나리오·수집기 테스트 |
| A6 보호 매도 재발주 간격 | 정책 `exit_reissue_seconds`(기본 60, 10~60). 매도에만 적용, 매수는 60초 고정 | `test_paper_runtime_controls.py` |

계측 전용 항목은 주문을 거절하지 않는다. `entry_net_target`의 `would_pass_net_target_gate`가 B 단계
순목표수익 게이트의 거절률을 미리 보여 준다. `reentry_after_stop`은 재진입 쿨다운 결정의 표본이다.

### A 단계 운영 절차

1. 시험 원장을 `pause`하고 서비스를 정상 종료한 뒤 새 코드로 재시작한다(정책 스키마는 추가형이며 마이그레이션 없음).
2. 시험 프로필에서만 `config --set '{"exit_reissue_seconds": 15}'`를 명시한다. 새 설치 기본값은 60이다.
3. 며칠간 `python -m quantpilot.paper --runtime-dir <원장> --json latency`로 p50/p95를 모은다.
4. 계측이 모이기 전에는 지연 개선의 효과를 주장하지 않는다.

## 5. B 단계 — 설계(A 인수 후 구현)

1. **스트림 원인 분석** (사용자 보고: 체결통보 복호·HTS ID 문제). `feeds.py` 계좌 ACK의 키/IV 길이 검증,
   `account_notices.py` 복호 경로, `cli.py`의 `KIS_PAPER_HTS_ID` 로딩을 원장 `hybrid_feed` 감사와 logs로 대조한다.
   실제 모의 통보 프레임 1건의 복호 성공이 선행 조건이다.
2. **보호 lane**: 별도 스레드가 `HybridFeed` 틱/호가 이벤트로 임계를 판정하고 기존 `place()`로 매도한다.
   `api_budget`에 `protection` 우선순위(`sell`과 동급)를 둔다. REST 사이클의 보호 검사는 백업으로 유지한다.
3. **대사 분리**: 잔고 조회 10초(보호 창 근거), 일별주문 30~60초 + 통보 wakeup. `protection_allowed`는 잔고 관측
   시각 기준. 스트림 단절 시 현재 15초 REST 창으로 복귀.
4. **legacy 순목표수익 게이트**: `risk.net_target_per_share`를 legacy 진입에도 적용. A5의 거절률 계측으로 영향을 먼저 본다.
5. **완료 기준**: 오프라인 fake 소켓으로 조건 관측 → POST 송신 5초 이내, 중복 매도 0, 단절 시 15초 창 복귀,
   독립 검토 PASS, 실제 모의 통보 프레임 인수. 4거래일 인수는 별도.

## 6. C 단계 — 실측 판정

A5의 `latency` 보고 p50/p95로 손절 5초·진입 30초 달성 여부를 판정한다. 같은 데이터·고정 전략으로 지연만 바꾼
재생 실험(지연 0/관측/악화)은 별도 전략 검증 작업이며 이 문서의 범위가 아니다.

## 7. 검증 기록

컨테이너(Linux, 전용 venv)에서 `python -m pytest quantpilot/tests`를 실행했다. 결과는 STATUS에 기록한다.
`test_paper_intelligence.py`의 codex CLI 3건은 실행기 부재로 이 환경에서 원래 실패하며 변경과 무관하다.
Windows runtime-venv의 `scripts/verify-paper.py`·`tach check`는 사용자 PC에서 한 번 더 확인한다.

독립 검토(`risk-gate-auditor`, 구현자와 다른 에이전트)는 **PASS**(차단 없음)였고, 변경 전 코드 `85f07e3`에서
잔고 만료·늦은 봉·kill 식별·재발주 테스트가 실제로 실패함을 별도 확인했다. should-fix 2건은 반영했다:
계측 기록 실패가 보호 매도를 막지 않도록 `place()`의 계측 호출을 격리(`latency_record_failed` 감사)했고,
청산 후 남던 `exit_condition:<symbol>` 기록을 매도 연결 시점에 정리해 재진입 에피소드가 이전 관측을
물려받지 않게 했다. nit 반영: 도달 불가였던 `balance_evidence_expired` 제거, 송신 경로의 계측 쓰기를
best-effort로 격리, 포그라운드 v2 모드의 분봉 재조회 복원, 문서 한정. 남긴 nit: `exit_reissue_seconds`
하한 10초는 운영 비용(취소 POST·강제 대사 반복)을 문서에 적고 유지한다.
