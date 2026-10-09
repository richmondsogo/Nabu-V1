# ADR 0004: Fresh-First Search Orchestration and Direct Download Link Resolution

## Status

Accepted

## Context

Following the implementation of ADR 0003, two key usability and user experience needs emerged:
1. **Catalog Freshness vs. Unrelated Local Hits:** When users perform a search, merging existing local catalogue items behind upstream results can dilute results with outdated or unrelated records. Fresh searches should query upstream sources to maximize coverage and freshness, reserving the local catalogue and stale cache strictly for degraded fallback when upstream mirrors are unreachable.
2. **Download Retention & Navigation Friction:** In the legacy link resolver, users received links pointing to mirror landing pages (`ads.php`), forcing them to navigate external pages and locate the "GET" download button manually. Direct one-click download URLs (`get.php?md5=...&key=...`) improve user retention and satisfaction.
3. **Download Key Ephemerality:** Libgen's direct download keys are temporary tokens issued per request, valid for only a few minutes. Users need clear instruction on how to handle expired download sessions.
4. **Logging Security & Credential Hygiene:** Telegram bot API and upstream HTTP interactions through `httpx`/`httpcore` could log sensitive request URLs and query parameters, risking credential leakage if not silenced.

## Decision

1. **Fresh-First Search Orchestration:**
   - Verbatim repeat queries are served from the search cache if within TTL.
   - For all new searches, Nabu bypasses the local catalogue and directly queries upstream sources through `SingleFlight` and polite rate limiters to guarantee freshness.
   - Newly discovered book records are upserted into the local database for offline persistence.
   - The local catalogue is never merged into successful fresh upstream results.
   - **Degraded Fallback:** If all upstream mirrors fail or time out, Nabu falls back first to stale cache entries, and then to local full-text search (FTS), clearly labeling results as degraded (`offline catalogue (degraded)` / `stale cache (degraded)`).

2. **Verbatim Search Cache TTL:**
   - The search cache TTL (`SEARCH_CACHE_TTL`) for successful queries is set to 12 hours (`43200.0s`), balancing mirror friendliness with result freshness.
   - Empty queries continue to use a short 5-minute TTL (`EMPTY_RESULT_CACHE_TTL=300.0s`).

3. **Direct One-Click Download Link Resolution:**
   - When a user taps a book card, Nabu asynchronously requests the book's landing page on the healthiest active `li`-fork mirror.
   - The server extracts the temporary server-rendered direct download link matching the pattern `https://libgen.li/get.php?md5=<md5>&key=<key>`.
   - **Graceful Landing-Page Fallback:** If GET extraction fails (e.g. HTTP 500, timeout, or missing key), Nabu falls back to the legacy `ads.php` landing-page link with a brief notice.
   - Book cards clearly notify users: *"Link is temporary. Tap the book again for a fresh one."*

4. **Logging Security:**
   - `httpx` and `httpcore` loggers are silenced to `logging.WARNING` across the application, preventing Telegram bot tokens, request headers, and query parameters from leaking into logs.

5. **User Communication & FAQ:**
   - Both `/help` and `/faq` commands provide a dedicated FAQ section explaining that direct download keys are temporary and that tapping the book card again regenerates a fresh link.
   - Documentation in `README.md` is updated accordingly.

## Trade-offs & Consequences

- Resolving direct download links adds one bounded HTTP GET request upon tapping a book card. This is mitigated by `MirrorManager` targeting the lowest-latency active mirror with strict connect timeouts.
- If upstream mirrors are offline, the user is seamlessly provided with offline catalogue hits or landing-page fallback links with explicit transparency notices.
