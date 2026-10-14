from retrieval.documents import build_canonical_product_document
from storage.models import ProductModel


def _product(**overrides: object) -> ProductModel:
    values = {
        "id": "clus_test",
        "category": "laptop",
        "brand": "Example",
        "model_name": "Model 15",
        "family": "Model",
        "chip": "Core i7",
        "ram_gb": 16,
        "storage_gb": 512,
        "screen_size_inches": 15.6,
        "gpu_model": "RTX 4060",
        "attributes": {
            "operating_system": "Windows 11",
            "refresh_rate_hz": 144,
            "spec_sections": {"ignored": "large retailer archive"},
            "price": 89999,
        },
    }
    values.update(overrides)
    return ProductModel(**values)


def test_document_is_stable_and_excludes_volatile_fields() -> None:
    first = build_canonical_product_document(_product())
    second = build_canonical_product_document(_product())

    assert first.content_hash == second.content_hash
    assert "Windows 11" in first.document_text
    assert "89999" not in first.document_text
    assert "large retailer archive" not in first.document_text
    assert first.source_fields["RAM (GB)"] == "ram_gb"


def test_document_hash_changes_when_semantic_identity_changes() -> None:
    first = build_canonical_product_document(_product())
    changed = build_canonical_product_document(_product(ram_gb=32))

    assert first.content_hash != changed.content_hash
