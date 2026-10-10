"""Anna's Archive (annas-archive.org / annas-archive.se) search and detail parser.

Secondary upstream source fallback when Libgen mirrors fail or return 0 hits.
Adheres strictly to the zero-server-storage link resolver architecture.
"""

from __future__ import annotations

import html
from html.parser import HTMLParser
import logging
import re
from urllib.parse import quote_plus

from models import Mirror, SearchHit
from sources.base import SourceParser, UpstreamInvalidResponseError
from sources.libgen import _check_challenge_page, _clean_text, parse_size

logger = logging.getLogger(__name__)

_KNOWN_EXTENSIONS = frozenset({
    "epub", "pdf", "mobi", "azw3", "djvu", "fb2", "cbr", "cbz", "txt", "rtf", "doc", "docx"
})


class _AnnasHTMLParser(HTMLParser):
    """Parses Anna's Archive search result HTML extracting book entries."""

    def __init__(self) -> None:
        super().__init__()
        self.hits: list[dict[str, str]] = []
        self._current_md5: str | None = None
        self._current_h3: list[str] = []
        self._current_author: list[str] = []
        self._in_h3 = False
        self._in_author = False
        self._all_texts: list[str] = []
        self._in_a = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "a":
            href = attr_dict.get("href", "")
            m = re.search(r"/md5/([a-fA-F0-9]{32})", href)
            if m:
                # If we were already in an md5 anchor, close previous
                if self._current_md5:
                    self._save_hit()
                self._current_md5 = m.group(1).lower()
                self._in_a = True
                self._current_h3 = []
                self._current_author = []
                self._all_texts = []
        elif tag == "h3" and self._in_a:
            self._in_h3 = True
        elif self._in_a and (
            "italic" in attr_dict.get("class", "").lower()
            or "author" in attr_dict.get("class", "").lower()
        ):
            self._in_author = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._in_h3:
            self._in_h3 = False
        elif self._in_author and tag in ("div", "span", "p"):
            self._in_author = False
        elif tag == "a" and self._in_a:
            self._in_a = False
            if self._current_md5:
                self._save_hit()

    def handle_data(self, data: str) -> None:
        if self._in_h3:
            self._current_h3.append(data)
        if self._in_author:
            self._current_author.append(data)
        if self._in_a:
            self._all_texts.append(data)

    def _save_hit(self) -> None:
        if not self._current_md5:
            return
        h3_text = " ".join("".join(self._current_h3).split()).strip()
        author_text = " ".join("".join(self._current_author).split()).strip()
        all_text = " ".join("".join(self._all_texts).split()).strip()
        self.hits.append({
            "md5": self._current_md5,
            "h3": h3_text,
            "author": author_text,
            "full_text": all_text,
        })
        self._current_md5 = None
        self._current_h3 = []
        self._current_author = []
        self._all_texts = []


class AnnasParser:
    """Parser for Anna's Archive mirrors."""

    def search_url(self, mirror: Mirror, query: str, page: int = 1) -> str:
        """Construct Anna's Archive search URL."""
        base = mirror.url.rstrip("/")
        encoded_q = quote_plus(query.strip())
        if page > 1:
            return f"{base}/search?q={encoded_q}&page={page}"
        return f"{base}/search?q={encoded_q}"

    def detail_url(self, mirror: Mirror, md5: str) -> str:
        """Construct detail/download page URL for MD5."""
        base = mirror.url.rstrip("/")
        clean_md5 = md5.strip().lower()
        return f"{base}/md5/{clean_md5}"

    def parse(self, html_text: str, mirror: Mirror) -> list[SearchHit]:
        """Parse search results from Anna's Archive HTML."""
        if not html_text:
            return []

        _check_challenge_page(html_text, mirror)

        parser = _AnnasHTMLParser()
        try:
            parser.feed(html_text)
            if parser._current_md5:
                parser._save_hit()
            extracted = parser.hits
        except Exception as exc:
            logger.debug("[annas] HTMLParser failed: %s, falling back to regex", exc)
            extracted = []

        # If HTMLParser found nothing, try regex-based block extraction
        if not extracted:
            extracted = self._regex_parse(html_text)

        hits: list[SearchHit] = []
        seen_md5s: set[str] = set()

        for item in extracted:
            md5 = item["md5"]
            if md5 in seen_md5s:
                continue
            seen_md5s.add(md5)

            h3_text = item.get("h3", "").strip()
            full_text = item.get("full_text", "").strip()

            title = h3_text if h3_text else (full_text.split("·")[0].split("\n")[0].strip() or "Unknown Title")
            author: str | None = item.get("author") or None
            extension: str | None = None
            filesize: int | None = None

            # Parse metadata tokens from full_text
            # Typical text: 'Title [Author] English [en], epub, 4.2MB'
            # Look for extension
            for ext in _KNOWN_EXTENSIONS:
                if re.search(rf"\b{ext}\b", full_text, re.IGNORECASE):
                    extension = ext
                    break

            # Look for file size (e.g., 4.2MB, 500KB, 1.2 GB)
            size_m = re.search(r"(\b[\d.]+\s*(?:KB|MB|GB|B|kB|Mb|Gb)\b)", full_text)
            if size_m:
                filesize = parse_size(size_m.group(1))

            # Attempt author extraction if not already found
            if not author and full_text and h3_text and h3_text in full_text:
                remaining = full_text.replace(h3_text, "").strip()
                # If there are metadata fragments like 'Author Name · English [en], epub...'
                parts = [p.strip() for p in re.split(r"[,·|•]", remaining) if p.strip()]
                for part in parts:
                    if not any(re.search(rf"\b{ext}\b", part, re.IGNORECASE) for ext in _KNOWN_EXTENSIONS):
                        if not re.search(r"[\d.]+\s*(?:KB|MB|GB)", part, re.IGNORECASE):
                            if not re.search(r"\[\w{2}\]", part):  # not language code like [en]
                                if len(part) > 2 and len(part) < 60:
                                    author = part
                                    break

            detail = self.detail_url(mirror, md5)
            hits.append(
                SearchHit(
                    source="annas",
                    source_id=md5,
                    title=title,
                    author=author,
                    extension=extension,
                    filesize=filesize,
                    detail_url=detail,
                    md5=md5,
                )
            )

        return hits

    def _regex_parse(self, html_text: str) -> list[dict[str, str]]:
        """Fallback regex extraction of Anna's Archive links."""
        results: list[dict[str, str]] = []
        # Find all /md5/<hash> anchors
        pattern = re.compile(
            r'<a[^>]+href=["\'](?:https?://[^/]+)?/md5/([a-fA-F0-9]{32})["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        for m in pattern.finditer(html_text):
            md5 = m.group(1).lower()
            inner = m.group(2)
            h3_m = re.search(r'<h3[^>]*>(.*?)</h3>', inner, re.DOTALL | re.IGNORECASE)
            h3_text = _clean_text(h3_m.group(1)) if h3_m else ""
            full_text = _clean_text(inner)
            results.append({
                "md5": md5,
                "h3": h3_text,
                "full_text": full_text,
            })
        return results


annas_parser = AnnasParser()
