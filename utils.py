"""Utility functions for Nabu-V1.

Includes:
- Magic-byte file format sniffing (PDF, EPUB, MOBI, AZW, ZIP, HTML).
- In-memory candidate cache with TTL for remote search results.
- Compact callback encoding and parsing (<= 64 bytes).
- Safe filename sanitization for Telegram delivery.
- Canonical user-facing error message mapping and size formatting.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import time
from typing import Any

from models import SearchHit

# Telegram inline keyboard callback_data has a hard 64-byte ceiling.
MAX_CALLBACK_BYTES = 64

# Standard user-facing error messages defined by GOAL.md lines 626-663.
ERROR_MESSAGES: dict[str, str] = {
    "kubo_timeout": "⚠️ File could not be retrieved from IPFS right now. Try again later.",
    "kubo_offline": "⚠️ File could not be retrieved from IPFS right now. Try again later.",
    "missing_cid": "⚠️ This book does not currently have a valid IPFS file reference.",
    "too_large": "⚠️ This file is too large for Telegram to deliver.",
    "telegram_upload_error": "⚠️ The file was retrieved but Telegram could not deliver it.",
    "no_sources_reachable": "⚠️ Could not reach any book source right now. Try again later.",
    "download_stuck": "⚠️ Found it, but the download is stuck. Try again later.",
    "file_corrupt": "⚠️ Found it, but the file looks corrupted.",
    "not_found": "⚠️ Not found on any source.",
    "unauthorized": "Sorry, this bot is private.",
}


def get_user_error(key: str, default: str | None = None) -> str:
    """Return a clean user-facing error message by error key."""
    if default is None:
        default = "⚠️ An error occurred while processing your request. Try again later."
    return ERROR_MESSAGES.get(key, default)


# -----------------------------------------------------------------------------
# File Format Sniffing via Magic Bytes
# -----------------------------------------------------------------------------

def sniff_file_type(header: bytes) -> str | None:
    """Inspect header bytes (at least 70 bytes recommended) to detect file type.

    Returns:
        'pdf', 'epub', 'mobi', 'azw3', 'zip', 'html', or None.
    """
    if not header:
        return None

    # HTML / XML error pages (common when shadow libraries serve a 404 or captcha as .pdf)
    header_strip = header.lstrip()
    if (
        header_strip.startswith(b"<!DOCTYPE")
        or header_strip.startswith(b"<!doctype")
        or header_strip.startswith(b"<html")
        or header_strip.startswith(b"<HTML")
        or header_strip.startswith(b"<?xml")
    ):
        return "html"

    # PDF: starts with '%PDF-'
    if header.startswith(b"%PDF-"):
        return "pdf"

    # ZIP-based containers (EPUB or plain ZIP)
    if header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06"):
        # EPUB check: EPUB files are ZIP archives whose uncompressed first file
        # is typically 'mimetype' containing 'application/epub+zip'.
        if b"mimetype" in header[:50] and b"application/epub+zip" in header[:120]:
            return "epub"
        # If mimetype isn't in first 120 bytes, it's still at least a zip archive
        return "zip"

    # MOBI / PalmDOC: Palm database format header
    # Bytes 60..68 in header contain the database type 'BOOKMOBI' or 'TEXtREAd'
    if len(header) >= 68:
        db_type = header[60:68]
        if db_type == b"BOOKMOBI":
            return "mobi"
        if db_type == b"TEXtREAd":
            return "mobi"

    # Amazon Kindle Topaz format
    if header.startswith(b"TPZ"):
        return "azw3"

    return None


def is_plausible_book_file(
    file_path_or_bytes: Path | str | bytes,
    expected_ext: str | None = None,
) -> tuple[bool, str]:
    """Validate that a downloaded file is a genuine book file and not an HTML error.

    Returns:
        (True, detected_type) if valid.
        (False, failure_reason) if invalid or corrupt.
    """
    if isinstance(file_path_or_bytes, (str, Path)):
        path = Path(file_path_or_bytes)
        if not path.exists():
            return False, "File does not exist on disk"
        if path.stat().st_size == 0:
            return False, "File is zero bytes"
        with open(path, "rb") as f:
            header = f.read(512)
    else:
        header = file_path_or_bytes[:512]
        if not header:
            return False, "Data is empty"

    detected = sniff_file_type(header)
    if detected == "html":
        return False, "Server returned an HTML page instead of a book file"

    if detected is None:
        # Check if expected_ext is provided and plausible
        if expected_ext and expected_ext.lower().lstrip(".") in ("txt", "fb2"):
            return True, expected_ext.lower().lstrip(".")
        return False, "Unrecognized magic bytes / corrupted file format"

    # If it's a generic zip and we expected an epub, check if 'epub' appears in header
    if detected == "zip" and expected_ext and expected_ext.lower().lstrip(".") == "epub":
        if b"epub" in header.lower():
            return True, "epub"
        return True, "zip"

    return True, detected


# -----------------------------------------------------------------------------
# Callback Data Encode & Decode
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# In-Memory Candidate Cache with TTL
# -----------------------------------------------------------------------------

class CandidateCache:
    """Per-user, TTL-backed in-memory cache for remote search candidate hits.

    Prevents inserting unverified remote hits into SQLite while enabling users
    to select hits using compact callback tokens: `web:<token>:<index>`.
    """

    def __init__(self, default_ttl_seconds: int = 600) -> None:
        self.default_ttl = default_ttl_seconds
        # Key: (user_id, token) -> Value: (hits, expires_at)
        self._cache: dict[tuple[int, str], tuple[list[SearchHit], float]] = {}

    def store(self, user_id: int, hits: list[SearchHit], ttl_seconds: int | None = None) -> str:
        """Store hits for a user and return an 8-character hex token."""
        self.sweep()
        token = secrets.token_hex(4)  # 8 hex chars (e.g. 'a1b2c3d4')
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        expires_at = time.monotonic() + ttl
        self._cache[(user_id, token)] = (list(hits), expires_at)
        return token

    def get(self, user_id: int, token: str, index: int) -> SearchHit | None:
        """Retrieve a specific hit by user_id, token, and index.

        Returns None if expired, missing, or index out of range.
        """
        key = (user_id, token)
        item = self._cache.get(key)
        if not item:
            return None
        hits, expires_at = item
        if time.monotonic() > expires_at:
            self._cache.pop(key, None)
            return None
        if 0 <= index < len(hits):
            return hits[index]
        return None

    def get_all(self, user_id: int, token: str) -> list[SearchHit] | None:
        """Retrieve all hits for a user_id and token.

        Returns None if expired or missing.
        """
        key = (user_id, token)
        item = self._cache.get(key)
        if not item:
            return None
        hits, expires_at = item
        if time.monotonic() > expires_at:
            self._cache.pop(key, None)
            return None
        return list(hits)

    def sweep(self) -> int:
        """Remove expired tokens from cache. Returns count of purged entries."""
        now = time.monotonic()
        expired_keys = [k for k, (_, exp) in self._cache.items() if now > exp]
        for k in expired_keys:
            self._cache.pop(k, None)
        return len(expired_keys)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()


# Global candidate cache singleton
candidates = CandidateCache()


# -----------------------------------------------------------------------------
# Filename & Caption Formatting
# -----------------------------------------------------------------------------

def sanitize_filename(name: str, max_length: int = 120) -> str:
    """Remove unsafe filesystem characters and limit filename length."""
    # Replace invalid filesystem characters with an underscore
    cleaned = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if not cleaned:
        cleaned = "book"
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" ._")
    return cleaned


def build_delivery_filename(title: str, author: str | None = None, extension: str | None = None) -> str:
    """Build a user-friendly delivery filename, e.g. 'Clean Code - Robert C. Martin.pdf'."""
    ext = (extension or "pdf").lower().lstrip(".")
    safe_title = sanitize_filename(title, max_length=80)
    if author and author.strip():
        safe_author = sanitize_filename(author.strip(), max_length=50)
        base = f"{safe_title} - {safe_author}"
    else:
        base = safe_title
    return f"{base}.{ext}"


def format_file_size(size_bytes: int | None) -> str:
    """Format bytes into a readable string (e.g. '4.2 MiB')."""
    if size_bytes is None or size_bytes < 0:
        return "Unknown size"
    units = ["B", "KiB", "MiB", "GiB"]
    val = float(size_bytes)
    for u in units:
        if val < 1024.0 or u == units[-1]:
            if u == "B":
                return f"{int(val)} B"
            return f"{val:.1f} {u}"
        val /= 1024.0
    return f"{size_bytes} B"


def format_caption(title: str, author: str | None = None, file_size: int | None = None) -> str:
    """Format Telegram document delivery caption."""
    lines = [f"📖 {title}"]
    if author and author.strip():
        lines.append(f"✍️ {author.strip()}")
    if file_size and file_size > 0:
        lines.append(f"💾 {format_file_size(file_size)}")
    return "\n".join(lines)
