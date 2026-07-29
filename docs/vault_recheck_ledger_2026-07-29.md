# 재검증 정정 장부 — 2026-07-29

`quantpilot-foundation/00-governance/project-charter.md §5`의 재검증 장부
(`HIST-001` ~ `HIST-011`)에 대한 **정정 레코드**다.

## 왜 이 문서가 필요한가

R0 헌장 §5는 이렇게 적고 있다:

> 현재 작업공간에는 `experiments/`가 없다. 따라서 아래 `original_evidence`는
> `CONCLUSIONS-1.md`가 설명한 증거일 뿐이며, 코드·원자료·시행 원장·출력을 직접
> 검사하거나 실행한 독립 재검증은 전부 미수행이다.

그래서 11개 항목 전부가 `recheck_status: not_performed`이고, 근거란에
*"현재 `experiments/` 없음"*, *"`expected_value.py`와 결과 없음"*, *"원 코드·출력 없음"*
이라고 적혀 있다.

**이 저장소에서는 사실이 아니다.** R0를 만든 세션이 `experiments/`를 보지 못했을 뿐,
실제로는 다음이 모두 존재한다:

| 항목 | 실측 |
|---|---|
| 실험 스크립트 | `experiments/trend_rebalance/` 26개 `.py` |
| 시행 원장 | `experiments/trials.jsonl` — 100행 (append-only) |
| point-in-time 가격 | `data_pit/` 163파일 · 27.9 MB |
| 장기 가격 | `data_long/` 235파일 · 133.6 MB |
| 기존 출력 | `results/` 24파일 |

원자료 CSV는 `experiments/.gitignore`로 git에서 제외돼 있으나 **재생성 가능**하다
(`fetch_pit.py`, `fetch_long.py`). 그래서 checkout에 없다고 해서 증거가 없는 것이 아니다.

## 이 문서가 vault 밖에 있는 이유

`tools/validate_vault.py`가 R0 릴리스 매니페스트 인벤토리와 실제 파일 목록을 대조하며,
등록되지 않은 파일은 `MISSING_MANIFEST_PATH`로 거부한다 (면제는 `60-book-notes/**`,
`90-private-sources/**`, 파일명 `r1` 토큰뿐). vault 안에 이 장부를 넣으려면 R0
릴리스 기록을 고쳐 써야 하는데, 헌장 §7이 금지하는 방향이다 — 새 결정은 과거 기록을
덮어쓰지 않는다. 그래서 **R0 원본은 한 글자도 건드리지 않고** 정정을 여기에 append한다.

## 방법

```powershell
cd experiments
python trend_rebalance/<script>.py
```

각 출력을 `experiments/CONCLUSIONS.md`의 기재값과 대조했다.

- 환경: Windows 11, Python 3.11 (uv cpython-3.11-windows-x86_64), 표준 라이브러리만
- 실행일: 2026-07-29 (Asia/Seoul)
- 8개 스크립트 전부 exit 0
- 브로커 호출 없음, 네트워크 접근 없음 (fetcher는 실행하지 않음)

## 정정 결과

| claim_id | 대상 | 실행 | 문서 기재값 | 재현값 | 판정 |
|---|---|---|---|---|---|
| `HIST-001` | 시행 원장·코드·출력 존재 | 파일 실측 | `experiments/trials.jsonl` | 실재, 100행 | **정정: 증거물 존재** |
| `HIST-002` | 통과한 비교는 두 가지 | `entry_rules` `expected_value` `placebo` | E1·buy_only 두 건 | 두 건 모두 재현 | **정정: reproduced** |
| `HIST-003` | buy_only 연 우위 | `expected_value.py` | +0.19%p, SE 0.06, t=+3.01, 7/7 | **+0.19%p, SE 0.06, t=+3.01, 7/7** | **일치** |
| `HIST-004` | E1 vs 기계적 분할 | `entry_rules.py` | +24.73% | **+24.7%p** (E1 291.6% − E4 266.9%) | **일치** |
| `HIST-005` | 출구 구현 10개 미채택 | — | — | **미실행** | `not_performed` 유지 |
| `HIST-006` | 정보 기반 매도 미시험 | — | — | 변동 없음 | `untested` 유지 |
| `HIST-007` | 25% 순위 철회 | `audit_stats` `era_test` `blend` | 0% t=+3.93 7/7 · 25% t=+1.69 4/7 · 100% t=+0.52 3/7 | **동일** | **일치** |
| `HIST-008` | 내부자 낙폭 매칭 | `insider_cluster.py` | 20d +0.02%p · 60d +0.10%p · 120d +0.04%p | **동일** | **일치** |
| `HIST-009` | GP/A 미검증 | `quality_filter.py` | +0.86%p ± 1.13, t=+0.76 | **+0.86%p, SE 1.13, t=+0.76, 2/3** | **일치** |
| `HIST-010` | 중앙 위험관리 등 미검증 | — | — | 변동 없음 | `unvalidated` 유지 |
| `HIST-011` | 실전 track record 0 | — | — | 변동 없음 | `hold` 유지 |

### 부수 확인

- `blend.py`: 순위 비중 0%만 7/7 시대 양수, 최악 시대 +0.0002x. 25% 이상은 지는 시대 발생.
  `CONCLUSIONS.md §3-3`의 철회 근거가 그대로 재현된다.
- `era_test.py`: 1973-82 승률 **24.0%**, 2000-09 풀80 **38.0%** — 레짐 의존 확인.
- `placebo.py`: rank가 셔플 데이터에서 2016-26 **+0.0755x → −0.2081x**로 소멸,
  buy_only는 **−0.0053x → +0.1543x**로 유지. buy_only가 예측 알파가 아니라 기계적
  리밸런싱 프리미엄이라는 §3-3의 결론이 재현된다.
