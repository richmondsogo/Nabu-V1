"""Search orchestration for Nabu-V1 Link Resolver.

Flow:
search_books(query) -> search_cache (verbatim repeat only) -> single_flight(_scrape) -> upsert -> serve

The local catalogue is never used to answer a new query. It is only a degraded
fallback (together with a stale cache entry) when every upstream mirror fails.
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
from models import Book, Mirror, SearchHit, SearchOutcome
from sources.libgen import is_parser, li_parser
from sources.mirror_manager import MirrorManager

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


class AllMirrorsFailed(Exception):
    """Raised when all available mirrors fail or time out."""


class RateLimitedError(Exception):
    """Raised when user exceeds upstream request rate limit."""


import unicodedata

_STOP_WORDS = frozenset({
    "a", "an", "the", "in", "on", "of", "at", "by", "for", "with",
    "about", "against", "between", "into", "through", "during", "before",
    "after", "above", "below", "to", "from", "up", "down", "and", "or",
    "but", "is", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "how", "what", "why", "when", "where",
})


def normalize_query(query: str) -> str:
    """Normalize query string: Unicode NFKC, strip search-breaking punctuation, collapse spaces."""
    if not query:
        return ""
    text = unicodedata.normalize("NFKC", query)
    cleaned = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def relax_query(query: str) -> str:
    """Produce a relaxed search query by stripping stop words or reducing tokens."""
    norm = normalize_query(query)
    tokens = norm.split()
    if len(tokens) <= 2:
        return norm
    significant = [t for t in tokens if t not in _STOP_WORDS]
    if len(significant) >= 2:
        return " ".join(significant)
    return " ".join(tokens[:2])


def _rank_books(books: list[Book], query: str) -> list[Book]:
    """Rank books by relevance:
    1. Exact title match
    2. All query tokens in title
    3. Query tokens in author
    4. Any query token in title
    5. Completeness of metadata
    """
    q_tokens = [t.lower() for t in re.findall(r"\w+", query, re.UNICODE)]
    q_lower = query.lower()

    def score(book: Book) -> tuple[int, int, int, int]:
        title_lower = (book.title or "").lower()
        author_lower = (book.author or "").lower()

        if title_lower == q_lower:
            tier = 0
        elif q_tokens and all(t in title_lower for t in q_tokens):
            tier = 1
        elif q_tokens and all(t in author_lower for t in q_tokens):
            tier = 2
        elif q_tokens and any(t in title_lower for t in q_tokens):
            tier = 3
        else:
            tier = 4

        token_matches = -sum(1 for t in q_tokens if t in title_lower)

        completeness = 0
        if book.author:
            completeness += 1
        if book.file_size and book.file_size > 0:
            completeness += 1
        if book.file_type:
            completeness += 1
        if book.md5:
            completeness += 1

        return (tier, token_matches, -completeness, book.id)

    return sorted(books, key=score)


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

    async def _scrape(
        self,
        query: str,
        max_results: int | None = None,
        max_pages: int | None = None,
    ) -> tuple[list[SearchHit], Mirror, int, int]:
        """Scrape active mirrors in health order across pages with polite delay and global concurrency bound.

        Returns:
            (hits, mirror, average_latency_ms, pages_fetched)
        """
        mirrors = await self.mirror_manager.get_active_mirrors("libgen")
        if not mirrors:
            raise AllMirrorsFailed("No active mirrors available")

        target_max_results = max_results or self.config.upstream_max_results
        target_max_pages = max_pages or self.config.upstream_max_pages

        last_exc: Exception | None = None
        for mirror in mirrors:
            parser = is_parser if mirror.fork == "is" else li_parser
            all_hits: list[SearchHit] = []
            seen_keys: set[str] = set()
            total_latency = 0
            pages_fetched = 0
            mirror_failed = False

            for page in range(1, target_max_pages + 1):
                url = parser.search_url(mirror, query, page=page)
                t0 = time.time()
                try:
                    async with self.host_rate_limiter.acquire(mirror.host):
                        async with self.global_semaphore:
                            resp = await self._client.get(
                                url,
                                timeout=httpx.Timeout(
                                    self.config.connect_timeout,
                                    connect=min(self.config.connect_timeout, 4.0),
                                ),
                                headers={"User-Agent": USER_AGENT},
                                follow_redirects=True,
                            )
                    latency = int((time.time() - t0) * 1000)
                    total_latency += latency
                    pages_fetched += 1
                    if resp.status_code >= 400:
                        raise RuntimeError(f"HTTP {resp.status_code}")

                    hits = parser.parse(resp.text, mirror)
                except Exception as e:
                    last_exc = e
                    err_desc = f"{type(e).__name__}('{e}')" if str(e) else repr(e)
                    if all_hits:
                        # Page 1 (or prior) already yielded hits: do not discard them
                        logger.warning(
                            "[search] Mirror %s page %d failed (%s); returning %d partial hits from previous pages",
                            mirror.url, page, err_desc, len(all_hits)
                        )
                        break
                    else:
                        logger.warning("[search] Mirror %s page %d failed: %s", mirror.url, page, err_desc)
                        await self.mirror_manager.record_result(mirror.url, False, error=err_desc)
                        mirror_failed = True
                        break

                if not hits:
                    break

                new_count = 0
                for hit in hits:
                    key = hit.md5 or (hit.title.strip().lower(), (hit.author or "").strip().lower())
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_hits.append(hit)
                        new_count += 1

                if len(all_hits) >= target_max_results:
                    all_hits = all_hits[:target_max_results]
                    break

                if new_count == 0 or len(hits) < 25:
                    break

            if mirror_failed:
                continue

            if all_hits or pages_fetched > 0:
                avg_latency = int(total_latency / max(1, pages_fetched))
                await self.mirror_manager.record_result(mirror.url, True, latency_ms=avg_latency)
                return all_hits, mirror, avg_latency, pages_fetched

        raise AllMirrorsFailed("All upstream mirrors failed") from last_exc

    async def _execute_scrape_and_persist(
        self,
        query: str,
    ) -> tuple[list[Book], list[SearchHit], Mirror | None, int, int]:
        """Runs upstream scrape, upserts books into DB, and writes to search_cache."""
        upstream_hits, mirror, latency_ms, pages_fetched = await self._scrape(query)

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

        final_ids = list(dict.fromkeys(upstream_ids))[:self.config.upstream_max_results]
        books = await self.db.get_books_by_ids(final_ids)
        ranked_books = _rank_books(books, query)

        qn = normalize_query(query)
        cache_ttl = self.config.search_cache_ttl if ranked_books else self.config.empty_result_cache_ttl
        await self.db.set_search_cache(
            qn,
            [b.id for b in ranked_books],
            cache_ttl,
            is_complete=True,
            pages_fetched=pages_fetched,
            total_upstream=len(upstream_hits),
        )

        return ranked_books, upstream_hits, mirror, latency_ms, pages_fetched

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
            return SearchOutcome(hits=[], source="local", degraded=False, total_count=0, query_normalized="")

        now = time.time()
        cached = await self.db.get_search_cache(qn)

        # 1. Fresh cache hit (only if complete and not expired)
        if not force_upstream and cached is not None:
            book_ids = cached[0]
            expires_at = cached[1]
            is_complete = cached[2] if len(cached) > 2 else True
            if expires_at > now and is_complete:
                books = await self.db.get_books_by_ids(book_ids)
                ranked_books = _rank_books(books, query)
                logger.info("[search] Query %r served from cache (%d books)", qn, len(ranked_books))
                return SearchOutcome(
                    hits=ranked_books,
                    source="cache",
                    degraded=False,
                    total_count=len(ranked_books),
                    query_normalized=qn,
                )

        # 2. Not a verbatim repeat: rate limit check for new upstream query
        target_results = self.config.upstream_max_results
        if user_id is not None and self.user_rate_limiter is not None:
            allowed = await self.user_rate_limiter.acquire(user_id)
            if not allowed:
                raise RateLimitedError("Rate limit exceeded")

        flight_key = f"search:{qn}"
        try:
            flight_res = await self.single_flight.do(
                flight_key,
                lambda: self._execute_scrape_and_persist(query),
                timeout=self.config.singleflight_timeout,
            )
            ranked_books, upstream_hits, mirror, latency_ms, pages_fetched = flight_res

            # 3. If strict query produced 0 results, retry once with relaxed query (without charging user token again)
            if not ranked_books:
                relaxed = relax_query(query)
                if relaxed and relaxed != qn:
                    logger.info("[search] Strict query %r returned 0 hits, trying relaxed query %r", qn, relaxed)
                    relaxed_outcome = await self.search_books(
                        relaxed,
                        user_id=None,  # Do not deduct second rate-limit token for automatic relaxation
                        force_upstream=True,
                    )
                    if relaxed_outcome.hits:
                        return SearchOutcome(
                            hits=relaxed_outcome.hits,
                            source=relaxed_outcome.source,
                            degraded=relaxed_outcome.degraded,
                            total_count=relaxed_outcome.total_count,
                            mirror_url=relaxed_outcome.mirror_url or (mirror.url if mirror else None),
                            latency_ms=relaxed_outcome.latency_ms or latency_ms,
                            query_normalized=qn,
                            is_relaxed=True,
                            upstream_reached=True,
                        )

            logger.info("[search] Query %r served upstream (%d books)", qn, len(ranked_books))
            return SearchOutcome(
                hits=ranked_books,
                source="upstream",
                degraded=False,
                total_count=len(ranked_books),
                mirror_url=mirror.url if mirror else None,
                latency_ms=latency_ms,
                query_normalized=qn,
                upstream_reached=True,
            )

        except Exception as exc:
            err_desc = f"{type(exc).__name__}('{exc}')" if str(exc) else repr(exc)
            logger.warning("[search] Upstream scrape failed for %r: %s", qn, err_desc)
            # Degraded fallbacks: stale cache first, then the local catalogue
            if cached is not None:
                stale_ids = cached[0]
                books = await self.db.get_books_by_ids(stale_ids)
                if books:
                    ranked_stale = _rank_books(books, query)
                    logger.info("[search] Serving stale cache for %r (degraded)", qn)
                    return SearchOutcome(
                        hits=ranked_stale,
                        source="cache",
                        degraded=True,
                        total_count=len(ranked_stale),
                        query_normalized=qn,
                    )

            local_hits = await self.db.search_books(qn, limit=target_results)
            if local_hits:
                ranked_local = _rank_books(local_hits, query)
                logger.info("[search] Serving local hits for %r (degraded)", qn)
                return SearchOutcome(
                    hits=ranked_local,
                    source="local",
                    degraded=True,
                    total_count=len(ranked_local),
                    query_normalized=qn,
                )

            raise AllMirrorsFailed("All sources unreachable. Try again later.") from exc
