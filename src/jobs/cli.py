import asyncio
import json
from datetime import UTC, datetime

import typer
from pydantic import AnyHttpUrl
from rich.console import Console
from rich.table import Table

from categories import get_category, list_departments, supported_categories
from collectors import (
    create_collection_plan,
    discover_category_references,
)
from config import get_settings
from matching import ProductFingerprint, compare_products, reconcile_products
from normalization import normalize_capacity_gb
from sources import (
    FetchedSourceDocument,
    HttpSourceTransport,
    ParsedProduct,
    RawSourceRecord,
    SourceAdapter,
    SourceProductReference,
    get_source_adapter,
    supported_sources,
)

app = typer.Typer(no_args_is_help=True, help="Certikart background data pipeline")
console = Console()


@app.command()
def doctor() -> None:
    settings = get_settings()
    console.print(
        {
            "environment": settings.environment,
            "log_level": settings.log_level,
            "max_source_concurrency": settings.max_source_concurrency,
            "obey_robots_txt": settings.obey_robots_txt,
            "collection_profile": settings.collection_profile,
            "schema_management": settings.schema_management,
            "embedding_generation_enabled": settings.embedding_generation_enabled,
            "embedding_api_format": settings.embedding_api_format,
            "embedding_provider": settings.embedding_provider or "not configured",
            "embedding_model": settings.embedding_model or "not configured",
            "embedding_dimensions": settings.embedding_dimensions,
            "semantic_search_enabled": settings.semantic_search_enabled,
            "supported_sources": supported_sources(),
            "database_url": "configured",
        }
    )


@app.command("db-upgrade")
def db_upgrade(revision: str = typer.Option("head", help="Alembic revision to apply")) -> None:
    """Apply managed catalog migrations as an explicit release step."""
    from storage.migrations import current_database_revision, upgrade_database

    upgrade_database(revision=revision)
    current = current_database_revision()
    console.print_json(json.dumps({"database_revision": current, "status": "upgraded"}))


@app.command("db-current")
def db_current() -> None:
    """Report the current managed catalog migration revision."""
    from storage.migrations import current_database_revision

    console.print_json(json.dumps({"database_revision": current_database_revision()}))


@app.command("backfill-product-images")
def backfill_product_images(
    limit: int = typer.Option(100, min=1, max=1000, help="Maximum products to inspect"),
    force: bool = typer.Option(False, help="Replace existing uploaded galleries"),
) -> None:
    """Upload historical retailer images and persist permanent product gallery URLs."""
    from pydantic import TypeAdapter
    from sqlalchemy import select

    from storage.engine import create_database_engine, get_session_factory
    from storage.images import ImageUploadCandidate, upload_image_candidates
    from storage.models import ProductModel, RetailerProductModel

    settings = get_settings()
    if not settings.image_upload_enabled:
        raise typer.BadParameter("Set CERTIKART_IMAGE_UPLOAD_ENABLED=true first")

    engine = create_database_engine(settings.database_url.get_secret_value())
    session_factory = get_session_factory(engine)
    url_adapter = TypeAdapter(tuple[AnyHttpUrl, ...])

    with session_factory() as session:
        products = list(
            session.scalars(select(ProductModel).order_by(ProductModel.id).limit(limit))
        )
        product_ids = [product.id for product in products]
        listings = list(
            session.scalars(
                select(RetailerProductModel)
                .where(RetailerProductModel.canonical_product_id.in_(product_ids))
                .order_by(
                    RetailerProductModel.canonical_product_id,
                    RetailerProductModel.last_seen_at.desc(),
                )
            )
        )

    products_by_id = {product.id: product for product in products}
    selected: dict[str, RetailerProductModel] = {}
    candidates: list[ImageUploadCandidate] = []
    for listing in listings:
        product_id = listing.canonical_product_id
        if product_id is None or product_id in selected:
            continue
        product = products_by_id[product_id]
        if not force and product.attributes.get("image_url"):
            continue
        raw_urls = listing.attributes.get("source_image_urls", [])
        if not raw_urls:
            continue
        try:
            urls = url_adapter.validate_python(raw_urls)
        except ValueError:
            continue
        selected[product_id] = listing
        candidates.append(
            ImageUploadCandidate(
                canonical_product_id=product_id,
                source=listing.source,
                source_product_id=listing.source_product_id,
                observed_at=(
                    listing.last_seen_at.replace(tzinfo=UTC)
                    if listing.last_seen_at.tzinfo is None
                    else listing.last_seen_at
                ),
                image_urls=urls,
            )
        )

    media = upload_image_candidates(candidates, settings)
    with session_factory() as session, session.begin():
        for product_id, images in media.items():
            stored_product = session.get(ProductModel, product_id)
            if stored_product is None:
                continue
            attributes = dict(stored_product.attributes)
            attributes["image_url"] = str(images[0].url)
            attributes["images"] = [image.model_dump(mode="json") for image in images]
            stored_product.attributes = attributes

    console.print_json(
        json.dumps(
            {
                "products_inspected": len(products),
                "products_with_source_images": len(candidates),
                "products_uploaded": len(media),
                "images_uploaded": sum(len(images) for images in media.values()),
            }
        )
    )


