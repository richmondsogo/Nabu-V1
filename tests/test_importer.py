"""Unit tests for importer CLI and bulk metadata ingestion."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import pytest

from database import Database
from importer import import_metadata, main


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "importer_test.db")
    db.init_schema()
    return db


def test_import_csv_success(test_db: Database, tmp_path: Path) -> None:
    csv_file = tmp_path / "books.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "author", "cid", "md5", "extension", "filesize"])
        writer.writerow(["Structure and Interpretation of Computer Programs", "Harold Abelson", "bafykbz_sicp", "11111111111111111111111111111111", "pdf", "15000000"])
        writer.writerow(["Concepts, Techniques, and Models of Computer Programming", "Peter Van Roy", "bafykbz_ctm", "22222222222222222222222222222222", "pdf", "12000000"])
        writer.writerow(["Types and Programming Languages", "Benjamin C. Pierce", None, "33333333333333333333333333333333", "pdf", "8000000"])

    stats = import_metadata(test_db, csv_file)
    assert stats.imported == 3
    assert stats.invalid == 0
    assert stats.duplicates == 0

    # Verify FTS5 search
    hits = test_db._search_sync("Structure and Interpretation")
    assert len(hits) == 1
    assert hits[0].title == "Structure and Interpretation of Computer Programs"
    assert hits[0].cid == "bafykbz_sicp"


def test_import_json_and_jsonl(test_db: Database, tmp_path: Path) -> None:
    # 1. JSON Array
    json_file = tmp_path / "data.json"
    json_file.write_text(
        json.dumps([
            {"title": "The Art of Computer Programming Vol 1", "author": "Donald Knuth", "md5": "44444444444444444444444444444444"},
            {"title": "The Art of Computer Programming Vol 2", "author": "Donald Knuth", "md5": "55555555555555555555555555555555"},
        ]),
        encoding="utf-8",
    )
    stats1 = import_metadata(test_db, json_file)
    assert stats1.imported == 2

    # 2. JSON Lines (JSONL)
    jsonl_file = tmp_path / "data.jsonl"
    jsonl_file.write_text(
        '{"title": "Compilers: Principles, Techniques, and Tools", "author": "Aho", "md5": "66666666666666666666666666666666"}\n'
        '{"title": "Operating Systems: Three Easy Pieces", "author": "Arpaci-Dusseau", "md5": "77777777777777777777777777777777"}\n',
        encoding="utf-8",
    )
    stats2 = import_metadata(test_db, jsonl_file)
    assert stats2.imported == 2

    hits = test_db._search_sync("Computer Programming")
    assert len(hits) == 2


def test_import_invalid_title_rejected(test_db: Database, tmp_path: Path) -> None:
    csv_file = tmp_path / "invalid.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "author", "md5"])
        writer.writerow(["", "Anonymous", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"])
        writer.writerow(["   ", "Ghost", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"])
        writer.writerow(["Valid Book Title", "Real Author", "cccccccccccccccccccccccccccccccc"])

    stats = import_metadata(test_db, csv_file)
    assert stats.invalid == 2
    assert stats.imported == 1


def test_import_duplicate_prevention(test_db: Database, tmp_path: Path) -> None:
    # Seed one book
    test_db._insert_book_sync(
        title="Original Book",
        md5="dddddddddddddddddddddddddddddddd",
        cid="bafykbz_original",
    )

    csv_file = tmp_path / "dupe.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "author", "md5", "cid"])
        writer.writerow(["Modified Title", "New Author", "dddddddddddddddddddddddddddddddd", "bafykbz_different"])

    stats = import_metadata(test_db, csv_file)
    assert stats.duplicates == 1
    assert stats.imported == 0

    # Ensure original was not overwritten
    orig = test_db._get_book_by_md5_sync("dddddddddddddddddddddddddddddddd")
    assert orig is not None
    assert orig.title == "Original Book"
    assert orig.cid == "bafykbz_original"


def test_import_backfill_cid_via_mapping_file(test_db: Database, tmp_path: Path) -> None:
    # 1. Existing book in DB has no CID
    test_db._insert_book_sync(
        title="Incomplete Book",
        md5="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        cid=None,
    )

    # 2. Mapping file containing MD5 -> CID
    map_file = tmp_path / "cid_map.csv"
    with open(map_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["md5", "cid"])
        writer.writerow(["eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "bafykbz_backfilled_cid"])

    # 3. CSV with metadata for the book
    csv_file = tmp_path / "metadata.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "md5"])
        writer.writerow(["Incomplete Book", "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"])

    stats = import_metadata(test_db, csv_file, mapping_file=map_file)
    assert stats.backfilled >= 1

    book = test_db._get_book_by_md5_sync("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")
    assert book is not None
    assert book.cid == "bafykbz_backfilled_cid"
    assert book.pinned is True


def test_importer_cli_main(tmp_path: Path) -> None:
    db_file = tmp_path / "cli_test.db"
    csv_file = tmp_path / "cli_books.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "author"])
        writer.writerow(["The Mythical Man-Month", "Fred Brooks"])

    ret = main([str(csv_file), "--db", str(db_file)])
    assert ret == 0

    db = Database(db_file)
    hits = db._search_sync("Mythical Man-Month")
    assert len(hits) == 1
