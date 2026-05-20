"""Tests for FastAPI application lifecycle wiring."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

from src.main import lifespan


@pytest.mark.asyncio
async def test_lifespan_tracks_and_cancels_startup_ingestion_task():
    """Startup ingestion is stored on app.state and cancelled cleanly on shutdown."""
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = False

    class FakeCandleFetcher:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def backfill_all(self):
            nonlocal cancelled
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise

    scheduler = MagicMock()
    scheduler.get_jobs.return_value = []

    app = FastAPI()
    app.state = SimpleNamespace()

    with (
        patch("src.ingestion.candle_fetcher.CandleFetcher", FakeCandleFetcher),
        patch("src.scheduler.jobs.create_scheduler", return_value=scheduler),
        patch("src.scheduler.jobs._set_pipeline_runner"),
    ):
        async with lifespan(app):
            await asyncio.wait_for(started.wait(), timeout=1)
            task = app.state.startup_ingestion_task
            assert isinstance(task, asyncio.Task)
            assert not task.done()

    assert cancelled is True
    assert app.state.startup_ingestion_task.cancelled()
    scheduler.shutdown.assert_called_once_with(wait=False)