@app.command("refresh-catalog-images")
def refresh_catalog_images(
    limit: int = typer.Option(500, min=1, max=2000, help="Maximum products to refresh"),
    concurrency: int = typer.Option(4, min=1, max=10, help="Concurrent retailer fetches"),
    force: bool = typer.Option(False, help="Refresh products that already have images"),
    category: str = typer.Option("all", help="Category to refresh: laptop, mobile, or all"),
    source: str = typer.Option("all", help="Prefer one retailer source, or use all"),
) -> None:
    """Fetch retailer pages for historical products, upload galleries and persist their URLs."""
    from sqlalchemy import select

    from storage.engine import create_database_engine, get_session_factory
    from storage.images import ImageUploadCandidate, upload_image_candidates
    from storage.models import ProductModel, RetailerProductModel

    settings = get_settings()
    if not settings.image_upload_enabled:
        raise typer.BadParameter("Set CERTIKART_IMAGE_UPLOAD_ENABLED=true first")

    category_names = (
        list(supported_categories())
        if category.casefold().strip() in {"all", "*"}
        else [get_category(category).code]
    )
    source_names = (
        list(supported_sources())
        if source.casefold().strip() in {"all", "*"}
        else [source.casefold().strip()]
    )
    unknown_sources = set(source_names) - set(supported_sources())
    if unknown_sources:
        names = ", ".join(sorted(unknown_sources))
        raise typer.BadParameter(f"Unsupported retailer source: {names}")

    engine = create_database_engine(settings.database_url.get_secret_value())
    session_factory = get_session_factory(engine)
    with session_factory() as session:
        products = list(
            session.scalars(
                select(ProductModel)
                .where(ProductModel.category.in_(category_names))
                .order_by(ProductModel.id)
            )
        )
        listings = list(
            session.scalars(
                select(RetailerProductModel).order_by(
                    RetailerProductModel.canonical_product_id,
                    RetailerProductModel.last_seen_at.desc(),
                )
            )
        )

    categories = {product.id: product.category for product in products}
    needs_image = {
        product.id for product in products if force or not product.attributes.get("image_url")
    }
    listings_by_product: dict[str, list[tuple[str, str, str]]] = {}
    for listing in listings:
        product_id = listing.canonical_product_id
        if (
            product_id is None
            or product_id not in needs_image
            or listing.source not in source_names
        ):
            continue
        listings_by_product.setdefault(product_id, []).append(
            (listing.source, listing.source_product_id, listing.source_url)
        )

    targets = list(listings_by_product.items())[:limit]

    async def collect() -> tuple[list[ImageUploadCandidate], int]:
        transport = HttpSourceTransport()
        semaphore = asyncio.Semaphore(concurrency)
        completed = 0
        failed = 0

        async def fetch_product(
            product_id: str,
            product_listings: list[tuple[str, str, str]],
        ) -> ImageUploadCandidate | None:
            nonlocal completed, failed
            async with semaphore:
                for source, source_product_id, source_url in product_listings:
                    try:
                        reference = SourceProductReference(
                            source_product_id=source_product_id,
                            category=categories[product_id],
                            subcategory=None,
                            source_url=AnyHttpUrl(source_url),
                        )
                        adapter = get_source_adapter(source)([reference], transport)
                        parsed = adapter.parse(await adapter.fetch(source_product_id))
                        if parsed.image_urls:
                            completed += 1
                            if completed % 10 == 0:
                                console.print(
                                    f"[cyan]Image refresh progress: {completed + failed}/"
                                    f"{len(targets)} products[/cyan]"
                                )
                            return ImageUploadCandidate(
                                canonical_product_id=product_id,
                                source=source,
                                source_product_id=source_product_id,
                                observed_at=parsed.observed_at,
                                image_urls=parsed.image_urls,
                            )
                    except Exception:
                        continue
                failed += 1
                if (completed + failed) % 10 == 0:
                    console.print(
                        f"[cyan]Image refresh progress: {completed + failed}/"
                        f"{len(targets)} products[/cyan]"
                    )
                return None

        gathered = await asyncio.gather(
            *(fetch_product(product_id, rows) for product_id, rows in targets)
        )
        return [candidate for candidate in gathered if candidate is not None], failed

    candidates, fetch_failures = asyncio.run(collect())
    media = upload_image_candidates(candidates, settings)
    with session_factory() as session, session.begin():
        for product_id, images in media.items():
            stored_product = session.get(ProductModel, product_id)
            if stored_product is None:
                continue
            attributes = dict(stored_product.attributes)
            attributes["image_url"] = str(images[0].url)
            attributes["images"] = [image.model_dump(mode="json") for image in images]
            stored_product.attributes = attributes

    console.print_json(
        json.dumps(
            {
                "products_targeted": len(targets),
                "categories": category_names,
                "sources": source_names,
                "retailer_pages_with_images": len(candidates),
                "retailer_fetch_failures": fetch_failures,
                "products_uploaded": len(media),
                "images_uploaded": sum(len(images) for images in media.values()),
            }
        )
    )


@app.command("list-sources")
def list_sources() -> None:
    console.print_json(json.dumps({"sources": supported_sources()}))


