"""The Lambda entry points: thin wrappers over the router and the projector.

The deployed product replays recordings only. Nothing imported from here loads
Strands, a model client or the scenario's live agent: tests/test_aws_entry.py
imports the api entry with `strands` blocked and serves GET /runs.
"""
