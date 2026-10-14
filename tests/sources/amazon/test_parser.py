from datetime import UTC, datetime

from pydantic import AnyHttpUrl

from sources import AmazonSourceAdapter, FetchedSourceDocument, RawSourceRecord
from sources.amazon.parser import parse_amazon_payload


class FakeTransport:
    async def fetch(self, source_url: AnyHttpUrl) -> FetchedSourceDocument:
        return FetchedSourceDocument(
            observed_at=datetime.now(UTC),
            payload={},
            content_hash="0123456789abcdef0123456789abcdef",
        )


def test_amazon_parser_extracts_all_fields_from_html() -> None:
    html = """
    <html>
        <span id="productTitle">ASUS Vivobook 15 Intel Core i3 (8 GB RAM/512GB SSD)</span>
        <span class="a-price"><span class="a-offscreen">₹38,990.00</span></span>
        <span class="a-text-price"><span class="a-offscreen">₹52,990.00</span></span>
        <div id="merchant-info">Sold by Appario Retail Private Ltd</div>
        <span class="a-icon-alt">4.1 out of 5 stars</span>
        <span id="acrCustomerReviewText">890 ratings</span>
        <table id="productDetails_techSpec_section_1">
            <tr><th>Processor Brand</th><td>Intel Core i3-1215U</td></tr>
            <tr><th>RAM Size</th><td>8 GB</td></tr>
            <tr><th>Hard Drive Size</th><td>512 GB</td></tr>
            <tr><th>Graphics Coprocessor</th><td>Intel UHD Graphics</td></tr>
        </table>
    </html>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.amazon.in/dp/B0CX98765")

    parsed = parse_amazon_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="B0CX98765",
        observed_at=now,
        category="laptop",
    )

    assert parsed.source == "amazon"
    assert parsed.source_product_id == "B0CX98765"
    assert parsed.brand == "Asus"
    assert parsed.price_paise == 3899000
    assert parsed.mrp_paise == 5299000
    assert parsed.in_stock is True
    assert parsed.seller == "Sold by Appario Retail Private Ltd"
    assert parsed.rating == 4.1
    assert parsed.review_count == 890
    assert parsed.attributes["cpu_model"] == "Intel Core i3-1215U"
    assert parsed.attributes["gpu_model"] == "Intel UHD Graphics"
    assert parsed.attributes["ram_gb"] == 8
    assert parsed.attributes["storage_gb"] == 512

    fp = parsed.to_fingerprint()
    assert fp.brand == "asus"
    assert fp.ram_gb == 8
    assert "ram_gb" not in fp.attributes

    obs = parsed.to_price_observation()
    assert obs.price_paise == 3899000
    assert obs.mrp_paise == 5299000


def test_amazon_parser_detects_out_of_stock() -> None:
    html = """
    <html>
        <span id="productTitle">Lenovo Legion Pro 5 Laptop</span>
        <span class="a-price"><span class="a-offscreen">₹1,45,000.00</span></span>
        <div id="availability"><span>Currently unavailable.</span></div>
    </html>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.amazon.in/dp/B0CXOUTOFSTOCK")

    parsed = parse_amazon_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="B0CXOUTOFSTOCK",
        observed_at=now,
    )

    assert parsed.brand == "Lenovo"
    assert parsed.in_stock is False
    assert parsed.price_paise == 14500000


def test_amazon_adapter_parse_method() -> None:
    adapter = AmazonSourceAdapter([], FakeTransport())
    now = datetime.now(UTC)
    record = RawSourceRecord(
        source="amazon",
        source_product_id="B0CXDEMO1",
        category="laptop",
        source_url=AnyHttpUrl("https://www.amazon.in/dp/B0CXDEMO1"),
        observed_at=now,
        payload={"title": "Apple MacBook Air M3", "price_paise": 11490000},
        content_hash="0123456789abcdef0123456789abcdef",
    )

    parsed = adapter.parse(record)
    assert parsed.source == "amazon"
    assert parsed.brand == "Apple"
    assert parsed.price_paise == 11490000


