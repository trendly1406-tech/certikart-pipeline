"""Comprehensive test suite for Croma parser covering hydration state decoding,

classifications, pricing, and error resilience.
"""

from datetime import UTC, datetime

from pydantic import AnyHttpUrl

from sources import CromaSourceAdapter, FetchedSourceDocument, RawSourceRecord
from sources.croma.parser import parse_croma_payload


class FakeTransport:
    async def fetch(self, source_url: AnyHttpUrl) -> FetchedSourceDocument:
        return FetchedSourceDocument(
            observed_at=datetime.now(UTC),
            payload={},
            content_hash="0123456789abcdef0123456789abcdef",
        )


def test_1_valid_initial_data_extraction() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "ASUS TUF Gaming A15 AMD Ryzen 7 (16GB, 512GB SSD, RTX 4060)",
                    "manufacturer": "ASUS",
                    "price": {"value": 89990.0, "formattedValue": "₹89,990"},
                    "mrp": {"value": 115990.0, "formattedValue": "₹1,15,990"},
                    "stock": "inStock"
                }
            }
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/asus-tuf-gaming-a15/p/320001")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320001",
        observed_at=datetime.now(UTC),
    )
    assert parsed.brand == "Asus"
    assert parsed.price_paise == 8999000
    assert parsed.mrp_paise == 11599000
    assert parsed.in_stock is True


def test_2_hydration_state_containing_error_message_undefined() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "Lenovo IdeaPad Slim 3 (16GB, 512GB SSD)",
                    "manufacturer": "Lenovo",
                    "price": {"value": 54990.0}
                }
            },
            "errorMessage": undefined,
            "isFetching": false
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/lenovo-ideapad-slim-3/p/320002")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320002",
        observed_at=datetime.now(UTC),
    )
    assert parsed.brand == "Lenovo"
    assert parsed.price_paise == 5499000


def test_3_product_title_cleaning() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "Buy Apple MacBook Air 13.6 M5 (16GB, 512GB SSD) Online - Croma"
                }
            }
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/apple-macbook-air-m5/p/320003")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320003",
        observed_at=datetime.now(UTC),
    )
    assert parsed.title == "Apple MacBook Air 13.6 M5 (16GB, 512GB SSD)"


def test_4_and_5_brand_and_model_family_extraction() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "HP Victus Gaming Laptop 15-fa1234TX (16GB, 512GB SSD)",
                    "manufacturer": "HP",
                    "classifications": [
                        {
                            "name": "Manufacturer Details",
                            "features": [
                                {"name": "Model Series", "featureValues": [{"value": "Victus 15"}]}
                            ]
                        }
                    ]
                }
            }
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/hp-victus-15/p/320004")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320004",
        observed_at=datetime.now(UTC),
    )
    assert parsed.brand == "Hp"
    assert parsed.model_name == "Victus 15"


def test_6_and_7_cpu_and_gpu_extraction() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "Dell Inspiron 15 (16GB, 512GB SSD)",
                    "classifications": [
                        {
                            "name": "Processor",
                            "features": [
                                {
                                    "name": "Processor Type",
                                    "featureValues": [{"value": "Intel Core i5-1335U"}]
                                },
                                {
                                    "name": "Graphics Processor",
                                    "featureValues": [{"value": "NVIDIA GeForce RTX 3050"}]
                                }
                            ]
                        }
                    ]
                }
            }
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/dell-inspiron-15/p/320005")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320005",
        observed_at=datetime.now(UTC),
    )
    assert parsed.attributes["cpu_model"] == "Intel Core i5-1335U"
    assert parsed.attributes["gpu_model"] == "NVIDIA GeForce RTX 3050"


def test_8_and_9_ram_and_storage_extraction() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "Acer Swift Go 14 Laptop",
                    "classifications": [
                        {
                            "name": "Memory & Storage",
                            "features": [
                                {"name": "RAM", "featureValues": [{"value": "16 GB"}]},
                                {"name": "SSD Capacity", "featureValues": [{"value": "1 TB"}]}
                            ]
                        }
                    ]
                }
            }
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/acer-swift-go/p/320006")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320006",
        observed_at=datetime.now(UTC),
    )
    assert parsed.attributes["ram_gb"] == 16
    assert parsed.attributes["storage_gb"] == 1024


