"""Distributed task functions for ARQ background worker execution."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import httpx
from curl_cffi import CurlError
from pydantic import AnyHttpUrl
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError

from collectors.discovery import discover_product_references
from collectors.sitemaps import SitemapDiscoveryEngine
from jobs.product_import import (
    ProductImportRepository,
    ProductImportRequest,
    ProductImportResult,
    ProductImportStatus,
    build_product_search_queries,
    parse_product_url,
)
from matching.matcher import MatchDecision, compare_products
from matching.reconciliation import reconcile_products
from retrieval.embeddings import build_configured_embedding_provider
from retrieval.indexing import backfill_product_embeddings
from sources.contracts import ParsedProduct, SourceProductReference
from sources.registry import get_source_adapter, supported_sources
from sources.transport import HttpSourceTransport
from storage.engine import create_database_engine, get_session_factory, init_db
from storage.images import upload_report_images
from storage.repository import PipelineRepository
from workers.config import WorkerConfig

logger = logging.getLogger(__name__)


class ProductImportRejectedError(ValueError):
    """Raised when a collected listing cannot become a canonical product."""


_MOBILE_CATEGORY_TERMS = (
    "smartphone",
    "mobile phone",
    "iphone",
    "galaxy s",
    "galaxy a",
    "oneplus",
    "pixel phone",
    "redmi",
    "poco",
    "realme",
    "vivo ",
    "oppo ",
)
_LAPTOP_CATEGORY_TERMS = (
    "laptop",
    "notebook",
    "chromebook",
    "macbook",
    "thinkpad",
    "ideapad",
    "vivobook",
)


def infer_import_category(product: ParsedProduct) -> str:
    """Infer the supported category from retailer-visible product identity."""
    identity = f"{product.title} {product.brand} {product.model_name}".casefold()
    if any(term in identity for term in _MOBILE_CATEGORY_TERMS):
        return "mobile"
    if any(term in identity for term in _LAPTOP_CATEGORY_TERMS):
        return "laptop"
    raise ProductImportRejectedError(
        "Could not determine whether the retailer product is a laptop or mobile"
    )


async def _publish_import_progress(
    ctx: dict[str, Any],
    result: ProductImportResult,
    stage: str,
) -> None:
    redis = ctx.get("redis")
    if redis is None or not hasattr(redis, "publish"):
        return
    payload = result.model_copy(update={"status": result.status}).model_dump(mode="json")
    payload["stage"] = stage
    await redis.publish(
        f"certikart:product-import:{result.run_id}",
        json.dumps(payload, separators=(",", ":")),
    )


async def task_discover_sitemap(
    ctx: dict[str, Any],
    source: str,
    sitemap_url: str,
    *,
    limit: int = 100,
    category: str = "laptop",
) -> dict[str, Any]:
    """Worker task: Ingest XML sitemap and discover product URLs."""
    transport = ctx.get("transport") or HttpSourceTransport()
    refs = await SitemapDiscoveryEngine.discover_from_sitemap(
        sitemap_url,
        source=source,
        transport=transport,
        max_items=limit,
        category=category,
    )
    return {
        "source": source,
        "sitemap_url": sitemap_url,
        "category": category,
        "discovered_count": len(refs),
        "product_ids": [r.source_product_id for r in refs],
    }


async def task_crawl_product(
    ctx: dict[str, Any],
    url: str,
    source: str,
    *,
    source_product_id: str | None = None,
    category: str = "laptop",
) -> dict[str, Any]:
    """Worker task: Fetch and parse a single product listing with host rate limiting."""
    transport = ctx.get("transport") or HttpSourceTransport()
    adapter_cls = get_source_adapter(source)

    p_id = source_product_id or url
    requested_category = category
    provisional_category = "laptop" if category == "auto" else category
    ref = SourceProductReference(
        source_product_id=p_id,
        category=provisional_category,
        subcategory=None,
        source_url=AnyHttpUrl(url),
    )
    adapter = adapter_cls([ref], transport)

    # Acquire retailer concurrency semaphore if registered in context
    semaphores: dict[str, asyncio.Semaphore] = ctx.get("semaphores", {})
    sem = semaphores.get(source.casefold().strip())

    async with sem if sem is not None else contextlib.nullcontext():
        raw_doc = await adapter.fetch(p_id)
        parsed = adapter.parse(raw_doc)
        if requested_category == "auto":
            detected_category = infer_import_category(parsed)
            if detected_category != parsed.category:
                parsed = adapter.parse(raw_doc.model_copy(update={"category": detected_category}))

    return {
        "status": "success",
        "source": parsed.source,
        "source_product_id": parsed.source_product_id,
        "title": parsed.title,
        "brand": parsed.brand,
        "model_name": parsed.model_name,
        "price_paise": parsed.price_paise,
        "in_stock": parsed.in_stock,
        "parsed_payload": parsed.model_dump(mode="json"),
        "category": parsed.category,
    }


async def _discover_cross_source_matches(
    ctx: dict[str, Any],
    seed: ParsedProduct,
    *,
    max_candidates_per_source: int = 5,
) -> list[ParsedProduct]:
    """Search other retailers and retain only exact compatible variants."""
    transport = ctx.get("transport") or HttpSourceTransport()
    queries = build_product_search_queries(seed)
    seed_fingerprint = seed.to_fingerprint()

    async def collect_source(source: str) -> list[ParsedProduct]:
        if source == seed.source.casefold().strip():
            return []

        reference_batches = await asyncio.gather(
            *(
                discover_product_references(
                    source,
                    query,
                    seed.category,
                    transport,
                    max_items=max_candidates_per_source,
                )
                for query in queries
            )
        )
        references: list[SourceProductReference] = []
        seen_ids: set[str] = set()
        for batch in reference_batches:
            for reference in batch:
                if reference.source_product_id in seen_ids:
                    continue
                seen_ids.add(reference.source_product_id)
                references.append(reference)
                if len(references) >= max_candidates_per_source:
                    break
            if len(references) >= max_candidates_per_source:
                break

        async def crawl_exact_match(reference: SourceProductReference) -> ParsedProduct | None:
            try:
                crawl_result = await task_crawl_product(
                    ctx,
                    url=str(reference.source_url),
                    source=source,
                    source_product_id=reference.source_product_id,
                    category=seed.category,
                )
                payload = crawl_result.get("parsed_payload")
                if not isinstance(payload, dict):
                    return None
                candidate = ParsedProduct.model_validate(payload)
                comparison = compare_products(seed_fingerprint, candidate.to_fingerprint())
                if comparison.decision is MatchDecision.MATCH:
                    return candidate
            except Exception as error:
                # Cross-retailer enrichment is best effort. The shopper's original
                # listing must still import when another retailer blocks or changes.
                logger.info(
                    "Cross-source candidate rejected after collection",
                    extra={
                        "source": source,
                        "source_product_id": reference.source_product_id,
                        "error_type": type(error).__name__,
                    },
                )
            return None

        candidates = await asyncio.gather(
            *(crawl_exact_match(reference) for reference in references)
        )
        matches = [candidate for candidate in candidates if candidate is not None]
        logger.info(
            "Cross-source discovery completed",
            extra={
                "source": source,
                "query_count": len(queries),
                "candidate_count": len(references),
                "exact_match_count": len(matches),
            },
        )
        return matches

    discovered = await asyncio.gather(*(collect_source(source) for source in supported_sources()))
    return [product for source_matches in discovered for product in source_matches]


async def task_reconcile_and_persist(
    ctx: dict[str, Any],
    parsed_product_payloads: list[dict[str, Any]],
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Worker task: Reconcile parsed products and persist to PostgreSQL."""
    parsed_products: list[ParsedProduct] = [
        ParsedProduct.model_validate(p) for p in parsed_product_payloads
    ]
    report = reconcile_products(parsed_products)

    metrics: dict[str, int] = {
        "products_persisted": 0,
        "offers_persisted": 0,
    }

    if persist:
        engine = ctx.get("db_engine") or create_database_engine()
        init_db(engine)
        session_factory = ctx.get("session_factory") or get_session_factory(engine)
        media = await asyncio.to_thread(upload_report_images, report)
        with session_factory() as session, session.begin():
            metrics = PipelineRepository.persist_reconciliation_report(session, report, media)

    return {
        "total_collected": report.total_collected,
        "total_clusters": report.total_clusters,
        "multi_source_clusters": report.multi_source_clusters,
        "source_breakdown": {k: v.model_dump() for k, v in report.source_breakdown.items()},
        "db_metrics": metrics,
    }


