# Nabu-V1

A private, self-hosted Telegram bot that acts as a high-throughput, zero-storage **Link Resolver** for books. The server searches upstream mirrors, caches metadata in a local SQLite catalog, and delivers direct multi-mirror download links to the user. The server never downloads or stores book binaries on disk.

---

## Architecture Overview

- **Model:** Zero file hosting, zero temp files, zero redistribution. Pure link resolution.
- **Search Path:** Search cache -> Local SQLite FTS5 -> Single-flight upstream scrape (`libgen.li`, `libgen.la`, `libgen.bz`, `libgen.is`, `libgen.rs`).
- **Delivery Path:** Instant multi-mirror browser links constructed directly from MD5 hashes with zero network calls.
- **Concurrency & Hygiene:** Deduplicated with `SingleFlight`, bounded with `MAX_UPSTREAM=4` semaphore, per-host polite delays (750ms), and per-user token bucket rate limiting (5 req / 10s).
- **Architecture Record:** See [`docs/adr/0002-link-resolver-architecture.md`](./docs/adr/0002-link-resolver-architecture.md).

---

## Prerequisites

- **Python:** Python 3.12+
- **Package & Environment Manager:** `uv` (`py -3.12 -m uv` or native `uv`).
- **No external daemon required:** No Kubo/IPFS daemon or Docker container needed.

---

## Setup & Environment

1. **Create and activate the virtual environment:**
   ```powershell
   uv venv --python 3.12 .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. **Install dependencies:**
   ```powershell
   uv pip install -r requirements-dev.txt
   ```

3. **Configure environment variables:**
   ```powershell
   Copy-Item .env.example .env
   # Edit .env to set TELEGRAM_TOKEN and TELEGRAM_ALLOWED_USER_IDS
   ```

4. **Run test suite & verify:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
   ```

5. **Start the bot:**
   ```powershell
   python bot.py
   ```

---

## Bot Commands

- `/start` / `/help` — Overview of bot search syntax and usage instructions.
- `/status` — System health, catalog item counts, cached search volume, and hit/miss metrics.
- `/mirrors` — Real-time upstream mirror status, measured latencies, and cooldown timers.
- `/rebuild` — Rebuilds SQLite FTS5 search index on demand from catalog records.

---

## Catalog Import CLI

Seed or backfill the local SQLite catalog from existing CSV, JSON, or JSONL dumps:

```powershell
python importer.py path/to/dump.csv --db data/books.db
```

Supported formats: `.csv`, `.json`, `.jsonl`. Duplicates are skipped automatically based on MD5, and FTS5 search indexes are synced after import.

---

## Production Deployment & Clean Shutdown

- **Graceful Lifecycle:** The bot handles `SIGINT` (`Ctrl+C`) and `SIGTERM` via `ApplicationBuilder.post_shutdown()`, cancelling all in-flight probers and single-flight tasks cleanly with zero orphaned tasks.
- **Service Running:** Run as a systemd service on Linux or background process via Task Scheduler on Windows. Logs are emitted via standard Python logging.
