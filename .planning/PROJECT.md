# 0rum

## What This Is

0rum (prononcé « orum », from *aurum* — gold in Latin) is a 24/7 autonomous trading bot for XAUUSD (Gold/USD) built on the OANDA v20 API. It runs 4 parallel technical strategies, filters and ranks signals through a pipeline, validates parameters via walk-forward optimization, and executes either as a Telegram signal sender (mode signal) or a fully automated order executor (mode auto). The system is entirely code-generated — the author is a non-developer.

## Core Value

The bot must reliably generate validated XAUUSD signals in mode signal, with every trade candidate passing all risk gates — signal quality and capital protection are non-negotiable before any auto-execution is considered.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Docker Compose stack (postgres, redis, app) starts cleanly and `/health` responds
- [ ] OANDA candle ingestion for M15, H1, H4, D1 with auto-backfill and gap detection
- [ ] 4 strategies (Liquidity Sweep, Trend Continuation, Breakout Expansion, EMA Momentum) generate CandidateSignals
- [ ] Signal pipeline (dedup, conflict filter, ranker, quota) produces ApprovedSignals
- [ ] Walk-forward optimizer (LHS × 100, WFE > 50%) activates best params per strategy every 24h
- [ ] Monte Carlo validation (1000 sims, P95 drawdown ≤ 2× historical, P5 profit factor > 1.0)
- [ ] Market regime detection (TRENDING_UP/DOWN, RANGING, HIGH_VOL) feeds signal ranking
- [ ] Risk gates: daily loss limit (−3%), max positions (5), concentration check
- [ ] ATR-based position sizing with volatility adjustment and 2% hard cap
- [ ] Circuit breaker: 8 consecutive stops → 24h shutdown with Telegram alert
- [ ] Mode signal: Telegram formatted signal + theoretical trade tracking in DB
- [ ] Mode auto: OANDA order placement, partial close at TP1, ATR trailing stop
- [ ] Telegram notifications for all event types (signal, TP, SL, circuit breaker, daily summary)
- [ ] Structured JSON logging via structlog throughout

### Out of Scope

- ML/neural networks (LSTM, sklearn, etc.) — pure technical strategies only, no AI in signal generation
- Multi-asset — XAUUSD exclusively, no instrument loop
- Frontend/dashboard — `/health` endpoint is sufficient for v1
- External data APIs (Fear & Greed, news) — no external dependencies beyond OANDA
- RSI/MACD — explicitly eliminated to reduce parameter count
- More than 3 optimizable parameters per strategy — overfitting protection

## Context

- **Author is non-developer**: all code produced by Claude Code from this spec
- **OANDA v20 quirks**: instrument is `XAU_USD` (not `XAUUSD`), granularity `D` (not `D1`), RFC3339 datetime format, 120 req/s rate limit
- **Optimization discipline**: 3 params max per strategy, WFE > 50% required, 6-month train / 2-month OOS windows, multi-window test (2/3 profitable)
- **Risk is fixed**: all risk parameters (`RISK_PER_TRADE`, `DAILY_LOSS_LIMIT`, etc.) are env vars, never optimized
- **Confidence scoring**: must never use OOS data or current optimizer results (circular logic risk)
- **Transition to auto**: minimum 4 weeks signal mode, win rate > 55% theoretical, PF > 1.3, WFE avg > 50%
- **Deployment target**: VPS or Mac Studio M3 Ultra local via Docker Compose

## Constraints

- **Tech Stack**: Python 3.12, FastAPI, PostgreSQL 16, Redis, Docker Compose, SQLAlchemy 2.0 async, Pydantic v2, httpx, APScheduler, structlog, scipy/numpy/pandas, python-telegram-bot — no deviations
- **Code Style**: async everywhere, type hints everywhere, Google-style docstrings, structured logging, no bare `except:`
- **Optimization**: max 3 params/strategy, WFE > 50% gate, no structural params (EMA50/200, swing order=10) in optimizer
- **Capital Safety**: risk gates are not optional — no trade executes without passing all 3 gates

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Signal mode before auto | Validate strategy quality with 4 weeks of real market signals before risking capital autonomously | — Pending |
| 4 pure technical strategies, no ML | Avoid overfitting, keep interpretability, reduce parameter explosion | — Pending |
| OANDA practice → live | Safe progression path with identical API | — Pending |
| LHS over grid search | Latin Hypercube Sampling covers parameter space more efficiently with same 100-combo budget | — Pending |
| Max 3 params per strategy | Prevents correlated/redundant parameters and overfitting | — Pending |
| Redis for circuit breaker state | Survives app restarts, consistent across potential future workers | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-05 after initialization*
