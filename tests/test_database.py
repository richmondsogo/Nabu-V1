"""Comprehensive test suite for database.py."""

from pathlib import Path
import sqlite3
import pytest

from database import Database
from models import Book


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_books.db"
    db = Database(db_file)
    db.init_schema()
    return db


def test_schema_initialization_idempotent(tmp_path: Path):
    db_file = tmp_path / "test_idem.db"
    db = Database(db_file)
    db.init_schema()
    # Calling it a second time must not fail
    db.init_schema()

    with db._get_connection() as conn:
        cur = conn.execute("SELECT name FROM sources ORDER BY name ASC")
        sources = [r["name"] for r in cur.fetchall()]
        assert sources == ["annas", "libgen"]


def test_additive_migration(tmp_path: Path):
    db_file = tmp_path / "test_legacy.db"
    # Create legacy table without additive columns
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            cid TEXT,
            md5 TEXT,
            filename TEXT,
            file_size INTEGER,
            file_type TEXT,
            description TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO books (title, author, created_at) VALUES ('Old Book', 'Legacy Author', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    # Now run db.init_schema() which must add the missing columns
    db = Database(db_file)
    db.init_schema()

    book = db._get_book_by_id_sync(1)
    assert book is not None
    assert book.title == "Old Book"
    assert book.source is None
    assert book.fetch_failures == 0
    assert book.pinned is False


def test_fts_triggers_insert_update_delete(test_db: Database):
    # 1. Insert
    book = test_db._insert_book_sync(
        title="Refactoring: Improving the Design of Existing Code",
        author="Martin Fowler",
        cid="bafybeirefactoring",
        md5="refactor_md5_01",
    )
    assert book.id is not None

    with test_db._get_connection() as conn:
        cur = conn.execute("SELECT * FROM books_fts WHERE rowid = ?", (book.id,))
        fts_row = cur.fetchone()
        assert fts_row is not None
        assert "Refactoring" in fts_row["title"]
        assert "Fowler" in fts_row["author"]

    # 2. Update
    with test_db._get_connection() as conn:
        conn.execute(
            "UPDATE books SET title = 'Refactoring 2nd Edition' WHERE id = ?",
            (book.id,),
        )
        conn.commit()

    with test_db._get_connection() as conn:
        cur = conn.execute("SELECT * FROM books_fts WHERE rowid = ?", (book.id,))
        fts_row = cur.fetchone()
        assert fts_row["title"] == "Refactoring 2nd Edition"

    # 3. Delete
    with test_db._get_connection() as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book.id,))
        conn.commit()

    with test_db._get_connection() as conn:
        cur = conn.execute("SELECT * FROM books_fts WHERE rowid = ?", (book.id,))
        assert cur.fetchone() is None


def test_search_exact_boost_and_ranking(test_db: Database):
    # Insert books where one has exact title "Clean Code"
    test_db._insert_book_sync(
        title="The Clean Coder",
        author="Robert C. Martin",
        cid="cid_clean_coder",
        md5="md5_01",
    )
    test_db._insert_book_sync(
        title="Clean Code",
        author="Robert C. Martin",
        cid="cid_clean_code",
        md5="md5_02",
    )
    test_db._insert_book_sync(
        title="Code Complete",
        author="Steve McConnell",
        cid="cid_code_complete",
        md5="md5_03",
    )

    results = test_db._search_sync("Clean Code", limit=5)
    assert len(results) >= 2
    # Exact match "Clean Code" must be first
    assert results[0].title == "Clean Code"
    assert results[1].title == "The Clean Coder"


def test_search_author(test_db: Database):
    test_db._insert_book_sync(
        title="Design Patterns",
        author="Erich Gamma, Richard Helm, Ralph Johnson, John Vlissides",
        cid="cid_dp",
        md5="md5_dp",
    )
    results = test_db._search_sync("Erich Gamma")
    assert len(results) == 1
    assert results[0].title == "Design Patterns"


def test_search_like_fallback(test_db: Database):
    test_db._insert_book_sync(
        title="Programming in C++ Primer",
        author="Stanley Lippman",
        cid="cid_cpp",
        md5="md5_cpp",
    )
    # Searching for "C++" which has special punctuation
    results = test_db._search_sync("C++")
    assert len(results) == 1
    assert results[0].title == "Programming in C++ Primer"


def test_md5_uniqueness(test_db: Database):
    test_db._insert_book_sync(
        title="Book One",
        md5="duplicate_md5",
    )
    with pytest.raises(sqlite3.IntegrityError):
        test_db._insert_book_sync(
            title="Book Two",
            md5="duplicate_md5",
        )


