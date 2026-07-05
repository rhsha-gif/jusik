"""Read-only news/analysis briefing boundary (design doc §4.7).

This package must never import signal, portfolio, or order modules: briefing
content is for human reading only and is not a trading input. A static
import-guard test enforces the boundary.
"""

from quantpilot.services.briefing.service import BriefingCard, daily_briefing

__all__ = ["BriefingCard", "daily_briefing"]