def test_amazon_parser_json_ld_and_bullets() -> None:
    html = """
    <html>
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "Dell XPS 13 Plus",
            "offers": {"price": "199990"}
        }
        </script>
        <div id="detailBullets_feature_div">
            <ul>
                <li>
                    <span class="a-list-item">
                        <span>Processor Brand: </span>
                        <span>Intel Core i7-1360P</span>
                    </span>
                </li>
                <li>
                    <span class="a-list-item">
                        <span>RAM Size: </span>
                        <span>32 GB</span>
                    </span>
                </li>
                <li>
                    <span class="a-list-item">
                        <span>Hard Drive Size: </span>
                        <span>1 TB</span>
                    </span>
                </li>
            </ul>
        </div>
    </html>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.amazon.in/dp/B0CXJSONLD")

    parsed = parse_amazon_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="B0CXJSONLD",
        observed_at=now,
    )

    assert parsed.title == "Dell XPS 13 Plus"
    assert parsed.brand == "Dell"
    assert parsed.price_paise == 19999000
    assert parsed.attributes["cpu_model"] == "Intel Core i7-1360P"
    assert parsed.attributes["ram_gb"] == 32
    assert parsed.attributes["storage_gb"] == 1024


def test_amazon_parser_json_ld_list_and_fallbacks() -> None:
    html = """
    <html>
        <script type="application/ld+json">
        [
            {"@type": "BreadcrumbList"},
            {"@type": "Product", "name": "CustomBrand UltraBook", "offers": {"price": "89000"}}
        ]
        </script>
    </html>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.amazon.in/dp/B0CXCUSTOM")

    parsed = parse_amazon_payload(
        payload={"html": html, "seller": "Cloudtail", "mrp_paise": 9900000},
        source_url=url,
        source_product_id="B0CXCUSTOM",
        observed_at=now,
    )

    assert parsed.title == "CustomBrand UltraBook"
    assert parsed.brand == "CustomBrand"
    assert parsed.price_paise == 8900000
    assert parsed.mrp_paise == 9900000
    assert parsed.seller == "Cloudtail"


