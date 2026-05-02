"""ExecutionRouter — routes ApprovedSignals to the correct execution path.

Signal mode: calls SignalSender.send_signal() — Telegram notification only.
Auto mode: raises NotImplementedError — Phase 8 extension point.

Per D-10 (07-CONTEXT.md):
- SIGNAL branch: send_signal(signal, size_lots), return success/failure bool.
- AUTO branch: raise NotImplementedError with explanatory message.
Per D-11: SignalSender is injected — the Bot singleton is owned by main.py.
"""

from decimal import Decimal

import structlog

from src.config import ExecutionMode, get_settings
from src.execution.signal_sender import SignalSender
from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)


class ExecutionRouter:
    """Routes an ApprovedSignal to signal mode or auto mode execution.

    Args:
        signal_sender: Initialized SignalSender instance (injected from main.py).
    """

    def __init__(self, signal_sender: SignalSender) -> None:
        self._sender = signal_sender

    async def execute(self, signal: CandidateSignal, size_lots: Decimal) -> bool:
        """Execute the signal in the currently configured execution mode.

        Args:
            signal: The CandidateSignal that was approved and risk-checked.
            size_lots: Calculated position size in lots from RiskDecision.sizing.size_lots.

        Returns:
            True if the signal was successfully delivered (SIGNAL mode).
            False if delivery failed (SIGNAL mode send error).

        Raises:
            NotImplementedError: Always, when execution_mode is AUTO (Phase 8 stub).
            ValueError: When execution_mode has an unknown value.
        """
        mode = get_settings().execution_mode
        if mode == ExecutionMode.SIGNAL:
            return await self._execute_signal_mode(signal, size_lots)
        elif mode == ExecutionMode.AUTO:
            raise NotImplementedError(
                "Auto mode broker execution not implemented — Phase 8 extension point."
            )
        else:
            raise ValueError(f"Unknown execution mode: {mode!r}")

    async def _execute_signal_mode(
        self, signal: CandidateSignal, size_lots: Decimal
    ) -> bool:
        """SIGNAL mode: send Telegram notification only — no broker order placed."""
        sent = await self._sender.send_signal(signal=signal, size_lots=size_lots)
        if sent:
            log.info(
                "execution.signal_mode.delivered",
                strategy=signal.strategy.value,
                direction=signal.direction.value,
            )
        else:
            log.warning(
                "execution.signal_mode.delivery_failed",
                strategy=signal.strategy.value,
                direction=signal.direction.value,
            )
        return sent
