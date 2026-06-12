---
name: quant-source-synthesis
description: >
  Gather, rank, and summarize academic and practitioner sources relevant to a
  quantitative strategy hypothesis. Outputs a ranked source list with relevance
  notes and key claims for use in recipe authoring.
triggers:
  - "find sources"
  - "research this strategy"
  - "what does the literature say"
  - "cite papers for"
model: claude-fable-5
---

# Quant Source Synthesis Skill

## Purpose

Identify and rank research sources that support or challenge a given strategy hypothesis. Ensures every QuantPilot recipe has rigorous academic and practitioner backing.

## Safety Constraints

- No broker API calls.
- No executable orders.
- No live trading code.
- No secrets access.
- Web searches are read-only research only.

## Minimum Source Requirements

Every synthesis must produce:
- ≥ 1 peer-reviewed paper (Journal of Finance, RFS, JFE, Journal of Portfolio Management, etc.)
- ≥ 1 SSRN working paper or dissertation
- ≥ 1 practitioner source (AQR, Two Sigma, Citadel, Man Group, Verdad, Alpha Architect, etc.)

## Process

1. Decompose hypothesis into testable factor claims.
2. Search academic databases and SSRN for each claim.
3. Search practitioner research blogs and whitepapers.
4. For each source: extract key empirical finding, sample period, asset class, and limitations.
5. Rank sources by: recency × replication quality × sample breadth.
6. Flag contradictory evidence explicitly.

## Output Format

```yaml
sources:
  - id: <short-slug>
    type: academic | practitioner | working-paper
    title: <string>
    authors: []
    year: <int>
    url_or_doi: <string>
    key_claim: <string>
    sample_period: <string>
    asset_class: <string>
    limitations: <string>
    relevance_to_hypothesis: high | medium | low
```

## Quality Gates

- Must flag if no peer-reviewed replication exists.
- Must include at least one source that challenges the hypothesis.
- Recency: prefer sources within the past 10 years unless foundational.
