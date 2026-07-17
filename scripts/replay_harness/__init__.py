"""Offline replay harness — measures 0rum's execution conventions without
touching the live paper state.

Hard rules (see docs in each module):
  * NOTHING here writes under state/. Snapshots live in backtests/snapshots/,
    run outputs in backtests/runs/<run_id>/, and the runtime replay redirects
    every PaperEngine path into the run directory.
  * The baselines REPRODUCE current conventions (including the imperfect
    ones: fill at signal-candle close, 2xATR14 sizing, SL-first collisions,
    gross legacy R) — improvements are additional policies compared against
    the baseline, never silent replacements of it.
  * No indicator may read a candle that closes after the simulated clock
    (`timeline.SnapshotProvider.as_of` is the only data access path).
"""
