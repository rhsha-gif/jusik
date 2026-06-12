# /review-quant-recipe

Audit an existing QuantPilot recipe for quality, bias, and risk adequacy.

## Usage

```
/review-quant-recipe [recipe-id or path]
```

## What This Command Does

Invokes three specialist agents in sequence to produce a complete recipe review:

1. **Backtest forensics** — `backtest-forensics-agent` checks for look-ahead bias, survivorship bias, overfitting, unrealistic microstructure assumptions, and regime sensitivity. Computes deflated Sharpe.

2. **Risk gatekeeper** — `risk-gatekeeper-agent` validates risk matrix internal consistency, sizing formula adequacy, circuit breaker completeness, and leverage limits.

3. **Source review** — `source-curator-agent` verifies that all signal claims have adequate empirical backing and flags any uncited parameters.

## Invocation Instructions

When this command is invoked:

1. If no recipe ID or path is provided, ask: "Which recipe should I review? (provide ID or path)"
2. Read the recipe from `docs/quant_recipes/<recipe-id>.yaml`
3. Invoke `backtest-forensics-agent` first — if it returns `reject`, report to user and stop.
4. Invoke `risk-gatekeeper-agent` — if it returns `rejected`, report to user and stop.
5. Invoke `source-curator-agent` to validate citations.
6. Produce overall review summary with: approve / revise / reject verdict, list of all findings, and remediation steps.
7. Write review report to `docs/quant_recipes/<recipe-id>-review.yaml`.

## Safety

- No broker API calls
- No executable orders
- No live trading code
- No secrets access

## Output

File: `docs/quant_recipes/<recipe-id>-review.yaml`
Verdict: approve | revise | reject
