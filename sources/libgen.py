"""Library Genesis (Libgen) dual-fork search and detail parsers.

Supports:
- li-fork: libgen.li, libgen.la, libgen.bz (/index.php search, /ads.php detail)
- is-fork: libgen.is, libgen.rs, libgen.st (/search.php search, /book/index.php detail)
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

from models import Mirror, SearchHit
from sources.base import SourceParser

logger = logging.getLogger(__name__)


def parse_size(size_str: str | None) -> int | None:
    """Parse human size strings like '277 kB', '4.5 Mb', '1.2 GB' into integer bytes."""
    if not size_str:
        return None
    m = re.search(r"([\d.]+)\s*([a-zA-Z]+)", size_str.strip())
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2).lower()
    if "kb" in unit or "k" in unit:
        return int(val * 1024)
    if "mb" in unit or "m" in unit:
        return int(val * 1024 * 1024)
    if "gb" in unit or "g" in unit:
        return int(val * 1024 * 1024 * 1024)
    if "b" in unit:
        return int(val)
    return int(val)


def _clean_text(html_fragment: str) -> str:
    """Strip tags and normalize whitespace."""
    no_tags = re.sub(r"<[^>]+>", " ", html_fragment)
    return re.sub(r"\s+", " ", no_tags).strip()


class LiForkParser:
    """Parser for Libgen li-fork mirrors (libgen.li, libgen.la, libgen.bz)."""

    def search_url(self, mirror: Mirror, query: str) -> str:
        q = quote_plus(query)
        base = mirror.url.rstrip("/")
        return f"{base}/index.php?req={q}&columns%5B%5D=t&columns%5B%5D=a&objects%5B%5D=f&topics%5B%5D=l&res=25"

    def detail_url(self, mirror: Mirror, md5: str) -> str:
        base = mirror.url.rstrip("/")
        return f"{base}/ads.php?md5={md5}"

    def parse(self, html: str, mirror: Mirror) -> list[SearchHit]:
        hits: list[SearchHit] = []
        row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
        rows = row_pattern.findall(html)

        for row in rows:
            if "<th" in row.lower():
                continue
            cols = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            if len(cols) < 8:
                continue

            try:
                # Column 0: Title and edition link
                title = _clean_text(cols[0])
                if not title:
                    continue

                # Column 1: Author(s)
                author = _clean_text(cols[1]) or None

                # Column 3: Year
                year = _clean_text(cols[3]) if len(cols) > 3 else None
                year = year if (year and year.isdigit()) else None

                # Column 4: Language
                language = _clean_text(cols[4]) if len(cols) > 4 else None

                # Column 6: Size
                size_str = _clean_text(cols[6]) if len(cols) > 6 else None
                filesize = parse_size(size_str)

                # Column 7: Extension
                extension = _clean_text(cols[7]).lower() if len(cols) > 7 else None

                # MD5 extraction from any href in the row (e.g. ads.php?md5=... or edition.php)
                md5 = None
                md5_matches = re.findall(r"[a-fA-F0-9]{32}", row)
                if md5_matches:
                    md5 = md5_matches[0].lower()

                detail = self.detail_url(mirror, md5) if md5 else None

                hit = SearchHit(
                    source="libgen",
                    source_id=md5 or title[:20],
                    title=title,
                    author=author,
                    year=year,
                    language=language,
                    extension=extension,
                    filesize=filesize,
                    detail_url=detail,
                    md5=md5,
                )
                hits.append(hit)
            except Exception as e:
                logger.debug("Failed parsing li-fork row: %s", e)
                continue

        return hits


class IsForkParser:
    """Parser for Libgen is-fork mirrors (libgen.is, libgen.rs, libgen.st)."""

    def search_url(self, mirror: Mirror, query: str) -> str:
        q = quote_plus(query)
        base = mirror.url.rstrip("/")
        return f"{base}/search.php?req={q}&column=def&res=25"

    def detail_url(self, mirror: Mirror, md5: str) -> str:
        base = mirror.url.rstrip("/")
        return f"{base}/book/index.php?md5={md5}"

    def parse(self, html: str, mirror: Mirror) -> list[SearchHit]:
        hits: list[SearchHit] = []
        row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
        rows = row_pattern.findall(html)

        for row in rows:
            if "<th" in row.lower():
                continue
            cols = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            if len(cols) < 9:
                continue

            try:
                # Column 0: ID
                source_id = _clean_text(cols[0])

                # Column 1: Author(s)
                author = _clean_text(cols[1]) or None

                # Column 2: Title
                title = _clean_text(cols[2])
                if not title:
                    continue

                # Column 4: Year
                year = _clean_text(cols[4]) if len(cols) > 4 else None
                year = year if (year and year.isdigit()) else None

                # Column 6: Language
                language = _clean_text(cols[6]) if len(cols) > 6 else None

                # Column 7: Size
                size_str = _clean_text(cols[7]) if len(cols) > 7 else None
                filesize = parse_size(size_str)

                # Column 8: Extension
                extension = _clean_text(cols[8]).lower() if len(cols) > 8 else None

                # MD5 extraction from column 2 (book/index.php?md5=...) or column 9 (/main/...)
                md5 = None
                search_scope = cols[2] + (cols[9] if len(cols) > 9 else "")
                md5_match = re.search(r"[a-fA-F0-9]{32}", search_scope)
                if md5_match:
                    md5 = md5_match.group(0).lower()
                else:
                    # Fallback across the whole row
                    row_md5 = re.findall(r"[a-fA-F0-9]{32}", row)
                    if row_md5:
                        md5 = row_md5[0].lower()

                detail = self.detail_url(mirror, md5) if md5 else None

                hit = SearchHit(
                    source="libgen",
                    source_id=source_id or md5 or title[:20],
                    title=title,
                    author=author,
                    year=year,
                    language=language,
                    extension=extension,
                    filesize=filesize,
                    detail_url=detail,
                    md5=md5,
                )
                hits.append(hit)
            except Exception as e:
                logger.debug("Failed parsing is-fork row: %s", e)
                continue

        return hits


li_parser = LiForkParser()
is_parser = IsForkParser()


def get_parser_for_mirror(mirror: Mirror) -> SourceParser:
    """Return the appropriate parser based on mirror fork ('li' or 'is')."""
    if mirror.fork == "is":
        return is_parser
    return li_parser
