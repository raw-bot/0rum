"""Unit tests for src/monitoring/telegram_bot.py — TelegramBot.

All tests use MagicMock + AsyncMock for telegram.Bot.
No live Telegram calls. Each test is independent.
_reset_alert_hooks autouse fixture from conftest.py clears hook state between tests.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.risk.events import CircuitBreakerAlert


def make_mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def make_cb_alert() -> CircuitBreakerAlert:
    now = datetime.now(timezone.utc)
    return CircuitBreakerAlert(
        tripped_at=now,
        consecutive_stops=4,
        cooldown_until=now + timedelta(hours=4),
        last_stop_strategy="liquidity_sweep",
        last_stop_trade_id=None,
    )


@pytest.fixture
def mock_bot():
    return make_mock_bot()


@pytest.fixture
def tbot(mock_bot):
    from src.monitoring.telegram_bot import TelegramBot
    return TelegramBot(mock_bot)


# --- Circuit breaker alert tests ---

@pytest.mark.asyncio
async def test_cb_alert_calls_send_message(tbot, mock_bot):
    """send_circuit_breaker_alert calls bot.send_message exactly once."""
    alert = make_cb_alert()
    await tbot.send_circuit_breaker_alert(alert)
    mock_bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_cb_alert_text_contains_tripped(tbot, mock_bot):
    """Circuit breaker message contains 'CIRCUIT BREAKER TRIPPED'."""
    alert = make_cb_alert()
    await tbot.send_circuit_breaker_alert(alert)
    call_kwargs = mock_bot.send_message.call_args.kwargs
    text = call_kwargs["text"]
    assert "CIRCUIT BREAKER TRIPPED" in text


@pytest.mark.asyncio
async def test_cb_alert_catches_exception(mock_bot):
    """send_circuit_breaker_alert does not raise when bot.send_message raises."""
    mock_bot.send_message = AsyncMock(side_effect=Exception("timeout"))
    from src.monitoring.telegram_bot import TelegramBot
    tbot = TelegramBot(mock_bot)
    alert = make_cb_alert()
    # Should not raise
    await tbot.send_circuit_breaker_alert(alert)


@pytest.mark.asyncio
async def test_cb_alert_logs_send_failed_on_exception(mock_bot):
    """send_circuit_breaker_alert logs monitor.telegram_send_failed on error."""
    mock_bot.send_message = AsyncMock(side_effect=Exception("network"))
    from src.monitoring.telegram_bot import TelegramBot

    logged_events = []

    class CapturingLogger:
        def error(self, event, **kwargs):
            logged_events.append(event)
        def warning(self, event, **kwargs):
            pass
        def info(self, event, **kwargs):
            pass

    tbot = TelegramBot(mock_bot)
    alert = make_cb_alert()
    with patch("src.monitoring.telegram_bot.log", CapturingLogger()):
        await tbot.send_circuit_breaker_alert(alert)

    assert "monitor.telegram_send_failed" in logged_events


# --- Lifecycle notification tests ---

@pytest.mark.asyncio
async def test_lifecycle_sl_contains_stopped(tbot, mock_bot):
    """SL close reason produces message containing 'STOPPED'."""
    await tbot.send_lifecycle_notification(
        trade_id="abc-123",
        strategy="liquidity_sweep",
        direction="BUY",
        close_reason="SL",
        exit_price=Decimal("2325.20"),
        pnl_pct=Decimal("-1.23"),
    )
    text = mock_bot.send_message.call_args.kwargs["text"]
    assert "STOPPED" in text
    assert "-1.23" in text


@pytest.mark.asyncio
async def test_lifecycle_tp1_contains_tp1_hit(tbot, mock_bot):
    """TP1 close reason produces message containing 'TP1 HIT'."""
    await tbot.send_lifecycle_notification(
        trade_id="abc-124",
        strategy="liquidity_sweep",
        direction="BUY",
        close_reason="TP1",
        exit_price=Decimal("2358.80"),
        pnl_pct=Decimal("1.56"),
    )
    text = mock_bot.send_message.call_args.kwargs["text"]
    assert "TP1 HIT" in text


@pytest.mark.asyncio
async def test_lifecycle_tp2_contains_tp2_hit(tbot, mock_bot):
    """TP2 close reason produces message containing 'TP2 HIT'."""
    await tbot.send_lifecycle_notification(
        trade_id="abc-125",
        strategy="liquidity_sweep",
        direction="BUY",
        close_reason="TP2",
        exit_price=Decimal("2377.10"),
        pnl_pct=Decimal("2.87"),
    )
    text = mock_bot.send_message.call_args.kwargs["text"]
    assert "TP2 HIT" in text


@pytest.mark.asyncio
async def test_lifecycle_trail_contains_trail_stop(tbot, mock_bot):
    """TRAIL close reason produces message containing 'TRAIL STOP'."""
    await tbot.send_lifecycle_notification(
        trade_id="abc-126",
        strategy="liquidity_sweep",
        direction="BUY",
        close_reason="TRAIL",
        exit_price=Decimal("2350.00"),
        pnl_pct=Decimal("0.62"),
    )
    text = mock_bot.send_message.call_args.kwargs["text"]
    assert "TRAIL STOP" in text


# --- Daily summary tests ---

@pytest.mark.asyncio
async def test_daily_summary_contains_date_and_signals(tbot, mock_bot):
    """Daily summary message contains date, signals_sent, and mode."""
    from src.monitoring.telegram_bot import DailySummaryPayload
    payload = DailySummaryPayload(
        date="2026-05-02",
        signals_sent=5,
        trades_by_reason={"TP1": 2, "TP2": 1, "SL": 1, "TRAIL": 0},
        daily_pnl_pct=1.34,
        mtd_pnl_pct=3.21,
        cb_tripped=False,
        consecutive_stops=0,
        execution_mode="SIGNAL",
        strategy_stats=[
            {"strategy": "liquidity_sweep", "win_rate": 0.75, "profit_factor": 2.1}
        ],
    )
    await tbot.send_daily_summary(payload)
    text = mock_bot.send_message.call_args.kwargs["text"]
    assert "2026-05-02" in text
    assert "5" in text  # signals_sent
    assert "SIGNAL" in text


@pytest.mark.asyncio
async def test_daily_summary_catches_exception(mock_bot):
    """send_daily_summary does not raise when bot.send_message raises."""
    mock_bot.send_message = AsyncMock(side_effect=Exception("timeout"))
    from src.monitoring.telegram_bot import TelegramBot, DailySummaryPayload
    tbot = TelegramBot(mock_bot)
    payload = DailySummaryPayload(
        date="2026-05-02",
        signals_sent=0,
        trades_by_reason={},
        daily_pnl_pct=0.0,
        mtd_pnl_pct=0.0,
        cb_tripped=False,
        consecutive_stops=0,
        execution_mode="SIGNAL",
        strategy_stats=[],
    )
    # Should not raise
    await tbot.send_daily_summary(payload)


@pytest.mark.asyncio
async def test_cb_alert_registered_as_hook(mock_bot):
    """TelegramBot.send_circuit_breaker_alert satisfies BreakerAlertHook callable type."""
    from src.monitoring.telegram_bot import TelegramBot
    from src.risk.hooks import register_alert_hook, _publish_alert

    tbot = TelegramBot(mock_bot)
    register_alert_hook(tbot.send_circuit_breaker_alert)

    alert = make_cb_alert()
    await _publish_alert(alert)

    mock_bot.send_message.assert_called_once()
