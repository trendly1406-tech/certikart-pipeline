"""Flipkart parser extracting structured product metadata and specifications."""

from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime

from parsel import Selector
from pydantic import AnyHttpUrl

from normalization import normalize_text
from sources.common import (
    build_category_attributes,
    build_spec_sections,
    extract_digits_to_paise,
    extract_manufacturer_part_number,
    infer_brand,
    is_ignored_spec_key,
)
from sources.contracts import ParsedProduct, RawSourceRecord
from sources.images import image_urls


def extract_flipkart_specs(
    sel: Selector,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Extract flat specs dictionary and section-organized specs hierarchy from Flipkart HTML."""
    specs: dict[str, str] = {}
    sections: dict[str, dict[str, str]] = {}

    # 1. Modern React Web Grid Sections (.grid-formation or section containers)
    section_blocks = sel.css(
        "div.grid-formation.grid-column-1, div.grid-formation, "
        "div._1psv1ze4i._1psv1ze29, div.r-1udh08x div.grid-formation"
    )
    for block in section_blocks:
        heading_el = (
            block.css("div[font='default-fk-font-l']::text").get()
            or block.css("div.v1zwn21n.v1zwn24::text").get()
            or block.css("div.v1zwn24::text").get()
        )
        section_name = heading_el.strip() if heading_el else "General"
        if is_ignored_spec_key(section_name):
            continue
        section_specs: dict[str, str] = {}

        for item in block.css("div.grid-formation-dynamic"):
            key_el = (
                item.css("div.v1zwn21o.v1zwn28::text").get()
                or item.css("div.v1zwn28::text").get()
                or item.css("div[font='default-fk-font-m']::text").get()
                or item.xpath(".//div[contains(@class, 'v1zwn21o')]/text()").get()
            )
            val_el = (
                item.css("div.v1zwn21n.v1zwn27::text").get()
                or item.css("div.v1zwn21n.v1zwn26::text").get()
                or item.css("div.v1zwn27::text").get()
                or item.css("div.v1zwn26::text").get()
                or item.css("div[font='s']::text").get()
                or item.xpath(
                    ".//div[contains(@class, 'v1zwn21n') or contains(@class, 'v1zwn27')]/text()"
                ).get()
            )
            if key_el and val_el:
                key_clean = key_el.strip()
                val_clean = val_el.strip()
                if not key_clean or not val_clean or is_ignored_spec_key(key_clean):
                    continue
                k_clean = normalize_text(key_clean)
                specs[k_clean] = val_clean
                section_specs[key_clean] = val_clean

        if section_specs:
            sections[section_name] = section_specs

    # 2. Standalone .grid-formation-dynamic elements (if inside spec sections)
    for item in sel.css("div.grid-formation-dynamic"):
        key_el = (
            item.css("div.v1zwn21o.v1zwn28::text").get()
            or item.css("div.v1zwn28::text").get()
            or item.css("div[font='default-fk-font-m']::text").get()
            or item.xpath(".//div[contains(@class, 'v1zwn21o')]/text()").get()
        )
        val_el = (
            item.css("div.v1zwn21n.v1zwn27::text").get()
            or item.css("div.v1zwn21n.v1zwn26::text").get()
            or item.css("div.v1zwn27::text").get()
            or item.css("div.v1zwn26::text").get()
            or item.css("div[font='s']::text").get()
            or item.xpath(
                ".//div[contains(@class, 'v1zwn21n') or contains(@class, 'v1zwn27')]/text()"
            ).get()
        )
        if key_el and val_el:
            key_clean = key_el.strip()
            val_clean = val_el.strip()
            if not key_clean or not val_clean or is_ignored_spec_key(key_clean):
                continue
            k_clean = normalize_text(key_clean)
            if k_clean not in specs:
                specs[k_clean] = val_clean

    # 3. Warranty tab items
    warranty_headings = sel.xpath(
        ".//div[contains(@class, 'v1zwn24') and not(contains(@class, 'v1zwn21n'))]"
    )
    for head in warranty_headings:
        head_text = head.xpath("./text()").get()
        val_text = head.xpath("following-sibling::div[contains(@class, 'v1zwn26')][1]/text()").get()
        if head_text and val_text:
            key_clean = head_text.strip()
            val_clean = val_text.strip()
            if not key_clean or not val_clean or is_ignored_spec_key(key_clean):
                continue
            k_clean = normalize_text(key_clean)
            if k_clean not in specs:
                specs[k_clean] = val_clean
            sections.setdefault("Warranty", {})[key_clean] = val_clean

    # 4. Manufacturer info items (direct child divs)
    mfg_xpath = (
        ".//div[contains(@class, '_1psv1zeb9') and "
        "./div[contains(@class, 'v1zwn21o')] and "
        "./div[contains(@class, 'v1zwn21n')]]"
    )
    for mfg in sel.xpath(mfg_xpath):
        k = mfg.xpath("./div[contains(@class, 'v1zwn21o')]/text()").get()
        v = mfg.xpath("./div[contains(@class, 'v1zwn21n')]/text()").get()
        if k and v:
            key_clean = k.strip()
            val_clean = v.strip()
            if not key_clean or not val_clean or is_ignored_spec_key(key_clean):
                continue
            k_clean = normalize_text(key_clean)
            if k_clean not in specs:
                specs[k_clean] = val_clean
            sections.setdefault("Manufacturer Info", {})[key_clean] = val_clean

    # 5. Legacy Flipkart table extraction (table._14cfVK, tr._1s_Smc, tr.row)
    for row in sel.css("tr._1s_Smc, tr.row, div._14cfVK tr"):
        key_el = row.css("td._1hKmda::text, td:first-child::text").get()
        val_el = row.css("td._21lJal li::text, td._21lJal::text, td:last-child::text").get()
        if key_el and val_el:
            key_clean = key_el.strip()
            val_clean = val_el.strip()
            if not key_clean or not val_clean or is_ignored_spec_key(key_clean):
                continue
            k_clean = normalize_text(key_clean)
            if k_clean not in specs:
                specs[k_clean] = val_clean

    # 5. Bullet points in description (_21Ahn-, _2418kt)
    for li in sel.css("li._21Ahn-::text, div._2418kt li::text").getall():
        li_clean = li.strip()
        if not li_clean or is_ignored_spec_key(li_clean):
            continue
        if ":" in li_clean:
            k, v = li_clean.split(":", 1)
            key_clean = k.strip()
            val_clean = v.strip()
            if not key_clean or not val_clean or is_ignored_spec_key(key_clean):
                continue
            k_clean = normalize_text(key_clean)
            if k_clean not in specs:
                specs[k_clean] = val_clean
        else:
            li_lower = li_clean.casefold()
            if (
                "ram" in li_lower or "rom" in li_lower or "storage" in li_lower
            ) and "memory" not in specs:
                specs["memory"] = li_clean
            elif (
                "display" in li_lower
                or "screen" in li_lower
                or "inch" in li_lower
                or "cm (" in li_lower
            ) and "display" not in specs:
                specs["display"] = li_clean
            elif ("camera" in li_lower or "mp " in li_lower) and "camera" not in specs:
                specs["camera"] = li_clean
            elif ("battery" in li_lower or "mah" in li_lower) and "battery" not in specs:
                specs["battery"] = li_clean
            elif (
                any(
                    p in li_lower
                    for p in (
                        "processor",
                        "snapdragon",
                        "dimensity",
                        "helio",
                        "bionic",
                        "unisoc",
                        "octa core",
                        "quad core",
                    )
                )
                and "processor" not in specs
            ):
                specs["processor"] = li_clean
            elif "warranty" in li_lower and "warranty" not in specs:
                specs["warranty"] = li_clean

    return specs, sections


def parse_flipkart_payload(
    payload: dict[str, object],
    source_url: AnyHttpUrl,
    source_product_id: str,
    observed_at: datetime,
    category: str = "laptop",
    subcategory: str | None = None,
) -> ParsedProduct:
    """Parse raw Flipkart payload or HTML string into normalized ParsedProduct."""
    html = str(payload.get("html", ""))
    sel = Selector(text=html) if html else None

    # Check JSON-LD if present in HTML
    json_ld_data: dict[str, object] = {}
    if sel:
        for raw_json_ld in sel.css('script[type="application/ld+json"]::text').getall():
            try:
                loaded = json.loads(raw_json_ld)
                if isinstance(loaded, dict) and loaded.get("@type") in ("Product", "ItemPage"):
                    json_ld_data = loaded
                    break
                if isinstance(loaded, list):
                    for item in loaded:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            json_ld_data = item
                            break
            except (json.JSONDecodeError, TypeError):
                continue

    # Title
    title = ""
    if sel:
        title = (
            sel.css("h1.VU-ZEz::text").get()
            or sel.css("span.B_NuCI::text").get()
            or sel.css("h1._6EBuvT::text").get()
            or sel.css("meta[property='og:title']::attr(content)").get()
            or sel.css("meta[name='twitter:title']::attr(content)").get()
            or sel.css("h1::text").get()
            or ""
        ).strip()
    if not title and json_ld_data.get("name"):
        title = str(json_ld_data["name"]).strip()
    if not title and sel:
        raw_page_title = sel.css("title::text").get()
        if raw_page_title and "buy products online" not in raw_page_title.casefold():
            title = raw_page_title.split("|")[0].split("-")[0].strip()
    if not title:
        title = str(payload.get("title", f"Flipkart Product {source_product_id}"))

    # Clean title stop-phrases
    if title:
        title = re.sub(
            r"\s*(?:online\s+at\s+best\s+price\s+(?:on|in)\s+flipkart(?:\.com)?|online\s+at\s+best\s+price.*|\(includes\s+extra\s+discount[^)]*\))\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()

    # Prices
    price_paise: int | None = None
    mrp_paise: int | None = None

    if sel:
        price_text = (
            sel.css("div._30jeq3._16J0da::text").get()
            or sel.css("div.Nx9bqj._4b5DiR::text").get()
            or sel.css("div._30jeq3::text").get()
            or sel.css("div.Nx9bqj::text").get()
        )
        price_paise = extract_digits_to_paise(price_text)

        # Strikethrough MRP extraction
        mrp_elements = sel.css(
            "div.yRaY8j, div._3I9_wc, div._3I9_wc._2p6cR5, div.yRaY8j._14Ij5r, span.k3eYh1"
        ).getall()
        for el in mrp_elements:
            m = re.search(r"₹\s*([\d,]{3,})", el)
            if m:
                val = extract_digits_to_paise(m.group(1))
                if val and (price_paise is None or val >= price_paise):
                    mrp_paise = val
                    break

        if mrp_paise is None and html:
            mrp_match = re.search(
                r"(?:MRP|Maximum Retail Price)[^\d₹]*₹?\s*([\d,]{3,})", html, re.IGNORECASE
            )
            if mrp_match:
                val = extract_digits_to_paise(mrp_match.group(1))
                if val and (price_paise is None or val >= price_paise):
                    mrp_paise = val

    if price_paise is None and "offers" in json_ld_data:
        offers = json_ld_data["offers"]
        if isinstance(offers, dict) and "price" in offers:
            price_paise = extract_digits_to_paise(str(offers["price"]))

    if price_paise is None:
        raw_price = payload.get("price_paise") or payload.get("price")
        price_paise = (
            int(raw_price)
            if isinstance(raw_price, int)
            else (extract_digits_to_paise(str(raw_price)) or 0)
        )

    if mrp_paise is None and "mrp_paise" in payload:
        raw_mrp = payload["mrp_paise"]
        if isinstance(raw_mrp, int):
            mrp_paise = raw_mrp
        elif raw_mrp is not None:
            mrp_paise = extract_digits_to_paise(str(raw_mrp))

    # In stock check
    in_stock = True
    if sel:
        out_of_stock_text = sel.css("div._16FRp0::text, div._16FRp0 span::text").get()
        if out_of_stock_text and "out of stock" in out_of_stock_text.casefold():
            in_stock = False
        sold_out = sel.css("button:disabled::text, div._1t_v5X::text").get()
        if sold_out and ("sold out" in sold_out.casefold() or "unavailable" in sold_out.casefold()):
            in_stock = False

    # Seller
    seller: str | None = None
    if sel:
        seller_raw = (
            sel.css("div#sellerName span span::text").get()
            or sel.css("div.DByuf4 span::text").get()
            or sel.css("div._1RLviY span::text").get()
            or sel.css("div._1RLviY::text").get()
            or sel.css("div._2mD1Ys span::text").get()
            or sel.css("span.h8pY3V::text").get()
        )
        if seller_raw:
            seller = seller_raw.strip()
    if not seller and payload.get("seller"):
        seller = str(payload["seller"])

    # Rating & Reviews
    rating: float | None = None
    review_count: int | None = None
    if sel:
        rating_text = sel.css("div._3LWZlK::text, div.XQDdHH::text").get()
        if rating_text:
            with contextlib.suppress(ValueError):
                rating = float(rating_text.strip())
        reviews_text = sel.css("span._2_R_DZ span::text, span.Wphh3N span::text").getall()
        for r_text in reviews_text:
            rev_match = re.search(r"([\d,]+)\s*Reviews", r_text, re.IGNORECASE)
            if rev_match:
                review_count = int(rev_match.group(1).replace(",", ""))
                break

    # Specs table extraction
    specs: dict[str, str] = {}
    sections: dict[str, dict[str, str]] = {}
    if sel:
        specs, sections = extract_flipkart_specs(sel)

    # Brand and Model
    brand = infer_brand(title, specs.get("brand") or specs.get("model brand"))
    model_name = (
        specs.get("model name")
        or specs.get("model")
        or specs.get("series")
        or specs.get("model number")
        or title
    )

    attributes = build_category_attributes(category, title, specs)
    spec_sections = build_spec_sections(specs, sections)
    if spec_sections:
        attributes["spec_sections"] = spec_sections

    mpn = extract_manufacturer_part_number(title, specs)
    gtin = specs.get("gtin") or specs.get("ean") or specs.get("upc")
    if mpn:
        attributes["mpn"] = mpn
    if gtin:
        attributes["gtin"] = gtin

    return ParsedProduct(
        source="flipkart",
        source_product_id=source_product_id,
        category=category,
        subcategory=subcategory,
        title=title,
        brand=brand,
        model_name=model_name,
        manufacturer_part_number=mpn,
        gtin=gtin,
        price_paise=price_paise,
        mrp_paise=mrp_paise,
        in_stock=in_stock,
        seller=seller,
        rating=rating,
        review_count=review_count,
        source_url=source_url,
        image_urls=image_urls(
            [
                json_ld_data.get("image"),
                payload.get("image_urls"),
                payload.get("image_url"),
                *(
                    sel.css(
                        "div._1BweB8 img::attr(src), "
                        "img.DByuf4::attr(src), "
                        "img._396cs4::attr(src), "
                        'meta[property="og:image"]::attr(content)'
                    ).getall()
                    if sel
                    else []
                ),
            ],
            source_url,
        ),
        attributes=attributes,
        observed_at=observed_at,
    )


def parse_flipkart_record(record: RawSourceRecord) -> ParsedProduct:
    """Parse a RawSourceRecord using Flipkart extraction rules."""
    return parse_flipkart_payload(
        payload=dict(record.payload),
        source_url=record.source_url,
        source_product_id=record.source_product_id,
        observed_at=record.observed_at,
        category=record.category,
        subcategory=record.subcategory,
    )
