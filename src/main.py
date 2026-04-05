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

    # Launch 6-month backfill as background task (non-blocking)
    from src.ingestion.candle_fetcher import CandleFetcher
    fetcher = CandleFetcher(settings=settings)
    asyncio.create_task(fetcher.backfill_all())
    logger.info("app.backfill_launched")

    # Start APScheduler with all 4 timeframe jobs
    from src.scheduler.jobs import create_scheduler
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("app.scheduler_started", job_count=len(scheduler.get_jobs()))

    yield

    scheduler.shutdown(wait=False)
    logger.info("app.shutdown")


app = FastAPI(
    title="0rum",
    description="Autonomous XAUUSD trading bot — signal mode first, auto mode after 4-week validation",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
