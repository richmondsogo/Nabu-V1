# Project context

This document provides the authoritative domain and architectural knowledge for Nabu-V1. It aligns with architectural decision records in `docs/adr/`.

---

## Product overview

Nabu is a private, self-hosted Telegram bot that functions as a high-throughput, zero-storage **link resolver** for book discovery. When you search for a book, Nabu queries a local SQLite full-text index (`FTS5`) and search cache, falls back to upstream shadow library mirrors (Library Genesis dual-fork clusters) using bounded concurrency, and returns direct multi-mirror browser download links built from the book's MD5 hash.

Nabu never downloads, buffers, or stores book binaries on the host server.

---

## Target users

Nabu is designed for a private whitelist of authorized users. You configure allowed users by listing their numeric Telegram user IDs in the `TELEGRAM_ALLOWED_USER_IDS` environment variable. Any request from an unauthorized user is rejected immediately with no data leakage.

---

## Core problem and approach

Direct browser access to shadow libraries often encounters ISP-level domain blocks, aggressive redirects, and intermittent database connection saturation. Conversely, traditional file-delivery bots encounter Telegram's 50 MB document upload limit, high server egress bandwidth costs, disk exhaustion risks, and legal exposure from redistributing copyright binaries.

Nabu solves this by operating purely as a **link resolver**:

1. **Search path:**
   - Check `search_cache` table for fresh cached query results (24-hour default TTL).
   - If missing or stale, query the local SQLite catalog using full-text search (`FTS5`).
   - If local hits fall below the threshold (`LOCAL_RESULT_THRESHOLD=3`), execute a single upstream scrape via `SingleFlight` across active, healthy mirrors.
   - Upsert discovered metadata and MD5 hashes into the catalog, update the search cache, and serve results.
2. **Delivery path:**
   - When you select a book result, Nabu constructs multi-mirror browser download URLs directly from the stored MD5 hash.
   - Nabu delivers these links in a structured HTML message with **zero outbound network calls**. You tap the link to download the file directly in your browser.

---

## Domain terminology

The following terms describe the core components of the link resolver architecture:

| Term | Definition |
|---|---|
| **Link resolver** | A system that indexes metadata and delivers direct upstream URLs without handling file binaries. |
| **MD5 digest** | A 32-character hexadecimal hash that uniquely identifies an edition across Library Genesis databases. |
| **`li-fork`** | Mirror cluster based on the `libgen.li` backend (`libgen.li`, `libgen.la`, `libgen.bz`). Uses `/index.php` for search and `/ads.php?md5=<md5>` for download pages. |
| **`is-fork`** | Mirror cluster based on the `libgen.is` backend (`libgen.is`, `libgen.rs`, `libgen.st`). Uses `/search.php` for search and `/book/index.php?md5=<md5>` for download pages. |
| **`SingleFlight`** | A concurrency synchronization primitive that ensures identical concurrent search queries share a single upstream HTTP request. |
| **`HostRateLimiter`** | An in-memory rate limiter enforcing per-host request serialization and polite delays (`POLITE_DELAY_MS=750`). |
| **`UserRateLimiter`** | A token-bucket rate limiter (5 tokens, 0.5 refill/sec) applied per user on upstream search requests. |
| **`search_cache`** | A SQLite table mapping normalized queries to ordered lists of book IDs with expiration timestamps. |
| **`mirrors` table** | A SQLite table tracking upstream mirror health, response latency, fail counts, and exponential cooldowns. |

---

## System actors

- **Authorized Telegram user:** Submits search queries, browses inline button results, and retrieves multi-mirror download links.
- **Telegram bot application (`src/bot.py`):** Handles incoming messages and callbacks, verifies user authorization, and renders HTML responses.
- **Search service (`src/search.py`):** Coordinates search caching, local full-text search, and upstream single-flight requests.
- **Mirror manager (`src/sources/mirror_manager.py`):** Manages mirror health probes, latency records, and exponential backoff cooldowns (30s to 600s).
- **Upstream mirror clusters:** Remote web endpoints providing book search results and client-side key-generation download pages.

---

## Constraints

- **Python runtime:** Python 3.12 or later running in a virtual environment (`.venv`).
- **Project layout:** All application source code resides under `src/`.
- **Zero file persistence:** The server must not write book content bytes or temporary `.part` files to disk.
- **Outbound concurrency:** Bound outbound HTTP requests with `MAX_UPSTREAM=4` and enforce a polite delay between requests to the same host.
- **Upstream timeouts:** Individual socket connection attempts must not exceed 8 seconds (`CONNECT_TIMEOUT=8.0`).
- **Authorization gating:** Every command and callback handler must verify the user ID against `TELEGRAM_ALLOWED_USER_IDS` before processing.

---

## Non-goals

- Downloading, proxying, or hosting book files on the server.
- Operating as a public or open bot without whitelist authorization.
- Running heavy web frameworks, REST APIs, or background task queues (such as Celery or Redis).
- Automating browser interactions with headless drivers (such as Playwright or Selenium).

---

## Key architecture decisions

- [ADR 0001: Initial Architecture (Superseded)](docs/adr/0001-architecture.md) — Documented the legacy file-delivery and IPFS pinning design.
- [ADR 0002: Transition to Zero-Storage Link Resolver Architecture](docs/adr/0002-link-resolver-architecture.md) — Accepted architectural pivot eliminating server file storage, demoting IPFS, and instituting cache-first link resolution.
