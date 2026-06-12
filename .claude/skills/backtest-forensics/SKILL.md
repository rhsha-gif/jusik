---
name: backtest-forensics
description: >
  Audit a backtest result for common failure modes: look-ahead bias, data snooping,
  overfitting, survivorship bias, unrealistic fill assumptions, and regime sensitivity.
  Outputs a forensics report with severity ratings and remediation steps.
triggers:
  - "audit backtest"
  - "backtest forensics"
  - "check for overfitting"
  - "look-ahead bias"
  - "data snooping"
  - "survivorship bias"
model: claude-fable-5
---

# Backtest Forensics Skill

## Purpose

Systematically identify and rate failure modes in a backtest report or recipe. Prevents overfit, biased, or unrealistic strategies from reaching the Codex implementation stage.

## Safety Constraints

- No broker API calls.
- No executable orders.
- No live trading code.
- No secrets access.
- This skill reads and analyzes — it does not modify backtest code.

## Failure Mode Checklist

### Data Integrity
- [ ] Survivorship bias: is the universe point-in-time?
- [ ] Look-ahead bias: are signals computed strictly from past data?
- [ ] Adjusted vs unadjusted prices: are corporate actions handled correctly?
- [ ] Data vendor consistency: single vendor or mixed sources?

### Statistical Validity
- [ ] In-sample / out-of-sample ratio: is OOS ≥ 30% of total period?
- [ ] Walk-forward windows: ≥ 5 windows recommended?
- [ ] Parameter sensitivity: does performance degrade gracefully near optimal params?
- [ ] Multiple testing correction: has the Sharpe been deflated for number of trials?
- [ ] Sample size: is OOS period ≥ 2 full market cycles?

### Market Microstructure
- [ ] Bid-ask spread modeled?
- [ ] Market impact modeled for position size vs ADV?
- [ ] Slippage assumptions realistic for strategy frequency?
- [ ] Short borrow cost included for short strategies?

### Regime Analysis
- [ ] Tested across bull/bear/sideways/crisis regimes?
- [ ] Correlation with common risk factors (Mkt, SMB, HML, MOM)?
- [ ] Maximum consecutive losing days / months?

## Output Format

```yaml
backtest_forensics:
  recipe_id: <string>
  audit_date: <date>
  overall_confidence: high | medium | low | reject
  findings:
    - category: data_integrity | statistical | microstructure | regime
      finding: <string>
      severity: critical | major | minor | info
      remediation: <string>
  deflated_sharpe: <float>
  recommended_action: approve | revise | reject
  notes: <string>
```

## Quality Gates

- Any `critical` severity finding → overall_confidence = reject.
- Deflated Sharpe must be computed using Bailey-López de Prado deflation formula.
- OOS Sharpe must be ≥ 0.5 before recipe approval.
