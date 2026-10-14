"""Croma parser extracting structured product metadata and specifications."""

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


def extract_croma_specs(
    sel: Selector,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Extract flat specs dictionary and section-organized specs hierarchy from Croma HTML."""
    specs: dict[str, str] = {}
    sections: dict[str, dict[str, str]] = {}

    # 1. Accordion Specification Containers (.cp-specification, .cp-specification-info)
    spec_info_blocks = sel.css(
        "ul.cp-specification-info, "
        "div.cp-specification ul.cp-specification-info, "
        "#specification_container ul.cp-specification-info"
    )
    for block in spec_info_blocks:
        heading_el = (
            block.css("li h3.title::text").get()
            or block.css("h3.title::text").get()
            or block.css(".title::text").get()
            or block.css("h3::text").get()
        )
        section_name = heading_el.strip() if heading_el else ""
        if not section_name:
            continue
        section_name_clean = normalize_text(section_name)
        if any(
            ignored in section_name_clean
            for ignored in (
                "company contact",
                "service promise",
                "packaged dimensions",
                "croma service",
            )
        ):
            continue
        section_specs: dict[str, str] = {}

        for title_el in block.css(".cp-specification-spec-title"):
            key_text = (
                title_el.css("h4::text").get() or title_el.css("::text").get() or ""
            ).strip()
            if is_ignored_spec_key(key_text):
                continue
            v_pieces = title_el.xpath(
                "following-sibling::*[contains(@class, 'cp-specification-spec-details')][1]//text()"
            ).getall()
            if not v_pieces:
                v_pieces = title_el.xpath(
                    "../li[contains(@class, 'cp-specification-spec-details')]//text()"
                ).getall()
            val_text = re.sub(r"\s+", " ", " ".join(v_pieces)).strip()

            if key_text and val_text:
                k_clean = normalize_text(key_text)
                specs[k_clean] = val_text
                section_specs[key_text] = val_text

        if section_specs:
            sections[section_name] = section_specs

    # 2. Standalone specification rows if any were outside the blocks
    for spec_row in sel.css("ul.cp-specification-spec-info"):
        key_el = (
            spec_row.css("li.cp-specification-spec-title h4::text").get()
            or spec_row.css("li.cp-specification-spec-title::text").get()
            or spec_row.css(".cp-specification-spec-title h4::text").get()
            or spec_row.css(".cp-specification-spec-title::text").get()
        )
        if key_el and is_ignored_spec_key(key_el):
            continue
        details_el = spec_row.css(
            "li.cp-specification-spec-details, .cp-specification-spec-details"
        )
        val_text = " ".join(details_el.xpath(".//text()").getall()).strip()
        val_text = re.sub(r"\s+", " ", val_text)
        if key_el and val_text:
            k_clean = normalize_text(key_el)
            if k_clean not in specs:
                specs[k_clean] = val_text

    # 3. Key Features bullet points (#panel5, .cp-keyfeature)
    key_features = sel.css("div.cp-keyfeature ul li, .cp-keyfeature li, #panel5 li")
    kf_dict: dict[str, str] = {}
    for idx, li in enumerate(key_features, 1):
        text = " ".join(li.xpath(".//text()").getall()).strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            continue
        if ":" in text:
            k, v = text.split(":", 1)
            if is_ignored_spec_key(k):
                continue
            k_clean = normalize_text(k)
            if k_clean not in specs:
                specs[k_clean] = v.strip()
            kf_dict[k.strip()] = v.strip()
        else:
            kf_dict[f"Feature {idx}"] = text
    if kf_dict:
        sections["Key Features"] = kf_dict

    # 4. Overview Section (#panel1, .cp-overview)
    overview_el = sel.css("div.cp-overview, #panel1 .cp-overview")
    if overview_el:
        overview_text = " ".join(overview_el.xpath(".//text()").getall()).strip()
        overview_text = re.sub(r"\s+", " ", overview_text)
        if overview_text:
            sections["Overview"] = {"Description": overview_text}

    # 5. Legacy / Alternative Croma table structure (e.g. .spec-body tr, .technical-details tr)
    for item in sel.css(
        ".cp-specification li, .spec-body tr, .technical-details tr, table.specifications tr"
    ):
        key_el = item.css(".spec-title::text, td:first-child::text, .key::text").get()
        val_el = item.css(".spec-desc::text, td:last-child::text, .value::text").get()
        if key_el and val_el and not is_ignored_spec_key(key_el):
            k_clean = normalize_text(key_el)
            if k_clean not in specs:
                specs[k_clean] = val_el.strip()

    return specs, sections


def parse_croma_payload(
    payload: dict[str, object],
    source_url: AnyHttpUrl,
    source_product_id: str,
    observed_at: datetime,
    category: str = "laptop",
    subcategory: str | None = None,
) -> ParsedProduct:
    """Parse raw Croma payload or HTML string into normalized ParsedProduct."""
    html = str(payload.get("html", ""))
    sel = Selector(text=html) if html else None

    # Check JSON-LD in HTML
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

    # Try parsing window.__INITIAL_DATA__ SSR state
    initial_data_product: dict[str, object] = {}
    specs_from_classifications: dict[str, str] = {}
    if html:
        idx = html.find("window.__INITIAL_DATA__")
        if idx != -1:
            sub = html[idx:]
            brace_idx = sub.find("{")
            if brace_idx != -1:
                clean_js = re.sub(r":\s*undefined\b", ":null", sub[brace_idx:])
                semicolon_idx = clean_js.find("</script>")
                if semicolon_idx != -1:
                    clean_js = clean_js[:semicolon_idx].strip()
                    if clean_js.endswith(";"):
                        clean_js = clean_js[:-1].strip()
                try:
                    loaded_state = json.loads(clean_js)
                    if isinstance(loaded_state, dict):
                        pdp_data = loaded_state.get("pdpReducer", {}).get("pdpData", {})
                        if isinstance(pdp_data, dict):
                            initial_data_product = pdp_data
                except json.JSONDecodeError:
                    match = re.search(r"window\.__INITIAL_DATA__\s*=\s*(\{.*?\});", html, re.DOTALL)
                    if match:
                        with contextlib.suppress(json.JSONDecodeError):
                            raw_dict = json.loads(match.group(1))
                            initial_data_product = raw_dict.get("pdpReducer", {}).get("pdpData", {})

    # Extract specs from classifications array if available in INITIAL_DATA
    if initial_data_product and "classifications" in initial_data_product:
        classifications = initial_data_product["classifications"]
        if isinstance(classifications, list):
            for group in classifications:
                if isinstance(group, dict) and "features" in group:
                    for feat in group.get("features", []):
                        if isinstance(feat, dict) and "name" in feat:
                            feature_name = str(feat["name"])
                            feature_vals = feat.get("featureValues", [])
                            val_str = ""
                            if feature_vals and isinstance(feature_vals, list):
                                val_str = ", ".join(
                                    str(fv.get("value", ""))
                                    for fv in feature_vals
                                    if isinstance(fv, dict) and fv.get("value")
                                )
                            elif "value" in feat:
                                val_str = str(feat["value"])
                            if feature_name and val_str:
                                specs_from_classifications[normalize_text(feature_name)] = (
                                    val_str.strip()
                                )

    # Direct payload dictionary support (e.g. from tests or API)
    if not initial_data_product and payload.get("data"):
        raw_data = payload["data"]
        if isinstance(raw_data, dict):
            initial_data_product = raw_data

    # Extract DOM specs and sections
    specs_from_dom, sections = extract_croma_specs(sel) if sel else ({}, {})
    specs: dict[str, str] = dict(specs_from_classifications)
    for k, v in specs_from_dom.items():
        specs[k] = v

    # Title
    title = ""
    if initial_data_product.get("name"):
        title = str(initial_data_product["name"]).strip()
    elif initial_data_product.get("productName"):
        title = str(initial_data_product["productName"]).strip()

    if not title and sel:
        title = (
            sel.css("h1.pd-title::text").get()
            or sel.css("h1.pdp-title::text").get()
            or sel.css("h1.prod-title::text").get()
            or sel.css("h1::text").get()
            or sel.css("meta[property='og:title']::attr(content)").get()
            or ""
        ).strip()

    if not title and json_ld_data.get("name"):
        title = str(json_ld_data["name"]).strip()

    if not title and sel:
        raw_page_title = sel.css("title::text").get()
        if raw_page_title and "croma" in raw_page_title.casefold():
            title = (
                raw_page_title.split("|")[0]
                .split("-")[0]
                .replace("Buy", "")
                .replace("Online", "")
                .strip()
            )

    if not title and (specs.get("brand") or specs.get("model series") or specs.get("model number")):
        b = specs.get("brand", "")
        m = specs.get("model series") or specs.get("model number", "")
        title = f"{b} {m}".strip()

    if not title:
        title = str(payload.get("title", f"Croma Product {source_product_id}"))

    if title:
        if title.startswith("Buy "):
            title = title[4:].strip()
        if " Online - Croma" in title:
            title = title.replace(" Online - Croma", "").strip()
        if " - Croma" in title:
            title = title.replace(" - Croma", "").strip()
        if " | Croma" in title:
            title = title.replace(" | Croma", "").strip()

    # Prices
    price_paise: int | None = None
    mrp_paise: int | None = None

    if initial_data_product.get("price"):
        price_obj = initial_data_product["price"]
        if isinstance(price_obj, dict):
            if "value" in price_obj:
                with contextlib.suppress(ValueError):
                    price_paise = int(float(str(price_obj["value"])) * 100)
            elif "formattedValue" in price_obj:
                price_paise = extract_digits_to_paise(str(price_obj["formattedValue"]))
        elif isinstance(price_obj, (int, float, str)):
            price_paise = extract_digits_to_paise(str(price_obj))

    if initial_data_product.get("mrp"):
        mrp_obj = initial_data_product["mrp"]
        if isinstance(mrp_obj, dict):
            if "value" in mrp_obj:
                with contextlib.suppress(ValueError):
                    mrp_paise = int(float(str(mrp_obj["value"])) * 100)
            elif "formattedValue" in mrp_obj:
                mrp_paise = extract_digits_to_paise(str(mrp_obj["formattedValue"]))
        elif isinstance(mrp_obj, (int, float, str)):
            mrp_paise = extract_digits_to_paise(str(mrp_obj))

    if price_paise is None and sel:
        price_text = (
            sel.css("span.amount::text").get()
            or sel.css("span.pdp-price::text").get()
            or sel.css("span.main-product-price::text").get()
            or sel.css("span.new-price::text").get()
        )
        price_paise = extract_digits_to_paise(price_text)
        mrp_text = (
            sel.css("span.old-price::text").get()
            or sel.css("span.mrp::text").get()
            or sel.css("span.strike-price::text").get()
        )
        mrp_paise = extract_digits_to_paise(mrp_text)

    if price_paise is None and "offers" in json_ld_data:
        offers = json_ld_data["offers"]
        if isinstance(offers, dict) and "price" in offers:
            price_paise = extract_digits_to_paise(str(offers["price"]))

    if price_paise is None and source_url.query:
        import urllib.parse

        q_params = urllib.parse.parse_qs(str(source_url.query))
        if "_price" in q_params:
            with contextlib.suppress(ValueError):
                price_paise = int(q_params["_price"][0])
        if "_mrp" in q_params:
            with contextlib.suppress(ValueError):
                mrp_paise = int(q_params["_mrp"][0])

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
    if initial_data_product.get("stock"):
        st = str(initial_data_product["stock"]).casefold()
        if "out" in st or "false" in st:
            in_stock = False
    elif sel:
        out_of_stock = (
            sel.css(".out-of-stock::text, .pdp-out-of-stock::text, button.btn-sold-out::text").get()
            or ""
        )
        if (
            "out of stock" in out_of_stock.casefold()
            or "currently unavailable" in out_of_stock.casefold()
        ):
            in_stock = False

    # Seller
    seller = "Croma"
    if payload.get("seller"):
        seller = str(payload["seller"])

    # Rating & Reviews
    rating: float | None = None
    review_count: int | None = None
    if initial_data_product.get("finalReviewRating"):
        with contextlib.suppress(ValueError):
            rating = float(str(initial_data_product["finalReviewRating"]))
    if initial_data_product.get("finalReviewRatingCount"):
        with contextlib.suppress(ValueError):
            review_count = int(str(initial_data_product["finalReviewRatingCount"]))
    elif initial_data_product.get("numberOfReviews"):
        with contextlib.suppress(ValueError):
            review_count = int(str(initial_data_product["numberOfReviews"]))

    if rating is None and sel:
        rating_text = sel.css("span.rating-text::text, div.cp-rating span::text").get()
        if rating_text:
            with contextlib.suppress(ValueError):
                rating = float(rating_text.strip())
        rev_text = sel.css("span.reviews-count::text, span.total-reviews::text").get()
        if rev_text:
            rev_match = re.search(r"(\d+)", rev_text)
            if rev_match:
                review_count = int(rev_match.group(1))

    brand = infer_brand(
        title,
        str(initial_data_product.get("manufacturer") or "")
        or specs.get("brand")
        or specs.get("manufacturer"),
    )
    model_name = (
        specs.get("model series")
        or specs.get("model number")
        or specs.get("model name")
        or specs.get("model")
        or specs.get("series")
        or initial_data_product.get("summary")
        or title
    )

    attributes = build_category_attributes(category, title, specs)
    spec_sections = build_spec_sections(specs, sections)
    if spec_sections:
        attributes["spec_sections"] = spec_sections

    if "ssd" in title.casefold() or any("ssd" in str(v).casefold() for v in specs.values()):
        attributes["storage_type"] = "SSD"
    elif "emmc" in title.casefold() or any("emmc" in str(v).casefold() for v in specs.values()):
        attributes["storage_type"] = "eMMC"

    mpn = extract_manufacturer_part_number(title, specs) or initial_data_product.get("specialSKU")
    if mpn:
        attributes["mpn"] = str(mpn).strip()

    ean = initial_data_product.get("ean") or specs.get("ean") or specs.get("gtin")
    if ean:
        attributes["ean"] = str(ean).strip()
        attributes["gtin"] = str(ean).strip()

    warranty = (
        specs.get("warranty on main product")
        or specs.get("warranty")
        or specs.get("standard warranty includes")
    )
    if warranty:
        attributes["warranty"] = str(warranty).strip()

    return ParsedProduct(
        source="croma",
        source_product_id=source_product_id,
        category=category,
        subcategory=subcategory,
        title=title,
        brand=brand,
        model_name=str(model_name),
        manufacturer_part_number=str(mpn).strip() if mpn else None,
        gtin=str(ean).strip() if ean else None,
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
                        ".pdp-img img::attr(src), "
                        ".product-image img::attr(src), "
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


def parse_croma_record(record: RawSourceRecord) -> ParsedProduct:
    """Parse a RawSourceRecord using Croma extraction rules."""
    return parse_croma_payload(
        payload=dict(record.payload),
        source_url=record.source_url,
        source_product_id=record.source_product_id,
        observed_at=record.observed_at,
        category=record.category,
        subcategory=record.subcategory,
    )
