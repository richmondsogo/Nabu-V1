"""Delivery queue manager with 3 concurrent workers for Nabu-V1.

Implements:
- asyncio.Queue[DownloadJob] with a fixed pool of exactly 3 concurrent workers
- Deterministic FIFO position calculation (not relying on qsize())
- Duplicate prevention per (user_id, book_id)
- Total failure isolation: one failing download immediately releases capacity and never halts the queue
- Graceful shutdown and queue draining
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import time
import uuid

from models import DownloadJob

logger = logging.getLogger(__name__)

# Type for the async callback that processes a single download job
JobHandler = Callable[[DownloadJob], Awaitable[None]]


class QueueManager:
    """Manages the in-memory download queue and exactly 3 persistent worker tasks."""

    def __init__(
        self,
        handler: JobHandler | None = None,
        max_workers: int = 3,
    ) -> None:
        self.max_workers = max_workers
        self.handler = handler
        self._queue: asyncio.Queue[DownloadJob] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._running = False

        # State tracking
        self._pending_order: list[str] = []  # Ordered job_ids in FIFO order
        self._jobs: dict[str, DownloadJob] = {}  # job_id -> DownloadJob
        self._active_jobs: dict[str, DownloadJob] = {}  # job_id -> DownloadJob
        self._user_book_locks: set[tuple[int, int]] = set()  # (user_id, book_id)

        # Statistics
        self.completed_count = 0
        self.failed_count = 0

    def start(self) -> None:
        """Spawn the fixed pool of worker tasks."""
        if self._running:
            return
        self._running = True
        for i in range(self.max_workers):
            task = asyncio.create_task(self._worker_loop(i), name=f"delivery-worker-{i}")
            self._workers.append(task)
        logger.info("Delivery QueueManager started with %d workers", self.max_workers)

    async def stop(self) -> None:
        """Cancel worker tasks and drain pending jobs."""
        if not self._running:
            return
        self._running = False
        logger.info("Stopping Delivery QueueManager...")

        for task in self._workers:
            task.cancel()

        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

        # Clean tracking state
        self._pending_order.clear()
        self._jobs.clear()
        self._active_jobs.clear()
        self._user_book_locks.clear()
        logger.info("Delivery QueueManager stopped cleanly")

    def is_active_or_pending(self, user_id: int, book_id: int) -> bool:
        """Check if this user already has an active or pending download for this book."""
        return (user_id, book_id) in self._user_book_locks

    def calculate_position(self, job_id: str) -> int:
        """Calculate deterministic FIFO queue position for a job.

        As specified in Defect D1:
            ahead = pending.index(job_id)  # 0-based
            free = max(0, max_workers - active_workers)
            position = max(1, ahead + 1 - free)
        """
        if job_id in self._active_jobs:
            return 1
        if job_id not in self._pending_order:
            return 1

        ahead = self._pending_order.index(job_id)
        active_count = len(self._active_jobs)
        free = max(0, self.max_workers - active_count)
        return max(1, ahead + 1 - free)

    def enqueue(
        self,
        user_id: int,
        chat_id: int,
        book_id: int,
        title: str = "",
    ) -> tuple[bool, DownloadJob | None, int, str]:
        """Attempt to enqueue a download job for a book.

        Returns:
            (success, job, position, message)
        """
        if not self._running:
            return False, None, 0, "Queue is currently paused or stopped."

        key = (user_id, book_id)
        if key in self._user_book_locks:
            return False, None, 0, "You already have a pending or active download for this book."

        job_id = f"job-{uuid.uuid4().hex[:10]}"
        job = DownloadJob(
            job_id=job_id,
            book_id=book_id,
            user_id=user_id,
            chat_id=chat_id,
            enqueued_at=time.time(),
            title=title,
        )

        self._user_book_locks.add(key)
        self._jobs[job_id] = job
        self._pending_order.append(job_id)

        position = self.calculate_position(job_id)
        self._queue.put_nowait(job)

        logger.info(
            "Enqueued job %s (book_id=%d, user=%d, pos=%d)",
            job_id,
            book_id,
            user_id,
            position,
        )
        return True, job, position, "Added to download queue."

    def stats(self) -> dict[str, int]:
        """Return operational queue metrics."""
        return {
            "queued": len(self._pending_order),
            "active": len(self._active_jobs),
            "completed": self.completed_count,
            "failed": self.failed_count,
        }

    def get_user_pending_jobs(self, user_id: int) -> list[DownloadJob]:
        """Return all pending and active jobs for a specific user."""
        results: list[DownloadJob] = []
        for job in self._active_jobs.values():
            if job.user_id == user_id:
                results.append(job)
        for job_id in self._pending_order:
            job = self._jobs.get(job_id)
            if job and job.user_id == user_id:
                results.append(job)
        return results

    async def _worker_loop(self, worker_id: int) -> None:
        """Worker loop consuming from the shared queue with absolute failure isolation."""
        logger.debug("Worker %d listening for download jobs", worker_id)
        while self._running:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                break

            job_id = job.job_id
            # Move from pending list to active jobs
            if job_id in self._pending_order:
                self._pending_order.remove(job_id)
            self._active_jobs[job_id] = job

            start_time = time.monotonic()
            logger.info("Worker %d starting job %s (book_id=%d)", worker_id, job_id, job.book_id)

            try:
                if self.handler:
                    await self.handler(job)
                self.completed_count += 1
                logger.info(
                    "Worker %d finished job %s in %.2fs",
                    worker_id,
                    job_id,
                    time.monotonic() - start_time,
                )
            except asyncio.CancelledError:
                logger.info("Worker %d job %s cancelled during execution", worker_id, job_id)
                self.failed_count += 1
                break
            except Exception as exc:
                self.failed_count += 1
                logger.exception("Worker %d failed processing job %s: %s", worker_id, job_id, exc)
            finally:
                # Invariant: Clean up tracking and always release worker capacity
                self._active_jobs.pop(job_id, None)
                self._jobs.pop(job_id, None)
                self._user_book_locks.discard((job.user_id, job.book_id))
                self._queue.task_done()
