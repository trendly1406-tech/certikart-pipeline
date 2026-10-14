"""Common extraction and parsing utilities shared across all retailer sources."""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from parsel import Selector

from categories.contracts import AttributeValue
from normalization import normalize_capacity_gb, normalize_text

PRICE_CLEAN_PATTERN = re.compile(r"[^\d.]")
PART_NUMBER_PATTERN = re.compile(
    r"\b(?=[A-Z0-9/-]{6,}\b)(?=[A-Z0-9/-]*[A-Z])(?=[A-Z0-9/-]*\d)"
    r"[A-Z0-9]+(?:[-/][A-Z0-9]+)+\b",
    re.IGNORECASE,
)

KNOWN_BRANDS: tuple[str, ...] = (
    "apple",
    "lenovo",
    "hp",
    "dell",
    "asus",
    "acer",
    "samsung",
    "msi",
    "infinix",
    "xiaomi",
    "redmibook",
    "realme",
    "honor",
    "microsoft",
    "lg",
    "primebook",
    "oneplus",
    "motorola",
    "nothing",
    "google",
    "sony",
    "boat",
    "noise",
    "boult",
)

IGNORED_SPEC_KEYS: frozenset[str] = frozenset(
    {
        "customer support number",
        "customer care number",
        "customer support email",
        "customer care email",
        "registered name and address",
        "manufacturer/importer/marketer name & address",
        "importer name & address",
        "manufacturer name & address",
        "customer care contact person",
        "grievance officer",
        "installation & demo applicable",
        "installation and demo applicable",
        "croma service promise",
        "spec_brand_url",
        "brand_url_pdp",
        "spec_viewmore_btn",
        "flipkart",
        "minutes",
        "grocery",
        "travel",
        "supercoin",
        "explore plus",
        "delivery details",
        "similar products",
        "trending products",
        "location not set",
        "name and address of the manufacturer",
        "name and address of the packer",
        "name and address of the importer",
    }
)


def is_ignored_spec_key(key: str) -> bool:
    """Check if specification key represents retailer contact/legal/promo noise."""
    k = normalize_text(key)
    if not k or len(k) < 2 or len(k) > 60:
        return True
    if k in IGNORED_SPEC_KEYS:
        return True
    if any(c in k for c in ("₹", "$", "\u20b9")):
        return True
    if re.match(r"^[\d,./_+-]+$", k):
        return True
    noise_patterns = (
        "customer support",
        "customer care",
        "grievance",
        "registered name",
        "service promise",
        "importer/marketer",
        "marketer name",
        "toll free",
        "installation & demo",
        "contact person",
        "delivery details",
        "similar products",
        "trending products",
        "location not set",
        "explore plus",
        "supercoin",
        "about us",
        "careers",
        "press",
        "stories",
        "help center",
        "corporate information",
        "mail us",
        "registered office",
    )
    return any(pattern in k for pattern in noise_patterns)


def extract_manufacturer_part_number(
    title: str,
    specs: dict[str, str] | None = None,
) -> str | None:
    """Read an explicit MPN/model code, with a conservative title fallback."""
    normalized_specs = specs or {}
    for key in (
        "manufacturer part number",
        "part number",
        "model number",
        "item model number",
        "mpn",
    ):
        value = normalized_specs.get(key)
        if value and value.strip():
            return value.strip()
    candidates = PART_NUMBER_PATTERN.findall(title)
    return candidates[-1].strip() if candidates else None


def extract_digits_to_paise(raw_text: str | None) -> int | None:
    """Extract numeric price string and convert to integer paise."""
    if not raw_text:
        return None
    # Reject discount percentages or rating badges (e.g. "8% off", "(2)")
    if ("%" in raw_text or "off" in raw_text.casefold()) and "₹" not in raw_text:
        return None
    cleaned = PRICE_CLEAN_PATTERN.sub("", raw_text)
    if not cleaned:
        return None
    try:
        amount_float = float(cleaned)
        # Reject single-digit badge numbers if no rupee symbol
        if amount_float < 50.0 and "₹" not in raw_text:
            return None
        return int(amount_float * 100)
    except ValueError:
        return None


def infer_brand(
    title: str,
    specs_brand: str | None = None,
    known_brands: tuple[str, ...] = KNOWN_BRANDS,
) -> str:
    """Infer brand from specs metadata or product title."""
    if specs_brand and specs_brand.strip():
        return specs_brand.strip().title()
    normalized_title = title.casefold()
    for brand in known_brands:
        if brand in normalized_title:
            return brand.title()
    parts = title.strip().split()
    return parts[0] if parts else "Generic"


def clean_capacity_str(raw: str) -> str:
    """Extract standard capacity expression (e.g. '16 GB', '512 GB', '1 TB') from raw string."""
    match = re.search(r"(\d+(?:\.\d+)?\s*(?:tb|gb))", raw, re.IGNORECASE)
    return match.group(1) if match else raw


