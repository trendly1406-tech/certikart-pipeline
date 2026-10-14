"""Shared URL validation; retailer selectors stay in each retailer parser."""

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import AnyHttpUrl, ValidationError


def image_urls(candidates: list[object], page_url: AnyHttpUrl) -> tuple[AnyHttpUrl, ...]:
    urls: list[AnyHttpUrl] = []
    identities: set[str] = set()
    for candidate in candidates:
        values = candidate if isinstance(candidate, list) else [candidate]
        for value in values:
            if isinstance(value, dict):
                value = value.get("url") or value.get("contentUrl")
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                url = AnyHttpUrl(urljoin(str(page_url), value.strip()))
            except ValidationError:
                continue
            if url.scheme == "https" and not url.username:
                identity = str(url)
                if url.host and (
                    url.host.endswith("media-amazon.com") or url.host.endswith("images-amazon.com")
                ):
                    parts = urlsplit(identity)
                    # Amazon serves the same gallery asset with different size/quality
                    # suffixes (for example `._AC_SX679_.jpg`). Treat those variants as
                    # one image while retaining the first, usually highest-quality URL.
                    path = re.sub(
                        r"\._[A-Za-z0-9_,+-]+_\.(?=(?:jpe?g|png|webp)$)",
                        ".",
                        parts.path,
                        flags=re.IGNORECASE,
                    )
                    identity = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
                if identity not in identities:
                    identities.add(identity)
                    urls.append(url)
            if len(urls) >= 6:
                return tuple(urls)
    return tuple(urls)
