"""Read-only research agents boundary (market briefs, candidate notes, security gate).

Everything in this package produces material for a human to read: a daily
market brief, candidate notes for the private investment ledger, and a
security verdict for `/ship`. None of it is a trading input. This package
must never import execution, operator, signal, portfolio, risk, broker,
harness or API modules — `tach.toml` and
`quantpilot/tests/unit/test_research_agents_boundary.py` enforce that
statically, and the acceptance matrix (§1 "Research isolation") records it as
a standing invariant. Model output reaching this package cannot create,
approve or submit an order because no order path is importable from here.
"""

from quantpilot.services.research_agents.models import (
    AgentResult,
    EvidenceBundle,
    MarketSnapshot,
    NewsItem,
    SecurityFinding,
    SecurityVerdict,
)

__all__ = [
    "AgentResult",
    "EvidenceBundle",
    "MarketSnapshot",
    "NewsItem",
    "SecurityFinding",
    "SecurityVerdict",
]