def test_amazon_detailed_expander_specifications_html() -> None:
    html = """
    <div id="productDetails_feature_div">
        <div id="prodDetails">
            <h1>Product information</h1>
            <div id="productDetails_expanderSectionTables">
                <div class="a-section-expander-container">
                    <span class="a-expander-prompt">Additional details</span>
                    <table class="prodDetTable">
                        <tr>
                            <th class="prodDetSectionEntry">Colour</th>
                            <td class="prodDetAttrValue">Cool Silver</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Hard Drive Size</th>
                            <td class="prodDetAttrValue">512 GB</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Operating System</th>
                            <td class="prodDetAttrValue">Windows 11 Home</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">
                                Other Special Features of the Product
                            </th>
                            <td class="prodDetAttrValue">
                                Anti-glare display, Backlit Keyboard, FHD camera
                            </td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Graphics Description</th>
                            <td class="prodDetAttrValue">Integrated</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Hard Disk Description</th>
                            <td class="prodDetAttrValue">SSD</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Item Weight</th>
                            <td class="prodDetAttrValue">1 kg 880 g</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Warranty Type</th>
                            <td class="prodDetAttrValue">Limited</td>
                        </tr>
                    </table>
                </div>
                <div class="a-section-expander-container">
                    <span class="a-expander-prompt">Display</span>
                    <table class="prodDetTable">
                        <tr>
                            <th class="prodDetSectionEntry">Screen Size</th>
                            <td class="prodDetAttrValue">16 Inches</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Maximum Display Resolution</th>
                            <td class="prodDetAttrValue">1920 x 1200 Pixels</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Maximum Display Brightness</th>
                            <td class="prodDetAttrValue">300 Nit</td>
                        </tr>
                    </table>
                </div>
                <div class="a-section-expander-container">
                    <span class="a-expander-prompt">Connectivity</span>
                    <table class="prodDetTable">
                        <tr>
                            <th class="prodDetSectionEntry">Wireless Technology</th>
                            <td class="prodDetAttrValue">Wi-Fi</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Computer Wireless Type</th>
                            <td class="prodDetAttrValue">802.11ax</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Wi-Fi Generation</th>
                            <td class="prodDetAttrValue">Wi-Fi 6</td>
                        </tr>
                    </table>
                </div>
                <div class="a-section-expander-container">
                    <span class="a-expander-prompt">Battery</span>
                    <table class="prodDetTable">
                        <tr>
                            <th class="prodDetSectionEntry">Battery Cell Type</th>
                            <td class="prodDetAttrValue">Lithium Ion</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Battery Life</th>
                            <td class="prodDetAttrValue">8 Hours</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">
                                Lithium Battery Energy Content
                            </th>
                            <td class="prodDetAttrValue">42 Watt Hours</td>
                        </tr>
                    </table>
                </div>
                <div class="a-section-expander-container">
                    <span class="a-expander-prompt">Processor</span>
                    <table class="prodDetTable">
                        <tr>
                            <th class="prodDetSectionEntry">Processor Type</th>
                            <td class="prodDetAttrValue">Intel Core Ultra 5</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">CPU Model Number</th>
                            <td class="prodDetAttrValue">Intel Core Ultra 5 Processor 225H</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">CPU Model Speed Maximum</th>
                            <td class="prodDetAttrValue">4.9 GHz</td>
                        </tr>
                    </table>
                </div>
                <div class="a-section-expander-container">
                    <span class="a-expander-prompt">Item details</span>
                    <table class="prodDetTable">
                        <tr>
                            <th class="prodDetSectionEntry">Brand Name</th>
                            <td class="prodDetAttrValue">ASUS</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Model Name</th>
                            <td class="prodDetAttrValue">ASUS Vivobook 16</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Model Number</th>
                            <td class="prodDetAttrValue">X1607CA-MB142WS</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Manufacturer Part Number</th>
                            <td class="prodDetAttrValue">90NB15A2-M007P0</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">ASIN</th>
                            <td class="prodDetAttrValue">B0DT74FF9P</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Warranty Description</th>
                            <td class="prodDetAttrValue">1 year Global Warranty</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">Customer Reviews</th>
                            <td>
                                <div id="averageCustomerReviews">
                                    <span class="a-size-small a-color-base">3.9</span>
                                    <span id="acrCustomerReviewText">68 Reviews</span>
                                </div>
                            </td>
                        </tr>
                    </table>
                </div>
                <div class="a-section-expander-container">
                    <span class="a-expander-prompt">Memory</span>
                    <table class="prodDetTable">
                        <tr>
                            <th class="prodDetSectionEntry">RAM Memory Installed</th>
                            <td class="prodDetAttrValue">16 GB</td>
                        </tr>
                        <tr>
                            <th class="prodDetSectionEntry">RAM Memory Technology</th>
                            <td class="prodDetAttrValue">DDR5</td>
                        </tr>
                    </table>
                </div>
            </div>
            <input type="hidden" name="priceValue" value="88990.0" id="priceValue">
            <input
                type="hidden"
                name="productTitle"
                value="ASUS Vivobook 16, Intel Core Ultra 5 225H, 16GB RAM, 512GB SSD"
                id="productTitle"
            >
            <input type="hidden" name="asin" value="B0DT74FF9P" id="asin">
        </div>
    </div>
    """
    now = datetime.now(UTC)
    url = AnyHttpUrl("https://www.amazon.in/dp/B0DT74FF9P")

    parsed = parse_amazon_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="B0DT74FF9P",
        observed_at=now,
        category="laptop",
    )

    assert parsed.brand == "Asus"
    assert parsed.price_paise == 8899000
    assert parsed.rating == 3.9
    assert parsed.review_count == 68
    assert parsed.attributes["cpu_model"] == "Intel Core Ultra 5 225H"
    assert parsed.attributes["ram_gb"] == 16
    assert parsed.attributes["ram_type"] == "DDR5"
    assert parsed.attributes["storage_gb"] == 512
    assert parsed.attributes["storage_type"] == "SSD"
    assert parsed.attributes["screen_size_inches"] == 16.0
    assert parsed.attributes["display_resolution"] == "WUXGA"
    assert parsed.attributes["weight_kg"] == 1.88
    assert parsed.attributes["battery_wh"] == 42.0
    assert parsed.attributes["keyboard_backlight"] is True
    assert parsed.attributes["wifi_standard"] == "Wi-Fi 6"
    assert parsed.attributes["asin"] == "B0DT74FF9P"
    assert parsed.attributes["mpn"] == "90NB15A2-M007P0"
    assert parsed.manufacturer_part_number == "90NB15A2-M007P0"
    assert parsed.to_fingerprint().manufacturer_part_number == "90nb15a2-m007p0"
    assert parsed.attributes["warranty"] == "1 year Global Warranty"
    spec_sec = parsed.attributes["spec_sections"]
    assert isinstance(spec_sec, dict)
    assert "Display" in spec_sec
    assert "Processor" in spec_sec
