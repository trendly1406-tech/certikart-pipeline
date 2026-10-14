"""Domain-specific identity normalizer for smartphone/mobile products."""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, ClassVar

from categories.contracts import AttributeValue

if TYPE_CHECKING:
    from matching.fingerprint import ProductFingerprint


class MobileIdentityNormalizer:
    """Extracts structured canonical identity from retailer smartphone titles."""

    KNOWN_BRANDS: ClassVar[tuple[str, ...]] = (
        "apple",
        "samsung",
        "oneplus",
        "xiaomi",
        "redmi",
        "poco",
        "realme",
        "vivo",
        "iqoo",
        "oppo",
        "google",
        "motorola",
        "nothing",
        "cmf",
        "infinix",
        "tecno",
        "lava",
        "honor",
        "asus",
        "nokia",
    )

    BRAND_ALIASES: ClassVar[dict[str, str]] = {
        "apple inc": "apple",
        "samsung electronics": "samsung",
        "google pixel": "google",
        "moto": "motorola",
        "nothing phone": "nothing",
        "redmi by xiaomi": "redmi",
        "poco by xiaomi": "poco",
    }

    PRODUCT_FAMILIES: ClassVar[tuple[tuple[str, str], ...]] = (
        (r"iphone\s+1[1-9]\s+pro\s+max", "iphone pro max"),
        (r"iphone\s+1[1-9]\s+pro", "iphone pro"),
        (r"iphone\s+1[1-9]\s+plus", "iphone plus"),
        (r"iphone\s+1[1-9]e?", "iphone"),
        (r"iphone\s+air", "iphone air"),
        (r"iphone\s+se\s*\d*", "iphone se"),
        (r"galaxy\s+s2[0-6]\s+ultra", "galaxy s ultra"),
        (r"galaxy\s+s2[0-6]\s+plus", "galaxy s plus"),
        (r"galaxy\s+s2[0-6]\s+fe", "galaxy s fe"),
        (r"galaxy\s+s2[0-6]", "galaxy s"),
        (r"galaxy\s+z\s+fold\s*\d*", "galaxy z fold"),
        (r"galaxy\s+z\s+flip\s*\d*", "galaxy z flip"),
        (r"galaxy\s+a\d{2}", "galaxy a"),
        (r"galaxy\s+m\d{2}", "galaxy m"),
        (r"galaxy\s+f\d{2}", "galaxy f"),
        (r"pixel\s+[6-9]\s+pro", "pixel pro"),
        (r"pixel\s+[6-9]\s+a", "pixel a"),
        (r"pixel\s+[6-9]", "pixel"),
        (r"oneplus\s+1[1-4]r", "oneplus r"),
        (r"oneplus\s+1[1-4]", "oneplus number"),
        (r"oneplus\s+nord\s+ce\s*\d*", "oneplus nord ce"),
        (r"oneplus\s+nord\s*\d*", "oneplus nord"),
        (r"oneplus\s+open", "oneplus open"),
        (r"redmi\s+note\s+\d+\s+pro\+?", "redmi note pro"),
        (r"redmi\s+note\s+\d+", "redmi note"),
        (r"redmi\s+turbo\s+\d+", "redmi turbo"),
        (r"redmi\s+a\d+\s+pro", "redmi a pro"),
        (r"redmi\s+a\d+", "redmi a"),
        (r"redmi\s+k\d+", "redmi k"),
        (r"redmi\s+\d+[a-z]*", "redmi number"),
        (r"realme\s+gt\s+\d+", "realme gt"),
        (r"realme\s+narzo\s+\d+\s*(?:pro|lite|speed)?", "realme narzo"),
        (r"realme\s+p\d+[a-z]*\s*(?:pro|lite)?", "realme p"),
        (r"realme\s+\d+\s+pro\+?", "realme number pro"),
        (r"realme\s+\d+[a-z]*", "realme number"),
        (r"poco\s+f\d+\s+pro", "poco f pro"),
        (r"poco\s+f\d+", "poco f"),
        (r"poco\s+x\d+\s+pro", "poco x pro"),
        (r"poco\s+x\d+", "poco x"),
        (r"poco\s+m\d+\s*(?:pro|power)?", "poco m"),
        (r"poco\s+c\d+[a-z]*", "poco c"),
        (r"iqoo\s+1[1-9]r?", "iqoo number"),
        (r"iqoo\s+neo\s*\d*", "iqoo neo"),
        (r"iqoo\s+z\d*", "iqoo z"),
        (r"vivo\s+x\d+t?\s*pro\+?", "vivo x pro"),
        (r"vivo\s+x\d+t?", "vivo x"),
        (r"vivo\s+v\d+\s*pro\+?", "vivo v pro"),
        (r"vivo\s+v\d+", "vivo v"),
        (r"vivo\s+t\d+", "vivo t"),
        (r"vivo\s+y\d+", "vivo y"),
        (r"nothing\s+phone\s*\([123a]+\)", "nothing phone"),
        (r"cmf\s+phone\s*\d*", "cmf phone"),
        (r"motorola\s+edge\s+\d+\s*pro", "motorola edge pro"),
        (r"motorola\s+edge\s+\d+\s*(?:fusion|ultra)?", "motorola edge"),
        (r"motorola\s+g\d+\s*(?:power)?", "moto g"),
        (r"moto\s+g\d+\s*(?:power)?", "moto g"),
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
    def extract_ram_gb(cls, title: str, specs: dict[str, str] | None = None) -> int | None:
        title_lower = title.casefold()
        matches = re.findall(r"\b([2468]|12|16|24)\s*gb(?:\s+ram)?\b", title_lower)
        for m in matches:
            val = int(m)
            if val in (2, 3, 4, 6, 8, 12, 16, 24):
                return val

        if specs and "ram" in specs:
            val_match = re.search(r"(\d+)\s*gb", specs["ram"].casefold())
            if val_match:
                return int(val_match.group(1))

        return None

    @classmethod
    def extract_storage_gb(cls, title: str, specs: dict[str, str] | None = None) -> int | None:
        title_lower = title.casefold()

        # Terabyte check
        if "1tb" in title_lower or "1 tb" in title_lower:
            return 1024

        # Gigabyte check
        matches = re.findall(r"\b(32|64|128|256|512)\s*gb(?:\s+(?:rom|storage))?\b", title_lower)
        for m in matches:
            val = int(m)
            if val in (32, 64, 128, 256, 512):
                return val

        if specs and "storage" in specs:
            clean_s = specs["storage"].casefold()
            if "1tb" in clean_s or "1 tb" in clean_s:
                return 1024
            gb_m = re.search(r"(\d+)\s*gb", clean_s)
            if gb_m:
                return int(gb_m.group(1))

        return None

    @classmethod
    def extract_chipset(cls, title: str, specs: dict[str, str] | None = None) -> str | None:
        title_lower = title.casefold()

        # Apple A-series
        apple_a = re.search(r"\b(a1[4-9](?:\s+pro)?(?:\s+bionic)?)\b", title_lower)
        if apple_a:
            return apple_a.group(1).strip()

        # Snapdragon
        snapdragon = re.search(
            r"\b(snapdragon\s+(?:8\s+gen\s+[1-5]|7\s+gen\s+[1-4]|6s?\s+gen\s+\d|4\s+gen\s+\d|8[5-8]\d|7\d{2}[a-z]?|\d{3,4}))\b",
            title_lower,
        )
        if snapdragon:
            return snapdragon.group(1).strip()

        # MediaTek Dimensity
        dimensity = re.search(
            r"\b(dimensity\s+(?:9\d{3}|8\d{3}|7\d{3}|6\d{3}|[1-9]\d{2}[a-z]?))\b",
            title_lower,
        )
        if dimensity:
            return dimensity.group(1).strip()

        # Google Tensor
        tensor = re.search(r"\b(tensor\s+g[1-5])\b", title_lower)
        if tensor:
            return tensor.group(1).strip()

        # Unisoc
        unisoc = re.search(r"\b(unisoc\s+(?:t\d{3,4}|octa-core|\w+))\b", title_lower)
        if unisoc:
            return unisoc.group(1).strip()

        if specs and "processor" in specs:
            return specs["processor"].casefold().strip()

        return None

    @classmethod
    def extract_color(cls, title: str) -> str | None:
        title_lower = title.casefold()
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
                return c.title()
        return None

    @classmethod
    def extract_screen_size_inches(
        cls, title: str, specs: dict[str, str] | None = None
    ) -> float | None:
        title_lower = title.casefold()
        # Look for e.g. 6.9in, 6.72", 6.1″, 6.5-inch
        m = re.search(r"([2-7](?:\.[0-9]{1,2})?)\s*(?:in|inch|inches|\"|″|-inch)", title_lower)
        if m:
            with contextlib.suppress(ValueError):
                return float(m.group(1))

        cm_t = re.search(r"(\d{1,2}(?:\.\d{1,2})?)\s*cm", title_lower)
        if cm_t:
            with contextlib.suppress(ValueError):
                cm_f = float(cm_t.group(1))
                val = round(cm_f / 2.54, 2)
                if 1.5 <= val <= 8.5:
                    return val

        if specs:
            for k in (
                "screen size in inches",
                "screen size (in inches)",
                "display size (in inches)",
                "screen size",
                "display size",
                "screen size in cm",
                "display size in cm",
            ):
                if k in specs:
                    sc_raw = specs[k]
                    inch_m = re.search(
                        r"(\d+(?:\.\d+)?)\s*(?:in|inch|inches|\"|″|-inch)", sc_raw, re.IGNORECASE
                    )
                    if inch_m:
                        with contextlib.suppress(ValueError):
                            val = float(inch_m.group(1))
                            if 1.5 <= val <= 8.5:
                                return val
                    cm_m = re.search(r"(\d+(?:\.\d+)?)\s*cm", sc_raw, re.IGNORECASE)
                    if cm_m:
                        with contextlib.suppress(ValueError):
                            cm_val = float(cm_m.group(1))
                            val = round(cm_val / 2.54, 2)
                            if 1.5 <= val <= 8.5:
                                return val
                    num_m = re.search(r"^(\d+(?:\.\d+)?)$", sc_raw.strip())
                    if num_m:
                        with contextlib.suppress(ValueError):
                            val = float(num_m.group(1))
                            if 1.5 <= val <= 8.5:
                                return val
                            elif val > 8.5:
                                val_cm = round(val / 2.54, 2)
                                if 1.5 <= val_cm <= 8.5:
                                    return val_cm
        return None

    @classmethod
    def extract_display_type(cls, title: str, specs: dict[str, str] | None = None) -> str | None:
        title_lower = title.casefold()
        if "super retina xdr" in title_lower or (specs and "super retina" in str(specs).casefold()):
            return "Super Retina XDR"
        if "amoled" in title_lower:
            return "AMOLED"
        if "oled" in title_lower:
            return "OLED"
        if "fhd+ ips" in title_lower or "ips" in title_lower:
            return "IPS LCD"
        if "hd+" in title_lower:
            return "HD+ Display"
        return None

    @classmethod
    def extract_refresh_rate_hz(cls, title: str, specs: dict[str, str] | None = None) -> int | None:
        title_lower = title.casefold()
        m = re.search(r"\b(60|90|120|144|165)\s*hz\b", title_lower)
        if m:
            with contextlib.suppress(ValueError):
                return int(m.group(1))
        if specs and "refresh rate" in specs:
            m = re.search(r"\b(60|90|120|144|165)\b", specs["refresh rate"])
            if m:
                with contextlib.suppress(ValueError):
                    return int(m.group(1))
        return None

    @classmethod
    def extract_primary_camera_mp(
        cls, title: str, specs: dict[str, str] | None = None
    ) -> int | None:
        title_lower = title.casefold()
        m = re.search(r"\b(200|108|64|50|48|32|13|12|8|5)\s*mp\b", title_lower)
        if m:
            with contextlib.suppress(ValueError):
                return int(m.group(1))
        if specs:
            for k in ("primary camera", "rear camera", "camera"):
                if k in specs:
                    m = re.search(r"\b(200|108|64|50|48|32|13|12|8|5)\b", specs[k])
                    if m:
                        with contextlib.suppress(ValueError):
                            return int(m.group(1))
        return None

    @classmethod
    def extract_battery_mah(cls, title: str, specs: dict[str, str] | None = None) -> int | None:
        title_lower = title.casefold()
        m = re.search(r"\b([1-9][0-9]{3})\s*mah\b", title_lower)
        if m:
            with contextlib.suppress(ValueError):
                return int(m.group(1))
        if specs and "battery capacity" in specs:
            m = re.search(r"\b([1-9][0-9]{3})\b", specs["battery capacity"])
            if m:
                with contextlib.suppress(ValueError):
                    return int(m.group(1))
        return None

    @classmethod
    def extract_fast_charging_w(cls, title: str, specs: dict[str, str] | None = None) -> int | None:
        title_lower = title.casefold()
        m = re.search(
            r"\b(\d{2,3})\s*w\s*(?:hypercharge|turbopower|fast|charging|supervooc)?\b", title_lower
        )
        if m:
            with contextlib.suppress(ValueError):
                return int(m.group(1))
        return None

    @classmethod
    def extract_operating_system(
        cls, title: str, specs: dict[str, str] | None = None
    ) -> str | None:
        title_lower = title.casefold()
        if "iphone" in title_lower or "apple" in title_lower:
            return "iOS"
        if "android" in title_lower:
            return "Android"
        if specs and "operating system" in specs:
            return specs["operating system"].strip()
        return "Android"

    @classmethod
    def extract_resolution_standard(
        cls, title: str, specs: dict[str, str] | None = None
    ) -> str | None:
        title_lower = title.casefold()
        if "4k" in title_lower:
            return "4K UHD"
        if "qhd+" in title_lower or "2k" in title_lower:
            return "QHD+"
        if "1.5k" in title_lower:
            return "1.5K"
        if "fhd+" in title_lower or "full hd+" in title_lower:
            return "FHD+"
        if "hd+" in title_lower:
            return "HD+"
        if specs and "resolution" in specs:
            r = specs["resolution"].casefold()
            if "3840" in r:
                return "4K UHD"
            if "2560" in r or "3120" in r:
                return "QHD+"
            if "2400" in r or "1080" in r:
                return "FHD+"
            if "720" in r:
                return "HD+"
        return None

    @classmethod
    def extract_camera_setup(cls, title: str, specs: dict[str, str] | None = None) -> str | None:
        title_lower = title.casefold()
        if "quad camera" in title_lower:
            return "Quad Camera"
        if "triple camera" in title_lower:
            return "Triple Camera"
        if "dual camera" in title_lower:
            return "Dual Camera"
        if specs and "camera" in specs:
            c = specs["camera"].casefold()
            if "quad" in c or c.count("+") >= 3:
                return "Quad Camera"
            if "triple" in c or c.count("+") == 2:
                return "Triple Camera"
            if "dual" in c or c.count("+") == 1:
                return "Dual Camera"
        return None

    @classmethod
    def extract_water_resistance(
        cls, title: str, specs: dict[str, str] | None = None
    ) -> str | None:
        title_lower = title.casefold()
        m = re.search(r"\b(ip6[89]|ip6[57]|ip5[45]|ip53)\b", title_lower)
        if m:
            return m.group(1).upper()
        if specs and "water resistance" in specs:
            m = re.search(
                r"\b(ip6[89]|ip6[57]|ip5[45]|ip53)\b", specs["water resistance"].casefold()
            )
            if m:
                return m.group(1).upper()
        return None

    @classmethod
    def extract_screen_protection(
        cls, title: str, specs: dict[str, str] | None = None
    ) -> str | None:
        title_lower = title.casefold()
        if "ceramic shield" in title_lower or "iphone" in title_lower:
            return "Ceramic Shield"
        if "victus 2" in title_lower or (specs and "victus 2" in str(specs).casefold()):
            return "Gorilla Glass Victus 2"
        if "victus" in title_lower or (specs and "victus" in str(specs).casefold()):
            return "Gorilla Glass Victus"
        if "gorilla glass" in title_lower or (specs and "gorilla glass" in str(specs).casefold()):
            return "Gorilla Glass"
        return None

    @classmethod
    def normalize(
        cls,
        title: str,
        *,
        category: str = "mobile",
        brand_raw: str | None = None,
        model_name_raw: str | None = None,
        specs: dict[str, str] | None = None,
        manufacturer_part_number: str | None = None,
        gtin: str | None = None,
        extra_attributes: dict[str, AttributeValue] | None = None,
    ) -> ProductFingerprint:
        """Construct structured canonical ProductFingerprint for smartphone product."""
        from categories.electronics.mobile.schemas import MobileAttributes

        brand = cls.extract_brand(title, brand_raw)
        family = cls.extract_family(title, model_name_raw or "")
        ram_gb = cls.extract_ram_gb(title, specs)
        storage_gb = cls.extract_storage_gb(title, specs)
        chipset = cls.extract_chipset(title, specs)
        color = cls.extract_color(title)
        network_type = "5G" if "5g" in title.casefold() else "4G"
        screen_size = cls.extract_screen_size_inches(title, specs)
        display_type = cls.extract_display_type(title, specs)
        refresh_rate = cls.extract_refresh_rate_hz(title, specs)
        primary_camera = cls.extract_primary_camera_mp(title, specs)
        battery = cls.extract_battery_mah(title, specs)
        charging = cls.extract_fast_charging_w(title, specs)
        os_name = cls.extract_operating_system(title, specs)
        resolution = cls.extract_resolution_standard(title, specs)
        camera_setup = cls.extract_camera_setup(title, specs)
        water_resistance = cls.extract_water_resistance(title, specs)
        screen_protection = cls.extract_screen_protection(title, specs)
        ois_supported = (
            True
            if "ois" in title.casefold() or (specs and "ois" in str(specs).casefold())
            else None
        )

        if extra_attributes:
            if "ram_gb" in extra_attributes:
                with contextlib.suppress(ValueError):
                    ram_gb = int(str(extra_attributes["ram_gb"]))
            if "storage_gb" in extra_attributes:
                with contextlib.suppress(ValueError):
                    storage_gb = int(str(extra_attributes["storage_gb"]))
            if (
                "screen_size_inches" in extra_attributes
                and extra_attributes["screen_size_inches"] is not None
            ):
                with contextlib.suppress(ValueError):
                    screen_size = float(str(extra_attributes["screen_size_inches"]))
            if extra_attributes.get("chipset"):
                chipset = str(extra_attributes["chipset"])
            if extra_attributes.get("color"):
                color = str(extra_attributes["color"])
            if extra_attributes.get("network_type"):
                network_type = str(extra_attributes["network_type"])
            if extra_attributes.get("display_type"):
                display_type = str(extra_attributes["display_type"])
            if (
                "refresh_rate_hz" in extra_attributes
                and extra_attributes["refresh_rate_hz"] is not None
            ):
                with contextlib.suppress(ValueError):
                    refresh_rate = int(str(extra_attributes["refresh_rate_hz"]))
            if (
                "primary_camera_mp" in extra_attributes
                and extra_attributes["primary_camera_mp"] is not None
            ):
                with contextlib.suppress(ValueError):
                    primary_camera = int(str(extra_attributes["primary_camera_mp"]))
            if "battery_mah" in extra_attributes and extra_attributes["battery_mah"] is not None:
                with contextlib.suppress(ValueError):
                    battery = int(str(extra_attributes["battery_mah"]))
            if (
                "fast_charging_w" in extra_attributes
                and extra_attributes["fast_charging_w"] is not None
            ):
                with contextlib.suppress(ValueError):
                    charging = int(str(extra_attributes["fast_charging_w"]))
            if extra_attributes.get("operating_system"):
                os_name = str(extra_attributes["operating_system"])
            if extra_attributes.get("resolution_standard"):
                resolution = str(extra_attributes["resolution_standard"])
            if extra_attributes.get("camera_setup"):
                camera_setup = str(extra_attributes["camera_setup"])
            if extra_attributes.get("water_resistance_rating"):
                water_resistance = str(extra_attributes["water_resistance_rating"]).upper()
            if extra_attributes.get("screen_protection"):
                screen_protection = str(extra_attributes["screen_protection"])
            if (
                "ois_supported" in extra_attributes
                and extra_attributes["ois_supported"] is not None
            ):
                ois_supported = bool(extra_attributes["ois_supported"])

        # Build clean model name: [Brand] [Family/Model] [Storage] [Color]
        parts = [brand.capitalize()]
        if family:
            clean_fam = family
            if clean_fam.lower().startswith(brand.lower()):
                clean_fam = clean_fam[len(brand) :].strip()
            parts.append(clean_fam.title())
        elif model_name_raw and len(model_name_raw) <= 30 and model_name_raw != title:
            parts.append(model_name_raw.title())
        else:
            short_model = re.split(r"\(|\d+\s*gb|\||:", title, flags=re.IGNORECASE)[0].strip()
            if short_model:
                short_model = re.sub(
                    rf"^{re.escape(brand)}\s+", "", short_model, flags=re.IGNORECASE
                ).strip()
                if len(short_model) <= 35:
                    parts.append(short_model.title())

        if ram_gb and storage_gb:
            parts.append(f"({ram_gb}GB / {storage_gb}GB)")
        elif storage_gb:
            parts.append(f"({storage_gb}GB)")
        if color:
            parts.append(color)

        clean_model_name = " ".join(p for p in parts if p).strip()
        clean_model_name = re.sub(
            r"\b(\w+)\s+\1\b", r"\1", clean_model_name, flags=re.IGNORECASE
        ).strip()

        # Build and validate through MobileAttributes Pydantic schema
        from sources.common import is_ignored_spec_key

        extra = {k: v for k, v in (extra_attributes or {}).items() if not is_ignored_spec_key(k)}
        extra.pop("spec_sections", None)
        for explicit_k in (
            "ram_gb",
            "storage_gb",
            "chipset",
            "color",
            "network_type",
            "screen_size_inches",
            "display_type",
            "refresh_rate_hz",
            "primary_camera_mp",
            "battery_mah",
            "fast_charging_w",
            "operating_system",
            "resolution_standard",
            "camera_setup",
            "water_resistance_rating",
            "screen_protection",
            "ois_supported",
            "mpn",
            "gtin",
            "asin",
            "ean",
        ):
            extra.pop(explicit_k, None)

        front_camera_val: int | None = None
        if (
            extra_attributes
            and "front_camera_mp" in extra_attributes
            and extra_attributes["front_camera_mp"] is not None
        ):
            with contextlib.suppress(ValueError):
                front_camera_val = int(str(extra_attributes["front_camera_mp"]))
        extra.pop("front_camera_mp", None)

        weight_g_val: float | None = None
        if (
            extra_attributes
            and "weight_grams" in extra_attributes
            and extra_attributes["weight_grams"] is not None
        ):
            with contextlib.suppress(ValueError):
                weight_g_val = float(str(extra_attributes["weight_grams"]))
        extra.pop("weight_grams", None)

        biometrics_val = (
            str(extra_attributes["biometrics"])
            if (
                extra_attributes
                and "biometrics" in extra_attributes
                and extra_attributes["biometrics"]
            )
            else None
        )
        extra.pop("biometrics", None)

        audio_jack_val = (
            bool(extra_attributes["audio_jack_3_5mm"])
            if (
                extra_attributes
                and "audio_jack_3_5mm" in extra_attributes
                and extra_attributes["audio_jack_3_5mm"] is not None
            )
            else None
        )
        extra.pop("audio_jack_3_5mm", None)

        nfc_val = (
            bool(extra_attributes["nfc_supported"])
            if (
                extra_attributes
                and "nfc_supported" in extra_attributes
                and extra_attributes["nfc_supported"] is not None
            )
            else None
        )
        extra.pop("nfc_supported", None)

        sim_type_val = (
            str(extra_attributes["sim_type"])
            if (
                extra_attributes and "sim_type" in extra_attributes and extra_attributes["sim_type"]
            )
            else None
        )
        extra.pop("sim_type", None)

        model_num_val = (
            str(extra_attributes["model_number"])
            if (
                extra_attributes
                and "model_number" in extra_attributes
                and extra_attributes["model_number"]
            )
            else None
        )
        extra.pop("model_number", None)

        asin_val = (
            str(extra_attributes["asin"])
            if (extra_attributes and "asin" in extra_attributes and extra_attributes["asin"])
            else None
        )
        extra.pop("asin", None)

        warranty_val = (
            str(extra_attributes["warranty"])
            if (
                extra_attributes and "warranty" in extra_attributes and extra_attributes["warranty"]
            )
            else None
        )
        extra.pop("warranty", None)

        final_mpn = manufacturer_part_number or (
            str(extra_attributes["mpn"])
            if (extra_attributes and "mpn" in extra_attributes)
            else None
        )
        final_gtin = gtin or (
            str(extra_attributes["gtin"])
            if (extra_attributes and "gtin" in extra_attributes)
            else None
        )

        mobile_schema = MobileAttributes(
            ram_gb=ram_gb,
            storage_gb=storage_gb,
            chipset=chipset,
            color=color,
            network_type=network_type,
            screen_size_inches=screen_size,
            display_type=display_type,
            refresh_rate_hz=refresh_rate,
            primary_camera_mp=primary_camera,
            front_camera_mp=front_camera_val,
            battery_mah=battery,
            fast_charging_w=charging,
            operating_system=os_name,
            resolution_standard=resolution,
            camera_setup=camera_setup,
            water_resistance_rating=water_resistance,
            screen_protection=screen_protection,
            ois_supported=ois_supported,
            biometrics=biometrics_val,
            audio_jack_3_5mm=audio_jack_val,
            nfc_supported=nfc_val,
            weight_grams=weight_g_val,
            sim_type=sim_type_val,
            model_number=model_num_val,
            mpn=final_mpn,
            gtin=final_gtin,
            asin=asin_val,
            warranty=warranty_val,
            **extra,
        )
        attributes = mobile_schema.to_attribute_dict()

        fingerprint_attributes = {
            key: value
            for key, value in attributes.items()
            if key
            not in {
                "chipset",
                "gtin",
                "mpn",
                "ram_gb",
                "screen_size_inches",
                "storage_gb",
            }
        }

        from matching.fingerprint import ProductFingerprint

        return ProductFingerprint(
            category=category,
            brand=brand,
            family=family,
            model_name=clean_model_name,
            chip=chipset,
            ram_gb=ram_gb,
            storage_gb=storage_gb,
            screen_size_inches=screen_size,
            manufacturer_part_number=manufacturer_part_number,
            gtin=gtin,
            attributes=fingerprint_attributes,
        )
