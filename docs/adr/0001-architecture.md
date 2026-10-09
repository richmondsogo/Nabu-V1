# ADR 0001: Shadow Library Ingestion, Local IPFS Pinning, and Dual-Queue Bot Architecture

## Status

Accepted

## Context

The system is a private, self-hosted Telegram bot providing book search and delivery. Earlier design iterations conceptualized the bot as a local-first catalog with optional external discovery. However, local storage is not the primary book source: shadow libraries (Anna's Archive and Libgen) constitute the authoritative library of record.

Key forces and constraints:
- **Private access & small scale:** Whitelisted Telegram users only; predictable low-concurrency usage without distributed infrastructure overhead.
- **Resource discipline:** No external database servers, no Redis, no Celery, no ORM, no web framework, and no headless browser.
- **Responsive user experience:** Fast local cache hits must return sub-second and must never be starved or blocked by slow, variable-latency remote scrapes.
- **Content delivery & durability:** Retrieved book payloads are pinned into a local IPFS/Kubo node (API on port 5001) for repeatable, decentralized, and deduplicated local delivery.
- **Telegram payload limits:** Telegram Bot API enforces a 50 MB (52,428,800 bytes) hard upload limit on bot document delivery. Callback payloads are strictly limited to 64 bytes.

## Decision

We chose a local-cache, shadow-library-primary architecture with the following concrete decisions:

1. **Source Primacy and Local Cache Role:**
   - External sources (Anna's Archive followed by Libgen) are the primary book library.
   - The local SQLite database (`data/books.db`) serves exclusively as a fast metadata cache and audit index.
   - The local Kubo node (`http://127.0.0.1:5001`) stores and pins the actual book files.

2. **CID-First Resolution:**
   - Anna's Archive is queried first. If an IPFS CID is discovered via metadata or detail pages, retrieval routes directly through the local Kubo node (`/api/v0/cat?arg=<CID>`), skipping HTTP file downloading entirely.
   - If only an HTTP URL is available (e.g., from Libgen or Anna's direct download), the file is streamed to a temporary file, validated by magic bytes, and ingested into Kubo via `POST /api/v0/add?cid-version=1&raw-leaves=true&pin=true`.

3. **Two Isolated Queue Worker Pools:**
   - **Delivery Queue:** Backed by an in-memory `asyncio.Queue[DownloadJob]` and exactly 3 long-lived worker tasks. Serves cached/pinned books to Telegram.
   - **Acquisition Queue:** Backed by an isolated `asyncio.Queue[AcquisitionJob]` with a pool of 2 worker tasks (`MAX_ACQUIRE_JOBS=2`).
   - Isolation guarantee: A slow remote scrape or multi-minute HTTP download can never saturate or block the delivery worker pool.

4. **Queue Position and Worker State:**
   - FIFO position is calculated deterministically via pending job tracking: `ahead = pending.index(job_id); free = max(0, max_workers - active_workers); position = max(1, ahead + 1 - free)`. It does not rely on `queue.qsize()`.

5. **Acquisition Concurrency Rules & Fan-Out:**
   - **One acquisition per user:** Enforced at `enqueue()` via active user tracking (`_user_active`). Additional requests from the same user are placed into a per-user waitlist (`_user_wait`) rather than occupying concurrent workers.
   - **Global MD5 Deduplication with Fan-Out:** Multiple users requesting the same in-flight book MD5 subscribe to a shared job (`by_md5[md5].append(job)`). On completion, the acquired book is delivered to all subscribers' chats.

6. **Search UX & Auto-Acquisition Threshold:**
   - When local cache returns 1 to 4 results, hits are delivered immediately with a `[🌐 More results from Anna's Archive and Libgen]` inline button. No automatic scrape occurs.
   - Automatic scraping triggers only when local search yields 0 hits, or all local hits have no CID (`cid IS NULL`).

7. **Candidate Cache for Browsing:**
   - Search results from external sources are cached in-memory with a short TTL (10 minutes) keyed by `(user_id, token)` instead of writing unverified rows into SQLite. Inline callbacks use the compact format `web:<token>:<index>`, guaranteeing payloads stay well below Telegram's 64-byte limit.

8. **SQLite Schema with AUTOINCREMENT (Intentional Deviation):**
   - The `books` table uses `id INTEGER PRIMARY KEY AUTOINCREMENT`. This prevents SQLite rowid recycling upon record deletions, ensuring stale callback tokens (`book:<id>`) cannot resolve to recycled book records.
   - Full-text search uses SQLite FTS5 with external content triggers (`books_fts_ai`, `books_fts_ad`, `books_fts_au`) and weighted bm25 ranking with exact-title boosting.

9. **Graceful Kubo Degradation:**
   - If the local Kubo daemon (port 5001) is offline on startup, the bot logs a clear warning and boots anyway. `/status` reports `Kubo: OFFLINE`. Download requests fail gracefully with user-friendly error messages rather than crashing the process.

10. **Runtime Dependencies & Environment:**
    - Python is pinned to 3.12 using `uv`.
    - Dependencies are restricted to `python-telegram-bot`, `httpx`, and `python-dotenv`.
    - No ORM, no Redis, no Celery. Tests use `pytest`, `pytest-asyncio`, and built-in `httpx.MockTransport` (no `respx`).
    - `curl_cffi` is deferred until specific mirror anti-bot protections demonstrably require TLS fingerprinting.

## Alternatives Considered

- **Redis + Celery:** Standard for background tasks, but introduces external infrastructure, network complexity, and operational overhead unsuitable for a self-hosted single-node bot.
- **SQLAlchemy / SQLModel ORM:** Adds abstraction layers over SQLite with negligible benefit for a 3-table schema, obscuring FTS5 triggers and WAL pragmas.
- **Headless Browser (Playwright/Selenium) for Scrapers:** Substantially increases resource usage and deployment friction. Pure HTTP with mirror rotation and backoff handles both sources reliably.
- **Parallel Scrapes across all sources:** Querying Anna's Archive and Libgen concurrently would double network footprint and risk immediate rate limiting. Sequential priority order is more polite and resilient.

## Trade-offs

- In-memory queues mean pending downloads and acquisitions are lost if the process restarts. This is acceptable for a personal/private bot where users can simply re-tap a book.
- Storing book files in IPFS requires a running Kubo daemon. If the daemon is stopped, file delivery is temporarily unavailable until restarted.
- Deferring `curl_cffi` means mirrors behind aggressive Cloudflare Turnstile challenges may trigger mirror rotation to alternative domains.

## Consequences

- The codebase remains compact, auditable, and maintainable without containerized external services.
- The distinction between cache and source-of-truth is enforced across all layers: database, queue, and handler.
- Local delivery remains sub-second regardless of external network conditions.
