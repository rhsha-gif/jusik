"""Point-in-time eligibility is separate from a volume ranking."""

from datetime import datetime
from quantpilot.paper.config import aware


def eligible_symbols(snapshot, now, *, fixture=False):
    if fixture:
        return list(snapshot["symbols"]), []
    allowed, issues = [], []
    metadata = snapshot.get("metadata", {})
    eligibility = metadata.get("eligibility", {})
    for symbol in snapshot["symbols"]:
        row = eligibility.get(symbol, {})
        try:
            observed = aware(datetime.fromisoformat(row["available_at"]))
            valid = (
                observed <= now
                and row["market"] in {"KOSPI", "KOSDAQ"}
                and row["instrument"] == "common_stock"
                and row["suspended"] is False
                and row["source_verified"] is True
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if valid:
            allowed.append(symbol)
        else:
            issues.append("point_in_time_eligibility_unverified:" + symbol)
    return allowed, issues