def test_10_screen_size_and_storage_type_extraction() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "Samsung Galaxy Book4 15.6 inch SSD Laptop",
                    "classifications": [
                        {
                            "name": "Display",
                            "features": [
                                {
                                    "name": "Screen Size (In Inches)",
                                    "featureValues": [{"value": "15.6"}]
                                }
                            ]
                        }
                    ]
                }
            }
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/samsung-galaxy-book4/p/320007")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320007",
        observed_at=datetime.now(UTC),
    )
    assert parsed.attributes["screen_size_inches"] == 15.6
    assert parsed.attributes["storage_type"] == "SSD"


def test_11_gtin_ean_and_mpn_extraction() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "Apple MacBook Air M5",
                    "ean": "195949778899",
                    "classifications": [
                        {
                            "name": "Manufacturer",
                            "features": [
                                {"name": "Model Number", "featureValues": [{"value": "MRYR3HN/A"}]}
                            ]
                        }
                    ]
                }
            }
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/apple-macbook-air-m5/p/320008")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320008",
        observed_at=datetime.now(UTC),
    )
    assert parsed.attributes["ean"] == "195949778899"
    assert parsed.attributes["gtin"] == "195949778899"
    assert parsed.attributes["mpn"] == "MRYR3HN/A"
    assert parsed.gtin == "195949778899"
    assert parsed.manufacturer_part_number == "MRYR3HN/A"


def test_12_and_13_price_and_mrp_extraction() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "HP Pavilion 15",
                    "price": {"value": 67990.0},
                    "mrp": {"value": 85990.0}
                }
            }
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/hp-pavilion-15/p/320009")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320009",
        observed_at=datetime.now(UTC),
    )
    assert parsed.price_paise == 6799000
    assert parsed.mrp_paise == 8599000


