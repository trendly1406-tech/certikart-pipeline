"""Tests for ARQ WorkerSettings and startup/shutdown lifecycle."""

import pytest

from workers.config import WorkerConfig
from workers.worker import WorkerSettings, shutdown, startup


def test_worker_config_and_settings() -> None:
    config = WorkerConfig()
    assert config.max_retries == 3
    assert WorkerConfig.get_retailer_concurrency("amazon") == 10
    assert WorkerConfig.get_retailer_concurrency("flipkart") == 10
    assert WorkerConfig.get_retailer_concurrency("croma") == 5
    assert WorkerConfig.get_retailer_concurrency("unknown") == 5

    assert len(WorkerSettings.functions) == 7
    assert WorkerSettings.max_jobs == 25
    assert WorkerSettings.queue_name == "certikart:crawl_queue"


@pytest.mark.asyncio
async def test_worker_startup_and_shutdown_lifecycle() -> None:
    ctx: dict[str, object] = {"db_url": "sqlite:///:memory:"}
    await startup(ctx)

    assert "transport" in ctx
    assert "semaphores" in ctx
    assert "db_engine" in ctx
    assert "session_factory" in ctx

    semaphores = ctx["semaphores"]
    assert isinstance(semaphores, dict)
    assert "amazon" in semaphores
    assert "croma" in semaphores

    await shutdown(ctx)
