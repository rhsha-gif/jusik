---
name: qp-strategist
description: "전략가팀(매크로 레짐·지정학·시나리오 작성자·독립 반증자·편집자) 실행 런북. 사용자가 \"매크로 전망\", \"시나리오 짜줘\", \"전략가팀 돌려\", \"레짐 판정\", \"지정학 리스크 정리\", \"무효화 조건 뽑아줘\"라고 하면 사용한다. 일간 시황 브리핑(qp-market)이나 종목 후보(qp-invest)에는 쓰지 않는다. 산출물은 원장 research/ 노트이며 거래 입력이 아니다."
---

# qp-strategist

전략가팀(매크로 레짐·지정학·시나리오 작성자·독립 반증자·편집자) 실행 런북. 사용자가 "매크로 전망", "시나리오 짜줘", "전략가팀 돌려", "레짐 판정", "지정학 리스크 정리", "무효화 조건 뽑아줘"라고 하면 사용한다. 일간 시황 브리핑(qp-market)이나 종목 후보(qp-invest)에는 쓰지 않는다. 산출물은 원장 research/ 노트이며 거래 입력이 아니다.

This definition requires anthropic. Before execution, the lead must select capabilityIds: [qp-strategist] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:qp-strategist; mode=bridge; edit .agents/aorch/definitions.json -->