def test_14_json_ld_fallback_price_extraction() -> None:
    html = """
    <html>
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "MSI Modern 14 Laptop",
            "offers": {"price": "43990"}
        }
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/msi-modern-14/p/320010")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320010",
        observed_at=datetime.now(UTC),
    )
    assert parsed.brand == "Msi"
    assert parsed.price_paise == 4399000


def test_15_and_16_product_url_and_code_extraction() -> None:
    adapter = CromaSourceAdapter([], FakeTransport())
    url = AnyHttpUrl("https://www.croma.com/dell-latitude-3440/p/320011")
    record = RawSourceRecord(
        source="croma",
        source_product_id="320011",
        category="laptop",
        source_url=url,
        observed_at=datetime.now(UTC),
        payload={"title": "Dell Latitude 3440", "price_paise": 6200000},
        content_hash="0123456789abcdef0123456789abcdef",
    )
    parsed = adapter.parse(record)
    assert parsed.source == "croma"
    assert parsed.source_product_id == "320011"
    assert str(parsed.source_url) == "https://www.croma.com/dell-latitude-3440/p/320011"


def test_17_missing_optional_specifications() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "pdpReducer": {
                "pdpData": {
                    "name": "Generic Office Laptop",
                    "price": {"value": 25000.0}
                }
            }
        };
        </script>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/generic-laptop/p/320012")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320012",
        observed_at=datetime.now(UTC),
    )
    assert parsed.title == "Generic Office Laptop"
    assert parsed.price_paise == 2500000
    assert "screen_size_inches" not in parsed.attributes
    assert "ean" not in parsed.attributes


def test_18_malformed_hydration_state_fallback() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= { this is corrupted unparseable javascript }}};;
        </script>
        <h1 class="pd-title">HP 15s Notebook</h1>
        <span class="amount">₹41,990</span>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/hp-15s/p/320013")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320013",
        observed_at=datetime.now(UTC),
    )
    assert parsed.title == "HP 15s Notebook"
    assert parsed.brand == "Hp"
    assert parsed.price_paise == 4199000


def test_19_empty_product_payload() -> None:
    url = AnyHttpUrl("https://www.croma.com/p/320014")
    parsed = parse_croma_payload(
        payload={},
        source_url=url,
        source_product_id="320014",
        observed_at=datetime.now(UTC),
    )
    assert parsed.source_product_id == "320014"
    assert parsed.title == "Croma Product 320014"
    assert parsed.price_paise == 0


def test_20_query_param_price_fallback() -> None:
    html = """
    <html>
        <h1>Lenovo V15 Laptop</h1>
    </html>
    """
    url = AnyHttpUrl("https://www.croma.com/lenovo-v15/p/320015?_price=3799000&_mrp=4999000")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="320015",
        observed_at=datetime.now(UTC),
    )
    assert parsed.price_paise == 3799000
    assert parsed.mrp_paise == 4999000


def test_21_croma_detailed_specifications_accordion_html() -> None:
    # fmt: off
    html = """
    <div class="cp-section accordContainer" id="accrdtop"><div class="container"><div class="sec-cont"><div class="cp-accordian"><div class="hide-keyfeature"><div class="MuiPaper-root MuiAccordion-root accordian-item MuiAccordion-rounded MuiPaper-elevation1 MuiPaper-rounded" id="panel5"><div class="MuiButtonBase-root MuiAccordionSummary-root" tabindex="0" role="button" aria-disabled="false" aria-expanded="false" aria-controls="panel5bh-content" id="panel5bh-header"><div class="MuiAccordionSummary-content"><h2 class="MuiTypography-root accorian-title MuiTypography-body1">Key Features</h2></div><div class="MuiButtonBase-root MuiIconButton-root MuiAccordionSummary-expandIcon MuiIconButton-edgeEnd" aria-disabled="false" aria-hidden="true"><span class="MuiIconButton-label"><svg class="MuiSvgIcon-root" focusable="false" viewBox="0 0 24 24" aria-hidden="true"><path d="M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z"></path></svg></span><span class="MuiTouchRipple-root"></span></div></div><div class="MuiCollapse-root MuiCollapse-hidden" style="min-height:0px"><div class="MuiCollapse-wrapper"><div class="MuiCollapse-wrapperInner"><div aria-labelledby="panel5bh-header" id="panel5bh-content" role="region"><div class="MuiAccordionDetails-root"><span class="MuiTypography-root accordian-content MuiTypography-body1"><div class="cp-keyfeature pd-eligibility-wrap"><ul><li>Display: 38.86 cms (15.3 inches), WQXGA IPS</li><li>Memory: 16GB DDR5 RAM, 512GB M.2 PCIe NVMe 4.0 SSD ROM</li><li>Processor: AMD Ryzen 7</li><li>OS: Windows 11 Home</li><li>Graphics: AMD Radeon</li><li>Included Software: Microsoft Office Home 2024, Microsoft 365 Basic</li><li>Dolby Audio, Backlit Keyboard, Camera Privacy Shutter</li><li>Warranty: 1 year Carry-In</li></ul></div></span></div></div></div></div></div></div></div><div class="MuiPaper-root MuiAccordion-root accordian-item product-info-accordian Mui-expanded MuiAccordion-rounded MuiPaper-elevation1 MuiPaper-rounded" id="panel2"><div class="MuiButtonBase-root MuiAccordionSummary-root Mui-expanded" tabindex="0" role="button" aria-disabled="false" aria-expanded="true" aria-controls="panel2bh-content" id="panel2bh-header"><div class="MuiAccordionSummary-content Mui-expanded"><h2 class="MuiTypography-root accorian-title MuiTypography-body1">Specifications</h2><div class="flex-containerDesktop"><img class="accorian-pdfimageDesktop" src="https://media-ik.croma.com/PDFImage_dmhcni.png" alt=""><div class="flex-containerDesktop"><div><a class="flex-item-leftDesktop" href="https://media.tatacroma.com/Croma%20Assets/Computers%20Peripherals/Laptop/User%20Manual/323284_User%20Manual?updatedAt=1783593568716" target="_blank" rel="noopener noreferrer">User Manual</a></div></div></div></div><div class="MuiButtonBase-root MuiIconButton-root MuiAccordionSummary-expandIcon Mui-expanded MuiIconButton-edgeEnd" aria-disabled="false" aria-hidden="true"><span class="MuiIconButton-label"><svg class="MuiSvgIcon-root" focusable="false" viewBox="0 0 24 24" aria-hidden="true"><path d="M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z"></path></svg></span><span class="MuiTouchRipple-root"></span></div></div><div class="MuiCollapse-root MuiCollapse-entered" style="min-height:0px"><div class="MuiCollapse-wrapper"><div class="MuiCollapse-wrapperInner"><div aria-labelledby="panel2bh-header" id="panel2bh-content" role="region"><div class="MuiAccordionDetails-root"><span class="MuiTypography-root accordian-content MuiTypography-body1"><div class="spec-fixed-height show-view-more"><div class="cp-specification" id="specification_container"><ul class="cp-specification-info"><li><h3 class="title">Laptop Category</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Laptop Type</h4></li><li class="cp-specification-spec-details">Thin and Light </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Suitable For</h4></li><li class="cp-specification-spec-details">Everyday Use  | Home  | Entertainment  | Office Use  | Work </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Manufacturer Details</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Brand</h4></li><li class="cp-specification-spec-details"><a href="/lenovo-store/b/b-0216" target="_blank" rel="noopener noreferrer" class="brand-url-pdp spec-brand-url">Lenovo </a></li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Model Series</h4></li><li class="cp-specification-spec-details">IdeaPad Slim 3 15ARP10 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Model Number</h4></li><li class="cp-specification-spec-details">83K7011MIN </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Product Dimensions (Open)</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Dimensions In CM (WxDxH)</h4></li><li class="cp-specification-spec-details">34.34 x 23.95 x 1.79 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Product Weight</h4></li><li class="cp-specification-spec-details">1.59 Kg</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Dimensions In Inches (WxDxH)</h4></li><li class="cp-specification-spec-details">13.52 x 9.43 x 0.7 </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Operating System</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>OS Type</h4></li><li class="cp-specification-spec-details">Windows OS </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Operating System</h4></li><li class="cp-specification-spec-details">Windows 11 </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Laptop Screen Specifications</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Display Size (In Inches)</h4></li><li class="cp-specification-spec-details">15.3 Inches</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Display Size (In Cms)</h4></li><li class="cp-specification-spec-details">38.86 cm</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Display Type</h4></li><li class="cp-specification-spec-details">IPS Screen </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Screen Resolution</h4></li><li class="cp-specification-spec-details">1920 x 1200 pixels </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Aspect Ratio</h4></li><li class="cp-specification-spec-details">16:10 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Touch Screen</h4></li><li class="cp-specification-spec-details">No </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Refresh Rate</h4></li><li class="cp-specification-spec-details">60 Hz</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Additional Screen Specifications</h4></li><li class="cp-specification-spec-details">WUXGA, Brightness: 300nits Anti-glare, 45% NTSC, IPS models: 90.7% AAR (Active Area Ratio), Contrast Ratio: 1200:1, Viewing Angle (L/R/U/D): 89 Degree </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Laptop Processor Details</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Processor Brand</h4></li><li class="cp-specification-spec-details">AMD </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Processor Type</h4></li><li class="cp-specification-spec-details">Ryzen 7 </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Processor Variant</h4></li><li class="cp-specification-spec-details">7735HS </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Number Of Cores</h4></li><li class="cp-specification-spec-details">8-cores </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Processor Speed</h4></li><li class="cp-specification-spec-details">3.2 GHz</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Maximum Turbo Speed</h4></li><li class="cp-specification-spec-details">4.75 GHz</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Cache</h4></li><li class="cp-specification-spec-details">20 MB</li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Internal Memory</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Type of RAM</h4></li><li class="cp-specification-spec-details">DDR5 </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>RAM</h4></li><li class="cp-specification-spec-details">16GB </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>RAM Frequency</h4></li><li class="cp-specification-spec-details">4800 MHz</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>RAM Form Factor</h4></li><li class="cp-specification-spec-details">SO-DIMM </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Storage Specification</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Storage Type</h4></li><li class="cp-specification-spec-details">M.2 NVMe PCIe 4.0 SSD </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>HDD Capacity</h4></li><li class="cp-specification-spec-details">No HDD </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>SSD Capacity</h4></li><li class="cp-specification-spec-details">512GB </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Expandable Upto</h4></li><li class="cp-specification-spec-details">2048 GB</li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Graphic Processor</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>GPU Brand</h4></li><li class="cp-specification-spec-details">AMD </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>GPU Model</h4></li><li class="cp-specification-spec-details">AMD Radeon 680M </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Graphic Processor Type</h4></li><li class="cp-specification-spec-details">Integrated </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Dedicated Graphics Memory</h4></li><li class="cp-specification-spec-details">Shared Memory </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Sound</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Additional Audio Features</h4></li><li class="cp-specification-spec-details">High Definition (HD) Audio, Dual-microphone Array </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Speaker Capacity</h4></li><li class="cp-specification-spec-details">4 Watts</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Audio Technology</h4></li><li class="cp-specification-spec-details">Dolby Audio </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Speaker Type</h4></li><li class="cp-specification-spec-details">Stereo Speakers </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Audio Jack Port</h4></li><li class="cp-specification-spec-details">3.5mm - Headphone/Microphone Combo Port </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Ports &amp; Slots</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Aux Port Type</h4></li><li class="cp-specification-spec-details">3.5mm - Headphone/Microphone Combo Port </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Number Of USB Ports</h4></li><li class="cp-specification-spec-details">3 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Number Of HDMI Ports</h4></li><li class="cp-specification-spec-details">1 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Type of DisplayPort</h4></li><li class="cp-specification-spec-details">DisplayPort </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>HDMI Type</h4></li><li class="cp-specification-spec-details">HDMI </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>USB Types Supported</h4></li><li class="cp-specification-spec-details">USB 3.2 (Type-C)  | USB 3.2 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>External Card Supported</h4></li><li class="cp-specification-spec-details">1 x SD Card Reader </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Other Ports &amp; Slots</h4></li><li class="cp-specification-spec-details">1 x Round Tip Power Connector </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Network Connectivity</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Bluetooth Supported</h4></li><li class="cp-specification-spec-details">Yes </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>WiFi Specifications</h4></li><li class="cp-specification-spec-details">Wi-Fi 6 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Wi-Fi Supported</h4></li><li class="cp-specification-spec-details">Yes </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Bluetooth Specifications</h4></li><li class="cp-specification-spec-details">Bluetooth 5.3 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Wi-Fi Features</h4></li><li class="cp-specification-spec-details">802.11ax 2x2 </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Laptop Camera Type</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Camera Type</h4></li><li class="cp-specification-spec-details">Webcam </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Camera Resolution</h4></li><li class="cp-specification-spec-details">1080p </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Camera Features</h4></li><li class="cp-specification-spec-details">Camera Privacy Shutter, IR Camera for Windows Hello, Fixed Focus </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Keyboard Specification</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Type of Keyboard</h4></li><li class="cp-specification-spec-details">Backlit Keyboard </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Keyboard Configuration</h4></li><li class="cp-specification-spec-details">6-row, Multimedia Fn Keys, Key Travel 1.3mm, Numeric Keypad, Copilot Key </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Mouse Specification</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Type of Mouse</h4></li><li class="cp-specification-spec-details">Touchpad </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Mouse Configuration</h4></li><li class="cp-specification-spec-details">Buttonless Mylar Surface Multi-touch Touchpad, Supports Precision TouchPad (PTP), 75 x 120 mm (2.95 x 4.72 Inches) </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Access Control And Security</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Lock</h4></li><li class="cp-specification-spec-details">Facial  | Password </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Additional Features</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Included Software</h4></li><li class="cp-specification-spec-details">Microsoft Office Home 2024, Microsoft 365 Basic </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Additional Features</h4></li><li class="cp-specification-spec-details">Surface Treatment: Aluminium Stamping Anodized (Top), Texture (Bottom), Security Chip: Firmware TPM 2.0 Enabled, Chipset: AMD SoC Platform, BIOS Security, DirectX 12 </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Laptop Battery</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Battery Type</h4></li><li class="cp-specification-spec-details">Non-Removable </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Battery Technology</h4></li><li class="cp-specification-spec-details">Lithium-Ion </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Number of Cells</h4></li><li class="cp-specification-spec-details">1 Cell</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Standard Battery Life</h4></li><li class="cp-specification-spec-details">50 Watt Hours, Local Video (1080p) Playback at 150nits: 15 Hours </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Charging</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Charging Pin Type</h4></li><li class="cp-specification-spec-details">Round Pin </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Fast Charging Capability</h4></li><li class="cp-specification-spec-details">Yes </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Battery Charge Time</h4></li><li class="cp-specification-spec-details">2 Hours of Runtime with a 15 Minute Charge </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Plug Details</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Power Consumption</h4></li><li class="cp-specification-spec-details">65 Watts</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Voltage Rating</h4></li><li class="cp-specification-spec-details">100 - 240 V </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Power Pin Type</h4></li><li class="cp-specification-spec-details">Round Tip (3-pin) </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Frequency</h4></li><li class="cp-specification-spec-details">50 - 60 Hz </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Materials &amp; Durability</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Body Material</h4></li><li class="cp-specification-spec-details">ABS Plastic  | Aluminum  | Polycarbonate </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Aesthetics</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Brand Color</h4></li><li class="cp-specification-spec-details">Luna Grey </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Color</h4></li><li class="cp-specification-spec-details">GREY </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">In The Box</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Documents</h4></li><li class="cp-specification-spec-details">1 x User Guide, 1 x Warranty Card </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Main product</h4></li><li class="cp-specification-spec-details">1 x Laptop U </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Accessories</h4></li><li class="cp-specification-spec-details">1 x Power Adapter </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Package Includes</h4></li><li class="cp-specification-spec-details">1 x Laptop U, 1 x Power Adapter, 1 x User Guide, 1 x Warranty Card </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Generic Name</h4></li><li class="cp-specification-spec-details">Laptop </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">After Sales &amp; Services</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Warranty on Main Product</h4></li><li class="cp-specification-spec-details">12 Months</li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Warranty Type</h4></li><li class="cp-specification-spec-details">Carry-In </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Standard Warranty Includes</h4></li><li class="cp-specification-spec-details">Manufacturing Defects </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Standard Warranty Excludes</h4></li><li class="cp-specification-spec-details">Physical Damage </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Warranty on Accessories</h4></li><li class="cp-specification-spec-details">12 Months</li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Installation &amp; Demo applicable</h4></li><li class="cp-specification-spec-details">No </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Certification</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>ROHS Compliance</h4></li><li class="cp-specification-spec-details">Yes </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Additional Certifications</h4></li><li class="cp-specification-spec-details">EPEAT Gold Registered, ErP Lot 6/26, MIL-STD-810H Military Test Passed, TUV Rheinland Low Blue Light </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Certification</h4></li><li class="cp-specification-spec-details">Yes </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Energy Star Certification</h4></li><li class="cp-specification-spec-details">ENERGY STAR 9.0 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Certifications</h4></li><li class="cp-specification-spec-details">ROHS Compliance  | Energy Star Certification </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Company Contact Information</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Customer Support Number</h4></li><li class="cp-specification-spec-details">18005727662 </li></div></ul><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Customer Support Email</h4></li><li class="cp-specification-spec-details">customersupport@croma.com </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Manufacturer/Importer/Marketer Name &amp; Address</h4></li><li class="cp-specification-spec-details">Manufacturer Name &amp; Address: Compal Information Technology (Kunshan) Co., Ltd., No. 58, 1st Street, Kunshan Export Processing Zone, Kunshan, Jiangsu 215300, P.R. China | Importer Name &amp; Address: Lenovo (India) Private Limited, RBD Icon, Level 2, Doddanekundi Village, Marathahalli Outer Ring Road, K.R. Puram Hobli, Bengaluru - 560037, Karnataka, India </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Country of Origin</h4></li><li class="cp-specification-spec-details">China </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Country of Brand Origin</h4></li><li class="cp-specification-spec-details">China </li></div></ul></li></ul><ul class="cp-specification-info"><li><h3 class="title">Croma Service Promise</h3></li><li><ul class="cp-specification-spec-info"><div><li class="cp-specification-spec-title"><h4>Customer Support Email</h4></li><li class="cp-specification-spec-details">customersupport@croma.com </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Registered Name and Address</h4></li><li class="cp-specification-spec-details">Infiniti Retail Ltd. - Unit No. 701 &amp; 702, 7th Floor, Kaledonia, Sahar Road, Andheri (East); Mumbai - 400069. India </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Customer Support Number</h4></li><li class="cp-specification-spec-details">1800 572 7662 </li></div></ul><ul class="cp-specification-info"><div><li class="cp-specification-spec-title"><h4>Customer Care Contact Person</h4></li><li class="cp-specification-spec-details">Grievance Officer </li></div></ul></li></ul><div class="btn-wrap specs-btn hide-specbtn"><button type="button" class="btn btn-viewmore hyperlink-default">View More</button></div><br></div><div class="btn-view-more" id="spec_viewMore_btn"><div class="btn-viewmore-click">View Less</div></div></div></span></div></div></div></div></div></div><div class="MuiPaper-root MuiAccordion-root accordian-item product-info-accordian Mui-expanded MuiAccordion-rounded MuiPaper-elevation1 MuiPaper-rounded" id="panel1"><div class="MuiButtonBase-root MuiAccordionSummary-root Mui-expanded" tabindex="0" role="button" aria-disabled="false" aria-expanded="true" aria-controls="panel1bh-content" id="panel1bh-header"><div class="MuiAccordionSummary-content Mui-expanded"><h2 class="MuiTypography-root accorian-title MuiTypography-body1">Overview</h2></div><div class="MuiButtonBase-root MuiIconButton-root MuiAccordionSummary-expandIcon Mui-expanded MuiIconButton-edgeEnd" aria-disabled="false" aria-hidden="true"><span class="MuiIconButton-label"><svg class="MuiSvgIcon-root" focusable="false" viewBox="0 0 24 24" aria-hidden="true"><path d="M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z"></path></svg></span><span class="MuiTouchRipple-root"></span></div></div><div class="MuiCollapse-root MuiCollapse-entered" style="min-height:0px"><div class="MuiCollapse-wrapper"><div class="MuiCollapse-wrapperInner"><div aria-labelledby="panel1bh-header" id="panel1bh-content" role="region"><div class="MuiAccordionDetails-root"><span class="MuiTypography-root accordian-content MuiTypography-body1"><div class="overview-fixed-height"><div class="cp-overview pd-eligibility-wrap" id="overview_inner_container"><p><strong>Capable AMD Ryzen 7 Performance</strong><br>
    Powered by the AMD Ryzen 7 processor with 8 cores and 16 threads, this Lenovo 15.3-inch Windows Laptop delivers responsive performance for everyday computing and multitasking needs.<br>
    <strong>High-Speed 16GB Memory</strong><br>
    Incorporating 16GB of DDR5-4800 memory, this Windows laptop is designed to support smooth multitasking.<br>
    <strong>Rapid 512GB SSD Storage</strong><br>
    Featuring a 512GB M.2 PCIe 4.0 NVMe SSD, this laptop offers fast data access.<br>
    <strong>Refined IPS Visuals</strong><br>
    Featuring a 15.3-inch IPS display with a 1920 x 1200 resolution.<br>
    <strong>Integrated Radeon Graphics</strong><br>
    Powered by integrated AMD Radeon 680M Graphics.<br>
    <strong>Practical Backlit Keyboard Design</strong><br>
    Incorporating a backlit keyboard, this Lenovo laptop enables comfortable typing.</p></div></div></span></div></div></div></div></div></div></div></div></div></div>
    """  # noqa: E501
    # fmt: on
    url = AnyHttpUrl("https://www.croma.com/lenovo-ideapad-slim-3-15arp10/p/323284")
    parsed = parse_croma_payload(
        payload={"html": html},
        source_url=url,
        source_product_id="323284",
        observed_at=datetime.now(UTC),
    )

    # Core Identifiers
    assert parsed.brand == "Lenovo"
    assert parsed.model_name == "IdeaPad Slim 3 15ARP10"
    assert "Lenovo IdeaPad Slim 3 15ARP10" in parsed.title

    # Structured Attributes
    assert parsed.attributes["cpu_model"] == "AMD Ryzen 7 7735HS"
    assert parsed.attributes["gpu_model"] == "AMD Radeon 680M"
    assert parsed.attributes["ram_gb"] == 16
    assert parsed.attributes["ram_type"] == "DDR5"
    assert parsed.attributes["storage_gb"] == 512
    assert parsed.attributes["storage_type"] == "SSD"
    assert parsed.attributes["screen_size_inches"] == 15.3
    assert parsed.attributes["display_type"] == "IPS LCD"
    assert parsed.attributes["display_resolution"] == "WUXGA"
    assert parsed.attributes["operating_system"] == "Windows 11"
    assert parsed.attributes["color"] == "Luna Grey"
    assert parsed.attributes["weight_kg"] == 1.59
    assert parsed.attributes["battery_wh"] == 50.0
    assert parsed.attributes["keyboard_backlight"] is True
    assert parsed.attributes["webcam_resolution"] == "1080p FHD"
    assert parsed.attributes["wifi_standard"] == "Wi-Fi 6"
    assert parsed.attributes["mpn"] == "83K7011MIN"
    assert parsed.attributes["warranty"] == "12 Months"

    # Hierarchy in spec_sections
    spec_sections = parsed.attributes.get("spec_sections", {})
    assert isinstance(spec_sections, dict)
    assert "Laptop Category" in spec_sections
    assert spec_sections["Laptop Category"]["Laptop Type"] == "Thin and Light"
    assert "Manufacturer Details" in spec_sections
    assert spec_sections["Manufacturer Details"]["Brand"] == "Lenovo"
    assert spec_sections["Manufacturer Details"]["Model Series"] == "IdeaPad Slim 3 15ARP10"
    assert spec_sections["Manufacturer Details"]["Model Number"] == "83K7011MIN"
    assert "Laptop Processor Details" in spec_sections
    assert spec_sections["Laptop Processor Details"]["Processor Variant"] == "7735HS"
    assert "Internal Memory" in spec_sections
    assert spec_sections["Internal Memory"]["Type of RAM"] == "DDR5"
    assert "Key Features" in spec_sections
    assert "Dolby Audio, Backlit Keyboard, Camera Privacy Shutter" in str(
        spec_sections["Key Features"].values()
    )
    assert "Overview" in spec_sections
    assert "AMD Ryzen 7" in spec_sections["Overview"]["Description"]

    # Verify retailer service/contact noise is strictly excluded
    assert "customer support number" not in parsed.attributes
    assert "customer support email" not in parsed.attributes
    assert "registered name and address" not in parsed.attributes
    assert "customer care contact person" not in parsed.attributes
    assert "Company Contact Information" not in spec_sections
    assert "Croma Service Promise" not in spec_sections
