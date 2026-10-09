"""Search orchestration for Nabu-V1 Link Resolver.

Flow:
search_books(query) -> search_cache -> local FTS -> (thin) -> single_flight(_scrape) -> upsert -> serve
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
import httpx

from config import Config
from concurrency import HostRateLimiter, SingleFlight
from database import Database
from models import Book, SearchHit, SearchOutcome
from sources.libgen import is_parser, li_parser
from sources.mirror_manager import MirrorManager

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


class AllMirrorsFailed(Exception):
    """Raised when all available mirrors fail or time out."""


class RateLimitedError(Exception):
    """Raised when user exceeds upstream request rate limit."""


def normalize_query(query: str) -> str:
    """Normalize query string for deterministic cache lookups."""
    return re.sub(r"\s+", " ", query.strip().lower())


class SearchService:
    """Orchestrates search cache, local FTS, and bounded upstream scraping."""

    def __init__(
        self,
        db: Database,
        config: Config,
        mirror_manager: MirrorManager,
        single_flight: SingleFlight,
        host_rate_limiter: HostRateLimiter,
        global_semaphore: Any,
        user_rate_limiter: Any | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.mirror_manager = mirror_manager
        self.single_flight = single_flight
        self.host_rate_limiter = host_rate_limiter
        self.global_semaphore = global_semaphore
        self.user_rate_limiter = user_rate_limiter
        self._client = http_client or httpx.AsyncClient()

    async def _scrape(self, query: str) -> list[SearchHit]:
        """Scrape active mirrors in health order with host polite delay and global concurrency bound."""
        mirrors = await self.mirror_manager.get_active_mirrors("libgen")
        if not mirrors:
            raise AllMirrorsFailed("No active mirrors available")

        last_exc: Exception | None = None
        for mirror in mirrors:
            parser = is_parser if mirror.fork == "is" else li_parser
            url = parser.search_url(mirror, query)
            try:
                t0 = time.time()
                async with self.host_rate_limiter.acquire(mirror.host):
                    async with self.global_semaphore:
                        resp = await self._client.get(
                            url,
                            timeout=httpx.Timeout(
                                self.config.connect_timeout,
                                connect=self.config.connect_timeout,
                            ),
                            headers={"User-Agent": USER_AGENT},
                            follow_redirects=True,
                        )
                latency = int((time.time() - t0) * 1000)
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}")

                hits = parser.parse(resp.text, mirror)
                await self.mirror_manager.record_result(mirror.url, True, latency_ms=latency)
                return hits
            except Exception as e:
                last_exc = e
                await self.mirror_manager.record_result(mirror.url, False, error=repr(e))
                continue

        raise AllMirrorsFailed("All upstream mirrors failed") from last_exc

    async def search_books(
        self,
        query: str,
        *,
        user_id: int | None = None,
        force_upstream: bool = False,
    ) -> SearchOutcome:
        """Search books adhering to the cache-first, FTS-fallback, SingleFlight architecture."""
        qn = normalize_query(query)
        if not qn:
            return SearchOutcome(hits=[], source="local", degraded=False)

        now = time.time()
        cached = await self.db.get_search_cache(qn)

        # 1. Fresh cache hit
        if not force_upstream and cached is not None:
            book_ids, expires_at = cached
            if expires_at > now:
                books = await self.db.get_books_by_ids(book_ids)
                logger.info("[search] Query %r served from cache (%d books)", qn, len(books))
                return SearchOutcome(hits=books, source="cache", degraded=False)

        # 2. Local FTS catalog check
        local_hits = await self.db.search_books(qn, limit=self.config.result_limit)
        if not force_upstream and len(local_hits) >= self.config.local_result_threshold:
            logger.info("[search] Query %r served from local FTS (%d books)", qn, len(local_hits))
            return SearchOutcome(hits=local_hits, source="local", degraded=False)

        # 3. Upstream SingleFlight scrape
        if user_id is not None and self.user_rate_limiter is not None:
            allowed = await self.user_rate_limiter.acquire(user_id)
            if not allowed:
                raise RateLimitedError("Rate limit exceeded")

        flight_key = f"search:{qn}"
        try:
            upstream_hits: list[SearchHit] = await self.single_flight.do(
                flight_key,
                lambda: self._scrape(query),
                timeout=self.config.singleflight_timeout,
            )

            # Upsert upstream hits into database
            upstream_ids: list[int] = []
            for hit in upstream_hits:
                bid = await self.db.upsert_book(
                    title=hit.title,
                    author=hit.author,
                    md5=hit.md5,
                    filename=None,
                    file_size=hit.filesize,
                    file_type=hit.extension,
                    source=hit.source,
                    source_id=hit.source_id,
                )
                upstream_ids.append(bid)

            # Merge upstream IDs ahead of local IDs, deduplicate, cap at result_limit
            seen: set[int] = set()
            merged_ids: list[int] = []
            for bid in upstream_ids:
                if bid not in seen:
                    seen.add(bid)
                    merged_ids.append(bid)
            for b in local_hits:
                if b.id not in seen:
                    seen.add(b.id)
                    merged_ids.append(b.id)

            final_ids = merged_ids[: self.config.result_limit]
            await self.db.set_search_cache(qn, final_ids, self.config.search_cache_ttl)
            books = await self.db.get_books_by_ids(final_ids)
            logger.info("[search] Query %r served upstream (%d books)", qn, len(books))
            return SearchOutcome(hits=books, source="upstream", degraded=False)

        except Exception as exc:
            logger.warning("[search] Upstream scrape failed for %r: %s", qn, exc)
            # Degraded fallbacks
            if cached is not None:
                stale_ids, _ = cached
                books = await self.db.get_books_by_ids(stale_ids)
                if books:
                    logger.info("[search] Serving stale cache for %r (degraded)", qn)
                    return SearchOutcome(hits=books, source="cache", degraded=True)

            if local_hits:
                logger.info("[search] Serving local hits for %r (degraded)", qn)
                return SearchOutcome(hits=local_hits, source="local", degraded=True)

            raise AllMirrorsFailed("All sources unreachable. Try again later.") from exc
