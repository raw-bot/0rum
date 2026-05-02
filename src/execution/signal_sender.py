"""SignalSender — formats and sends the initial BUY/SELL Telegram signal message.

Called by ExecutionRouter in signal mode (D-09, D-10).
Single Bot instance injected at construction — never instantiate Bot here (D-11).
Uses ParseMode.HTML — avoids MarkdownV2 escaping issues with decimal prices.
"""

import html
from decimal import Decimal

import structlog
from telegram import Bot
from telegram.constants import ParseMode

from src.config import get_settings
from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)

_STRATEGY_DISPLAY = {
    "liquidity_sweep": "Liquidity Sweep",
    "trend_continuation": "Trend Continuation",
    "breakout_expansion": "Breakout Expansion",
    "ema_momentum": "EMA Momentum",
}


class SignalSender:
    """Formats and sends a Telegram signal message for each ApprovedSignal.

    Args:
        bot: Initialized telegram.Bot instance (shared singleton from main.py).
    """

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._settings = get_settings()

    def _format_message(self, signal: CandidateSignal, size_lots: Decimal) -> str:
        """Build the HTML-formatted Telegram signal message (AGENTS.md §13.1).

        All arithmetic in Decimal. HTML-escape all user-derived strings.
        Percentages: (level - entry) / entry * 100, signed.
        For BUY: SL < entry → sl_pct negative. TP > entry → tp_pct positive.
        For SELL: SL > entry → sl_pct negative (price moves against). TP < entry → tp_pct positive.
        """
        entry = Decimal(str(signal.entry_price))
        sl = Decimal(str(signal.sl_price))
        tp1 = Decimal(str(signal.tp1_price))
        tp2 = Decimal(str(signal.tp2_price)) if signal.tp2_price is not None else None

        # For BUY: SL is below entry (sl - entry < 0 → negative)
        # For SELL: SL is above entry (sl - entry > 0) but it's adverse → negate
        direction_sign = Decimal("1") if signal.direction.value == "BUY" else Decimal("-1")
        sl_pct = float((sl - entry) / entry * Decimal("100") * direction_sign)
        tp1_pct = float((tp1 - entry) / entry * Decimal("100") * direction_sign)

        emoji = "🟢" if signal.direction.value == "BUY" else "🔴"
        strategy_name = html.escape(
            _STRATEGY_DISPLAY.get(signal.strategy.value, signal.strategy.value)
        )

        lines = [
            f"{emoji} <b>{signal.direction.value} XAUUSD</b>",
            f"Entry: <code>{float(entry):.2f}</code>",
            f"SL: <code>{float(sl):.2f}</code> ({sl_pct:+.2f}%)",
            f"TP1: <code>{float(tp1):.2f}</code> ({tp1_pct:+.2f}%)",
        ]

        if tp2 is not None:
            tp2_pct = float((tp2 - entry) / entry * Decimal("100") * direction_sign)
            lines.append(f"TP2: <code>{float(tp2):.2f}</code> ({tp2_pct:+.2f}%)")

        lines.extend([
            f"Strategy: {strategy_name}",
            f"Confidence: {signal.confidence:.0%}",
            f"Size suggestion: <code>{float(size_lots):.2f}</code> lots",
        ])

        return "\n".join(lines)

    async def send_signal(self, signal: CandidateSignal, size_lots: Decimal) -> bool:
        """Format and send the signal message to the configured Telegram chat.

        Returns:
            True on successful delivery, False on any exception.
            On failure: logs execution.send_failed with strategy, direction, error.
            The bot token is NEVER logged (D-11 / security gate T-07-02-01).
        """
        message = self._format_message(signal, size_lots)
        try:
            await self._bot.send_message(
                chat_id=self._settings.telegram_chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
            )
            log.info(
                "execution.signal_sent",
                strategy=signal.strategy.value,
                direction=signal.direction.value,
                size_lots=str(size_lots),
            )
            return True
        except Exception as exc:
            log.error(
                "execution.send_failed",
                strategy=signal.strategy.value,
                direction=signal.direction.value,
                error=str(exc),
            )
            return False
