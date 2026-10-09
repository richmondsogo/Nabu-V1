"""Comprehensive unit tests for utils.py."""

from pathlib import Path
import time
import pytest

from models import SearchHit
from utils import (
    CandidateCache,
    build_delivery_filename,
    decode_callback,
    encode_callback,
    format_caption,
    format_file_size,
    get_user_error,
    is_plausible_book_file,
    sanitize_filename,
    sniff_file_type,
)


# -----------------------------------------------------------------------------
# Magic-Byte Sniffing Tests
# -----------------------------------------------------------------------------

def test_sniff_pdf():
    header = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    assert sniff_file_type(header) == "pdf"
    valid, detected = is_plausible_book_file(header)
    assert valid is True
    assert detected == "pdf"


def test_sniff_epub():
    # EPUB: ZIP header with mimetype application/epub+zip
    header = b"PK\x03\x04\n\x00\x00\x00\x00\x00mimetypeapplication/epub+zip" + b"\x00" * 50
    assert sniff_file_type(header) == "epub"
    valid, detected = is_plausible_book_file(header)
    assert valid is True
    assert detected == "epub"


def test_sniff_mobi():
    # MOBI: Palm database header with BOOKMOBI at offset 60
    header = b"\x00" * 60 + b"BOOKMOBI" + b"\x00" * 20
    assert sniff_file_type(header) == "mobi"
    valid, detected = is_plausible_book_file(header)
    assert valid is True
    assert detected == "mobi"


def test_sniff_plain_zip():
    header = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\x00" * 50
    assert sniff_file_type(header) == "zip"
    valid, detected = is_plausible_book_file(header)
    assert valid is True
    assert detected == "zip"


def test_sniff_html_error_page():
    html_sample = b"<!DOCTYPE html>\n<html><head><title>404 Not Found</title></head></html>"
    assert sniff_file_type(html_sample) == "html"
    valid, reason = is_plausible_book_file(html_sample)
    assert valid is False
    assert "HTML" in reason


def test_sniff_corrupt_or_empty():
    valid, reason = is_plausible_book_file(b"")
    assert valid is False
    assert "empty" in reason.lower()

    valid, reason = is_plausible_book_file(b"\x00\x01\x02\x03\x04")
    assert valid is False
    assert "Unrecognized" in reason


def test_is_plausible_book_file_from_disk(tmp_path: Path):
    pdf_file = tmp_path / "valid.pdf"
    pdf_file.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n")
    valid, detected = is_plausible_book_file(pdf_file)
    assert valid is True
    assert detected == "pdf"

    html_file = tmp_path / "error.pdf"
    html_file.write_bytes(b"<html><body>Access Denied - Cloudflare</body></html>")
    valid, reason = is_plausible_book_file(html_file)
    assert valid is False
    assert "HTML" in reason

    empty_file = tmp_path / "empty.pdf"
    empty_file.write_bytes(b"")
    valid, reason = is_plausible_book_file(empty_file)
    assert valid is False
    assert "zero bytes" in reason


# -----------------------------------------------------------------------------
# Callback Encode & Decode Tests
# -----------------------------------------------------------------------------

def test_callback_encoding_and_decoding():
    # Book delivery callback
    cb_book = encode_callback("book", 1842)
    assert cb_book == "book:1842"
    action, args = decode_callback(cb_book)
    assert action == "book"
    assert args == ["1842"]

    # Candidate cache callback
    cb_web = encode_callback("web", "a1b2c3d4", 2)
    assert cb_web == "web:a1b2c3d4:2"
    action, args = decode_callback(cb_web)
    assert action == "web"
    assert args == ["a1b2c3d4", "2"]

    # Pick callback
    cb_pick = encode_callback("pick", 42, 1)
    assert cb_pick == "pick:42:1"
    action, args = decode_callback(cb_pick)
    assert action == "pick"
    assert args == ["42", "1"]


def test_callback_exceeding_64_bytes_raises():
    long_arg = "a" * 60
    with pytest.raises(ValueError, match="exceeds 64 bytes"):
        encode_callback("book", long_arg)


def test_callback_decode_empty():
    action, args = decode_callback("")
    assert action == ""
    assert args == []


# -----------------------------------------------------------------------------
# Candidate Cache Tests
# -----------------------------------------------------------------------------

def test_candidate_cache_store_and_get():
    cache = CandidateCache(default_ttl_seconds=60)
    hit1 = SearchHit(source="annas", source_id="s1", title="Book 1")
    hit2 = SearchHit(source="libgen", source_id="s2", title="Book 2")

    token = cache.store(user_id=123, hits=[hit1, hit2])
    assert len(token) == 8

    # Correct retrieval
    assert cache.get(user_id=123, token=token, index=0) == hit1
    assert cache.get(user_id=123, token=token, index=1) == hit2

    # Out of range index
    assert cache.get(user_id=123, token=token, index=2) is None

    # Wrong user ID
    assert cache.get(user_id=456, token=token, index=0) is None

    # Non-existent token
    assert cache.get(user_id=123, token="nonexist", index=0) is None


def test_candidate_cache_ttl_and_sweep():
    cache = CandidateCache(default_ttl_seconds=1)
    hit = SearchHit(source="annas", source_id="s1", title="Expiring Book")

    # Store with 0.05s TTL
    token = cache.store(user_id=123, hits=[hit], ttl_seconds=0.05)
    time.sleep(0.08)

    # Retrieval after expiration should return None and clear entry
    assert cache.get(user_id=123, token=token, index=0) is None

    # Storing and sweeping
    token2 = cache.store(user_id=123, hits=[hit], ttl_seconds=0.05)
    time.sleep(0.08)
    purged = cache.sweep()
    assert purged >= 1


# -----------------------------------------------------------------------------
# Filename Sanitization & Caption Formatting Tests
# -----------------------------------------------------------------------------

def test_sanitize_filename():
    unsafe = 'Clean Code: A Handbook / "Agile" <Craftsmanship>? *vol.1* |'
    safe = sanitize_filename(unsafe)
    assert ":" not in safe
    assert "/" not in safe
    assert '"' not in safe
    assert "<" not in safe
    assert ">" not in safe
    assert "?" not in safe
    assert "*" not in safe
    assert "|" not in safe
    assert "Clean Code_ A Handbook _ _Agile_" in safe


def test_build_delivery_filename():
    fn1 = build_delivery_filename("Clean Code", "Robert C. Martin", "pdf")
    assert fn1 == "Clean Code - Robert C. Martin.pdf"

    fn2 = build_delivery_filename("Design Patterns", None, ".epub")
    assert fn2 == "Design Patterns.epub"


def test_format_file_size():
    assert format_file_size(500) == "500 B"
    assert format_file_size(2048) == "2.0 KiB"
    assert format_file_size(4_404_019) == "4.2 MiB"
    assert format_file_size(None) == "Unknown size"


def test_format_caption():
    cap = format_caption("Clean Code", "Robert C. Martin", 4_404_019)
    assert "📖 Clean Code" in cap
    assert "✍️ Robert C. Martin" in cap
    assert "💾 4.2 MiB" in cap


# -----------------------------------------------------------------------------
# Canonical Error Messages Tests
# -----------------------------------------------------------------------------

def test_get_user_error():
    assert "IPFS right now" in get_user_error("kubo_timeout")
    assert "too large" in get_user_error("too_large")
    assert "corrupted" in get_user_error("file_corrupt")
    assert "private" in get_user_error("unauthorized")
    assert "An error occurred" in get_user_error("unknown_key_xyz")