- `quality_filter.py`: quality vs placebo **+1.00%p** — `§9 1-b`가 기록한
  "시행마다 다시 섞자 +3.17%p가 +1.00%p로 줄었다"와 일치.
- `expected_value.csv`가 **바이트 동일하게** 재생성됐다 (git diff 없음, 개행 표기만 차이).

## 이 재현이 입증하지 않는 것

1. **수치가 옳다는 뜻이 아니다.** 같은 코드가 같은 데이터로 같은 답을 낸다는 것만
   보였다. `CONCLUSIONS.md §9`가 나열한 11개 한계 — 생존편향, 유니버스 후견지명,
   표본 외 검증 없음, 실전 track record 0 — 는 전부 그대로다.
2. **8개만 실행했다.** `HIST-005`가 가리키는 출구 규칙 구현 10종
   (`partial_reduce` `backstop` `rebalance` `rebalance_cash` `exit_slicing`
   `sideways_alpha` `concentration_cap` `analyst_lead_lag`)은 실행하지 않았다.
   해당 claim은 `not_performed`로 남는다.
3. **원자료의 출처를 검증하지 않았다.** `data_pit/` `data_long/`은 기존에 받아둔
   파일이고, fetcher를 다시 돌려 원천과 대조하지는 않았다.
4. **전향 검증이 아니다.** `§5`의 정정 각주대로 buy_only와 E1의 정확한 지위는
   "검증 완료"가 아니라 **"채택 기준 통과, 전향 관찰 중"** 이다.

## 다음 결정

- `HIST-005`의 10종을 마저 재현할지 (스크립트는 전부 있음)
- R0 헌장 §5 표 자체를 R1 방식 overlay로 갱신할지, 이 장부를 참조로 두고 원본은
  역사 기록으로 보존할지 — 현재는 후자

---

## Append 2026-07-30 — HIST-005 재현 완료 (10종)

위 "이 재현이 입증하지 않는 것" §2가 남겨둔 출구·방어 규칙 10종을 전부 실행했다.
전부 exit 0이고, **결과 CSV 13개가 커밋본과 바이트 동일하게 재생성됐다**
(git diff 내용 0줄, 개행 표기만 차이). 콘솔 출력과 `CONCLUSIONS.md` 기재값 대조:

| 스크립트 | 대상 | 문서 기재값 | 재현값 | 판정 |
|---|---|---|---|---|
| `partial_reduce` | EXP-004 | 휩쏘율 83.5%, 왕복 +1.69%, 피한 하락 0.67% | **동일** | 일치 |
| `backstop` | EXP-005/008 | 손절 후 회복률 86~92% | saved 8~14% → 회복 **86~92%** | 일치 |
| `rebalance` | EXP-006 | 평균 −29.5%, 중앙값 +14.2% — 부호 상충 | annual: med +14.22%, mean 289.6−319.1=**−29.5%p** | 일치 |
| `rebalance_cash` | EXP-007 | +0.044x, 129/200, 세금 $0 | 129/200, $0 동일 · **+0.044 = 쌍별 차이의 평균** (CSV에서 직접 계산 0.0440; 콘솔 표는 중앙값 기반 0.053) | 일치* |
| `analyst_lead_lag` | EXP-009 | 하향 전 −3.73% / 후 −0.23% / 120d +1.77%p | **동일** | 일치 |
| `concentration_cap` | EXP-010 | MDD 36.5→36.4%, 세금 $1.8K~8K | MDD 동일, tax $1,823~7,993 | 일치 |
| `sideways_alpha` | EXP-011 | corr −0.008, 적중률 49.4%, 창 4종 동일 | **동일** (20/40/60/120d 전부) | 일치 |
| `exit_slicing` | EXP-012 | 분산 0→15.8% 단조 증가 | spread 0.0→**15.8%** (4×quarterly) | 일치 |
| `rank_deposit` | EXP-013 | 정정 전 t=+24.33 (포트폴리오 단위, §3-3이 무효 선언) | RANKING t=**+24.33** 그대로 재현 — 문서가 정정한 원수치의 출처 확인 | 일치 |
| `pool_size` | EXP-014 | 작동 시작점 풀 40 · 열린자리 0이면 효과 0 | median edge 양수 전환 풀 30→40 (+0.0227→+0.1210) · seed10/book10 전부 marginal/no value | 일치 |

\* EXP-007 주기: `CONCLUSIONS.md §3`의 "+0.044x"는 **200개 포트폴리오 쌍별 차이의
평균**이고, 스크립트 콘솔 표의 "vs base" 열은 중앙값 기반(+0.053x)이다. 같은 CSV에서
둘 다 계산되며 모순이 아니다. 인용 시 어느 통계량인지 명시할 것.

이로써 `HIST-001`~`HIST-011` 중 재현 가능한 실험 전부(EXP-002~018 관련 18개 스크립트)가
이 저장소에서 실행·대조됐다. 남는 미수행 항목은 실험이 아니라 상태 그 자체다 —
HIST-006(정보 기반 매도 미시험), HIST-010(중앙 구조 미검증), HIST-011(실전 기록 0).

한계는 본문 §"이 재현이 입증하지 않는 것" §1·3·4가 그대로 적용된다: 재현은 수치의
옳음이 아니라 코드·데이터·결과의 정합성만 보이며, 생존편향·후견지명·전향 미검증
한계는 변하지 않는다.
