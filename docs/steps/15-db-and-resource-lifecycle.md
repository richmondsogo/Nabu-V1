# Step 15: Database and Resource Lifecycle Hardening

## Objective

Harden SQLite connection and file handle lifecycles to prevent descriptor leaks, eliminate transaction/connection churn via batch upserts, implement maintenance routines (WAL checkpointing and expired search cache pruning), and ensure clean shutdown of all background tasks and HTTP clients.

---

## Scope & Implementation

1. **SQLite Connection Closure & Transaction Management (`src/database.py`)**:
   - Refactored `Database._get_connection()` into a context manager yielding the connection within a transactional block (`with conn: yield conn`) and terminating with `finally: conn.close()`.
   - In Python's standard `sqlite3`, using `with conn:` only manages transactions (committing on success and rolling back on exception), leaving the underlying database and OS file handle open indefinitely.
   - Guaranteed every caller exiting `with self._get_connection() as conn:` commits transactions and releases the file handle.

2. **Batch Upserting (`src/database.py`, `src/search.py`)**:
   - Implemented `Database._upsert_books_sync(book_dicts)` and `async def upsert_books()`.
   - In `SearchService._execute_scrape_and_persist()`, replaced the iterative loop of 25 single-item `upsert_book()` calls with a single batch `upsert_books()` transaction.
   - Eliminated connection churn and file I/O latency, cutting test suite runtime and preventing token refill races in `UserRateLimiter`.

3. **Database Maintenance Operations (`src/database.py`, `src/bot.py`)**:
   - Added `wal_checkpoint(mode="TRUNCATE")` to `Database` to flush and reset the WAL file (`books.db-wal`).
   - Added `prune_expired_cache(now=None)` to `Database` to delete stale entries from `search_cache` where `expires_at < current_time`.
   - Integrated both maintenance operations into `/rebuild` (`handle_rebuild`) and logged maintenance results.

4. **HTTP Client Lifecycle (`src/search.py`, `src/sources/mirror_manager.py`)**:
   - Added `async def aclose(self) -> None` to `SearchService` to safely close `self._client`.
   - Added optional `http_client` and `async def aclose(self) -> None` to `MirrorManager`, reusing the persistent client in `probe_all()` and `resolve_direct_link()` and ensuring closure.

5. **Application Teardown Integration (`src/bot.py`)**:
   - Updated `post_shutdown()` to cleanly:
     - Cancel and await `startup_probe_task`.
     - Await `search_service.aclose()`.
     - Await `mirror_manager.aclose()`.
     - Execute `await db.wal_checkpoint("TRUNCATE")`.

---

## Discoveries

- **Python `sqlite3.Connection` Context Manager Behavior**: Python's `sqlite3.Connection` object implements `__enter__` and `__exit__` solely for transaction management (`commit` / `rollback`), not for connection lifetime. Using `with self._get_connection() as conn:` without an outer `@contextmanager` generator containing `finally: conn.close()` permanently leaked open file descriptors and kept SQLite locks active.
- **Connection Churn Under Scrape Persistence**: Persisting 25 search hits individually resulted in 25 separate `asyncio.to_thread` dispatches, connection initializations, WAL PRAGMAs, and transaction commits per query. Replacing this with a single batch transaction in `Database.upsert_books()` reduced search persistence from seconds to milliseconds.

---

## Verification

- **Automated Tests (`uv run pytest`)**:
  - All 86 tests passed, 0 failures in 6.80s.
  - Added test cases:
    - `test_connection_lifecycle_closes_handles` (`tests/test_database.py`)
    - `test_wal_checkpoint_and_modes` (`tests/test_database.py`)
    - `test_prune_expired_search_cache` (`tests/test_database.py`)
    - `test_search_service_aclose` (`tests/test_search.py`)
    - `test_mirror_manager_aclose` (`tests/test_search.py`)
    - Expanded `test_post_shutdown_cancels_background_tasks` (`tests/test_bot.py`) to verify shutdown teardown across search service, mirror manager, and database checkpointing.
- **Project Verification (`scripts\verify.ps1`)**:
  - Passed all 3 checks (Git state, secret scan, pytest).
