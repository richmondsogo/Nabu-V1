"""Base adapter protocol and scraping hygiene utilities for shadow libraries."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import logging
from pathlib import Path
import random
import time
from typing import Any
from urllib.parse import urlparse
import httpx

from models import DownloadHandle, SearchHit

logger = logging.getLogger(__name__)

# Pool of realistic browser User-Agents
USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
)


class BaseSource(ABC):
    """Abstract base class that all shadow library adapters must implement."""

    name: str

    def __init__(
        self,
        mirrors: tuple[str, ...],
        polite_delay_ms: int = 750,
        scrape_timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.mirrors = list(mirrors)
        self.polite_delay_ms = polite_delay_ms
        self.scrape_timeout = scrape_timeout
        self._current_mirror_idx = 0
        self.cooldown_until: float = 0.0
        self.last_error: str | None = None

        # Scraping hygiene: dedicated client, cookie jar, and per-host polite delays
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        self._client = httpx.AsyncClient(
            limits=limits,
            transport=transport,
            follow_redirects=True,
            timeout=httpx.Timeout(scrape_timeout),
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )
        self._last_request_time: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}

    @property
    def current_mirror(self) -> str:
        if not self.mirrors:
            return ""
        return self.mirrors[self._current_mirror_idx % len(self.mirrors)].rstrip("/")

    def rotate_mirror(self) -> str:
        """Rotate to the next configured mirror upon failure."""
        if not self.mirrors:
            return ""
        self._current_mirror_idx = (self._current_mirror_idx + 1) % len(self.mirrors)
        new_mirror = self.current_mirror
        logger.warning("[%s] Rotating to mirror: %s", self.name, new_mirror)
        return new_mirror

    async def _enforce_polite_delay(self, url: str) -> None:
        """Enforce POLITE_DELAY_MS between requests to the same host."""
        host = urlparse(url).netloc
        if host not in self._host_locks:
            self._host_locks[host] = asyncio.Lock()

        async with self._host_locks[host]:
            last_time = self._last_request_time.get(host, 0.0)
            elapsed_ms = (time.monotonic() - last_time) * 1000.0
            if elapsed_ms < self.polite_delay_ms:
                wait_sec = (self.polite_delay_ms - elapsed_ms) / 1000.0
                await asyncio.sleep(wait_sec)
            self._last_request_time[host] = time.monotonic()

    async def request_with_retry(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        data: Any = None,
        max_attempts: int = 3,
    ) -> httpx.Response:
        """Execute HTTP request with polite delay, mirror rotation, and backoff on 429/503."""
        for attempt in range(1, max_attempts + 1):
            await self._enforce_polite_delay(url)
            try:
                resp = await self._client.request(method, url, params=params, data=data)

                # Never retry 404
                if resp.status_code == 404:
                    return resp

                # Backoff on 429 Too Many Requests or 503 Service Unavailable
                if resp.status_code in (429, 503):
                    if attempt == max_attempts:
                        self.rotate_mirror()
                        return resp
                    backoff = (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.warning(
                        "[%s] Got HTTP %d on %s, backing off %.2fs (attempt %d/%d)",
                        self.name,
                        resp.status_code,
                        url,
                        backoff,
                        attempt,
                        max_attempts,
                    )
                    await asyncio.sleep(backoff)
                    continue

                return resp

            except (httpx.RequestError, httpx.TimeoutException) as exc:
                self.last_error = str(exc)
                logger.warning(
                    "[%s] Request to %s failed (attempt %d/%d): %s",
                    self.name,
                    url,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt == max_attempts:
                    self.rotate_mirror()
                    raise
                await asyncio.sleep(0.5 * attempt)

        raise RuntimeError(f"[{self.name}] Failed request to {url} after {max_attempts} attempts")

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        """Search shadow library and return up to limit candidate hits."""

    @abstractmethod
    async def resolve(self, hit: SearchHit) -> DownloadHandle:
        """Resolve a SearchHit into a DownloadHandle (either kind='cid' or kind='url')."""

    @abstractmethod
    async def download(
        self,
        handle: DownloadHandle,
        dest: Path,
        max_bytes: int,
        timeout: float,
    ) -> int:
        """Download file from resolved handle to destination path."""

    async def available(self) -> bool:
        """Check if source is currently healthy and not in cooldown."""
        return time.monotonic() > self.cooldown_until

    def set_cooldown(self, seconds: float = 300.0) -> None:
        """Put source into temporary cooldown."""
        self.cooldown_until = time.monotonic() + seconds
        logger.warning("[%s] Put into cooldown for %.1fs", self.name, seconds)

    async def close(self) -> None:
        """Close source's HTTP client."""
        await self._client.aclose()
