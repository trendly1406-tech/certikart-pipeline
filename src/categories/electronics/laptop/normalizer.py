"""Domain-specific identity normalizers for extracting canonical laptop product fingerprints."""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, ClassVar

from categories.contracts import AttributeValue

if TYPE_CHECKING:
    from matching.fingerprint import ProductFingerprint


class LaptopIdentityNormalizer:
    """Extracts structured canonical identity from messy retailer laptop titles."""

    KNOWN_BRANDS: ClassVar[tuple[str, ...]] = (
        "apple",
        "asus",
        "lenovo",
        "hp",
        "dell",
        "acer",
        "samsung",
        "msi",
        "primebook",
        "infinix",
        "xiaomi",
        "realme",
        "honor",
        "microsoft",
        "lg",
        "avita",
        "zebronics",
    )

    BRAND_ALIASES: ClassVar[dict[str, str]] = {
        "hewlett packard": "hp",
        "hewlett-packard": "hp",
        "lenovo (india)": "lenovo",
        "apple inc": "apple",
        "dell inc": "dell",
        "acer inc": "acer",
        "asus inc": "asus",
        "asustek": "asus",
    }

    PRODUCT_FAMILIES: ClassVar[tuple[tuple[str, str], ...]] = (
        # (Pattern, Canonical Family)
        (r"macbook\s+air", "macbook air"),
        (r"macbook\s+pro", "macbook pro"),
        (r"macbook\s+neo", "macbook neo"),
        (r"macbook", "macbook"),
        (r"rog\s+strix", "rog strix"),
        (r"rog\s+zephyrus", "rog zephyrus"),
        (r"tuf\s+(?:gaming\s+)?[af]\d+", "tuf gaming"),
        (r"tuf\s+gaming", "tuf gaming"),
        (r"vivobook\s+s\d+", "vivobook s"),
        (r"vivobook\s+pro", "vivobook pro"),
        (r"vivobook\s+go", "vivobook go"),
        (r"vivobook\s+\d+", "vivobook"),
        (r"vivobook", "vivobook"),
        (r"zenbook", "zenbook"),
        (r"expertbook", "expertbook"),
        (r"victus", "victus"),
        (r"omen\s+\d+", "omen"),
        (r"omen", "omen"),
        (r"omnibook\s+\d+", "omnibook"),
        (r"omnibook", "omnibook"),
        (r"pavilion\s+plus", "pavilion plus"),
        (r"pavilion\s+aero", "pavilion aero"),
        (r"pavilion", "pavilion"),
        (r"envy", "envy"),
        (r"spectre", "spectre"),
        (r"elitebook", "elitebook"),
        (r"probook", "probook"),
        (r"\bhp\s+1[45]s?\b", "hp 15"),
        (r"\bhp\s+25[05]\b", "hp 255"),
        (r"\bdell\s+1[45]\b", "inspiron"),
        (r"ideapad(?:\s+slim)?(?:\s+gaming)?(?:\s+pro)?(?:\s+\d+)?", "ideapad"),
        (r"thinkpad", "thinkpad"),
        (r"thinkbook\s+\d+", "thinkbook"),
        (r"thinkbook", "thinkbook"),
        (r"loq", "loq"),
        (r"legion\s+(?:pro\s+)?\d+", "legion"),
        (r"legion", "legion"),
        (r"yoga\s+(?:slim\s+)?\d+", "yoga"),
        (r"yoga", "yoga"),
        (r"nitro\s+v\s*\d*", "nitro"),
        (r"nitro\s*\d*", "nitro"),
        (r"nitro", "nitro"),
        (r"\blenovo\s+v\s*1[45]\b", "v15"),
        (r"\bv1[45]\s+g\d+\b", "v15"),
        (r"inspiron\s+\d+", "inspiron"),
        (r"inspiron", "inspiron"),
        (r"latitude\s+\d+", "latitude"),
        (r"latitude", "latitude"),
        (r"vostro\s+\d+", "vostro"),
        (r"vostro", "vostro"),
        (r"xps\s+\d+", "xps"),
        (r"xps", "xps"),
        (r"alienware", "alienware"),
        (r"dell\s+g\s*(?:series|\d+)", "g series"),
        (r"aspire\s+lite", "aspire lite"),
        (r"aspire\s+\d+", "aspire"),
        (r"aspire", "aspire"),
        (r"predator\s+helios", "predator helios"),
        (r"predator", "predator"),
        (r"swift\s+(?:go\s+)?\d+", "swift"),
        (r"swift", "swift"),
        (r"travellite", "travellite"),
        (r"travelmate", "travelmate"),
        (r"extensa", "extensa"),
        (r"chromebook", "chromebook"),
        (r"galaxy\s+book\s*\d*(?:\s*pro)?(?:\s*360)?", "galaxy book"),
        (r"primebook\s*[a-z0-9]*", "primebook"),
    )

    @classmethod
    def extract_brand(cls, title: str, specs_brand: str | None = None) -> str:
        if specs_brand:
            clean_specs = specs_brand.strip().casefold()
            clean_specs = cls.BRAND_ALIASES.get(clean_specs, clean_specs)
            if clean_specs in cls.KNOWN_BRANDS:
                return clean_specs

        title_lower = title.casefold()
        for alias, canonical in cls.BRAND_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", title_lower):
                return canonical
        for brand in cls.KNOWN_BRANDS:
            if re.search(rf"\b{re.escape(brand)}\b", title_lower):
                return brand
        return title.split()[0].casefold() if title.split() else "unknown"

    @classmethod
    def extract_family(cls, title: str, model_name: str = "") -> str | None:
        combined = f"{model_name} {title}".casefold()
        for pattern, canonical in cls.PRODUCT_FAMILIES:
            if re.search(pattern, combined):
                return canonical
        return None

    @classmethod
    def extract_chip(cls, title: str, specs: dict[str, str] | None = None) -> str | None:
        # Check rich specs first if present (contains exact processor name / variant / cpu_model)
        if specs:
            if "cpu_model" in specs and specs["cpu_model"].strip():
                return specs["cpu_model"].casefold().strip()
            if "processor name" in specs and specs["processor name"].strip():
                p_name = specs["processor name"].strip()
                p_var = specs.get("processor variant", "").strip()
                p_brand = specs.get("processor brand", "").strip()
                full_p = f"{p_name} {p_var}".strip()
                if p_brand and p_brand.lower() not in full_p.lower():
                    full_p = f"{p_brand} {full_p}".strip()
                return full_p.casefold()

        title_lower = title.casefold()

        # 1. Apple Silicon M-series & A-series
        apple_match = re.search(r"\b((?:m[1-5]|a1[7-9])(?:\s+(?:pro|max|ultra))?)\b", title_lower)
        if apple_match:
            return apple_match.group(1).strip()

        # 2. Intel Core Ultra / Intel Core 3/5/7 series
        intel_ultra = re.search(
            r"\b(intel\s+core\s+ultra\s+[579](?:\s+\d{3,4}[a-z]*)?|core\s+ultra\s+[579](?:\s+\d{3,4}[a-z]*)?|core\s+[3579]\s+\d{3,4}[a-z]*)\b",
            title_lower,
        )
        if intel_ultra:
            return intel_ultra.group(1).strip()

        # 3. Intel Core i-series with exact generation/SKU
        intel_i_exact = re.search(
            r"\b((?:intel\s+)?(?:core\s+)?i[3579][-\s]*(?:[0-9]{1,2}(?:th|nd|rd|st)\s+gen\s+)?([0-9]{4,5}[a-z]{0,2}))\b",
            title_lower,
        )
        if intel_i_exact:
            sku = intel_i_exact.group(2)
            tier_match = re.search(r"i[3579]", intel_i_exact.group(1))
            if tier_match:
                return f"{tier_match.group(0)}-{sku}".casefold()

        # 4. Intel Core i-series generic tier
        intel_i_tier = re.search(
            r"\b((?:intel\s+)?(?:core\s+)?(i[3579]))\b(?:\s+(\d{1,2}(?:th|nd|rd|st)\s+gen))?",
            title_lower,
        )
        if intel_i_tier:
            gen_tier = intel_i_tier.group(2)
            gen_val = intel_i_tier.group(3)
            return f"{gen_tier} {gen_val}".strip() if gen_val else gen_tier

        # 5. AMD Ryzen exact SKU & AI series
        ryzen_exact = re.search(
            r"\b(ryzen\s+(?:ai\s+)?[3579]\s+([0-9]{3,4}[a-z]{0,2}))\b",
            title_lower,
        )
        if ryzen_exact:
            return ryzen_exact.group(1).strip()

        # 6. AMD Ryzen tier
        ryzen_tier = re.search(r"\b(ryzen\s+[3579]|athlon\s*\w*)\b", title_lower)
        if ryzen_tier:
            return ryzen_tier.group(1).strip()

        # 7. MediaTek processor (e.g. Helio G99, Kompanio 520, MT8788)
        mediatek = re.search(
            r"\b(mediatek\s+(?:helio\s+[a-z0-9]+|kompanio\s+\d+|mt[0-9]{4}[a-z]*)|helio\s+[a-z0-9]+|kompanio\s+\d+|mt[0-9]{4}[a-z]*)\b",
            title_lower,
        )
        if mediatek:
            return mediatek.group(1).strip()

        # 8. Intel Celeron / Pentium / N-series
        intel_other = re.search(
            r"\b(celeron\s*\w*|pentium\s*\w*|intel\s+processor\s+n\d+|n\d{3,4})\b",
            title_lower,
        )
        if intel_other:
            return intel_other.group(1).strip()

        # 9. Snapdragon X Elite / Plus / Processor
        snapdragon = re.search(
            r"\b(snapdragon\s+x\s+(?:elite|plus|processor)|\bsnapdragon\s+x\b)\b",
            title_lower,
        )
        if snapdragon:
            return snapdragon.group(1).strip()

        # Fallback to specs if present
        if specs:
            for k in (
                "processor name",
                "cpu model",
                "processor",
                "cpu_model",
                "processor type",
                "chipset",
            ):
                if k in specs and specs[k].strip():
                    p_val = specs[k].strip()
                    p_var = specs.get("processor variant", "").strip()
                    p_brand = specs.get("processor brand", "").strip()
                    full_p = f"{p_val} {p_var}".strip()
                    if p_brand and p_brand.lower() not in full_p.lower():
                        full_p = f"{p_brand} {full_p}".strip()
                    return full_p.casefold()

        return None

    @classmethod
    def extract_gpu(cls, title: str, specs: dict[str, str] | None = None) -> str | None:
        title_lower = title.casefold()

        # Dedicated NVIDIA RTX/GTX
        nvidia = re.search(
            r"\b((?:geforce\s+)?(?:rtx|gtx)\s*(\d{4}(?:\s*ti)?))\b",
            title_lower,
        )
        if nvidia:
            return f"nvidia rtx {nvidia.group(2)}".strip()

        # Dedicated AMD Radeon
        amd_gpu = re.search(r"\b(radeon\s+rx\s*\d{4}[a-z]*)\b", title_lower)
        if amd_gpu:
            return f"amd {amd_gpu.group(1)}".strip()

        # Intel Arc
        intel_arc = re.search(r"\b(intel\s+arc\s*[a-z0-9]*)\b", title_lower)
        if intel_arc:
            return intel_arc.group(1).strip()

        if specs:
            for k in (
                "graphic processor",
                "graphics processor",
                "graphics",
                "gpu",
                "gpu model",
                "gpu_model",
            ):
                if k in specs and specs[k].strip():
                    return specs[k].casefold().strip()

        # Apple Silicon custom GPU
        apple_m = re.search(r"\b(m[1-5](?:\s+(?:pro|max|ultra))?)\b", title_lower)
        if apple_m:
            return f"apple {apple_m.group(1).strip()} gpu"

        return None

    @classmethod
    def extract_ram_gb(cls, title: str, specs: dict[str, str] | None = None) -> int | None:
        title_lower = title.casefold()
        matches = re.findall(
            r"\b(\d{1,2})\s*gb(?:\s+(?:ddr\d|lpddr\d|unified\s+memory|ram))?\b", title_lower
        )
        for m in matches:
            val = int(m)
            if val in (4, 8, 12, 16, 18, 24, 32, 36, 48, 64, 96, 128):
                return val

        if specs and "ram" in specs:
            val_match = re.search(r"(\d+)\s*gb", specs["ram"].casefold())
            if val_match:
                return int(val_match.group(1))

        return None

    @classmethod
    def extract_storage_gb(cls, title: str, specs: dict[str, str] | None = None) -> int | None:
        title_lower = title.casefold()

        # TB matches (e.g. 1TB, 2TB) -> GB
        tb_match = re.search(r"\b([1-4])\s*tb\b", title_lower)
        if tb_match:
            return int(tb_match.group(1)) * 1024

        # GB matches (e.g. 256GB, 512GB, 128GB, 64GB)
        gb_matches = re.findall(r"\b(\d{2,4})\s*gb\b", title_lower)
        for m in gb_matches:
            val = int(m)
            if val in (32, 64, 128, 256, 512, 1024, 2048):
                return val

        if specs:
            for k in (
                "storage",
                "ssd capacity",
                "hdd capacity",
                "hard disk size",
                "storage capacity",
            ):
                if k in specs:
                    raw = specs[k].casefold()
                    if "tb" in raw:
                        tb_m = re.search(r"(\d+)\s*tb", raw)
                        if tb_m:
                            return int(tb_m.group(1)) * 1024
                    gb_m = re.search(r"(\d+)\s*gb", raw)
                    if gb_m:
                        return int(gb_m.group(1))

        return None

    @classmethod
    def extract_screen_size_inches(
        cls, title: str, specs: dict[str, str] | None = None
    ) -> float | None:
        title_lower = title.casefold()
        match = re.search(
            r"\b(\d{2}(?:\.\d{1,2})?)\s*(?:inch|inches|\"|\s*-inch|\s*cm)\b",
            title_lower,
        )
        if match:
            raw_val = match.group(1)
            with contextlib.suppress(ValueError):
                val = float(raw_val)
                # Convert cm to inches if in reasonable laptop range
                if val > 25.0:
                    val = round(val / 2.54, 1)
                if 10.0 <= val <= 20.0:
                    return val
        if specs:
            for k in (
                "screen size",
                "display size",
                "display size (in inches)",
                "screen size (in inches)",
                "screen size (in cm)",
                "display size (in cms)",
                "screen dimensions",
            ):
                if k in specs:
                    raw_spec = specs[k]
                    inch_m = re.search(
                        r"(\d{2}(?:\.\d{1,2})?)\s*(?:inch|inches|\")", raw_spec, re.I
                    )
                    if inch_m:
                        with contextlib.suppress(ValueError):
                            return float(inch_m.group(1))
                    num_m = re.search(r"(\d+(?:\.\d+)?)", raw_spec)
                    if num_m:
                        with contextlib.suppress(ValueError):
                            val = float(num_m.group(1))
                            if val > 25.0:
                                val = round(val / 2.54, 1)
                            if 10.0 <= val <= 20.0:
                                return val
        return None

    @classmethod
    def extract_generation(cls, title: str) -> str | None:
        title_lower = title.casefold()
        match = re.search(r"\b(\d{1,2}(?:th|nd|rd|st))\s+gen(?:eration)?\b", title_lower)
        return match.group(1) if match else None

    @classmethod
    def extract_ram_type(cls, title: str, specs: dict[str, str] | None = None) -> str | None:
        title_lower = title.casefold()
        for t in ("lpddr5x", "lpddr5", "ddr5", "lpddr4x", "ddr4", "unified memory"):
            if t in title_lower:
                return t.upper() if "ddr" in t else "Unified Memory"
        if specs and "ram type" in specs:
            return specs["ram type"].upper()
        return None

    @classmethod
    def extract_display_resolution(
        cls, title: str, specs: dict[str, str] | None = None
    ) -> str | None:
        title_lower = title.casefold()
        if "4k" in title_lower or "uhd" in title_lower:
            return "4K UHD"
        if "2.8k" in title_lower:
            return "2.8K"
        if "2.5k" in title_lower or "qhd" in title_lower:
            return "2.5K QHD"
        if "fhd" in title_lower or "1080p" in title_lower:
            return "FHD"
        if "wuxga" in title_lower:
            return "WUXGA"
        if "retina" in title_lower:
            return "Liquid Retina"
        return None

    @classmethod
    def extract_display_type(cls, title: str, specs: dict[str, str] | None = None) -> str | None:
        title_lower = title.casefold()
        if "liquid retina xdr" in title_lower:
            return "Liquid Retina XDR"
        if "oled" in title_lower:
            return "OLED"
        if "ips" in title_lower:
            return "IPS LCD"
        return None

    @classmethod
    def extract_gpu_vram_gb(cls, title: str, specs: dict[str, str] | None = None) -> int | None:
        title_lower = title.casefold()
        m = re.search(r"\b([1-9]|1[26]|24)\s*gb\s*(?:graphics|vram|rtx|gtx)\b", title_lower)
        if m:
            with contextlib.suppress(ValueError):
                return int(m.group(1))
        return None

    @classmethod
    def extract_weight_kg(cls, title: str, specs: dict[str, str] | None = None) -> float | None:
        title_lower = title.casefold()
        m = re.search(r"\b([0-2]\.[0-9]{1,2})\s*kg\b", title_lower)
        if m:
            with contextlib.suppress(ValueError):
                return float(m.group(1))
        if specs:
            for k in ("weight", "item weight", "product weight", "package weight"):
                if k in specs:
                    raw_w = specs[k].casefold()
                    kg_g_m = re.search(r"(\d+)\s*kg\s*(\d+)\s*g", raw_w)
                    if kg_g_m:
                        with contextlib.suppress(ValueError):
                            w_val = round(int(kg_g_m.group(1)) + int(kg_g_m.group(2)) / 1000.0, 2)
                            if 0.5 <= w_val <= 10.0:
                                return w_val
                    m = re.search(r"(\d+(?:\.\d+)?)\s*kg", raw_w)
                    if m:
                        with contextlib.suppress(ValueError):
                            val = float(m.group(1))
                            if 0.5 <= val <= 10.0:
                                return val
        return None

    @classmethod
    def extract_battery_wh(cls, title: str, specs: dict[str, str] | None = None) -> float | None:
        title_lower = title.casefold()
        m = re.search(r"\b(\d{2,3}(?:\.\d)?)\s*wh\b", title_lower)
        if m:
            with contextlib.suppress(ValueError):
                return float(m.group(1))
        if specs:
            for k in (
                "battery cell",
                "battery",
                "battery capacity",
                "standard battery life",
                "lithium battery energy content",
            ):
                if k in specs:
                    m = re.search(r"(\d{2,3}(?:\.\d)?)\s*(?:wh|watt\s*hours)", specs[k].casefold())
                    if m:
                        with contextlib.suppress(ValueError):
                            val = float(m.group(1))
                            if 10.0 <= val <= 150.0:
                                return val
        return None

    @classmethod
    def normalize(
        cls,
        title: str,
        *,
        category: str = "laptop",
        brand_raw: str | None = None,
        model_name_raw: str | None = None,
        specs: dict[str, str] | None = None,
        manufacturer_part_number: str | None = None,
        gtin: str | None = None,
        extra_attributes: dict[str, AttributeValue] | None = None,
    ) -> ProductFingerprint:
        """Create a fully normalized ProductFingerprint from product title and raw attributes."""
        brand = cls.extract_brand(title, brand_raw)
        family = cls.extract_family(title, model_name_raw or "")
        chip = cls.extract_chip(title, specs)
        gpu = cls.extract_gpu(title, specs)
        ram_gb = cls.extract_ram_gb(title, specs)
        storage_gb = cls.extract_storage_gb(title, specs)
        screen_size = cls.extract_screen_size_inches(title, specs)
        generation = cls.extract_generation(title)
        ram_type = cls.extract_ram_type(title, specs)
        display_res = cls.extract_display_resolution(title, specs)
        display_type = cls.extract_display_type(title, specs)
        gpu_vram = cls.extract_gpu_vram_gb(title, specs)
        weight_kg = cls.extract_weight_kg(title, specs)
        battery_wh = cls.extract_battery_wh(title, specs)
        backlight = (
            True
            if "backlit" in title.casefold() or (specs and "backlit" in str(specs).casefold())
            else None
        )

        # Canonical parser fields take priority over title-derived values.
        if extra_attributes:
            if "ram_gb" in extra_attributes:
                with contextlib.suppress(ValueError):
                    ram_gb = int(str(extra_attributes["ram_gb"]))
            if "storage_gb" in extra_attributes:
                with contextlib.suppress(ValueError):
                    storage_gb = int(str(extra_attributes["storage_gb"]))
            if extra_attributes.get("cpu_model"):
                chip = str(extra_attributes["cpu_model"])
            if extra_attributes.get("gpu_model"):
                gpu = str(extra_attributes["gpu_model"])
            if (
                "screen_size_inches" in extra_attributes
                and extra_attributes["screen_size_inches"] is not None
            ):
                with contextlib.suppress(ValueError):
                    screen_size = float(str(extra_attributes["screen_size_inches"]))
            if extra_attributes.get("generation"):
                generation = str(extra_attributes["generation"])
            if extra_attributes.get("ram_type"):
                ram_type = str(extra_attributes["ram_type"])
            if extra_attributes.get("display_resolution"):
                display_res = str(extra_attributes["display_resolution"])
            if extra_attributes.get("display_type"):
                display_type = str(extra_attributes["display_type"])
            if "gpu_vram_gb" in extra_attributes:
                with contextlib.suppress(ValueError):
                    gpu_vram = int(str(extra_attributes["gpu_vram_gb"]))
            if "weight_kg" in extra_attributes:
                with contextlib.suppress(ValueError):
                    weight_kg = float(str(extra_attributes["weight_kg"]))
            if "battery_wh" in extra_attributes:
                with contextlib.suppress(ValueError):
                    battery_wh = float(str(extra_attributes["battery_wh"]))
            if (
                "keyboard_backlight" in extra_attributes
                and extra_attributes["keyboard_backlight"] is not None
            ):
                backlight = bool(extra_attributes["keyboard_backlight"])

        # Build clean model name: [family] [screen_size] [chip]
        components: list[str] = []
        if family:
            if family.startswith(brand):
                components.append(family)
            else:
                components.extend([brand, family])
        else:
            components.append(brand)

        if screen_size:
            components.append(f"{screen_size} inch")
        if chip:
            components.append(chip)
        if gpu and "nvidia" in gpu:
            components.append(gpu)
        if ram_gb and storage_gb:
            components.append(f"{ram_gb}gb / {storage_gb}gb")

        clean_model_name = (
            model_name_raw
            if (
                model_name_raw
                and model_name_raw.strip()
                and model_name_raw != title
                and len(model_name_raw) <= 60
            )
            else " ".join(components)
        )
        clean_model_name = re.sub(
            r"\b(\w+)\s+\1\b", r"\1", clean_model_name, flags=re.IGNORECASE
        ).strip()

        if gpu is None and chip is not None:
            gpu = f"apple {chip} gpu" if re.match(r"^m[1-5]", chip) else "integrated graphics"

        # Build and validate through LaptopAttributes Pydantic schema
        from categories.electronics.laptop.schemas import LaptopAttributes
        from sources.common import is_ignored_spec_key

        extra = {k: v for k, v in (extra_attributes or {}).items() if not is_ignored_spec_key(k)}
        extra.pop("spec_sections", None)
        for explicit_k in (
            "ram_gb",
            "storage_gb",
            "cpu_model",
            "gpu_model",
            "screen_size_inches",
            "generation",
            "ram_type",
            "display_resolution",
            "display_type",
            "gpu_vram_gb",
            "weight_kg",
            "battery_wh",
            "keyboard_backlight",
            "mpn",
            "gtin",
            "asin",
            "ean",
        ):
            extra.pop(explicit_k, None)

        laptop_schema = LaptopAttributes(
            ram_gb=ram_gb,
            storage_gb=storage_gb,
            cpu_model=chip,
            gpu_model=gpu,
            screen_size_inches=screen_size,
            generation=generation,
            ram_type=ram_type,
            display_resolution=display_res,
            display_type=display_type,
            gpu_vram_gb=gpu_vram,
            weight_kg=weight_kg,
            battery_wh=battery_wh,
            keyboard_backlight=backlight,
            mpn=manufacturer_part_number,
            gtin=gtin,
            **extra,
        )
        attributes = laptop_schema.to_attribute_dict()

        if chip and (
            "cpu_model" not in attributes or attributes["cpu_model"] == "standard processor"
        ):
            attributes["cpu_model"] = chip
        if gpu and (
            "gpu_model" not in attributes
            or (gpu.startswith("apple") and attributes["gpu_model"] == "integrated graphics")
        ):
            attributes["gpu_model"] = gpu

        fingerprint_attributes = {
            key: value
            for key, value in attributes.items()
            if key
            not in {
                "cpu_model",
                "generation",
                "gpu_model",
                "gtin",
                "mpn",
                "ram_gb",
                "screen_size_inches",
                "storage_gb",
                "storage_type",
            }
        }

        from matching.fingerprint import ProductFingerprint

        return ProductFingerprint(
            category=category,
            brand=brand,
            family=family,
            model_name=clean_model_name,
            generation=generation,
            chip=chip,
            ram_gb=ram_gb,
            storage_gb=storage_gb,
            storage_type="ssd",
            screen_size_inches=screen_size,
            gpu_model=gpu,
            manufacturer_part_number=manufacturer_part_number,
            gtin=gtin,
            attributes=fingerprint_attributes,
        )

    @classmethod
    def normalize_product(
        cls,
        title: str,
        *,
        category: str = "laptop",
        brand_raw: str | None = None,
        model_name_raw: str | None = None,
        specs: dict[str, str] | None = None,
        manufacturer_part_number: str | None = None,
        gtin: str | None = None,
        extra_attributes: dict[str, AttributeValue] | None = None,
    ) -> ProductFingerprint:
        """Create a normalized ProductFingerprint from product title and raw attributes."""
        return cls.normalize(
            title=title,
            category=category,
            brand_raw=brand_raw,
            model_name_raw=model_name_raw,
            specs=specs,
            manufacturer_part_number=manufacturer_part_number,
            gtin=gtin,
            extra_attributes=extra_attributes,
        )
