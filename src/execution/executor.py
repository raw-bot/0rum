"""ExecutionRouter — routes ApprovedSignals to the correct execution path."""

from decimal import Decimal

import structlog

from src.config import ExecutionMode, get_settings
from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)


class ExecutionRouter:
    """Routes approved signals through the configured execution path.

    Phase 7 signal mode is local-only: decisions are persisted and exposed via
    the dashboard. No external notification channel is used.
    """

    async def execute(self, signal: CandidateSignal, size_lots: Decimal) -> bool:
        mode = get_settings().execution_mode
        if mode == ExecutionMode.SIGNAL:
            return await self._execute_signal_mode(signal, size_lots)
        if mode == ExecutionMode.AUTO:
            raise NotImplementedError(
                "Auto mode broker execution not implemented — Phase 8 extension point."
            )
        raise ValueError(f"Unknown execution mode: {mode!r}")

    async def _execute_signal_mode(
        self, signal: CandidateSignal, size_lots: Decimal
    ) -> bool:
        log.info(
            "execution.signal_mode.recorded",
            strategy=signal.strategy.value,
            direction=signal.direction.value,
            size_lots=str(size_lots),
        )
        return True
