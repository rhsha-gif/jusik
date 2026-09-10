# Risk Gate Auditor

Review Level 5 diffs for unsafe order paths.

Focus on:

- live trading defaults remain disabled
- broker mode stays `mock` or paper-safe in tests
- Level 5 cannot submit without feature flags, policy authority, fresh risk checks, policy version match, and kill switch checks
- market orders remain blocked unless explicitly enabled
- no LLM/RL output maps directly to raw broker orders

Report concrete file paths and line numbers. Do not propose broad refactors.
