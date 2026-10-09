"""Database layer for Nabu-V1.

Implements SQLite storage with WAL mode, FTS5 full-text indexing with triggers,
weighted ranking, exact-title boosting, LIKE fallback, additive schema migrations,
and an async-safe wrapper around synchronous sqlite3 operations.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any

from models import Book

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


def _sanitize_fts_query(raw_query: str) -> str:
    """Sanitize user search string into valid FTS5 prefix match tokens."""
    tokens = re.findall(r"\w+", raw_query, re.UNICODE)
    if not tokens:
        return ""
    # Token prefix matching, e.g. "clean"* "code"*
    return " ".join(f'"{token}"*' for token in tokens)


def _escape_like(raw: str) -> str:
    """Escape special LIKE pattern characters % and _ with backslash."""
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Database:
    """Async-safe SQLite database manager for Nabu-V1."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._ensure_parent_dir()

    def _ensure_parent_dir(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

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

            # Seed default sources
            conn.execute("INSERT OR IGNORE INTO sources (name) VALUES ('annas'), ('libgen')")

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
    # Search Operations
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

            # 2. LIKE fallback if results are fewer than requested limit
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
    # Book Retrieval & Mutation
    # -------------------------------------------------------------------------

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

            # Retrieve newly inserted book
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
    # Acquisition Audit Tracking
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
    # Sources Table & Health Tracking
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
