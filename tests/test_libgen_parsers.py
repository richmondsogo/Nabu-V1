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
