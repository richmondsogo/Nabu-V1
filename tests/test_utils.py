"""Unit tests for utils.py."""

import pytest
from utils import build_links, decode_callback, encode_callback, escape, escape_html, format_file_size


def test_build_links_with_valid_md5():
    md5 = "7a7ef891b9d2b2ae8d9cd864556f7cd8"
    links = build_links(md5)
    assert len(links) == 3
    assert links[0][0] == "Libgen.li"
    assert links[0][1] == f"https://libgen.li/ads.php?md5={md5}"

    assert links[1][0] == "Libgen.la"
    assert links[1][1] == f"https://libgen.la/ads.php?md5={md5}"

    assert links[2][0] == "Libgen.is"
    assert links[2][1] == f"https://libgen.is/book/index.php?md5={md5}"


def test_build_links_with_none_or_empty():
    assert build_links(None) == []
    assert build_links("") == []
    assert build_links("   ") == []


def test_html_escaping():
    raw = "The Rust & C++ Guide: <Advanced> [Vol. 1] *Fast* _Safe_"
    escaped = escape_html(raw)
    assert "&amp;" in escaped
    assert "&lt;" in escaped
    assert "&gt;" in escaped
    assert "<" not in escaped
    assert ">" not in escaped
    # _ and * are preserved as plaintext (safe for HTML parse mode, unlike MarkdownV2)
    assert "_Safe_" in escaped
    assert "*Fast*" in escaped
    assert "[Vol. 1]" in escaped

    # Alias check
    assert escape(raw) == escaped
    assert escape(None) == ""


def test_callback_encoding_and_decoding():
    enc = encode_callback("book", 42)
    assert enc == "book:42"
    act, args = decode_callback(enc)
    assert act == "book"
    assert args == ["42"]

    enc_ref = encode_callback("refresh", "abcd1234")
    assert enc_ref == "refresh:abcd1234"
    act_ref, args_ref = decode_callback(enc_ref)
    assert act_ref == "refresh"
    assert args_ref == ["abcd1234"]


def test_format_file_size():
    assert format_file_size(500) == "500 B"
    assert format_file_size(283648) == "277.00 KB"
    assert format_file_size(1048576) == "1.00 MB"
    assert format_file_size(None) == "Unknown size"
    assert format_file_size(0) == "Unknown size"