@app.command("list-departments")
def list_departments_cmd() -> None:
    console.print_json(json.dumps({"departments": list_departments()}))


@app.command("list-categories")
def list_categories() -> None:
    categories = [get_category(code).model_dump(mode="json") for code in supported_categories()]
    console.print_json(json.dumps({"categories": categories}))


@app.command("collection-plan")
def collection_plan(
    source: str = typer.Option("amazon", help="Configured retailer source"),
    category: str = typer.Option("laptop", help="Registered product category"),
    available_products: int = typer.Option(
        0, min=0, help="Discovered/seeded products available to this run"
    ),
    profile: str = typer.Option(
        "", help="smoke, shadow, incremental, or approved backfill; defaults to settings"
    ),
) -> None:
    source_name = source.casefold().strip()
    category_name = get_category(category).code
    get_source_adapter(source_name)
    settings = get_settings()
    selected_profile = profile.casefold().strip() or settings.collection_profile
    plan = create_collection_plan(
        source=source_name,
        category=category_name,
        available_products=available_products,
        profile=selected_profile,
    )
    console.print_json(json.dumps(plan.model_dump(mode="json")))


@app.command("pipeline-demo")
def pipeline_demo() -> None:
    left = ProductFingerprint(
        category="laptop",
        brand="Lenovo",
        model_name="ThinkBook 14 Gen 6",
        attributes={
            "cpu_model": "Ryzen 7 7730U",
            "gpu_model": "Integrated Radeon",
            "ram_gb": 16,
            "storage_gb": normalize_capacity_gb("512 GB"),
        },
    )
    right = left.model_copy()
    console.print_json(json.dumps(compare_products(left, right).model_dump(mode="json")))


@app.command("parse-demo")
def parse_demo(
    source: str = typer.Option("flipkart", help="Source to demonstrate (flipkart, croma, amazon)"),
) -> None:
    source_name = source.casefold().strip()
    adapter_cls = get_source_adapter(source_name)
    now = datetime.now(UTC)

    sample_html: str
    sample_url: AnyHttpUrl
    if source_name == "flipkart":
        sample_url = AnyHttpUrl("https://www.flipkart.com/lenovo-thinkbook-14/p/itm12345")
        sample_html = """
        <html>
            <h1 class="VU-ZEz">Lenovo ThinkBook 14 Gen 6 Laptop (16 GB/512 GB SSD)</h1>
            <div class="Nx9bqj">₹54,990</div>
            <div class="yRaY8j">₹82,500</div>
            <div id="sellerName"><span><span>RetailNet</span></span></div>
            <div class="XQDdHH">4.4</div>
            <span class="Wphh3N"><span>120 Ratings & 18 Reviews</span></span>
            <table class="_14cfVK">
                <tr class="_1s_Smc">
                    <td class="_1hKmda">Processor Name</td>
                    <td class="_21lJal">AMD Ryzen 7 7730U</td>
                </tr>
                <tr class="_1s_Smc">
                    <td class="_1hKmda">RAM</td>
                    <td class="_21lJal">16 GB</td>
                </tr>
                <tr class="_1s_Smc">
                    <td class="_1hKmda">SSD Capacity</td>
                    <td class="_21lJal">512 GB</td>
                </tr>
                <tr class="_1s_Smc">
                    <td class="_1hKmda">Graphic Processor</td>
                    <td class="_21lJal">Integrated Radeon</td>
                </tr>
            </table>
        </html>
        """
    elif source_name == "croma":
        sample_url = AnyHttpUrl("https://www.croma.com/lenovo-thinkbook-14/p/267890")
        sample_html = """
        <html>
            <h1 class="pd-title">Lenovo ThinkBook 14 Gen 6 Laptop (16GB, 512GB SSD)</h1>
            <span class="amount">₹54,990</span>
            <span class="old-price">₹82,500</span>
            <span class="rating-text">4.4</span>
            <ul class="cp-specification">
                <li>
                    <span class="spec-title">Processor Type</span>
                    <span class="spec-desc">AMD Ryzen 7 7730U</span>
                </li>
                <li>
                    <span class="spec-title">RAM</span>
                    <span class="spec-desc">16 GB</span>
                </li>
                <li>
                    <span class="spec-title">SSD Capacity</span>
                    <span class="spec-desc">512 GB</span>
                </li>
                <li>
                    <span class="spec-title">Graphics Processor</span>
                    <span class="spec-desc">Integrated Radeon</span>
                </li>
            </ul>
        </html>
        """
    else:
        sample_url = AnyHttpUrl("https://www.amazon.in/dp/B0CX12345")
        sample_html = """
        <html>
            <span id="productTitle">Lenovo ThinkBook 14 Gen 6 Laptop (16GB/512GB SSD)</span>
            <span class="a-price"><span class="a-offscreen">₹54,990.00</span></span>
            <span class="a-text-price"><span class="a-offscreen">₹82,500.00</span></span>
            <div id="merchant-info">Sold by Appario Retail Private Ltd</div>
            <span class="a-icon-alt">4.4 out of 5 stars</span>
            <span id="acrCustomerReviewText">120 ratings</span>
            <table id="productDetails_techSpec_section_1">
                <tr><th>Processor Brand</th><td>AMD Ryzen 7 7730U</td></tr>
                <tr><th>RAM Size</th><td>16 GB</td></tr>
                <tr><th>Hard Drive Size</th><td>512 GB</td></tr>
                <tr><th>Graphics Coprocessor</th><td>Integrated Radeon</td></tr>
            </table>
        </html>
        """

    record = RawSourceRecord(
        source=source_name,
        source_product_id="DEMO-PROD-001",
        category="laptop",
        source_url=sample_url,
        observed_at=now,
        payload={"html": sample_html},
        content_hash="0123456789abcdef0123456789abcdef",
    )

    adapter = adapter_cls([], DummyTransport())
    parsed = adapter.parse(record)

    console.print_json(
        json.dumps(
            {
                "parsed_product": parsed.model_dump(mode="json"),
                "fingerprint": parsed.to_fingerprint().model_dump(mode="json"),
                "price_observation": parsed.to_price_observation().model_dump(mode="json"),
            }
        )
    )


