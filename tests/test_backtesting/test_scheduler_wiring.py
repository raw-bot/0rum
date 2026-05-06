"""Tests for optimizer job registration in APScheduler."""

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")

from src.scheduler.jobs import create_scheduler, run_optimizer

EXPECTED_JOB_IDS = {
    "refresh_m15",
    "refresh_h1",
    "refresh_h4",
    "refresh_d1",
    "run_pipeline",
    "run_optimizer",
    "monitor_trades",
    "daily_summary",
}


def test_optimizer_job_registered():
    """create_scheduler() must register a job with id='run_optimizer'."""
    scheduler = create_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "run_optimizer" in job_ids, f"run_optimizer not in {job_ids}"


def test_optimizer_job_max_instances():
    """run_optimizer job must have max_instances=1 to prevent concurrent runs."""
    scheduler = create_scheduler()
    optimizer_job = next(j for j in scheduler.get_jobs() if j.id == "run_optimizer")
    assert optimizer_job.max_instances == 1


def test_all_existing_jobs_still_registered():
    """Adding optimizer job must not remove any existing job — regression guard."""
    scheduler = create_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert EXPECTED_JOB_IDS == job_ids, f"Job mismatch. Got: {job_ids}"


def test_run_optimizer_is_async():
    """run_optimizer() must be an async coroutine function."""
    assert asyncio.iscoroutinefunction(run_optimizer)
