# Step 02 Policy Compiler Semantic Layer Report

## Implemented

- Added `SemanticPolicyCompiler` for `PolicyAST -> PolicyCompilationResult`.
- Added typed semantic DTOs:
  - `SemanticPolicy`
  - `UniverseConstraints`
  - `ForbiddenConstraints`
  - `RiskBudget`
  - `PolicyCompilationResult`
- Added compatibility wrappers:
  - `compile_policy_ast_semantics(policy_ast)`
  - `compile_semantic_policy_text(text, user_id=...)`
- Preserved `compile_policy_text(text)` as the existing `PolicyAST` API.
- Added semantic handling for accumulate, cash raise, trim sector, horizon,
  confidence, ambiguity, unsupported intents, and fail-closed orderability.
- Kept market orders disabled in semantic outputs even when the user requests
  market orders.

## Safety Invariants

- Live trading enabled: no.
- Broker mode used for validation: `mock`.
- Order submission from semantic compilation: disabled.
- Market orders enabled: no.
- Short or inverse execution enabled: no.
- Credential UI or broker credential handling added: no.
- Real broker order submission path added: no.

## Validation

```powershell
python -m pytest quantpilot/tests/unit/test_policy_semantic_compiler.py quantpilot/tests/unit/test_policy_semantic_fail_closed.py quantpilot/tests/unit/test_policy_compiler_foundation.py -q -p no:cacheprovider --basetemp=.pytest_tmp
```

Result: 19 passed.

```powershell
python -m pytest quantpilot/tests -p no:cacheprovider --basetemp=.pytest_tmp
```

Result: 260 passed, 1 skipped.

```powershell
python -m quantpilot.jobs.run_smoke
```

Result: completed with `broker=mock`, `live_trading_enabled=false`, and Level 5
operator fallback `level5_flag_disabled`.

## Known Limitations

- The semantic compiler is an opt-in layer and is not yet wired into every API
  response.
- Universe ranking, signal generation, optimizer behavior, and live trading are
  intentionally out of scope.

## Next Step

Wire semantic policy outputs into downstream intent or policy preview surfaces
only where callers need typed constraints, keeping existing response fields
backward compatible.
