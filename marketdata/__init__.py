"""Standalone market-data fetchers and a self-contained chart renderer.

Research and inspection only: nothing here reads credentials, touches a broker,
or places orders. Each source adapter downloads public historical bars and
writes them to CSV; :mod:`marketdata.chart` turns those bars into a single
self-contained HTML file that references no external resources.

This package is deliberately independent of ``quantpilot/`` and uses only the
Python standard library.
"""
