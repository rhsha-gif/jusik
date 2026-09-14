---
name: qp-designer
description: "설계자팀(시장구조 분석가·전략 설계자·백테스트 포렌식·리스크 게이트) 실행 런북. 사용자가 \"전략 설계해줘\", \"이 가설 백테스트\", \"설계자팀 돌려\", \"레시피 만들어\", \"시장을 시스템으로 보면\", \"퀀트 전략 검증\"이라고 하면 사용한다. 기존 레시피의 백테스트만 다시 돌릴 때는 local-backtest, 기존 결과의 편향 감사만 할 때는 backtest-forensics 스킬을 쓴다. 산출물은 draft 레시피와 연구 노트이며 승격·거래 입력이 아니다."
---

# qp-designer

설계자팀(시장구조 분석가·전략 설계자·백테스트 포렌식·리스크 게이트) 실행 런북. 사용자가 "전략 설계해줘", "이 가설 백테스트", "설계자팀 돌려", "레시피 만들어", "시장을 시스템으로 보면", "퀀트 전략 검증"이라고 하면 사용한다. 기존 레시피의 백테스트만 다시 돌릴 때는 local-backtest, 기존 결과의 편향 감사만 할 때는 backtest-forensics 스킬을 쓴다. 산출물은 draft 레시피와 연구 노트이며 승격·거래 입력이 아니다.

This definition requires anthropic. Before execution, the lead must select capabilityIds: [qp-designer] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:qp-designer; mode=bridge; edit .agents/aorch/definitions.json -->
