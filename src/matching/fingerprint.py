"""Canonical product identity fingerprint."""

from __future__ import annotations

from typing import ClassVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from categories.contracts import AttributeValue
from categories.registry import get_category


class ProductFingerprint(BaseModel):
    """Canonical representation of a product variant for identity resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    _ATTRIBUTE_FIELDS: ClassVar[dict[str, str]] = {
        "cpu_model": "chip",
        "chipset": "chip",
        "gpu_model": "gpu_model",
        "ram_gb": "ram_gb",
        "storage_gb": "storage_gb",
        "storage_type": "storage_type",
        "screen_size_inches": "screen_size_inches",
        "generation": "generation",
        "mpn": "manufacturer_part_number",
        "gtin": "gtin",
    }

    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    brand: str = Field(min_length=1)
    family: str | None = None
    model_name: str = Field(min_length=1)
    generation: str | None = None
    chip: str | None = None
    ram_gb: int | None = None
    storage_gb: int | None = None
    storage_type: str | None = None
    screen_size_inches: float | None = None
    gpu_model: str | None = None
    manufacturer_part_number: str | None = None
    gtin: str | None = None
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)

    @field_validator(
        "brand",
        "family",
        "model_name",
        "generation",
        "chip",
        "storage_type",
        "gpu_model",
        "manufacturer_part_number",
        "gtin",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | None) -> str | None:
        return " ".join(value.casefold().strip().split()) if value else None

    @field_validator("category")
    @classmethod
    def require_supported_category(cls, value: str) -> str:
        return get_category(value).code

    def value_for(self, key: str) -> AttributeValue | None:
        """Read a canonical top-level field, falling back to supplemental attributes."""
        normalized_key = key.casefold().strip()
        field_name = self._ATTRIBUTE_FIELDS.get(normalized_key)
        if field_name:
            value = cast("AttributeValue | None", getattr(self, field_name))
            if value is not None:
                return value
        return self.attributes.get(normalized_key)

    @field_validator("attributes")
    @classmethod
    def normalize_attributes(cls, values: dict[str, AttributeValue]) -> dict[str, AttributeValue]:
        normalized: dict[str, AttributeValue] = {}
        for key, value in values.items():
            normalized_key = key.casefold().strip()
            if not normalized_key:
                raise ValueError("attribute names cannot be blank")
            normalized[normalized_key] = (
                " ".join(value.casefold().strip().split()) if isinstance(value, str) else value
            )
        return normalized
