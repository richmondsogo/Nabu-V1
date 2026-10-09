"""Unit tests for shadow library sources, Libgen adapter, and SourceResolver."""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
import httpx

from models import DownloadHandle, SearchHit
from sources.base import BaseSource
from sources.libgen import LibgenSource
from sources.resolver import SourceResolver

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class DummySource(BaseSource):
    """Dummy test source for testing SourceResolver."""

    name = "dummy"

    def __init__(self, search_results: list[SearchHit] | None = None) -> None:
        super().__init__(mirrors=("https://dummy.example.com",))
        self.search_results = search_results or []
        self.search_called_count = 0

    async def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        self.search_called_count += 1
        return self.search_results[:limit]

    async def resolve(self, hit: SearchHit) -> DownloadHandle:
        return DownloadHandle(
            kind="url",
            url=f"https://dummy.example.com/download/{hit.source_id}",
            source=self.name,
            md5=hit.md5,
        )

    async def download(
        self,
        handle: DownloadHandle,
        dest: Path,
        max_bytes: int,
        timeout: float,
    ) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 dummy content")
        return len(b"%PDF-1.4 dummy content")


def test_libgen_parse_search_html() -> None:
    html_file = FIXTURES_DIR / "libgen_search.html"
    assert html_file.exists()
    html = html_file.read_text(encoding="utf-8")

    source = LibgenSource()
    hits = source.parse_search_html(html, "https://libgen.is")

    assert len(hits) == 2

    hit1 = hits[0]
    assert hit1.source == "libgen"
    assert hit1.source_id == "1001"
    assert hit1.title == "Clean Code: A Handbook of Agile Software Craftsmanship"
    assert hit1.author == "Robert C. Martin"
    assert hit1.year == "2008"
    assert hit1.language == "English"
    assert hit1.extension == "pdf"
    assert hit1.filesize == 4718592
    assert hit1.md5 == "0123456789abcdef0123456789abcdef"
    assert hit1.detail_url == "http://library.lol/main/0123456789abcdef0123456789abcdef"

    hit2 = hits[1]
    assert hit2.source_id == "1002"
    assert hit2.title == "The Pragmatic Programmer: Your Journey To Mastery"
    assert hit2.author == "Andrew Hunt, David Thomas"
    assert hit2.year == "2019"
    assert hit2.extension == "epub"
    assert hit2.filesize == 2936012
    assert hit2.md5 == "fedcba9876543210fedcba9876543210"


