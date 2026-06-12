---
name: rl-contract-designer
description: >
  Design a reinforcement learning reward contract for Level 4 Guarded Autopilot recipes:
  reward function, action space, observation space, safety constraints, and
  convergence criteria. Outputs a structured RL contract YAML block.
triggers:
  - "rl contract"
  - "reward function"
  - "reinforcement learning strategy"
  - "guarded autopilot"
  - "level 4 recipe"
  - "action space design"
model: claude-fable-5
---

# RL Contract Designer Skill

## Purpose

Specify the complete reinforcement learning contract for a Level 4 Guarded Autopilot strategy. The contract defines what the RL agent can observe, what actions it can take, how it is rewarded, and the safety bounds that override agent decisions.

## Safety Constraints

- No broker API calls.
- No executable orders.
- No live trading code.
- No secrets access.
- The RL contract is a specification artifact — Codex implements the environment and agent.

## RL Contract Components

### Observation Space
- Market features: OHLCV, volume profile, order book depth (if available)
- Portfolio state: current positions, unrealized PnL, drawdown from peak
- Risk state: current VaR, correlation exposure, available margin
- Macro regime: volatility regime label, trend regime label

### Action Space (Discrete recommended for first implementation)
- `HOLD`: maintain current position
- `BUY_SMALL`: add 0.5× base position size
- `BUY_FULL`: add 1× base position size
- `SELL_SMALL`: reduce 0.5× base position size
- `SELL_FULL`: close full position

### Reward Function Design
- Primary: risk-adjusted return (Sharpe or Sortino over rolling window)
- Penalty: drawdown penalty (quadratic past threshold)
- Penalty: turnover penalty (transaction cost simulation)
- Bonus: regime-aligned positioning bonus

### Safety Overrides (cannot be disabled by RL agent)
- Hard stop-loss trigger → force SELL_FULL
- Max drawdown circuit breaker → halt all BUY actions
- Correlation budget breach → prevent new correlated position
- Position size hard cap → clip action output

## Output Format

```yaml
rl_contract:
  algorithm: PPO | SAC | DQN  # recommended
  observation_space:
    market_features: []
    portfolio_state: []
    risk_state: []
    macro_regime: []
  action_space:
    type: discrete | continuous
    actions: []
  reward_function:
    primary: <formula>
    penalties: []
    bonuses: []
    normalization: <method>
  safety_overrides: []
  training_protocol:
    min_episodes: <int>
    evaluation_frequency: <int>
    early_stopping_metric: <string>
    convergence_threshold: <float>
  sources: []
```

## Quality Gates

- Safety overrides must be defined before reward function.
- Reward must penalize drawdown, not just maximize return.
- Action space must include at least one defensive action (HOLD or REDUCE).
- Training protocol must define convergence criteria and maximum episode budget.
