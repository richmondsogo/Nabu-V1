"""Unit tests for Anna's Archive source adapter and SourceResolver integration."""

from __future__ import annotations

from pathlib import Path
import pytest
import httpx

from models import DownloadHandle, SearchHit
from sources.annas import AnnasSource
from sources.libgen import LibgenSource
from sources.resolver import SourceResolver

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_annas_parse_search_html() -> None:
    html_file = FIXTURES_DIR / "annas_search.html"
    assert html_file.exists()
    html = html_file.read_text(encoding="utf-8")

    source = AnnasSource()
    hits = source.parse_search_html(html, "https://annas-archive.org")

    assert len(hits) == 2

    hit1 = hits[0]
    assert hit1.source == "annas"
    assert hit1.source_id == "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert hit1.title == "Design Patterns: Elements of Reusable Object-Oriented Software"
    assert "Erich Gamma" in (hit1.author or "")
    assert hit1.extension == "pdf"
    assert hit1.filesize == int(5.2 * 1024 * 1024)
    assert hit1.year == "1994"
    assert hit1.md5 == "a1b2c3d4e5f60718293a4b5c6d7e8f90"

    hit2 = hits[1]
    assert hit2.source_id == "11223344556677889900aabbccddeeff"
    assert hit2.title == "Patterns of Enterprise Application Architecture"
    assert hit2.author == "Martin Fowler"
    assert hit2.extension == "epub"
    assert hit2.filesize == int(3.1 * 1024 * 1024)
    assert hit2.year == "2002"


@pytest.mark.asyncio
async def test_annas_search_with_mock_transport() -> None:
    html = (FIXTURES_DIR / "annas_search.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/search" in str(request.url)
        assert request.url.params["q"] == "design patterns"
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    source = AnnasSource(polite_delay_ms=0, transport=transport)

    hits = await source.search("design patterns", limit=1)
    assert len(hits) == 1
    assert hits[0].title == "Design Patterns: Elements of Reusable Object-Oriented Software"
    await source.close()


@pytest.mark.asyncio
async def test_annas_resolve_cid_first_route() -> None:
    html = (FIXTURES_DIR / "annas_detail_cid.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    source = AnnasSource(polite_delay_ms=0, transport=transport)

    hit = SearchHit(
        source="annas",
        source_id="a1b2c3d4e5f60718293a4b5c6d7e8f90",
        title="Design Patterns",
        md5="a1b2c3d4e5f60718293a4b5c6d7e8f90",
        detail_url="https://annas-archive.org/md5/a1b2c3d4e5f60718293a4b5c6d7e8f90",
    )

    handle = await source.resolve(hit)
    assert handle.kind == "cid"
    assert handle.cid == "bafykbzaced8394jsdf832kdf9382kdsfjsf9238kdsf9382kdlsf93829dkfjs9d"
    await source.close()


@pytest.mark.asyncio
async def test_annas_resolve_fast_api_key() -> None:
    detail_html = "<html><body>No CID here</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if "fast_download.json" in str(request.url):
            assert request.url.params["key"] == "secret_test_key"
            assert request.url.params["md5"] == "11223344556677889900aabbccddeeff"
            return httpx.Response(200, json={"download_url": "https://fast.annas-archive.org/download.epub"})
        return httpx.Response(200, text=detail_html)

    transport = httpx.MockTransport(handler)
    source = AnnasSource(api_key="secret_test_key", polite_delay_ms=0, transport=transport)

    hit = SearchHit(
        source="annas",
        source_id="11223344556677889900aabbccddeeff",
        title="Fast Download Book",
        md5="11223344556677889900aabbccddeeff",
    )

    handle = await source.resolve(hit)
    assert handle.kind == "url"
    assert handle.url == "https://fast.annas-archive.org/download.epub"
    await source.close()


@pytest.mark.asyncio
async def test_annas_resolve_slow_download_polling() -> None:
    detail_html = (FIXTURES_DIR / "annas_detail_slow.html").read_text(encoding="utf-8")
    slow_html = (FIXTURES_DIR / "annas_slow_page.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if "/slow_download/" in str(request.url):
            return httpx.Response(200, text=slow_html)
        return httpx.Response(200, text=detail_html)

    transport = httpx.MockTransport(handler)
    source = AnnasSource(polite_delay_ms=0, transport=transport)

    hit = SearchHit(
        source="annas",
        source_id="11223344556677889900aabbccddeeff",
        title="Slow Download Book",
        md5="11223344556677889900aabbccddeeff",
        detail_url="https://annas-archive.org/md5/11223344556677889900aabbccddeeff",
    )

    handle = await source.resolve(hit)
    assert handle.kind == "url"
    assert handle.url == "https://download.annas-archive.org/file/11223344556677889900aabbccddeeff.epub"
    await source.close()


@pytest.mark.asyncio
async def test_annas_mirror_rotation_on_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(handler)
    source = AnnasSource(
        mirrors=("https://annas-archive.org", "https://annas-archive.se"),
        polite_delay_ms=0,
        transport=transport,
    )

    assert source.current_mirror == "https://annas-archive.org"
    resp = await source.request_with_retry("GET", "https://annas-archive.org/search", max_attempts=2)
    assert resp.status_code == 503
    assert source.current_mirror == "https://annas-archive.se"
    await source.close()


@pytest.mark.asyncio
async def test_annas_download_streaming_and_oversize_abort(tmp_path: Path) -> None:
    payload = b"%PDF-1.4 mock annas pdf download content"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    transport = httpx.MockTransport(handler)
    source = AnnasSource(polite_delay_ms=0, transport=transport)

    handle = DownloadHandle(
        kind="url",
        url="https://download.annas-archive.org/test.pdf",
        source="annas",
    )
    dest = tmp_path / "valid.pdf"
    written = await source.download(handle, dest, max_bytes=50000, timeout=10.0)
    assert written == len(payload)
    assert dest.exists()
    assert dest.read_bytes() == payload

    # Oversize test
    dest_oversize = tmp_path / "oversize.part"
    with pytest.raises(ValueError, match="Download exceeded max size"):
        await source.download(handle, dest_oversize, max_bytes=10, timeout=10.0)

    assert not dest_oversize.exists()
    await source.close()


@pytest.mark.asyncio
async def test_annas_and_libgen_resolver_integration() -> None:
    annas_html = (FIXTURES_DIR / "annas_search.html").read_text(encoding="utf-8")
    annas_detail = (FIXTURES_DIR / "annas_detail_cid.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if "/md5/" in str(request.url):
            return httpx.Response(200, text=annas_detail)
        return httpx.Response(200, text=annas_html)

    transport = httpx.MockTransport(handler)
    src_annas = AnnasSource(polite_delay_ms=0, transport=transport)
    src_libgen = LibgenSource(polite_delay_ms=0)

    resolver = SourceResolver(
        sources={"annas": src_annas, "libgen": src_libgen},
        priority=("annas", "libgen"),
    )

    # 1. Primary Anna's Archive search yields hits
    hits = await resolver.search("Design Patterns", limit=2)
    assert len(hits) == 2
    assert hits[0].source == "annas"

    # 2. Resolving hit yields CID
    source, handle = await resolver.resolve(hits[0])
    assert handle.kind == "cid"
    assert "bafykbzaced" in (handle.cid or "")

    await resolver.close()
