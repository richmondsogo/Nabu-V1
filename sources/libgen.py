"""Library Genesis (Libgen) adapter for Nabu-V1."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
from typing import Any
import httpx

from models import DownloadHandle, SearchHit
from sources.base import BaseSource

logger = logging.getLogger(__name__)


class LibgenSource(BaseSource):
    """Source adapter for Library Genesis mirrors."""

    name = "libgen"

    def __init__(
        self,
        mirrors: tuple[str, ...] = ("https://libgen.is", "https://libgen.rs", "https://libgen.st"),
        polite_delay_ms: int = 750,
        scrape_timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            mirrors=mirrors,
            polite_delay_ms=polite_delay_ms,
            scrape_timeout=scrape_timeout,
            transport=transport,
        )

    def parse_search_html(self, html: str, base_url: str) -> list[SearchHit]:
        """Parse Libgen search table HTML into SearchHit objects."""
        hits: list[SearchHit] = []

        # Find all table rows; header rows with <th> or non-data rows will have len(cols) < 10
        row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
        rows = row_pattern.findall(html)

        for row in rows:
            cols = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            if len(cols) < 10:
                continue

            try:
                # Column 0: ID
                source_id = re.sub(r"<[^>]+>", "", cols[0]).strip()

                # Column 1: Author(s)
                author = re.sub(r"<[^>]+>", "", cols[1]).strip()

                # Column 2: Title and details
                title_col = cols[2]
                # Extract clean title text from <a> tag
                title_match = re.search(r"<a[^>]*>(.*?)</a>", title_col, re.DOTALL | re.IGNORECASE)
                raw_title = title_match.group(1) if title_match else title_col
                title = re.sub(r"<[^>]+>", "", raw_title).strip()
                if not title:
                    continue

                # Column 4: Year
                year = re.sub(r"<[^>]+>", "", cols[4]).strip()

                # Column 6: Language
                language = re.sub(r"<[^>]+>", "", cols[6]).strip()

                # Column 7: Size (e.g. "4 Mb")
                size_str = re.sub(r"<[^>]+>", "", cols[7]).strip()
                filesize = self._parse_size(size_str)

                # Column 8: Extension (e.g. "pdf", "epub")
                extension = re.sub(r"<[^>]+>", "", cols[8]).strip().lower()

                # Column 9: Mirror 1 link (usually library.lol or get.php)
                mirror_match = re.search(r'href=["\']([^"\']+)["\']', cols[9], re.IGNORECASE)
                detail_url = mirror_match.group(1) if mirror_match else ""

                # Extract MD5 if present in detail_url (e.g. /main/0123456789abcdef... or md5=...)
                md5 = None
                md5_match = re.search(r"[a-fA-F0-9]{32}", detail_url)
                if md5_match:
                    md5 = md5_match.group(0).lower()

                hit = SearchHit(
                    source=self.name,
                    source_id=source_id,
                    title=title,
                    author=author or None,
                    year=year or None,
                    language=language or None,
                    extension=extension or None,
                    filesize=filesize,
                    detail_url=detail_url or None,
                    md5=md5,
                )
                hits.append(hit)

            except Exception as exc:
                logger.debug("Failed to parse Libgen row: %s", exc)
                continue

        return hits

    @staticmethod
    def _parse_size(size_str: str) -> int | None:
        """Parse size strings like '4.2 Mb', '850 Kb' into integer bytes."""
        m = re.search(r"([\d.]+)\s*([a-zA-Z]+)", size_str)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2).lower()
        if "kb" in unit or "k" in unit:
            return int(val * 1024)
        if "mb" in unit or "m" in unit:
            return int(val * 1024 * 1024)
        if "gb" in unit or "g" in unit:
            return int(val * 1024 * 1024 * 1024)
        return int(val)

    async def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        """Search Libgen mirrors for matching books."""
        mirror = self.current_mirror
        url = f"{mirror}/search.php"
        params = {
            "req": query,
            "res": "25",
            "column": "def",
            "sort": "def",
            "sortmode": "ASC",
        }

        try:
            resp = await self.request_with_retry("GET", url, params=params)
            if resp.status_code != 200:
                logger.warning("[%s] Search returned status %d", self.name, resp.status_code)
                return []

            hits = self.parse_search_html(resp.text, mirror)
            logger.info("[%s] Found %d candidate hits for query %r", self.name, len(hits), query)
            return hits[:limit]

        except Exception as exc:
            logger.error("[%s] Search failed on mirror %s: %s", self.name, mirror, exc)
            self.set_cooldown(180.0)
            return []

    async def resolve(self, hit: SearchHit) -> DownloadHandle:
        """Resolve Libgen SearchHit into a direct download URL or CID.

        Fetches the detail page (e.g. library.lol) to discover the direct file download link
        or IPFS gateway reference.
        """
        if not hit.detail_url:
            raise ValueError(f"[{self.name}] SearchHit carries no detail_url to resolve")

        detail_url = hit.detail_url
        if not detail_url.startswith("http"):
            detail_url = f"{self.current_mirror}/{detail_url.lstrip('/')}"

        logger.info("[%s] Resolving download for %r via %s", self.name, hit.title, detail_url)
        resp = await self.request_with_retry("GET", detail_url)
        if resp.status_code != 200:
            raise RuntimeError(f"[{self.name}] Failed to resolve {detail_url}: HTTP {resp.status_code}")

        html = resp.text

        # 1. Check if an IPFS CID is exposed on the detail page
        cid_match = re.search(r"bafy[a-z0-9]{55,}", html) or re.search(r"Qm[a-zA-Z0-9]{44}", html)
        if cid_match:
            cid = cid_match.group(0)
            logger.info("[%s] Found direct IPFS CID %s on detail page", self.name, cid)
            return DownloadHandle(
                kind="cid",
                cid=cid,
                source=self.name,
                md5=hit.md5,
                filesize=hit.filesize,
                extension=hit.extension,
            )

        # 2. Extract direct GET link (e.g. <a href="...download link...">GET</a>)
        get_match = re.search(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>GET</a>', html, re.IGNORECASE)
        if not get_match:
            # Fallback: look for Cloudflare or IPFS links on library.lol
            get_match = re.search(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>Cloudflare</a>', html, re.IGNORECASE)

        if not get_match:
            # Fallback: first download link in download block
            get_match = re.search(r'<h2><a\s+[^>]*href=["\']([^"\']+)["\']', html, re.IGNORECASE)

        if not get_match:
            raise RuntimeError(f"[{self.name}] Could not locate direct download link on {detail_url}")

        direct_url = get_match.group(1)
        logger.info("[%s] Resolved direct HTTP download URL: %s", self.name, direct_url)
        return DownloadHandle(
            kind="url",
            url=direct_url,
            source=self.name,
            md5=hit.md5,
            filesize=hit.filesize,
            extension=hit.extension,
        )

    async def download(
        self,
        handle: DownloadHandle,
        dest: Path,
        max_bytes: int,
        timeout: float,
    ) -> int:
        """Stream file from handle.url to destination path with byte limits."""
        if handle.kind != "url" or not handle.url:
            raise ValueError(f"[{self.name}] Handle must be of kind='url' with a valid URL")

        dest.parent.mkdir(parents=True, exist_ok=True)
        total_written = 0

        try:
            async with asyncio.timeout(timeout):
                async with self._client.stream("GET", handle.url) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(f"Download returned HTTP {resp.status_code}")

                    with open(dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            total_written += len(chunk)
                            if total_written > max_bytes:
                                raise ValueError(f"Download exceeded max size ({total_written} > {max_bytes} bytes)")
                            f.write(chunk)

            logger.info("[%s] Download complete: %d bytes written to %s", self.name, total_written, dest.name)
            return total_written

        except Exception:
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            raise
