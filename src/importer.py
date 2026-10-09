"""Bulk database importer CLI for Nabu-V1 Link Resolver catalog.

Seeds the SQLite catalog from CSV, JSON, or JSONL metadata dumps, skips duplicates,
normalizes metadata, and supports bulk CID backfilling from MD5 mappings.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import sys
from typing import Any

# Ensure src directory is on sys.path for direct script execution
_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from database import Database

logger = logging.getLogger(__name__)


@dataclass
class ImportStats:
    imported: int = 0
    skipped: int = 0
    duplicates: int = 0
    invalid: int = 0
    backfilled: int = 0

    def format_report(self) -> str:
        lines = [
            f"Imported:   {self.imported:,}",
            f"Skipped:    {self.skipped:,}",
            f"Duplicates: {self.duplicates:,}",
            f"Invalid:    {self.invalid:,}",
        ]
        if self.backfilled > 0:
            lines.append(f"Backfilled: {self.backfilled:,}")
        return "\n".join(lines)


def _clean_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("none", "null", "nan") else None


def _clean_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None


def load_records_from_file(file_path: Path) -> list[dict[str, Any]]:
    """Parse a CSV or JSON/JSONL file into a list of row dictionaries."""
    suffix = file_path.suffix.lower()
    records: list[dict[str, Any]] = []

    if suffix == ".csv":
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(dict(row))
    elif suffix in (".json", ".jsonl"):
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
            if not content:
                return []
            if content.startswith("["):
                # Standard JSON array
                data = json.loads(content)
                if isinstance(data, list):
                    records = [r for r in data if isinstance(r, dict)]
            else:
                # JSON Lines (JSONL)
                for line in content.splitlines():
                    line = line.strip()
                    if line:
                        try:
                            item = json.loads(line)
                            if isinstance(item, dict):
                                records.append(item)
                        except json.JSONDecodeError:
                            continue
    else:
        raise ValueError(f"Unsupported file format: {suffix} (expected .csv, .json, or .jsonl)")

    return records


def import_metadata(
    db: Database,
    file_path: Path,
    mapping_file: Path | None = None,
) -> ImportStats:
    """Import records into SQLite database and optionally apply MD5-to-CID mappings."""
    db.init_schema()
    stats = ImportStats()

    # 1. Load optional MD5 -> CID mapping file
    md5_to_cid: dict[str, str] = {}
    if mapping_file and mapping_file.exists():
        mapping_records = load_records_from_file(mapping_file)
        for rec in mapping_records:
            m = _clean_str(rec.get("md5"))
            c = _clean_str(rec.get("cid"))
            if m and c:
                md5_to_cid[m.lower()] = c

    # 2. Load metadata records
    records = load_records_from_file(file_path)

    for rec in records:
        raw_title = rec.get("title")
        title = _clean_str(raw_title)

        # Reject records without a usable title
        if not title:
            stats.invalid += 1
            continue

        author = _clean_str(rec.get("author"))
        cid = _clean_str(rec.get("cid"))
        md5 = _clean_str(rec.get("md5"))
        if md5:
            md5 = md5.lower()

        # If CID missing but present in mapping file, backfill it
        if not cid and md5 and md5 in md5_to_cid:
            cid = md5_to_cid[md5]
            stats.backfilled += 1

        filename = _clean_str(rec.get("filename"))
        file_size = _clean_int(rec.get("file_size") or rec.get("filesize"))
        file_type = _clean_str(rec.get("file_type") or rec.get("extension"))
        description = _clean_str(rec.get("description"))
        source = _clean_str(rec.get("source"))
        source_id = _clean_str(rec.get("source_id"))

        # Check for existing record by MD5 or CID to prevent duplicate overwrite
        if md5:
            existing = db._get_book_by_md5_sync(md5)
            if existing:
                # If existing has no CID and we now have one, backfill it
                if not existing.cid and cid:
                    db._update_book_cid_sync(existing.id, cid, pinned=True)
                    stats.backfilled += 1
                else:
                    stats.duplicates += 1
                continue

        if cid:
            existing_cid = db._get_book_by_cid_sync(cid)
            if existing_cid:
                stats.duplicates += 1
                continue

        try:
            db._insert_book_sync(
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
                pinned=bool(cid),
            )
            stats.imported += 1
        except Exception as exc:
            logger.warning("Failed to insert record %r: %s", title, exc)
            stats.skipped += 1

    # Rebuild FTS index to guarantee all newly imported records are searchable
    db._rebuild_fts_sync()
    return stats


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for bulk catalog import."""
    parser = argparse.ArgumentParser(description="Nabu-V1 Bulk Catalog Importer")
    parser.add_argument("file", type=Path, help="Path to CSV or JSON/JSONL metadata file")
    parser.add_argument("--db", type=Path, default=Path("data/books.db"), help="Path to SQLite database")
    parser.add_argument("--map", type=Path, default=None, help="Optional MD5-to-CID mapping file (CSV or JSON)")
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        return 1

    db = Database(args.db)
    print(f"Importing records from {args.file} into {args.db}…")
    stats = import_metadata(db, args.file, mapping_file=args.map)

    print()
    print(stats.format_report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
