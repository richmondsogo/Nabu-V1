# Step 05: Delivery Queue Manager and 3-Worker Pool

## Objective

Implement the in-memory download delivery queue with a fixed pool of exactly 3 concurrent workers, deterministic FIFO position calculation (without relying on `qsize()`), duplicate prevention per user/book, and absolute failure isolation.

## Scope

- In-memory `asyncio.Queue[DownloadJob]` backed by exactly 3 worker tasks (`queue_manager.py`).
- Deterministic FIFO position calculation: `ahead = pending.index(job_id); free = max(0, max_workers - active_workers); position = max(1, ahead + 1 - free)`.
- User and book deduplication: prevents same user from enqueueing identical book while pending or active.
- Failure isolation: worker catches exceptions, increments failed count, and executes `task_done()` so failing jobs never halt the queue.
- Unit tests in `tests/test_queue.py`.

## Plan

1. Implement `QueueManager` with configurable worker count (`default=3`).
2. Implement `enqueue()` with duplicate check and deterministic position calculation.
3. Implement `_worker_loop()` with try/except/finally blocks guaranteeing `_queue.task_done()`.
4. Implement `stats()` and `get_user_pending_jobs()`.
5. Write concurrency tests verifying max 3 active downloads across 6 jobs.
6. Verify failure isolation, position calculation, and duplicate prevention in `tests/test_queue.py`.
7. Run `scripts/verify.ps1`.
8. Commit on `step-05-queue-manager`, push, and open Pull Request.

## Implementation

- Used an ordered list `_pending_order: list[str]` to track exact FIFO order independent of `queue.qsize()`.
- Enforced deduplication using an in-memory lock set `_user_book_locks: set[tuple[int, int]]`.
- Reused async callback protocol `JobHandler` allowing `bot.py` to wire up download execution easily.

## Discoveries

- Tracking `_pending_order` alongside `_active_jobs` provides exact FIFO position without racing against active worker capacity.

## Verification

- Tests run: 50 passed (9 config, 10 database, 17 utils, 9 ipfs, 5 queue), 0 failed.
- Verification script: `scripts/verify.ps1` returned 3 passed, 0 failed.
- Working tree: Clean git status on `step-05-queue-manager`.

## Diff / Checkpoint

- Branch: `step-05-queue-manager`.
- Files created: `queue_manager.py`, `tests/test_queue.py`, `docs/steps/05-queue-manager.md`.

## Unresolved Issues

- None.

## Decisions

- Retained deterministic position formula specified in Defect D1: `position = max(1, ahead + 1 - free)`.
