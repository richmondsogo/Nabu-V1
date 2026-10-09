# Step 10: Bulk Importer CLI & Full Auto-Acquisition Bot Pipeline

## Objective

Deliver the bulk metadata importer CLI (`importer.py`) supporting CSV, JSON, and JSONL catalog ingestion with MD5-to-CID backfilling, and finalize end-to-end integration across the Telegram bot, delivery queue, and shadow library acquisition subsystem.

## Scope

- `importer.py`:
  - `BulkImporter` class supporting streaming ingestion of CSV, JSON, and JSONL catalog exports.
  - Strict input validation: non-empty title required, valid extensions, sanitized metadata.
  - MD5 duplicate prevention avoiding database collisions.
  - Secondary MD5-to-CID backfill mapping file support for enriching existing catalog rows.
  - Automatic FTS5 search index rebuild upon import completion.
  - CLI entrypoint (`python -m importer` / `python importer.py`) with `--db`, `--format`, `--backfill-cid`, and `--batch-size` arguments.
- `bot.py`:
  - Initialized `AcquisitionManager` and wired it into Telegram `Application` lifecycle.
  - Auto-acquisition fallback: automatically searches shadow libraries when local catalog returns no hits (when `AUTO_ACQUIRE=true`).
  - Added interactive inline "Search web" / "More results" buttons with `web:<token>` callback handler.
  - Enhanced command handlers:
    - `/get <query>`: Direct shadow library acquisition.
    - `/fetch <book_id>`: Resolves and acquires local catalog entries lacking CIDs.
    - `/sources`: Real-time shadow library status, last success, and last error.
    - `/acquire`: Lists active and pending user acquisitions.
    - `/rebuild`: Rebuilds SQLite FTS5 search index on demand.
  - Made `acquirer` optional across all handlers for resilient degradation and isolated testability.
- `utils.py`:
  - Added `get_all(user_id, token)` method to `CandidateCache` for token-based candidate retrieval.
- `tests/test_importer.py`:
  - Comprehensive unit test suite covering CSV, JSON, JSONL ingestion, invalid title rejection, duplicate prevention, CID backfilling, and CLI parsing.
- `tests/test_bot.py`:
  - Added 6 new unit tests validating `/get`, `/fetch`, `/sources`, `/acquire`, `/rebuild`, auto-acquisition on local miss, and web search callback flows.

## Verification

- Ran `scripts/verify.ps1`:
  - Git available: OK
  - No tracked secrets: OK
  - Python tests: OK (102 passed, 0 failed in 38.89s).
- Verified `importer.py` CLI dry-run and execution via unit tests.
- Verified bot command handlers, callback routing, and queue interaction across all test cases.

## Decisions

- **Streaming Batch Ingestion**: Bulk imports process records in batches with transaction management and duplicate skipping, preventing out-of-memory errors on large dump files.
- **Fail-Safe Acquirer Wiring**: Telegram bot handlers gracefully check for `acquirer` availability so that tests or local deployments without active acquisition workers remain functional.
