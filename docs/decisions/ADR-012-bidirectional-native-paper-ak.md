# ADR-012: Bidirectional AK-MACD execution in the native paper portfolio

## Status

Accepted — implemented 2026-08-03.

## Context

The native multi-strategy paper broker persisted a `side` field but priced,
closed and protected every position as a long. `PaperEngine` also ignored
`Side.SHORT`. AK-MACD was the only active native engine already capable of
emitting a confirmed short candidate, and both paper portfolio configurations
disabled that branch with `allow_short: false`.

The separate LLM paper simulator already supports shorts; this decision concerns
only the native portfolio ledger (`paper_positions.json` / `paper_fills.jsonl`).

ADR-011's champion evidence remains the historical AK-MACD H4 **long-only**
baseline. Enabling a paper short is an experiment and does not promote the short
branch to validated champion status.

## Decision

- Enable `allow_short` only for the AK-MACD H4 paper sleeve. UT Bot, HA Trend,
  Donchian and Gold COT keep their existing long/exit signal semantics because
  they do not emit a native short entry.
- Make broker PnL, unrealized PnL and R-multiples direction-aware. Missing side
  in a legacy position defaults to `long`; unknown or mixed persisted sides fail
  closed.
- Require every short to have a complete finite structural bracket at the broker
  boundary: positive take-profit below entry and stop above entry. This keeps
  every broker-created short reloadable under the persisted-state contract.
- Mirror static stop/target checks and metadata migration for shorts, preserving
  stop-first behavior when both levels are touched in one candle.
- Close all opposite-direction tranches and open the new direction on the same
  signal candle. AK-MACD emits crossover pulses, so delaying the entry would lose
  the reversal signal on the next candle. The broker independently prohibits
  simultaneous long and short tranches for one strategy.
- Keep the existing dynamic MFE/SSL exit manager long-only. Short AK positions
  use their frozen structural stop/target and persist no `dynamic_exit` state.
- Make the realistic-fill repricer and operational dashboard side-aware so the
  audit path cannot report long arithmetic for a short trade.
- Publish all fills through the persisted account outbox. The account mutation
  and the complete cycle fill batch are saved before JSONL publication, so a
  crash cannot leave a phantom ledger open/close or duplicate it after restart.

## Alternatives considered

- **Close and wait for the next candle.** Rejected because a crossover pulse is
  not repeated and the reversal would be skipped.
- **Apply the long dynamic exit manager to shorts.** Rejected because its stop,
  target and MFE geometry is directional and has not been mirrored or validated.
- **Treat every sell/exit signal as a short entry.** Rejected. Only engines that
  explicitly emit `Side.SHORT` are eligible.

## Consequences

- Native paper can hold protected AK shorts without enabling any broker/live
  execution path.
- Historical side-less state remains readable, while malformed direction state
  now stops the cycle instead of being silently interpreted as short.
- A reversal can produce two fills on one candle (close, then open), which is
  intentional and auditable by `position_id` and `side`.
- A crash can leave pending fills inside the account state; the next cycle
  publishes them idempotently before evaluating new candles.
- Short AK results must be evaluated separately before any claim that the
  long-only champion evidence extends to shorts.

## Verification

- Broker tests cover short sizing/PnL/MTM, malformed brackets, legacy hydration
  and mixed-side rejection.
- Engine tests cover directional risk distance, short stop-first protection,
  mirrored migration, malformed-signal fail-closed behavior and same-candle
  long-to-short reversal.
- Replay and dashboard contract tests cover direction-aware accounting and
  tranche pairing.
