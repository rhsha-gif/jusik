---
name: local-backtest
description: "Run and interpret a QuantPilot local-historical backtest with the confirmed KIS cost basis, the proposed acceptance thresholds, and fill-buffer sensitivity analysis. Use this skill whenever the user asks to backtest a strategy, validate signals against real KRX data, check whether a strategy passes acceptance thresholds, measure return/MDD/Sharpe, or says \"백테스트 돌려\", \"전략 검증\", \"수익률 확인\", \"승인 기준 통과하는지\" — even if they don't say \"backtest\" explicitly. For auditing an EXISTING backtest result for bias, use backtest-forensics instead; this skill is for running new ones correctly."
---

# local-backtest

Run and interpret a QuantPilot local-historical backtest with the confirmed KIS cost basis, the proposed acceptance thresholds, and fill-buffer sensitivity analysis. Use this skill whenever the user asks to backtest a strategy, validate signals against real KRX data, check whether a strategy passes acceptance thresholds, measure return/MDD/Sharpe, or says "백테스트 돌려", "전략 검증", "수익률 확인", "승인 기준 통과하는지" — even if they don't say "backtest" explicitly. For auditing an EXISTING backtest result for bias, use backtest-forensics instead; this skill is for running new ones correctly.

This definition requires anthropic or openai. Before execution, the lead must select capabilityIds: [local-backtest] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:local-backtest; mode=bridge; edit .agents/aorch/definitions.json -->