async def task_import_product_url(
    ctx: dict[str, Any],
    payload: dict[str, object],
) -> dict[str, Any]:
    """Validate, refresh, reconcile, and persist one shopper-supplied product URL."""
    request = ProductImportRequest.model_validate(payload)
    target = parse_product_url(request.product_url, request.category)
    engine = ctx.get("db_engine") or create_database_engine()
    init_db(engine)
    session_factory = ctx.get("session_factory") or get_session_factory(engine)

    with session_factory() as session, session.begin():
        repository = ProductImportRepository(session)
        run = repository.create_or_get(request, target)
        if run.status == ProductImportStatus.SUCCEEDED.value:
            return ProductImportResult(
                run_id=run.id,
                status=ProductImportStatus.SUCCEEDED,
                canonical_product_id=run.canonical_product_id,
                reused_fresh_listing=run.reused_fresh_listing,
            ).model_dump(mode="json")
        if run.status == ProductImportStatus.CANCELLED.value:
            return ProductImportResult(
                run_id=run.id,
                status=ProductImportStatus.CANCELLED,
            ).model_dump(mode="json")
        repository.mark_running(run, "checking_freshness")
        fresh_product_id = repository.find_fresh_product(target, request.freshness_seconds)
        reuse_fresh_listing = fresh_product_id is not None and repository.has_multiple_sources(
            fresh_product_id
        )
        if reuse_fresh_listing and fresh_product_id is not None:
            repository.mark_succeeded(
                run,
                fresh_product_id,
                reused_fresh_listing=True,
            )

    if reuse_fresh_listing and fresh_product_id is not None:
        result = ProductImportResult(
            run_id=request.run_id,
            status=ProductImportStatus.SUCCEEDED,
            canonical_product_id=fresh_product_id,
            reused_fresh_listing=True,
        )
        await _publish_import_progress(ctx, result, "completed")
        return result.model_dump(mode="json")

    try:
        with session_factory() as session, session.begin():
            repository = ProductImportRepository(session)
            active_run = repository.get(request.run_id)
            if active_run is None:
                raise RuntimeError("Product import run disappeared before collection")
            if active_run.status == ProductImportStatus.CANCELLED.value:
                return ProductImportResult(
                    run_id=active_run.id,
                    status=ProductImportStatus.CANCELLED,
                ).model_dump(mode="json")
            repository.update_stage(active_run, "fetching")

        await _publish_import_progress(
            ctx,
            ProductImportResult(run_id=request.run_id, status=ProductImportStatus.RUNNING),
            "fetching",
        )
        crawl_result = await task_crawl_product(
            ctx,
            url=str(target.source_url),
            source=target.source,
            source_product_id=target.source_product_id,
            category=target.category,
        )
        parsed_payload = crawl_result.get("parsed_payload")
        if not isinstance(parsed_payload, dict):
            raise ProductImportRejectedError("Retailer parser returned no product payload")
        parsed = ParsedProduct.model_validate(parsed_payload)
        effective_target = target.model_copy(update={"category": parsed.category})

        with session_factory() as session, session.begin():
            repository = ProductImportRepository(session)
            active_run = repository.get(request.run_id)
            if active_run is None:
                raise RuntimeError("Product import run disappeared before source discovery")
            repository.update_stage(active_run, "discovering_sources")
        await _publish_import_progress(
            ctx,
            ProductImportResult(run_id=request.run_id, status=ProductImportStatus.RUNNING),
            "discovering_sources",
        )
        related_listings = await _discover_cross_source_matches(ctx, parsed)
        report = reconcile_products([parsed, *related_listings])
        if not report.clusters:
            raise ProductImportRejectedError("Collected listing failed catalog quality checks")

        await _publish_import_progress(
            ctx,
            ProductImportResult(run_id=request.run_id, status=ProductImportStatus.RUNNING),
            "persisting",
        )
        media = await asyncio.to_thread(upload_report_images, report)
        with session_factory() as session, session.begin():
            repository = ProductImportRepository(session)
            persist_run = repository.get(request.run_id)
            if persist_run is None:
                raise RuntimeError("Product import run disappeared before persistence")
            if persist_run.status == ProductImportStatus.CANCELLED.value:
                return ProductImportResult(
                    run_id=persist_run.id,
                    status=ProductImportStatus.CANCELLED,
                ).model_dump(mode="json")
            repository.update_stage(persist_run, "persisting")
            if persist_run.category == "auto":
                persist_run.category = parsed.category
            PipelineRepository.persist_reconciliation_report(session, report, media)
            canonical_product_id = repository.resolved_product_id(effective_target)
            if canonical_product_id is None:
                raise ProductImportRejectedError("Product persisted without a canonical catalog ID")
            repository.mark_succeeded(
                persist_run,
                canonical_product_id,
                reused_fresh_listing=False,
            )

        result = ProductImportResult(
            run_id=request.run_id,
            status=ProductImportStatus.SUCCEEDED,
            canonical_product_id=canonical_product_id,
        )
        await _publish_import_progress(ctx, result, "completed")
        return result.model_dump(mode="json")
    except ProductImportRejectedError as error:
        failure_code = "product_rejected"
        failure_message = str(error)
    except (httpx.HTTPError, CurlError) as error:
        failure_code = "retailer_unavailable"
        failure_message = str(error)
    except (PydanticValidationError, ValueError, LookupError) as error:
        failure_code = "invalid_product_data"
        failure_message = str(error)
    except (SQLAlchemyError, RuntimeError) as error:
        failure_code = "pipeline_unavailable"
        failure_message = str(error)

    with session_factory() as session, session.begin():
        repository = ProductImportRepository(session)
        failed_run = repository.get(request.run_id)
        if failed_run is not None and failed_run.status != ProductImportStatus.CANCELLED.value:
            repository.mark_failed(failed_run, failure_code, failure_message)
    result = ProductImportResult(
        run_id=request.run_id,
        status=ProductImportStatus.FAILED,
        failure_code=failure_code,
    )
    await _publish_import_progress(ctx, result, "failed")
    return result.model_dump(mode="json")


