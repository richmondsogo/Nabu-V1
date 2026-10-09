from pathlib import Path
from models import Mirror
from sources.libgen import li_parser, is_parser, get_parser_for_mirror

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_li_fork_parser():
    html_path = FIXTURES_DIR / "libgen_li_search.html"
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")

    mirror = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    hits = li_parser.parse(html, mirror)

    assert len(hits) > 0
    first = hits[0]
    assert "Rust Programming Language" in first.title
    assert first.author is not None
    assert first.extension in ("epub", "pdf")
    assert first.filesize is not None and first.filesize > 0
    assert first.md5 == "7a7ef891b9d2b2ae8d9cd864556f7cd8"
    assert first.detail_url == "https://libgen.li/ads.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8"


def test_is_fork_parser():
    html_path = FIXTURES_DIR / "libgen_search.html"
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")

    mirror = Mirror(id=2, source="libgen", url="https://libgen.is", fork="is")
    hits = is_parser.parse(html, mirror)

    assert len(hits) == 2
    clean_code = hits[0]
    assert clean_code.title == "Clean Code: A Handbook of Agile Software Craftsmanship"
    assert clean_code.author == "Robert C. Martin"
    assert clean_code.year == "2008"
    assert clean_code.extension == "pdf"
    assert clean_code.filesize == int(4.5 * 1024 * 1024)
    assert clean_code.md5 == "0123456789abcdef0123456789abcdef"
    assert clean_code.detail_url == "https://libgen.is/book/index.php?md5=0123456789abcdef0123456789abcdef"


def test_parser_selection_by_fork():
    m_li = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    m_is = Mirror(id=2, source="libgen", url="https://libgen.is", fork="is")

    assert get_parser_for_mirror(m_li) is li_parser
    assert get_parser_for_mirror(m_is) is is_parser


def test_row_without_md5():
    fake_html = """
    <table>
      <tr><th>Title</th><th>Author</th><th>Col3</th><th>Year</th><th>Lang</th><th>Col5</th><th>Size</th><th>Ext</th><th>Mirrors</th></tr>
      <tr>
        <td>Book Without MD5</td>
        <td>Jane Doe</td>
        <td>extra</td>
        <td>2021</td>
        <td>English</td>
        <td>extra</td>
        <td>500 kB</td>
        <td>pdf</td>
        <td><a href="broken_link.php">link</a></td>
      </tr>
    </table>
    """
    mirror = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    hits = li_parser.parse(fake_html, mirror)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "Book Without MD5"
    assert hit.author == "Jane Doe"
    assert hit.md5 is None
    assert hit.detail_url is None


def test_search_url_pagination():
    m_li = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    m_is = Mirror(id=2, source="libgen", url="https://libgen.is", fork="is")

    url_li_p1 = li_parser.search_url(m_li, "rust", page=1)
    url_li_p2 = li_parser.search_url(m_li, "rust", page=2)
    assert "page=2" in url_li_p2
    assert "page=" not in url_li_p1

    url_is_p1 = is_parser.search_url(m_is, "rust", page=1)
    url_is_p2 = is_parser.search_url(m_is, "rust", page=2)
    assert "page=2" in url_is_p2
    assert "page=" not in url_is_p1


def test_missing_optional_fields_parsing():
    # Test li_parser with missing year, language, extension, size
    fake_html_li = """
    <table>
      <tr>
        <td>Minimal Book</td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td><a href="ads.php?md5=abcdef1234567890abcdef1234567890">1</a></td>
      </tr>
    </table>
    """
    mirror_li = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    hits_li = li_parser.parse(fake_html_li, mirror_li)
    assert len(hits_li) == 1
    assert hits_li[0].title == "Minimal Book"
    assert hits_li[0].author is None
    assert hits_li[0].year is None
    assert hits_li[0].md5 == "abcdef1234567890abcdef1234567890"

    # Test is_parser with missing optional fields
    fake_html_is = """
    <table>
      <tr bgcolor="#C0C0C0"><td>ID</td><td>Author</td><td>Title</td><td>Publisher</td><td>Year</td><td>Pages</td><td>Language</td><td>Size</td><td>Extension</td><td>Mirrors</td></tr>
      <tr>
        <td>1</td>
        <td></td>
        <td><a href="book/index.php?md5=11223344556677889900aabbccddeeff">Sparse Is Book</a></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td><a href="http://libgen.is/get.php?md5=11223344556677889900aabbccddeeff">[1]</a></td>
      </tr>
    </table>
    """
    mirror_is = Mirror(id=2, source="libgen", url="https://libgen.is", fork="is")
    hits_is = is_parser.parse(fake_html_is, mirror_is)
    assert len(hits_is) == 1
    assert hits_is[0].title == "Sparse Is Book"
    assert hits_is[0].author is None
    assert hits_is[0].year is None
    assert hits_is[0].md5 == "11223344556677889900aabbccddeeff"


