# Step 12: Search Quality, Result Count, and Result Transparency

## Objective

Resolve search quality, low result count, and lack of transparency in Nabu. Decouple upstream pagination from Telegram display limits, implement relevance ranking and query normalization, record cache completeness with short TTLs for empty results, update catalog records on conflict, and introduce rich result transparency headers and inline pagination.

## Scope & Implementation

1. **Configuration & Decoupled Pagination (`src/config.py`, `.env.example`)**:
   - Added `upstream_max_results: int = 50` and `upstream_max_pages: int = 3`.
   - Added `page_size: int = 8` for Telegram inline display.
   - Added `empty_result_cache_ttl: float = 300.0` (5 minutes) for empty queries.
   - Decoupled `result_limit: int = 50` from display pagination.

2. **Upstream Multi-Page Scraping (`src/sources/base.py`, `src/sources/libgen.py`, `src/search.py`)**:
   - Updated `SourceParser.search_url(mirror, query, page=1)` to generate page URLs for both `li-fork` (`&page={page}`) and `is-fork` (`&page={page}`).
   - Multi-page polite scraping in `SearchService._scrape`: iterates up to `upstream_max_pages`, respecting `HostRateLimiter` and `GlobalSemaphore`.
   - Detects end-of-results via `len(hits) < 25`, duplicate pages, or reaching `upstream_max_results`.

3. **Catalog Upsert & Upstream Metadata Priority (`src/database.py`, `src/search.py`)**:
   - Updated `_upsert_book_sync` to `ON CONFLICT(md5) DO UPDATE SET`, updating titles, authors, formats, sizes, and detail URLs while preserving local IDs.
   - Searches merge upstream results ahead of local FTS matches to ensure the latest upstream metadata is prioritized.

4. **Search Cache Lifecycle & Invalidation (`src/database.py`, `src/search.py`)**:
   - Schema and additive migration added `is_complete`, `pages_fetched`, and `total_upstream` columns to `search_cache`.
   - Created `SearchCacheEntry` supporting 2-tuple unpacking (`ids, expires_at = cached`) and full attribute access.
   - Empty search results (0 hits) cached with short TTL (`EMPTY_RESULT_CACHE_TTL=300s`); populated results cached for 24h (`SEARCH_CACHE_TTL=86400s`). Errors are never cached.
   - `🔄 Refresh from sources` button invalidates cache and triggers rate-limited upstream re-query.

5. **Search Quality, Normalization & Relevance Ranking (`src/search.py`, `src/database.py`, `src/sources/libgen.py`)**:
   - `normalize_query`: Unicode NFKC normalization and punctuation stripping.
   - `_sanitize_fts_query`: Prefix token matching with strict `AND` query, falling back to forgiving `OR` matching if strict `AND` returns 0 hits.
   - `relax_query`: Retries once with stop words stripped if strict query produces 0 hits.
   - `_rank_books`: Deterministic relevance ranking prioritizing exact title matches, full token matches, author token matches, partial title matches, and metadata completeness.
   - Audited parsers: `LiForkParser` and `IsForkParser` retain rows with missing optional fields (year, publisher, language) and log warnings when rows cannot be parsed.

6. **Telegram UX & Typography Presentation (`src/bot.py`, `src/models.py`)**:
   - Extended `SearchOutcome` with `source: Literal["cache", "local", "upstream", "mixed"]`, `total_count`, `mirror_url`, `latency_ms`, `query_normalized`, `is_relaxed`, and `upstream_reached`.
   - Clean search header: Displays bold query, hit count, range, provenance, and latency with minimal punctuation and no emoji clutter.
   - Refined book cards: Clean bold titles with middot-separated author, format, and size lines, removing noisy icons (`👤`, `📦`, `📖`).
   - Book detail view: Elegant typography with clear title, author, format tag, and bold download mirror links.
   - Inline keyboard: Formats compact specs (`[EXT] Title - Author`) with `⬅️ Prev`, `Page X/Y`, `Next ➡️` navigation and `🔄 Refresh` button.
   - Clean empty state explains whether upstream was contacted and suggests broader terms.
   - Strict authorization maintained across all commands and callback queries.

## Verification

- **Automated Tests**:
  - 63 tests passing with 0 failures (`uv run pytest`).
  - `powershell -ExecutionPolicy Bypass -File scripts\verify.ps1` passing (3 checks passed, 0 failed, 0 secrets).
- **Security & Architecture**:
  - Zero-storage rule preserved: no file binaries stored or downloaded on server.
  - User whitelist enforcement verified on all command and callback routes.
