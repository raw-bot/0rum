"""TelegramBot — lifecycle, circuit-breaker, and daily-summary Telegram notifications.

Handles all notification types EXCEPT the initial BUY/SELL signal (that is SignalSender).
Notification types (NOTIF-02, NOTIF-03, NOTIF-04):
  - Lifecycle: TP1 HIT, TP2 HIT, SL (STOPPED), TRAIL STOP
  - Circuit breaker trip alert
  - Daily summary at 00:00 UTC

Single Bot instance injected at construction (D-11). Never instantiate Bot here.
Uses ParseMode.HTML — avoids MarkdownV2 escaping issues.
All send failures: caught, logged as monitor.telegram_send_failed, never raised.
"""

import html
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import structlog
from telegram import Bot
from telegram.constants import ParseMode

from src.config import get_settings
from src.risk.events import CircuitBreakerAlert

log = structlog.get_logger(__name__)

# Close reason → display string mapping (per AGENTS.md §14.1 / UI-SPEC copywriting)
_CLOSE_REASON_DISPLAY = {
    "SL": "STOPPED",
    "TP1": "TP1 HIT",
    "TP2": "TP2 HIT",
    "TRAIL": "TRAIL STOP",
    "CIRCUIT_BREAKER": "CIRCUIT BREAK",
    "MANUAL": "MANUAL CLOSE",
}

_CLOSE_REASON_EMOJI = {
    "SL": "❌",
    "TP1": "✅",
    "TP2": "✅",
    "TRAIL": "⚠️",
    "CIRCUIT_BREAKER": "🚨",
    "MANUAL": "ℹ️",
}


@dataclass
class DailySummaryPayload:
    """Payload for the daily summary notification (D-19, AGENTS.md §14.3).

    All fields populated by the daily_summary scheduler job from DB queries.
    """

    date: str                        # YYYY-MM-DD
    signals_sent: int
    trades_by_reason: dict[str, int]  # {"TP1": 2, "SL": 1, "TRAIL": 0, "TP2": 0}
    daily_pnl_pct: float
    mtd_pnl_pct: float
    cb_tripped: bool
    consecutive_stops: int
    execution_mode: str               # "SIGNAL" or "AUTO"
    strategy_stats: list[dict]        # [{"strategy": str, "win_rate": float, "profit_factor": float}]


