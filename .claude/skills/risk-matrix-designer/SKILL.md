---
name: risk-matrix-designer
description: >
  Design a quantitative risk matrix for a trading strategy: position sizing,
  drawdown limits, correlation budgets, stop-loss levels, and Kelly-based
  allocation formulas. Outputs a structured YAML risk block.
triggers:
  - "design risk matrix"
  - "position sizing"
  - "drawdown limit"
  - "risk parameters"
  - "kelly criterion"
model: claude-fable-5
---

# Risk Matrix Designer Skill

## Purpose

Produce a complete, internally consistent risk matrix for inclusion in a QuantPilot recipe. All parameters must be derivable from historical return statistics or published risk management frameworks.

## Safety Constraints

- No broker API calls.
- No executable orders.
- No live trading code.
- No secrets access.
- All formulas are design artifacts — Codex implements the computation.

## Inputs Required

1. Strategy return distribution assumptions (Sharpe, volatility, skew)
2. Target portfolio role (core / satellite / tactical overlay)
3. Investor risk tolerance tier (conservative / moderate / aggressive)
4. Asset class (equities / futures / crypto / FX / multi-asset)

## Risk Parameters to Design

| Parameter | Formula Basis | Default (moderate) |
|---|---|---|
| `max_position_pct` | Kelly fraction × 0.25 (fractional Kelly) | 5% |
| `max_sector_pct` | Correlation budget analysis | 20% |
| `max_portfolio_drawdown_pct` | Historical worst drawdown × 1.5 safety factor | 15% |
| `stop_loss_pct` | 2× daily ATR or 1.5× rolling volatility | 3% |
| `correlation_budget` | Max pairwise ρ between active positions | 0.4 |
| `leverage_max` | Regulatory + volatility-scaled cap | 1.5× |
| `var_limit_95_daily` | Portfolio VaR at 95% confidence | 2% |

## Output Format

```yaml
risk_matrix:
  sizing_formula: fractional_kelly | fixed_fraction | vol_targeting
  max_position_pct: <float>
  max_sector_pct: <float>
  max_portfolio_drawdown_pct: <float>
  stop_loss_pct: <float>
  correlation_budget: <float>
  leverage_max: <float>
  var_limit_95_daily: <float>
  rebalance_frequency: daily | weekly | monthly
  circuit_breakers:
    - trigger: portfolio_drawdown_exceeds_10pct
      action: halt_new_entries
    - trigger: position_loss_exceeds_stop_loss
      action: close_position
  sources: []
```

## Quality Gates

- Fractional Kelly must use ≤ 25% of full Kelly to avoid ruin risk.
- `max_portfolio_drawdown_pct` must be ≥ 2× `max_position_pct`.
- At least two circuit breakers must be defined.
- All sizing formulas must cite a source.
