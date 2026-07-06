"""Execution seam: the single boundary through which positions are opened and
closed.

Phase 3.5 is a NEUTRAL refactor. The paper trade logic still lives in its
original functions in ``loop.py`` (tests import them directly); this module
wraps them behind an ``Executor`` interface so:

  - ``run_loop`` opens/closes through one object instead of three inline calls;
  - the external-signal path (Phase 4) executes through the SAME seam;
  - a future live broker becomes a drop-in ``CcxtExecutor`` with no change to
    callers — the native and external modes both switch at once.

``PaperExecutor`` adds no behaviour of its own: every method delegates to the
existing function, so the established test suite proves the refactor changed
nothing. Live execution is intentionally NOT implemented here (deferred until a
strategy is proven profitable in paper).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from orum.loop import (
    _build_closed_trade,
    close_position_if_needed,
    open_position_from_signal,
)


@runtime_checkable
class Executor(Protocol):
    """How 0rum acts on an accepted signal. Implementations own *how* an
    order is realized; the decision to act stays with the caller."""

    def open(
        self,
        *,
        asset: str,
        strategy: dict,
        goal: dict,
        market: dict,
        rsi: float | None,
        regime: dict | None = None,
        entry_summary: str | None = None,
    ) -> dict:
        """Open a position and return the position record."""

    def close(
        self,
        *,
        position: dict,
        strategy: dict,
        market: dict,
        rsi: float | None,
        regime: dict | None = None,
        exit_triggered: bool = False,
    ) -> dict | None:
        """Close if a risk/DSL exit fires; return the closed trade or None."""

    def force_close(
        self,
        *,
        position: dict,
        strategy: dict,
        market: dict,
        rsi: float | None,
        regime: dict | None = None,
        reason: str = "emergency_stop",
    ) -> dict:
        """Unconditionally close (guardrail emergency); return the closed trade."""


class PaperExecutor:
    """Paper-mode executor. Pure delegation to the existing loop functions."""

    def open(
        self,
        *,
        asset: str,
        strategy: dict,
        goal: dict,
        market: dict,
        rsi: float | None,
        regime: dict | None = None,
        entry_summary: str | None = None,
    ) -> dict:
        return open_position_from_signal(
            asset, strategy, goal, market, rsi, regime, entry_summary=entry_summary
        )

    def close(
        self,
        *,
        position: dict,
        strategy: dict,
        market: dict,
        rsi: float | None,
        regime: dict | None = None,
        exit_triggered: bool = False,
    ) -> dict | None:
        return close_position_if_needed(
            position, strategy, market, rsi, regime, exit_triggered=exit_triggered
        )

    def force_close(
        self,
        *,
        position: dict,
        strategy: dict,
        market: dict,
        rsi: float | None,
        regime: dict | None = None,
        reason: str = "emergency_stop",
    ) -> dict:
        return _build_closed_trade(position, strategy, market, rsi, regime, reason)
