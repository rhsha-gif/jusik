"""Strategy design service: code-computed market-structure evidence for research agents.

Research-only. Nothing in this package is a trading input. The strategist and
designer agents read the JSON produced here and quote its numbers verbatim;
every statistic (breadth, realized volatility, correlation, sector ranks, flow
z-scores, per-symbol structure) is computed by code in
`quantpilot.services.strategy_design.analytics` from bars at or before an
explicit `as_of` date, never by a model.

Boundary rules (enforced by `tach.toml` and
`quantpilot/tests/unit/test_research_agents_boundary.py`):

- May depend only on the shared schemas, backtest, data, strategies and
  technical packages plus `quantpilot.services.research_agents` (for the
  `SectorMove` / `InvestorFlows` shapes).
- Must never import execution, operator, signal, portfolio, risk, broker,
  harness, API or paper-submission modules — no order path is importable from
  here, so model output reaching this package cannot create, approve or submit
  an order.
- `MarketStructureEvidence.signal_input` is the constant `False` by type; it
  cannot be flipped, and nothing downstream may treat this evidence as a signal.
- Pure standard library plus pydantic; no numpy or pandas.
"""

from quantpilot.services.strategy_design.models import (
    BreadthPoint,
    CorrelationSummary,
    FlowZScore,
    MarketStructureEvidence,
    SectorMomentum,
    SymbolStructure,
    VolatilityRegime,
)

__all__ = [
    "BreadthPoint",
    "CorrelationSummary",
    "FlowZScore",
    "MarketStructureEvidence",
    "SectorMomentum",
    "SymbolStructure",
    "VolatilityRegime",
]
