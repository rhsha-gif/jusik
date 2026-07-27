# 아카이브 — 왜 여기 있는가

> 정리일 2026-07-26 · **삭제하지 않았다.** 전부 git 이력에 있고 언제든 되돌릴 수 있다.
> 현행 기준은 `docs/`에, 확정된 결정과 실증 근거는 `experiments/CONCLUSIONS.md`에 있다.

이 문서들이 나쁘거나 틀렸다는 뜻이 아니다. **당시에는 맞았고 지금은 전제가 바뀌었다.**
현행 문서와 섞여 있으면 나중에 잘못된 근거로 쓰이기 때문에 분리했다.

---

## krx_kis/ (19) — 시장 전환으로 무효화 예정

**바뀐 전제: KRX·KIS → 미국주식·IBKR (2026-07-26 확정)**

schema v10 원자적 예약, schema v11 이벤트 저널, KIS paper kill, Execution Kernel v2 계약,
KRX 로컬 백테스트 등. 설계 자체는 여전히 잘 만들어졌고 **집행 계층의 원리는 재사용 가치가 있다.**
다만 KIS API·KRX 캘린더·원화·매도세 20bps에 묶여 있어 그대로는 쓸 수 없다.

> 참고: 미국주식은 매도세가 없고 **양도세 22%(연 250만 공제, FIFO)** 가 대신 붙는다.
> 세금 구조가 반대이므로 비용 관련 결론은 전부 다시 계산해야 한다.

## level5/ (5) — 목표 자율도 하향으로 무효

**바뀐 전제: 최종 자율도 = Level 4 (전략만 승인)**

사용자가 인터뷰에서 명시적으로 Level 5를 배제했다 — *"판단하는 자리를 내주면 시스템의 존재 이유가 사라진다."*
Level 5 완전 자동 오퍼레이터 설계는 목표에서 제외됐다.

## completed_missions/ (25) — 완료된 작업의 역사 기록

professional operator workboard, roadmap 시리즈, step 04~08 리포트, stage 리포트,
pre-harness/hardening, level 1-2/3-4 리포트, 환경 검증, 워크플로 문서 등.

**정크가 아니라 증거다.** 다만 전부 완료됐고 새 결정의 근거가 되지 않는다.

## learning/ (3) — 목적과 무관

초보 개발자 커리큘럼과 학습 갭 분석. 시스템 설계·운용과 직접 관련이 없다.

## claude_setup/ (10) — 도구 셋업 가이드

Claude Code 플러그인·MCP·설치 가이드. 저장소의 투자 시스템과 무관한 개발 환경 문서.

---

## 함께 정리한 것

```
CLAUDE.md.bak, CLAUDE.md.20260705.bak      삭제 (git에 원본 있음)
quantpilot/packages/core/{validation, realtime, level12,
                          ledger, learning, costs}/       삭제
    → 소스 파일 0개, stale .pyc만 남아 있던 유령 디렉터리.
      해당 모듈은 커밋된 적이 없거나 다른 경로로 이동했다.
```

---

## 되돌리는 법

```powershell
git log --oneline -- docs/_archive        # 이동 이력 확인
git mv docs/_archive/<분류>/<파일> docs/  # 개별 복원
git checkout HEAD~1 -- docs               # 정리 이전 상태로 통째 복원
```

## 현행으로 남긴 15개

```
STATUS.md                              현황판 (미장 전환 미반영 — 갱신 필요)
agent_collaboration_protocol.md        Codex-Claude 협업 프로토콜
agent_capability_scorecard.md          적합도 라우팅
agent_workboard_template.md            미션 작업보드 템플릿
safety_checklist.md                    안전 점검
live_trading_enablement_checklist.md   라이브 12항목 (0/12)
historical_data_quality.md             데이터 품질 기준
operator_runbook.md                    운영 절차
operator_fallback_matrix.md            폴백 (Level 5 부분은 무효)
operator_strategy_promotion_policy.md  승격 정책
strategy_authoring_and_promotion.md    전략 작성·승격
roadmap_acceptance_matrix.md           승인 기준 (실질 기준으로 재작성 필요)
product_vision_alignment_design.md     제품 구상 ↔ 아키텍처
contracts/operator_contracts.md        오퍼레이터 계약
contracts/professional_operator_contracts.md
```
