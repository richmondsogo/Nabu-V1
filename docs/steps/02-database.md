# Step 02: Database Layer and FTS5 Indexing

## Objective

Implement SQLite database schema with WAL mode, FTS5 full-text indexing, external content triggers, exact title boost ranking, LIKE fallback, additive schema migrations, and async-safe thread wrapper.

## Scope

- Data models (`models.py`).
- SQLite database manager (`database.py`).
- Full schema: `books`, `sources`, `acquisitions`, `meta`, and `books_fts`.
- FTS5 external content triggers (`books_fts_ai`, `books_fts_ad`, `books_fts_au`).
- Weighted bm25 ranking + exact/prefix title boost.
- LIKE fallback when FTS returns fewer than requested limit or encounters syntax issues.
- Additive schema migration check (`source`, `source_id`, `acquired_at`, `pinned`, `fetch_failures`, `last_fetch_error`).
- FTS startup verification against `meta['fts_docs']`.
- Unit test suite (`tests/test_database.py`).

## Plan

1. Create `models.py` with dataclasses (`Book`, `SearchHit`, `DownloadHandle`, `DownloadJob`, `AcquisitionJob`).
2. Implement `database.py` with schema DDL, WAL pragma configuration, and `asyncio.to_thread` wrappers.
3. Implement FTS query sanitization, exact title boost, weighted bm25, and LIKE fallback.
4. Implement additive migration check and FTS sync validation.
5. Implement CRUD and state management operations for books, fetch failures, acquisitions, and sources.
6. Write comprehensive tests in `tests/test_database.py` covering schema idempotency, migration, triggers, ranking, LIKE fallback, failure tracking, and async methods.
7. Run test suite and project verification script.

## Implementation

- Used `id INTEGER PRIMARY KEY AUTOINCREMENT` for `books` to prevent SQLite rowid recycling (per ADR 0001).
- Triggers correctly update `books_fts` on INSERT, UPDATE, and DELETE.
- Exact title boost implemented via CASE expression in ORDER BY, followed by `bm25(books_fts, 12.0, 6.0, 2.0) ASC`.
- Implemented LIKE fallback for short tokens and punctuation (e.g. `C++`) that FTS tokenizers drop.
- Tracked fetch failures: `Book.has_cid` returns False once `fetch_failures >= 3`, enabling automated re-acquisition.

## Discoveries

- FTS5 unicode61 tokenizer strips symbols; LIKE fallback successfully captures punctuation-heavy titles like `C++ Primer`.
- `sqlite3.Row` factory allows tuple and dict-like column access seamlessly.

## Verification

- Tests run: 19 passed, 0 failed (`tests/test_config.py` + `tests/test_database.py`).
- Verification script: `scripts/verify.ps1` returned 3 passed, 0 failed.
- Memory & file cleanup: SQLite in-memory and temp test databases unlinked cleanly.

## Diff / Checkpoint

- Files created: `models.py`, `database.py`, `tests/test_database.py`, `docs/steps/01-scaffold.md`, `docs/steps/02-database.md`.

## Unresolved Issues

- None in database layer.

## Decisions

- Retained `AUTOINCREMENT` on `books.id` to prevent token reuse bugs.
