"""Unified paper portfolio: one shared account across every strategy.

`paper_broker` is pure accounting logic (no I/O, no network) — the single place
fills, positions, cash and equity are computed. `paper_engine` orchestrates the
strategies and persists the ledger. Nothing here ever trades live.
"""
