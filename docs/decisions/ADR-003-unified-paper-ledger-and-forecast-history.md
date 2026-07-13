# ADR-003: Make the unified paper ledger authoritative and archive forecasts

## Status

Accepted for the paper runtime

## Date

2026-07-13

## Context

Two paper systems are active: a legacy worker/local AK producer and the unified
portfolio. The dashboard reads only the latter, while the producer submits to a
retired authorization path. At the same time, the existing AK position has no
enforceable exit levels and forecasts are stored only as a replaceable latest
snapshot.

Reauthorizing the producer would create two independent ledgers and risk paths.
Merging their P&L would hide double exposure and make the account unauditable.

## Decision

The unified `PaperEngine` account, fills and equity journals are the only
authoritative paper portfolio. Legacy trades remain readable as a separately
labelled historical audit and never contribute to unified metrics.

Protective exit metadata is persisted with each position and enforced on closed
monitoring candles. The already-open AK position receives an explicitly marked
ATR fallback bracket because its original structural inputs were not persisted.
Signal-exit strategies keep their validated exit semantics; missing TP/SL
levels are displayed honestly rather than fabricated.

Forecast state is keyed and merged by strategy. An append-only six-hour history
records predictions and later realizations so the dashboard can compare what
was known at the time with what actually happened.

## Consequences

- A rejected legacy candidate is no longer interpreted as a missed unified
  trade.
- A unified open position has an inspectable exit policy, and AK bracket levels
  are executable rather than decorative.
- Forecast cards cannot borrow another strategy's state merely because they
  share `BTC/USDT`.
- Historical forecast comparison begins only when the archive is deployed; the
  prior week cannot be reconstructed without inventing data.
- Operational retirement of the currently running legacy process still needs
  explicit restart/stop approval.
