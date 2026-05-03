"""0rum — XAUUSD autonomous trading bot.

FastAPI application entry point with structured logging and lifecycle management.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI

from src.config import get_settings
from src.monitoring.health import health_router


def configure_structlog() -> None:
    """Configure structlog for JSON-formatted output.

    All log output is JSON — never plain print() statements.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown hooks."""
    configure_structlog()
    logger = structlog.get_logger(__name__)
    settings = get_settings()

    logger.info(
        "app.starting",
        execution_mode=settings.execution_mode.value,
        database_url=settings.database_url.split("@")[-1],  # hide credentials
    )

    from src.config import MarketDataProvider
    from src.ingestion.candle_fetcher import CandleFetcher

    # Bot initialization (D-11) — MUST precede scheduler start
    from telegram import Bot
    bot = Bot(token=settings.telegram_bot_token)
    await bot.initialize()
    logger.info("app.telegram_bot_initialized")

    # Service instantiation and wiring (D-11, D-12)
    from src.execution.signal_sender import SignalSender
    from src.execution.executor import ExecutionRouter
    from src.monitoring.telegram_bot import TelegramBot
    from src.risk.hooks import register_alert_hook
    from src.scheduler.jobs import _set_monitor_services, _set_pipeline_runner

    signal_sender = SignalSender(bot=bot)
    telegram_bot_inst = TelegramBot(bot=bot)
    register_alert_hook(telegram_bot_inst.send_circuit_breaker_alert)
    executor = ExecutionRouter(signal_sender=signal_sender)
    _set_monitor_services(telegram_bot=telegram_bot_inst, breaker_manager=None)

    # PipelineRunner wiring (inject router singleton)
    from src.pipeline.runner import PipelineRunner
    runner = PipelineRunner(router=executor)
    _set_pipeline_runner(runner)
    logger.info("app.execution_services_wired")

    async def _run_startup_ingestion() -> None:
        async with CandleFetcher(settings=settings) as fetcher:
            if settings.market_data_provider == MarketDataProvider.IG:
                await fetcher.warm_up_all()
            else:
                await fetcher.backfill_all()

    asyncio.create_task(_run_startup_ingestion())
    logger.info(
        "app.startup_ingestion_launched",
        provider=settings.market_data_provider.value,
    )

    # Start APScheduler with all 4 timeframe jobs
    from src.scheduler.jobs import create_scheduler
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("app.scheduler_started", job_count=len(scheduler.get_jobs()))

    yield

    scheduler.shutdown(wait=False)
    logger.info("app.scheduler_shutdown")
    await bot.shutdown()
    logger.info("app.telegram_bot_shutdown")
    logger.info("app.shutdown")


app = FastAPI(
    title="0rum",
    description="Autonomous XAUUSD trading bot — signal mode first, auto mode after 4-week validation",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)

from src.monitoring.dashboard import dashboard_router
app.include_router(dashboard_router)
