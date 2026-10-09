"""Mirror health, prober, cooldown tracking, and selection for Nabu-V1."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Sequence
import httpx

from database import Database
from models import Mirror

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


class MirrorManager:
    """Manages upstream mirror selection, health probing, latency, and exponential cooldowns."""

    def __init__(self, db: Database, connect_timeout: float = 8.0) -> None:
        self.db = db
        self.connect_timeout = connect_timeout

    async def get_active_mirrors(self, source: str = "libgen") -> list[Mirror]:
        """Return enabled mirrors not currently in cooldown, sorted by health and latency."""
        return await self.db.active_mirrors(source)

    async def get_best_mirror(self, source: str = "libgen") -> Mirror | None:
        """Return healthiest active mirror with lowest latency, or None if all are down."""
        mirrors = await self.get_active_mirrors(source)
        return mirrors[0] if mirrors else None

    async def record_result(
        self,
        url: str,
        ok: bool,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        """Record mirror result, resetting fail count or applying exponential backoff."""
        await self.db.record_mirror_result(url, ok, latency_ms=latency_ms, error=error)
        if ok:
            logger.debug("[MirrorManager] Mirror %s OK (%sms)", url, latency_ms)
        else:
            logger.warning("[MirrorManager] Mirror %s failed: %s", url, error)

    async def probe_mirror(
        self,
        mirror: Mirror,
        client: httpx.AsyncClient,
    ) -> bool:
        """Probe a single mirror. Measures latency and updates database record."""
        t0 = time.time()
        try:
            # Check mirror root or search endpoint with connect_timeout
            resp = await client.get(
                mirror.url,
                timeout=httpx.Timeout(self.connect_timeout, connect=self.connect_timeout),
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
            latency = int((time.time() - t0) * 1000)
            if resp.status_code < 500:
                await self.record_result(mirror.url, True, latency_ms=latency)
                return True
            else:
                await self.record_result(mirror.url, False, error=f"HTTP {resp.status_code}")
                return False
        except Exception as exc:
            await self.record_result(mirror.url, False, error=repr(exc))
            return False

    async def probe_all(self, source: str = "libgen", client: httpx.AsyncClient | None = None) -> None:
        """Probe all configured mirrors in parallel. Fail-soft, never raises."""
        try:
            mirrors = await self.db.get_all_mirrors(source)
            if not mirrors:
                return

            close_client = False
            if client is None:
                client = httpx.AsyncClient(timeout=self.connect_timeout)
                close_client = True

            try:
                tasks = [self.probe_mirror(m, client) for m in mirrors]
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                if close_client:
                    await client.aclose()
        except Exception as e:
            logger.warning("[MirrorManager] Error during mirror probe: %s", e)

    def startup_probe(self, client: httpx.AsyncClient | None = None) -> asyncio.Task:
        """Launch background health check on startup. Never blocks boot."""
        return asyncio.create_task(self.probe_all(client=client))