def extract_json_ld_products(html_or_selector: str | Selector) -> list[dict[str, Any]]:
    """Extract all schema.org Product structures from JSON-LD script elements."""
    sel = Selector(text=html_or_selector) if isinstance(html_or_selector, str) else html_or_selector
    products: list[dict[str, Any]] = []

    for script_text in sel.css("script[type='application/ld+json']::text").getall():
        cleaned = script_text.strip()
        if not cleaned:
            continue
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("@type", "")).casefold()
            if item_type in ("product", "individualproduct"):
                products.append(item)
            elif "@graph" in item and isinstance(item["@graph"], list):
                for graph_item in item["@graph"]:
                    if isinstance(graph_item, dict) and str(
                        graph_item.get("@type", "")
                    ).casefold() in ("product", "individualproduct"):
                        products.append(graph_item)

    return products


def build_category_attributes(
    category: str,
    title: str,
    raw_specs: dict[str, str],
) -> dict[str, AttributeValue]:
    """Construct canonical domain attributes from retailer specifications."""
    attributes: dict[str, AttributeValue] = {}

    norm_specs = {normalize_text(k): v for k, v in raw_specs.items() if not is_ignored_spec_key(k)}

    # Laptop category extraction
    if category.casefold() == "laptop":
        # Extract RAM
        for ram_key in (
            "ram",
            "ram size",
            "system memory",
            "ram capacity",
            "internal memory",
            "ram memory installed",
            "ram memory maximum size",
            "memory",
        ):
            if ram_key in norm_specs:
                with contextlib.suppress(ValueError):
                    attributes["ram_gb"] = normalize_capacity_gb(
                        clean_capacity_str(norm_specs[ram_key])
                    )
                    break
        if "ram_gb" not in attributes:
            ram_match = re.search(
                r"(\d+\s*(?:GB|TB))\s+(?:RAM|DDR|LPDDR|Memory)", title, re.IGNORECASE
            )
            if ram_match:
                with contextlib.suppress(ValueError):
                    attributes["ram_gb"] = normalize_capacity_gb(ram_match.group(1))

        # Extract Storage
        for st_key in (
            "storage",
            "ssd capacity",
            "hard drive size",
            "hdd capacity",
            "ssd",
            "emmc capacity",
            "hard disk description",
            "hard disk size",
            "storage capacity",
            "hard drive capacity",
        ):
            if st_key in norm_specs:
                with contextlib.suppress(ValueError):
                    attributes["storage_gb"] = normalize_capacity_gb(
                        clean_capacity_str(norm_specs[st_key])
                    )
                    break
        if "storage_gb" not in attributes:
            st_match = re.search(
                r"(\d+\s*(?:GB|TB))\s+(?:SSD|HDD|ROM|NVMe|Storage|EMMC)", title, re.IGNORECASE
            )
            if st_match:
                with contextlib.suppress(ValueError):
                    attributes["storage_gb"] = normalize_capacity_gb(st_match.group(1))

        for st_type_key in (
            "hard disk description",
            "storage type",
            "drive type",
            "hard drive interface",
        ):
            if st_type_key in norm_specs:
                v = norm_specs[st_type_key].lower()
                if "emmc" in v:
                    attributes["storage_type"] = "eMMC"
                    break
                elif "ssd" in v or "nvme" in v:
                    attributes["storage_type"] = "SSD"
                    break
                elif "hdd" in v or "hard disk" in v:
                    attributes["storage_type"] = "HDD"
                    break
        if "storage_type" not in attributes:
            if "ssd capacity" in norm_specs or "ssd" in norm_specs or "ssd" in title.lower():
                attributes["storage_type"] = "SSD"
            elif "emmc" in title.lower() or "emmc capacity" in norm_specs:
                attributes["storage_type"] = "eMMC"
            elif "hdd" in title.lower() or "hdd capacity" in norm_specs:
                attributes["storage_type"] = "HDD"

        # Extract CPU
        if (
            "cpu model number" in norm_specs
            or "processor name" in norm_specs
            or "cpu model" in norm_specs
            or "processor type" in norm_specs
        ):
            p_name = (
                norm_specs.get("cpu model number")
                or norm_specs.get("processor name")
                or norm_specs.get("cpu model")
                or norm_specs.get("processor type", "")
            )
            p_var = norm_specs.get("processor variant", "")
            p_brand = norm_specs.get("processor brand", "")
            full_cpu = f"{p_name} {p_var}".strip()
            if p_brand and p_brand.lower() not in full_cpu.lower():
                full_cpu = f"{p_brand} {full_cpu}".strip()
            full_cpu = re.sub(r"\bprocessor\b", "", full_cpu, flags=re.IGNORECASE).strip()
            full_cpu = re.sub(r"\s+", " ", full_cpu)
            attributes["cpu_model"] = full_cpu
        else:
            for cpu_key in (
                "cpu model number",
                "processor type",
                "processor",
                "cpu",
                "processor variant",
                "processor brand",
                "processor speed",
                "chipset",
            ):
                if cpu_key in norm_specs:
                    attributes["cpu_model"] = norm_specs[cpu_key].strip()
                    break

        if "cpu_model" not in attributes:
            cpu_match = re.search(
                r"\b(Apple\s+M[1-5](?:\s+(?:Pro|Max|Ultra))?|M[1-5](?:\s+(?:Pro|Max|Ultra))?(?:\s+chip)?|Intel\s+Core\s+(?:Ultra\s+)?[i3579]-?\w*|AMD\s+Ryzen\s+[3579]-?\w+|Snapdragon\s+X\s+\w+|MediaTek\s+\w+)\b",
                title,
                re.IGNORECASE,
            )
            if cpu_match:
                raw_cpu = cpu_match.group(1).strip()
                attributes["cpu_model"] = re.sub(
                    r"\s+chip$", "", raw_cpu, flags=re.IGNORECASE
                ).strip()
            elif "macbook" in title.casefold() or "apple" in title.casefold():
                m_match = re.search(r"\b(M[1-5](?:\s+(?:Pro|Max|Ultra))?)\b", title, re.IGNORECASE)
                if m_match:
                    attributes["cpu_model"] = f"Apple {m_match.group(1).strip()}"

        # Extract GPU
        for gpu_key in (
            "graphic processor",
            "graphics processor",
            "graphics coprocessor",
            "graphics card description",
            "gpu",
            "gpu model",
            "graphics",
            "video processor",
            "graphics description",
            "dedicated graphic memory capacity",
            "dedicated graphic card",
        ):
            if gpu_key in norm_specs:
                attributes["gpu_model"] = norm_specs[gpu_key].strip()
                break
        if "gpu_model" not in attributes:
            gpu_match = re.search(
                r"\b(NVIDIA\s+GeForce\s+RTX\s+\d+(?:\s*Ti)?|NVIDIA\s+RTX\s+\d+|Intel\s+Arc\s+\w+|Intel\s+Iris\s+Xe|AMD\s+Radeon\s+\w+|Intel\s+iGPU)\b",
                title,
                re.IGNORECASE,
            )
            if gpu_match:
                attributes["gpu_model"] = gpu_match.group(1).strip()
            elif "apple" in title.casefold() or "macbook" in title.casefold():
                m_gpu_match = re.search(
                    r"\b(M[1-5](?:\s+(?:Pro|Max|Ultra))?)\b", title, re.IGNORECASE
                )
                if m_gpu_match:
                    attributes["gpu_model"] = f"Apple {m_gpu_match.group(1).strip()} GPU"

        # Extract Screen Size
        for sc_key in (
            "screen size",
            "display size",
            "screen size (in inches)",
            "display size (in inches)",
            "display size (in cms)",
            "display",
        ):
            if sc_key in norm_specs:
                sc_m = re.search(r"(\d{1,2}(?:\.\d{1,2})?)", norm_specs[sc_key])
                if sc_m:
                    with contextlib.suppress(ValueError):
                        val = float(sc_m.group(1))
                        if val > 24.0:
                            val = round(val / 2.54, 1)
                        if 9.0 <= val <= 24.0:
                            attributes["screen_size_inches"] = val
                            break
        if "screen_size_inches" not in attributes:
            sc_match = re.search(r"(\d{2}(?:\.\d{1,2})?)\s*(?:inch|inches|\"|-inch)", title, re.I)
            if sc_match:
                with contextlib.suppress(ValueError):
                    attributes["screen_size_inches"] = float(sc_match.group(1))

        # Additional Laptop fields
        for rt_key in (
            "ram type",
            "type of ram",
            "memory technology",
            "ram memory technology",
            "system ram type",
        ):
            if rt_key in norm_specs:
                rt_val = norm_specs[rt_key].strip()
                if "ddr5" in rt_val.lower():
                    attributes["ram_type"] = "DDR5"
                elif "ddr4" in rt_val.lower():
                    attributes["ram_type"] = "DDR4"
                elif "lpddr5" in rt_val.lower():
                    attributes["ram_type"] = "LPDDR5"
                elif "lpddr4" in rt_val.lower():
                    attributes["ram_type"] = "LPDDR4"
                elif "unified" in rt_val.lower():
                    attributes["ram_type"] = "Unified Memory"
                else:
                    attributes["ram_type"] = rt_val.upper()
                break

        if "operating system" in norm_specs:
            attributes["operating_system"] = norm_specs["operating system"].strip()
        elif "os" in norm_specs:
            attributes["operating_system"] = norm_specs["os"].strip()

        if "brand color" in norm_specs:
            attributes["color"] = norm_specs["brand color"].strip()
        elif "colour" in norm_specs:
            attributes["color"] = norm_specs["colour"].strip()
        elif "color" in norm_specs:
            attributes["color"] = norm_specs["color"].strip()

        if "display type" in norm_specs:
            dt_val = norm_specs["display type"].lower()
            if "oled" in dt_val:
                attributes["display_type"] = "OLED"
            elif "ips" in dt_val:
                attributes["display_type"] = "IPS LCD"
            elif "led" in dt_val:
                attributes["display_type"] = "LED"

        if "backlit keyboard" in norm_specs:
            b_val = norm_specs["backlit keyboard"].lower()
            if b_val in ("yes", "true", "1"):
                attributes["keyboard_backlight"] = True
            elif b_val in ("no", "false", "0"):
                attributes["keyboard_backlight"] = False
        elif (
            any(
                "backlit" in str(norm_specs.get(k, "")).lower()
                for k in (
                    "type of keyboard",
                    "keyboard description",
                    "keyboard",
                    "other special features of the product",
                    "special features",
                )
            )
            or "backlit" in title.lower()
        ):
            attributes["keyboard_backlight"] = True

        for cam_key in (
            "web camera",
            "camera resolution",
            "camera",
            "webcam",
            "laptop camera type",
            "other special features of the product",
        ):
            if cam_key in norm_specs:
                w_val = norm_specs[cam_key].lower()
                if "1080p" in w_val or "fhd" in w_val:
                    attributes["webcam_resolution"] = "1080p FHD"
                    break
                elif "720p" in w_val or "hd" in w_val:
                    attributes["webcam_resolution"] = "720p HD"
                    break

        for w_key in ("weight", "product weight", "item weight", "package weight"):
            if w_key in norm_specs:
                raw_w = norm_specs[w_key].lower()
                kg_g_m = re.search(r"(\d+)\s*kg\s*(\d+)\s*g", raw_w)
                if kg_g_m:
                    with contextlib.suppress(ValueError):
                        weight_kg_val = round(
                            int(kg_g_m.group(1)) + int(kg_g_m.group(2)) / 1000.0, 2
                        )
                        if 0.5 <= weight_kg_val <= 10.0:
                            attributes["weight_kg"] = weight_kg_val
                            break
                w_m = re.search(r"(\d+(?:\.\d+)?)\s*kg", raw_w)
                if w_m:
                    with contextlib.suppress(ValueError):
                        weight_float = float(w_m.group(1))
                        if 0.5 <= weight_float <= 10.0:
                            attributes["weight_kg"] = weight_float
                            break

        for b_key in (
            "battery cell",
            "battery",
            "battery capacity",
            "standard battery life",
            "laptop battery",
            "lithium battery energy content",
        ):
            if b_key in norm_specs:
                b_m = re.search(
                    r"(\d{2,3}(?:\.\d)?)\s*(?:wh|watt\s*hours)", norm_specs[b_key].lower()
                )
                if b_m:
                    with contextlib.suppress(ValueError):
                        battery_wh_float = float(b_m.group(1))
                        if 10.0 <= battery_wh_float <= 150.0:
                            attributes["battery_wh"] = battery_wh_float
                            break

        for wlan_key in (
            "wireless lan",
            "wifi specifications",
            "wi-fi specifications",
            "network connectivity",
            "wi-fi generation",
            "computer wireless type",
            "wireless technology",
        ):
            if wlan_key in norm_specs:
                w_lan = norm_specs[wlan_key].lower()
                if "wi-fi 7" in w_lan or "wifi 7" in w_lan or "802.11be" in w_lan:
                    attributes["wifi_standard"] = "Wi-Fi 7"
                    break
                elif "wi-fi 6e" in w_lan or "wifi 6e" in w_lan:
                    attributes["wifi_standard"] = "Wi-Fi 6E"
                    break
                elif (
                    "wi-fi 6" in w_lan or "wifi 6" in w_lan or "802.11ax" in w_lan or "ax" in w_lan
                ):
                    attributes["wifi_standard"] = "Wi-Fi 6"
                    break
                elif "wi-fi 5" in w_lan or "wifi 5" in w_lan or "802.11ac" in w_lan:
                    attributes["wifi_standard"] = "Wi-Fi 5"
                    break

        for res_key in (
            "screen resolution",
            "screen resolution type",
            "display resolution",
            "native resolution",
            "maximum display resolution",
            "scanner resolution",
            "additional screen specifications",
        ):
            if res_key in norm_specs:
                r_val = norm_specs[res_key].lower()
                if "4k" in r_val or "3840" in r_val:
                    attributes["display_resolution"] = "4K UHD"
                    break
                if "2.8k" in r_val or "2880" in r_val:
                    attributes["display_resolution"] = "2.8K"
                    break
                if "2.5k" in r_val or "2560" in r_val:
                    attributes["display_resolution"] = "2.5K QHD"
                    break
                if "wuxga" in r_val or "1920 x 1200" in r_val or "1920x1200" in r_val:
                    attributes["display_resolution"] = "WUXGA"
                    break
                if "1080" in r_val or "fhd" in r_val or "full hd" in r_val:
                    attributes["display_resolution"] = "FHD"
                    break
                if "720" in r_val or "hd" in r_val:
                    attributes["display_resolution"] = "HD"
                    break

        if "manufacturer part number" in norm_specs:
            attributes["mpn"] = norm_specs["manufacturer part number"].strip()
        elif "part number" in norm_specs:
            attributes["mpn"] = norm_specs["part number"].strip()

        if "model number" in norm_specs:
            attributes["model_number"] = norm_specs["model number"].strip()
        elif "model name" in norm_specs:
            attributes["model_number"] = norm_specs["model name"].strip()

        if "asin" in norm_specs:
            attributes["asin"] = norm_specs["asin"].strip()

        for w_desc_key in ("warranty description", "warranty", "warranty type"):
            if w_desc_key in norm_specs:
                attributes["warranty"] = norm_specs[w_desc_key].strip()
                break

    # Mobile category extraction
    elif category.casefold() in ("mobile", "smartphone", "phone"):
        title_lower = title.casefold()

        # 1. RAM Extraction
        for ram_key in (
            "ram",
            "ram capacity",
            "internal memory",
            "ram memory installed size",
            "system memory",
            "installed ram",
            "system ram type",
        ):
            if ram_key in norm_specs:
                with contextlib.suppress(ValueError):
                    attributes["ram_gb"] = normalize_capacity_gb(
                        clean_capacity_str(norm_specs[ram_key])
                    )
                    break
        if "ram_gb" not in attributes:
            ram_match = re.search(r"\b([23468]|12|16|24)\s*gb(?:\s+ram)?\b", title_lower)
            if ram_match:
                with contextlib.suppress(ValueError):
                    attributes["ram_gb"] = int(ram_match.group(1))

        # 2. Storage Extraction
        for st_key in (
            "internal storage",
            "storage",
            "rom",
            "storage capacity",
            "memory storage capacity",
            "capacity",
        ):
            if st_key in norm_specs:
                with contextlib.suppress(ValueError):
                    attributes["storage_gb"] = normalize_capacity_gb(
                        clean_capacity_str(norm_specs[st_key])
                    )
                    break
        if "storage_gb" not in attributes:
            if "1tb" in title_lower or "1 tb" in title_lower:
                attributes["storage_gb"] = 1024
            else:
                st_match = re.search(
                    r"\b(32|64|128|256|512)\s*gb(?:\s+(?:rom|storage|internal\s+storage))?\b",
                    title_lower,
                )
                if st_match:
                    with contextlib.suppress(ValueError):
                        attributes["storage_gb"] = int(st_match.group(1))

        # 3. Chipset / Processor Extraction
        p_brand = norm_specs.get("processor brand", "").strip()
        if p_brand.lower() in ("not available", "na", "null", "none"):
            p_brand = ""
        p_name = (
            norm_specs.get("processor name")
            or norm_specs.get("processor type")
            or norm_specs.get("processor")
            or norm_specs.get("chipset")
            or norm_specs.get("cpu model")
            or ""
        ).strip()
        if p_name.lower() in ("not available", "na", "null", "none"):
            p_name = norm_specs.get("processor", "").strip()
            if p_name.lower() in ("not available", "na", "null", "none"):
                p_name = ""

        if p_name:
            full_proc = (
                f"{p_brand} {p_name}".strip()
                if (p_brand and p_brand.lower() not in p_name.lower())
                else p_name
            )
            full_proc = re.sub(
                r"\b(?:processor|octa-core|octa core|quad-core|quad core|"
                r"dual-core|dual core|[\d.]+\s*ghz)\b",
                "",
                full_proc,
                flags=re.IGNORECASE,
            ).strip()
            full_proc = re.sub(r",\s*$", "", full_proc).strip()
            full_proc = re.sub(r"\s+", " ", full_proc)
            if full_proc and full_proc.lower() not in ("not available", "na"):
                attributes["chipset"] = full_proc
        if "chipset" not in attributes:
            apple_a = re.search(r"\b(a1[4-9](?:\s+pro)?(?:\s+bionic)?(?:\s+chip)?)\b", title_lower)
            if apple_a:
                attributes["chipset"] = re.sub(
                    r"\s+chip$", "", apple_a.group(1).title(), flags=re.IGNORECASE
                ).strip()
            else:
                chip_m = re.search(
                    r"\b(Snapdragon\s+(?:8\s+Gen\s+[1-5]|7\s+Gen\s+[1-4]|6s?\s+Gen\s+\d|4\s+Gen\s+\d|8[5-8]\d|7\d{2}[a-z]?|\d{3,4})|Dimensity\s+(?:9\d{3}|8\d{3}|7\d{3}|6\d{3}|[1-9]\d{2}[a-z]?)|Tensor\s+G[1-5]|Unisoc\s+(?:T\d{3,4}|\w+)|Helio\s+[A-Z]\d{2,3})\b",
                    title,
                    re.IGNORECASE,
                )
                if chip_m:
                    attributes["chipset"] = chip_m.group(1).strip()

        # 4. Color
        for col_key in ("brand color", "color", "colour"):
            if col_key in norm_specs:
                attributes["color"] = norm_specs[col_key].strip()
                break
        if "color" not in attributes:
            colors = (
                "desert titanium",
                "natural titanium",
                "white titanium",
                "black titanium",
                "titanium gray",
                "titanium black",
                "phantom black",
                "marble gray",
                "cobalt violet",
                "amber yellow",
                "onyx black",
                "obsidian",
                "porcelain",
                "hazel",
                "bay",
                "rose",
                "midnight",
                "starlight",
                "space black",
                "space gray",
                "emerald green",
                "silver",
                "gold",
                "black",
                "blue",
                "green",
                "purple",
                "pink",
                "white",
                "yellow",
            )
            for c in colors:
                if re.search(rf"\b{re.escape(c)}\b", title_lower):
                    attributes["color"] = c.title()
                    break

        # 5. Network Type
        for net_key in (
            "cellular technology",
            "network type",
            "supported networks",
            "network connectivity",
        ):
            if net_key in norm_specs:
                net_val = norm_specs[net_key].upper()
                if "5G" in net_val:
                    attributes["network_type"] = "5G"
                    break
                elif "4G" in net_val or "LTE" in net_val:
                    attributes["network_type"] = "4G"
                    break
        if "network_type" not in attributes:
            attributes["network_type"] = "5G" if "5g" in title_lower else "4G"

        # 6. Screen Size (Inches)
        for sc_key in (
            "screen size in inches",
            "screen size (in inches)",
            "display size (in inches)",
            "display size",
            "screen size",
            "screen size in cm",
            "display size in cm",
        ):
            if sc_key in norm_specs:
                sc_raw = norm_specs[sc_key]
                inch_m = re.search(
                    r"(\d+(?:\.\d+)?)\s*(?:in|inch|inches|\"|″|-inch)", sc_raw, re.IGNORECASE
                )
                if inch_m:
                    with contextlib.suppress(ValueError):
                        val = float(inch_m.group(1))
                        if 1.5 <= val <= 8.5:
                            attributes["screen_size_inches"] = val
                            break
                # Check for cm pattern
                cm_m = re.search(r"(\d+(?:\.\d+)?)\s*cm", sc_raw, re.IGNORECASE)
                if cm_m:
                    with contextlib.suppress(ValueError):
                        cm_val = float(cm_m.group(1))
                        val = round(cm_val / 2.54, 2)
                        if 1.5 <= val <= 8.5:
                            attributes["screen_size_inches"] = val
                            break
                num_m = re.search(r"^(\d+(?:\.\d+)?)$", sc_raw.strip())
                if num_m:
                    with contextlib.suppress(ValueError):
                        val = float(num_m.group(1))
                        if 1.5 <= val <= 8.5:
                            attributes["screen_size_inches"] = val
                            break
                        elif val > 8.5:
                            val_cm = round(val / 2.54, 2)
                            if 1.5 <= val_cm <= 8.5:
                                attributes["screen_size_inches"] = val_cm
                                break
        if "screen_size_inches" not in attributes:
            sc_m = re.search(
                r"([2-7](?:\.[0-9]{1,2})?)\s*(?:in|inch|inches|\"|″|-inch)", title_lower
            )
            if sc_m:
                with contextlib.suppress(ValueError):
                    attributes["screen_size_inches"] = float(sc_m.group(1))
            else:
                cm_t = re.search(r"(\d{1,2}(?:\.\d{1,2})?)\s*cm", title_lower)
                if cm_t:
                    with contextlib.suppress(ValueError):
                        cm_f = float(cm_t.group(1))
                        val = round(cm_f / 2.54, 2)
                        if 1.5 <= val <= 8.5:
                            attributes["screen_size_inches"] = val

        # 7. Display Type
        for dt_key in ("display type", "screen type", "resolution type", "panel type"):
            if dt_key in norm_specs:
                dt_v = norm_specs[dt_key].lower()
                if "super amoled" in dt_v:
                    attributes["display_type"] = "Super AMOLED"
                    break
                elif "amoled" in dt_v:
                    attributes["display_type"] = "AMOLED"
                    break
                elif "super retina" in dt_v or "xdr" in dt_v:
                    attributes["display_type"] = "Super Retina XDR"
                    break
                elif "oled" in dt_v:
                    attributes["display_type"] = "OLED"
                    break
                elif "ips" in dt_v:
                    attributes["display_type"] = "IPS LCD"
                    break
                elif "lcd" in dt_v:
                    attributes["display_type"] = "LCD"
                    break
        if "display_type" not in attributes:
            if "super retina" in title_lower or "xdr" in title_lower:
                attributes["display_type"] = "Super Retina XDR"
            elif "amoled" in title_lower:
                attributes["display_type"] = "AMOLED"
            elif "oled" in title_lower:
                attributes["display_type"] = "OLED"

        # 8. Refresh Rate (Hz)
        for rf_key in ("refresh rate", "standard refresh rate", "display refresh rate"):
            if rf_key in norm_specs:
                rf_m = re.search(r"\b(60|90|120|144|165)\b", norm_specs[rf_key])
                if rf_m:
                    with contextlib.suppress(ValueError):
                        attributes["refresh_rate_hz"] = int(rf_m.group(1))
                        break
        if "refresh_rate_hz" not in attributes:
            rf_m = re.search(r"\b(60|90|120|144|165)\s*hz\b", title_lower)
            if rf_m:
                with contextlib.suppress(ValueError):
                    attributes["refresh_rate_hz"] = int(rf_m.group(1))

        # 9. Primary Camera (MP)
        for cam_key in (
            "camera",
            "primary camera",
            "rear camera",
            "main camera",
            "rear camera setup",
        ):
            if cam_key in norm_specs:
                cam_spec_str = norm_specs[cam_key].lower()
                all_mps = re.findall(r"\b(\d{1,3})\s*(?:mp|megapixel|mega pixel)\b", cam_spec_str)
                if not all_mps:
                    all_mps = re.findall(r"\b(200|108|64|50|48|32|13|12|8|5)\b", cam_spec_str)
                if all_mps:
                    valid_mps = [int(x) for x in all_mps if 5 <= int(x) <= 200]
                    if valid_mps:
                        attributes["primary_camera_mp"] = max(valid_mps)
                        break
        if "primary_camera_mp" not in attributes:
            cam_m = re.search(r"\b(200|108|64|50|48|32|13|12|8|5)\s*mp\b", title_lower)
            if cam_m:
                with contextlib.suppress(ValueError):
                    attributes["primary_camera_mp"] = int(cam_m.group(1))

        # 10. Front Camera (MP)
        for fcam_key in (
            "secondary camera",
            "front camera",
            "front camera setup",
            "selfie camera",
        ):
            if fcam_key in norm_specs:
                fcam_spec_str = norm_specs[fcam_key].lower()
                all_mps = re.findall(r"\b(\d{1,3})\s*(?:mp|megapixel|mega pixel)\b", fcam_spec_str)
                if not all_mps:
                    all_mps = re.findall(r"\b(50|32|20|16|13|12|8|5)\b", fcam_spec_str)
                if all_mps:
                    valid_mps = [int(x) for x in all_mps if 4 <= int(x) <= 64]
                    if valid_mps:
                        attributes["front_camera_mp"] = max(valid_mps)
                        break

        # 11. Battery (mAh)
        for b_key in ("battery capacity", "battery", "battery size"):
            if b_key in norm_specs:
                b_m = re.search(r"(\d{3,5})\s*mah", norm_specs[b_key].lower())
                if not b_m:
                    b_m = re.search(r"\b([1-9][0-9]{3})\b", norm_specs[b_key])
                if b_m:
                    with contextlib.suppress(ValueError):
                        bat_val = int(b_m.group(1))
                        if 500 <= bat_val <= 15000:
                            attributes["battery_mah"] = bat_val
                            break
        if "battery_mah" not in attributes:
            b_m = re.search(r"\b([1-9][0-9]{3})\s*mah\b", title_lower)
            if b_m:
                with contextlib.suppress(ValueError):
                    attributes["battery_mah"] = int(b_m.group(1))

        # 12. Fast Charging (Watts)
        for ch_key in (
            "additional charging features",
            "fast charging capability",
            "quick charging",
            "charging speed",
        ):
            if ch_key in norm_specs:
                ch_m = re.search(r"(\d{2,3})\s*w", norm_specs[ch_key].lower())
                if ch_m:
                    with contextlib.suppress(ValueError):
                        attributes["fast_charging_w"] = int(ch_m.group(1))
                        break
        if "fast_charging_w" not in attributes:
            ch_m = re.search(
                r"\b(\d{2,3})\s*w\s*(?:charging|fast|supervooc|hypercharge|turbopower|dart)?\b",
                title_lower,
            )
            if ch_m:
                with contextlib.suppress(ValueError):
                    attributes["fast_charging_w"] = int(ch_m.group(1))

        # 13. Operating System
        for os_key in ("os name & version", "operating system", "os type", "os"):
            if os_key in norm_specs:
                attributes["operating_system"] = norm_specs[os_key].strip()
                break
        if "operating_system" not in attributes:
            if "iphone" in title_lower or "apple" in title_lower or "ios" in title_lower:
                attributes["operating_system"] = "iOS"
            else:
                attributes["operating_system"] = "Android"

        # 14. Resolution Standard
        for res_key in ("resolution", "screen resolution", "resolution type"):
            if res_key in norm_specs:
                r_v = norm_specs[res_key].lower()
                if "4k" in r_v or "3840" in r_v:
                    attributes["resolution_standard"] = "4K UHD"
                    break
                elif "qhd+" in r_v or "2560" in r_v or "3120" in r_v:
                    attributes["resolution_standard"] = "QHD+"
                    break
                elif "1.5k" in r_v or "1220" in r_v or "1272" in r_v:
                    attributes["resolution_standard"] = "1.5K"
                    break
                elif "fhd+" in r_v or "full hd+" in r_v or "2400" in r_v or "1080" in r_v:
                    attributes["resolution_standard"] = "FHD+"
                    break
                elif "hd+" in r_v or "1600" in r_v or "720" in r_v:
                    attributes["resolution_standard"] = "HD+"
                    break

        # 15. Camera Setup
        for csetup_key in (
            "rear camera configuration",
            "main camera setup",
            "dual camera lens",
            "camera setup",
        ):
            if csetup_key in norm_specs:
                cs_v = norm_specs[csetup_key].lower()
                if "quad" in cs_v:
                    attributes["camera_setup"] = "Quad Camera"
                    break
                elif "triple" in cs_v:
                    attributes["camera_setup"] = "Triple Camera"
                    break
                elif "dual" in cs_v:
                    attributes["camera_setup"] = "Dual Camera"
                    break
                elif "single" in cs_v:
                    attributes["camera_setup"] = "Single Camera"
                    break

        # 16. OIS Supported
        for ois_key in ("camera features", "primary camera features", "camera"):
            if ois_key in norm_specs and (
                "ois" in norm_specs[ois_key].lower()
                or "optical image" in norm_specs[ois_key].lower()
            ):
                attributes["ois_supported"] = True
                break
        if "ois_supported" not in attributes and "ois" in title_lower:
            attributes["ois_supported"] = True

        # 17. Water Resistance / IP Rating
        for ip_key in ("ip rating", "water resistance", "resistance type"):
            if ip_key in norm_specs:
                ip_m = re.search(r"\b(ip6[89]|ip6[57]|ip5[45]|ip53)\b", norm_specs[ip_key].lower())
                if ip_m:
                    attributes["water_resistance_rating"] = ip_m.group(1).upper()
                    break
        if "water_resistance_rating" not in attributes:
            ip_m = re.search(r"\b(ip6[89]|ip6[57]|ip5[45]|ip53)\b", title_lower)
            if ip_m:
                attributes["water_resistance_rating"] = ip_m.group(1).upper()

        # 18. Screen Protection
        for prot_key in ("screen protection", "protection", "glass protection"):
            if prot_key in norm_specs:
                attributes["screen_protection"] = norm_specs[prot_key].strip()
                break
        if "screen_protection" not in attributes:
            if "ceramic shield" in title_lower or "iphone" in title_lower:
                attributes["screen_protection"] = "Ceramic Shield"
            elif "victus 2" in title_lower:
                attributes["screen_protection"] = "Gorilla Glass Victus 2"
            elif "victus" in title_lower:
                attributes["screen_protection"] = "Gorilla Glass Victus"

        # 19. Biometrics
        for bio_key in ("fingerprint sensor", "security", "lock", "sensors"):
            if bio_key in norm_specs:
                b_v = norm_specs[bio_key].lower()
                if "in-display" in b_v or "in display" in b_v:
                    attributes["biometrics"] = "In-Display Fingerprint"
                    break
                elif "side-mounted" in b_v or "side fingerprint" in b_v:
                    attributes["biometrics"] = "Side Fingerprint"
                    break
                elif "face id" in b_v:
                    attributes["biometrics"] = "Face ID"
                    break
                elif "fingerprint" in b_v:
                    attributes["biometrics"] = "Fingerprint Sensor"
                    break

        # 20. Audio Jack (3.5mm)
        for aj_key in ("audio jack", "audio jack port", "headphone jack"):
            if aj_key in norm_specs:
                aj_v = norm_specs[aj_key].lower()
                if "3.5" in aj_v or "yes" in aj_v:
                    attributes["audio_jack_3_5mm"] = True
                elif "no" in aj_v or "type-c" in aj_v or "usb" in aj_v:
                    attributes["audio_jack_3_5mm"] = False
                break

        # 21. SIM Type
        for sim_key in (
            "sim type",
            "number of sims supported",
            "secondary sim type",
            "dual sim mode",
        ):
            if sim_key in norm_specs:
                attributes["sim_type"] = norm_specs[sim_key].strip()
                break

        # 22. Weight (Grams)
        for w_key in ("product weight", "weight", "item weight"):
            if w_key in norm_specs:
                w_m = re.search(r"(\d+(?:\.\d+)?)\s*g", norm_specs[w_key].lower())
                if w_m:
                    with contextlib.suppress(ValueError):
                        wt_val = float(w_m.group(1))
                        if 50.0 <= wt_val <= 600.0:
                            attributes["weight_grams"] = wt_val
                            break

        # 23. Identifiers & Part Numbers
        for mpn_key in (
            "model number",
            "item model number",
            "model series",
            "part number",
            "specialsku",
        ):
            if mpn_key in norm_specs:
                attributes["mpn"] = norm_specs[mpn_key].strip()
                attributes["model_number"] = norm_specs[mpn_key].strip()
                break

        if "asin" in norm_specs:
            attributes["asin"] = norm_specs["asin"].strip()

        for gtin_key in ("ean", "gtin", "upc"):
            if gtin_key in norm_specs:
                attributes["gtin"] = norm_specs[gtin_key].strip()
                attributes["ean"] = norm_specs[gtin_key].strip()
                break

        for w_key in (
            "warranty summary",
            "warranty on main product",
            "warranty description",
            "warranty",
        ):
            if w_key in norm_specs:
                attributes["warranty"] = norm_specs[w_key].strip()
                break

    return attributes


def build_spec_sections(
    raw_specs: dict[str, str],
    sections: dict[str, dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    """Keep source specifications once, grouped for display and provenance."""
    cleaned_sections: dict[str, dict[str, str]] = {}
    represented: set[tuple[str, str]] = set()

    for section_name, values in (sections or {}).items():
        clean_values: dict[str, str] = {}
        for key, value in values.items():
            clean_key = str(key).strip()
            clean_value = str(value).strip()
            if not clean_key or not clean_value or is_ignored_spec_key(clean_key):
                continue
            clean_values[clean_key] = clean_value
            represented.add((normalize_text(clean_key), normalize_text(clean_value)))
        if clean_values:
            cleaned_sections[str(section_name).strip()] = clean_values

    ungrouped: dict[str, str] = {}
    for key, value in raw_specs.items():
        clean_key = str(key).strip()
        clean_value = str(value).strip()
        identity = (normalize_text(clean_key), normalize_text(clean_value))
        if (
            clean_key
            and clean_value
            and not is_ignored_spec_key(clean_key)
            and identity not in represented
        ):
            ungrouped[clean_key] = clean_value
    if ungrouped:
        cleaned_sections["Other Specifications"] = ungrouped

    return cleaned_sections