@app.command("reconcile-demo")
def reconcile_demo() -> None:
    now = datetime.now(UTC)
    flipkart_adapter_cls = get_source_adapter("flipkart")
    croma_adapter_cls = get_source_adapter("croma")
    amazon_adapter_cls = get_source_adapter("amazon")

    flipkart_rec = RawSourceRecord(
        source="flipkart",
        source_product_id="FLIP-TB14",
        category="laptop",
        source_url=AnyHttpUrl("https://www.flipkart.com/lenovo-thinkbook-14/p/itm123"),
        observed_at=now,
        payload={
            "html": """
            <html>
                <h1 class="VU-ZEz">Lenovo ThinkBook 14 Gen 6 Laptop (16 GB/512 GB SSD)</h1>
                <div class="Nx9bqj">₹54,990</div>
                <div class="yRaY8j">₹82,500</div>
                <div id="sellerName"><span><span>RetailNet</span></span></div>
                <div class="XQDdHH">4.4</div>
                <span class="Wphh3N"><span>18 Reviews</span></span>
                <table class="_14cfVK">
                    <tr class="_1s_Smc"><td>Processor Name</td><td>AMD Ryzen 7 7730U</td></tr>
                    <tr class="_1s_Smc"><td>RAM</td><td>16 GB</td></tr>
                    <tr class="_1s_Smc"><td>SSD Capacity</td><td>512 GB</td></tr>
                    <tr class="_1s_Smc"><td>Graphic Processor</td><td>Integrated Radeon</td></tr>
                </table>
            </html>
            """
        },
        content_hash="0123456789abcdef0123456789abcdef",
    )

    croma_rec = RawSourceRecord(
        source="croma",
        source_product_id="CROMA-TB14",
        category="laptop",
        source_url=AnyHttpUrl("https://www.croma.com/lenovo-thinkbook-14/p/267890"),
        observed_at=now,
        payload={
            "html": """
            <html>
                <h1 class="pd-title">Lenovo ThinkBook 14 Gen 6 Laptop (16GB, 512GB SSD)</h1>
                <span class="amount">₹53,490</span>
                <span class="old-price">₹82,500</span>
                <span class="rating-text">4.4</span>
                <ul class="cp-specification">
                    <li>
                        <span class="spec-title">Processor Type</span>
                        <span class="spec-desc">AMD Ryzen 7 7730U</span>
                    </li>
                    <li>
                        <span class="spec-title">RAM</span>
                        <span class="spec-desc">16 GB</span>
                    </li>
                    <li>
                        <span class="spec-title">SSD Capacity</span>
                        <span class="spec-desc">512 GB</span>
                    </li>
                    <li>
                        <span class="spec-title">Graphics Processor</span>
                        <span class="spec-desc">Integrated Radeon</span>
                    </li>
                </ul>
            </html>
            """
        },
        content_hash="0123456789abcdef0123456789abcdef",
    )

    amazon_rec = RawSourceRecord(
        source="amazon",
        source_product_id="B0CXTB14",
        category="laptop",
        source_url=AnyHttpUrl("https://www.amazon.in/dp/B0CXTB14"),
        observed_at=now,
        payload={
            "html": """
            <html>
                <span id="productTitle">Lenovo ThinkBook 14 Gen 6 Laptop (16GB/512GB SSD)</span>
                <span class="a-price"><span class="a-offscreen">₹54,200.00</span></span>
                <span class="a-text-price"><span class="a-offscreen">₹82,500.00</span></span>
                <div id="merchant-info">Sold by Appario Retail Private Ltd</div>
                <span class="a-icon-alt">4.4 out of 5 stars</span>
                <span id="acrCustomerReviewText">120 ratings</span>
                <table id="productDetails_techSpec_section_1">
                    <tr><th>Processor Brand</th><td>AMD Ryzen 7 7730U</td></tr>
                    <tr><th>RAM Size</th><td>16 GB</td></tr>
                    <tr><th>Hard Drive Size</th><td>512 GB</td></tr>
                    <tr><th>Graphics Coprocessor</th><td>Integrated Radeon</td></tr>
                </table>
            </html>
            """
        },
        content_hash="0123456789abcdef0123456789abcdef",
    )

    amazon_hp_rec = RawSourceRecord(
        source="amazon",
        source_product_id="B0CXHP15",
        category="laptop",
        source_url=AnyHttpUrl("https://www.amazon.in/dp/B0CXHP15"),
        observed_at=now,
        payload={
            "html": """
            <html>
                <span id="productTitle">HP Pavilion 15 Intel Core i5 (16GB/512GB SSD)</span>
                <span class="a-price"><span class="a-offscreen">₹62,990.00</span></span>
                <span class="a-text-price"><span class="a-offscreen">₹79,990.00</span></span>
                <div id="merchant-info">Sold by Appario Retail Private Ltd</div>
                <span class="a-icon-alt">4.5 out of 5 stars</span>
                <span id="acrCustomerReviewText">85 ratings</span>
                <table id="productDetails_techSpec_section_1">
                    <tr><th>Processor Brand</th><td>Intel Core i5-1335U</td></tr>
                    <tr><th>RAM Size</th><td>16 GB</td></tr>
                    <tr><th>Hard Drive Size</th><td>512 GB</td></tr>
                </table>
            </html>
            """
        },
        content_hash="0123456789abcdef0123456789abcdef",
    )

    dummy_t = DummyTransport()
    flipkart_adapter = flipkart_adapter_cls([], dummy_t)
    croma_adapter = croma_adapter_cls([], dummy_t)
    amazon_adapter = amazon_adapter_cls([], dummy_t)

    products = [
        flipkart_adapter.parse(flipkart_rec),
        croma_adapter.parse(croma_rec),
        amazon_adapter.parse(amazon_rec),
        amazon_adapter.parse(amazon_hp_rec),
    ]

    report = reconcile_products(products)
    console.print_json(json.dumps(report.model_dump(mode="json")))


