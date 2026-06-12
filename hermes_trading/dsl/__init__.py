"""Strategy DSL: schema-validated condition trees interpreted in pure Python.

The LLM emits data (JSON conditions), never code. The evaluator interprets
those conditions over a fixed-size OHLCV candle buffer. Risk fields are not
part of the mutable schema; they stay enforced in loop.py.
"""

# Candles requested from the price adapter each tick. Warm-up validation in
# dsl.schema is checked against this constant so a strategy that cannot be
# evaluated live is rejected at mutation time, not at runtime.
CANDLE_BUFFER = 200
