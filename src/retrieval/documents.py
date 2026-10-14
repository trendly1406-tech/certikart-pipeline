"""Build stable, auditable product text for semantic indexing."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from storage.models import ProductModel

DOCUMENT_VERSION = "product-document-1.0"
SEMANTIC_ATTRIBUTE_LABELS = {
    "operating_system": "Operating system",
    "display_type": "Display type",
    "display_resolution": "Display resolution",
    "refresh_rate_hz": "Refresh rate",
    "battery_wh": "Battery capacity",
    "battery_mah": "Battery capacity",
    "weight_kg": "Weight",
    "weight_grams": "Weight",
    "primary_camera_mp": "Primary camera",
    "network_types": "Network",
    "nfc": "NFC",
    "esim": "eSIM",
}


class CanonicalProductDocument(BaseModel):
    canonical_product_id: str
    category: str
    document_text: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    document_version: str = DOCUMENT_VERSION
    source_fields: dict[str, str] = Field(default_factory=dict)


def _render_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, list):
        rendered = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(rendered) if rendered else None
    return None


def build_canonical_product_document(
    product: ProductModel,
) -> CanonicalProductDocument:
    fields: list[tuple[str, str, Any]] = [
        ("Product", "model_name", f"{product.brand} {product.model_name}".strip()),
        ("Category", "category", product.category),
        ("Family", "family", product.family),
        ("Processor", "chip", product.chip),
        ("RAM (GB)", "ram_gb", product.ram_gb),
        ("Storage (GB)", "storage_gb", product.storage_gb),
        ("Screen size (inches)", "screen_size_inches", product.screen_size_inches),
        ("GPU", "gpu_model", product.gpu_model),
    ]
    attributes = product.attributes or {}
    for key, label in SEMANTIC_ATTRIBUTE_LABELS.items():
        fields.append((label, f"attributes.{key}", attributes.get(key)))

    lines: list[str] = []
    source_fields: dict[str, str] = {}
    for label, source_key, raw_value in fields:
        value = _render_value(raw_value)
        if value is None:
            continue
        lines.append(f"{label}: {value}")
        source_fields[label] = source_key

    document_text = "\n".join(lines)
    hash_payload = {
        "version": DOCUMENT_VERSION,
        "product_id": product.id,
        "text": document_text,
        "source_fields": source_fields,
    }
    content_hash = hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CanonicalProductDocument(
        canonical_product_id=product.id,
        category=product.category,
        document_text=document_text,
        content_hash=content_hash,
        source_fields=source_fields,
    )
