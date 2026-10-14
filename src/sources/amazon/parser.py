"""Amazon India parser extracting structured product metadata and specifications."""

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


def clean_amz_str(val: str | None) -> str:
    """Clean Amazon strings by stripping invisible direction marks and excess spaces."""
    if not val:
        return ""
    cleaned = val.replace("\u200e", "").replace("\u200f", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_amazon_specs(
    sel: Selector,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Extract flat specs dictionary and section-organized specs hierarchy from Amazon HTML."""
    specs: dict[str, str] = {}
    sections: dict[str, dict[str, str]] = {}

    # 1. Expander section tables (#productDetails_expanderSectionTables,
    # depthLeftSections, depthRightSections)
    expander_containers = sel.css(
        "#productDetails_expanderSectionTables .a-section-expander-container, "
        "#prodDetails .a-section-expander-container, "
        "div.a-section-expander-container"
    )
    for container in expander_containers:
        heading_el = (
            container.css(".a-expander-prompt::text").get()
            or container.css(".a-expander-header span::text").get()
            or container.css(".a-expander-header::text").get()
        )
        section_name = clean_amz_str(heading_el)
        if not section_name:
            continue
        section_name_clean = normalize_text(section_name)
        if any(
            ignored in section_name_clean
            for ignored in ("feedback", "customer review", "rating", "lower price")
        ):
            continue

        section_specs: dict[str, str] = {}
        for row in container.css("table.prodDetTable tr, table.a-keyvalue tr"):
            key_el = (
                row.css("th.prodDetSectionEntry::text").get()
                or row.css("th::text").get()
                or row.css("td:first-child::text").get()
            )
            val_el = (
                row.css("td.prodDetAttrValue::text").get()
                or row.css("td.prodDetAttrValue *::text").get()
                or row.css("td:last-child::text").get()
            )
            if not val_el:
                v_pieces = row.css("td.prodDetAttrValue *::text, td:last-child *::text").getall()
                if v_pieces:
                    val_el = " ".join(v_pieces)

            if key_el and val_el:
                key_clean = clean_amz_str(key_el)
                val_clean = clean_amz_str(val_el)
                if not key_clean or not val_clean or is_ignored_spec_key(key_clean):
                    continue
                k_norm = normalize_text(key_clean)
                specs[k_norm] = val_clean
                section_specs[key_clean] = val_clean

        if section_specs:
            sections[section_name] = section_specs

    # 2. Classic product details tables (e.g. #productDetails_techSpec_section_1, #poExpander)
    for row in sel.css(
        "#productDetails_techSpec_section_1 tr, "
        "#productDetails_techSpec_section_2 tr, "
        "#prodDetails table.prodDetTable tr, "
        "#poExpander tr, "
        "#technicalSpecifications_section_1 tr, "
        "#productOverview_feature_div tr"
    ):
        key_el = (
            row.css("th::text").get()
            or row.css("td.a-span3 span::text").get()
            or row.css("td.a-span3::text").get()
            or row.css("td:first-child span::text").get()
            or row.css("td:first-child::text").get()
        )
        val_el = (
            row.css("td::text").get()
            or row.css("td.a-span9 span::text").get()
            or row.css("td.a-span9::text").get()
            or row.css("td:last-child span::text").get()
            or row.css("td:last-child::text").get()
        )
        if not val_el:
            v_pieces = row.css("td.a-span9 *::text, td:last-child *::text").getall()
            if v_pieces:
                val_el = " ".join(v_pieces)

        if key_el and val_el:
            key_clean = clean_amz_str(key_el)
            val_clean = clean_amz_str(val_el)
            if not key_clean or not val_clean or is_ignored_spec_key(key_clean):
                continue
            k_norm = normalize_text(key_clean)
            if k_norm not in specs:
                specs[k_norm] = val_clean

    # 3. Bullet specifications (#detailBullets_feature_div)
    for li in sel.css("#detailBullets_feature_div li span.a-list-item"):
        parts = [clean_amz_str(p) for p in li.css("span::text").getall() if clean_amz_str(p)]
        if len(parts) >= 2:
            key_clean = clean_amz_str(parts[0].replace(":", ""))
            val_clean = clean_amz_str(parts[1])
            if not key_clean or not val_clean or is_ignored_spec_key(key_clean):
                continue
            k_norm = normalize_text(key_clean)
            if k_norm not in specs:
                specs[k_norm] = val_clean

    return specs, sections


def parse_amazon_payload(
    payload: dict[str, object],
    source_url: AnyHttpUrl,
    source_product_id: str,
    observed_at: datetime,
    category: str = "laptop",
    subcategory: str | None = None,
) -> ParsedProduct:
    """Parse raw Amazon India payload or HTML string into normalized ParsedProduct."""
    html = str(payload.get("html", ""))
    sel = Selector(text=html) if html else None

    # Check JSON-LD if present
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

    # Extract Specifications and Sections
    specs, sections = extract_amazon_specs(sel) if sel else ({}, {})

    # Title
    title = ""
    if sel:
        title = (
            sel.css("input#productTitle::attr(value)").get()
            or sel.css("input[name='productTitle']::attr(value)").get()
            or sel.css("span#productTitle::text").get()
            or sel.css("h1#title::text").get()
            or sel.css("meta[name='title']::attr(content)").get()
            or sel.css("meta[property='og:title']::attr(content)").get()
            or sel.css("h1::text").get()
            or ""
        ).strip()
    if not title and json_ld_data.get("name"):
        title = str(json_ld_data["name"]).strip()
    if not title and sel:
        raw_page_title = sel.css("title::text").get()
        if raw_page_title and "page not found" not in raw_page_title.casefold():
            title = raw_page_title.split(":")[0].split("|")[0].strip()
    if not title and (specs.get("brand name") or specs.get("brand") or specs.get("model name")):
        b = specs.get("brand name") or specs.get("brand", "")
        m = specs.get("model name") or specs.get("model number", "")
        title = f"{b} {m}".strip()
    if not title:
        title = str(payload.get("title", f"Amazon Product {source_product_id}"))

    # Prices
    price_paise: int | None = None
    mrp_paise: int | None = None

    if sel:
        price_input = (
            sel.css("input#priceValue::attr(value)").get()
            or sel.css("input[name='priceValue']::attr(value)").get()
        )
        if price_input:
            price_paise = extract_digits_to_paise(price_input)

        if price_paise is None:
            offscreen_prices = [
                p.strip()
                for p in sel.css("span.a-price span.a-offscreen::text").getall()
                if p.strip() and "₹" in p
            ]
            whole_price = (
                sel.css("span.priceToPay span.a-price-whole::text").get()
                or sel.css("span.a-price span.a-price-whole::text").get()
                or sel.css(
                    "div#corePriceDisplay_desktop_feature_div span.a-price-whole::text"
                ).get()
            )
            price_text = (
                (offscreen_prices[0] if offscreen_prices else None)
                or whole_price
                or sel.css("span.priceToPay span.a-offscreen::text").get()
                or sel.css("span.apexPriceToPay span.a-offscreen::text").get()
                or sel.css("div#corePrice_desktop span.a-offscreen::text").get()
            )
            price_paise = extract_digits_to_paise(price_text)

        mrp_text = (
            sel.css("span.basisPrice span.a-offscreen::text").get()
            or sel.css("span.a-text-price span.a-offscreen::text").get()
            or sel.css("span.a-price[data-a-strike='true'] span.a-offscreen::text").get()
            or sel.css(
                "div#corePriceDisplay_desktop_feature_div "
                "span.a-price.a-text-price span.a-offscreen::text"
            ).get()
            or sel.css("div#corePrice_desktop span.a-text-price span.a-offscreen::text").get()
        )
        mrp_paise = extract_digits_to_paise(mrp_text)

        if price_paise is None and html:
            price_match = re.search(r"₹\s*([\d,]+(?:\.\d{2})?)", html)
            if price_match:
                price_paise = extract_digits_to_paise(price_match.group(1))

        if mrp_paise is None and html:
            mrp_match = re.search(
                r"(?:M\.R\.P\.|MRP)[^\d₹]*₹?\s*([\d,]+(?:\.\d{2})?)", html, re.IGNORECASE
            )
            if mrp_match:
                mrp_paise = extract_digits_to_paise(mrp_match.group(1))

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
        avail_text = sel.css("div#availability span::text").get() or ""
        if (
            "currently unavailable" in avail_text.casefold()
            or "out of stock" in avail_text.casefold()
        ):
            in_stock = False

    # Seller
    seller: str | None = None
    if sel:
        seller_raw = (
            sel.css("a#sellerProfileTriggerId::text").get()
            or sel.css("div#merchant-info a span::text").get()
            or sel.css("div#merchant-info::text").get()
            or sel.css("#tabular-buybox-truncate-0 span::text").get()
            or sel.css("div.tabular-buybox-text[tabular-attribute-name='Sold by'] span::text").get()
        )
        if seller_raw:
            seller = seller_raw.strip()
    if not seller and payload.get("seller"):
        seller = str(payload["seller"])

    # Rating & Reviews
    rating: float | None = None
    review_count: int | None = None
    if sel:
        rating_text = (
            sel.css("#averageCustomerReviews span.a-size-small.a-color-base::text").get()
            or sel.css("span.a-icon-alt::text, i.a-icon-star span::text").get()
        )
        if rating_text:
            match = re.search(r"(\d+(?:\.\d+)?)", rating_text)
            if match:
                with contextlib.suppress(ValueError):
                    rating = float(match.group(1))
        rev_text = (
            sel.css("span#acrCustomerReviewText::text").get()
            or sel.css("#averageCustomerReviews a[href*='customerReviews'] span::text").get()
        )
        if rev_text:
            rev_match = re.search(r"([\d,]+)", rev_text)
            if rev_match:
                review_count = int(rev_match.group(1).replace(",", ""))

    brand = clean_amz_str(
        infer_brand(
            title,
            clean_amz_str(
                specs.get("brand")
                or specs.get("brand name")
                or specs.get("manufacturer")
                or specs.get("manufacturer name")
            ),
        )
    )
    title = clean_amz_str(title)
    model_name = clean_amz_str(
        specs.get("model name") or specs.get("model") or specs.get("series") or title
    )

    attributes = build_category_attributes(category, title, specs)
    spec_sections = build_spec_sections(specs, sections)
    if spec_sections:
        attributes["spec_sections"] = spec_sections

    # Identifiers & Warranty
    asin_val = (
        (
            sel.css("input#asin::attr(value)").get()
            or sel.css("input[name='asin']::attr(value)").get()
        )
        if sel
        else None
    ) or specs.get("asin")
    if asin_val:
        attributes["asin"] = clean_amz_str(str(asin_val))

    mpn = extract_manufacturer_part_number(title, specs)
    clean_m = clean_amz_str(str(mpn)) if mpn else None
    if clean_m:
        attributes["mpn"] = clean_m
        attributes["model_number"] = clean_m
    gtin_raw = specs.get("gtin") or specs.get("ean") or specs.get("upc")
    gtin = clean_amz_str(str(gtin_raw)) if gtin_raw else None
    if gtin:
        attributes["gtin"] = gtin

    warranty = (
        specs.get("warranty description") or specs.get("warranty") or specs.get("warranty type")
    )
    if warranty:
        attributes["warranty"] = clean_amz_str(str(warranty))

    return ParsedProduct(
        source="amazon",
        source_product_id=source_product_id,
        category=category,
        subcategory=subcategory,
        title=title,
        brand=brand,
        model_name=model_name,
        manufacturer_part_number=clean_m,
        gtin=gtin,
        price_paise=price_paise,
        mrp_paise=mrp_paise,
        in_stock=in_stock,
        seller=clean_amz_str(seller) if seller else None,
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
                        "img#landingImage::attr(data-old-hires), "
                        "img#landingImage::attr(src), "
                        "#imgTagWrapperId img::attr(src), "
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


def parse_amazon_record(record: RawSourceRecord) -> ParsedProduct:
    """Parse a RawSourceRecord using Amazon extraction rules."""
    return parse_amazon_payload(
        payload=dict(record.payload),
        source_url=record.source_url,
        source_product_id=record.source_product_id,
        observed_at=record.observed_at,
        category=record.category,
        subcategory=record.subcategory,
    )