def test_extract_get_link():
    from sources.libgen import extract_get_link

    mirror = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    html_sample = """
    <div id="download">
        <h2>Download:</h2>
        <a href="get.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8&key=ABCDEF1234567890">GET</a>
    </div>
    """
    direct_link = extract_get_link(html_sample, mirror, "7a7ef891b9d2b2ae8d9cd864556f7cd8")
    assert direct_link == "https://libgen.li/get.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8&key=ABCDEF1234567890"


def test_extract_get_link_mismatched_or_invalid():
    from sources.libgen import extract_get_link

    mirror = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    html_sample = '<a href="get.php?md5=11111111111111111111111111111111&key=ABCDEF1234567890">GET</a>'
    # MD5 mismatch
    assert extract_get_link(html_sample, mirror, "7a7ef891b9d2b2ae8d9cd864556f7cd8") is None
    # Invalid MD5
    assert extract_get_link(html_sample, mirror, "invalid-md5") is None


import pytest
import httpx


@pytest.mark.asyncio
async def test_resolve_direct_download_link_success():
    from sources.libgen import resolve_direct_download_link

    mirror = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    html_sample = '<a href="get.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8&key=KEY12345678">GET</a>'

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert "ads.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8" in str(request.url)
        return httpx.Response(200, text=html_sample)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    resolved = await resolve_direct_download_link(mirror, "7a7ef891b9d2b2ae8d9cd864556f7cd8", client=client)
    assert resolved == "https://libgen.li/get.php?md5=7a7ef891b9d2b2ae8d9cd864556f7cd8&key=KEY12345678"


@pytest.mark.asyncio
async def test_resolve_direct_download_link_http_failure():
    from sources.libgen import resolve_direct_download_link

    mirror = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    resolved = await resolve_direct_download_link(mirror, "7a7ef891b9d2b2ae8d9cd864556f7cd8", client=client)
    assert resolved is None


def test_challenge_and_nginx_detection_raises_upstream_invalid():
    from sources.base import UpstreamInvalidResponseError

    mirror_li = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    mirror_is = Mirror(id=2, source="libgen", url="https://libgen.is", fork="is")

    nginx_html = "<html><head><title>Welcome to nginx!</title></head><body><h1>Welcome</h1></body></html>"
    cf_html = "<html><head><title>Attention Required! | Cloudflare</title></head><body>cf-browser-verification</body></html>"

    with pytest.raises(UpstreamInvalidResponseError, match=r"(?i)welcome to nginx"):
        li_parser.parse(nginx_html, mirror_li)

    with pytest.raises(UpstreamInvalidResponseError, match=r"(?i)attention required"):
        is_parser.parse(cf_html, mirror_is)


def test_search_url_includes_comics_fiction_and_libgen_topics():
    mirror = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    url = li_parser.search_url(mirror, "hulk")
    assert "topics%5B%5D=l" in url
    assert "topics%5B%5D=c" in url
    assert "topics%5B%5D=f" in url


def test_li_parser_prioritizes_ads_md5_over_arbitrary_hash():
    # Row contains a decoy hash in random text, but genuine MD5 in ads.php
    decoy = "00000000000000000000000000000000"
    real = "11111111111111111111111111111111"
    fake_html = f"""
    <table>
      <tr>
        <td>Edition with decoy {decoy}</td>
        <td>Author</td>
        <td>Col3</td>
        <td>2020</td>
        <td>EN</td>
        <td>Col5</td>
        <td>5 MB</td>
        <td>cbr</td>
        <td><a href="ads.php?md5={real}">Download</a></td>
      </tr>
    </table>
    """
    mirror = Mirror(id=1, source="libgen", url="https://libgen.li", fork="li")
    hits = li_parser.parse(fake_html, mirror)
    assert len(hits) == 1
    assert hits[0].md5 == real


