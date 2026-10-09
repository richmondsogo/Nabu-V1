# Step 08: Acquisition Queue and Pipeline Manager

## Objective

Implement the background acquisition queue and pipeline manager (`acquirer.py`), decoupled from the delivery queue with dedicated concurrency limits, one-active-acquisition-per-user gating, global MD5 deduplication fan-out, hard timeout enforcement, and guaranteed temporary file cleanup.

## Scope

- `models.py`:
  - Extended `AcquisitionJob` with `acq_id: int | None` and `hit: SearchHit | None` fields.
- `acquirer.py`:
  - `AcquisitionManager`:
    - Dedicated worker pool (`MAX_ACQUIRE_JOBS=2`) consuming from an independent `asyncio.Queue[AcquisitionJob]`.
    - **One-active-per-user gate**: keeps per-user FIFO waitlist (`_user_waiting[user_id]`) ensuring each user can have at most one active scraping job, without blocking or dropping subsequent requests.
    - **Global MD5 deduplication**: if two users request the same missing book concurrently, subsequent jobs are attached as subscribers (`_active_by_md5[md5]`); exactly one retrieval executes, and upon completion, delivery is enqueued for all subscribers.
    - **Hard deadline**: entire acquisition pipeline wrapped in `asyncio.timeout(acquire_timeout)` (300s).
    - **Dual retrieval routes**:
      - `kind == "cid"`: direct retrieval via `KuboClient.cat_file` (with 90s timeout), pinning via `KuboClient.pin_cid`.
      - `kind == "url"`: streaming download via `source.download` to `tmp/<job_id>.part` with 50 MB limit, followed by ingestion into Kubo via `KuboClient.add_file(..., pin=True)`.
    - **Validation**: magic-byte inspection via `is_plausible_book_file`. Rejecting HTML error pages and corrupt files.
    - **Catalog ingestion**: updates existing row if known or inserts new row in SQLite catalog, marking acquisition record as imported/finished.
    - **Delivery dispatch**: triggers `QueueManager.enqueue` for the primary requester and all fan-out subscribers.
    - **Zero orphaned files**: temporary `.part` files unlinked in `finally` blocks on all normal and error paths.
- `tests/test_acquirer.py`:
  - 8 unit tests covering URL streaming route, CID-first route, one-active-per-user queueing, global MD5 deduplication fan-out, corrupt file rejection, timeout abortion, automatic search fallback, and updating existing book rows without duplicates.

## Verification

- Ran `scripts/verify.ps1`:
  - Git available: OK
  - No tracked secrets: OK
  - Python tests: OK (82 passed in 28.1s across all suites, 0 failed).
- All 8 acquisition unit tests passed.

## Decisions

- **Two independent queues:** Acquisition runs on 2 workers while delivery runs on 3 workers. A slow scrape or download never starves local delivery of already-pinned books.
- **Deduplication fan-out:** Rather than rejecting duplicate requests, multiple users asking for the same book share the in-flight download and both receive the document upon arrival.
- **Fail-safe cleanup:** Temp files are deleted in `finally:`, preventing disk bloat even when a download times out or fails magic-byte verification.
