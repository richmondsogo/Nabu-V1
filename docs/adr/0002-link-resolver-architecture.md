# ADR 0002: Transition from File Delivery Bot to Zero-Storage Link Resolver

## Status

Accepted

## Context

The original architecture (ADR 0001) implemented Nabu as a file delivery bot: searching upstream mirrors, downloading binary book files into a temporary directory (`tmp/<job>.part`), verifying file integrity via magic-byte sniffing, pinning to a local Kubo IPFS daemon, and uploading files to Telegram users via `send_document`.

During development and Phase 0 probing, this approach ran into critical architectural barriers:
1. **Telegram Limits:** Telegram Bots are hard-capped at 50 MB uploads, excluding textbooks, technical PDFs, and large EPUBs without cumbersome user-facing failures.
2. **IPFS Public Gateway Instability:** Public IPFS gateways (`ipfs.io`, `dweb.link`, `4everland.io`) have moved to service workers, throttled public traffic with 429s, or blocked copyright content with HTTP 451.
3. **Upstream Key Generation & Server Bottlenecks:** Upstream Libgen mirrors (`libgen.li`, `libgen.la`, `libgen.bz`) generate download keys client-side. Attempting to resolve direct download links server-side creates fragile dependencies on upstream MySQL connection pools (which frequently throw `HTTP 500: max_user_connections (80) exceeded`).
4. **Server Egress & Reachability Asymmetry:** The host running the bot may be network-restricted (e.g. TCP blackholing of `libgen.is`), while end users on diverse ISPs can reach mirrors that the server cannot.

Phase 0 verification confirmed:
- `libgen.li`, `libgen.la`, and `libgen.bz` are directly accessible and return `HTTP 200` (~20 KB) on `/ads.php?md5=<md5>`. The page renders anonymously with the direct download button.
- For `is-fork` mirrors (`libgen.is`, `libgen.rs`), the canonical detail path is `/book/index.php?md5=<md5>` (with `/md5/<md5>` rewrite alias).
- Handing links constructed from MD5 delivers permanent, stable download links with zero server resolve requests.

## Decision

Nabu is permanently transitioned to a **Link Resolver**. The server stores metadata and URLs only, never file bytes.

1. **Zero Storage & Zero File I/O:**
   - Remove temporary file storage, chunked download streaming, `.part` files, cleanup sweeps, and `send_document`.
   - Remove local Kubo IPFS daemon management from the bot lifecycle.
2. **Delivery Architecture:**
   - User searches -> checked against `search_cache` and local SQLite FTS catalog.
   - If missing/thin, 1 upstream search scrape is performed and cached.
   - User picks a book -> Nabu constructs multi-mirror download links directly from the MD5 with zero network calls:
     - Primary `li-fork`: `https://libgen.li/ads.php?md5={md5}`
     - Alternate `li-fork`: `https://libgen.la/ads.php?md5={md5}`
     - Alternate `is-fork`: `https://libgen.is/book/index.php?md5={md5}` (or `https://libgen.rs/book/index.php?md5={md5}`)
   - Links are sent in an HTML message formatted with title, author, format, and size.
3. **Bounded Concurrency & Rate Limiting:**
   - `SingleFlight` deduplicates concurrent identical upstream searches with leak-free cleanup (`finally: pop()`) and an overall timeout (`SINGLEFLIGHT_TIMEOUT=15s`).
   - `GlobalSemaphore` (`MAX_UPSTREAM=4`) bounds total outbound HTTP requests.
   - `HostRateLimiter` enforces a polite delay (`POLITE_DELAY_MS=750`) between requests to each individual host.
   - `UserRateLimiter` enforces a token bucket (5 tokens, 0.5 refill/sec) per user on upstream search paths only.
4. **Mirror Health & Resiliency:**
   - `mirrors` table tracks latency, error counts, and exponential cooldowns (30s to 600s) on mirrors.
   - Connect timeout is capped at 8 seconds.
5. **Anna's Archive Scope:**
   - Phase 1 focuses exclusively on Libgen. Anna's Archive dump ingestion and CID mapping are deferred to Phase 3.

## Alternatives Considered

1. **Direct Download Proxying (File Delivery):** Rejected due to 50 MB Telegram limits, server disk I/O, bandwidth costs, and legal exposure of handling copyright binaries.
2. **Server-Side Direct Link Resolve:** Rejected because upstream Libgen detail pages render keys client-side, and server-side scraping of `get.php` keys frequently fails due to upstream database connection pool exhaustion.
3. **Public IPFS Gateways as Primary Delivery:** Rejected due to HTTP 403/429/451 errors across public gateways.

## Trade-offs

- **User Clicks:** Users tap a download link that opens in their browser rather than receiving a file directly inside Telegram. However, this ensures reliability across all devices and file sizes (even multi-gigabyte files).
- **Domain Dependence:** External mirrors may be blocked by specific ISPs. We mitigate this by providing three distinct mirror links per book.

## Consequences

- The bot becomes extremely lightweight, fast, and resilient.
- Server memory and disk requirements drop to near zero (storing only SQLite metadata and search cache).
- No legal or hosting risk associated with redistributing file binaries.
