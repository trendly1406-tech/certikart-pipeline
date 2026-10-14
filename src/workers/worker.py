from __future__ import annotations

import asyncio
from typing import Any

from arq import cron, func

from sources.transport import HttpSourceTransport
from storage.engine import create_database_engine, get_session_factory, init_db
from workers.config import WorkerConfig
from workers.tasks import (
    task_backfill_product_embeddings,
    task_cancel_product_import,
    task_crawl_product,
    task_discover_sitemap,
    task_import_product_url,
    task_reconcile_and_persist,
    task_send_to_dead_letter,
)


async def startup(ctx: dict[Any, Any]) -> None:
    """Initialize shared HTTP transports, DB engines, and host semaphores on worker startup."""
    ctx["transport"] = HttpSourceTransport()

    # Per-retailer concurrency semaphores
    ctx["semaphores"] = {
        "amazon": asyncio.Semaphore(WorkerConfig.get_retailer_concurrency("amazon")),
        "flipkart": asyncio.Semaphore(WorkerConfig.get_retailer_concurrency("flipkart")),
        "croma": asyncio.Semaphore(WorkerConfig.get_retailer_concurrency("croma")),
    }

    # DB engine & session factory
    db_url = ctx.get("db_url")
    engine = create_database_engine(str(db_url) if db_url else None)
    init_db(engine)
    ctx["db_engine"] = engine
    ctx["session_factory"] = get_session_factory(engine)


async def shutdown(ctx: dict[Any, Any]) -> None:
    """Graceful teardown of shared connections on worker shutdown."""
    transport = ctx.get("transport")
    if transport is not None and hasattr(transport, "close"):
        await transport.close()

    engine = ctx.get("db_engine")
    if engine is not None and hasattr(engine, "dispose"):
        engine.dispose()


class WorkerSettings:
    """ARQ Worker Settings configuration."""

    functions = (
        task_discover_sitemap,
        task_crawl_product,
        func(task_import_product_url, timeout=180),
        task_cancel_product_import,
        func(task_reconcile_and_persist, timeout=900),
        func(task_backfill_product_embeddings, timeout=1800),
        task_send_to_dead_letter,
    )
    cron_jobs = (cron(task_backfill_product_embeddings, minute={0, 15, 30, 45}),)
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = WorkerConfig.get_redis_settings()
    max_jobs = 25
    job_timeout = 60
    queue_name = WorkerConfig.CRAWL_QUEUE
