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
