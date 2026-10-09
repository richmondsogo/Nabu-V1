# Step 14: Search and Upstream Reliability Hardening

## Objective

Harden upstream search reliability, eliminate premature timeout cancellations, preserve partial search results across pages, expand category coverage to comics and fiction, reject upstream edge/challenge pages, and add global error handling to Telegram bot updates.

---

## Scope & Implementation

1. **Partial Result Preservation & Per-Mirror Scrape Resilience (`src/search.py`)**:
   - In `_scrape()`, when page 1 yields valid hits but subsequent pages encounter read timeouts or HTTP 502 Bad Gateway responses, the parser now preserves and returns the partial results rather than discarding the entire query and failing all mirrors.
   - Guarded per-mirror socket connection with a 4.0s connect timeout bound and structured exception logging.

2. **Persistence Inside SingleFlight (`src/search.py`)**:
   - Introduced `_execute_scrape_and_persist(query)`: database upserting and search cache writes now execute *inside* the background single-flight worker.
   - If an initial client call times out or cancels, the shared worker still completes in the background, upserting all discovered books into SQLite and populating `search_cache` for subsequent searches or refresh requests.

3. **Multi-Topic Query Coverage (`src/sources/libgen.py`)**:
   - Updated `LiForkParser.search_url` from hardcoded non-fiction only (`topics[]=l`) to query all standard book categories: scientific/non-fiction (`topics[]=l`), comics (`topics[]=c`), and fiction (`topics[]=f`).
   - Resolves issues where comic issues (such as *Immortal Hulk*) and fiction titles returned 0 results.

4. **Challenge & Nginx Default Page Rejection (`src/sources/base.py`, `src/sources/libgen.py`)**:
   - Introduced `UpstreamInvalidResponseError` in `sources.base`.
   - Both `LiForkParser` and `IsForkParser` inspect incoming HTML for error and challenge signatures (e.g. `Welcome to nginx!`, Cloudflare challenges, DDoS-Guard).
   - Upstream edge errors raise `UpstreamInvalidResponseError` and trigger mirror failover rather than returning 0 hits and poisoning the 5-minute empty result cache.

5. **MD5 Extraction Prioritization (`src/sources/libgen.py`)**:
   - `LiForkParser.parse` now searches for genuine `ads.php?md5=` or `get.php?md5=` parameters first, preventing arbitrary 32-hex hashes in titles or text from hijacking download links.

6. **Observability & Error Representation (`src/search.py`, `src/sources/mirror_manager.py`)**:
   - Formatted all mirror and scraper catch blocks with `f"{type(exc).__name__}('{exc}')" if str(exc) else repr(exc)`.
   - Resolves empty string log outputs caused by Python's `TimeoutError` string formatting.

7. **Bot Global Error Handler & Callback Protection (`src/bot.py`)**:
   - Added `error_handler()` registered via `app.add_error_handler` to catch unhandled Telegram update exceptions and notify users gracefully.
   - Enforced type and integer validation on callback payloads (`action == "book"`, `action == "page"`).
   - Wrapped `resolve_direct_link()` with a 6-second timeout bound to fall back to landing-page links without stalling Telegram callback queries.

8. **Configuration Headroom (`src/config.py`)**:
   - Increased default `singleflight_timeout` from 15.0s to 25.0s in `Config` and `load_config` to accommodate multi-page and failover scrapes under congested network conditions.

---

## Discoveries

- **LibGen Topic Filtering**: The `topics[]` parameter on LibGen li-fork is strict: `topics[]=l` exclusively queries the scientific non-fiction database. Comics and fiction are indexed under `topics[]=c` and `topics[]=f`. Without passing these topic codes, comic books return 0 hits regardless of title matching.
- **LibGen Fallback Static Page**: Without a standard browser `User-Agent` or under certain edge errors, LibGen's nginx server returns the default `Welcome to nginx!` page with HTTP status 200 (639 bytes). Treating this as a successful 0-hit search causes false empty results to be cached for 5 minutes.
- **Python TimeoutError String Representation**: `str(TimeoutError())` returns an empty string `""`. Logging `%s` for exceptions without `repr()` or `type()` erased error details from the log stream.

---

## Verification

- **Automated Tests (`uv run pytest`)**:
  - All 81 tests passed, 0 failures.
  - Added tests:
    - `test_partial_results_preserved_when_later_page_fails`
    - `test_challenge_response_does_not_poison_empty_cache`
    - `test_relaxed_query_does_not_consume_second_token`
    - `test_challenge_and_nginx_detection_raises_upstream_invalid`
    - `test_search_url_includes_comics_fiction_and_libgen_topics`
    - `test_li_parser_prioritizes_ads_md5_over_arbitrary_hash`
    - `test_error_handler_notifies_user_on_message`
    - `test_error_handler_notifies_user_on_callback`
    - `test_callback_malformed_arguments_handled_gracefully`
- **Verification Script (`scripts/verify.ps1`)**:
  - Run verification suite: 3/3 checks passed (Git available, no tracked secrets, pytest passed).
- **Git Diff**:
  - Reviewed clean diff across `src/bot.py`, `src/config.py`, `src/search.py`, `src/sources/base.py`, `src/sources/libgen.py`, `src/sources/mirror_manager.py`, and test files.