async def task_cancel_product_import(
    ctx: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Mark a queued/running import cancelled; the orchestrator checks before persistence."""
    engine = ctx.get("db_engine") or create_database_engine()
    init_db(engine)
    session_factory = ctx.get("session_factory") or get_session_factory(engine)
    with session_factory() as session, session.begin():
        cancelled = ProductImportRepository(session).cancel(run_id)
    status = ProductImportStatus.CANCELLED if cancelled else ProductImportStatus.FAILED
    result = ProductImportResult(
        run_id=run_id,
        status=status,
        failure_code=None if cancelled else "not_cancellable",
    )
    if cancelled:
        await _publish_import_progress(ctx, result, "cancelled")
    return result.model_dump(mode="json")


async def task_backfill_product_embeddings(
    ctx: dict[str, Any],
    *,
    limit: int = 1000,
    category: str | None = None,
) -> dict[str, Any]:
    """Worker task: idempotently generate missing or stale catalog embeddings."""
    if limit < 1:
        raise ValueError("Embedding backfill limit must be positive")

    from config import get_settings

    settings = get_settings()
    injected_provider = ctx.get("embedding_provider")
    provider = injected_provider or build_configured_embedding_provider(settings)
    engine = ctx.get("db_engine") or create_database_engine()
    init_db(engine)
    session_factory = ctx.get("session_factory") or get_session_factory(engine)

    def run() -> dict[str, Any]:
        with session_factory() as session:
            result = backfill_product_embeddings(
                session,
                provider=provider,
                batch_size=settings.embedding_batch_size,
                limit=limit,
                category=category,
            )
        return result.model_dump(mode="json")

    return await asyncio.to_thread(run)


async def task_send_to_dead_letter(
    ctx: dict[str, Any],
    url: str,
    source: str,
    error_message: str,
    attempts: int,
) -> dict[str, Any]:
    """Worker task: Isolate repeated failures in the dead-letter queue."""
    return {
        "status": "dead_lettered",
        "url": url,
        "source": source,
        "error": error_message,
        "attempts": attempts,
        "queue": WorkerConfig.DEAD_LETTER_QUEUE,
    }