async def _fetch_and_parse_live_targets(
    targets: list[tuple[str, str, str]],
) -> list[ParsedProduct]:
    transport = HttpSourceTransport()
    parsed_products: list[ParsedProduct] = []

    for source, prod_id, url_str in targets:
        url = AnyHttpUrl(url_str)
        adapter_cls = get_source_adapter(source)
        ref = SourceProductReference(
            source_product_id=prod_id,
            category="laptop",
            subcategory=None,
            source_url=url,
        )
        adapter = adapter_cls([ref], transport)
        try:
            raw_rec = await adapter.fetch(prod_id)
            parsed = adapter.parse(raw_rec)
            parsed_products.append(parsed)
            price_display = f"₹{parsed.price_paise / 100:,.2f}"
            console.print(
                f"[bold green]✓ Live product fetched from {source}:[/bold green] "
                f"{parsed.title} | Price: {price_display} | Seller: {parsed.seller or 'N/A'}"
            )
        except Exception as err:
            console.print(
                f"[bold yellow]! Live fetch note for {source} ({url_str}):[/bold yellow] {err}"
            )

    return parsed_products


@app.command("live-collect")
def live_collect(
    amazon_url: str = typer.Option(
        "",
        help="Amazon product URL to fetch live",
    ),
    flipkart_url: str = typer.Option(
        "",
        help="Flipkart product URL to fetch live",
    ),
    croma_url: str = typer.Option(
        "",
        help="Croma product URL to fetch live",
    ),
) -> None:
    """Fetch live product pages over HTTP from Amazon, Flipkart, and Croma, then reconcile."""
    targets: list[tuple[str, str, str]] = []

    if amazon_url:
        targets.append(("amazon", "LIVE-AMAZON-01", amazon_url))
    if flipkart_url:
        targets.append(("flipkart", "LIVE-FLIPKART-01", flipkart_url))
    if croma_url:
        targets.append(("croma", "LIVE-CROMA-01", croma_url))

    if not targets:
        # Default representative live targets
        targets = [
            (
                "flipkart",
                "LIVE-FLIP-1",
                "https://www.flipkart.com/lenovo-ideapad-slim-3-intel-core-i5-12th-gen-1235u-16-gb-512-gb-ssd-windows-11-home-15iau7-thin-light-laptop/p/itm2271dfecfe3aa",
            ),
            (
                "amazon",
                "LIVE-AMZ-1",
                "https://www.amazon.in/dp/B0CRR6DK7V",
            ),
            (
                "croma",
                "LIVE-CROMA-1",
                "https://www.croma.com/p/316655",
            ),
        ]

    console.print(f"[cyan]Initiating live collection across {len(targets)} sources...[/cyan]")
    products = asyncio.run(_fetch_and_parse_live_targets(targets))

    if not products:
        console.print(
            "[yellow]No live products could be parsed (network block, captcha, or invalid URLs). "
            "Displaying offline verified multi-source reconciliation demo:[/yellow]"
        )
        reconcile_demo()
        return

    console.print(
        f"\n[bold cyan]Reconciling and deduplicating {len(products)} "
        "live products across sources:[/bold cyan]"
    )
    report = reconcile_products(products)
    console.print_json(json.dumps(report.model_dump(mode="json")))