class TelegramBot:
    """Telegram notification handler for lifecycle events, circuit breaker, and daily summary.

    Args:
        bot: Initialized telegram.Bot singleton from main.py (D-11).
    """

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._settings = get_settings()

    async def _send(self, text: str, event_name: str) -> None:
        """Internal send wrapper with error isolation (per hooks.py pattern)."""
        try:
            await self._bot.send_message(
                chat_id=self._settings.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            log.error("monitor.telegram_send_failed", notification=event_name, error=str(exc))

    async def send_circuit_breaker_alert(self, alert: CircuitBreakerAlert) -> None:
        """Send circuit breaker trip notification (NOTIF-03).

        Hook-compatible: async def(alert: CircuitBreakerAlert) -> None
        Satisfies BreakerAlertHook = Callable[[CircuitBreakerAlert], Awaitable[None]].
        """
        cooldown_str = alert.cooldown_until.strftime("%Y-%m-%d %H:%M UTC")
        strategy_safe = html.escape(alert.last_stop_strategy)
        text = (
            f"🚨 <b>CIRCUIT BREAKER TRIPPED</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Consecutive stops: <code>{alert.consecutive_stops}</code>\n"
            f"Triggered by: {strategy_safe}\n"
            f"Cooldown until: <code>{cooldown_str}</code>\n"
            f"All new signals blocked until cooldown expires."
        )
        log.warning(
            "monitor.circuit_breaker_alert",
            consecutive_stops=alert.consecutive_stops,
            cooldown_until=alert.cooldown_until.isoformat(),
        )
        await self._send(text, event_name="circuit_breaker_alert")

    async def send_lifecycle_notification(
        self,
        trade_id: str,
        strategy: str,
        direction: str,
        close_reason: str,
        exit_price: Optional[Decimal],
        pnl_pct: Optional[Decimal],
    ) -> None:
        """Send trade lifecycle notification (NOTIF-02): TP1, TP2, SL, TRAIL.

        Args:
            trade_id: TradeORM UUID string (for log correlation only)
            strategy: Strategy name string
            direction: "BUY" or "SELL"
            close_reason: "SL", "TP1", "TP2", or "TRAIL"
            exit_price: Price at which the trade closed
            pnl_pct: Final blended P&L percentage (Decimal, None if not yet computed)
        """
        display = _CLOSE_REASON_DISPLAY.get(close_reason, close_reason)
        emoji = _CLOSE_REASON_EMOJI.get(close_reason, "ℹ️")
        strategy_safe = html.escape(strategy)
        price_str = f"{float(exit_price):.2f}" if exit_price is not None else "N/A"
        pnl_str = f"{float(pnl_pct):+.2f}%" if pnl_pct is not None else ""

        text = (
            f"{emoji} <b>{display}</b> — {strategy_safe} {direction} XAUUSD"
            f" @ <code>{price_str}</code>"
        )
        if pnl_str:
            text += f" | P&amp;L: <code>{pnl_str}</code>"

        log.info(
            "monitor.trade_closed",
            trade_id=trade_id,
            strategy=strategy,
            direction=direction,
            close_reason=close_reason,
            pnl_pct=str(pnl_pct) if pnl_pct is not None else None,
        )
        await self._send(text, event_name=f"lifecycle_{close_reason.lower()}")

    async def send_daily_summary(self, payload: DailySummaryPayload) -> None:
        """Send daily summary notification at 00:00 UTC (NOTIF-04, AGENTS.md §14.3).

        Args:
            payload: DailySummaryPayload assembled by the daily_summary scheduler job.
        """
        tp1_count = payload.trades_by_reason.get("TP1", 0)
        tp2_count = payload.trades_by_reason.get("TP2", 0)
        sl_count = payload.trades_by_reason.get("SL", 0)
        trail_count = payload.trades_by_reason.get("TRAIL", 0)
        trades_opened = sum(payload.trades_by_reason.values())

        cb_status = (
            f"TRIPPED ({payload.consecutive_stops}/8 consecutive stops)"
            if payload.cb_tripped
            else f"OFF ({payload.consecutive_stops}/8 consecutive stops)"
        )

        strategy_lines = []
        for s in payload.strategy_stats:
            name = html.escape(s.get("strategy", "?"))
            wr = s.get("win_rate", 0)
            pf = s.get("profit_factor", 0)
            strategy_lines.append(f"  {name}: WR {wr:.0%} | PF {pf:.2f}")

        strategy_block = "\n".join(strategy_lines) if strategy_lines else "  No data yet"

        text = (
            f"📊 <b>Daily Summary — {html.escape(payload.date)}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Signals: <code>{payload.signals_sent}</code> sent\n"
            f"Trades: <code>{trades_opened}</code> opened | "
            f"<code>{tp1_count}</code> TP1 | <code>{tp2_count}</code> TP2 | "
            f"<code>{sl_count}</code> stopped | <code>{trail_count}</code> trail\n"
            f"P&amp;L: <code>{payload.daily_pnl_pct:+.2f}%</code> (daily) | "
            f"<code>{payload.mtd_pnl_pct:+.2f}%</code> (MTD)\n"
            f"Circuit Breaker: {cb_status}\n"
            f"Mode: {html.escape(payload.execution_mode)}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Per-strategy:\n{strategy_block}"
        )

        log.info("monitor.daily_summary_sent", date=payload.date, signals=payload.signals_sent)
        await self._send(text, event_name="daily_summary")
