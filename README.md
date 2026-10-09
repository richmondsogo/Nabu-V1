# Nabu

Nabu is a private, self-hosted Telegram bot that provides fast, zero-storage book discovery and link resolution. When you query Nabu, the bot checks a local SQLite full-text index (`FTS5`) and search cache before performing a single-flight scrape against Library Genesis upstream mirrors. When you select a title, Nabu constructs direct download links for multiple independent mirrors from the book's MD5 hash and returns them instantly with zero server network overhead.

Nabu never downloads, buffers, or stores book binaries on the host server.

---

## Features

- **Zero server storage:** The server stores only book metadata and URLs in a local SQLite database. Files download directly in your browser.
- **Cache-first search:** Repeat queries resolve in less than 1 millisecond directly from the local search cache and full-text index.
- **Single-flight concurrency:** Duplicate concurrent search requests share a single upstream request, preventing duplicate scraping.
- **Multi-mirror redundancy:** Every book delivers up to three distinct download links (`libgen.li`, `libgen.la`, and `libgen.is`) to bypass regional or ISP-level DNS blocks.
- **Polite upstream scraping:** Outbound requests are governed by a global concurrency semaphore (`MAX_UPSTREAM=4`) and a per-host polite delay (`POLITE_DELAY_MS=750`).
- **Private access control:** Strict user whitelist verification rejects unauthorized users on every command and callback with zero information leakage.
- **Bulk catalog ingestion:** Includes a dedicated CLI to seed and backfill your local catalog from CSV, JSON, or JSONL dumps.

---

## System architecture

Nabu separates discovery from file delivery:

```
[Telegram User]
       │
       ▼
 1. Send query
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ src/bot.py (Authorization & Telegram Handlers)              │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ src/search.py (SearchService)                               │
│                                                             │
│  Step A: Check search_cache table (TTL: 24h)                │
│          ├── HIT: Return cached book IDs (0 network calls)  │
│          └── MISS: Query local books_fts table              │
│                                                             │
│  Step B: Evaluate local FTS hit count                       │
│          ├── >= 3 hits: Serve local catalog results         │
│          └── < 3 hits: Trigger SingleFlight scrape          │
│                                                             │
│  Step C: Upstream SingleFlight Scrape                       │
│          ├── Acquire HostRateLimiter (750ms delay)          │
│          ├── Acquire GlobalSemaphore (max 4 concurrent)     │
│          ├── Query healthiest mirror (libgen.li / .is)      │
│          ├── Parse HTML table into SearchHit records        │
│          ├── Upsert metadata & MD5 into SQLite books table  │
│          └── Cache query IDs into search_cache              │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
 2. Display search results as inline keyboard buttons
                               │
                               ▼
[User taps a book button]
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Callback Handler (book:<id>)                                │
│                                                             │
│  1. Acknowledge callback immediately (clears spinner)       │
│  2. Fetch book row from SQLite catalog by primary key       │
│  3. Call build_links(md5) -> 3 mirror download URLs         │
│  4. Render HTML message with download links                 │
│                                                             │
│  ZERO SERVER NETWORK CALLS ON SELECTION                     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
 3. User taps mirror link -> Downloads file in browser
```

### Upstream mirror failure domains

When building download links, Nabu selects up to three distinct mirror endpoints:

1. **Primary `li-fork` mirror:** `https://libgen.li/ads.php?md5={md5}`
2. **Secondary `li-fork` mirror:** `https://libgen.la/ads.php?md5={md5}`
3. **Primary `is-fork` mirror:** `https://libgen.is/book/index.php?md5={md5}`

Because `.li` and `.bz` share an infrastructure operator, `.la` is fronted by Cloudflare, and `.is`/`.rs` run on an independent backend, these links provide resilient alternative paths if an individual mirror encounters downtime or ISP-level restrictions.

---

## Directory layout

The repository uses a standard `src/` layout:

