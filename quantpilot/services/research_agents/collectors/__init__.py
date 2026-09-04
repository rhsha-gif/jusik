"""Collectors turn external sources into the evidence bundle.

Every number the agents will see is computed here, in code. Collectors are
fail-closed: a partial snapshot is never returned, because an agent narrating
half a market is worse than no brief at all.
"""
