"""Unit tests for Anna's Archive parser."""

import pytest
from models import Mirror
from sources.annas import AnnasParser, annas_parser
from sources.base import UpstreamInvalidResponseError


SAMPLE_ANNAS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Search results - Anna’s Archive</title></head>
<body>
<main>
  <div class="search-results">
    <a href="/md5/11223344556677889900aabbccddeeff" class="custom-a">
      <div class="content">
        <h3 class="text-xl">Designing Data-Intensive Applications</h3>
        <div class="author italic">Martin Kleppmann</div>
        <div class="meta text-sm">English [en], pdf, 12.4MB, "Designing Data-Intensive Applications"...</div>
      </div>
    </a>
    <a href="/md5/aabbccddeeff00112233445566778899" class="custom-a">
      <div class="content">
        <h3 class="text-xl">Database Internals</h3>
        <div class="author italic">Alex Petrov</div>
        <div class="meta text-sm">English [en], epub, 4.8MB, O'Reilly Media</div>
      </div>
    </a>
  </div>
</main>
</body>
</html>
"""

CHALLENGE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Just a moment...</title></head>
<body>
  <div class="cf-browser-verification">Attention Required! | Cloudflare</div>
</body>
</html>
"""


def test_annas_search_url_construction():
    mirror = Mirror(id=1, source="annas", url="https://annas-archive.org", fork="annas")
    url_p1 = annas_parser.search_url(mirror, "distributed systems", page=1)
    assert url_p1 == "https://annas-archive.org/search?q=distributed+systems"

    url_p2 = annas_parser.search_url(mirror, "distributed systems", page=2)
    assert url_p2 == "https://annas-archive.org/search?q=distributed+systems&page=2"


def test_annas_detail_url_construction():
    mirror = Mirror(id=1, source="annas", url="https://annas-archive.org", fork="annas")
    url = annas_parser.detail_url(mirror, "11223344556677889900AABBCCDDEEFF")
    assert url == "https://annas-archive.org/md5/11223344556677889900aabbccddeeff"


def test_annas_html_parser_success():
    mirror = Mirror(id=1, source="annas", url="https://annas-archive.org", fork="annas")
    hits = annas_parser.parse(SAMPLE_ANNAS_HTML, mirror)
    assert len(hits) == 2

    h1 = hits[0]
    assert h1.title == "Designing Data-Intensive Applications"
    assert h1.author == "Martin Kleppmann"
    assert h1.extension == "pdf"
    assert h1.filesize == 12 * 1024 * 1024 + int(0.4 * 1024 * 1024)
    assert h1.md5 == "11223344556677889900aabbccddeeff"
    assert h1.source == "annas"
    assert "https://annas-archive.org/md5/" in h1.detail_url

    h2 = hits[1]
    assert h2.title == "Database Internals"
    assert h2.author == "Alex Petrov"
    assert h2.extension == "epub"
    assert h2.filesize == 4 * 1024 * 1024 + int(0.8 * 1024 * 1024)
    assert h2.md5 == "aabbccddeeff00112233445566778899"


def test_annas_regex_fallback():
    mirror = Mirror(id=1, source="annas", url="https://annas-archive.se", fork="annas")
    # Malformed HTML where tags are unclosed
    malformed = """
    <div>
      <a href="https://annas-archive.se/md5/abcdefabcdefabcdefabcdefabcdef12">
        <h3>Building Microservices</h3>
        <span>Sam Newman</span> · epub · 3.5MB
      </a>
    </div>
    """
    hits = annas_parser.parse(malformed, mirror)
    assert len(hits) == 1
    assert hits[0].title == "Building Microservices"
    assert hits[0].extension == "epub"
    assert hits[0].md5 == "abcdefabcdefabcdefabcdefabcdef12"


def test_annas_challenge_detection():
    mirror = Mirror(id=1, source="annas", url="https://annas-archive.org", fork="annas")
    with pytest.raises(UpstreamInvalidResponseError):
        annas_parser.parse(CHALLENGE_HTML, mirror)
