from datetime import UTC, datetime

from pydantic import AnyHttpUrl

from sources import FetchedSourceDocument, FlipkartSourceAdapter, RawSourceRecord
from sources.flipkart.parser import parse_flipkart_payload


class FakeTransport:
    async def fetch(self, source_url: AnyHttpUrl) -> FetchedSourceDocument:
        return FetchedSourceDocument(
            observed_at=datetime.now(UTC),
            payload={},
            content_hash="0123456789abcdef0123456789abcdef",
        )


def test_flipkart_parser_extracts_all_fields_from_html() -> None:
    html = """
    <html>
        <h1 class="VU-ZEz">Lenovo IdeaPad Slim 3 Laptop (16 GB/512 GB SSD)</h1>
        <div class="Nx9bqj">₹49,990</div>
        <div class="yRaY8j">₹68,990</div>
        <div id="sellerName"><span><span>RetailNet</span></span></div>
        <div class="XQDdHH">4.3</div>
        <span class="Wphh3N"><span>450 Ratings & 45 Reviews</span></span>
        <table class="_14cfVK">
            <tr class="_1s_Smc">
                <td class="_1hKmda">Processor Name</td>
                <td class="_21lJal">Core i5 1235U</td>
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
                <td class="_21lJal">Intel Integrated Iris Xe</td>
            </tr>
        </table>
    </html>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.flipkart.com/lenovo-ideapad-slim-3/p/itm123")

    parsed = parse_flipkart_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="FLIP-001",
        observed_at=now,
        category="laptop",
    )

    assert parsed.source == "flipkart"
    assert parsed.source_product_id == "FLIP-001"
    assert parsed.brand == "Lenovo"
    assert parsed.price_paise == 4999000
    assert parsed.mrp_paise == 6899000
    assert parsed.in_stock is True
    assert parsed.seller == "RetailNet"
    assert parsed.rating == 4.3
    assert parsed.review_count == 45
    assert parsed.attributes["cpu_model"] == "Core i5 1235U"
    assert parsed.attributes["gpu_model"] == "Intel Integrated Iris Xe"
    assert parsed.attributes["ram_gb"] == 16
    assert parsed.attributes["storage_gb"] == 512

    # Verify conversions
    fp = parsed.to_fingerprint()
    assert fp.brand == "lenovo"
    assert fp.ram_gb == 16
    assert "ram_gb" not in fp.attributes

    obs = parsed.to_price_observation()
    assert obs.price_paise == 4999000
    assert obs.mrp_paise == 6899000
    assert obs.in_stock is True


def test_flipkart_parser_detects_out_of_stock() -> None:
    html = """
    <html>
        <h1 class="VU-ZEz">Apple MacBook Air M2 - (8 GB/256 GB SSD)</h1>
        <div class="Nx9bqj">₹89,900</div>
        <div class="_16FRp0"><span>This item is currently out of stock</span></div>
    </html>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.flipkart.com/apple-macbook-air-m2/p/itm456")

    parsed = parse_flipkart_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="FLIP-002",
        observed_at=now,
    )

    assert parsed.brand == "Apple"
    assert parsed.in_stock is False
    assert parsed.price_paise == 8990000


def test_flipkart_parser_promotes_part_number_from_title() -> None:
    parsed = parse_flipkart_payload(
        payload={
            "title": "Apple MacBook Air M5 16 GB 1 TB MDVT4HN/A",
            "price_paise": 21_590_000,
        },
        source_url=AnyHttpUrl("https://www.flipkart.com/apple/p/itmM5AIR001"),
        source_product_id="itmM5AIR001",
        observed_at=datetime.now(UTC),
    )

    assert parsed.manufacturer_part_number == "MDVT4HN/A"
    assert parsed.to_fingerprint().manufacturer_part_number == "mdvt4hn/a"


def test_flipkart_adapter_parse_method() -> None:
    adapter = FlipkartSourceAdapter([], FakeTransport())
    now = datetime.now(UTC)
    record = RawSourceRecord(
        source="flipkart",
        source_product_id="FLIP-003",
        category="laptop",
        source_url=AnyHttpUrl("https://www.flipkart.com/p/itm789"),
        observed_at=now,
        payload={"title": "ASUS TUF Gaming F15", "price_paise": 6500000},
        content_hash="0123456789abcdef0123456789abcdef",
    )

    parsed = adapter.parse(record)
    assert parsed.source == "flipkart"
    assert parsed.brand == "Asus"
    assert parsed.price_paise == 6500000


