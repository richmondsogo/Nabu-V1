"""Utility functions for Nabu-V1 Link Resolver.

Includes:
- HTML escaping for Telegram messages.
- Multi-mirror link building from MD5.
- Compact callback encoding and parsing (<= 64 bytes).
- File size formatting.
"""

from __future__ import annotations

import html
import re
from typing import Any

# Telegram inline keyboard callback_data ceiling
MAX_CALLBACK_BYTES = 64


def escape_html(text: str | None) -> str:
    """Escape &, <, > for safe Telegram HTML parse mode rendering."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# Alias
escape = escape_html


# Note: .li and .bz share one operator/IP; .la is Cloudflare-fronted;
# .is/.rs/.st share one IP. Three links are two-and-a-half failure domains, not three.
def build_links(md5: str | None) -> list[tuple[str, str]]:
    """Build multi-mirror download links from MD5 hash.

    Returns up to 3 links with labels naming the host:
    1. Primary li mirror (libgen.li)
    2. Second li mirror (libgen.la)
    3. Best is mirror (libgen.is)
    """
    if not md5 or not md5.strip():
        return []

    clean_md5 = md5.strip().lower()
    return [
        ("Libgen.li", f"https://libgen.li/ads.php?md5={clean_md5}"),
        ("Libgen.la", f"https://libgen.la/ads.php?md5={clean_md5}"),
        ("Libgen.is", f"https://libgen.is/book/index.php?md5={clean_md5}"),
    ]


def format_file_size(size_bytes: int | None) -> str:
    """Format bytes into a readable string (e.g. '277 kB', '4.2 MB')."""
    if size_bytes is None or size_bytes <= 0:
        return "Unknown size"
    units = ["B", "KB", "MB", "GB"]
    val = float(size_bytes)
    for u in units:
        if val < 1024.0 or u == units[-1]:
            if u == "B":
                return f"{int(val)} B"
            return f"{val:.2f} {u}"
        val /= 1024.0
    return f"{size_bytes} B"


def encode_callback(action: str, *args: Any) -> str:
    """Encode an action and its arguments into a compact Telegram callback string.

    Guarantees that the resulting UTF-8 encoded string is <= 64 bytes.
    Raises ValueError if length exceeds 64 bytes.
    """
    parts = [action] + [str(a) for a in args]
    payload = ":".join(parts)
    encoded = payload.encode("utf-8")
    if len(encoded) > MAX_CALLBACK_BYTES:
        raise ValueError(
            f"Callback data exceeds 64 bytes limit ({len(encoded)} bytes): {payload!r}"
        )
    return payload


def decode_callback(data: str) -> tuple[str, list[str]]:
    """Decode a Telegram callback string into (action, args_list)."""
    if not data:
        return "", []
    parts = data.split(":")
    return parts[0], parts[1:]