async def _run_bulk_collection(
    categories: list[str],
    sources: list[str],
    limit_per_source: int,
    concurrency: int = 15,
) -> list[ParsedProduct]:
    transport = HttpSourceTransport()
    all_products: list[ParsedProduct] = []
    semaphore = asyncio.Semaphore(concurrency)

    for cat in categories:
        for source in sources:
            console.print(f"[bold blue]→ Discovering {cat} listings from {source}...[/bold blue]")
            refs = await discover_category_references(
                source, cat, transport, max_items=limit_per_source
            )
            console.print(f"  Discovered {len(refs)} live product links on {source} for {cat}.")

            if not refs:
                continue

            adapter_cls = get_source_adapter(source)
            adapter = adapter_cls(refs, transport)

            async def _fetch_single(
                ref_item: SourceProductReference,
                src_adapter: SourceAdapter = adapter,
                src_name: str = source,
            ) -> ParsedProduct | None:
                async with semaphore:
                    try:
                        raw_rec = await src_adapter.fetch(ref_item.source_product_id)
                        parsed = src_adapter.parse(raw_rec)
                        price_str = (
                            f"₹{parsed.price_paise / 100:,.2f}" if parsed.price_paise else "N/A"
                        )
                        console.print(
                            f"  [green]✓[/green] {src_name}: {parsed.title[:60]}... | {price_str}"
                        )
                        return parsed
                    except Exception as err:
                        console.print(
                            f"  [yellow]![/yellow] Error parsing {src_name} "
                            f"{ref_item.source_product_id}: {err}"
                        )
                        return None

            results = await asyncio.gather(*[_fetch_single(r) for r in refs])
            for p in results:
                if p is not None:
                    all_products.append(p)

    return all_products


@app.command("bulk-collect")
def bulk_collect(
    category: str = typer.Option(
        "all",
        "--category",
        "-cat",
        help="Category to scrape (e.g. 'laptop', 'mobile', or 'all')",
    ),
    sources: str = typer.Option(
        "all",
        "--sources",
        "-s",
        help="Comma-separated sources (e.g. 'amazon,flipkart,croma') or 'all'",
    ),
    limit: int = typer.Option(
        20, "--limit", "-l", help="Max products to discover per source per category"
    ),
    concurrency: int = typer.Option(15, "--concurrency", "-c", help="Concurrent worker tasks"),
    output: str = typer.Option(
        "collection_report.json",
        "--output",
        "-o",
        help="Path to save output JSON report",
    ),
    persist: bool = typer.Option(
        True,
        "--persist/--no-persist",
        help="Persist canonical products, offers, and price history to database",
    ),
) -> None:
    """Discover, collect, and reconcile products across any/all categories and retailer sources."""
    limit_val = limit if isinstance(limit, int) else 10
    concurrency_val = concurrency if isinstance(concurrency, int) else 5
    output_val = output if isinstance(output, str) else "collection_report.json"
    persist_val = persist if isinstance(persist, bool) else False

    # Determine target categories
    all_registered = supported_categories()
    if category.casefold().strip() in ("all", "*"):
        target_categories = list(all_registered)
    else:
        cat_clean = category.casefold().strip()
        get_category(cat_clean)
        target_categories = [cat_clean]

    # Determine target sources
    all_sources = supported_sources()
    if sources.casefold().strip() in ("all", "*"):
        target_sources = list(all_sources)
    else:
        target_sources = [
            s.strip().casefold()
            for s in sources.split(",")
            if s.strip() and s.strip().casefold() in all_sources
        ]
        if not target_sources:
            target_sources = list(all_sources)

    total_target = len(target_categories) * len(target_sources) * limit_val
    console.print(
        f"[bold magenta]=== Starting Multi-Source Bulk Collection ===[/bold magenta]\n"
        f"  Categories: [cyan]{', '.join(target_categories)}[/cyan]\n"
        f"  Sources: [yellow]{', '.join(target_sources)}[/yellow]\n"
        f"  Target: [green]up to {total_target} products ({limit_val}/source/cat)[/green]\n"
    )

    products = asyncio.run(
        _run_bulk_collection(
            categories=target_categories,
            sources=target_sources,
            limit_per_source=limit_val,
            concurrency=concurrency_val,
        )
    )

    if not products:
        console.print("[red]No products collected. Exiting.[/red]")
        return

    console.print(
        f"\n[bold cyan]Successfully collected and parsed {len(products)} "
        "total product records across retailers.[/bold cyan]"
    )
    console.print(
        "[bold cyan]Reconciling exact variants and calculating cross-retailer prices...[/bold cyan]"
    )

    report = reconcile_products(products)

    # Rich table output
    table = Table(title=f"Bulk Collection Summary ({len(report.clusters)} Canonical Variants)")
    table.add_column("Cluster ID", style="cyan", no_wrap=True)
    table.add_column("Category", style="blue")
    table.add_column("Brand", style="magenta")
    table.add_column("Model / Configuration", style="white")
    table.add_column("Offers", justify="right", style="green")
    table.add_column("Best Price", justify="right", style="bold green")
    table.add_column("Best Source", style="yellow")
    table.add_column("Savings", justify="right", style="bold red")

    for cluster in report.clusters:
        offers_summary = ", ".join(f"{o.source.capitalize()}" for o in cluster.offers)
        best_price_str = (
            f"₹{cluster.best_price_paise / 100:,.2f}"
            if cluster.best_price_paise is not None
            else "N/A"
        )
        savings_str = (
            f"₹{cluster.savings_paise / 100:,.2f}"
            if cluster.savings_paise and cluster.savings_paise > 0
            else "-"
        )
        model_display = cluster.model_name[:45]

        table.add_row(
            cluster.cluster_id,
            cluster.category.capitalize(),
            cluster.brand.capitalize(),
            model_display,
            f"{len(cluster.offers)} ({offers_summary})",
            best_price_str,
            cluster.best_source.capitalize() if cluster.best_source else "-",
            savings_str,
        )

    console.print(table)

    # Source breakdown table
    if report.source_breakdown:
        breakdown_table = Table(title="Per-Retailer Reconciliation & Identity Breakdown")
        breakdown_table.add_column("Retailer Source", style="yellow")
        breakdown_table.add_column("Collected", justify="right", style="cyan")
        breakdown_table.add_column("Multi-Retailer Linked", justify="right", style="green")
        breakdown_table.add_column("Single-Source Canonical", justify="right", style="magenta")
        breakdown_table.add_column("Review Queue", justify="right", style="red")

        for src, stats in report.source_breakdown.items():
            breakdown_table.add_row(
                src.capitalize(),
                str(stats.collected_count),
                str(stats.matched_in_multi_source_cluster),
                str(stats.single_source_canonical_count),
                str(stats.review_count),
            )
        console.print(breakdown_table)

    # Save output report JSON
    report_dict = report.model_dump(mode="json")
    with open(output_val, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2)

    console.print(
        f"\n[green]✓ Detailed collection report saved to [bold]{output_val}[/bold][/green]"
    )

    # Database persistence
    if persist_val:
        try:
            from storage.engine import create_database_engine, get_session_factory, init_db
            from storage.images import upload_report_images
            from storage.repository import PipelineRepository

            engine = create_database_engine()
            init_db(engine)
            session_factory = get_session_factory(engine)
            media = upload_report_images(report)
            with session_factory() as session, session.begin():
                db_metrics = PipelineRepository.persist_reconciliation_report(
                    session, report, media
                )
            p_cnt = db_metrics["products_persisted"]
            o_cnt = db_metrics["offers_persisted"]
            console.print(f"[green]✓ DB: {p_cnt} products, {o_cnt} offers.[/green]")
        except Exception as db_err:
            console.print(f"[yellow]! Database persistence note: {db_err}[/yellow]")


