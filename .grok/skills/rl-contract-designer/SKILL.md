---
name: rl-contract-designer
description: "Design a reinforcement learning reward contract for Level 4 Guarded Autopilot recipes: reward function, action space, observation space, safety constraints, and convergence criteria. Outputs a structured RL contract YAML block."
---

# rl-contract-designer

Design a reinforcement learning reward contract for Level 4 Guarded Autopilot recipes: reward function, action space, observation space, safety constraints, and convergence criteria. Outputs a structured RL contract YAML block.

This definition requires anthropic or openai. Before execution, the lead must select capabilityIds: [rl-contract-designer] in an aorch task and route it to a supported provider. Use aorch inventory and dispatch --dry-run to check availability. If that provider is unavailable, return blocked with the needed action. Do not simulate the missing feature, grant approval, or delegate again from a worker. Return any question to the lead as inputRequest.

<!-- aorch-generated: skill:rl-contract-designer; mode=bridge; edit .agents/aorch/definitions.json -->
