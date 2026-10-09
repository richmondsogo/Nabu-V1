# ADR 0003: Search Quality, Upstream Pagination, and Transparency Architecture

## Status

Accepted

## Context

Following the transition to a zero-storage link resolver (ADR 0002), users reported several usability and discovery limitations:
1. **Truncated Results & Fixed Limits:** Searches were artificially limited to 5 results because scraping stopped after page 1, ignoring dozens of relevant upstream books.
2. **Strict FTS Query Failures:** Punctuation (e.g. colons, hyphens), special characters, and diacritics caused SQLite FTS syntax errors or yielded zero hits on titles that upstream easily matched.
3. **No Relevance Ranking:** Scraped and local results were displayed in arbitrary upstream table order rather than prioritized by query relevance.
4. **Cache & Upstream Opacity:** Users had no visibility into where results came from (cache, local catalog, upstream scrape), which mirror responded, how fast it responded, or whether the results were complete.
5. **Cache Contamination on Incomplete/Empty Results:** Empty scrapes or partial failures were either cached for 24 hours (locking in empty results) or caused confusing behavior when refreshing.

## Decision

We enhance search quality, recall, and user transparency across the system while preserving the zero-storage architecture:

1. **Decoupled Result Limits & Upstream Pagination:**
   - Decouple user display limits (`PAGE_SIZE=8`) from upstream scraping limits (`UPSTREAM_MAX_RESULTS=50`, `UPSTREAM_MAX_PAGES=3`).
   - `_scrape` iterates polite, rate-limited page requests (`page=1..3`) until reaching `UPSTREAM_MAX_RESULTS`, detecting pagination exhaustion (`len(hits) < 25`), or encountering duplicate pages.
   - Inline keyboard implements pagination (`⬅️ Prev`, `Page X/Y`, `Next ➡️`) and preserves `🔄 Refresh from sources`.

2. **Catalog Upsert & Upstream Priority:**
   - Catalog insertion uses `ON CONFLICT(md5) DO UPDATE SET` so newer upstream metadata updates existing records while preserving local primary keys and acquisition history.
   - Searches merge upstream results ahead of local FTS matches to ensure fresh, complete metadata.

3. **Search Cache Lifecycle & Invalidation:**
   - `search_cache` table records `is_complete`, `pages_fetched`, and `total_upstream` metadata.
   - Empty results (0 hits) are cached with a short TTL (`EMPTY_RESULT_CACHE_TTL=300s`), while populated results retain the full TTL (`SEARCH_CACHE_TTL=86400s`).
   - Errors are never cached.
   - The `🔄 Refresh` button forces an upstream re-query and updates the cache.

4. **Query Normalization, Forgiving Matching & Ranking:**
   - `normalize_query`: Applies Unicode NFKC normalization and strips non-alphanumeric punctuation.
   - `_sanitize_fts_query`: Generates token prefix queries. If strict `AND` returns 0 hits, forgiving `OR` matching is executed.
   - `relax_query`: If a strict query yields 0 results, the query is retried once with stop words removed.
   - `_rank_books`: Orders results deterministically: exact title matches -> all tokens in title -> tokens in author -> any token in title -> metadata completeness.
   - Parsers (`LiForkParser`, `IsForkParser`) tolerate missing optional columns (year, publisher, language) and log warnings rather than discarding valid rows.

5. **Transparency & UX:**
   - Result headers display normalized query, result range, total count, provenance indicator (`💾 cache`, `🗂️ local catalog`, `🌐 upstream`, `🔀 mixed`), responding mirror, and latency in milliseconds.
   - Result list and buttons display compact file formats and sizes.
   - Empty states provide actionable guidance and explain whether upstream sources were contacted.

## Trade-offs & Consequences

- Multi-page scraping increases upstream request volume for cold queries, but it is bounded by `GlobalSemaphore`, `HostRateLimiter`, and `UPSTREAM_MAX_PAGES=3`.
- Warm searches remain sub-millisecond cache hits with zero outbound network calls.
