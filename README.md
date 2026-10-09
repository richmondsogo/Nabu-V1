# Nabu-V1

A private, self-hosted Telegram bot for searching and delivering books from Anna's Archive and Libgen, cached into a local SQLite metadata database and pinned into a local IPFS/Kubo node.

---

## Architecture Overview

- **Primary Source Layer:** Anna's Archive and Library Genesis.
- **Local Cache & Index:** SQLite with WAL mode and FTS5 full-text search (`data/books.db`).
- **File Storage & Pinning:** Local IPFS Kubo RPC daemon (`http://127.0.0.1:5001`).
- **Delivery Queue:** In-memory queue with exactly 3 persistent workers delivering local/pinned files.
- **Acquisition Queue:** Isolated in-memory queue with 2 persistent workers handling remote search and downloads.
- **Architecture Record:** See [`docs/adr/0001-architecture.md`](./docs/adr/0001-architecture.md).

---

## Prerequisites

- **Python:** Pinned to **Python 3.12** (do not use 3.14+).
- **Package & Environment Manager:** `uv` (`py -3.12 -m uv` or native `uv`).
- **IPFS / Kubo:** Local Kubo RPC daemon running on `http://127.0.0.1:5001`.

---

## Setup & Environment

1. **Create and activate the Python 3.12 virtual environment:**
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

4. **Verify environment and run test suite:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
   ```
