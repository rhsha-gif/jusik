"""Per-source daily-bar adapters.

Each module here implements :class:`marketdata.types.DailySource` for exactly
one market-data provider. Adding an asset class means adding a module here and
registering it in :data:`marketdata.fetch.SOURCES` -- the CSV writer and the
chart renderer stay untouched.
"""
