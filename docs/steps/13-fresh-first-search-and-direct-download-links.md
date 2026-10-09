# Step 13: Fresh-First Search Orchestration and Direct Download Links

## Objective

Deliver fresh-first search orchestration (never showing local catalogue results on new queries unless upstream mirrors fail), 12-hour verbatim repeat cache TTL, direct one-click download link resolution (`https://libgen.li/get.php?md5=...&key=...`) with legacy landing-page fallback, httpx/httpcore logging security silence, and user-facing FAQ / temporary download link notices.

## Scope & Implementation

1. **Fresh-First Search Orchestration (`src/search.py`)**:
   - New search queries query upstream sources directly via `SingleFlight` and polite rate limiters to guarantee maximum freshness.
   - The local catalogue is never merged into successful fresh upstream search results.
   - Newly scraped books are upserted into the SQLite database for record preservation.
   - **Degraded Fallback:** When all upstream mirrors are unreachable or time out, `SearchService` falls back to stale cache entries, then to the local catalogue FTS, clearly flagging results with `degraded=True`.

2. **12-Hour Search Cache TTL (`src/config.py`, `README.md`)**:
   - Updated default `SEARCH_CACHE_TTL` from 24h (`86400.0s`) to 12h (`43200.0s`) in `Config` and `load_config`.
   - Empty search queries retain the short 5-minute TTL (`EMPTY_RESULT_CACHE_TTL=300.0s`).

3. **Direct One-Click Download Link Extraction & Fallback (`src/sources/libgen.py`, `src/sources/mirror_manager.py`, `src/bot.py`)**:
   - `extract_get_link`: Regex extractor extracting temporary `get.php?md5=<md5>&key=<key>` direct links from `li-fork` `ads.php` pages.
   - `resolve_direct_download_link`: Asynchronously fetches `ads.php` for the requested MD5 on the mirror and parses the temporary session key.
   - `MirrorManager.resolve_direct_link`: Tries the healthiest active `li` mirror with connection timeouts.
   - `src/bot.py`: When tapping a book button (`action == "book"`), resolves the direct link and formats:
     - Direct Download link: `⚡ Direct Download (One-Click)` plus backup mirror links.
     - Fallback notice on extraction failure: `<i>⚠️ Direct download link unavailable; using landing page links:</i>` with legacy `ads.php` and `book/index.php` links.
     - Prominent UI notice: *"Link is temporary. Tap the book again for a fresh one."*

4. **Logging Security (`src/bot.py`)**:
   - Silenced `httpx` and `httpcore` loggers to `logging.WARNING` at module level and inside `main()`.
   - Prevents Telegram bot tokens, request headers, and upstream search parameters from leaking into server stdout or log files.

5. **User Communication & FAQ (`src/bot.py`, `README.md`)**:
   - Registered `/faq` command handler in addition to `/help` and `/start`.
   - Added user-facing FAQ section explaining that direct download keys are temporary (valid for a few minutes) and how tapping the book card again regenerates a fresh key.
   - Clearly documents degraded fallback behavior when upstream mirrors encounter outages.

## Verification

- **Automated Tests (`uv run pytest`)**:
  - 72 tests passed, 0 failures.
  - Covers parser extraction of `get.php` links, fallback behavior, direct link callbacks, FAQ handlers, 12h cache TTL configuration, and logging security level.
- **Verification Script (`scripts/verify.ps1`)**:
  - Run automated verification suite to validate test suite and secrets check.
