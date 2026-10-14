"""Unit tests for shared source extraction and parsing utilities."""

from __future__ import annotations

from sources.common import (
    build_category_attributes,
    build_spec_sections,
    clean_capacity_str,
    extract_digits_to_paise,
    extract_json_ld_products,
    extract_manufacturer_part_number,
    infer_brand,
)


def test_extract_digits_to_paise() -> None:
    assert extract_digits_to_paise("₹39,990.00") == 3999000
    assert extract_digits_to_paise("54,990") == 5499000
    assert extract_digits_to_paise("1,15,990.50") == 11599050
    assert extract_digits_to_paise("") is None
    assert extract_digits_to_paise(None) is None
    assert extract_digits_to_paise("Free / Not Available") is None


def test_extract_manufacturer_part_number_prefers_specs_then_title_code() -> None:
    assert (
        extract_manufacturer_part_number(
            "Apple MacBook Air M5 MDVT4HN/A",
            {"model number": "SPEC-CODE-1"},
        )
        == "SPEC-CODE-1"
    )
    assert (
        extract_manufacturer_part_number("Apple MacBook Air M5 16GB 1TB MDVT4HN/A") == "MDVT4HN/A"
    )
    assert extract_manufacturer_part_number("Apple MacBook Air M5 16GB 1TB") is None


def test_infer_brand() -> None:
    assert infer_brand("ASUS Vivobook 15", None) == "Asus"
    assert infer_brand("Apple MacBook Air M5", None) == "Apple"
    assert infer_brand("HP Pavilion 14", None) == "Hp"
    assert infer_brand("Lenovo IdeaPad Slim 3", None) == "Lenovo"
    assert infer_brand("Unknown Laptop X", "Samsung") == "Samsung"
    assert infer_brand("Custom Device Pro", None) == "Custom"
    assert infer_brand("", None) == "Generic"


def test_clean_capacity_str() -> None:
    assert clean_capacity_str("16GB DDR5") == "16GB"
    assert clean_capacity_str("512 GB SSD") == "512 GB"
    assert clean_capacity_str("1 TB NVMe") == "1 TB"
    assert clean_capacity_str("Non-matching string") == "Non-matching string"


def test_extract_json_ld_products() -> None:
    html = """
    <html>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "ASUS Vivobook 15",
            "offers": {"price": 38990}
        }
        </script>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Product",
                    "name": "Apple MacBook Air M5"
                }
            ]
        }
        </script>
    </html>
    """
    products = extract_json_ld_products(html)
    assert len(products) == 2
    assert products[0]["name"] == "ASUS Vivobook 15"
    assert products[1]["name"] == "Apple MacBook Air M5"


def test_build_category_attributes_laptop() -> None:
    raw_specs = {
        "RAM Size": "16 GB",
        "Hard Drive Size": "512 GB",
        "Processor Brand": "Intel Core i5-13420H",
        "Graphics Card Description": "NVIDIA GeForce RTX 3050",
        "Screen Size": "15.6 inch",
    }
    attrs = build_category_attributes(
        category="laptop",
        title="ASUS TUF Gaming Laptop",
        raw_specs=raw_specs,
    )
    assert attrs["ram_gb"] == 16
    assert attrs["storage_gb"] == 512
    assert attrs["cpu_model"] == "Intel Core i5-13420H"
    assert attrs["gpu_model"] == "NVIDIA GeForce RTX 3050"
    assert attrs["screen_size_inches"] == 15.6
    assert "ram size" not in attrs


def test_build_spec_sections_deduplicates_grouped_raw_specs() -> None:
    sections = build_spec_sections(
        {
            "Screen Size": "15.6 inch",
            "RAM Size": "16 GB",
            "Warranty": "1 Year",
        },
        {"Display": {"Screen Size": "15.6 inch"}},
    )

    assert sections["Display"] == {"Screen Size": "15.6 inch"}
    assert sections["Other Specifications"] == {
        "RAM Size": "16 GB",
        "Warranty": "1 Year",
    }


def test_build_category_attributes_mobile() -> None:
    raw_specs = {
        "Battery Capacity": "5000 mAh",
        "Primary Camera": "50 MP",
        "Front Camera": "16 MP",
        "RAM": "8 GB",
        "Internal Storage": "256 GB",
        "Processor Name": "Snapdragon 7s Gen 2",
        "Display Size": "17.02 cm (6.7 inch)",
        "Refresh Rate": "120 Hz",
        "Fast Charging Capability": "67W",
        "Operating System": "Android 14",
    }
    attrs = build_category_attributes(
        category="mobile",
        title="Realme 12 Pro 5G (Submarine Blue, 8GB RAM, 256GB Storage)",
        raw_specs=raw_specs,
    )
    assert attrs["battery_mah"] == 5000
    assert attrs["primary_camera_mp"] == 50
    assert attrs["front_camera_mp"] == 16
    assert attrs["ram_gb"] == 8
    assert attrs["storage_gb"] == 256
    assert attrs["chipset"] == "Snapdragon 7s Gen 2"
    assert attrs["screen_size_inches"] == 6.7
    assert attrs["refresh_rate_hz"] == 120
    assert attrs["fast_charging_w"] == 67
    assert attrs["operating_system"] == "Android 14"
    assert attrs["network_type"] == "5G"