@app.command("bulk-collect-laptops")
def bulk_collect_laptops(
    limit: int = typer.Option(50, "--limit", "-l", help="Max products to discover per source"),
    concurrency: int = typer.Option(15, "--concurrency", "-c", help="Concurrent worker tasks"),
    output: str = typer.Option(
        "laptop_collection_report.json",
        "--output",
        "-o",
        help="Path to save output JSON report",
    ),
    persist: bool = typer.Option(
        True,
        "--persist/--no-persist",
        help="Persist canonical products, offers, and price history to database",
    ),
) -> None:
    """Discover, collect, and reconcile laptops across Flipkart, Amazon, and Croma."""
    bulk_collect(
        category="laptop",
        sources="all",
        limit=limit,
        concurrency=concurrency,
        output=output,
        persist=persist,
    )


@app.command("sitemap-sync")
def sitemap_sync(
    source: str = typer.Option("croma", "--source", "-s", help="Retailer source name"),
    sitemap_url: str = typer.Option(
        "https://www.croma.com/sitemap.xml",
        "--url",
        "-u",
        help="Sitemap URL",
    ),
    limit: int = typer.Option(50, "--limit", "-l", help="Max product URLs to extract"),
    category: str = typer.Option("laptop", "--category", "-c", help="Category filter"),
) -> None:
    """Ingest XML sitemap and discover product references."""
    from collectors.sitemaps import SitemapDiscoveryEngine

    transport = HttpSourceTransport()

    async def _run() -> None:
        console.print(
            f"[bold cyan]Fetching and parsing sitemap for {source} ({sitemap_url})...[/bold cyan]"
        )
        refs = await SitemapDiscoveryEngine.discover_from_sitemap(
            sitemap_url,
            source=source,
            transport=transport,
            max_items=limit,
            category=category,
        )
        console.print(
            f"[green]✓ Discovered {len(refs)} URLs from sitemap matching '{category}'.[/green]"
        )
        for r in refs[:5]:
            console.print(f"  • [{r.source_product_id}] {r.source_url}")

    asyncio.run(_run())


@app.command("lifecycle-summary")
def lifecycle_summary() -> None:
    """Display catalog health distribution across lifecycle states."""
    from storage.engine import create_database_engine, get_session_factory, init_db
    from storage.repository import PipelineRepository

    engine = create_database_engine()
    init_db(engine)
    session_factory = get_session_factory(engine)

    with session_factory() as session:
        dist = PipelineRepository.get_lifecycle_distribution(session)

    table = Table(title="Catalog Product Lifecycle Distribution")
    table.add_column("Lifecycle Status", style="yellow")
    table.add_column("Count", justify="right", style="green")
    table.add_column("Health / Policy", style="cyan")

    policy_notes = {
        "ACTIVE": "In Stock & Fresh (4h recrawl)",
        "STALE": "Missed 1-2 Crawl Cycles (Warning)",
        "UNAVAILABLE": "Out of Stock / Inactive (48h recrawl)",
        "DISCONTINUED": "Delisted > 14 Days (7d recrawl)",
    }

    for status_name, count_val in dist.items():
        note = policy_notes.get(status_name, "-")
        table.add_row(status_name, str(count_val), note)

    console.print(table)


