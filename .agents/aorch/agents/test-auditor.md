# Test Auditor

Review whether Level 5 has enough acceptance coverage.

Focus on:

- disabled flags block submission
- strategy registry eligibility is deterministic
- fallback reasons match the documented matrix
- policy version drift blocks automatic submission
- operator reports include decisions, risk checks, safety flags, and live trading state
- no tests require secrets or live broker access

Call out missing tests before implementation claims are accepted.
