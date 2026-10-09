"""Anna's Archive (AA) source adapter for Nabu-V1.

Implements CID-first resolution, multi-mirror rotation, search parsing,
slow queue polling, and optional fast API key support.
"""

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


class AnnasSource(BaseSource):
    """Source adapter for Anna's Archive mirrors."""

    name = "annas"

    def __init__(
        self,
        mirrors: tuple[str, ...] = ("https://annas-archive.org", "https://annas-archive.se"),
        api_key: str = "",
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
        self.api_key = api_key.strip()

    def parse_search_html(self, html: str, base_url: str) -> list[SearchHit]:
        """Parse Anna's Archive search HTML into candidate SearchHit items."""
        hits: list[SearchHit] = []
        if not html:
            return hits

        # Find link blocks pointing to /md5/<32-hex>
        md5_block_pattern = re.compile(
            r'<a\s+[^>]*href=["\'](/md5/([a-fA-F0-9]{32}))["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        matches = md5_block_pattern.findall(html)
        seen_md5s: set[str] = set()

        for rel_url, md5, inner_content in matches:
            md5_lower = md5.lower()
            if md5_lower in seen_md5s:
                continue
            seen_md5s.add(md5_lower)

            try:
                # 1. Extract title from <h3> or text
                title_match = re.search(r"<h3[^>]*>(.*?)</h3>", inner_content, re.DOTALL | re.IGNORECASE)
                if title_match:
                    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
                else:
                    # Fallback to bold or text
                    clean_text = re.sub(r"<[^>]+>", " ", inner_content).strip()
                    lines = [ln.strip() for ln in clean_text.split("  ") if ln.strip()]
                    title = lines[0] if lines else ""

                if not title:
                    continue

                # 2. Extract author, publisher, format, size from metadata snippets
                # Anna's Archive typically shows snippets like: "English [en], pdf, 4.2MB, 2021"
                author = None
                year = None
                language = None
                extension = None
                filesize = None

                # Look for format (pdf, epub, mobi, azw3, etc.)
                fmt_match = re.search(r"\b(pdf|epub|mobi|azw3|cbr|cbz|djvu)\b", inner_content, re.IGNORECASE)
                if fmt_match:
                    extension = fmt_match.group(1).lower()

                # Look for 4-digit year
                year_match = re.search(r"\b(19\d\d|20\d\d)\b", inner_content)
                if year_match:
                    year = year_match.group(1)

                # Look for size (e.g. 4.2MB, 850KB)
                size_match = re.search(r"([\d.]+)\s*(MB|KB|GB|B)", inner_content, re.IGNORECASE)
                if size_match:
                    filesize = self._parse_size(f"{size_match.group(1)} {size_match.group(2)}")

                # Look for author in italic or text before title
                author_match = re.search(r"<div[^>]*italic[^>]*>(.*?)</div>", inner_content, re.DOTALL | re.IGNORECASE)
                if author_match:
                    author = re.sub(r"<[^>]+>", "", author_match.group(1)).strip() or None

                hit = SearchHit(
                    source=self.name,
                    source_id=md5_lower,
                    title=title,
                    author=author,
                    year=year,
                    language=language,
                    extension=extension,
                    filesize=filesize,
                    detail_url=f"{base_url.rstrip('/')}{rel_url}",
                    md5=md5_lower,
                )
                hits.append(hit)

            except Exception as exc:
                logger.debug("[%s] Failed to parse hit block: %s", self.name, exc)
                continue

        return hits

    @staticmethod
    def _parse_size(size_str: str) -> int | None:
        """Parse size strings like '4.2 MB' into bytes."""
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
        """Search Anna's Archive for candidate books."""
        mirror = self.current_mirror
        url = f"{mirror}/search"
        params = {"q": query}

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
        """Resolve an Anna's Archive SearchHit into a CID or download URL.
        
        Prioritizes IPFS CID discovery to bypass HTTP downloading entirely.
        """
        # If SearchHit already has a CID, return it immediately
        if hit.cid:
            return DownloadHandle(
                kind="cid",
                cid=hit.cid,
                source=self.name,
                md5=hit.md5,
                filesize=hit.filesize,
                extension=hit.extension,
            )

        detail_url = hit.detail_url
        if not detail_url:
            if hit.md5:
                detail_url = f"{self.current_mirror}/md5/{hit.md5}"
            else:
                raise ValueError(f"[{self.name}] SearchHit carries no detail_url or md5")

        logger.info("[%s] Resolving detail page for %r at %s", self.name, hit.title, detail_url)
        resp = await self.request_with_retry("GET", detail_url)
        if resp.status_code != 200:
            raise RuntimeError(f"[{self.name}] Failed to resolve {detail_url}: HTTP {resp.status_code}")

        html = resp.text

        # 1. CID-FIRST ROUTE: Search detail page for IPFS CIDs
        cid_match = re.search(r"bafy[a-z0-9]{55,}", html) or re.search(r"Qm[a-zA-Z0-9]{44}", html)
        if cid_match:
            cid = cid_match.group(0)
            logger.info("[%s] Discovered IPFS CID %s on detail page. Bypassing HTTP.", self.name, cid)
            return DownloadHandle(
                kind="cid",
                cid=cid,
                source=self.name,
                md5=hit.md5,
                filesize=hit.filesize,
                extension=hit.extension,
            )

        # 2. Fast Download API route if API key configured
        if self.api_key and hit.md5:
            fast_api_url = f"{self.current_mirror}/dyn/api/fast_download.json"
            try:
                fast_resp = await self._client.get(
                    fast_api_url,
                    params={"md5": hit.md5, "key": self.api_key},
                    timeout=httpx.Timeout(15.0),
                )
                if fast_resp.status_code == 200:
                    data = fast_resp.json()
                    download_url = data.get("download_url")
                    if download_url:
                        logger.info("[%s] Fast API key resolved direct download URL", self.name)
                        return DownloadHandle(
                            kind="url",
                            url=download_url,
                            source=self.name,
                            md5=hit.md5,
                            filesize=hit.filesize,
                            extension=hit.extension,
                        )
            except Exception as api_exc:
                logger.warning("[%s] Fast download API request failed: %s", self.name, api_exc)

        # 3. Slow Download queue link
        slow_match = re.search(
            r'<a\s+[^>]*href=["\'](/slow_download/[^"\']+)["\'][^>]*>',
            html,
            re.IGNORECASE,
        )
        if slow_match:
            slow_url = f"{self.current_mirror}{slow_match.group(1)}"
            direct_url = await self._poll_slow_download(slow_url)
            if direct_url:
                return DownloadHandle(
                    kind="url",
                    url=direct_url,
                    source=self.name,
                    md5=hit.md5,
                    filesize=hit.filesize,
                    extension=hit.extension,
                )

        # 4. External partner or mirror download links on page
        ext_match = re.search(
            r'<a\s+[^>]*href=["\'](https?://(?:library\.lol|libgen\.[a-z]+|download\.[^"\']+)/[^"\']+)["\'][^>]*>',
            html,
            re.IGNORECASE,
        )
        if ext_match:
            ext_url = ext_match.group(1)
            logger.info("[%s] Resolved external partner download link: %s", self.name, ext_url)
            return DownloadHandle(
                kind="url",
                url=ext_url,
                source=self.name,
                md5=hit.md5,
                filesize=hit.filesize,
                extension=hit.extension,
            )

        raise RuntimeError(f"[{self.name}] Could not locate CID or download link on {detail_url}")

    async def _poll_slow_download(self, slow_url: str, max_polls: int = 5) -> str | None:
        """Poll Anna's Archive slow download countdown page politely."""
        logger.info("[%s] Polling slow download page at %s", self.name, slow_url)
        for poll_idx in range(1, max_polls + 1):
            await self._enforce_polite_delay(slow_url)
            resp = await self._client.get(slow_url)
            if resp.status_code != 200:
                break

            html = resp.text
            # Look for final download button or link
            final_match = re.search(
                r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(?:Download now|Click here to download)</a>',
                html,
                re.IGNORECASE,
            )
            if final_match:
                url = final_match.group(1)
                if not url.startswith("http"):
                    url = f"{self.current_mirror}/{url.lstrip('/')}"
                logger.info("[%s] Slow download ready: %s", self.name, url)
                return url

            # If page indicates wait time, sleep politely
            wait_match = re.search(r"(\d+)\s*seconds", html, re.IGNORECASE)
            wait_seconds = int(wait_match.group(1)) if wait_match else 5
            sleep_duration = min(wait_seconds, 10)
            logger.info("[%s] Slow queue waiting %ds (poll %d/%d)…", self.name, sleep_duration, poll_idx, max_polls)
            await asyncio.sleep(sleep_duration)

        return None

    async def download(
        self,
        handle: DownloadHandle,
        dest: Path,
        max_bytes: int,
        timeout: float,
    ) -> int:
        """Stream download file over HTTP to destination with size limits."""
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
