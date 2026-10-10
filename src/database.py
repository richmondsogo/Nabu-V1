"""Database layer for Nabu-V1.

Implements SQLite storage with WAL mode, FTS5 full-text indexing with triggers,
weighted ranking, exact-title boosting, LIKE fallback, additive schema migrations,
and an async-safe wrapper around synchronous sqlite3 operations.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from models import Book, Mirror

SCHEMA_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS books (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    author          TEXT,
    cid             TEXT,
    md5             TEXT,
    filename        TEXT,
    file_size       INTEGER,
    file_type       TEXT,
    description     TEXT,
    created_at      TEXT NOT NULL,
    source          TEXT,
    source_id       TEXT,
    acquired_at     TEXT,
    pinned          INTEGER NOT NULL DEFAULT 0,
    fetch_failures  INTEGER NOT NULL DEFAULT 0,
    last_fetch_error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_books_md5 ON books(md5) WHERE md5 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_books_cid ON books(cid) WHERE cid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_books_title_nocase ON books(title COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
    title,
    author,
    description,
    content='books',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS books_fts_ai AFTER INSERT ON books BEGIN
  INSERT INTO books_fts(rowid, title, author, description)
  VALUES (new.id, new.title, coalesce(new.author,''), coalesce(new.description,''));
END;

CREATE TRIGGER IF NOT EXISTS books_fts_ad AFTER DELETE ON books BEGIN
  INSERT INTO books_fts(books_fts, rowid, title, author, description)
  VALUES ('delete', old.id, old.title, coalesce(old.author,''), coalesce(old.description,''));
END;

CREATE TRIGGER IF NOT EXISTS books_fts_au AFTER UPDATE ON books BEGIN
  INSERT INTO books_fts(books_fts, rowid, title, author, description)
  VALUES ('delete', old.id, old.title, coalesce(old.author,''), coalesce(old.description,''));
  INSERT INTO books_fts(rowid, title, author, description)
  VALUES (new.id, new.title, coalesce(new.author,''), coalesce(new.description,''));
END;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_ok TEXT,
    last_error TEXT,
    cooldown_until TEXT
);

CREATE TABLE IF NOT EXISTS acquisitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER REFERENCES books(id),
    md5 TEXT,
    source TEXT NOT NULL,
    source_id TEXT,
    status TEXT NOT NULL,
    requested_by INTEGER,
    requested_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_acq_status ON acquisitions(status);

CREATE TABLE IF NOT EXISTS search_cache (
    query_norm TEXT PRIMARY KEY,
    book_ids   TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    is_complete INTEGER NOT NULL DEFAULT 1,
    pages_fetched INTEGER NOT NULL DEFAULT 1,
    total_upstream INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mirrors (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL,
    url            TEXT NOT NULL UNIQUE,
    fork           TEXT NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    fail_count     INTEGER NOT NULL DEFAULT 0,
    last_ok        TEXT,
    last_error     TEXT,
    cooldown_until REAL,
    latency_ms     INTEGER
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_book(row: sqlite3.Row) -> Book:
    return Book(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        cid=row["cid"],
        md5=row["md5"],
        filename=row["filename"],
        file_size=row["file_size"],
        file_type=row["file_type"],
        description=row["description"],
        created_at=row["created_at"],
        source=row["source"],
        source_id=row["source_id"],
        acquired_at=row["acquired_at"],
        pinned=bool(row["pinned"]),
        fetch_failures=row["fetch_failures"] if "fetch_failures" in row.keys() else 0,
        last_fetch_error=row["last_fetch_error"] if "last_fetch_error" in row.keys() else None,
    )


def _row_to_mirror(row: sqlite3.Row) -> Mirror:
    return Mirror(
        id=row["id"],
        source=row["source"],
        url=row["url"],
        fork=row["fork"],
        enabled=bool(row["enabled"]),
        fail_count=row["fail_count"],
        last_ok=row["last_ok"],
        last_error=row["last_error"],
        cooldown_until=row["cooldown_until"],
        latency_ms=row["latency_ms"],
    )


@dataclass
class SearchCacheEntry:
    book_ids: list[int]
    expires_at: float
    is_complete: bool = True
    pages_fetched: int = 1
    total_upstream: int = 0

    def __iter__(self):
        return iter((self.book_ids, self.expires_at))

    def __getitem__(self, index):
        return (self.book_ids, self.expires_at, self.is_complete, self.pages_fetched, self.total_upstream)[index]

    def __len__(self):
        return 5


def _sanitize_fts_query(raw_query: str, op: str = "AND") -> str:
    """Sanitize user search string into valid FTS5 prefix match tokens with AND or OR operator."""
    tokens = re.findall(r"\w+", raw_query, re.UNICODE)
    if not tokens:
        return ""
    if op == "OR":
        return " OR ".join(f'"{token}"*' for token in tokens)
    return " ".join(f'"{token}"*' for token in tokens)


def _escape_like(raw: str) -> str:
    """Escape special LIKE pattern characters % and _ with backslash."""
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class DatabaseStats:
    books_count: int
    search_cache_count: int
    db_size_bytes: int
    wal_size_bytes: int


class Database:
    """Async-safe SQLite database manager for Nabu-V1."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._ensure_parent_dir()

    def _ensure_parent_dir(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Synchronously initialize schema, run additive migrations, and sync FTS index."""
        with self._get_connection() as conn:
            conn.executescript(SCHEMA_DDL)

            # Additive migration check for existing books table
            cursor = conn.execute("PRAGMA table_info(books)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            additive_cols = {
                "source": "TEXT",
                "source_id": "TEXT",
                "acquired_at": "TEXT",
                "pinned": "INTEGER NOT NULL DEFAULT 0",
                "fetch_failures": "INTEGER NOT NULL DEFAULT 0",
                "last_fetch_error": "TEXT",
            }
            for col, col_def in additive_cols.items():
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE books ADD COLUMN {col} {col_def}")

            # Additive migration check for existing search_cache table
            cursor_cache = conn.execute("PRAGMA table_info(search_cache)")
            existing_cache_cols = {row["name"] for row in cursor_cache.fetchall()}
            additive_cache_cols = {
                "is_complete": "INTEGER NOT NULL DEFAULT 1",
                "pages_fetched": "INTEGER NOT NULL DEFAULT 1",
                "total_upstream": "INTEGER NOT NULL DEFAULT 0",
            }
            for col, col_def in additive_cache_cols.items():
                if col not in existing_cache_cols:
                    conn.execute(f"ALTER TABLE search_cache ADD COLUMN {col} {col_def}")

            # Seed default sources
            conn.execute("INSERT OR IGNORE INTO sources (name) VALUES ('annas'), ('libgen')")

            # Seed default mirrors
            mirrors_seed = [
                ("libgen", "https://libgen.li", "li"),
                ("libgen", "https://libgen.la", "li"),
                ("libgen", "https://libgen.bz", "li"),
                ("libgen", "https://libgen.is", "is"),
                ("libgen", "https://libgen.rs", "is"),
            ]
            for src, url, fork in mirrors_seed:
                conn.execute(
                    "INSERT OR IGNORE INTO mirrors (source, url, fork) VALUES (?, ?, ?)",
                    (src, url, fork),
                )

            # FTS verification against meta table
            count_cur = conn.execute("SELECT count(*) as cnt FROM books")
            total_books = count_cur.fetchone()["cnt"]

            meta_cur = conn.execute("SELECT value FROM meta WHERE key = 'fts_docs'")
            meta_row = meta_cur.fetchone()

            if meta_row is None or meta_row["value"] != str(total_books):
                conn.execute("INSERT INTO books_fts(books_fts) VALUES('rebuild')")
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('fts_docs', ?)",
                    (str(total_books),),
                )
            conn.commit()

    async def async_init_schema(self) -> None:
        """Asynchronously initialize schema."""
        await asyncio.to_thread(self.init_schema)

    # -------------------------------------------------------------------------
    # Search Operations (FTS & LIKE)
    # -------------------------------------------------------------------------

    def _search_sync(self, query: str, limit: int = 5) -> list[Book]:
        cleaned = query.strip()
        if not cleaned:
            return []

        fts_query = _sanitize_fts_query(cleaned)
        found_books: list[Book] = []
        found_ids: set[int] = set()

        with self._get_connection() as conn:
            # 1. Try weighted FTS search if valid tokens exist
            if fts_query:
                try:
                    sql_fts = """
                        SELECT b.*
                        FROM books b
                        JOIN books_fts f ON b.id = f.rowid
                        WHERE books_fts MATCH :query
                        ORDER BY
                          CASE
                            WHEN lower(b.title) = lower(:exact) THEN 0
                            WHEN lower(b.title) LIKE lower(:prefix) ESCAPE '\\' THEN 1
                            ELSE 2
                          END,
                          bm25(books_fts, 12.0, 6.0, 2.0) ASC,
                          b.title COLLATE NOCASE ASC
                        LIMIT :limit;
                    """
                    exact = cleaned
                    prefix = f"{_escape_like(cleaned)}%"
                    cursor = conn.execute(
                        sql_fts,
                        {
                            "query": fts_query,
                            "exact": exact,
                            "prefix": prefix,
                            "limit": limit,
                        },
                    )
                    for row in cursor.fetchall():
                        book = _row_to_book(row)
                        found_books.append(book)
                        found_ids.add(book.id)
                except sqlite3.OperationalError:
                    # Catch syntax or operational errors and fall back to LIKE
                    pass

            # 2. Try forgiving FTS OR query if results from strict AND returned 0 hits
            fts_or_query = _sanitize_fts_query(cleaned, op="OR")
            if not found_books and fts_or_query and fts_or_query != fts_query:
                try:
                    sql_fts_or = """
                        SELECT b.*
                        FROM books b
                        JOIN books_fts f ON b.id = f.rowid
                        WHERE books_fts MATCH :query
                        ORDER BY
                          CASE
                            WHEN lower(b.title) = lower(:exact) THEN 0
                            WHEN lower(b.title) LIKE lower(:prefix) ESCAPE '\\' THEN 1
                            ELSE 2
                          END,
                          bm25(books_fts, 12.0, 6.0, 2.0) ASC,
                          b.title COLLATE NOCASE ASC
                        LIMIT :limit;
                    """
                    cursor = conn.execute(
                        sql_fts_or,
                        {
                            "query": fts_or_query,
                            "exact": cleaned,
                            "prefix": f"{_escape_like(cleaned)}%",
                            "limit": limit,
                        },
                    )
                    for row in cursor.fetchall():
                        book = _row_to_book(row)
                        found_books.append(book)
                        found_ids.add(book.id)
                except sqlite3.OperationalError:
                    pass

            # 3. LIKE fallback if results are fewer than requested limit
            if len(found_books) < limit:
                needed = limit - len(found_books)
                like_term = f"%{_escape_like(cleaned)}%"

                if found_ids:
                    placeholders = ",".join("?" for _ in found_ids)
                    sql_like = f"""
                        SELECT * FROM books
                        WHERE (lower(title) LIKE lower(?) ESCAPE '\\' OR lower(author) LIKE lower(?) ESCAPE '\\')
                          AND id NOT IN ({placeholders})
                        ORDER BY
                          CASE
                            WHEN lower(title) = lower(?) THEN 0
                            WHEN lower(title) LIKE lower(?) ESCAPE '\\' THEN 1
                            ELSE 2
                          END,
                          title COLLATE NOCASE ASC
                        LIMIT ?;
                    """
                    prefix = f"{_escape_like(cleaned)}%"
                    params = [like_term, like_term, *found_ids, cleaned, prefix, needed]
                else:
                    sql_like = """
                        SELECT * FROM books
                        WHERE (lower(title) LIKE lower(?) ESCAPE '\\' OR lower(author) LIKE lower(?) ESCAPE '\\')
                        ORDER BY
                          CASE
                            WHEN lower(title) = lower(?) THEN 0
                            WHEN lower(title) LIKE lower(?) ESCAPE '\\' THEN 1
                            ELSE 2
                          END,
                          title COLLATE NOCASE ASC
                        LIMIT ?;
                    """
                    prefix = f"{_escape_like(cleaned)}%"
                    params = [like_term, like_term, cleaned, prefix, needed]

                cursor = conn.execute(sql_like, params)
                for row in cursor.fetchall():
                    found_books.append(_row_to_book(row))

        return found_books

    async def search_books(self, query: str, limit: int = 5) -> list[Book]:
        """Search books in SQLite catalog using FTS5 and LIKE fallback."""
        return await asyncio.to_thread(self._search_sync, query, limit)

    # -------------------------------------------------------------------------
    # Search Cache Operations
    # -------------------------------------------------------------------------

    def _get_search_cache_sync(
        self, query_norm: str
    ) -> SearchCacheEntry | None:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT book_ids, expires_at, is_complete, pages_fetched, total_upstream FROM search_cache WHERE query_norm = ?",
                (query_norm,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            try:
                ids = json.loads(row["book_ids"])
                keys = row.keys()
                is_comp = bool(row["is_complete"]) if "is_complete" in keys else True
                pgs = int(row["pages_fetched"]) if "pages_fetched" in keys else 1
                tot = int(row["total_upstream"]) if "total_upstream" in keys else 0
                return SearchCacheEntry(
                    book_ids=ids,
                    expires_at=float(row["expires_at"]),
                    is_complete=is_comp,
                    pages_fetched=pgs,
                    total_upstream=tot,
                )
            except Exception:
                return None

    async def get_search_cache(
        self, query_norm: str
    ) -> SearchCacheEntry | None:
        """Retrieve cached book IDs and expiry timestamp for normalized query."""
        return await asyncio.to_thread(self._get_search_cache_sync, query_norm)

    def _set_search_cache_sync(
        self,
        query_norm: str,
        book_ids: list[int],
        ttl: float,
        is_complete: bool = True,
        pages_fetched: int = 1,
        total_upstream: int = 0,
        now: float | None = None,
    ) -> None:
        current_time = time.time() if now is None else now
        expires_at = current_time + ttl
        payload = json.dumps(book_ids)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO search_cache (query_norm, book_ids, fetched_at, expires_at, is_complete, pages_fetched, total_upstream)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(query_norm) DO UPDATE SET
                    book_ids = excluded.book_ids,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at,
                    is_complete = excluded.is_complete,
                    pages_fetched = excluded.pages_fetched,
                    total_upstream = excluded.total_upstream
                """,
                (query_norm, payload, current_time, expires_at, 1 if is_complete else 0, pages_fetched, total_upstream),
            )
            conn.commit()

    async def set_search_cache(
        self,
        query_norm: str,
        book_ids: list[int],
        ttl: float,
        is_complete: bool = True,
        pages_fetched: int = 1,
        total_upstream: int = 0,
        now: float | None = None,
    ) -> None:
        """Store book IDs in search cache with TTL and completion status."""
        await asyncio.to_thread(
            self._set_search_cache_sync,
            query_norm,
            book_ids,
            ttl,
            is_complete,
            pages_fetched,
            total_upstream,
            now,
        )

    # -------------------------------------------------------------------------
    # Book Retrieval & Mutation
    # -------------------------------------------------------------------------

    def _get_books_by_ids_sync(self, ids: list[int]) -> list[Book]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM books WHERE id IN ({placeholders})",
                tuple(ids),
            )
            rows = {row["id"]: _row_to_book(row) for row in cursor.fetchall()}
            # Preserve the ordering of the input ids
            return [rows[i] for i in ids if i in rows]

    async def get_books_by_ids(self, ids: list[int]) -> list[Book]:
        """Fetch books by a list of primary keys, preserving order."""
        return await asyncio.to_thread(self._get_books_by_ids_sync, ids)

    def _get_book_by_id_sync(self, book_id: int) -> Book | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,))
            row = cursor.fetchone()
            return _row_to_book(row) if row else None

    async def get_book_by_id(self, book_id: int) -> Book | None:
        return await asyncio.to_thread(self._get_book_by_id_sync, book_id)

    def _get_book_by_md5_sync(self, md5: str) -> Book | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM books WHERE md5 = ?", (md5,))
            row = cursor.fetchone()
            return _row_to_book(row) if row else None

    async def get_book_by_md5(self, md5: str) -> Book | None:
        return await asyncio.to_thread(self._get_book_by_md5_sync, md5)

    def _get_book_by_cid_sync(self, cid: str) -> Book | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM books WHERE cid = ?", (cid,))
            row = cursor.fetchone()
            return _row_to_book(row) if row else None

    async def get_book_by_cid(self, cid: str) -> Book | None:
        return await asyncio.to_thread(self._get_book_by_cid_sync, cid)

    def _upsert_book_sync(
        self,
        title: str,
        author: str | None = None,
        cid: str | None = None,
        md5: str | None = None,
        filename: str | None = None,
        file_size: int | None = None,
        file_type: str | None = None,
        description: str | None = None,
        source: str | None = None,
        source_id: str | None = None,
        acquired_at: str | None = None,
        pinned: bool = False,
    ) -> int:
        """Insert book or update existing record on md5 conflict (upstream wins)."""
        now = _now_iso()
        with self._get_connection() as conn:
            if md5:
                cursor = conn.execute(
                    """
                    INSERT INTO books (
                        title, author, cid, md5, filename, file_size, file_type,
                        description, created_at, source, source_id, acquired_at, pinned
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(md5) WHERE md5 IS NOT NULL DO UPDATE SET
                        title = excluded.title,
                        author = coalesce(excluded.author, books.author),
                        file_size = coalesce(excluded.file_size, books.file_size),
                        file_type = coalesce(excluded.file_type, books.file_type),
                        source = coalesce(excluded.source, books.source),
                        source_id = coalesce(excluded.source_id, books.source_id),
                        description = coalesce(excluded.description, books.description)
                    """,
                    (
                        title,
                        author,
                        cid,
                        md5,
                        filename,
                        file_size,
                        file_type,
                        description,
                        now,
                        source,
                        source_id,
                        acquired_at or (now if cid else None),
                        1 if pinned else 0,
                    ),
                )
                conn.commit()
                if cursor.rowcount > 0 and cursor.lastrowid:
                    return cursor.lastrowid

                # Existing row on conflict: fetch its id
                cur_existing = conn.execute("SELECT id FROM books WHERE md5 = ?", (md5,))
                row = cur_existing.fetchone()
                if row:
                    return row["id"]

            # Fallback if no md5 provided
            cursor = conn.execute(
                """
                INSERT INTO books (
                    title, author, cid, md5, filename, file_size, file_type,
                    description, created_at, source, source_id, acquired_at, pinned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    author,
                    cid,
                    md5,
                    filename,
                    file_size,
                    file_type,
                    description,
                    now,
                    source,
                    source_id,
                    acquired_at or (now if cid else None),
                    1 if pinned else 0,
                ),
            )
            conn.commit()
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    async def upsert_book(
        self,
        title: str,
        author: str | None = None,
        cid: str | None = None,
        md5: str | None = None,
        filename: str | None = None,
        file_size: int | None = None,
        file_type: str | None = None,
        description: str | None = None,
        source: str | None = None,
        source_id: str | None = None,
        acquired_at: str | None = None,
        pinned: bool = False,
    ) -> int:
        return await asyncio.to_thread(
            self._upsert_book_sync,
            title=title,
            author=author,
            cid=cid,
            md5=md5,
            filename=filename,
            file_size=file_size,
            file_type=file_type,
            description=description,
            source=source,
            source_id=source_id,
            acquired_at=acquired_at,
            pinned=pinned,
        )

    def _upsert_books_sync(self, book_dicts: list[dict[str, Any]]) -> list[int]:
        """Batch upsert multiple books within a single connection and transaction."""
        now = _now_iso()
        ids: list[int] = []
        with self._get_connection() as conn:
            for b in book_dicts:
                title = b["title"]
                author = b.get("author")
                cid = b.get("cid")
                md5 = b.get("md5")
                filename = b.get("filename")
                file_size = b.get("file_size")
                file_type = b.get("file_type")
                description = b.get("description")
                source = b.get("source")
                source_id = b.get("source_id")
                acquired_at = b.get("acquired_at")
                pinned = b.get("pinned", False)

                if md5:
                    cursor = conn.execute(
                        """
                        INSERT INTO books (
                            title, author, cid, md5, filename, file_size, file_type,
                            description, created_at, source, source_id, acquired_at, pinned
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(md5) WHERE md5 IS NOT NULL DO UPDATE SET
                            title = excluded.title,
                            author = coalesce(excluded.author, books.author),
                            file_size = coalesce(excluded.file_size, books.file_size),
                            file_type = coalesce(excluded.file_type, books.file_type),
                            source = coalesce(excluded.source, books.source),
                            source_id = coalesce(excluded.source_id, books.source_id),
                            description = coalesce(excluded.description, books.description)
                        """,
                        (
                            title,
                            author,
                            cid,
                            md5,
                            filename,
                            file_size,
                            file_type,
                            description,
                            now,
                            source,
                            source_id,
                            acquired_at or (now if cid else None),
                            1 if pinned else 0,
                        ),
                    )
                    if cursor.rowcount > 0 and cursor.lastrowid:
                        ids.append(cursor.lastrowid)
                        continue

                    cur_existing = conn.execute("SELECT id FROM books WHERE md5 = ?", (md5,))
                    row = cur_existing.fetchone()
                    if row:
                        ids.append(row["id"])
                        continue

                cursor = conn.execute(
                    """
                    INSERT INTO books (
                        title, author, cid, md5, filename, file_size, file_type,
                        description, created_at, source, source_id, acquired_at, pinned
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        author,
                        cid,
                        md5,
                        filename,
                        file_size,
                        file_type,
                        description,
                        now,
                        source,
                        source_id,
                        acquired_at or (now if cid else None),
                        1 if pinned else 0,
                    ),
                )
                if cursor.lastrowid:
                    ids.append(cursor.lastrowid)
            conn.commit()
        return ids

    async def upsert_books(self, book_dicts: list[dict[str, Any]]) -> list[int]:
        """Asynchronously batch upsert multiple books."""
        return await asyncio.to_thread(self._upsert_books_sync, book_dicts)


    def _insert_book_sync(
        self,
        title: str,
        author: str | None = None,
        cid: str | None = None,
        md5: str | None = None,
        filename: str | None = None,
        file_size: int | None = None,
        file_type: str | None = None,
        description: str | None = None,
        source: str | None = None,
        source_id: str | None = None,
        acquired_at: str | None = None,
        pinned: bool = False,
    ) -> Book:
        now = _now_iso()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO books (
                    title, author, cid, md5, filename, file_size, file_type,
                    description, created_at, source, source_id, acquired_at, pinned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    author,
                    cid,
                    md5,
                    filename,
                    file_size,
                    file_type,
                    description,
                    now,
                    source,
                    source_id,
                    acquired_at or (now if cid else None),
                    1 if pinned else 0,
                ),
            )
            book_id = cursor.lastrowid
            assert book_id is not None
            conn.commit()

            cur = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,))
            return _row_to_book(cur.fetchone())

    async def insert_book(
        self,
        title: str,
        author: str | None = None,
        cid: str | None = None,
        md5: str | None = None,
        filename: str | None = None,
        file_size: int | None = None,
        file_type: str | None = None,
        description: str | None = None,
        source: str | None = None,
        source_id: str | None = None,
        acquired_at: str | None = None,
        pinned: bool = False,
    ) -> Book:
        return await asyncio.to_thread(
            self._insert_book_sync,
            title=title,
            author=author,
            cid=cid,
            md5=md5,
            filename=filename,
            file_size=file_size,
            file_type=file_type,
            description=description,
            source=source,
            source_id=source_id,
            acquired_at=acquired_at,
            pinned=pinned,
        )

    def _update_book_cid_sync(self, book_id: int, cid: str, pinned: bool = True) -> None:
        now = _now_iso()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE books
                SET cid = ?, pinned = ?, acquired_at = ?, fetch_failures = 0, last_fetch_error = NULL
                WHERE id = ?
                """,
                (cid, 1 if pinned else 0, now, book_id),
            )
            conn.commit()

    async def update_book_cid(self, book_id: int, cid: str, pinned: bool = True) -> None:
        await asyncio.to_thread(self._update_book_cid_sync, book_id, cid, pinned)

    def _record_fetch_failure_sync(self, book_id: int, error: str) -> int:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE books
                SET fetch_failures = fetch_failures + 1, last_fetch_error = ?
                WHERE id = ?
                """,
                (error, book_id),
            )
            conn.commit()
            cursor = conn.execute("SELECT fetch_failures FROM books WHERE id = ?", (book_id,))
            row = cursor.fetchone()
            return row["fetch_failures"] if row else 0

    async def record_fetch_failure(self, book_id: int, error: str) -> int:
        return await asyncio.to_thread(self._record_fetch_failure_sync, book_id, error)

    # -------------------------------------------------------------------------
    # Acquisition Audit Tracking (Retained for backwards compatibility)
    # -------------------------------------------------------------------------

    def _create_acquisition_sync(
        self,
        source: str,
        requested_by: int,
        md5: str | None = None,
        source_id: str | None = None,
        book_id: int | None = None,
        status: str = "queued",
    ) -> int:
        now = _now_iso()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO acquisitions (
                    book_id, md5, source, source_id, status, requested_by, requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (book_id, md5, source, source_id, status, requested_by, now),
            )
            acq_id = cursor.lastrowid
            assert acq_id is not None
            conn.commit()
            return acq_id

    async def create_acquisition(
        self,
        source: str,
        requested_by: int,
        md5: str | None = None,
        source_id: str | None = None,
        book_id: int | None = None,
        status: str = "queued",
    ) -> int:
        return await asyncio.to_thread(
            self._create_acquisition_sync,
            source=source,
            requested_by=requested_by,
            md5=md5,
            source_id=source_id,
            book_id=book_id,
            status=status,
        )

    def _update_acquisition_status_sync(
        self,
        acq_id: int,
        status: str,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        finished_at = _now_iso() if finished else None
        with self._get_connection() as conn:
            if finished_at:
                conn.execute(
                    """
                    UPDATE acquisitions
                    SET status = ?, error = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (status, error, finished_at, acq_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE acquisitions
                    SET status = ?, error = ?
                    WHERE id = ?
                    """,
                    (status, error, acq_id),
                )
            conn.commit()

    async def update_acquisition_status(
        self,
        acq_id: int,
        status: str,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        await asyncio.to_thread(
            self._update_acquisition_status_sync,
            acq_id=acq_id,
            status=status,
            error=error,
            finished=finished,
        )

    def _get_acquisition_sync(self, acq_id: int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM acquisitions WHERE id = ?", (acq_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    async def get_acquisition(self, acq_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_acquisition_sync, acq_id)

    # -------------------------------------------------------------------------
    # Mirrors Table & Health Tracking
    # -------------------------------------------------------------------------

    def _active_mirrors_sync(self, source: str = "libgen") -> list[Mirror]:
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM mirrors
                WHERE source = ? AND enabled = 1 AND (cooldown_until IS NULL OR cooldown_until <= ?)
                ORDER BY fail_count ASC, coalesce(latency_ms, 999999) ASC
                """,
                (source, now),
            )
            return [_row_to_mirror(row) for row in cursor.fetchall()]

    async def active_mirrors(self, source: str = "libgen") -> list[Mirror]:
        """Return active mirrors for source, ordered by health and latency."""
        return await asyncio.to_thread(self._active_mirrors_sync, source)

    def _get_all_mirrors_sync(self, source: str = "libgen") -> list[Mirror]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM mirrors WHERE source = ? ORDER BY id ASC",
                (source,),
            )
            return [_row_to_mirror(row) for row in cursor.fetchall()]

    async def get_all_mirrors(self, source: str = "libgen") -> list[Mirror]:
        return await asyncio.to_thread(self._get_all_mirrors_sync, source)

    def _record_mirror_result_sync(
        self,
        url: str,
        ok: bool,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        now = time.time()
        now_str = _now_iso()
        with self._get_connection() as conn:
            if ok:
                conn.execute(
                    """
                    UPDATE mirrors
                    SET fail_count = 0, last_ok = ?, last_error = NULL, cooldown_until = NULL, latency_ms = ?
                    WHERE url = ?
                    """,
                    (now_str, latency_ms, url),
                )
            else:
                cur = conn.execute("SELECT fail_count FROM mirrors WHERE url = ?", (url,))
                row = cur.fetchone()
                current_fails = row["fail_count"] if row else 0
                new_fails = current_fails + 1
                # Exponential cooldown: 30s, 60s, 120s, up to 600s
                backoff_secs = min(30.0 * (2 ** (new_fails - 1)), 600.0)
                cooldown_until = now + backoff_secs
                err_msg = f"{now_str}: {error}" if error else now_str

                conn.execute(
                    """
                    UPDATE mirrors
                    SET fail_count = ?, last_error = ?, cooldown_until = ?, latency_ms = NULL
                    WHERE url = ?
                    """,
                    (new_fails, err_msg, cooldown_until, url),
                )
            conn.commit()

    async def record_mirror_result(
        self,
        url: str,
        ok: bool,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        """Record mirror success or failure with latency and exponential backoff."""
        await asyncio.to_thread(self._record_mirror_result_sync, url, ok, latency_ms, error)

    # -------------------------------------------------------------------------
    # Sources Table & Health Tracking (Backwards compatibility)
    # -------------------------------------------------------------------------

    def _get_source_sync(self, name: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM sources WHERE name = ?", (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    async def get_source(self, name: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_source_sync, name)

    def _get_sources_sync(self) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM sources ORDER BY id ASC")
            return [dict(row) for row in cursor.fetchall()]

    async def get_sources(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._get_sources_sync)

    def _update_source_status_sync(
        self,
        name: str,
        ok: bool,
        error: str | None = None,
        cooldown_until: str | None = None,
    ) -> None:
        now = _now_iso()
        with self._get_connection() as conn:
            if ok:
                conn.execute(
                    """
                    UPDATE sources
                    SET last_ok = ?, last_error = NULL, cooldown_until = NULL
                    WHERE name = ?
                    """,
                    (now, name),
                )
            else:
                conn.execute(
                    """
                    UPDATE sources
                    SET last_error = ?, cooldown_until = coalesce(?, cooldown_until)
                    WHERE name = ?
                    """,
                    (f"{now}: {error}" if error else now, cooldown_until, name),
                )
            conn.commit()

    async def update_source_status(
        self,
        name: str,
        ok: bool,
        error: str | None = None,
        cooldown_until: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._update_source_status_sync,
            name=name,
            ok=ok,
            error=error,
            cooldown_until=cooldown_until,
        )

    def _get_user_active_acquisitions_sync(self, user_id: int) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM acquisitions
                WHERE requested_by = ? AND finished_at IS NULL
                ORDER BY id DESC
                """,
                (user_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    async def get_user_active_acquisitions(self, user_id: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._get_user_active_acquisitions_sync, user_id)

    def _get_pending_acquisition_by_md5_sync(self, md5: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM acquisitions
                WHERE md5 = ? AND finished_at IS NULL
                ORDER BY id ASC
                LIMIT 1
                """,
                (md5,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    async def get_pending_acquisition_by_md5(self, md5: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_pending_acquisition_by_md5_sync, md5)

    # -------------------------------------------------------------------------
    # Maintenance
    # -------------------------------------------------------------------------

    def _rebuild_fts_sync(self) -> None:
        with self._get_connection() as conn:
            conn.execute("INSERT INTO books_fts(books_fts) VALUES('rebuild')")
            cursor = conn.execute("SELECT count(*) as cnt FROM books")
            total = cursor.fetchone()["cnt"]
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('fts_docs', ?)",
                (str(total),),
            )
            conn.commit()

    async def rebuild_fts(self) -> None:
        """Rebuild the SQLite FTS5 index from books table."""
        await asyncio.to_thread(self._rebuild_fts_sync)

    def wal_checkpoint_sync(self, mode: str = "TRUNCATE") -> dict[str, int]:
        """Run a SQLite WAL checkpoint (default TRUNCATE to flush and reset WAL file)."""
        valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
        mode_upper = mode.upper()
        if mode_upper not in valid_modes:
            raise ValueError(f"Invalid WAL checkpoint mode: {mode}. Must be one of {valid_modes}")
        with self._get_connection() as conn:
            cursor = conn.execute(f"PRAGMA wal_checkpoint({mode_upper});")
            row = cursor.fetchone()
            if row:
                return {"busy": int(row[0]), "log": int(row[1]), "checkpointed": int(row[2])}
            return {"busy": 0, "log": 0, "checkpointed": 0}

    async def wal_checkpoint(self, mode: str = "TRUNCATE") -> dict[str, int]:
        """Asynchronously run a SQLite WAL checkpoint."""
        return await asyncio.to_thread(self.wal_checkpoint_sync, mode)

    def prune_expired_cache_sync(self, now: float | None = None) -> int:
        """Delete expired search cache entries and return the count of deleted rows."""
        current_time = time.time() if now is None else now
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM search_cache WHERE expires_at < ?",
                (current_time,),
            )
            return cursor.rowcount

    async def prune_expired_cache(self, now: float | None = None) -> int:
        """Asynchronously delete expired search cache entries."""
        return await asyncio.to_thread(self.prune_expired_cache_sync, now)

    def _get_stats_sync(self) -> DatabaseStats:
        with self._get_connection() as conn:
            row_b = conn.execute("SELECT count(*) as cnt FROM books").fetchone()
            books_cnt = row_b["cnt"] if row_b else 0
            row_c = conn.execute("SELECT count(*) as cnt FROM search_cache").fetchone()
            cache_cnt = row_c["cnt"] if row_c else 0

        wal_path = Path(str(self.db_path) + "-wal")
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        wal_size = wal_path.stat().st_size if wal_path.exists() else 0
        return DatabaseStats(
            books_count=books_cnt,
            search_cache_count=cache_cnt,
            db_size_bytes=db_size,
            wal_size_bytes=wal_size,
        )

    async def get_stats(self) -> DatabaseStats:
        """Asynchronously retrieve database counts and file size telemetry."""
        return await asyncio.to_thread(self._get_stats_sync)


