"""Comprehensive tests for queue_manager.py validating concurrency, position, dedupe, and failure isolation."""

import asyncio
import pytest

from models import DownloadJob
from queue_manager import QueueManager


@pytest.mark.asyncio
async def test_three_worker_concurrency_limit():
    max_active = 0
    current_active = 0
    lock = asyncio.Lock()

    async def mock_handler(job: DownloadJob) -> None:
        nonlocal max_active, current_active
        async with lock:
            current_active += 1
            if current_active > max_active:
                max_active = current_active

        # Simulate work
        await asyncio.sleep(0.05)

        async with lock:
            current_active -= 1

    manager = QueueManager(handler=mock_handler, max_workers=3)
    manager.start()

    try:
        # Enqueue 6 different jobs
        for i in range(6):
            ok, job, pos, msg = manager.enqueue(user_id=i + 1, chat_id=i + 1, book_id=100 + i)
            assert ok is True

        # Wait for all jobs to complete
        await asyncio.wait_for(manager._queue.join(), timeout=2.0)

        assert manager.completed_count == 6
        assert manager.failed_count == 0
        # Crucial invariant: Never more than 3 workers concurrent
        assert max_active == 3
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_fifo_position_calculation():
    # Keep workers busy with an event
    release_event = asyncio.Event()

    async def paused_handler(job: DownloadJob) -> None:
        await release_event.wait()

    manager = QueueManager(handler=paused_handler, max_workers=3)
    manager.start()

    try:
        # First 3 jobs immediately get picked up by the 3 workers
        ok1, job1, pos1, _ = manager.enqueue(user_id=1, chat_id=1, book_id=10)
        ok2, job2, pos2, _ = manager.enqueue(user_id=2, chat_id=2, book_id=20)
        ok3, job3, pos3, _ = manager.enqueue(user_id=3, chat_id=3, book_id=30)

        # Allow workers to consume the first 3
        await asyncio.sleep(0.02)

        # Now all 3 workers are active (free=0)
        # Job 4 is 1st in pending queue -> position should be 1
        ok4, job4, pos4, _ = manager.enqueue(user_id=4, chat_id=4, book_id=40)
        assert pos4 == 1

        # Job 5 is 2nd in pending queue -> position should be 2
        ok5, job5, pos5, _ = manager.enqueue(user_id=5, chat_id=5, book_id=50)
        assert pos5 == 2

        # Job 6 is 3rd in pending queue -> position should be 3
        ok6, job6, pos6, _ = manager.enqueue(user_id=6, chat_id=6, book_id=60)
        assert pos6 == 3

        # Release workers
        release_event.set()
        await asyncio.wait_for(manager._queue.join(), timeout=2.0)
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_duplicate_prevention():
    release_event = asyncio.Event()

    async def paused_handler(job: DownloadJob) -> None:
        await release_event.wait()

    manager = QueueManager(handler=paused_handler, max_workers=3)
    manager.start()

    try:
        # User 1 enqueues book 42 -> ok
        ok1, job1, pos1, msg1 = manager.enqueue(user_id=1, chat_id=1, book_id=42)
        assert ok1 is True

        # User 1 tries to enqueue book 42 again while still active/pending -> rejected!
        ok2, job2, pos2, msg2 = manager.enqueue(user_id=1, chat_id=1, book_id=42)
        assert ok2 is False
        assert "already have a pending or active download" in msg2

        # User 2 enqueues book 42 -> ok (different user)
        ok3, job3, pos3, msg3 = manager.enqueue(user_id=2, chat_id=2, book_id=42)
        assert ok3 is True

        release_event.set()
        await asyncio.wait_for(manager._queue.join(), timeout=2.0)

        # After completion, User 1 can enqueue book 42 again
        ok4, job4, pos4, msg4 = manager.enqueue(user_id=1, chat_id=1, book_id=42)
        assert ok4 is True
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_failure_isolation_one_broken_job_never_halts_queue():
    processed_jobs: list[int] = []

    async def faulty_handler(job: DownloadJob) -> None:
        if job.book_id == 666:
            raise RuntimeError("Fatal download error simulated!")
        processed_jobs.append(job.book_id)

    manager = QueueManager(handler=faulty_handler, max_workers=3)
    manager.start()

    try:
        # Enqueue failing job, followed by healthy jobs
        manager.enqueue(user_id=1, chat_id=1, book_id=666)
        manager.enqueue(user_id=2, chat_id=2, book_id=101)
        manager.enqueue(user_id=3, chat_id=3, book_id=102)

        await asyncio.wait_for(manager._queue.join(), timeout=2.0)

        # One failed, two succeeded
        assert manager.failed_count == 1
        assert manager.completed_count == 2
        assert processed_jobs == [101, 102]
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_queue_stats_and_user_pending():
    release_event = asyncio.Event()

    async def paused_handler(job: DownloadJob) -> None:
        await release_event.wait()

    manager = QueueManager(handler=paused_handler, max_workers=1)
    manager.start()

    try:
        manager.enqueue(user_id=10, chat_id=10, book_id=1)
        manager.enqueue(user_id=10, chat_id=10, book_id=2)
        manager.enqueue(user_id=20, chat_id=20, book_id=3)

        await asyncio.sleep(0.02)
        stats = manager.stats()
        assert stats["active"] == 1
        assert stats["queued"] == 2

        user10_jobs = manager.get_user_pending_jobs(10)
        assert len(user10_jobs) == 2
        book_ids = {j.book_id for j in user10_jobs}
        assert book_ids == {1, 2}

        release_event.set()
        await asyncio.wait_for(manager._queue.join(), timeout=2.0)
    finally:
        await manager.stop()
