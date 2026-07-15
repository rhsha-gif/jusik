---
name: local-backtest
description: >
  Run and interpret a QuantPilot local-historical backtest with the confirmed
  KIS cost basis, the proposed acceptance thresholds, and fill-buffer
  sensitivity analysis. Use this skill whenever the user asks to backtest a
  strategy, validate signals against real KRX data, check whether a strategy
  passes acceptance thresholds, measure return/MDD/Sharpe, or says "백테스트
  돌려", "전략 검증", "수익률 확인", "승인 기준 통과하는지" — even if they
  don't say "backtest" explicitly. For auditing an EXISTING backtest result
  for bias, use backtest-forensics instead; this skill is for running new ones
  correctly.
triggers:
  - "백테스트"
  - "backtest"
  - "전략 검증"
  - "수익률 확인"
  - "승인 기준"
---

# Local Backtest

## Why this exists

백테스트 실행 자체는 잡 하나지만, **비용 기준·체결모델 가정·승인 임계값**이
결과를 가른다 (실측: buffer 0bps는 Sharpe 0.144로 탈락, 50bps는 통과).
잘못된 가정으로 돌리면 결과가 승인 판단에 쓰일 수 없다. 상세 배경:
`docs/local_data_backtest_validation_report.md`, `docs/STATUS.md`.

## Preconditions

- `DATA_MODE=local_historical` CSV가 `local_data/`에 있어야 한다. 없거나
  종목·기간이 부족하면 먼저 생성한다:
  ```powershell
  python -m quantpilot.jobs.fetch_krx_local_data --help
  ```
- 실행은 리플레이 기반 no-lookahead다. 신호 로직을 바꿨다면 미래 데이터 접근이
  없는지 확인한다 (`quantpilot/packages/core/backtest/replay.py`).
- 브로커 호출 없음, live 플래그 무관 — 안전 불변식은 건드리지 않는다.

## Confirmed cost basis (사용자 확정, 임의 변경 금지)

한투 실거래 오픈API·일반 개인 기준 (`backtest/costs.py`):
수수료 1.40527bps/편도 (뱅키스 온라인), 매도세 20bps (2026년 KRX), 양도세 없음.
영업점 계좌 시나리오만 `--fee-bps 14.7` 오버라이드. 슬리피지(현 5bps)는
연구용 가정 — 결과 보고에 가정임을 명시한다.

## Run

기본 실행 + 제안 승인 임계값 (2026-07-06 제안, 24개월 기준 — 아직 사람 확정 전):

```powershell
python -m quantpilot.jobs.run_local_backtest `
  --min-total-return 0 --max-drawdown 0.10 --min-simplified-sharpe 0.3 `
  --min-filled-trades 15 --max-turnover 4.0
```

주요 인자: `--limit-buffer-bps`(체결버퍼), `--initial-cash`(기본 1천만),
`--warmup-bars`(기본 20), `--train-size`/`--test-size`(워크포워드 60/20).
전체 목록은 `--help` 또는 `quantpilot/jobs/run_local_backtest.py`.

## Fill-buffer sensitivity (필수)

limit=종가 체결모델은 모멘텀 갭업에서 구조적으로 미체결된다. 단일 buffer
결과만 보고하지 말고 최소 두 번 돌려 비교한다:

```powershell
# --limit-buffer-bps 0  vs  --limit-buffer-bps 50
```

두 결과가 승인 판정을 가르면 (한쪽 통과·한쪽 탈락) 그 사실을 결론 첫머리에
쓴다 — 체결버퍼 가정 확정이 선행 조건이라는 뜻이다.

## Known engine characteristics (해석 시 참고)

- exit 기본은 `marketable_next_open` (리스크 exit 시장가화; trim은 limit 유지).
- 노출도 병목은 건당 비중이 아니라 **신호 동시성** — 평균 노출 ~5-6%가
  관측된 바 있다. 비중 상향은 순수 리스크 스케일링이라 Sharpe가 안 변한다.
- 대형주 소수 종목·단기간이면 체결 표본이 부족하다 (`min_filled_trades` 미달).
  유니버스·기간 확대를 먼저 검토한다.

## Report format

한국어로, 다음을 포함해 보고한다:

1. 실행 조건: 유니버스 크기·기간·bar 수, buffer, 비용 기준, 초기 자본
2. 결과: 체결 건수, 총수익률, MDD, simplified Sharpe, 회전율
3. 승인 임계값 대비 통과/탈락 (임계값이 아직 제안 단계임을 명시)
4. buffer 민감도 비교와 해석
5. 가정·한계 (슬리피지 연구용, 체결모델 민감도 등)

결과가 승인·전략 판단에 쓰일 수준이면 `docs/STATUS.md` 갱신은 status-sync
스킬, 편향 심층 감사는 backtest-forensics 스킬로 이어간다.