def test_fetch_failures_and_has_cid(test_db: Database):
    book = test_db._insert_book_sync(
        title="Unstable CID Book",
        cid="bafybeihangscid",
        md5="md5_unstable",
    )
    assert book.has_cid is True
    assert book.fetch_failures == 0

    cnt1 = test_db._record_fetch_failure_sync(book.id, "Kubo cat timed out")
    assert cnt1 == 1

    cnt2 = test_db._record_fetch_failure_sync(book.id, "Gateway 504")
    assert cnt2 == 2
    b2 = test_db._get_book_by_id_sync(book.id)
    assert b2.has_cid is True

    cnt3 = test_db._record_fetch_failure_sync(book.id, "Kubo connection reset")
    assert cnt3 == 3
    b3 = test_db._get_book_by_id_sync(book.id)
    # Exceeded threshold >= 3 failures -> treated as CID-less
    assert b3.has_cid is False

    # Updating CID resets failures
    test_db._update_book_cid_sync(book.id, "bafybeinewcid", pinned=True)
    b4 = test_db._get_book_by_id_sync(book.id)
    assert b4.cid == "bafybeinewcid"
    assert b4.fetch_failures == 0
    assert b4.pinned is True
    assert b4.has_cid is True


def test_acquisitions_and_sources_lifecycle(test_db: Database):
    # Acquisitions
    acq_id = test_db._create_acquisition_sync(
        source="annas",
        requested_by=12345,
        md5="some_md5",
        status="queued",
    )
    assert acq_id > 0

    test_db._update_acquisition_status_sync(acq_id, status="downloading")
    with test_db._get_connection() as conn:
        cur = conn.execute("SELECT status, finished_at FROM acquisitions WHERE id = ?", (acq_id,))
        row = cur.fetchone()
        assert row["status"] == "downloading"
        assert row["finished_at"] is None

    test_db._update_acquisition_status_sync(acq_id, status="imported", finished=True)
    with test_db._get_connection() as conn:
        cur = conn.execute("SELECT status, finished_at FROM acquisitions WHERE id = ?", (acq_id,))
        row = cur.fetchone()
        assert row["status"] == "imported"
        assert row["finished_at"] is not None

    # Sources
    sources = test_db._get_sources_sync()
    assert len(sources) == 2
    names = {s["name"] for s in sources}
    assert "annas" in names
    assert "libgen" in names

    test_db._update_source_status_sync("annas", ok=False, error="HTTP 429 Too Many Requests", cooldown_until="2026-10-09T08:00:00Z")
    sources_after = test_db._get_sources_sync()
    annas = next(s for s in sources_after if s["name"] == "annas")
    assert "HTTP 429" in annas["last_error"]
    assert annas["cooldown_until"] == "2026-10-09T08:00:00Z"

    test_db._update_source_status_sync("annas", ok=True)
    annas_recovered = next(s for s in test_db._get_sources_sync() if s["name"] == "annas")
    assert annas_recovered["last_ok"] is not None
    assert annas_recovered["last_error"] is None
    assert annas_recovered["cooldown_until"] is None


@pytest.mark.asyncio
async def test_async_database_operations(tmp_path: Path):
    db_file = tmp_path / "test_async.db"
    db = Database(db_file)
    await db.async_init_schema()

    inserted = await db.insert_book(
        title="Async Python Architecture",
        author="Guido van Rossum",
        cid="bafybeiasync",
        md5="md5_async",
    )
    assert inserted.id > 0

    hits = await db.search_books("Async Python")
    assert len(hits) == 1
    assert hits[0].title == "Async Python Architecture"

    by_id = await db.get_book_by_id(inserted.id)
    assert by_id is not None
    assert by_id.title == "Async Python Architecture"

    by_cid = await db.get_book_by_cid("bafybeiasync")
    assert by_cid is not None
    assert by_cid.id == inserted.id

    by_md5 = await db.get_book_by_md5("md5_async")
    assert by_md5 is not None
    assert by_md5.id == inserted.id

    # Test FTS rebuild
    await db.rebuild_fts()
    hits_after = await db.search_books("Async Python")
    assert len(hits_after) == 1

    # Test acquisition queries
    acq_id = await db.create_acquisition(source="annas", requested_by=999, md5="md5_async")
    acq = await db.get_acquisition(acq_id)
    assert acq is not None
    assert acq["source"] == "annas"

    user_acqs = await db.get_user_active_acquisitions(999)
    assert len(user_acqs) == 1
    assert user_acqs[0]["id"] == acq_id

    pending_md5 = await db.get_pending_acquisition_by_md5("md5_async")
    assert pending_md5 is not None
    assert pending_md5["id"] == acq_id

    # Test source lookup
    src = await db.get_source("annas")
    assert src is not None
    assert src["name"] == "annas"
