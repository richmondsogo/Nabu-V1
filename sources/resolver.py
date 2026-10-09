"""Multi-source resolver for orchestrating sequential search, deduplication, and resolution."""

from __future__ import annotations

import logging
import re
from typing import Mapping

from models import DownloadHandle, SearchHit
from sources.base import BaseSource

logger = logging.getLogger(__name__)


def _normalize_key(title: str, author: str | None = None) -> str:
    """Normalize title and author to a lowercase alphanumeric key for deduplication."""
    clean_title = re.sub(r"[^\w\s]", "", title.lower()).strip()
    clean_author = re.sub(r"[^\w\s]", "", (author or "").lower()).strip()
    return f"{clean_title}::{clean_author}"


class SourceResolver:
    """Orchestrates shadow library adapters in priority order with deduplication."""

    def __init__(
        self,
        sources: Mapping[str, BaseSource],
        priority: tuple[str, ...] = ("annas", "libgen"),
    ) -> None:
        self.sources = dict(sources)
        self.priority = priority

    async def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        """Query sources sequentially in priority order and deduplicate results.

        Stops at the first healthy source that returns results, or aggregates if needed.
        Deduplicates by MD5 and normalized (title, author). Prefers hits with CIDs.
        """
        combined_hits: list[SearchHit] = []
        seen_md5s: set[str] = set()
        seen_keys: set[str] = set()

        for source_name in self.priority:
            source = self.sources.get(source_name)
            if not source:
                continue

            if not await source.available():
                logger.info("Source %s is currently in cooldown, skipping", source_name)
                continue

            try:
                hits = await source.search(query, limit=limit)
                for h in hits:
                    # Deduplicate by MD5
                    if h.md5:
                        if h.md5 in seen_md5s:
                            continue
                        seen_md5s.add(h.md5)

                    # Deduplicate by normalized title/author
                    norm_key = _normalize_key(h.title, h.author)
                    if norm_key in seen_keys:
                        continue
                    seen_keys.add(norm_key)

                    combined_hits.append(h)

                # Stop if primary source returned usable results
                if combined_hits:
                    logger.info("Resolver found %d results from %s", len(combined_hits), source_name)
                    break

            except Exception as exc:
                logger.warning("Source %s failed during search: %s. Continuing to next source.", source_name, exc)
                continue

        # Sort: prefer hits with a CID over those without
        combined_hits.sort(key=lambda h: 0 if h.cid else 1)
        return combined_hits[:limit]

    async def resolve(self, hit: SearchHit) -> tuple[BaseSource, DownloadHandle]:
        """Resolve a SearchHit via its originating source adapter."""
        source = self.sources.get(hit.source)
        if not source:
            raise ValueError(f"Originating source {hit.source!r} not configured in resolver")

        # If SearchHit already carries a verified CID, return CID handle immediately
        if hit.cid:
            logger.info("SearchHit carries direct CID %s, bypassing HTTP resolution", hit.cid)
            return source, DownloadHandle(
                kind="cid",
                cid=hit.cid,
                source=hit.source,
                md5=hit.md5,
                filesize=hit.filesize,
                extension=hit.extension,
            )

        # Delegate resolution to the source adapter
        handle = await source.resolve(hit)
        return source, handle

    async def close(self) -> None:
        """Close all source adapters."""
        for src in self.sources.values():
            await src.close()
