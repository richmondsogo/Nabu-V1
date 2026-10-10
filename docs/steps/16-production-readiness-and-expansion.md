# Step 16: Production Readiness and Expansion

## Objective

Deliver the four sequential production and feature expansion milestones under the zero-server-storage link resolver architecture:
1. **Background Maintenance & Operational Metrics**: Periodic WAL checkpoints, cache pruning, and enhanced `/status` telemetry.
2. **Search & UI Enhancements**: Format filtering buttons (`[ALL]`, `[EPUB]`, `[PDF]`), modular book card formatter, and Telegram Inline Query mode (`@bot <query>`).
3. **Secondary Upstream Source (Anna's Archive)**: Complete fallback parser (`src/sources/annas.py`) when Libgen mirrors fail or return 0 hits.
4. **Production Deployment & Containerization**: Multi-stage `Dockerfile`, `docker-compose.yml`, and hardened `nabu.service` systemd unit.

---

## Scope & Implementation

1. **Part 1: Background Maintenance & Operational Metrics (`src/config.py`, `src/database.py`, `src/bot.py`)**:
   - Added `maintenance_interval_sec` to `Config` (defaulting to 6 hours / 21,600s).
   - Added `DatabaseStats` dataclass and `Database.get_stats()` providing book counts, cache counts, database file size, and WAL size in bytes.
   - Wired `_maintenance_loop` into `post_init` and `post_shutdown` in `bot.py` to periodically perform passive WAL checkpoints and expired cache pruning.
   - Enhanced `/status` command to report human uptime (`Xh Ym Zs`), database and WAL size, active mirror counts, and traffic metrics (cache hit ratio, upstream scrapes, degraded hits).

2. **Part 2: Search & UI Enhancements (`src/bot.py`, `tests/test_bot.py`)**:
   - Added in-memory format filtering (`_filter_hits`) with interactive buttons: `[• ALL •]`, `[EPUB]`, `[PDF]`. Clicking a filter filters cached search hits in-memory without consuming upstream tokens, updating results inline via `edit_message_text`.
   - Extracted shared `_format_book_card(book, direct_url)` ensuring identical presentation across callback queries and inline results.
   - Implemented Telegram Inline Query mode (`handle_inline_query`) via `InlineQueryHandler` returning up to 20 `InlineQueryResultArticle` cards with zero file persistence.

3. **Part 3: Secondary Upstream Source: Anna's Archive (`src/sources/annas.py`, `src/search.py`, `src/database.py`)**:
   - Implemented `AnnasParser` implementing the `SourceParser` protocol with HTML and regex fallback parsing, challenge detection, title, author, format, file size, and MD5 extraction.
   - Seeded default mirrors with `annas` endpoints (`https://annas-archive.org`, `https://annas-archive.se`).
   - Updated `SearchService._scrape` to query Libgen mirrors first, automatically falling back to Anna's Archive when all Libgen mirrors fail or return 0 hits.

4. **Part 4: Production Deployment & Containerization (`Dockerfile`, `docker-compose.yml`, `nabu.service`, `.dockerignore`)**:
   - Created multi-stage `Dockerfile` based on `python:3.12-slim-bookworm` with `uv`, least-privilege non-root user (`nabu:nabu`, UID 10001), `/app/data` volume mount, and unbuffered logging.
   - Created `docker-compose.yml` with host volume mapping (`./data:/app/data`), environment file loading, and log rotation (`max-size: 10m`, `max-file: 3`).
   - Created `nabu.service` systemd unit with full security sandboxing (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`).

---

## Discoveries & Architectural Decisions

- **In-Memory Format Filtering**: Because `SearchService` stores the full set of upstream results in `search_cache` upon query execution, format filtering requires zero additional upstream requests or network latency. Filtering against `book.file_type` in-memory allows instantaneous UI updates.
- **Zero-Persistence Inline Search**: By generating `InlineQueryResultArticle` objects equipped with `InputTextMessageContent` and HTML-formatted direct/mirror download links, users can share book results in any Telegram chat without server-side file staging.
- **Dual-Source Upstream Resiliency**: Upstream shadow library mirrors occasionally suffer regional Cloudflare blocks or database outages. Adding Anna's Archive as an automated fallback prevents downtime when Libgen mirrors are unreachable or when a book exists only on Anna's Archive.

---

## Verification

- **Automated Test Suite (`uv run pytest`)**:
   - 98 passed in 9.23s across all test modules.
   - Added unit test suites:
     - `test_annas_search_url_construction` (`tests/test_annas.py`)
     - `test_annas_detail_url_construction` (`tests/test_annas.py`)
     - `test_annas_html_parser_success` (`tests/test_annas.py`)
     - `test_annas_regex_fallback` (`tests/test_annas.py`)
     - `test_annas_challenge_detection` (`tests/test_annas.py`)
     - `test_search_annas_fallback_on_libgen_mirrors_failed` (`tests/test_search.py`)
     - `test_search_annas_fallback_on_libgen_zero_hits` (`tests/test_search.py`)
     - `test_callback_format_filter` (`tests/test_bot.py`)
     - `test_handle_inline_query_success` (`tests/test_bot.py`)
     - `test_handle_inline_query_unauthorized` (`tests/test_bot.py`)
- **Project Verification (`scripts\verify.ps1`)**:
   - 3 passed, 0 failed, 0 skipped (Git state, secret scan, pytest).