@pytest.mark.asyncio
async def test_libgen_search_with_mock_transport() -> None:
    html = (FIXTURES_DIR / "libgen_search.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "search.php" in str(request.url)
        assert request.url.params["req"] == "clean code"
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    source = LibgenSource(polite_delay_ms=0, transport=transport)

    hits = await source.search("clean code", limit=1)
    assert len(hits) == 1
    assert hits[0].source_id == "1001"
    await source.close()


@pytest.mark.asyncio
async def test_libgen_resolve_direct_get_url() -> None:
    html = (FIXTURES_DIR / "libgen_detail_get.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    source = LibgenSource(polite_delay_ms=0, transport=transport)

    hit = SearchHit(
        source="libgen",
        source_id="1001",
        title="Clean Code",
        detail_url="http://library.lol/main/0123456789abcdef0123456789abcdef",
        md5="0123456789abcdef0123456789abcdef",
    )

    handle = await source.resolve(hit)
    assert handle.kind == "url"
    assert handle.url == "https://download.library.lol/main/1001/0123456789abcdef/Clean_Code.pdf"
    assert handle.md5 == hit.md5
    await source.close()


@pytest.mark.asyncio
async def test_libgen_resolve_direct_ipfs_cid() -> None:
    html = (FIXTURES_DIR / "libgen_detail_cid.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    source = LibgenSource(polite_delay_ms=0, transport=transport)

    hit = SearchHit(
        source="libgen",
        source_id="1002",
        title="The Pragmatic Programmer",
        detail_url="http://library.lol/main/fedcba9876543210fedcba9876543210",
        md5="fedcba9876543210fedcba9876543210",
    )

    handle = await source.resolve(hit)
    assert handle.kind == "cid"
    assert handle.cid == "bafykbzaced572s64zfvsmv7kld47e25ptz7evydr7hhy4hgbq77o4qjty4d7q"
    await source.close()


@pytest.mark.asyncio
async def test_libgen_mirror_rotation_on_failure() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(handler)
    source = LibgenSource(
        mirrors=("https://libgen.is", "https://libgen.rs", "https://libgen.st"),
        polite_delay_ms=0,
        transport=transport,
    )

    assert source.current_mirror == "https://libgen.is"
    # request_with_retry backs off and then rotates mirror on exhausted retries
    resp = await source.request_with_retry("GET", "https://libgen.is/search.php", max_attempts=2)
    assert resp.status_code == 503
    # Mirror rotated to next
    assert source.current_mirror == "https://libgen.rs"
    await source.close()


@pytest.mark.asyncio
async def test_libgen_download_stream_success(tmp_path: Path) -> None:
    payload = b"%PDF-1.4 mock pdf binary stream content"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    transport = httpx.MockTransport(handler)
    source = LibgenSource(polite_delay_ms=0, transport=transport)

    handle = DownloadHandle(
        kind="url",
        url="https://download.library.lol/file.pdf",
        source="libgen",
    )
    dest = tmp_path / "downloaded.pdf"
    written = await source.download(handle, dest, max_bytes=50000, timeout=10.0)

    assert written == len(payload)
    assert dest.exists()
    assert dest.read_bytes() == payload
    await source.close()


@pytest.mark.asyncio
async def test_libgen_download_oversize_aborts_and_cleans_up(tmp_path: Path) -> None:
    # 100 KB payload
    payload = b"X" * 102400

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    transport = httpx.MockTransport(handler)
    source = LibgenSource(polite_delay_ms=0, transport=transport)

    handle = DownloadHandle(
        kind="url",
        url="https://download.library.lol/file.pdf",
        source="libgen",
    )
    dest = tmp_path / "oversize.part"

    # Limit to 50 KB
    with pytest.raises(ValueError, match="Download exceeded max size"):
        await source.download(handle, dest, max_bytes=51200, timeout=10.0)

    # Verification: partial file must be cleaned up
    assert not dest.exists()
    await source.close()


@pytest.mark.asyncio
async def test_source_resolver_priority_and_deduplication() -> None:
    hit_primary = SearchHit(
        source="annas",
        source_id="aa-1",
        title="Refactoring",
        author="Martin Fowler",
        md5="11111111111111111111111111111111",
    )
    hit_duplicate_md5 = SearchHit(
        source="libgen",
        source_id="lg-1",
        title="Refactoring: Improving Code Design",
        author="Martin Fowler",
        md5="11111111111111111111111111111111",
    )
    hit_secondary = SearchHit(
        source="libgen",
        source_id="lg-2",
        title="Clean Architecture",
        author="Robert C. Martin",
        md5="22222222222222222222222222222222",
        cid="bafykbzac...cid",
    )

    src_annas = DummySource(search_results=[hit_primary])
    src_annas.name = "annas"

    src_libgen = DummySource(search_results=[hit_duplicate_md5, hit_secondary])
    src_libgen.name = "libgen"

    resolver = SourceResolver(
        sources={"annas": src_annas, "libgen": src_libgen},
        priority=("annas", "libgen"),
    )

    # Primary returns hits, so resolver stops at annas
    hits = await resolver.search("Refactoring", limit=5)
    assert len(hits) == 1
    assert hits[0].source == "annas"
    assert src_annas.search_called_count == 1
    assert src_libgen.search_called_count == 0


@pytest.mark.asyncio
async def test_source_resolver_fallback_when_primary_empty() -> None:
    src_annas = DummySource(search_results=[])
    src_annas.name = "annas"

    hit_libgen = SearchHit(
        source="libgen",
        source_id="lg-2",
        title="Design Patterns",
        author="Gang of Four",
        md5="33333333333333333333333333333333",
    )
    src_libgen = DummySource(search_results=[hit_libgen])
    src_libgen.name = "libgen"

    resolver = SourceResolver(
        sources={"annas": src_annas, "libgen": src_libgen},
        priority=("annas", "libgen"),
    )

    hits = await resolver.search("Design Patterns", limit=5)
    assert len(hits) == 1
    assert hits[0].source == "libgen"
    assert src_annas.search_called_count == 1
    assert src_libgen.search_called_count == 1


@pytest.mark.asyncio
async def test_source_resolver_skips_cooling_source() -> None:
    src_annas = DummySource(search_results=[])
    src_annas.name = "annas"
    src_annas.set_cooldown(600.0)  # Cooldown for 10 mins

    hit_libgen = SearchHit(
        source="libgen",
        source_id="lg-3",
        title="Code Complete",
        author="Steve McConnell",
    )
    src_libgen = DummySource(search_results=[hit_libgen])
    src_libgen.name = "libgen"

    resolver = SourceResolver(
        sources={"annas": src_annas, "libgen": src_libgen},
        priority=("annas", "libgen"),
    )

    hits = await resolver.search("Code Complete", limit=5)
    assert len(hits) == 1
    assert hits[0].source == "libgen"
    # annas was in cooldown, so its search was skipped completely
    assert src_annas.search_called_count == 0
    assert src_libgen.search_called_count == 1


@pytest.mark.asyncio
async def test_source_resolver_resolve_direct_cid() -> None:
    src = DummySource()
    resolver = SourceResolver(sources={"dummy": src})

    hit_with_cid = SearchHit(
        source="dummy",
        source_id="d-1",
        title="Test Book",
        cid="bafykbzaced572s64zfvsmv7kld47e25ptz7evydr7hhy4hgbq77o4qjty4d7q",
    )

    source, handle = await resolver.resolve(hit_with_cid)
    assert handle.kind == "cid"
    assert handle.cid == hit_with_cid.cid
