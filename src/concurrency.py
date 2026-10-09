"""Concurrency primitives and rate limiting for Nabu-V1 Link Resolver.

Includes:
- SingleFlight: Deduplicates identical in-flight coroutines.
- HostRateLimiter: Per-host locks enforcing a polite delay between requests.
- UserRateLimiter: In-memory token bucket per user.
- GlobalSemaphore: Bounded concurrency across all outbound upstream requests.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time
from typing import Any, Callable, Coroutine


class SingleFlight:
    """Deduplicates concurrent in-flight requests for the same key.

    If key is already in flight, subsequent callers await the same shared Future.
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()

    async def _run(
        self,
        key: str,
        fut: asyncio.Future[Any],
        coro_fn: Callable[[], Coroutine[Any, Any, Any]],
    ) -> None:
        try:
            res = await coro_fn()
            if not fut.done():
                fut.set_result(res)
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
        finally:
            # Guarantees key is popped from _futures in all cases (completion, exception, or cancellation).
            # This prevents dead futures from leaking and trapping future callers.
            async with self._lock:
                self._futures.pop(key, None)

    async def do(
        self,
        key: str,
        coro_fn: Callable[[], Coroutine[Any, Any, Any]],
        timeout: float | None = 15.0,
    ) -> Any:
        async with self._lock:
            if key in self._futures:
                fut = self._futures[key]
            else:
                fut = asyncio.get_running_loop().create_future()
                self._futures[key] = fut
                asyncio.create_task(self._run(key, fut, coro_fn))

        # Shield semantics:
        # asyncio.shield protects the shared background execution from cancellation
        # if this individual caller cancels or times out. Other callers awaiting this
        # flight (and the underlying in-flight task) continue to completion uninterrupted.
        waiter = asyncio.shield(fut)
        if timeout is not None and timeout > 0:
            return await asyncio.wait_for(waiter, timeout=timeout)
        return await waiter


class HostRateLimiter:
    """Enforces per-host serialization and minimum delay between requests."""

    def __init__(self, polite_delay_ms: int = 750) -> None:
        self.polite_delay_sec = polite_delay_ms / 1000.0
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def _get_host_lock(self, host: str) -> asyncio.Lock:
        async with self._lock:
            if host not in self._locks:
                self._locks[host] = asyncio.Lock()
            return self._locks[host]

    @asynccontextmanager
    async def acquire(self, host: str):
        """Acquire per-host lock and enforce polite delay."""
        lock = await self._get_host_lock(host)
        async with lock:
            last = self._last_request.get(host, 0.0)
            elapsed = time.time() - last
            if elapsed < self.polite_delay_sec:
                await asyncio.sleep(self.polite_delay_sec - elapsed)
            try:
                yield
            finally:
                self._last_request[host] = time.time()


class UserRateLimiter:
    """In-memory token bucket rate limiter per Telegram user_id.

    Consumed on the upstream search path only, never on cache hits.
    """

    def __init__(self, max_tokens: float = 5.0, refill_per_sec: float = 0.5) -> None:
        self.max_tokens = max_tokens
        self.refill_per_sec = refill_per_sec
        self._buckets: dict[int, tuple[float, float]] = {}  # user_id -> (tokens, last_time)
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: int) -> bool:
        """Attempt to consume 1 token for user. Returns True if allowed, False if rate limited."""
        now = time.time()
        async with self._lock:
            tokens, last_time = self._buckets.get(user_id, (self.max_tokens, now))
            elapsed = max(0.0, now - last_time)
            tokens = min(self.max_tokens, tokens + elapsed * self.refill_per_sec)
            if tokens >= 1.0:
                self._buckets[user_id] = (tokens - 1.0, now)
                return True
            self._buckets[user_id] = (tokens, now)
            return False


# Known limitation: Acquiring GlobalSemaphore before HostRateLimiter can starve other
# hosts when slots queue on one lock during an extended outage or slow mirror.
# At this bot's scale, this is completely acceptable and ensures an absolute cap on
# concurrent upstream sockets.
def create_global_semaphore(max_upstream: int = 4) -> asyncio.Semaphore:
    return asyncio.Semaphore(max_upstream)
