# Step 07: Sources Architecture, Libgen Adapter, and Source Resolver

## Objective

Implement the shadow library source abstraction layer (`sources/base.py`), the Library Genesis adapter (`sources/libgen.py`), and the multi-source coordinator (`sources/resolver.py`).

## Scope

- `sources/base.py`:
  - `BaseSource` abstract base class defining `search`, `resolve`, `download`, `available`, `set_cooldown`, `rotate_mirror`.
  - Scraping hygiene: dedicated `httpx.AsyncClient` per source, realistic browser user-agent rotation, per-host locks enforcing `POLITE_DELAY_MS`, exponential backoff on HTTP 429/503 with jitter, non-retry of HTTP 404, mirror rotation on exhausted retries.
- `sources/libgen.py`:
  - `LibgenSource` subclass implementing Libgen mirrors.
  - Robust HTML search parsing (`parse_search_html`) extracting ID, author, title, year, language, size, extension, MD5, and detail URLs.
  - Detail page resolution (`resolve`) discovering IPFS CIDs (`bafy...`, `Qm...`) or direct HTTP GET download URLs.
  - Streaming download (`download`) with `asyncio.timeout`, mid-stream byte limit enforcement, and automatic `.part` file unlinking on failure.
- `sources/resolver.py`:
  - `SourceResolver` coordinating sources in configurable `SOURCE_PRIORITY` order.
  - Sequential queries stopping at the first healthy source with results.
  - Deduplication across sources by MD5 and normalized title/author.
  - CID-first sorting preference.
  - Transparent dispatch to originating source for resolution.
- Static test fixtures:
  - `tests/fixtures/libgen_search.html`
  - `tests/fixtures/libgen_detail_get.html`
  - `tests/fixtures/libgen_detail_cid.html`
- Unit tests:
  - `tests/test_sources.py` covering HTML parsing, mock transport search, detail resolution (GET & CID), mirror rotation, stream downloading, size limits, priority order, deduplication, and cooldowns.

## Verification

- Ran `scripts/verify.ps1`:
  - Git available: OK
  - No tracked secrets: OK
  - Python tests: OK (68 passed in 10.2s across all suites, 0 failed).
- All 11 source-specific unit tests passed.

## Decisions

- **Dedicated clients:** Each source adapter manages its own `httpx.AsyncClient` instance, cookie jar, and connection pool as mandated in `docs/GOAL.md`.
- **Zero orphaned files:** All failed downloads clean up any written disk bytes in `except/finally` blocks immediately.
- **Deduplication:** Normalization handles punctuation and whitespace differences so title/author variations don't produce duplicate hits.