@app.command("embedding-status")
def embedding_status(
    provider: str = typer.Option("", help="Provider label; defaults to pipeline settings"),
    model: str = typer.Option("", help="Embedding model; defaults to pipeline settings"),
    dimensions: int | None = typer.Option(
        None,
        min=1,
        help="Expected dimensions; defaults to pipeline settings",
    ),
    category: str = typer.Option("", help="Optional exact category code"),
) -> None:
    """Report document and embedding coverage without calling a provider."""
    from retrieval.indexing import get_embedding_index_health
    from storage.engine import create_database_engine, get_session_factory, init_db

    settings = get_settings()
    provider_name = provider.strip() or settings.embedding_provider
    model_name = model.strip() or settings.embedding_model
    if not provider_name or not model_name:
        raise typer.BadParameter("Configure or pass both provider and model")

    engine = create_database_engine()
    init_db(engine)
    session_factory = get_session_factory(engine)
    with session_factory() as session:
        health = get_embedding_index_health(
            session,
            provider=provider_name,
            model_name=model_name,
            dimensions=dimensions or settings.embedding_dimensions,
            category=category.strip() or None,
        )
    console.print_json(json.dumps(health.model_dump(mode="json")))


@app.command("embedding-backfill")
def embedding_backfill(
    limit: int = typer.Option(1000, min=1, help="Maximum embeddings to write"),
    category: str = typer.Option("", help="Optional exact category code"),
) -> None:
    """Explicitly generate missing or stale embeddings in resumable batches."""
    from retrieval.embeddings import build_configured_embedding_provider
    from retrieval.indexing import backfill_product_embeddings
    from storage.engine import create_database_engine, get_session_factory, init_db

    settings = get_settings()
    provider = build_configured_embedding_provider(settings)
    engine = create_database_engine()
    init_db(engine)
    session_factory = get_session_factory(engine)
    with session_factory() as session:
        result = backfill_product_embeddings(
            session,
            provider=provider,
            batch_size=settings.embedding_batch_size,
            limit=limit,
            category=category.strip() or None,
        )
    console.print_json(json.dumps(result.model_dump(mode="json")))


@app.command("crawl-cycle")
def crawl_cycle(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of URLs to crawl in this cycle"),
    persist: bool = typer.Option(True, "--persist/--no-persist", help="Persist to database"),
) -> None:
    """Execute one incremental crawl cycle guided by scheduler priority."""
    console.print(
        f"[bold cyan]Running incremental crawl cycle (batch limit: {limit})...[/bold cyan]"
    )
    # Re-use bulk collection logic with dynamic scheduling
    bulk_collect_laptops(limit=limit, persist=persist)


@app.command("worker")
def run_worker_daemon(
    burst: bool = typer.Option(False, "--burst", "-b", help="Execute existing jobs and exit"),
) -> None:
    """Launch distributed ARQ background worker process."""
    from typing import Any, cast

    from arq.worker import run_worker

    from workers.worker import WorkerSettings

    console.print("[bold green]Starting ARQ Background Worker daemon...[/bold green]")
    run_worker(cast(Any, WorkerSettings), burst=burst)


@app.command("queue-status")
def queue_status(
    redis_url: str = typer.Option(
        "redis://localhost:6379/0",
        "--redis-url",
        "-r",
        help="Redis URL",
    ),
) -> None:
    """Display distributed Redis queue lengths and backlog status."""
    import redis

    from workers.config import WorkerConfig

    try:
        r = redis.from_url(redis_url)  # type: ignore[no-untyped-call]
        table = Table(title="Distributed Redis Worker Queues")
        table.add_column("Queue Name", style="yellow")
        table.add_column("Key", style="cyan")
        table.add_column("Backlog", justify="right", style="green")

        queues = [
            ("Discovery Queue", WorkerConfig.DISCOVERY_QUEUE),
            ("Crawl Queue", WorkerConfig.CRAWL_QUEUE),
            ("Persistence Queue", WorkerConfig.PERSISTENCE_QUEUE),
            ("Dead Letter Queue", WorkerConfig.DEAD_LETTER_QUEUE),
        ]

        for q_name, q_key in queues:
            length = r.llen(q_key) if r.exists(q_key) else 0
            table.add_row(q_name, q_key, str(length))

        console.print(table)
    except Exception as err:
        console.print(f"[yellow]! Redis queue status note: {err}[/yellow]")


class DummyTransport:
    async def fetch(self, source_url: AnyHttpUrl) -> FetchedSourceDocument:
        return FetchedSourceDocument(
            observed_at=datetime.now(UTC),
            payload={},
            content_hash="0123456789abcdef0123456789abcdef",
        )


if __name__ == "__main__":
    app()