def test_flipkart_parser_json_ld_and_bullet_points() -> None:
    html = """
    <html>
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "Acer Predator Helios 16",
            "offers": {"price": "149990.00"}
        }
        </script>
        <div class="_2418kt">
            <ul>
                <li class="_21Ahn-">Processor: Intel Core i9-13900HX</li>
                <li class="_21Ahn-">RAM: 32 GB</li>
                <li class="_21Ahn-">Storage: 1 TB SSD</li>
                <li class="_21Ahn-">Graphics: NVIDIA GeForce RTX 4080</li>
            </ul>
        </div>
    </html>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.flipkart.com/acer-predator/p/itm999")

    parsed = parse_flipkart_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="FLIP-JSONLD",
        observed_at=now,
    )

    assert parsed.title == "Acer Predator Helios 16"
    assert parsed.brand == "Acer"
    assert parsed.price_paise == 14999000
    assert parsed.attributes["cpu_model"] == "Intel Core i9-13900HX"
    assert parsed.attributes["ram_gb"] == 32
    assert parsed.attributes["storage_gb"] == 1024
    assert parsed.attributes["gpu_model"] == "NVIDIA GeForce RTX 4080"


def test_flipkart_parser_json_ld_list_and_fallbacks() -> None:
    html = """
    <html>
        <script type="application/ld+json">
        [
            {"@type": "BreadcrumbList"},
            {"@type": "Product", "name": "CustomBrand ProBook", "offers": {"price": "55000"}}
        ]
        </script>
    </html>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.flipkart.com/custom/p/itm111")

    parsed = parse_flipkart_payload(
        payload={"html": html, "seller": "RetailNet", "mrp_paise": 6500000},
        source_url=url,
        source_product_id="FLIP-LIST",
        observed_at=now,
    )

    assert parsed.title == "CustomBrand ProBook"
    assert parsed.brand == "CustomBrand"
    assert parsed.price_paise == 5500000
    assert parsed.mrp_paise == 6500000
    assert parsed.seller == "RetailNet"


