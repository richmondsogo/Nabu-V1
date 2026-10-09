"""Acquisition queue manager and pipeline for Nabu-V1.

Orchestrates shadow library searches, detail resolution, IPFS/HTTP retrieval,
magic-byte validation, SQLite catalog ingestion, and Telegram delivery queue dispatch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Awaitable, Callable
import uuid

from database import Database
from ipfs import KuboClient
from models import AcquisitionJob, SearchHit
from queue_manager import QueueManager
from sources.base import BaseSource
from sources.resolver import SourceResolver
from utils import is_plausible_book_file, sanitize_filename

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], Awaitable[None]]


class AcquisitionManager:
    """Manages the background acquisition worker pool with user gating and MD5 deduplication."""

    def __init__(
        self,
        db: Database,
        kubo_client: KuboClient,
        resolver: SourceResolver,
        delivery_queue: QueueManager | None = None,
        max_workers: int = 2,
        acquire_timeout: float = 300.0,
        max_file_size: int = 52428800,  # 50 MB
        temp_dir: Path | str = "tmp",
    ) -> None:
        self.db = db
        self.kubo_client = kubo_client
        self.resolver = resolver
        self.delivery_queue = delivery_queue
        self.max_workers = max_workers
        self.acquire_timeout = acquire_timeout
        self.max_file_size = max_file_size
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self._queue: asyncio.Queue[AcquisitionJob] = asyncio.Queue()
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._running = False

        # One-active-acquisition-per-user gate
        self._user_active: dict[int, str] = {}  # user_id -> job_id
        self._user_waiting: dict[int, list[AcquisitionJob]] = {}  # user_id -> [queued_jobs]

        # Global deduplication by MD5 (subscribers fan-out)
        self._active_by_md5: dict[str, list[AcquisitionJob]] = {}  # md5 -> [jobs]

        # Callbacks registry: job_id -> StatusCallback
        self._status_callbacks: dict[str, StatusCallback] = {}

        # Tracking stats
        self.completed_count = 0
        self.failed_count = 0

    async def start(self) -> None:
        """Spawn background acquisition workers."""
        if self._running:
            return
        self._running = True
        for i in range(self.max_workers):
            task = asyncio.create_task(self._worker_loop(i + 1), name=f"acq-worker-{i+1}")
            self._worker_tasks.append(task)
        logger.info("AcquisitionManager started with %d workers", self.max_workers)

    async def stop(self) -> None:
        """Stop background acquisition workers cleanly."""
        if not self._running:
            return
        self._running = False
        for task in self._worker_tasks:
            task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        logger.info("AcquisitionManager stopped")

    async def enqueue(
        self,
        query: str,
        user_id: int,
        chat_id: int,
        md5: str | None = None,
        hit: SearchHit | None = None,
        status_callback: StatusCallback | None = None,
    ) -> AcquisitionJob:
        """Enqueue an acquisition request.
        
        Enforces one-active-per-user and global MD5 deduplication.
        """
        job_id = f"acq-{uuid.uuid4().hex[:12]}"
        now = asyncio.get_running_loop().time()

        job = AcquisitionJob(
            job_id=job_id,
            query=query,
            user_id=user_id,
            chat_id=chat_id,
            enqueued_at=now,
            md5=md5 or (hit.md5 if hit else None),
            hit=hit,
            source=hit.source if hit else None,
            status="queued",
        )

        if status_callback:
            self._status_callbacks[job_id] = status_callback

        # Record acquisition entry in SQLite
        try:
            acq_id = await self.db.create_acquisition(
                source=job.source or "resolver",
                requested_by=user_id,
                md5=job.md5,
                source_id=hit.source_id if hit else None,
                status="queued",
            )
            job.acq_id = acq_id
        except Exception as exc:
            logger.warning("Could not persist acquisition entry in DB: %s", exc)

        # 1. Global deduplication by MD5
        if job.md5 and job.md5 in self._active_by_md5:
            logger.info("Job %s deduplicated against active MD5 %s", job_id, job.md5)
            self._active_by_md5[job.md5].append(job)
            if status_callback:
                await status_callback("⏳ An acquisition for this book is already underway. Subscribing…")
            return job

        if job.md5:
            self._active_by_md5[job.md5] = [job]

        # 2. One-active-acquisition-per-user gate
        if user_id in self._user_active:
            logger.info("User %d already has active acquisition %s. Queuing %s.", user_id, self._user_active[user_id], job_id)
            if user_id not in self._user_waiting:
                self._user_waiting[user_id] = []
            self._user_waiting[user_id].append(job)
            if status_callback:
                await status_callback("⏳ Added to acquisition queue. Waiting for your previous request to finish…")
            return job

        # User is free: mark active and push to worker queue
        self._user_active[user_id] = job_id
        await self._queue.put(job)
        return job

    async def _notify(self, job_id: str, message: str) -> None:
        """Send progress notification if callback registered."""
        cb = self._status_callbacks.get(job_id)
        if cb:
            try:
                await cb(message)
            except Exception as exc:
                logger.debug("Status callback for %s failed: %s", job_id, exc)

    async def _worker_loop(self, worker_id: int) -> None:
        """Worker task processing acquisition jobs."""
        logger.debug("[Worker %d] Started", worker_id)
        while self._running:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                break

            try:
                await self._process_job(job, worker_id)
                self.completed_count += 1
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.failed_count += 1
                logger.error("[Worker %d] Failed acquisition %s: %s", worker_id, job.job_id, exc, exc_info=True)
                job.status = "failed"
                job.error = str(exc)
                if job.acq_id:
                    await self.db.update_acquisition_status(job.acq_id, status="failed", error=str(exc), finished=True)
                await self._notify(job.job_id, f"⚠️ Acquisition failed: {exc}")
            finally:
                self._cleanup_job_state(job)
                self._queue.task_done()

    def _cleanup_job_state(self, job: AcquisitionJob) -> None:
        """Release user and MD5 locks and advance user queue."""
        # Clean MD5 mapping
        if job.md5 and job.md5 in self._active_by_md5:
            self._active_by_md5.pop(job.md5, None)

        # Release user active lock
        if self._user_active.get(job.user_id) == job.job_id:
            self._user_active.pop(job.user_id, None)

        # Remove status callback
        self._status_callbacks.pop(job.job_id, None)

        # Advance user waiting queue if any
        waiting_list = self._user_waiting.get(job.user_id)
        if waiting_list:
            next_job = waiting_list.pop(0)
            if not waiting_list:
                self._user_waiting.pop(job.user_id, None)
            self._user_active[job.user_id] = next_job.job_id
            self._queue.put_nowait(next_job)

    async def _process_job(self, job: AcquisitionJob, worker_id: int) -> None:
        """Execute complete acquisition pipeline with hard ACQUIRE_TIMEOUT."""
        temp_path = self.temp_dir / f"{job.job_id}.part"

        async with asyncio.timeout(self.acquire_timeout):
            # Step 1: Search if no pre-selected hit provided
            hit = job.hit
            if not hit:
                job.status = "searching"
                await self._notify(job.job_id, f"🔎 Searching shadow libraries for \"{job.query}\"…")
                if job.acq_id:
                    await self.db.update_acquisition_status(job.acq_id, status="searching")

                hits = await self.resolver.search(job.query, limit=1)
                if not hits:
                    raise RuntimeError(f"Could not find any candidates for '{job.query}'")
                hit = hits[0]
                job.hit = hit
                job.md5 = hit.md5

            # Step 2: Resolve download handle
            job.status = "resolving"
            await self._notify(job.job_id, f"🔍 Resolving download for \"{hit.title}\"…")
            if job.acq_id:
                await self.db.update_acquisition_status(job.acq_id, status="resolving")

            source, handle = await self.resolver.resolve(hit)

            # Step 3: Download / Retrieve to temporary file
            job.status = "downloading"
            if job.acq_id:
                await self.db.update_acquisition_status(job.acq_id, status="downloading")

            try:
                if handle.kind == "cid" and handle.cid:
                    await self._notify(job.job_id, "📥 Retrieving book via IPFS…")
                    # Fetch from local Kubo (or fallback gateway)
                    await self.kubo_client.cat_file(
                        cid=handle.cid,
                        dest=temp_path,
                        max_bytes=self.max_file_size,
                        timeout=90.0,
                    )
                    # Pin CID in local Kubo node
                    try:
                        await self.kubo_client.pin_cid(handle.cid)
                    except Exception as pin_exc:
                        logger.warning("Failed to pin CID %s in Kubo: %s", handle.cid, pin_exc)
                    final_cid = handle.cid

                elif handle.kind == "url" and handle.url:
                    await self._notify(job.job_id, f"📥 Downloading from {source.name}…")
                    await source.download(
                        handle=handle,
                        dest=temp_path,
                        max_bytes=self.max_file_size,
                        timeout=self.acquire_timeout,
                    )

                    # Ingest into local Kubo node
                    job.status = "pinning"
                    await self._notify(job.job_id, "📌 Ingesting and pinning to local IPFS node…")
                    if job.acq_id:
                        await self.db.update_acquisition_status(job.acq_id, status="pinning")

                    final_cid = await self.kubo_client.add_file(temp_path, pin=True)

                else:
                    raise RuntimeError("DownloadHandle carried neither valid CID nor URL")

                # Step 4: Validate content by magic bytes
                is_plausible, detected_fmt = is_plausible_book_file(temp_path)
                if not is_plausible:
                    raise ValueError(f"Downloaded content failed validation (detected type: {detected_fmt})")

                # Step 5: Catalog ingestion / update in SQLite
                job.status = "imported"
                await self._notify(job.job_id, "✅ Added to the local library.")
                now_iso = datetime.now(timezone.utc).isoformat()
                file_size = temp_path.stat().st_size

                # Check if existing row matches MD5
                book_id: int
                existing = await self.db.get_book_by_md5(hit.md5) if hit.md5 else None
                if existing:
                    await self.db.update_book_cid(existing.id, final_cid, pinned=True)
                    book_id = existing.id
                else:
                    new_book = await self.db.insert_book(
                        title=hit.title,
                        author=hit.author,
                        cid=final_cid,
                        md5=hit.md5 or handle.md5,
                        filename=f"{sanitize_filename(hit.title)}.{detected_fmt}",
                        file_size=file_size,
                        file_type=detected_fmt,
                        source=source.name,
                        source_id=hit.source_id,
                        acquired_at=now_iso,
                        pinned=True,
                    )
                    book_id = new_book.id

                job.book_id = book_id
                if job.acq_id:
                    await self.db.update_acquisition_status(job.acq_id, status="imported", finished=True)

                # Step 6: Dispatch to delivery queue
                if self.delivery_queue:
                    await self._notify(job.job_id, "📦 Delivering to chat…")
                    self.delivery_queue.enqueue(
                        user_id=job.user_id,
                        chat_id=job.chat_id,
                        book_id=book_id,
                        title=hit.title,
                    )

                    # Also notify and enqueue delivery for all fan-out subscribers
                    subscribers = self._active_by_md5.get(job.md5 or "", [])
                    for sub in subscribers:
                        if sub.job_id != job.job_id:
                            await self._notify(sub.job_id, "📦 Delivering to chat…")
                            self.delivery_queue.enqueue(
                                user_id=sub.user_id,
                                chat_id=sub.chat_id,
                                book_id=book_id,
                                title=hit.title,
                            )

            finally:
                # Guaranteed cleanup: delete temporary part file on all code paths
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