```
Nabu-V1/
├── pyproject.toml              # Project metadata & pytest configuration
├── requirements.txt            # Production dependencies
├── requirements-dev.txt        # Development and testing dependencies
├── .env.example                # Example environment variable template
├── CONTEXT.md                  # Project context and domain definitions
├── README.md                   # Technical documentation and guides
├── src/
│   ├── __init__.py             # Nabu root package definition
│   ├── bot.py                  # Telegram bot entrypoint, handlers, & lifecycle
│   ├── concurrency.py          # SingleFlight, HostRateLimiter, & UserRateLimiter
│   ├── config.py               # Environment configuration loader & validation
│   ├── database.py             # SQLite database layer with FTS5 and WAL mode
│   ├── importer.py             # Bulk catalog importer CLI
│   ├── models.py               # Plain dataclasses (Book, SearchHit, Mirror)
│   ├── search.py               # Search orchestration and fallback logic
│   ├── utils.py                # Link builder, HTML escaper, & callback encoder
│   └── sources/
│       ├── __init__.py         # Sources package exports
│       ├── base.py             # SourceParser protocol definition
│       ├── libgen.py           # Dual-fork (li / is) HTML table parsers
│       └── mirror_manager.py   # Mirror health probing & cooldown tracking
├── docs/
│   ├── GOAL.md                 # Project requirements and history
│   ├── adr/
│   │   ├── 0001-architecture.md
│   │   └── 0002-link-resolver-architecture.md
│   └── steps/                  # Chronological engineering logs
├── scripts/
│   ├── bootstrap.ps1           # Environment bootstrap script
│   ├── check-env.ps1           # Environment diagnostic script
│   ├── probe-links.ps1         # Live mirror connectivity tester (PowerShell)
│   ├── probe-links.py          # Live mirror connectivity tester (Python)
│   └── verify.ps1              # Project verification script
└── tests/
    ├── fixtures/               # Recorded HTML search fixtures
    ├── test_bot.py             # Bot handler and callback unit tests
    ├── test_concurrency.py     # Concurrency and rate limiting tests
    ├── test_config.py          # Configuration validation tests
    ├── test_database.py        # SQLite schema, FTS, and cache tests
    ├── test_importer.py        # Catalog importer CLI tests
    ├── test_libgen_parsers.py  # Dual-fork HTML parser tests
    ├── test_search.py          # Search service orchestration tests
    └── test_utils.py           # Utility function tests
```

---

## Prerequisites

Before running Nabu, ensure your system meets the following requirements:

- **Python:** Python 3.12 or later.
- **Package manager:** `uv` (recommended) or standard `pip`.
- **Telegram Bot Token:** Obtained from [@BotFather](https://t.me/BotFather).
- **Telegram User ID:** Your numeric Telegram user ID (obtained via [@userinfobot](https://t.me/userinfobot)).

---

## Getting started

Follow these steps to set up and run Nabu locally.

### 1. Clone the repository and create a virtual environment

Using `uv`:

```powershell
# Windows PowerShell
uv venv --python 3.12 .venv
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
uv venv --python 3.12 .venv
source .venv/bin/activate
```

### 2. Install dependencies

```powershell
uv pip install -r requirements-dev.txt
```

### 3. Configure environment variables

Copy the example configuration file:

```powershell
# Windows PowerShell
Copy-Item .env.example .env

# Linux / macOS
cp .env.example .env
```

Open `.env` in your text editor and set your credentials:

```dotenv
TELEGRAM_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
DB_PATH=data/books.db
```

### 4. Verify the installation

Run the automated verification suite:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify.ps1
```

Or run `pytest` directly:

```powershell
pytest
```

### 5. Start the bot

```powershell
python src/bot.py
```

---

## Configuration reference

All configuration settings load from environment variables or a local `.env` file:

| Variable | Type | Default | Description |
|---|---|---|---|
| `TELEGRAM_TOKEN` | string | *Required* | Telegram Bot API token issued by @BotFather. |
| `TELEGRAM_ALLOWED_USER_IDS` | string | *Required* | Comma-separated list of numeric Telegram user IDs permitted to use the bot. |
| `DB_PATH` | path | `data/books.db` | File path for the SQLite database. |
| `LOCAL_RESULT_THRESHOLD` | integer | `3` | Minimum number of local FTS catalog hits required before skipping upstream scraping. |
| `SEARCH_CACHE_TTL` | float | `86400.0` | Cache time-to-live for populated search queries, in seconds (default: 24 hours). |
| `EMPTY_RESULT_CACHE_TTL` | float | `300.0` | Cache time-to-live for empty search queries (0 hits), in seconds (default: 5 minutes). |
| `MAX_UPSTREAM` | integer | `4` | Maximum number of concurrent outbound HTTP requests. |
| `POLITE_DELAY_MS` | integer | `750` | Minimum delay in milliseconds between requests to the same mirror host. |
| `CONNECT_TIMEOUT` | float | `8.0` | Socket connection timeout in seconds for upstream requests. |
| `SINGLEFLIGHT_TIMEOUT` | float | `15.0` | Overall timeout in seconds for in-flight search deduplication tasks. |
| `RESULT_LIMIT` | integer | `50` | Maximum number of search results returned per query. |
| `UPSTREAM_MAX_RESULTS` | integer | `50` | Maximum number of results collected from upstream scrapes. |
| `UPSTREAM_MAX_PAGES` | integer | `3` | Maximum number of pagination pages fetched per upstream query. |
| `PAGE_SIZE` | integer | `8` | Number of results displayed per inline pagination page in Telegram. |
| `USER_BUCKET_TOKENS` | float | `5.0` | Maximum token bucket burst capacity for upstream search requests per user. |
| `USER_BUCKET_REFILL` | float | `0.5` | Refill rate in tokens per second for user rate limiting. |
| `REFRESH_COOLDOWN` | float | `300.0` | Minimum cooldown in seconds between manual result refreshes on the same query. |
| `MIRROR_LIBGEN` | string | See description | Comma-separated list of active Libgen mirror base URLs (defaults to `https://libgen.li,https://libgen.la,https://libgen.bz,https://libgen.is,https://libgen.rs`). |

---

## Telegram command reference

The bot exposes the following commands to authorized users:

### `/start` and `/help`
Displays an overview of search instructions, syntax, and bot capabilities.

### `/status`
Reports operational health and performance statistics:
- Total books indexed in the local SQLite catalog.
- Total active search queries stored in the search cache.
- Count of currently active versus cooling upstream mirrors.
- Hit counters for cache hits, local FTS hits, and upstream scrapes.

### `/mirrors`
Lists all configured mirrors with real-time operational status:
- Operational state: `🟢 Active`, `🟡 Cooling`, or `🔴 Disabled`.
- Fork type (`li` or `is`).
- Measured round-trip response latency in milliseconds.
- Consecutively logged failure counts and active backoff cooldown timers.
- Most recent recorded error message (if any).

### `/rebuild`
Triggers an immediate, synchronous rebuild of the SQLite `FTS5` virtual table index (`books_fts`) from the `books` table. Use this command after manual database modifications or bulk imports.

---

## Bulk catalog importer CLI

Nabu includes a command-line interface (`src/importer.py`) to seed or backfill your local database from external metadata dumps.

### Usage

```powershell
python src/importer.py <file-path> [--db <database-path>] [--map <mapping-file>]
```

### Arguments and options

- `file`: Path to the CSV, JSON, or JSONL catalog file.
- `--db` *(optional)*: Path to the SQLite database file (default: `data/books.db`).
- `--map` *(optional)*: Path to a secondary CSV or JSON file containing MD5-to-CID mappings.

### Supported formats

- **CSV:** Must include a `title` column. Columns such as `author`, `md5`, `extension`, and `filesize` are parsed automatically.
- **JSON:** A JSON array of book objects containing at minimum a `"title"` key.
- **JSONL:** Line-delimited JSON objects.

Duplicates are identified by MD5 hash and skipped automatically without aborting the import. After ingestion finishes, the importer rebuilds the FTS5 search index automatically.

---

## Production deployment

### Running as a systemd service (Linux)

To run Nabu continuously as a system service on Linux:

1. Create a service unit file at `/etc/systemd/system/nabu.service`:

   ```ini
   [Unit]
   Description=Nabu Telegram Link Resolver Bot
   After=network.target

   [Service]
   Type=simple
   User=nabu
   WorkingDirectory=/opt/nabu
   ExecStart=/opt/nabu/.venv/bin/python src/bot.py
   Restart=always
   RestartSec=5
   EnvironmentFile=/opt/nabu/.env

   [Install]
   WantedBy=multi-user.target
   ```

2. Reload systemd, enable, and start the service:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable nabu
   sudo systemctl start nabu
   ```

3. View live service logs:

   ```bash
   journalctl -u nabu -f
   ```

### Graceful shutdown

Nabu handles `SIGINT` (`Ctrl+C`) and `SIGTERM` signals using `ApplicationBuilder.post_shutdown()`. When interrupted, the bot cancels in-flight health probes and single-flight tasks cleanly, closes database handles, and exits with code 0 without dangling background threads.

---

## Testing and verification

Nabu includes a test suite covering unit behavior, database migrations, rate limiting, and dual-fork HTML parsing.

Run the test suite with coverage:

```powershell
pytest
```

Run specific test modules:

```powershell
pytest tests/test_concurrency.py
pytest tests/test_search.py
pytest tests/test_bot.py
```

All test cases use `httpx.MockTransport` and local disk fixtures. Tests never make live outbound network requests.