def test_flipkart_parser_modern_specifications_tab() -> None:
    html = """
    <div style="height: 100%; width: 100%;">
        <div>
            <h1 class="VU-ZEz">
                HP Pavilion 14 AI PC Intel Core Ultra 7 (16 GB/512 GB) 14-gr1036TU
            </h1>
            <div class="Nx9bqj">₹79,990</div>
            <div class="yRaY8j">₹95,990</div>
            <div id="sellerName"><span><span>OmniTechRetail</span></span></div>
            <div class="XQDdHH">4.6</div>
            <span class="Wphh3N"><span>120 Ratings & 18 Reviews</span></span>
            <div class="r-1udh08x">
                <div class="grid-formation grid-column-1">
                    <div class="_1psv1zeb9 _1psv1ze0 _1psv1ze4i _1psv1ze29">
                        <div class="v1zwn21n v1zwn24 _1psv1ze9x" font="default-fk-font-l">
                            Processor and Memory Features
                        </div>
                        <div class="css-g5y9jx">
                            <div class="grid-formation-dynamic">
                                <div>
                                    <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                        Processor Brand
                                    </div>
                                    <div class="v1zwn21n v1zwn27" font="s">Intel</div>
                                </div>
                            </div>
                            <div class="grid-formation-dynamic">
                                <div>
                                    <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                        Processor Name
                                    </div>
                                    <div class="v1zwn21n v1zwn27" font="s">Core Ultra 7</div>
                                </div>
                            </div>
                            <div class="grid-formation-dynamic">
                                <div>
                                    <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                        Processor Variant
                                    </div>
                                    <div class="v1zwn21n v1zwn27" font="s">155H</div>
                                </div>
                            </div>
                            <div class="grid-formation-dynamic">
                                <div>
                                    <div class="v1zwn21o v1zwn28" font="default-fk-font-m">RAM</div>
                                    <div class="v1zwn21n v1zwn27" font="s">16 GB</div>
                                </div>
                            </div>
                            <div class="grid-formation-dynamic">
                                <div>
                                    <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                        RAM Type
                                    </div>
                                    <div class="v1zwn21n v1zwn27" font="s">DDR5</div>
                                </div>
                            </div>
                            <div class="grid-formation-dynamic">
                                <div>
                                    <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                        SSD Capacity
                                    </div>
                                    <div class="v1zwn21n v1zwn27" font="s">512 GB</div>
                                </div>
                            </div>
                            <div class="grid-formation-dynamic">
                                <div>
                                    <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                        Graphic Processor
                                    </div>
                                    <div class="v1zwn21n v1zwn27" font="s">
                                        Intel Integrated Arc
                                    </div>
                                </div>
                            </div>
                            <div class="grid-formation-dynamic">
                                <div>
                                    <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                        Clock Speed
                                    </div>
                                    <div class="v1zwn21n v1zwn27" font="s">up to 4.8 GHz</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="grid-formation grid-column-1">
                        <div class="_1psv1zeb9 _1psv1ze0 _1psv1ze4i _1psv1ze29">
                            <div class="v1zwn21n v1zwn24 _1psv1ze9x" font="default-fk-font-l">
                                General
                            </div>
                            <div class="css-g5y9jx">
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Brand
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">HP</div>
                                    </div>
                                </div>
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Model Number
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            14-ep1151TU/14-gr1036TU/14-ep1151TU
                                        </div>
                                    </div>
                                </div>
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Part Number
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            C33RKPA#ACJ / C72QHPA#ACJ
                                        </div>
                                    </div>
                                </div>
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Model Name
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">14-gr1036TU</div>
                                    </div>
                                </div>
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Color
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            Natural Silver
                                        </div>
                                    </div>
                                </div>
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Battery Cell
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            3-cell, 41 Wh Li-ion polymer
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="grid-formation grid-column-1">
                        <div class="_1psv1zeb9 _1psv1ze0 _1psv1ze4i _1psv1ze29">
                            <div class="v1zwn21n v1zwn24 _1psv1ze9x" font="default-fk-font-l">
                                Display and Audio Features
                            </div>
                            <div class="css-g5y9jx">
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Screen Size
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            35.56 cm (14 Inch)
                                        </div>
                                    </div>
                                </div>
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Screen Resolution
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            1920 x 1080 Pixel
                                        </div>
                                    </div>
                                </div>
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Screen Type
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            FHD (1920 x 1080), micro-edge, 250 nits, 62.5% sRGB
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="grid-formation grid-column-1">
                        <div class="_1psv1zeb9 _1psv1ze0 _1psv1ze4i _1psv1ze29">
                            <div class="v1zwn21n v1zwn24 _1psv1ze9x" font="default-fk-font-l">
                                Dimensions
                            </div>
                            <div class="css-g5y9jx">
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Weight
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">1.4 kg kg</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="grid-formation grid-column-1">
                        <div class="_1psv1zeb9 _1psv1ze0 _1psv1ze4i _1psv1ze29">
                            <div class="v1zwn21n v1zwn24 _1psv1ze9x" font="default-fk-font-l">
                                Additional Features
                            </div>
                            <div class="css-g5y9jx">
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Backlit Keyboard
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">Yes</div>
                                    </div>
                                </div>
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Web Camera
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            HP True Vision 1080p FHD camera
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="grid-formation grid-column-1">
                        <div class="_1psv1zeb9 _1psv1ze0 _1psv1ze4i _1psv1ze29">
                            <div class="v1zwn21n v1zwn24 _1psv1ze9x" font="default-fk-font-l">
                                Connectivity Features
                            </div>
                            <div class="css-g5y9jx">
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Wireless LAN
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            Realtek Wi-Fi 6 (2x2)
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="grid-formation grid-column-1">
                        <div class="_1psv1zeb9 _1psv1ze0 _1psv1ze4i _1psv1ze29">
                            <div class="v1zwn21n v1zwn24 _1psv1ze9x" font="default-fk-font-l">
                                Operating System
                            </div>
                            <div class="css-g5y9jx">
                                <div class="grid-formation-dynamic">
                                    <div>
                                        <div class="v1zwn21o v1zwn28" font="default-fk-font-m">
                                            Operating System
                                        </div>
                                        <div class="v1zwn21n v1zwn27" font="s">
                                            Windows 11 Home
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <!-- Warranty tab -->
            <div hidden="">
                <div class="css-g5y9jx" style="padding: 16px;">
                    <div>
                        <div class="v1zwn24 _1psv1zeb9 _1psv1ze0">Warranty Summary</div>
                        <div class="v1zwn26 _1psv1zeb9 _1psv1ze0">1 Year Onsite Warranty</div>
                        <div class="v1zwn24 _1psv1zeb9 _1psv1ze0">Covered in Warranty</div>
                        <div class="v1zwn26 _1psv1zeb9 _1psv1ze0">Manufacturing Defects Only</div>
                    </div>
                </div>
            </div>
            <!-- Manufacturer info tab -->
            <div hidden="">
                <div class="_1psv1zeb9 _1psv1ze0">
                    <div class="_1psv1zeb9 _1psv1ze0">
                        <div class="v1zwn21o v1zwn28 _1psv1zeb9 _1psv1ze0">Generic Name</div>
                        <div class="v1zwn21n v1zwn26 _1psv1zeb9 _1psv1ze0">Computers</div>
                    </div>
                    <div class="_1psv1zeb9 _1psv1ze0">
                        <div class="v1zwn21o v1zwn28 _1psv1zeb9 _1psv1ze0">Country of Origin</div>
                        <div class="v1zwn21n v1zwn26 _1psv1zeb9 _1psv1ze0">India</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.flipkart.com/hp-pavilion-14/p/itmhp123")

    parsed = parse_flipkart_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="FLIP-HP14",
        observed_at=now,
        category="laptop",
    )

    assert parsed.source == "flipkart"
    assert parsed.brand == "Hp"
    assert parsed.price_paise == 7999000
    assert parsed.mrp_paise == 9599000
    assert parsed.seller == "OmniTechRetail"
    assert parsed.rating == 4.6
    assert parsed.review_count == 18

    # Canonical attributes stay at the root; retailer fields live once in sections.
    raw_sections = parsed.attributes["spec_sections"]
    assert isinstance(raw_sections, dict)
    section_specs = {
        key.casefold(): value for values in raw_sections.values() for key, value in values.items()
    }
    assert section_specs["processor brand"] == "Intel"
    assert section_specs["processor name"] == "Core Ultra 7"
    assert section_specs["processor variant"] == "155H"
    assert parsed.attributes["cpu_model"] == "Intel Core Ultra 7 155H"

    assert section_specs["ram"] == "16 GB"
    assert parsed.attributes["ram_gb"] == 16
    assert parsed.attributes["ram_type"] == "DDR5"

    assert section_specs["ssd capacity"] == "512 GB"
    assert parsed.attributes["storage_gb"] == 512

    assert section_specs["graphic processor"] == "Intel Integrated Arc"
    assert parsed.attributes["gpu_model"] == "Intel Integrated Arc"

    assert section_specs["screen size"] == "35.56 cm (14 Inch)"
    assert parsed.attributes["screen_size_inches"] == 14.0

    assert section_specs["backlit keyboard"] == "Yes"
    assert parsed.attributes["keyboard_backlight"] is True

    assert parsed.attributes["webcam_resolution"] == "1080p FHD"
    assert parsed.attributes["weight_kg"] == 1.4
    assert parsed.attributes["battery_wh"] == 41.0
    assert parsed.attributes["wifi_standard"] == "Wi-Fi 6"
    assert parsed.attributes["operating_system"] == "Windows 11 Home"
    assert parsed.attributes["display_resolution"] == "FHD"

    # Check Warranty and Manufacturer specs
    assert section_specs["warranty summary"] == "1 Year Onsite Warranty"
    assert section_specs["covered in warranty"] == "Manufacturing Defects Only"
    assert section_specs["generic name"] == "Computers"
    assert section_specs["country of origin"] == "India"
    assert "processor brand" not in parsed.attributes
    assert "screen size" not in parsed.attributes

    # Check fingerprint creation
    fp = parsed.to_fingerprint()
    assert fp.brand == "hp"
    assert fp.chip == "intel core ultra 7 155h"
    assert fp.ram_gb == 16
    assert fp.storage_gb == 512
    assert fp.screen_size_inches == 14.0
    assert "cpu_model" not in fp.attributes
    assert "ram_gb" not in fp.attributes
